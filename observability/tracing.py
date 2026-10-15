"""Opt-in manual observations. Disabled calls never import the Langfuse SDK.

Only counts, ids, model and usage belong here. Never pass prompts or bodies.
The SDK's separate kill switch also short-circuits before client creation.
"""
from contextlib import nullcontext
from functools import wraps
import os


def enabled():
    return (os.getenv('LANGFUSE_ENABLED', '').lower() in {'1', 'true', 'yes'}
            and os.getenv('LANGFUSE_TRACING_ENABLED', 'true').lower() != 'false')


def _get_client():
    from langfuse import get_client
    return get_client()


def stage(name, *, as_type='span', **attributes):
    if not enabled():
        return nullcontext(None)
    return _get_client().start_as_current_observation(name=name, as_type=as_type, **attributes)


def traced(name, *, as_type='span'):
    """Wrap a root or tool without capturing function arguments or returns."""
    def decorate(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            with stage(name, as_type=as_type):
                return fn(*args, **kwargs)
        return wrapped
    return decorate


def record_response(observation, response):
    """Read only already materialised SDK response fields; omit absent usage."""
    if observation is None:
        return
    usage = getattr(response, 'usage', None)
    details = {}
    for source, target in [('input_tokens', 'input'), ('output_tokens', 'output'),
                           ('cache_read_input_tokens', 'cache_read_input_tokens'),
                           ('cache_creation_input_tokens', 'cache_creation_input_tokens')]:
        value = usage.get(source) if isinstance(usage, dict) else getattr(usage, source, None)
        if isinstance(value, int):
            details[target] = value
    attributes = {'model': response.model} if getattr(response, 'model', None) else {}
    if details:
        attributes['usage_details'] = details
    if attributes:
        observation.update(**attributes)


def flush():
    if enabled():
        _get_client().flush()
