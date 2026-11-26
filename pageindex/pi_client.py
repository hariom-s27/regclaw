"""Resilient, budgeted, logged API client wrappers for Part II (Anthropic, LiteLLM, Azure judge)."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from dataclasses import dataclass, field
import json
import logging
import os
from pathlib import Path
import sys
import threading
import time
from typing import Any, Callable
import uuid

# Use LiteLLM's bundled model cost map instead of fetching raw.githubusercontent.com on import.
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

import anthropic
import httpx

try:
    from openai import APITimeoutError as _OpenAITimeoutError, OpenAIError as _OpenAIError
except ImportError:  # pragma: no cover
    class _OpenAIError(Exception):  # type: ignore[no-redef]
        pass

    class _OpenAITimeoutError(Exception):  # type: ignore[no-redef]
        pass

if __package__ in (None, ""):
    _repo_root = str(Path(__file__).resolve().parents[1])
    if _repo_root not in sys.path:
        sys.path.insert(0, _repo_root)

from eval.s11.ledger import (  # noqa: E402
    CapExceededError,
    CostLedger,
    Reservation,
    UsageLogger,
    count_chars_in_payload,
    estimate_input_tokens,
    normalize_model_name,
    now_utc_iso,
)

log = logging.getLogger("pageindex.client")

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BACKOFF_SCHEDULE: tuple[float, ...] = (2.0, 4.0, 8.0)


class ModelVersionChangedError(_OpenAIError, RuntimeError):
    """Raised immediately when an Azure deployment returns a different model version."""

    def __init__(
        self,
        *,
        deployment: str,
        expected_model: str,
        observed_model: str,
        lane: str,
    ) -> None:
        self.deployment = deployment
        self.expected_model = expected_model
        self.observed_model = observed_model
        self.lane = lane
        super().__init__(
            f"Model version drift on {lane} deployment {deployment!r}: "
            f"expected {expected_model!r}, got {observed_model!r}"
        )


def _extract_status_code(exc: BaseException) -> int | None:
    code = getattr(exc, "status_code", None)
    if code is not None:
        try:
            return int(code)
        except (TypeError, ValueError):
            pass
    resp = getattr(exc, "response", None)
    if resp is not None:
        code = getattr(resp, "status_code", None)
        if code is not None:
            try:
                return int(code)
            except (TypeError, ValueError):
                pass
    return None


def _is_timeout_exception(exc: BaseException) -> bool:
    return isinstance(exc, (TimeoutError, httpx.TimeoutException, anthropic.APITimeoutError, _OpenAITimeoutError))


def _is_connection_exception(exc: BaseException) -> bool:
    if _is_timeout_exception(exc):
        return False
    return isinstance(exc, (anthropic.APIConnectionError, httpx.TransportError, ConnectionError))


def _classify_anthropic_exception(exc: BaseException) -> tuple[str, int | None, bool, bool]:
    """Return (status, status_code, is_timeout, retryable)."""
    if _is_timeout_exception(exc):
        return "timeout", _extract_status_code(exc), True, True
    code = _extract_status_code(exc)
    if code is not None:
        retryable = code in (408, 429) or code >= 500
        return "error", code, False, retryable
    if _is_connection_exception(exc):
        return "error", None, False, True
    return "error", None, False, False


def _has_cache_control(obj: Any) -> bool:
    if isinstance(obj, dict):
        if "cache_control" in obj:
            return True
        return any(_has_cache_control(v) for v in obj.values())
    if isinstance(obj, (list, tuple)):
        return any(_has_cache_control(x) for x in obj)
    return False


def _usage_ints(usage: Any) -> tuple[int, int, int, int]:
    """Extract (input_tokens, output_tokens, cache_read_input_tokens, cache_creation_input_tokens)."""
    if usage is None:
        return 0, 0, 0, 0
    if isinstance(usage, dict):
        get = usage.get
    else:
        get = lambda k, d=0: getattr(usage, k, d)
    inp = int(get("input_tokens", None) or get("prompt_tokens", None) or 0)
    out = int(get("output_tokens", None) or get("completion_tokens", None) or 0)
    cr = int(get("cache_read_input_tokens", None) or 0)
    if cr == 0:
        details = get("prompt_tokens_details", None)
        if isinstance(details, dict):
            cr = int(details.get("cached_tokens", None) or 0)
        elif details is not None:
            cr = int(getattr(details, "cached_tokens", None) or 0)
    cw = int(get("cache_creation_input_tokens", None) or 0)
    return inp, out, cr, cw


def _extract_effort(kwargs: dict[str, Any]) -> str | None:
    cfg = kwargs.get("output_config")
    if cfg is None:
        return None
    val = cfg.get("effort") if isinstance(cfg, dict) else getattr(cfg, "effort", None)
    return None if val is None else str(val)


def _extract_thinking_tokens(usage: Any) -> int | None:
    if usage is None:
        return None
    details = (
        usage.get("output_tokens_details")
        if isinstance(usage, dict)
        else getattr(usage, "output_tokens_details", None)
    )
    if details is None:
        return None
    tt = (
        details.get("thinking_tokens")
        if isinstance(details, dict)
        else getattr(details, "thinking_tokens", None)
    )
    if tt is None or isinstance(tt, bool):
        return None
    return int(tt)


def _assert_ends_with_user(messages: Any) -> None:
    """Guard against assistant-ending messages (prefill 400 on Sonnet/Haiku 5.5)."""
    if messages is None:
        raise ValueError("Anthropic call requires a non-empty messages sequence ending with role='user'")
    seq = list(messages) if not isinstance(messages, (list, tuple)) else messages
    if not seq:
        raise ValueError("Anthropic call requires a non-empty messages sequence ending with role='user'")
    last = seq[-1]
    role = last.get("role") if isinstance(last, dict) else getattr(last, "role", None)
    if role != "user":
        raise ValueError(f"Anthropic messages must end with role='user' (assistant prefill rejected), got {role!r}")


class _MessagesProxy:
    def __init__(self, parent: "AnthropicRetryClient") -> None:
        self._parent = parent

    def create(self, **kwargs: Any) -> Any:
        return self._parent._create_message(**kwargs)

    def count_tokens(self, **kwargs: Any) -> Any:
        return self._parent._count_tokens(**kwargs)


class AnthropicRetryClient:
    """Drop-in Anthropic client wrapper with per-attempt cost reservation, backoff retry, and JSONL logging."""

    def __init__(
        self,
        sdk_client: Any,
        *,
        ledger: CostLedger,
        usage_log: UsageLogger,
        lane: str,
        timeout_s: float | None = None,
        default_max_tokens: int | None = None,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        backoff_schedule: tuple[float, ...] = DEFAULT_BACKOFF_SCHEDULE,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        self._sdk = sdk_client
        self.ledger = ledger
        self.usage_log = usage_log
        self.lane = lane
        self.timeout_s = timeout_s
        self.default_max_tokens = default_max_tokens
        self.max_attempts = max(1, int(max_attempts))
        self.backoff_schedule = tuple(backoff_schedule)
        self._sleep = sleep_fn
        self.retries = 0
        self.messages = _MessagesProxy(self)

    def _create_message(self, **kwargs: Any) -> Any:
        _assert_ends_with_user(kwargs.get("messages"))
        call_id = f"ant-{uuid.uuid4().hex[:12]}"
        model_req = str(kwargs.get("model") or "")
        effective_timeout = kwargs.get("timeout", self.timeout_s)
        effort = _extract_effort(kwargs)
        max_tokens = kwargs.get("max_tokens") or self.default_max_tokens
        if max_tokens is None or int(max_tokens) <= 0:
            raise ValueError(f"Anthropic call requires positive max_tokens, got {max_tokens!r}")
        max_tokens = int(max_tokens)

        payload_chars = count_chars_in_payload(
            {
                "system": kwargs.get("system"),
                "messages": kwargs.get("messages"),
                "tools": kwargs.get("tools"),
            }
        )
        input_est = estimate_input_tokens(payload_chars)
        cache_ctrl = _has_cache_control({"system": kwargs.get("system"), "messages": kwargs.get("messages"), "tools": kwargs.get("tools")})

        for attempt in range(1, self.max_attempts + 1):
            try:
                res = self.ledger.reserve(
                    call_id=call_id,
                    lane=self.lane,
                    model=model_req,
                    attempt=attempt,
                    input_estimate=input_est,
                    max_tokens=max_tokens,
                    max_tokens_source="explicit",
                    has_cache_control=cache_ctrl,
                )
            except CapExceededError as exc:
                self.usage_log.log(
                    provider="anthropic",
                    lane=self.lane,
                    model_requested=model_req,
                    model_returned=None,
                    attempt=attempt,
                    status="cap_refused",
                    status_code=None,
                    error_type=type(exc).__name__,
                    cost_usd=0.0,
                    latency_s=0.0,
                    timeout_s=effective_timeout,
                    call_id=call_id,
                    effort=effort,
                    thinking_tokens=None,
                )
                raise

            t0 = time.perf_counter()
            try:
                resp = self._sdk.messages.create(**kwargs)
            except Exception as exc:
                latency = time.perf_counter() - t0
                status, status_code, is_timeout, retryable = _classify_anthropic_exception(exc)
                st = self.ledger.settle(
                    res.reservation_id,
                    status=status,
                    timeout_retained=is_timeout,
                )
                self.usage_log.log(
                    provider="anthropic",
                    lane=self.lane,
                    model_requested=model_req,
                    model_returned=None,
                    attempt=attempt,
                    status=status,
                    status_code=status_code,
                    error_type=type(exc).__name__,
                    cost_usd=float(st.actual_usd),
                    latency_s=latency,
                    timeout_s=effective_timeout,
                    call_id=call_id,
                    effort=effort,
                    thinking_tokens=None,
                )
                if retryable and attempt < self.max_attempts:
                    delay = self.backoff_schedule[min(attempt - 1, len(self.backoff_schedule) - 1)]
                    self.retries += 1
                    log.warning(
                        "Anthropic %s call retrying attempt %d/%d after %s (sleep %.1fs)",
                        self.lane,
                        attempt + 1,
                        self.max_attempts,
                        type(exc).__name__,
                        delay,
                    )
                    self._sleep(delay)
                    continue
                raise

            latency = time.perf_counter() - t0
            model_ret = getattr(resp, "model", None)
            stop_reason = getattr(resp, "stop_reason", None)
            usage_obj = getattr(resp, "usage", None)
            inp, out, cr, cw = _usage_ints(usage_obj)
            thinking_toks = _extract_thinking_tokens(usage_obj)
            call_status = "model_refusal" if stop_reason == "refusal" else "ok"
            err_type = "model_refusal" if stop_reason == "refusal" else None
            st = self.ledger.settle(
                res.reservation_id,
                input_tokens=inp,
                output_tokens=out,
                cache_read_input_tokens=cr,
                cache_creation_input_tokens=cw,
                status=call_status,
            )
            self.usage_log.log(
                provider="anthropic",
                lane=self.lane,
                model_requested=model_req,
                model_returned=model_ret,
                attempt=attempt,
                status=call_status,
                status_code=200,
                error_type=err_type,
                input_tokens=inp,
                output_tokens=out,
                cache_read_input_tokens=cr,
                cache_creation_input_tokens=cw,
                cost_usd=float(st.actual_usd),
                latency_s=latency,
                timeout_s=effective_timeout,
                call_id=call_id,
                effort=effort,
                thinking_tokens=thinking_toks,
            )
            return resp

    def _count_tokens(self, **kwargs: Any) -> Any:
        """Count prompt tokens via Anthropic count_tokens without ledger reserve (cost=0)."""
        _assert_ends_with_user(kwargs.get("messages"))
        call_id = f"ant-{uuid.uuid4().hex[:12]}"
        model_req = str(kwargs.get("model") or "")
        effective_timeout = kwargs.get("timeout", self.timeout_s)
        effort = _extract_effort(kwargs)

        for attempt in range(1, self.max_attempts + 1):
            t0 = time.perf_counter()
            try:
                resp = self._sdk.messages.count_tokens(**kwargs)
            except Exception as exc:
                latency = time.perf_counter() - t0
                status, status_code, _is_timeout, retryable = _classify_anthropic_exception(exc)
                self.usage_log.log(
                    provider="anthropic",
                    lane="count_tokens",
                    model_requested=model_req,
                    model_returned=None,
                    attempt=attempt,
                    status=status,
                    status_code=status_code,
                    error_type=type(exc).__name__,
                    cost_usd=0.0,
                    latency_s=latency,
                    timeout_s=effective_timeout,
                    call_id=call_id,
                    effort=effort,
                    thinking_tokens=None,
                )
                if retryable and attempt < self.max_attempts:
                    delay = self.backoff_schedule[min(attempt - 1, len(self.backoff_schedule) - 1)]
                    self.retries += 1
                    self._sleep(delay)
                    continue
                raise

            latency = time.perf_counter() - t0
            inp = int(getattr(resp, "input_tokens", 0) or 0)
            self.usage_log.log(
                provider="anthropic",
                lane="count_tokens",
                model_requested=model_req,
                model_returned=None,
                attempt=attempt,
                status="ok",
                status_code=200,
                error_type=None,
                input_tokens=inp,
                output_tokens=0,
                cache_read_input_tokens=0,
                cache_creation_input_tokens=0,
                cost_usd=0.0,
                latency_s=latency,
                timeout_s=effective_timeout,
                call_id=call_id,
                effort=effort,
                thinking_tokens=None,
            )
            return resp


# --------------------------------------------------------------------------- LiteLLM / PageIndex hook
def _provider_for_litellm_model(model: str) -> str:
    return "azure_openai" if model.startswith("openai/") else "anthropic"


@dataclass
class _PendingLiteLLMCall:
    call_id: str
    lane: str
    provider: str
    model_requested: str
    timeout_s: float | None
    started_perf: float
    reservation: Reservation | None
    billable: bool


@dataclass
class LiteLLMHookState:
    ledger: CostLedger
    usage_log: UsageLogger
    tree_reserve_max_tokens: int
    lane: str = "tree"
    lock: threading.Lock = field(default_factory=threading.Lock)
    _cond: threading.Condition = field(init=False, repr=False)
    tracked_reservation_ids: list[str] = field(default_factory=list)
    refusal_error: BaseException | None = None
    open_reservations: list[str] = field(default_factory=list)
    wrapper_calls: int = 0
    custom_logger_calls: int = 0
    fewer_logger_events: bool = False
    bypass_detected: bool = False
    _seen_logger_call_ids: set[str] = field(default_factory=set, repr=False)

    def __post_init__(self) -> None:
        self._cond = threading.Condition(self.lock)

    def record_refusal(self, exc: BaseException) -> None:
        with self._cond:
            if self.refusal_error is None:
                self.refusal_error = exc

    def record_custom_logger_event(self, call_id: str | None = None) -> None:
        with self._cond:
            if call_id:
                if call_id in self._seen_logger_call_ids:
                    return
                self._seen_logger_call_ids.add(call_id)
            self.custom_logger_calls += 1
            self._cond.notify_all()

    def wait_for_custom_logger(self, timeout_s: float) -> None:
        """Wait up to timeout_s while CustomLogger count lags wrapper_calls."""
        deadline = time.monotonic() + max(0.0, float(timeout_s))
        with self._cond:
            while self.custom_logger_calls < self.wrapper_calls:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._cond.wait(timeout=remaining)

    def start_attempt(self, *, call_id: str, model: str, messages: Any, kwargs: dict[str, Any]) -> _PendingLiteLLMCall:
        provider = _provider_for_litellm_model(model)
        norm = normalize_model_name(model)
        billable = self.ledger.is_billable(norm)
        timeout_s = kwargs.get("timeout")
        explicit_max = kwargs.get("max_tokens")
        if explicit_max is not None:
            max_tokens = int(explicit_max)
            max_tokens_source = "explicit"
        else:
            max_tokens = self.tree_reserve_max_tokens
            max_tokens_source = "tree_reserve_max_tokens"

        reservation: Reservation | None = None
        if billable:
            chars = count_chars_in_payload({"messages": messages, "tools": kwargs.get("tools")})
            input_est = estimate_input_tokens(chars)
            try:
                reservation = self.ledger.reserve(
                    call_id=call_id,
                    lane=self.lane,
                    model=norm,
                    attempt=1,
                    input_estimate=input_est,
                    max_tokens=max_tokens,
                    max_tokens_source=max_tokens_source,
                    has_cache_control=_has_cache_control({"messages": messages, "tools": kwargs.get("tools")}),
                )
            except CapExceededError as exc:
                self.record_refusal(exc)
                self.usage_log.log(
                    provider=provider,
                    lane=self.lane,
                    model_requested=model,
                    model_returned=None,
                    attempt=1,
                    status="cap_refused",
                    status_code=None,
                    error_type=type(exc).__name__,
                    cost_usd=0.0,
                    latency_s=0.0,
                    timeout_s=timeout_s,
                    call_id=call_id,
                )
                raise

        entry = _PendingLiteLLMCall(
            call_id=call_id,
            lane=self.lane,
            provider=provider,
            model_requested=model,
            timeout_s=timeout_s,
            started_perf=time.perf_counter(),
            reservation=reservation,
            billable=billable,
        )
        with self._cond:
            self.wrapper_calls += 1
            if reservation is not None:
                self.tracked_reservation_ids.append(reservation.reservation_id)
            self._cond.notify_all()
        return entry

    def handle_success(self, entry: _PendingLiteLLMCall, response_obj: Any) -> None:
        latency = max(0.0, time.perf_counter() - entry.started_perf)
        model_ret = getattr(response_obj, "model", None)
        inp, out, cr, cw = _usage_ints(getattr(response_obj, "usage", None))
        cost_usd: float | None = None
        if entry.reservation is not None:
            st = self.ledger.settle(
                entry.reservation.reservation_id,
                input_tokens=inp,
                output_tokens=out,
                cache_read_input_tokens=cr,
                cache_creation_input_tokens=cw,
                status="ok",
            )
            cost_usd = float(st.actual_usd)
        self.usage_log.log(
            provider=entry.provider,
            lane=entry.lane,
            model_requested=entry.model_requested,
            model_returned=model_ret,
            attempt=1,
            status="ok",
            status_code=200,
            error_type=None,
            input_tokens=inp,
            output_tokens=out,
            cache_read_input_tokens=cr,
            cache_creation_input_tokens=cw,
            cost_usd=cost_usd,
            latency_s=latency,
            timeout_s=entry.timeout_s,
            call_id=entry.call_id,
        )

    def handle_failure(self, entry: _PendingLiteLLMCall, exception: BaseException) -> None:
        latency = max(0.0, time.perf_counter() - entry.started_perf)
        is_timeout = _is_timeout_exception(exception)
        status = "timeout" if is_timeout else "error"
        status_code = _extract_status_code(exception)
        error_type = type(exception).__name__
        cost_usd: float | None = None
        if entry.reservation is not None:
            st = self.ledger.settle(
                entry.reservation.reservation_id,
                status=status,
                timeout_retained=is_timeout,
            )
            cost_usd = float(st.actual_usd)
        self.usage_log.log(
            provider=entry.provider,
            lane=entry.lane,
            model_requested=entry.model_requested,
            model_returned=None,
            attempt=1,
            status=status,
            status_code=status_code,
            error_type=error_type,
            cost_usd=cost_usd,
            latency_s=latency,
            timeout_s=entry.timeout_s,
            call_id=entry.call_id,
        )


def _call_id_from_litellm_kwargs(kwargs: Any) -> str | None:
    if not isinstance(kwargs, dict):
        return None
    cid = kwargs.get("litellm_call_id")
    if cid:
        return str(cid)
    params = kwargs.get("litellm_params")
    if isinstance(params, dict) and params.get("litellm_call_id"):
        return str(params["litellm_call_id"])
    return None


def _build_custom_logger(state: LiteLLMHookState, litellm_mod: Any):
    """Cross-check CustomLogger that counts LiteLLM events independently of the wrapper."""
    base_cls: type = object
    try:
        from litellm.integrations.custom_logger import CustomLogger  # type: ignore

        base_cls = CustomLogger
    except Exception:
        base_cls = getattr(litellm_mod, "CustomLogger", object) or object

    class PageIndexLiteLLMLogger(base_cls):  # type: ignore[misc,valid-type]
        def __init__(self) -> None:
            if base_cls is not object:
                super().__init__(message_logging=True)
            self._state = state

        def log_pre_api_call(self, model, messages, kwargs):
            return None

        def log_success_event(self, kwargs, response_obj, start_time, end_time):
            self._state.record_custom_logger_event(_call_id_from_litellm_kwargs(kwargs))

        async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
            self._state.record_custom_logger_event(_call_id_from_litellm_kwargs(kwargs))

        def log_failure_event(self, kwargs, response_obj, start_time, end_time):
            self._state.record_custom_logger_event(_call_id_from_litellm_kwargs(kwargs))

        async def async_log_failure_event(self, kwargs, response_obj, start_time, end_time):
            self._state.record_custom_logger_event(_call_id_from_litellm_kwargs(kwargs))

    return PageIndexLiteLLMLogger()


def _flush_litellm_async_worker(timeout_s: float) -> None:
    """Drain LiteLLM's GLOBAL_LOGGING_WORKER queue on a fresh loop when no loop is running."""
    try:
        asyncio.get_running_loop()
        return
    except RuntimeError:
        pass
    try:
        from litellm.litellm_core_utils.logging_worker import GLOBAL_LOGGING_WORKER  # type: ignore
    except Exception:
        return
    q = getattr(GLOBAL_LOGGING_WORKER, "_queue", None)
    if q is None:
        return
    try:
        asyncio.run(asyncio.wait_for(GLOBAL_LOGGING_WORKER.flush(), timeout=max(0.1, float(timeout_s))))
    except Exception:
        pass


def _attach_logger(litellm_mod: Any, logger_obj: Any) -> list[str] | None:
    mgr = getattr(litellm_mod, "logging_callback_manager", None)
    if mgr is not None and hasattr(mgr, "add_litellm_callback"):
        mgr.add_litellm_callback(logger_obj)
        return None
    orig_callbacks = list(getattr(litellm_mod, "callbacks", []) or [])
    litellm_mod.callbacks = [*orig_callbacks, logger_obj]
    return orig_callbacks


def _detach_logger(litellm_mod: Any, logger_obj: Any, orig_callbacks: list[str] | None) -> None:
    mgr = getattr(litellm_mod, "logging_callback_manager", None)
    if mgr is not None and hasattr(mgr, "remove_callback_from_all_lists"):
        mgr.remove_callback_from_all_lists(logger_obj, require_self=False)
    if orig_callbacks is not None:
        litellm_mod.callbacks = orig_callbacks
    for attr in ("callbacks", "input_callback", "_async_input_callback", "success_callback", "failure_callback",
                 "_async_success_callback", "_async_failure_callback"):
        lst = getattr(litellm_mod, attr, None)
        if isinstance(lst, list):
            while logger_obj in lst:
                lst.remove(logger_obj)


@contextmanager
def litellm_usage_logger(
    ledger: CostLedger,
    usage_log: UsageLogger,
    *,
    tree_reserve_max_tokens: int = 32000,
    lane: str = "tree",
    settle_timeout_s: float = 10.0,
    litellm_module: Any = None,
):
    """Wrap litellm.completion / acompletion as primary accounting path with CustomLogger cross-check."""
    using_real_litellm = litellm_module is None
    if litellm_module is None:
        try:
            import litellm as litellm_module  # type: ignore
        except ImportError:
            if os.environ.get("REGCLAW_STUB") == "1":
                yield LiteLLMHookState(
                    ledger=ledger,
                    usage_log=usage_log,
                    tree_reserve_max_tokens=tree_reserve_max_tokens,
                    lane=lane,
                )
                return
            raise

    state = LiteLLMHookState(
        ledger=ledger,
        usage_log=usage_log,
        tree_reserve_max_tokens=tree_reserve_max_tokens,
        lane=lane,
    )
    logger_obj = _build_custom_logger(state, litellm_module)
    orig_completion = litellm_module.completion
    orig_acompletion = getattr(litellm_module, "acompletion", None)
    orig_callbacks = _attach_logger(litellm_module, logger_obj)

    def _prepare(args: tuple[Any, ...], kwargs: dict[str, Any]) -> tuple[_PendingLiteLLMCall, dict[str, Any]]:
        call_kwargs = dict(kwargs)
        call_id = str(call_kwargs.get("litellm_call_id") or f"ltm-{uuid.uuid4().hex[:12]}")
        call_kwargs["litellm_call_id"] = call_id
        model = str(args[0] if args else call_kwargs.get("model") or "")
        messages = args[1] if len(args) > 1 else call_kwargs.get("messages")
        entry = state.start_attempt(call_id=call_id, model=model, messages=messages, kwargs=call_kwargs)
        return entry, call_kwargs

    def wrapped_completion(*args: Any, **kwargs: Any) -> Any:
        entry, call_kwargs = _prepare(args, kwargs)
        try:
            resp = orig_completion(*args, **call_kwargs)
        except Exception as exc:
            state.handle_failure(entry, exc)
            raise
        state.handle_success(entry, resp)
        return resp

    async def wrapped_acompletion(*args: Any, **kwargs: Any) -> Any:
        assert orig_acompletion is not None
        entry, call_kwargs = _prepare(args, kwargs)
        try:
            resp = await orig_acompletion(*args, **call_kwargs)
        except Exception as exc:
            state.handle_failure(entry, exc)
            raise
        state.handle_success(entry, resp)
        return resp

    litellm_module.completion = wrapped_completion
    if orig_acompletion is not None:
        litellm_module.acompletion = wrapped_acompletion

    try:
        yield state
    finally:
        litellm_module.completion = orig_completion
        if orig_acompletion is not None:
            litellm_module.acompletion = orig_acompletion
        if using_real_litellm or getattr(litellm_module, "__name__", None) == "litellm":
            _flush_litellm_async_worker(settle_timeout_s)
        state.wait_for_custom_logger(settle_timeout_s)
        state.open_reservations = ledger.open_reservation_ids(state.tracked_reservation_ids)
        if state.custom_logger_calls > state.wrapper_calls:
            state.bypass_detected = True
            log.error(
                "LiteLLM CustomLogger counted more calls (%d) than wrapper (%d); call path bypassed wrapper",
                state.custom_logger_calls,
                state.wrapper_calls,
            )
        elif state.custom_logger_calls < state.wrapper_calls:
            state.fewer_logger_events = True
            log.warning(
                "LiteLLM CustomLogger counted fewer calls (%d) than wrapper (%d)",
                state.custom_logger_calls,
                state.wrapper_calls,
            )
        _detach_logger(litellm_module, logger_obj, orig_callbacks)


# --------------------------------------------------------------------------- Azure judge wrapper (httpx transport)
class ModelVersionGuard:
    """Locks each deployment to the first observed response model version and stores any violations."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._observed: dict[str, str] = {}
        self._violations: list[dict[str, Any]] = []

    @property
    def observed_models(self) -> dict[str, str]:
        with self._lock:
            return dict(self._observed)

    @property
    def violations(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(v) for v in self._violations]

    def check(self, *, deployment: str, returned_model: str | None, lane: str) -> str | None:
        if not returned_model:
            return None
        model_str = str(returned_model).strip()
        if not model_str:
            return None
        with self._lock:
            expected = self._observed.get(deployment)
            if expected is None:
                self._observed[deployment] = model_str
                return model_str
            if model_str != expected:
                self._violations.append(
                    {
                        "ts_utc": now_utc_iso(),
                        "lane": lane,
                        "deployment": deployment,
                        "expected_model": expected,
                        "observed_model": model_str,
                    }
                )
                raise ModelVersionChangedError(
                    deployment=deployment,
                    expected_model=expected,
                    observed_model=model_str,
                    lane=lane,
                )
            return model_str


def _azure_timeout_from_request(request: httpx.Request, default_timeout: float | None) -> float | None:
    ext = (request.extensions or {}).get("timeout")
    if isinstance(ext, dict):
        for key in ("read", "connect", "pool"):
            val = ext.get(key)
            if val is not None:
                try:
                    return float(val)
                except (TypeError, ValueError):
                    pass
    return default_timeout


def _rebuild_httpx_response(resp: httpx.Response, raw_bytes: bytes, request: httpx.Request) -> httpx.Response:
    headers = [
        (k, v)
        for k, v in resp.headers.multi_items()
        if k.lower() not in ("content-encoding", "transfer-encoding", "content-length")
    ]
    return httpx.Response(
        status_code=resp.status_code,
        headers=headers,
        content=raw_bytes,
        request=request,
        extensions=resp.extensions,
    )


class GuardedAsyncTransport(httpx.AsyncBaseTransport):
    """httpx AsyncBaseTransport performing retry, usage.jsonl logging, and ModelVersionGuard checks."""

    def __init__(
        self,
        *,
        usage_log: UsageLogger,
        guard: ModelVersionGuard,
        inner: httpx.AsyncBaseTransport | None = None,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        backoff_schedule: tuple[float, ...] = DEFAULT_BACKOFF_SCHEDULE,
        sleep_fn: Callable[[float], Any] = asyncio.sleep,
        timeout_s: float | None = None,
    ) -> None:
        self._usage_log = usage_log
        self._guard = guard
        self._owns_inner = inner is None
        self._inner = (
            inner
            if inner is not None
            else httpx.AsyncHTTPTransport(limits=httpx.Limits(max_keepalive_connections=0))
        )
        self._max_attempts = max(1, int(max_attempts))
        self._backoff_schedule = tuple(backoff_schedule)
        self._sleep = sleep_fn
        self._timeout_s = timeout_s

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        call_id = f"az-{uuid.uuid4().hex[:12]}"
        req_model = "unknown"
        try:
            body_bytes = await request.aread()
            if body_bytes:
                parsed = json.loads(body_bytes.decode("utf-8"))
                if isinstance(parsed, dict) and parsed.get("model"):
                    req_model = str(parsed["model"])
        except Exception:
            pass

        path_str = request.url.path or ""
        lane = "embedding" if "embeddings" in path_str else "judge"
        effective_timeout = _azure_timeout_from_request(request, self._timeout_s)

        for attempt in range(1, self._max_attempts + 1):
            t0 = time.perf_counter()
            try:
                resp = await self._inner.handle_async_request(request)
            except (httpx.TimeoutException, TimeoutError) as exc:
                latency = time.perf_counter() - t0
                self._usage_log.log(
                    provider="azure_openai",
                    lane=lane,
                    model_requested=req_model,
                    model_returned=None,
                    attempt=attempt,
                    status="timeout",
                    status_code=None,
                    error_type=type(exc).__name__,
                    cost_usd=None,
                    latency_s=latency,
                    timeout_s=effective_timeout,
                    call_id=call_id,
                )
                if attempt < self._max_attempts:
                    delay = self._backoff_schedule[min(attempt - 1, len(self._backoff_schedule) - 1)]
                    await self._sleep(delay)
                    continue
                raise
            except (httpx.TransportError, OSError) as exc:
                latency = time.perf_counter() - t0
                self._usage_log.log(
                    provider="azure_openai",
                    lane=lane,
                    model_requested=req_model,
                    model_returned=None,
                    attempt=attempt,
                    status="error",
                    status_code=None,
                    error_type=type(exc).__name__,
                    cost_usd=None,
                    latency_s=latency,
                    timeout_s=effective_timeout,
                    call_id=call_id,
                )
                if attempt < self._max_attempts:
                    delay = self._backoff_schedule[min(attempt - 1, len(self._backoff_schedule) - 1)]
                    await self._sleep(delay)
                    continue
                raise

            try:
                raw_bytes = await resp.aread()
            finally:
                await resp.aclose()
            latency = time.perf_counter() - t0
            rebuilt = _rebuild_httpx_response(resp, raw_bytes, request)

            if resp.status_code >= 400:
                retryable = resp.status_code in (408, 429) or resp.status_code >= 500
                self._usage_log.log(
                    provider="azure_openai",
                    lane=lane,
                    model_requested=req_model,
                    model_returned=None,
                    attempt=attempt,
                    status="error",
                    status_code=resp.status_code,
                    error_type=f"HTTP{resp.status_code}",
                    cost_usd=None,
                    latency_s=latency,
                    timeout_s=effective_timeout,
                    call_id=call_id,
                )
                if retryable and attempt < self._max_attempts:
                    delay = self._backoff_schedule[min(attempt - 1, len(self._backoff_schedule) - 1)]
                    await self._sleep(delay)
                    continue
                return rebuilt

            data: dict[str, Any] = {}
            try:
                parsed_resp = json.loads(raw_bytes.decode("utf-8"))
                if isinstance(parsed_resp, dict):
                    data = parsed_resp
            except Exception:
                pass
            model_ret = data.get("model")
            inp, out, cr, cw = _usage_ints(data.get("usage"))
            try:
                self._guard.check(deployment=req_model, returned_model=model_ret, lane=lane)
            except ModelVersionChangedError as exc:
                self._usage_log.log(
                    provider="azure_openai",
                    lane=lane,
                    model_requested=req_model,
                    model_returned=model_ret,
                    attempt=attempt,
                    status="model_version_changed",
                    status_code=resp.status_code,
                    error_type=type(exc).__name__,
                    input_tokens=inp,
                    output_tokens=out,
                    cache_read_input_tokens=cr,
                    cache_creation_input_tokens=cw,
                    cost_usd=None,
                    latency_s=latency,
                    timeout_s=effective_timeout,
                    call_id=call_id,
                )
                raise

            self._usage_log.log(
                provider="azure_openai",
                lane=lane,
                model_requested=req_model,
                model_returned=model_ret,
                attempt=attempt,
                status="ok",
                status_code=resp.status_code,
                error_type=None,
                input_tokens=inp,
                output_tokens=out,
                cache_read_input_tokens=cr,
                cache_creation_input_tokens=cw,
                cost_usd=None,
                latency_s=latency,
                timeout_s=effective_timeout,
                call_id=call_id,
            )
            return rebuilt

        raise RuntimeError("Unreachable retry exhaustion in GuardedAsyncTransport")

    async def aclose(self) -> None:
        if self._owns_inner:
            await self._inner.aclose()


def build_guarded_async_openai(
    *,
    base_url: str,
    api_key: str,
    timeout: float,
    usage_log: UsageLogger,
    guard: ModelVersionGuard | None = None,
    inner_transport: httpx.AsyncBaseTransport | None = None,
    async_openai_cls: Any = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    backoff_schedule: tuple[float, ...] = DEFAULT_BACKOFF_SCHEDULE,
    sleep_fn: Callable[[float], Any] = asyncio.sleep,
) -> Any:
    """Create a genuine openai.AsyncOpenAI wired to GuardedAsyncTransport (no ledger; Azure only)."""
    if async_openai_cls is None:
        from openai import AsyncOpenAI

        async_openai_cls = AsyncOpenAI
    guard_obj = guard if guard is not None else ModelVersionGuard()
    transport = GuardedAsyncTransport(
        usage_log=usage_log,
        guard=guard_obj,
        inner=inner_transport,
        max_attempts=max_attempts,
        backoff_schedule=backoff_schedule,
        sleep_fn=sleep_fn,
        timeout_s=timeout,
    )
    http_client = httpx.AsyncClient(transport=transport, timeout=timeout)
    client = async_openai_cls(
        base_url=base_url,
        api_key=api_key,
        max_retries=0,
        timeout=timeout,
        http_client=http_client,
    )
    try:
        setattr(client, "_model_guard", guard_obj)
        setattr(client, "_guarded_http_client", http_client)
    except Exception:
        pass
    return client
