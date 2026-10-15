"""Injected retrieval + rules + optional classifier + guard; no implicit API use."""
import logging
from .assemble import assemble
from .classifier import make_request
from .data_loader import load_data
from .engine import evaluate
from .guard import guard
from .profile import DeviceProfile

log = logging.getLogger(__name__)


def get_applicable_standards(device_profile, retriever, *, llm=None, data=None, generated_at=None):
    """mandatory = must be addressed in the biological evaluation, not necessarily tested.

    recommended = consider the trigger; not_applicable = not indicated by current
    contact/duration/triggers. Retrieval and LLM clients are explicitly injected.
    """
    profile = device_profile if isinstance(device_profile, DeviceProfile) else DeviceProfile.model_validate(device_profile)
    data = load_data() if data is None else data
    rules = evaluate(profile, data)
    candidates = []
    try:
        hits, _ = retriever.retrieve(profile.device_name + ' biological evaluation ISO 10993', top_k=30)
        for hit in hits:
            value = hit if isinstance(hit, dict) else vars(hit)
            candidates.append(dict(clause_id=value['clause_id'], doc=value.get('doc', value.get('standard', '')),
                                   edition=value.get('edition', 'UNVERIFIED'), text=value.get('text', '')))
    except Exception as exc:
        # Log only type, never exception text (connection strings may hold secrets).
        log.warning('standards retrieval unavailable: %s', type(exc).__name__)
        rules['notes'].append('Retrieval unavailable; decisions are unsupported by corpus citations.')
    response = None
    if llm is not None:
        try:
            response = llm.classify(make_request(profile, data, rules, candidates))
        except Exception as exc:
            log.warning('standards classifier unavailable: %s', type(exc).__name__)
    guarded = guard(rules, response, data, candidates, profile.device_specific_standards)
    return assemble(profile, data, rules, guarded, getattr(llm, 'model', 'unavailable'), generated_at=generated_at)
