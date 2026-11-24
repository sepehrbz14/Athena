"""Read-only, hash-pinned ingestion of frozen ensemble-selected signals.

This module is an input boundary only. It never recomputes strategies, uses
future outcomes, changes signal fields, performs risk checks, or executes an
order. Candle and signal bytes are verified before either dataset is parsed.
"""
from __future__ import annotations

import csv
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
from typing import Any, Mapping

import jsonschema

from .models import Candle, Side, Signal

INPUT_MANIFEST_SCHEMA = "athena.frozen-signal-input-manifest.v1"
OUTPUT_MANIFEST_SCHEMA = "athena.frozen-signal-ingestion-manifest.v1"
SIGNAL_SCHEMA = "athena.frozen_signal.v1"
CANDLE_SCHEMA = "athena.ohlcv.csv.v1"
ORDERING_POLICY = "ascending_utc_available_at_instrument_id_signal_id"
_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_SIGNAL_SCHEMA_PATH = _REPOSITORY_ROOT / "schemas" / "athena2_frozen_signal_v1.schema.json"
_INPUT_MANIFEST_SCHEMA_PATH = _REPOSITORY_ROOT / "schemas" / "athena_frozen_signal_input_manifest_v1.schema.json"
_METADATA_SCHEMA_PATH = _REPOSITORY_ROOT / "schemas" / "athena_frozen_signal_capture_metadata_v1.schema.json"
_OUTPUT_MANIFEST_SCHEMA_PATH = _REPOSITORY_ROOT / "schemas" / "athena_frozen_signal_ingestion_manifest_v1.schema.json"


class FrozenSignalInputError(ValueError):
    """Frozen input cannot be admitted without repairing or replacing evidence."""

    def __init__(self, message: str, *, raw_rows: int = 0,
                 rejection_reasons: tuple[Mapping[str, Any], ...] = ()) -> None:
        super().__init__(message)
        self.raw_rows = raw_rows
        self.rejection_reasons = rejection_reasons or ({"row": None, "reason": message},)
        self.invalid_input_count = len(self.rejection_reasons)


@dataclass(frozen=True, slots=True)
class FrozenSignalIngestionConfig:
    """Immutable ingestion-only policy; it does not alter strategy behavior."""

    instrument_id: str = "BTCUSDT"
    reference_price_tolerance_abs: float = 1e-8

    def __post_init__(self) -> None:
        if self.instrument_id != "BTCUSDT":
            raise ValueError("the frozen signal schema currently supports BTCUSDT only")
        if (isinstance(self.reference_price_tolerance_abs, bool)
                or not isinstance(self.reference_price_tolerance_abs, (int, float))
                or not math.isfinite(self.reference_price_tolerance_abs)
                or self.reference_price_tolerance_abs < 0):
            raise ValueError("reference price tolerance must be a finite nonnegative number")


@dataclass(frozen=True, slots=True)
class FrozenSignalEvent:
    """Selected immutable signal with its validated next-candle execution time."""

    instrument_id: str
    signal_id: int
    candle_open_time: datetime
    available_at: datetime
    execution_at: datetime
    strategy: str
    direction: Side
    confidence: float
    reference_price: float
    stop_price: float
    take_profit: float | None
    source_signal_sha256: str
    source_candle_sha256: str

    def to_signal(self) -> Signal:
        """Adapt to the canonical execution Signal without duplicating execution."""
        return Signal(
            side=self.direction,
            stop_price=self.stop_price,
            take_profit=self.take_profit,
            confidence=self.confidence,
            strategy=self.strategy,
            rationale=f"frozen_signal_id={self.signal_id}",
        )

    def normalized_record(self) -> dict[str, Any]:
        """Return the existing frozen-signal v1 normalized interface record."""
        return {
            "schema_version": SIGNAL_SCHEMA,
            "source_signal_sha256": self.source_signal_sha256,
            "source_candle_sha256": self.source_candle_sha256,
            "instrument_id": self.instrument_id,
            "signal_id": self.signal_id,
            "candle_open_time": self.candle_open_time.isoformat().replace("+00:00", "Z"),
            "available_at": self.available_at.isoformat().replace("+00:00", "Z"),
            "strategy": self.strategy,
            "direction": self.direction.value,
            "confidence": self.confidence,
            "reference_price": self.reference_price,
            "stop_price": self.stop_price,
            "take_profit": self.take_profit,
        }


@dataclass(frozen=True, slots=True)
class FrozenSignalIngestionResult:
    signals: tuple[FrozenSignalEvent, ...]
    candles: tuple[Candle, ...]
    manifest: Mapping[str, Any]


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_regular_file(path: str | Path, label: str) -> tuple[Path, bytes]:
    target = Path(path).expanduser().absolute()
    if target.is_symlink() or not target.is_file():
        raise FrozenSignalInputError(f"{label} must be an existing regular non-symlink file")
    try:
        return target, target.read_bytes()
    except OSError as exc:
        raise FrozenSignalInputError(f"cannot read {label}") from exc


def _validate_json_schema(value: Mapping[str, Any], schema_path: Path, label: str) -> None:
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator.check_schema(schema)
        jsonschema.validate(value, schema, format_checker=jsonschema.FormatChecker())
    except Exception as exc:
        raise FrozenSignalInputError(f"{label} does not satisfy its strict versioned schema") from exc


def _utc_timestamp(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} is missing or not a string")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} is malformed") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{label} must include an explicit timezone")
    return parsed.astimezone(timezone.utc)


def _candle_timestamp(value: Any) -> datetime:
    """Parse frozen OHLCV timestamps using the repository's seconds/ms convention."""
    if isinstance(value, str) and re.fullmatch(
            r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?", value):
        numeric = float(value)
        if not math.isfinite(numeric):
            raise ValueError("candle timestamp must be finite")
        if numeric > 10_000_000_000:
            numeric /= 1000
        try:
            return datetime.fromtimestamp(numeric, tz=timezone.utc)
        except (OverflowError, OSError, ValueError) as exc:
            raise ValueError("candle timestamp is outside the supported range") from exc
    return _utc_timestamp(value, "candle timestamp")


def _number(value: Any, label: str, *, positive: bool = False,
            minimum: float | None = None, maximum: float | None = None) -> float:
    if (not isinstance(value, str) or not value
            or value != value.strip()
            or re.fullmatch(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?", value) is None):
        raise ValueError(f"{label} is missing or not numeric")
    try:
        parsed = float(value)
    except ValueError as exc:
        raise ValueError(f"{label} is not numeric") from exc
    if not math.isfinite(parsed):
        raise ValueError(f"{label} must be finite")
    if positive and parsed <= 0:
        raise ValueError(f"{label} must be positive")
    if minimum is not None and parsed < minimum:
        raise ValueError(f"{label} is below its minimum")
    if maximum is not None and parsed > maximum:
        raise ValueError(f"{label} exceeds its maximum")
    return parsed


def _strict_bool(value: Any, label: str) -> bool:
    if value in ("true", "True"):
        return True
    if value in ("false", "False"):
        return False
    raise ValueError(f"{label} must be exactly true or false")


def _source_revision() -> tuple[str, bool, str]:
    def git(*args: str) -> str:
        completed = subprocess.run(
            ["git", "-C", str(_REPOSITORY_ROOT), *args],
            check=True, capture_output=True, text=True, encoding="utf-8",
        )
        return completed.stdout.strip()

    try:
        commit = git("rev-parse", "HEAD")
        dirty = bool(git("status", "--porcelain", "--untracked-files=all"))
    except (OSError, subprocess.CalledProcessError) as exc:
        raise FrozenSignalInputError("cannot establish the ingestion source Git revision") from exc
    source_digest = _sha256(Path(__file__).read_bytes())
    return commit, dirty, source_digest


def _parse_candles(data: bytes, interval_minutes: int) -> tuple[Candle, ...]:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise FrozenSignalInputError("candle CSV must be UTF-8") from exc
    reader = csv.DictReader(text.splitlines())
    required = {"timestamp", "open", "high", "low", "close", "volume"}
    if (not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames)
            or not required.issubset(reader.fieldnames)):
        raise FrozenSignalInputError("candle CSV must have unique timestamp,open,high,low,close,volume columns")
    candles: list[Candle] = []
    prior: datetime | None = None
    issues: list[Mapping[str, Any]] = []
    for row_number, row in enumerate(reader, start=2):
        if None in row:
            issues.append({"row": row_number, "reason": "extra_csv_fields"})
            continue
        try:
            stamp = _candle_timestamp(row.get("timestamp"))
            interval_seconds = interval_minutes * 60
            epoch_seconds = int(stamp.timestamp())
            if stamp.microsecond or epoch_seconds % interval_seconds:
                raise ValueError("candle timestamp is not aligned to the declared interval")
            prices = {key: _number(row.get(key), key, positive=True)
                      for key in ("open", "high", "low", "close")}
            volume = _number(row.get("volume"), "volume", minimum=0)
            if prices["low"] > min(prices["open"], prices["close"]):
                raise ValueError("low does not contain candle open and close")
            if prices["high"] < max(prices["open"], prices["close"]):
                raise ValueError("high does not contain candle open and close")
            if prior is not None:
                delta = stamp - prior
                if delta.total_seconds() <= 0:
                    raise ValueError("candle timestamps are duplicate or out of order")
                # Gaps are valid observations and are preserved; no candle is synthesized.
            candles.append(Candle(timestamp=stamp, volume=volume, **prices))
            prior = stamp
        except (TypeError, ValueError) as exc:
            issues.append({"row": row_number, "reason": str(exc)})
    if issues:
        detail = "; ".join(f"row {item['row']}: {item['reason']}" for item in issues)
        raise FrozenSignalInputError("candle dataset contains invalid records: " + detail,
                                     raw_rows=max(0, len(text.splitlines()) - 1),
                                     rejection_reasons=tuple(issues))
    if not candles:
        raise FrozenSignalInputError("candle dataset is empty")
    # Referencing the interval here also makes its accepted range explicit.
    if interval_minutes <= 0:
        raise FrozenSignalInputError("candle interval must be positive")
    return tuple(candles)


def _parse_signals(data: bytes, *, signal_sha: str, candle_sha: str,
                   candle_by_time: Mapping[datetime, Candle],
                   interval_minutes: int, config: FrozenSignalIngestionConfig
                   ) -> tuple[tuple[FrozenSignalEvent, ...], int, int, tuple[Mapping[str, Any], ...]]:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise FrozenSignalInputError("signal CSV must be UTF-8") from exc
    reader = csv.DictReader(text.splitlines())
    required = {"signal_id", "timestamp", "available_at", "strategy", "direction",
                "confidence", "ensemble_selected", "reference_price", "stop_price", "take_profit"}
    if (not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames)
            or not required.issubset(reader.fieldnames)):
        raise FrozenSignalInputError("signal CSV lacks unique required frozen signal columns")

    raw_rows = 0
    excluded_rows = 0
    issues: list[Mapping[str, Any]] = []
    selected: list[FrozenSignalEvent] = []
    seen_ids: set[int] = set()
    seen_identity: set[tuple[datetime, str, str]] = set()
    for row_number, row in enumerate(reader, start=2):
        raw_rows += 1
        if None in row:
            issues.append({"row": row_number, "reason": "extra_csv_fields"})
            continue
        missing_fields = sorted(name for name in required if row.get(name) is None)
        if missing_fields:
            issues.append({"row": row_number,
                           "reason": "missing_required_fields:" + ",".join(missing_fields)})
            continue
        try:
            chosen = _strict_bool(row.get("ensemble_selected"), "ensemble_selected")
        except ValueError as exc:
            issues.append({"row": row_number, "reason": str(exc)})
            continue
        if not chosen:
            excluded_rows += 1
            continue
        try:
            signal_id_text = row.get("signal_id")
            if (not isinstance(signal_id_text, str)
                    or re.fullmatch(r"[0-9]+", signal_id_text) is None):
                raise ValueError("signal_id must be a positive base-10 integer")
            signal_id = int(signal_id_text)
            if signal_id <= 0:
                raise ValueError("signal_id must be positive")
            if signal_id in seen_ids:
                raise ValueError("duplicate selected signal_id")
            timestamp = _utc_timestamp(row.get("timestamp"), "signal timestamp")
            available_at = _utc_timestamp(row.get("available_at"), "available_at")
            expected_available = timestamp + timedelta(minutes=interval_minutes)
            if available_at != expected_available:
                raise ValueError("available_at does not equal signal candle close")
            candle = candle_by_time.get(timestamp)
            if candle is None:
                raise ValueError("selected signal timestamp has no exact source candle")
            reference = _number(row.get("reference_price"), "reference_price", positive=True)
            if abs(reference - candle.close) > config.reference_price_tolerance_abs:
                raise ValueError("reference_price differs from exact source candle close")
            if timestamp + timedelta(minutes=interval_minutes) not in candle_by_time:
                raise ValueError("selected signal has no following execution candle")
            strategy = row.get("strategy")
            if not isinstance(strategy, str) or not strategy.strip():
                raise ValueError("strategy is missing")
            if strategy != strategy.strip():
                raise ValueError("strategy must not have leading or trailing whitespace")
            direction_text = row.get("direction")
            if direction_text not in ("long", "short"):
                raise ValueError("direction must be long or short")
            direction = Side.LONG if direction_text == "long" else Side.SHORT
            confidence = _number(row.get("confidence"), "confidence", minimum=0,
                                 maximum=1)
            if confidence <= 0:
                raise ValueError("confidence must be greater than zero")
            stop = _number(row.get("stop_price"), "stop_price", positive=True)
            raw_target = row.get("take_profit")
            target = None if raw_target == "" else _number(
                raw_target, "take_profit", positive=True)
            if direction is Side.LONG and stop >= reference:
                raise ValueError("long stop_price must be below reference_price")
            if direction is Side.SHORT and stop <= reference:
                raise ValueError("short stop_price must be above reference_price")
            if target is not None and direction is Side.LONG and target <= reference:
                raise ValueError("long take_profit must be above reference_price")
            if target is not None and direction is Side.SHORT and target >= reference:
                raise ValueError("short take_profit must be below reference_price")
            identity = (timestamp, strategy, direction.value)
            if identity in seen_identity:
                raise ValueError("duplicate selected (timestamp,strategy,direction) identity")
            execution_time = timestamp + timedelta(minutes=interval_minutes)
            event = FrozenSignalEvent(
                instrument_id=config.instrument_id,
                signal_id=signal_id,
                candle_open_time=timestamp,
                available_at=available_at,
                execution_at=execution_time,
                strategy=strategy,
                direction=direction,
                confidence=confidence,
                reference_price=reference,
                stop_price=stop,
                take_profit=target,
                source_signal_sha256=signal_sha,
                source_candle_sha256=candle_sha,
            )
            _validate_json_schema(event.normalized_record(), _SIGNAL_SCHEMA_PATH,
                                  "normalized frozen selected signal")
            selected.append(event)
            seen_ids.add(signal_id)
            seen_identity.add(identity)
        except (TypeError, ValueError) as exc:
            issues.append({"row": row_number, "reason": str(exc)})

    if issues:
        return (), raw_rows, excluded_rows, tuple(issues)
    selected.sort(key=lambda item: (item.available_at, item.instrument_id, item.signal_id))
    return tuple(selected), raw_rows, excluded_rows, ()


def ingest_frozen_signals(*, signal_csv_path: str | Path,
                          metadata_path: str | Path,
                          candle_csv_path: str | Path,
                          input_manifest_path: str | Path,
                          config: FrozenSignalIngestionConfig | None = None,
                          output_manifest_path: str | Path | None = None) -> FrozenSignalIngestionResult:
    """Verify committed read-only inputs and return selected signals/candles.

    The manifest at ``input_manifest_path`` commits to the exact signal,
    metadata, and candle bytes. Those hashes are checked before JSON or CSV
    dataset parsing. If requested, the output manifest is exclusively created
    so existing reports and source files cannot be overwritten.
    """
    config = config or FrozenSignalIngestionConfig()
    manifest_path, manifest_bytes = _read_regular_file(input_manifest_path, "input manifest")
    try:
        input_manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise FrozenSignalInputError("input manifest is not valid UTF-8 JSON") from exc
    if not isinstance(input_manifest, dict):
        raise FrozenSignalInputError("input manifest must be a JSON object")
    _validate_json_schema(input_manifest, _INPUT_MANIFEST_SCHEMA_PATH, "input manifest")

    signal_path, signal_bytes = _read_regular_file(signal_csv_path, "signal CSV")
    metadata_file, metadata_bytes = _read_regular_file(metadata_path, "source metadata")
    candle_path, candle_bytes = _read_regular_file(candle_csv_path, "candle CSV")
    actual_hashes = {
        "signals_sha256": _sha256(signal_bytes),
        "metadata_sha256": _sha256(metadata_bytes),
        "candles_sha256": _sha256(candle_bytes),
    }
    mismatches = [name for name, value in actual_hashes.items() if input_manifest[name] != value]
    if mismatches:
        reasons = tuple({"row": None, "reason": f"{name}_mismatch"} for name in mismatches)
        raise FrozenSignalInputError("input SHA-256 commitment mismatch: " + ",".join(mismatches),
                                     rejection_reasons=reasons)
    if input_manifest["signal_schema_version"] != "athena.frozen_signal_csv.v1":
        raise FrozenSignalInputError("unsupported frozen source signal schema")
    if input_manifest["candle_schema_version"] != CANDLE_SCHEMA:
        raise FrozenSignalInputError("unsupported frozen candle schema")
    if input_manifest["metadata_schema_version"] != "athena.frozen-signal-capture-metadata.v1":
        raise FrozenSignalInputError("unsupported frozen source metadata schema")

    try:
        metadata = json.loads(metadata_bytes.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise FrozenSignalInputError("source metadata is not valid UTF-8 JSON") from exc
    if not isinstance(metadata, dict):
        raise FrozenSignalInputError("source metadata must be a JSON object")
    _validate_json_schema(metadata, _METADATA_SCHEMA_PATH, "source metadata")

    interval = input_manifest["candle_interval_minutes"]
    candles = _parse_candles(candle_bytes, interval)
    candle_by_time = {candle.timestamp: candle for candle in candles}
    signals, raw_rows, excluded_rows, issues = _parse_signals(
        signal_bytes,
        signal_sha=actual_hashes["signals_sha256"],
        candle_sha=actual_hashes["candles_sha256"],
        candle_by_time=candle_by_time,
        interval_minutes=interval,
        config=config,
    )
    reasons = list(issues)
    if metadata.get("signals") != raw_rows:
        reasons.append({"row": None, "reason": "metadata_signal_count_mismatch"})
    if metadata.get("candles") != len(candles):
        reasons.append({"row": None, "reason": "metadata_candle_count_mismatch"})
    if reasons:
        detail = "; ".join(
            (f"row {item['row']}: " if item.get("row") is not None else "") + str(item["reason"])
            for item in reasons
        )
        raise FrozenSignalInputError("frozen signal input failed validation: " + detail,
                                     raw_rows=raw_rows,
                                     rejection_reasons=tuple(reasons))

    commit, dirty, source_sha = _source_revision()
    config_data = {**asdict(config), "candle_interval_minutes": interval}
    config_hash = _sha256(_canonical(config_data))
    output_manifest: dict[str, Any] = {
        "schema_version": OUTPUT_MANIFEST_SCHEMA,
        "input_manifest_schema_version": input_manifest["schema_version"],
        "input_manifest_identity": manifest_path.name,
        "input_manifest_sha256": _sha256(manifest_bytes),
        "inputs": {
            "signals": {"identity": signal_path.name, "sha256": actual_hashes["signals_sha256"]},
            "metadata": {"identity": metadata_file.name, "sha256": actual_hashes["metadata_sha256"]},
            "candles": {"identity": candle_path.name, "sha256": actual_hashes["candles_sha256"]},
        },
        "signal_schema_version": input_manifest["signal_schema_version"],
        "normalized_signal_schema_version": SIGNAL_SCHEMA,
        "metadata_schema_version": input_manifest["metadata_schema_version"],
        "candle_schema_version": input_manifest["candle_schema_version"],
        "candle_interval_minutes": interval,
        "source_code": {"commit": commit, "dirty": dirty, "ingestion_module_sha256": source_sha},
        "configuration": config_data,
        "configuration_sha256": config_hash,
        "total_raw_rows": raw_rows,
        "selected_rows": len(signals),
        "excluded_nonselected_rows": excluded_rows,
        "invalid_input_count": 0,
        "rejection_reasons": [],
        "date_range_utc": {
            "start_inclusive": signals[0].candle_open_time.isoformat().replace("+00:00", "Z") if signals else None,
            "end_inclusive": signals[-1].candle_open_time.isoformat().replace("+00:00", "Z") if signals else None,
        },
        "ordering_policy": ORDERING_POLICY,
    }
    _validate_json_schema(output_manifest, _OUTPUT_MANIFEST_SCHEMA_PATH, "ingestion output manifest")
    if output_manifest_path is not None:
        destination = Path(output_manifest_path).expanduser().absolute()
        protected = {signal_path.resolve(), metadata_file.resolve(), candle_path.resolve(), manifest_path.resolve()}
        if destination.resolve() in protected:
            raise FrozenSignalInputError("output manifest cannot overwrite a frozen input")
        for parent in (*reversed(destination.parents), destination):
            if parent.exists() and parent.is_symlink():
                raise FrozenSignalInputError("output manifest path cannot contain symlinks")
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            with destination.open("xb") as handle:
                handle.write(_canonical(output_manifest) + b"\n")
                handle.flush()
                os.fsync(handle.fileno())
        except FileExistsError as exc:
            raise FrozenSignalInputError("output manifest refuses overwrite") from exc
    return FrozenSignalIngestionResult(signals=signals, candles=candles, manifest=output_manifest)

