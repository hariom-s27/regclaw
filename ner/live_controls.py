"""Azure-only S4 pacing and persistent cost ledger; no entity decisions here."""
from collections import deque
from email.utils import parsedate_to_datetime
import json
import threading
import time

from .client import request


class RunStopped(RuntimeError):
    """A user stop threshold was crossed; never convert this to an error row."""


def retry_delay(exc, fallback):
    response = getattr(exc, 'response', None)
    headers = getattr(response, 'headers', {})
    try:
        return max(fallback, float(headers['retry-after-ms']) / 1000)
    except (KeyError, TypeError, ValueError):
        pass
    try:
        return max(fallback, float(headers['retry-after']))
    except (KeyError, TypeError, ValueError):
        try:
            return max(fallback, parsedate_to_datetime(headers['retry-after']).timestamp() - time.time())
        except (KeyError, TypeError, ValueError):
            return fallback


def azure_cost(usage):
    # Conservative regional published rates, no cached-input discount. Reasoning
    # is already included in output_tokens, so never bill it a second time.
    return (usage.get('input_tokens', 0) * .275 + usage.get('output_tokens', 0) * 2.2) / 1_000_000


class LiveControls:
    def __init__(self, client, folder, total, *, requested=None, clock=time.monotonic, sleep=time.sleep):
        import tiktoken
        self.encoding = tiktoken.get_encoding('o200k_base')
        self.client, self.total = client, total
        self.requested = requested if requested is not None else total
        self.path = folder / 'call_ledger.json'
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.ledger = json.loads(self.path.read_text()) if self.path.exists() else []
        self.lock, self.window = threading.Lock(), deque()
        self.clock, self.sleep = clock, sleep
        self.reserved, self.stopped = {}, None
        self.started = clock()

    def save(self):
        self.path.write_text(json.dumps(self.ledger, indent=2) + '\n', encoding='utf-8')

    def before_request(self, clause, budget):
        mapped = self.client.map_request(request(clause, max_tokens=budget, mode=self.client.mode))
        input_tokens = sum(len(self.encoding.encode(m['content'])) + 8 for m in mapped['messages'])
        input_tokens += len(self.encoding.encode(json.dumps(mapped['response_format']))) + 32
        # Reserve the full completion cap, plus a margin on input estimates.
        tokens = int(1.10 * input_tokens) + mapped['max_completion_tokens']
        worst = azure_cost(dict(input_tokens=int(input_tokens*1.10), output_tokens=mapped['max_completion_tokens']))
        if tokens > 78000:
            raise RunStopped('single request exceeds 78K token pacing budget')
        while True:
            with self.lock:
                if self.stopped:
                    raise RunStopped(self.stopped)
                spent = sum(e.get('cost_estimate_usd', 0) for e in self.ledger)
                if spent + sum(self.reserved.values()) + worst > 8:
                    self.stopped = 'estimated cumulative cost would exceed $8'
                    raise RunStopped(self.stopped)
                now = self.clock()
                while self.window and now - self.window[0][0] >= 61:
                    self.window.popleft()
                if len(self.window) < 78 and sum(t for _, t in self.window) + tokens <= 78000:
                    self.window.append((now, tokens))
                    ticket = len(self.ledger)
                    self.reserved[ticket] = worst
                    self.ledger.append(dict(clause_id=clause['id'], effort=self.client.effort,
                                            status='started', reserved_tokens=tokens,
                                            provenance=__import__('llm.provider', fromlist=['provenance']).provenance(self.client)))
                    self.save()
                    return ticket
                delay = max(.05, 61 - (now-self.window[0][0]))
            self.sleep(min(delay, 30))

    def after_request(self, ticket, raw=None, exc=None):
        with self.lock:
            entry = self.ledger[ticket]
            reservation = self.reserved.pop(ticket)
            if raw is not None:
                entry.update(status='response', usage=raw.usage, cost_estimate_usd=azure_cost(raw.usage),
                             latency_s=raw.latency_s, stop_reason=raw.stop_reason, provenance=raw.provenance)
            else:
                status = getattr(exc, 'status_code', None)
                # HTTP rejections are unbilled. Unknown network failures remain
                # conservatively charged at the reserved maximum.
                entry.update(status='error', error=type(exc).__name__, http_status=status,
                             cost_estimate_usd=0 if status else reservation)
            self.save()

    def completed(self, results):
        errors = sum('error' in r for r in results.values())
        if errors / self.total > .05:
            self.stopped = f'error rows {errors}/{self.total} exceed 5%'
            raise RunStopped(self.stopped)
        low = [e for e in self.ledger if e.get('effort') == 'low']
        completed = {e['clause_id'] for e in low if e['status'] == 'response'}
        if self.client.effort == 'low' and self.requested == self.total and len(completed) >= 10:
            projected = sum(e.get('cost_estimate_usd', 0) for e in low) / len(completed) * self.total
            if projected > 8:
                self.stopped = f'projected full-run cost ${projected:.4f} exceeds $8'
                raise RunStopped(self.stopped)
