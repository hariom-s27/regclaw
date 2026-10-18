"""SDK request contract, recorded fake responses, and an explicitly opt-in real client."""
import copy
from dataclasses import dataclass, field
import inspect
import json
from pathlib import Path
import threading
from typing import Protocol

from .checks import SCHEMA
from .prompt import SYSTEM_PROMPT

MODEL = 'claude-haiku-5-5'
FIXTURES = Path(__file__).resolve().parents[1] / 'tests/fixtures/ner'


@dataclass
class RawResult:
    data: dict
    stop_reason: str = 'end_turn'
    model: str = MODEL
    usage: dict = field(default_factory=dict)
    provenance: dict = field(default_factory=dict)
    latency_s: float | None = None
    finish_reason: str | None = None


class LLMClient(Protocol):
    model: str
    effort: str
    mode: str

    def extract(self, clause: dict, *, max_tokens: int = 4000) -> RawResult: ...


def request(clause, *, model=MODEL, effort='low', mode='structured', max_tokens=4000):
    result = {'model': model, 'max_tokens': max_tokens,
              'system': [{'type': 'text', 'text': SYSTEM_PROMPT, 'cache_control': {'type': 'ephemeral'}}],
              'messages': [{'role': 'user', 'content': f'<clause id="{clause["id"]}" source="{clause["doc"]}">{clause["text"]}</clause>'}],
              'output_config': {'effort': effort}}
    if mode == 'structured':
        result['output_config']['format'] = {'type': 'json_schema', 'schema': SCHEMA}
    elif mode == 'strict_tool':
        result['tools'] = [{'name': 'emit_entities', 'description': 'Extract grounded entities.', 'input_schema': SCHEMA, 'strict': True}]
        result['tool_choice'] = {'type': 'tool', 'name': 'emit_entities'}
    else:
        raise ValueError('mode must be structured or strict_tool')
    return result


class FakeClient:
    model = 'stub-fixture'

    def __init__(self, fixtures=None, *, effort='low', mode='structured'):
        self.fixtures = fixtures or {}
        self.effort, self.mode = effort, mode
        self.calls, self.requests, self._lock = 0, [], threading.Lock()
        self.default = json.loads((FIXTURES / 'empty.json').read_text(encoding='utf-8'))

    def extract(self, clause, *, max_tokens=4000):
        captured = request(clause, effort=self.effort, mode=self.mode, max_tokens=max_tokens)
        with self._lock:
            self.calls += 1
            self.requests.append(captured)
            fixture = self.fixtures.get(clause['id'], self.default)
            if isinstance(fixture, list):
                fixture = fixture.pop(0) if len(fixture) > 1 else fixture[0]
        if isinstance(fixture, (str, Path)):
            fixture = json.loads(Path(fixture).read_text(encoding='utf-8'))
        if isinstance(fixture, Exception):
            raise fixture
        if isinstance(fixture, RawResult):
            return copy.deepcopy(fixture)
        return RawResult(**copy.deepcopy(fixture))


class AnthropicClient:
    model = MODEL
    provider = 'anthropic'

    def __init__(self, *, effort='low', mode='structured', sdk_client=None):
        import anthropic
        from anthropic.types import OutputConfigParam
        parameters = inspect.signature(anthropic.resources.messages.Messages.create).parameters
        annotations = OutputConfigParam.__annotations__
        if 'output_config' not in parameters or 'effort' not in annotations or 'format' not in annotations:
            raise NotImplementedError(f'anthropic {anthropic.__version__} lacks verified output_config.format/effort support; install a compatible SDK explicitly.')
        if mode == 'strict_tool':
            from anthropic.types import ToolParam
            if 'strict' not in ToolParam.__annotations__:
                raise NotImplementedError(f'anthropic {anthropic.__version__} lacks verified ToolParam.strict support.')
        self.sdk_version = anthropic.__version__
        self.effort, self.mode = effort, mode
        self.client = sdk_client or anthropic.Anthropic(max_retries=0)

    def extract(self, clause, *, max_tokens=4000):
        from observability.tracing import stage, record_response
        from llm.provider import provenance
        with stage('ner.extract', as_type='generation', model=self.model,
                   metadata=provenance(self)) as observation:
            response = self.client.messages.create(**request(clause, effort=self.effort, mode=self.mode, max_tokens=max_tokens))
            record_response(observation, response)
            if observation is not None:
                observation.update(metadata=provenance(self, response.model))
        if self.mode == 'strict_tool':
            blocks = [b for b in response.content if b.type == 'tool_use' and b.name == 'emit_entities']
            data = blocks[0].input if blocks else {}
        else:
            content = ''.join(b.text for b in response.content if b.type == 'text')
            try:
                data = json.loads(content)
            except json.JSONDecodeError:
                data = {}
        return RawResult(data, response.stop_reason, response.model, response.usage.model_dump(),
                         provenance(self, response.model))
