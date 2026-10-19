"""Azure v1 Chat Completions adapter for the existing S4/S10 interfaces.

GPT-5 reasoning models require max_completion_tokens (reasoning + visible
output). Only the supported request fields below are forwarded: temperature,
top_p, penalties, stop, logprobs and Anthropic cache controls are never sent.
Prompts and schemas are copied verbatim, including strict-tool input schemas.
"""
import copy
import json
import os
import time
from random import random
from email.utils import parsedate_tz, mktime_tz
from types import SimpleNamespace

from observability.tracing import stage, record_response
from .provider import provenance

S8_ROLES = ('s8_cypher', 's8_hyde', 's8_prelabel')
SPAN_NAMES = {'s8_cypher': 'cypher.generate', 's8_hyde': 'hyde.generate', 's8_prelabel': 'prelabel.generate'}
# S8 Addendum 3: in run family c gpt-5-mini spent the whole 800-token cap on hidden reasoning in
# 49% of first attempts. Only these roles get more completion headroom (x5 at any effort):
# 800 -> 4,000 on the first attempt, 1,600 -> 8,000 on the retry. S4, S10, s8_prelabel unchanged.
ROLE_BUDGET_SCALE = {'s8_cypher': 5, 's8_hyde': 5}


class AzureOpenAIAdapter:
    provider = 'azure_openai'

    def __init__(self, role, *, mode='structured', sdk_client=None):
        if role not in {'S4', 'S10', *S8_ROLES}:
            raise ValueError('role must be S4, S10, s8_cypher, s8_hyde or s8_prelabel')
        if mode not in {'structured', 'strict_tool'}:
            raise ValueError('mode must be structured or strict_tool')
        self.role, self.mode = role, mode
        # S8 evaluation helpers share the provisional s8_cypher deployment and effort (ADR 0025).
        env_role = 'S8' if role in S8_ROLES else role
        self.model = (os.getenv('AZURE_OPENAI_S8_DEPLOYMENT', 'gpt-5-mini')
                      if role in S8_ROLES else os.environ[f'AZURE_OPENAI_{role}_DEPLOYMENT'])
        self.effort = os.getenv(f'AZURE_OPENAI_{env_role}_REASONING_EFFORT',
                                'high' if role == 'S10' else 'low')
        if self.effort not in {'minimal', 'low', 'medium', 'high'}:
            raise ValueError('Azure reasoning effort must be minimal, low, medium or high')
        if sdk_client is None:
            from openai import OpenAI
            sdk_client = OpenAI(
                base_url=os.environ['AZURE_OPENAI_ENDPOINT'].rstrip('/') + '/openai/v1/',
                api_key=os.environ['AZURE_OPENAI_API_KEY'],
                # S4 retries belong to ner.extract; S10 policy is matched below.
                max_retries=0)
        self.client = sdk_client

    def _send(self, mapped):
        # Match anthropic 1.14's S10 policy, including long Retry-After values.
        # OpenAI 2.54's own retry policy rejects Retry-After >120s, so leaving
        # SDK retries enabled would subtly change the existing S10 policy.
        from openai import APIConnectionError
        for attempt in range(3 if self.role == 'S10' else 1):
            try:
                return self.client.chat.completions.create(**mapped)
            except Exception as exc:
                response = getattr(exc, 'response', None)
                headers = response.headers if response is not None else {}
                status = getattr(exc, 'status_code', None)
                override = headers.get('x-should-retry')
                retry = (isinstance(exc, APIConnectionError) or status in (408, 409, 429)
                         or isinstance(status, int) and status >= 500)
                if override in ('true', 'false'):
                    retry = override == 'true'
                if self.role != 'S10' or not retry or attempt == 2:
                    raise
                delay = None
                try:
                    delay = float(headers.get('retry-after-ms')) / 1000
                except (ValueError, TypeError):
                    pass
                if delay is None:
                    try:
                        delay = float(headers.get('retry-after'))
                    except (ValueError, TypeError):
                        date = parsedate_tz(headers.get('retry-after'))
                        if date is not None:
                            delay = float(mktime_tz(date) - time.time())
                if delay is None or not delay > 0:
                    delay = min(.5 * 2**attempt, 8) * (1 - .25 * random())
                time.sleep(min(delay, 4_294_967.0))

    def map_request(self, request):
        system = request['system']
        if isinstance(system, list):
            system = ''.join(block['text'] for block in system)
        output = request.get('output_config', {}).get('format')
        if output is not None:
            schema = output['schema']
            name = {'S4': 'entities', 'S10': 'standards', 's8_cypher': 'cypher',
                    's8_hyde': 'hyde_passage', 's8_prelabel': 'prelabel'}[self.role]
        else:
            tool = next(t for t in request['tools'] if t['name'] == request['tool_choice']['name'])
            schema, name = tool['input_schema'], tool['name']
        # GPT's cap covers hidden reasoning as well as the schema response.
        # The S10 high-effort smoke exhausted a direct 8,000-token mapping.
        # Reserve two response budgets for high effort, one for medium.
        scale = (ROLE_BUDGET_SCALE.get(self.role)
                 or {'minimal': 1, 'low': 1, 'medium': 2, 'high': 3}[self.effort])
        budget = request['max_tokens'] * scale
        return dict(model=self.model, max_completion_tokens=budget,
                    reasoning_effort=self.effort,
                    messages=[dict(role='system', content=system)] + copy.deepcopy(request['messages']),
                    response_format=dict(type='json_schema', json_schema=dict(
                        name=name, strict=True, schema=copy.deepcopy(schema))))

    def _complete(self, request, span_name):
        started = time.perf_counter()
        request = dict(request)
        version = request.pop('prompt_version', None)  # trace-only; never sent to the API
        tag = {} if version is None else {'prompt_version': version}
        with stage(span_name, as_type='generation', model=self.model,
                   metadata={**provenance(self), **tag}) as observation:
            mapped = self.map_request(request)
            response = self._send(mapped)
            choice = response.choices[0]
            message = choice.message
            raw_finish = choice.finish_reason
            if getattr(message, 'refusal', None):
                stop = 'refusal'
            else:
                stop = {'stop': 'end_turn', 'length': 'max_tokens',
                        'content_filter': 'refusal'}.get(raw_finish, raw_finish or 'unavailable')
            try:
                data = json.loads(message.content or '')
            except (ValueError, TypeError):
                data = {}
            raw_usage = response.usage
            usage = {} if raw_usage is None else dict(
                input_tokens=raw_usage.prompt_tokens, output_tokens=raw_usage.completion_tokens)
            completion_details = getattr(raw_usage, 'completion_tokens_details', None)
            prompt_details = getattr(raw_usage, 'prompt_tokens_details', None)
            if completion_details is not None:
                usage['reasoning_tokens'] = getattr(completion_details, 'reasoning_tokens', 0) or 0
            if prompt_details is not None:
                usage['cache_read_input_tokens'] = getattr(prompt_details, 'cached_tokens', 0) or 0
            details = provenance(self, getattr(response, 'model', None))
            if self.role in ROLE_BUDGET_SCALE:  # record the real cap; other roles' provenance unchanged
                details['max_completion_tokens'] = mapped['max_completion_tokens']
            record_response(observation, SimpleNamespace(model=self.model, usage=usage))
            if observation is not None:
                observation.update(metadata={**details, **tag, 'finish_reason': raw_finish,
                                             'stop_reason': stop})
        return dict(data=data, stop_reason=stop, model=self.model, usage=usage,
                    provenance=details, latency_s=time.perf_counter() - started,
                    finish_reason=raw_finish)

    def extract(self, clause, *, max_tokens=4000):
        from ner.client import RawResult, request
        return RawResult(**self._complete(request(clause, mode=self.mode, max_tokens=max_tokens), 'ner.extract'))

    def classify(self, request):
        from standards.classifier import LLMResponse
        result = self._complete(request, 'standards.classify')
        return LLMResponse(**result)

    def generate(self, request):
        return self._complete(request, SPAN_NAMES[self.role])
