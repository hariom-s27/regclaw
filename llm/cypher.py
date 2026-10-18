"""S8 structured Text2Cypher transport; no tools or sampling parameters."""
import json
import os

from observability.tracing import stage, record_response
from .provider import provenance

OUTPUT_SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'properties': {
        'cypher': {'type': 'string'},
        'params': {'type': 'array', 'items': {
            'type': 'object', 'additionalProperties': False,
            'properties': {'name': {'type': 'string'}, 'value': {'type': 'string'}},
            'required': ['name', 'value']}},
    },
    'required': ['cypher', 'params'],
}


def request(system, messages, *, max_tokens=800):
    return dict(model=os.getenv('CYPHER_MODEL', 'claude-haiku-5-5'),
                max_tokens=max_tokens, system=system, messages=messages,
                output_config={'format': {'type': 'json_schema', 'schema': OUTPUT_SCHEMA}})


def parse_output(data):
    import jsonschema
    jsonschema.validate(data, OUTPUT_SCHEMA)
    params = {}
    for item in data['params']:
        if item['name'] in params:
            raise ValueError('Duplicate Cypher parameter name')
        params[item['name']] = item['value']
    return data['cypher'], params


class AnthropicCypherClient:
    provider, role, effort = 'anthropic', 's8_cypher', None

    def __init__(self, sdk_client=None):
        self.model = os.getenv('CYPHER_MODEL', 'claude-haiku-5-5')
        if sdk_client is None:
            import anthropic
            sdk_client = anthropic.Anthropic(max_retries=0)
        self.client = sdk_client

    def generate(self, request):
        request = {**request, 'model': self.model}
        with stage('cypher.generate', as_type='generation', model=self.model,
                   metadata=provenance(self)) as observation:
            response = self.client.messages.create(**request)
            record_response(observation, response)
            details = provenance(self, getattr(response, 'model', None))
            stop = response.stop_reason
            if observation is not None:
                observation.update(metadata={**details, 'stop_reason': stop})
        text = ''.join(b.text for b in response.content if b.type == 'text')
        try:
            data = json.loads(text)
        except (ValueError, TypeError):
            data = None
        return dict(data=data, stop_reason=stop, provenance=details)
