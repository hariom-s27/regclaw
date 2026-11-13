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


def run_dir():
    """Optional REGCLAW_RUN_DIR (repo-relative) for live smoke runs; None when unset.

    Unset keeps the original paths. A set value must stay inside data/runs/ and
    outside data/runs/azure_openai/, so smoke runs never write into S8/S10 folders.
    """
    value = os.getenv('REGCLAW_RUN_DIR', '').strip()
    if not value:
        return None
    runs = (ROOT / 'data/runs').resolve()
    path = (ROOT / value).resolve()
    if not path.is_relative_to(runs) or path == runs or path.is_relative_to(runs / 'azure_openai'):
        raise ValueError('REGCLAW_RUN_DIR must be inside data/runs/ and outside data/runs/azure_openai/')
    return path


def save_record(role, record, *, root=None):
    """Persist provisional live S10 outputs separately from the Claude outputs."""
    if record['provenance'].get('provider') != 'azure_openai':
        return
    override = run_dir() if root is None else None
    folder = (override / role.lower() if override is not None
              else Path(root or ROOT) / 'data/runs/azure_openai' / role.lower())
    folder.mkdir(parents=True, exist_ok=True)
    (folder / (uuid4().hex + '.json')).write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
