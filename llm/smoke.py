"""One metered Azure call per invocation through the real S4 or S10 path.

Run with the project environment: python -m llm.smoke S4 (then S10).
The persistent ledger caps this smoke at three calls; no automatic retries send
another request. A third invocation is reserved for a confirmed mapping fix.
"""
import argparse
from dataclasses import asdict
import json
import os
import time
from types import SimpleNamespace

from .provider import ROOT, make_client


class SmokeCallLimit(RuntimeError):
    pass


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('role', choices=['S4', 'S10'])
    args = parser.parse_args(argv)
    from dotenv import load_dotenv
    load_dotenv(ROOT / '.env')
    if os.getenv('LLM_PROVIDER') != 'azure_openai':
        parser.error('Smoke requires LLM_PROVIDER=azure_openai')
    if os.getenv('REGCLAW_STUB') == '1':
        parser.error('Smoke requires REGCLAW_STUB unset or 0')
    # Avoid external telemetry calls; adapter tracing is verified with fakes.
    os.environ['LANGFUSE_ENABLED'] = 'false'
    folder = ROOT / 'data/runs/azure_openai/smoke'
    folder.mkdir(parents=True, exist_ok=True)
    ledger_path = folder / 'calls.json'
    ledger = json.loads(ledger_path.read_text()) if ledger_path.exists() else []
    if len(ledger) >= 3:
        parser.error('Three-call smoke budget exhausted')
    if len(ledger) == 2 and all(entry.get('status') == 'ok' for entry in ledger):
        parser.error('Both calls succeeded; no third call permitted')
    client = make_client(args.role)
    create = client.client.chat.completions.create
    sent, failures, raw_results = [], [], []

    def one_call(**request):
        if sent:
            raise SmokeCallLimit('Only one request per smoke invocation')
        sent.append(True)
        entry = dict(role=args.role, status='started')
        ledger.append(entry)
        ledger_path.write_text(json.dumps(ledger, indent=2) + '\n')
        try:
            return create(**request)
        except Exception as exc:
            # Never persist exception text, request headers, endpoint, or keys.
            body = getattr(exc, 'body', None)
            error = body.get('error', body) if isinstance(body, dict) else {}
            failure = dict(type=type(exc).__name__, status_code=getattr(exc, 'status_code', None))
            if isinstance(error, dict):
                for key in ('code', 'param'):
                    value = error.get(key)
                    if isinstance(value, str) and len(value) < 120:
                        failure[key] = value
            failures.append(failure)
            raise

    # Disable hidden SDK retries; the limiter also blocks pipeline/schema retries.
    client.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=one_call)))
    method_name = 'extract' if args.role == 'S4' else 'classify'
    original = getattr(client, method_name)
    def capture(*a, **kw):
        result = original(*a, **kw)
        raw_results.append(result)
        return result
    setattr(client, method_name, capture)
    started = time.perf_counter()
    if args.role == 'S4':
        from ner.extract import run_pipeline
        from ner.checks import SCHEMA
        import jsonschema
        clauses = json.loads((ROOT / 'data/clauses.json').read_text(encoding='utf-8'))
        clause = next(c for c in clauses if c['doc'] == 'fda-iso10993-1' and 100 <= len(c['text']) <= 800)
        rows, summary = run_pipeline([clause], client, force=True)
        valid = bool(raw_results)
        if raw_results:
            try:
                jsonschema.validate(raw_results[-1].data, SCHEMA)
            except jsonschema.ValidationError:
                valid = False
        passed = valid and rows[0]['status'] == 'ok'
        parsed = rows[0]
        fixture = clause['id']
    else:
        from standards.demo import FakeRetriever, example_profiles
        from standards.tool import get_applicable_standards
        from standards.classifier import OUTPUT_SCHEMA
        import jsonschema
        parsed = get_applicable_standards(example_profiles()['E2'], FakeRetriever(), llm=client)
        fixture = 'E2 (empty fixture retriever; corpus citations unavailable)'
        valid = bool(raw_results)
        if raw_results:
            try:
                jsonschema.validate(raw_results[-1].data, OUTPUT_SCHEMA)
            except jsonschema.ValidationError:
                valid = False
        passed = valid and parsed['provenance']['llm_status'] == 'ok'
    usage = raw_results[-1].usage if raw_results else {}
    estimate = (usage.get('input_tokens', 0) * .25 + usage.get('output_tokens', 0) * 2) / 1_000_000
    report = dict(role=args.role, fixture=fixture, status='ok' if passed else 'failed',
                  calls=len(sent), latency_s=time.perf_counter() - started,
                  parsed=parsed, raw=asdict(raw_results[-1]) if raw_results else None,
                  usage=usage, estimated_cost_usd=estimate if usage else None,
                  prices='UNVERIFIED; illustrative GPT-5 mini $0.25 input/$2 output per 1M tokens',
                  failures=failures)
    if sent:
        ledger[-1]['status'] = report['status']
        ledger_path.write_text(json.dumps(ledger, indent=2) + '\n')
    (folder / f'{args.role.lower()}-{len(ledger)}.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    brief = {k: v for k, v in report.items() if k not in {'parsed', 'raw'}}
    brief['parsed_short'] = str(parsed)[:900]
    print(json.dumps(brief, ensure_ascii=True, indent=2))
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
