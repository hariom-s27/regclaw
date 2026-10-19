"""Role selection and additive live provenance, without loading keys in stub mode."""
import json
import os
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


def provider_name():
    provider = os.getenv('LLM_PROVIDER', 'anthropic')
    if provider not in {'anthropic', 'azure_openai'}:
        raise ValueError('LLM_PROVIDER must be anthropic or azure_openai')
    return provider


def live_configured():
    if os.getenv('REGCLAW_STUB') == '1':
        return False
    key = 'AZURE_OPENAI_API_KEY' if provider_name() == 'azure_openai' else 'ANTHROPIC_API_KEY'
    return bool(os.getenv(key))


# S8 evaluation helpers (ADR 0025): provisional Azure only, never part of the report pipeline.
S8_HELPER_ROLES = {'s8_hyde', 's8_prelabel'}


def make_client(role, *, effort='low', mode='structured'):
    if role not in {'S4', 'S10', 's8_cypher', *S8_HELPER_ROLES}:
        raise ValueError('role must be S4, S10, s8_cypher, s8_hyde or s8_prelabel')
    if provider_name() == 'azure_openai':
        from .azure_openai import AzureOpenAIAdapter
        return AzureOpenAIAdapter(role, mode=mode)
    if role in S8_HELPER_ROLES:
        raise ValueError(f'{role} is a provisional Azure evaluation helper; set LLM_PROVIDER=azure_openai')
    if role == 's8_cypher':
        from .cypher import AnthropicCypherClient
        return AnthropicCypherClient()
    if role == 'S4':
        from ner.client import AnthropicClient
        return AnthropicClient(effort=effort, mode=mode)
    from standards.classifier import AnthropicLLM
    return AnthropicLLM()


def provenance(client, response_model=None):
    provider = getattr(client, 'provider', None)
    if provider is None:  # Recorded fixtures retain their existing contracts.
        return {}
    details = dict(provider=provider, model=client.model,
                model_version=response_model, provisional=provider != 'anthropic',
                reasoning_effort=getattr(client, 'effort', 'low'))
    if getattr(client, 'role', None) in {'s8_cypher', *S8_HELPER_ROLES}:
        details.update(role=client.role, requested_model=client.model,
                       response_model=response_model, effort=client.effort)
    return details


def save_record(role, record, *, root=None):
    """Persist provisional live S10 outputs separately from the Claude outputs."""
    if record['provenance'].get('provider') != 'azure_openai':
        return
    folder = Path(root or ROOT) / 'data/runs/azure_openai' / role.lower()
    folder.mkdir(parents=True, exist_ok=True)
    (folder / (uuid4().hex + '.json')).write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
