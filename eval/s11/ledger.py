"""Append-only cost ledger, usage JSONL logger, OS paid lock, and write-once helpers for S11."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
import json
import logging
import os
from pathlib import Path
import threading
import time
from typing import Any, Iterable
import uuid

import yaml

log = logging.getLogger("s11.ledger")

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PRICES_PATH = REPO_ROOT / "pageindex" / "pi_prices.yaml"
DEFAULT_CAP_USD = Decimal("60.0")
_USD_QUANT = Decimal("0.0000000001")


class PaidLockHeld(RuntimeError):
    """Raised when another paid S11 process already holds the lock file."""


class CapExceededError(RuntimeError):
    """Raised before an attempt when reserving its worst-case cost would exceed the USD cap."""


class DuplicateReservationError(ValueError):
    """Raised if a reservation_id is reused inside the append-only ledger."""


class UnknownReservationError(KeyError):
    """Raised when settling an unknown reservation_id."""


class AlreadySettledError(ValueError):
    """Raised if an attempt is made to settle a reservation_id twice."""


class UnknownModelPriceError(KeyError):
    """Raised when a model is absent from pi_prices.yaml."""


class WriteOnceError(FileExistsError):
    """Raised when write_once_json targets an already existing path."""


def now_utc_iso() -> str:
    """Current UTC timestamp in ISO-8601 Z format."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def default_s11_paths(root: Path | None = None) -> tuple[Path, Path, Path]:
    """Return CLI/driver default paths (ledger, usage_log, lock) without touching disk."""
    base = Path(root) if root is not None else REPO_ROOT
    return (
        base / "data" / "runs" / "s11" / "ledger.jsonl",
        base / "data" / "runs" / "s11" / "usage.jsonl",
        base / "data" / "runs" / "s11_paid.lock",
    )


@contextmanager
def paid_lock(root: Path | None = None, *, lock_path: Path | None = None):
    """Acquire an exclusive non-blocking OS lock for paid S11 runs."""
    base = Path(root) if root is not None else REPO_ROOT
    path = Path(lock_path) if lock_path is not None else (base / "data" / "runs" / "s11_paid.lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT)
    acquired = False
    try:
        try:
            if os.name == "nt":
                import msvcrt

                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except OSError as exc:
            raise PaidLockHeld("another paid S11 process is running") from exc
        yield path
    finally:
        try:
            if acquired:
                if os.name == "nt":
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def write_once_json(path: Path | str, payload: Any) -> Path:
    """Create and flush JSON atomically via exclusive open ('x'); refuse overwrite."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    try:
        with open(target, "x", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
    except FileExistsError as exc:
        raise WriteOnceError(f"Refusing to overwrite existing write-once JSON: {target}") from exc
    return target


@dataclass(frozen=True)
class PromptTier:
    max_prompt_tokens: int | None
    input_per_1m: Decimal
    output_per_1m: Decimal
    cache_read_per_1m: Decimal | None = None
    cache_write_5m_per_1m: Decimal | None = None


@dataclass(frozen=True)
class ModelPriceSpec:
    name: str
    provider: str
    bill_against_cap: bool
    input_per_1m: Decimal | None = None
    output_per_1m: Decimal | None = None
    cache_read_per_1m: Decimal | None = None
    cache_write_5m_per_1m: Decimal | None = None
    prompt_tiers: tuple[PromptTier, ...] = ()


@dataclass(frozen=True)
class PriceTable:
    per_tokens: int
    cap_usd: Decimal
    models: dict[str, ModelPriceSpec]


def _dec(v: Any, field: str) -> Decimal:
    try:
        d = Decimal(str(v))
    except Exception as exc:
        raise ValueError(f"Invalid decimal for {field}: {v!r}") from exc
    if not d.is_finite() or d <= 0:
        raise ValueError(f"Price/cap {field} must be finite and > 0, got {v!r}")
    return d


def load_prices(path: Path | str = DEFAULT_PRICES_PATH) -> PriceTable:
    """Load and validate pi_prices.yaml."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    per_tokens = int(raw.get("per_tokens", 1_000_000))
    if per_tokens <= 0:
        raise ValueError(f"per_tokens must be > 0, got {per_tokens!r}")
    cap_usd = _dec(raw.get("cap_usd", DEFAULT_CAP_USD), "cap_usd")
    raw_models = raw.get("models") or {}
    if not isinstance(raw_models, dict) or not raw_models:
        raise ValueError("pi_prices.yaml must define a non-empty 'models' mapping")

    specs: dict[str, ModelPriceSpec] = {}
    for name, cfg in raw_models.items():
        if not isinstance(cfg, dict):
            raise ValueError(f"Model {name!r} price entry must be a mapping")
        provider = str(cfg.get("provider", ""))
        bill = bool(cfg.get("bill_against_cap", False))
        tiers_raw = cfg.get("prompt_tiers")
        tiers: list[PromptTier] = []
        if not bill:
            specs[name] = ModelPriceSpec(name=name, provider=provider, bill_against_cap=False)
            continue
        if tiers_raw:
            for idx, tr in enumerate(tiers_raw):
                mx = tr.get("max_prompt_tokens")
                if mx is not None and int(mx) <= 0:
                    raise ValueError(f"{name}.prompt_tiers[{idx}].max_prompt_tokens must be > 0")
                cr_val = tr.get("cache_read_per_1m")
                cw_val = tr.get("cache_write_5m_per_1m")
                tiers.append(
                    PromptTier(
                        max_prompt_tokens=None if mx is None else int(mx),
                        input_per_1m=_dec(tr["input_per_1m"], f"{name}.tier[{idx}].input"),
                        output_per_1m=_dec(tr["output_per_1m"], f"{name}.tier[{idx}].output"),
                        cache_read_per_1m=None if cr_val is None else _dec(cr_val, f"{name}.tier[{idx}].cache_read_per_1m"),
                        cache_write_5m_per_1m=None if cw_val is None else _dec(cw_val, f"{name}.tier[{idx}].cache_write_5m_per_1m"),
                    )
                )
            specs[name] = ModelPriceSpec(
                name=name,
                provider=provider,
                bill_against_cap=True,
                prompt_tiers=tuple(tiers),
            )
        else:
            cr_val = cfg.get("cache_read_per_1m")
            cw_val = cfg.get("cache_write_5m_per_1m")
            specs[name] = ModelPriceSpec(
                name=name,
                provider=provider,
                bill_against_cap=True,
                input_per_1m=_dec(cfg["input_per_1m"], f"{name}.input_per_1m"),
                output_per_1m=_dec(cfg["output_per_1m"], f"{name}.output_per_1m"),
                cache_read_per_1m=None if cr_val is None else _dec(cr_val, f"{name}.cache_read_per_1m"),
                cache_write_5m_per_1m=None if cw_val is None else _dec(cw_val, f"{name}.cache_write_5m_per_1m"),
            )
    return PriceTable(per_tokens=per_tokens, cap_usd=cap_usd, models=specs)


def normalize_model_name(model: str) -> str:
    """Strip LiteLLM route prefix ('anthropic/' or 'openai/') when present."""
    for prefix in ("anthropic/", "openai/"):
        if model.startswith(prefix):
            return model[len(prefix):]
    return model


def resolve_model_spec(prices: PriceTable, model: str) -> ModelPriceSpec:
    """Resolve exact model name or dated snapshot prefix (e.g. model-YYYYMMDD)."""
    norm = normalize_model_name(model.strip())
    if norm in prices.models:
        return prices.models[norm]
    for key in sorted(prices.models, key=len, reverse=True):
        if norm.startswith(key + "-"):
            return prices.models[key]
    raise UnknownModelPriceError(f"Unknown model {model!r} in pricing table")


def _tier_for_prompt_tokens(spec: ModelPriceSpec, prompt_tokens: int) -> PromptTier:
    for tier in spec.prompt_tiers:
        if tier.max_prompt_tokens is None or prompt_tokens <= tier.max_prompt_tokens:
            return tier
    raise ValueError(f"No matching prompt tier for {spec.name!r} at {prompt_tokens} prompt tokens")


def _q_usd(val: Decimal) -> Decimal:
    return val.quantize(_USD_QUANT, rounding=ROUND_HALF_UP)


def estimate_input_tokens(chars: int) -> int:
    """Conservative input token estimate: max(1, chars // 3)."""
    return max(1, int(chars) // 3)


def count_chars_in_payload(obj: Any) -> int:
    """Count total characters in prompt/messages/tools structures without retaining text."""
    if obj is None or isinstance(obj, bool):
        return 0
    if isinstance(obj, str):
        return len(obj)
    if isinstance(obj, (int, float)):
        return len(str(obj))
    if isinstance(obj, dict):
        total = 0
        for k, v in obj.items():
            # Skip base64 data fields if ever present; count structured schema/keys/text.
            total += count_chars_in_payload(k) + count_chars_in_payload(v)
        return total
    if isinstance(obj, (list, tuple)):
        return sum(count_chars_in_payload(x) for x in obj)
    return len(str(obj))


def estimate_reserve_cost_usd(
    prices: PriceTable,
    model: str,
    input_estimate: int,
    max_tokens: int,
    *,
    has_cache_control: bool = False,
) -> Decimal:
    """Compute worst-case USD cost for one attempt from input_estimate and max_tokens."""
    if input_estimate < 1:
        raise ValueError(f"input_estimate must be >= 1, got {input_estimate}")
    if max_tokens <= 0:
        raise ValueError(f"max_tokens must be > 0, got {max_tokens}")
    spec = resolve_model_spec(prices, model)
    if not spec.bill_against_cap:
        raise ValueError(f"Model {model!r} has bill_against_cap=false and cannot reserve against the cap")
    denom = Decimal(prices.per_tokens)
    inp = Decimal(input_estimate)
    out = Decimal(max_tokens)
    if spec.prompt_tiers:
        tier = _tier_for_prompt_tokens(spec, input_estimate)
        in_rate = tier.input_per_1m
        if has_cache_control and tier.cache_write_5m_per_1m is not None:
            in_rate = max(in_rate, tier.cache_write_5m_per_1m)
        return _q_usd((inp * in_rate + out * tier.output_per_1m) / denom)
    assert spec.input_per_1m is not None and spec.output_per_1m is not None
    in_rate = spec.input_per_1m
    if has_cache_control and spec.cache_write_5m_per_1m is not None:
        in_rate = max(in_rate, spec.cache_write_5m_per_1m)
    return _q_usd((inp * in_rate + out * spec.output_per_1m) / denom)


def compute_actual_cost_usd(
    prices: PriceTable,
    model: str,
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_input_tokens: int = 0,
    cache_creation_input_tokens: int = 0,
) -> Decimal | None:
    """Compute actual billed USD cost from token usage, or None if bill_against_cap=false."""
    spec = resolve_model_spec(prices, model)
    if not spec.bill_against_cap:
        return None
    for name, val in (
        ("input_tokens", input_tokens),
        ("output_tokens", output_tokens),
        ("cache_read_input_tokens", cache_read_input_tokens),
        ("cache_creation_input_tokens", cache_creation_input_tokens),
    ):
        if val < 0:
            raise ValueError(f"{name} must be >= 0, got {val}")

    denom = Decimal(prices.per_tokens)
    inp = Decimal(input_tokens)
    out = Decimal(output_tokens)
    cr = Decimal(cache_read_input_tokens)
    cw = Decimal(cache_creation_input_tokens)

    if spec.prompt_tiers:
        prompt_tokens = input_tokens + cache_read_input_tokens + cache_creation_input_tokens
        tier = _tier_for_prompt_tokens(spec, prompt_tokens)
        cr_rate = tier.cache_read_per_1m if tier.cache_read_per_1m is not None else tier.input_per_1m
        cw_rate = tier.cache_write_5m_per_1m if tier.cache_write_5m_per_1m is not None else tier.input_per_1m
        return _q_usd((inp * tier.input_per_1m + out * tier.output_per_1m + cr * cr_rate + cw * cw_rate) / denom)

    assert spec.input_per_1m is not None and spec.output_per_1m is not None
    cr_rate = spec.cache_read_per_1m if spec.cache_read_per_1m is not None else spec.input_per_1m
    cw_rate = spec.cache_write_5m_per_1m if spec.cache_write_5m_per_1m is not None else spec.input_per_1m
    return _q_usd((inp * spec.input_per_1m + out * spec.output_per_1m + cr * cr_rate + cw * cw_rate) / denom)


def _append_jsonl_locked(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(row, ensure_ascii=False) + "\n"
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(line)
        fh.flush()
        os.fsync(fh.fileno())


@dataclass(frozen=True)
class Reservation:
    reservation_id: str
    call_id: str
    lane: str
    model: str
    attempt: int
    input_estimate: int
    max_tokens: int
    max_tokens_source: str
    reserved_usd: Decimal


@dataclass(frozen=True)
class Settlement:
    reservation_id: str
    call_id: str
    status: str
    actual_usd: Decimal
    retained: bool


class CostLedger:
    """Thread-safe append-only USD reservation/settlement ledger enforcing a hard cap."""

    def __init__(
        self,
        ledger_path: Path | str,
        *,
        prices: PriceTable | None = None,
        cap_usd: Decimal | float | str | None = None,
    ) -> None:
        self.path = Path(ledger_path)
        self.prices = prices or load_prices()
        self.cap_usd = _dec(cap_usd, "cap_usd") if cap_usd is not None else self.prices.cap_usd
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._reserves: dict[str, Reservation] = {}
        self._settlements: dict[str, Settlement] = {}
        if self.path.exists():
            self._load_existing()

    def _load_existing(self) -> None:
        for lineno, raw in enumerate(self.path.read_text(encoding="utf-8").splitlines(), start=1):
            if not raw.strip():
                continue
            row = json.loads(raw)
            ev = row.get("event")
            rid = str(row["reservation_id"])
            if ev == "reserve":
                if rid in self._reserves:
                    raise DuplicateReservationError(f"Duplicate reserve for {rid!r} at line {lineno}")
                self._reserves[rid] = Reservation(
                    reservation_id=rid,
                    call_id=str(row["call_id"]),
                    lane=str(row["lane"]),
                    model=str(row["model"]),
                    attempt=int(row["attempt"]),
                    input_estimate=int(row["input_estimate"]),
                    max_tokens=int(row["max_tokens"]),
                    max_tokens_source=str(row.get("max_tokens_source", "explicit")),
                    reserved_usd=_q_usd(Decimal(str(row["reserved_usd"]))),
                )
            elif ev == "settle":
                if rid not in self._reserves:
                    raise UnknownReservationError(f"Settle without reserve for {rid!r} at line {lineno}")
                if rid in self._settlements:
                    raise AlreadySettledError(f"Duplicate settle for {rid!r} at line {lineno}")
                self._settlements[rid] = Settlement(
                    reservation_id=rid,
                    call_id=str(row["call_id"]),
                    status=str(row["status"]),
                    actual_usd=_q_usd(Decimal(str(row["actual_usd"]))),
                    retained=bool(row.get("retained", False)),
                )
            else:
                raise ValueError(f"Unknown ledger event {ev!r} at line {lineno}")

    def _spent_unlocked(self) -> Decimal:
        return sum((s.actual_usd for s in self._settlements.values()), Decimal("0"))

    def _open_reserved_unlocked(self) -> Decimal:
        return sum(
            (r.reserved_usd for rid, r in self._reserves.items() if rid not in self._settlements),
            Decimal("0"),
        )

    def _committed_unlocked(self) -> Decimal:
        return self._spent_unlocked() + self._open_reserved_unlocked()

    @property
    def spent_usd(self) -> Decimal:
        with self._lock:
            return self._spent_unlocked()

    @property
    def reserved_usd(self) -> Decimal:
        with self._lock:
            return self._open_reserved_unlocked()

    @property
    def committed_usd(self) -> Decimal:
        with self._lock:
            return self._committed_unlocked()

    @property
    def remaining_usd(self) -> Decimal:
        with self._lock:
            rem = self.cap_usd - self._committed_unlocked()
            return rem if rem > Decimal("0") else Decimal("0")

    def open_reservation_ids(self, subset: Iterable[str] | None = None) -> list[str]:
        with self._lock:
            if subset is None:
                return [rid for rid in self._reserves if rid not in self._settlements]
            return [rid for rid in subset if rid in self._reserves and rid not in self._settlements]

    def is_billable(self, model: str) -> bool:
        return resolve_model_spec(self.prices, model).bill_against_cap

    def reserve(
        self,
        *,
        call_id: str,
        lane: str,
        model: str,
        attempt: int,
        input_estimate: int,
        max_tokens: int,
        max_tokens_source: str = "explicit",
        has_cache_control: bool = False,
        reservation_id: str | None = None,
    ) -> Reservation:
        reserved_usd = estimate_reserve_cost_usd(
            self.prices,
            model,
            input_estimate,
            max_tokens,
            has_cache_control=has_cache_control,
        )
        canonical_model = resolve_model_spec(self.prices, model).name
        with self._cond:
            rid = reservation_id or f"{call_id}:a{attempt}:{uuid.uuid4().hex[:8]}"
            if rid in self._reserves:
                raise DuplicateReservationError(f"Reservation ID already exists: {rid!r}")
            committed = self._committed_unlocked()
            if committed + reserved_usd > self.cap_usd:
                raise CapExceededError(
                    f"Cost cap exceeded for {lane}/{canonical_model} attempt={attempt}: "
                    f"committed=${float(committed):.6f} + reserve=${float(reserved_usd):.6f} "
                    f"> cap=${float(self.cap_usd):.2f}"
                )
            rec = Reservation(
                reservation_id=rid,
                call_id=call_id,
                lane=lane,
                model=canonical_model,
                attempt=attempt,
                input_estimate=input_estimate,
                max_tokens=max_tokens,
                max_tokens_source=max_tokens_source,
                reserved_usd=reserved_usd,
            )
            _append_jsonl_locked(
                self.path,
                {
                    "ts_utc": now_utc_iso(),
                    "event": "reserve",
                    "reservation_id": rid,
                    "call_id": call_id,
                    "lane": lane,
                    "model": canonical_model,
                    "attempt": attempt,
                    "input_estimate": input_estimate,
                    "max_tokens": max_tokens,
                    "max_tokens_source": max_tokens_source,
                    "reserved_usd": float(reserved_usd),
                },
            )
            self._reserves[rid] = rec
            self._cond.notify_all()
            return rec

    def settle(
        self,
        reservation_id: str,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_read_input_tokens: int = 0,
        cache_creation_input_tokens: int = 0,
        status: str = "ok",
        timeout_retained: bool = False,
        actual_usd: Decimal | float | str | None = None,
    ) -> Settlement:
        with self._cond:
            res = self._reserves.get(reservation_id)
            if res is None:
                raise UnknownReservationError(f"Unknown reservation_id: {reservation_id!r}")
            if reservation_id in self._settlements:
                raise AlreadySettledError(f"Reservation already settled: {reservation_id!r}")

            if timeout_retained:
                settled = res.reserved_usd
                retained = True
            elif actual_usd is not None:
                settled = _q_usd(Decimal(str(actual_usd)))
                if settled < 0:
                    raise ValueError(f"actual_usd must be >= 0, got {actual_usd!r}")
                retained = False
            elif status != "ok" and (
                input_tokens + output_tokens + cache_read_input_tokens + cache_creation_input_tokens
            ) == 0:
                settled = Decimal("0")
                retained = False
            else:
                computed = compute_actual_cost_usd(
                    self.prices,
                    res.model,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cache_read_input_tokens=cache_read_input_tokens,
                    cache_creation_input_tokens=cache_creation_input_tokens,
                )
                settled = Decimal("0") if computed is None else computed
                retained = False

            st = Settlement(
                reservation_id=reservation_id,
                call_id=res.call_id,
                status=status,
                actual_usd=settled,
                retained=retained,
            )
            _append_jsonl_locked(
                self.path,
                {
                    "ts_utc": now_utc_iso(),
                    "event": "settle",
                    "reservation_id": reservation_id,
                    "call_id": res.call_id,
                    "lane": res.lane,
                    "model": res.model,
                    "attempt": res.attempt,
                    "status": status,
                    "retained": retained,
                    "input_tokens": int(input_tokens),
                    "output_tokens": int(output_tokens),
                    "cache_read_input_tokens": int(cache_read_input_tokens),
                    "cache_creation_input_tokens": int(cache_creation_input_tokens),
                    "reserved_usd": float(res.reserved_usd),
                    "actual_usd": float(settled),
                },
            )
            self._settlements[reservation_id] = st
            self._cond.notify_all()
            return st

    def wait_for_settled(self, reservation_ids: Iterable[str], timeout_s: float = 10.0) -> list[str]:
        """Wait up to timeout_s for all reservation_ids to settle; return still-open IDs."""
        targets = list(dict.fromkeys(reservation_ids))
        if not targets:
            return []
        deadline = time.monotonic() + max(0.0, float(timeout_s))
        with self._cond:
            while True:
                pending = [rid for rid in targets if rid in self._reserves and rid not in self._settlements]
                if not pending:
                    return []
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return pending
                self._cond.wait(timeout=remaining)


USAGE_FIELDS = (
    "ts_utc",
    "provider",
    "lane",
    "model_requested",
    "model_returned",
    "attempt",
    "status",
    "status_code",
    "error_type",
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
    "cost_usd",
    "latency_s",
    "timeout_s",
    "call_id",
    "effort",
    "thinking_tokens",
)


class UsageLogger:
    """Thread-safe append-only usage.jsonl writer recording metadata only (never prompt/answer text)."""

    def __init__(self, log_path: Path | str) -> None:
        self.path = Path(log_path)
        self._lock = threading.Lock()

    def log(
        self,
        *,
        provider: str,
        lane: str,
        model_requested: str,
        model_returned: str | None,
        attempt: int,
        status: str,
        status_code: int | None,
        error_type: str | None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_read_input_tokens: int = 0,
        cache_creation_input_tokens: int = 0,
        cost_usd: float | None = None,
        latency_s: float = 0.0,
        timeout_s: float | None = None,
        call_id: str,
        effort: str | None = None,
        thinking_tokens: int | None = None,
    ) -> dict[str, Any]:
        row = {
            "ts_utc": now_utc_iso(),
            "provider": str(provider),
            "lane": str(lane),
            "model_requested": str(model_requested),
            "model_returned": None if model_returned is None else str(model_returned),
            "attempt": int(attempt),
            "status": str(status),
            "status_code": None if status_code is None else int(status_code),
            "error_type": None if error_type is None else str(error_type),
            "input_tokens": int(input_tokens),
            "output_tokens": int(output_tokens),
            "cache_read_input_tokens": int(cache_read_input_tokens),
            "cache_creation_input_tokens": int(cache_creation_input_tokens),
            "cost_usd": None if cost_usd is None else float(cost_usd),
            "latency_s": round(float(latency_s), 4),
            "timeout_s": None if timeout_s is None else float(timeout_s),
            "call_id": str(call_id),
            "effort": None if effort is None else str(effort),
            "thinking_tokens": None if thinking_tokens is None or isinstance(thinking_tokens, bool) else int(thinking_tokens),
        }
        with self._lock:
            _append_jsonl_locked(self.path, row)
        return row
