"""Frozen M0 chronology and candidate-independent opportunity construction.

Only registered fold boundaries and frozen signal bytes are accepted. This
module does not execute candidates, score outcomes, or read future labels.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import calendar
from decimal import Decimal, InvalidOperation
import hashlib
from io import BytesIO, TextIOWrapper
import re
from types import MappingProxyType
from typing import Mapping, Sequence

from .experiment_registry import canonical_json_bytes


class ChronologyError(ValueError):
    """Frozen chronology or signal universe is malformed."""


BAR = timedelta(minutes=15)
LABEL_HORIZON_BARS = 96
EMBARGO_BARS = 96
WARMUP_BARS = 816
TERMINAL_ENTRY_BLACKOUT_BARS = 96
MAX_POSITION_HORIZON_BARS = 17_664
_STRATEGIES = {"trend_following", "breakout", "momentum", "mean_reversion"}
_SIGNAL_FIELDS = ("signal_id", "timestamp", "available_at", "strategy",
                  "direction", "confidence", "regime", "reference_price", "stop_price", "take_profit")


def _utc(value: str) -> datetime:
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ChronologyError("timestamps must be ISO-8601 UTC") from exc
    if stamp.tzinfo is None or stamp.utcoffset() != timedelta(0):
        raise ChronologyError("timestamps must be ISO-8601 UTC")
    return stamp.astimezone(timezone.utc)


def _add_months(stamp: datetime, count: int) -> datetime:
    absolute = stamp.year * 12 + stamp.month - 1 + count
    year, month0 = divmod(absolute, 12)
    month = month0 + 1
    return stamp.replace(year=year, month=month, day=min(stamp.day, calendar.monthrange(year, month)[1]))


@dataclass(frozen=True, slots=True)
class FoldWindow:
    fold_id: str
    train: tuple[datetime, datetime]
    validation: tuple[datetime, datetime]
    outer_test: tuple[datetime, datetime]
    train_validation_embargo: tuple[datetime, datetime]
    validation_test_embargo: tuple[datetime, datetime]


@dataclass(frozen=True, slots=True)
class SignalRef:
    signal_id: int
    strategy: str
    signal_timestamp: datetime
    available_at: datetime
    direction: str
    confidence: str
    regime: str
    reference_price: str
    stop_price: str
    take_profit: str | None


@dataclass(frozen=True, slots=True)
class Opportunity:
    opportunity_id: str
    instrument_id: str
    available_at: datetime
    signals: tuple[SignalRef, ...]
    utc_week: str


@dataclass(frozen=True, slots=True)
class OpportunityUniverse:
    """Shared opportunity rows plus the complete scored UTC-week calendar."""

    opportunities: tuple[Opportunity, ...]
    utc_week_keys: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CandleTimeline:
    """Immutable index of timestamps parsed from the exact pinned candle bytes."""

    timestamps: tuple[datetime, ...]
    index_by_timestamp: Mapping[datetime, int]

    @classmethod
    def from_timestamps(cls, timestamps: Sequence[datetime]) -> "CandleTimeline":
        normalized = tuple(_datetime_utc(value) for value in timestamps)
        if not normalized:
            raise ChronologyError("pinned candle timeline is empty")
        if any(right <= left for left, right in zip(normalized, normalized[1:])):
            raise ChronologyError("pinned candle timeline must be strictly chronological and unique")
        index = MappingProxyType({stamp: i for i, stamp in enumerate(normalized)})
        return cls(normalized, index)

    def resolve(self, signal_timestamp: datetime) -> int:
        stamp = _datetime_utc(signal_timestamp)
        try:
            return self.index_by_timestamp[stamp]
        except KeyError as exc:
            raise ChronologyError("signal timestamp is absent from the pinned candle timeline") from exc


def _datetime_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ChronologyError("timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


def _candle_stamp(value: str) -> datetime:
    """Parse the pinned OHLCV timestamp representation (ISO, seconds, or ms)."""
    try:
        numeric = Decimal(value)
    except (InvalidOperation, TypeError):
        try:
            stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise ChronologyError("pinned candle timestamp is invalid") from exc
        return _datetime_utc(stamp)
    if not numeric.is_finite():
        raise ChronologyError("pinned candle timestamp must be finite")
    seconds = numeric / Decimal(1000) if numeric > Decimal("10000000000") else numeric
    try:
        return datetime.fromtimestamp(float(seconds), tz=timezone.utc)
    except (OverflowError, OSError, ValueError) as exc:
        raise ChronologyError("pinned candle timestamp is outside supported range") from exc


def candle_timeline_from_bytes(candle_bytes: bytes) -> CandleTimeline:
    """Build a timeline without reopening a path or changing pinned input bytes."""
    try:
        text = TextIOWrapper(BytesIO(candle_bytes), encoding="utf-8", newline="")
        reader = csv.DictReader(text)
        if not reader.fieldnames or "timestamp" not in reader.fieldnames:
            raise ChronologyError("pinned candle bytes lack a timestamp column")
        timestamps = [_candle_stamp(row["timestamp"]) for row in reader]
    except (KeyError, UnicodeDecodeError, csv.Error) as exc:
        raise ChronologyError("pinned candle timeline is malformed") from exc
    return CandleTimeline.from_timestamps(timestamps)


def _positive_decimal(value: str, field: str, *, allow_zero: bool = False) -> Decimal:
    try:
        number = Decimal(value)
    except (InvalidOperation, TypeError) as exc:
        raise ChronologyError(f"signal {field} must be numeric") from exc
    if not number.is_finite() or (number < 0 if allow_zero else number <= 0):
        raise ChronologyError(f"signal {field} must be finite and positive")
    return number


def complete_utc_week_keys(interval: tuple[datetime, datetime]) -> tuple[str, ...]:
    """All ISO UTC weeks intersecting a scored half-open interval, including empty weeks."""
    start, end = (_datetime_utc(interval[0]), _datetime_utc(interval[1]))
    if start >= end:
        raise ChronologyError("eligible interval must be ordered")
    monday = start.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=start.weekday())
    keys = []
    while monday < end:
        keys.append(_week(monday))
        monday += timedelta(days=7)
    return tuple(keys)


def build_fold_windows(protocol: Mapping) -> tuple[FoldWindow, ...]:
    """Validate and materialize the exact fold boundaries pre-registered in M0."""
    chronology = protocol.get("chronology")
    if not isinstance(chronology, Mapping):
        raise ChronologyError("protocol is missing frozen chronology")
    frozen = {
        "clock": "UTC_signal_availability", "fold_method": "nested_expanding_rolling_origin",
        "min_training_months": 24, "inner_validation_months": 6,
        "outer_test_months": 6, "outer_step_months": 6,
        "minimum_eligible_outer_folds": 6, "nonoverlapping_outer_tests": True,
        "max_label_horizon_bars": 96, "max_position_horizon_bars": 17664,
        "embargo_bars": 96, "terminal_entry_blackout_bars": 96,
        "purge_policy": "purge_label_or_trade_outcome_crossing_boundary",
        "warmup_policy": "816_prior_contiguous_15m_bars_reset_after_gap_causal_only_unscored",
        "state_reset": "fresh_account_orders_and_safety_state_per_fold",
        "fold_end_policy": "frozen_phase4_force_close_at_end_with_costs_reported",
    }
    for key, expected in frozen.items():
        if chronology.get(key) != expected:
            raise ChronologyError(f"protocol chronology changes frozen M0 value: {key}")
    rows = chronology.get("folds")
    if not isinstance(rows, list) or len(rows) < 6:
        raise ChronologyError("at least six explicitly pre-registered outer folds are required")
    windows: list[FoldWindow] = []
    previous_test_end: datetime | None = None
    for row in rows:
        try:
            fold_id = row["fold_id"]
            train = (_utc(row["train_start_utc"]), _utc(row["train_end_utc"]))
            validation = (_utc(row["validation_start_utc"]), _utc(row["validation_end_utc"]))
            test = (_utc(row["outer_test_start_utc"]), _utc(row["outer_test_end_utc"]))
        except (KeyError, TypeError) as exc:
            raise ChronologyError("fold row lacks frozen UTC boundaries") from exc
        if not (train[0] < train[1] <= validation[0] < validation[1] <= test[0] < test[1]):
            raise ChronologyError("fold boundaries must be chronological half-open intervals")
        if train[1] < _add_months(train[0], 24):
            raise ChronologyError("training history is shorter than 24 calendar months")
        if validation[1] < _add_months(validation[0], 6):
            raise ChronologyError("inner validation is shorter than six calendar months")
        if test[1] < _add_months(test[0], 6):
            raise ChronologyError("outer test is shorter than six calendar months")
        if validation[0] < train[1] + timedelta(hours=24):
            raise ChronologyError("train-to-validation embargo is shorter than 96 bars/24 hours")
        if test[0] < validation[1] + timedelta(hours=24):
            raise ChronologyError("validation-to-test embargo is shorter than 96 bars/24 hours")
        if previous_test_end is not None and test[0] < previous_test_end:
            raise ChronologyError("outer test intervals overlap or are not chronological")
        if windows and test[0] < _add_months(windows[-1].outer_test[0], 6):
            raise ChronologyError("outer test step is shorter than six calendar months")
        previous_test_end = test[1]
        windows.append(FoldWindow(
            str(fold_id), train, validation, test,
            (train[1], validation[0]), (validation[1], test[0]),
        ))
    return tuple(windows)


def _week(stamp: datetime) -> str:
    iso = stamp.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def build_opportunity_universe(
    signal_bytes: bytes,
    *,
    instrument_id: str,
    dataset_manifest_sha256: str,
    eligible_interval: tuple[datetime, datetime],
) -> OpportunityUniverse:
    """Build union of raw frozen signals; never parse/use forward outcome fields."""
    if not instrument_id or not re.fullmatch(r"[a-f0-9]{64}", dataset_manifest_sha256):
        raise ChronologyError("instrument and frozen dataset-manifest digest are required")
    start, end = (_datetime_utc(eligible_interval[0]), _datetime_utc(eligible_interval[1]))
    if start >= end:
        raise ChronologyError("eligible interval must be ordered")
    groups: dict[datetime, list[SignalRef]] = {}
    seen: set[tuple[datetime, str]] = set()
    seen_signal_ids: set[int] = set()
    try:
        text = TextIOWrapper(BytesIO(signal_bytes), encoding="utf-8", newline="")
        reader = csv.DictReader(text)
        if not reader.fieldnames or not set(_SIGNAL_FIELDS).issubset(reader.fieldnames):
            raise ChronologyError("frozen signal bytes are missing required decision-time fields")
        for row in reader:
            # Deliberately project an allowlist; forward_outcomes and other columns
            # are never deserialized or exposed to this construction path.
            try:
                signal_id_decimal = Decimal(row["signal_id"])
            except (InvalidOperation, TypeError) as exc:
                raise ChronologyError("signal_id must be a positive integer") from exc
            if (not signal_id_decimal.is_finite() or signal_id_decimal <= 0 or
                    signal_id_decimal != signal_id_decimal.to_integral_value()):
                raise ChronologyError("signal_id must be a positive integer")
            signal_id = int(signal_id_decimal)
            if signal_id in seen_signal_ids:
                raise ChronologyError("duplicate frozen signal_id")
            seen_signal_ids.add(signal_id)
            confidence = _positive_decimal(row["confidence"], "confidence")
            if confidence > 1:
                raise ChronologyError("signal confidence must be at most 1")
            _positive_decimal(row["reference_price"], "reference_price")
            _positive_decimal(row["stop_price"], "stop_price")
            if row["take_profit"] not in (None, ""):
                _positive_decimal(row["take_profit"], "take_profit")
            ref = SignalRef(
                signal_id=signal_id, strategy=row["strategy"],
                signal_timestamp=_utc(row["timestamp"]), available_at=_utc(row["available_at"]),
                direction=row["direction"],
                confidence=row["confidence"], regime=row["regime"],
                reference_price=row["reference_price"], stop_price=row["stop_price"],
                take_profit=row["take_profit"] or None,
            )
            if ref.strategy not in _STRATEGIES or ref.direction not in {"long", "short"}:
                raise ChronologyError("unsupported frozen strategy or direction")
            if ref.available_at != ref.signal_timestamp + BAR:
                raise ChronologyError("available_at must be one bar after original signal timestamp")
            if not (start <= ref.available_at < end):
                continue
            duplicate_key = (ref.available_at, ref.strategy)
            if duplicate_key in seen:
                raise ChronologyError("duplicate strategy signal at an opportunity timestamp")
            seen.add(duplicate_key)
            groups.setdefault(ref.available_at, []).append(ref)
    except (KeyError, ValueError, UnicodeDecodeError, csv.Error) as exc:
        if isinstance(exc, ChronologyError):
            raise
        raise ChronologyError("frozen signal row is malformed") from exc

    opportunities = []
    for available_at in sorted(groups):
        signals = tuple(sorted(groups[available_at], key=lambda item: item.strategy))
        identity = {
            "schema_version": "athena.m3.opportunity.v1",
            "dataset_manifest_sha256": dataset_manifest_sha256,
            "instrument_id": instrument_id,
            "available_at_utc": available_at.isoformat().replace("+00:00", "Z"),
        }
        oid = hashlib.sha256(canonical_json_bytes(identity)).hexdigest()
        opportunities.append(Opportunity(oid, instrument_id, available_at, signals, _week(available_at)))
    return OpportunityUniverse(tuple(opportunities), complete_utc_week_keys((start, end)))


def label_endpoint(
    signal_timestamp: datetime,
    candle_timeline: CandleTimeline,
    *,
    recorded_signal_index: int | None = None,
) -> datetime | None:
    """Resolve by original timestamp on pinned candles, then return its 96-bar endpoint.

    The optional CSV index is only a cross-check. It is never used to locate the
    signal, so absence of that non-schema field cannot shift or invent labels.
    """
    signal_index = candle_timeline.resolve(signal_timestamp)
    if recorded_signal_index is not None and recorded_signal_index != signal_index:
        raise ChronologyError("recorded candle_index disagrees with pinned timestamp mapping")
    candle_timestamps = candle_timeline.timestamps
    endpoint_index = signal_index + LABEL_HORIZON_BARS
    if endpoint_index >= len(candle_timestamps):
        return None
    for index in range(signal_index + 1, endpoint_index + 1):
        if candle_timestamps[index] - candle_timestamps[index - 1] != BAR:
            return None
    return candle_timestamps[endpoint_index]


def label_survives_boundary(endpoint: datetime | None, boundary: datetime) -> bool:
    """M0 strict rule: endpoint must exist and be strictly before boundary."""
    return endpoint is not None and endpoint < boundary


def is_entry_in_terminal_blackout(available_at: datetime, scored_end: datetime) -> bool:
    """No entries in the final 96 expected bars (24 elapsed hours) of a block."""
    return scored_end - timedelta(hours=24) <= available_at < scored_end


def warmup_ready(signal_index: int, candle_timestamps: Sequence[datetime]) -> bool:
    """Require 816 immediately prior contiguous bars; gaps reset causal warm-up."""
    if signal_index < WARMUP_BARS or signal_index >= len(candle_timestamps):
        return False
    start = signal_index - WARMUP_BARS
    return all(candle_timestamps[i] - candle_timestamps[i - 1] == BAR
               for i in range(start + 1, signal_index + 1))


def position_horizon_within_fold(entry_index: int, exit_index: int) -> bool:
    """A position cannot exceed the frozen six-month/17,664 expected-bar cap."""
    return 0 <= entry_index <= exit_index and exit_index - entry_index <= MAX_POSITION_HORIZON_BARS

