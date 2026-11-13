"""Read-only validation and normalization for the frozen selected-signal file."""
from __future__ import annotations

import csv
import hashlib
import json
import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Sequence

from jsonschema import Draft202012Validator

from .models import Candle, Side
from .portfolio_models import FrozenSignal, PortfolioConfig, SignalDisposition, parse_utc
from .portfolio_ledger import D


class FrozenSignalInputError(ValueError):
    pass


class FrozenSignalReader:
    def __init__(self, config: PortfolioConfig, signal_schema_path: str | Path | None = None) -> None:
        self.config = config
        self.schema_path = Path(signal_schema_path) if signal_schema_path else Path(__file__).resolve().parents[1] / "schemas" / "athena2_frozen_signal_v1.schema.json"

    @staticmethod
    def sha256(path: str | Path) -> str:
        digest = hashlib.sha256()
        with Path(path).open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    def read(self, *, signal_path: str | Path, metadata_path: str | Path, candle_path: str | Path,
             candles: Sequence[Candle], expected_signal_sha256: str,
             expected_candle_sha256: str) -> tuple[list[FrozenSignal], list[SignalDisposition], dict[str, object]]:
        actual_signal_hash = self.sha256(signal_path)
        actual_candle_hash = self.sha256(candle_path)
        if actual_signal_hash.lower() != expected_signal_sha256.lower():
            raise FrozenSignalInputError("SOURCE_HASH_MISMATCH: frozen signal bytes do not match pinned SHA-256")
        if actual_candle_hash.lower() != expected_candle_sha256.lower():
            raise FrozenSignalInputError("SOURCE_HASH_MISMATCH: candle bytes do not match pinned SHA-256")
        metadata_hash = self.sha256(metadata_path)
        metadata = json.loads(Path(metadata_path).read_text(encoding="utf-8"))
        if not isinstance(metadata, dict) or not isinstance(metadata.get("source"), str):
            raise FrozenSignalInputError("SOURCE_SCHEMA_INVALID: source run metadata is incomplete")
        if metadata.get("candles") != len(candles):
            raise FrozenSignalInputError("SOURCE_SCHEMA_INVALID: metadata candle count does not match input")

        candle_by_time: dict[datetime, tuple[int, Candle]] = {}
        prior = None
        for index, candle in enumerate(candles):
            try:
                stamp = parse_utc(candle.timestamp)
            except (TypeError, ValueError) as exc:
                raise FrozenSignalInputError("SOURCE_SCHEMA_INVALID: candle timestamps must be UTC") from exc
            if prior is not None and stamp <= prior:
                raise FrozenSignalInputError("SOURCE_SCHEMA_INVALID: candles must be unique and chronological")
            candle_by_time[stamp] = (index, candle)
            prior = stamp

        schema = json.loads(self.schema_path.read_text(encoding="utf-8"))
        validator = Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)
        rows: list[dict[str, str]] = []
        with Path(signal_path).open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            required = {"signal_id", "timestamp", "available_at", "strategy", "direction", "confidence",
                        "ensemble_selected", "reference_price", "stop_price", "take_profit"}
            if not reader.fieldnames or not required.issubset(reader.fieldnames):
                raise FrozenSignalInputError("SOURCE_SCHEMA_INVALID: frozen signal CSV is missing required columns")
            rows.extend(reader)
        if metadata.get("signals") is not None and metadata["signals"] != len(rows):
            raise FrozenSignalInputError("SOURCE_SCHEMA_INVALID: metadata signal count does not match CSV")

        dispositions: list[SignalDisposition | None] = [None] * len(rows)
        pending: list[tuple[int, FrozenSignal]] = []
        seen_ids: defaultdict[int, list[int]] = defaultdict(list)
        duplicate_keys: defaultdict[tuple[datetime, str], list[int]] = defaultdict(list)
        parsed_selected: dict[int, FrozenSignal] = {}

        for row_index, row in enumerate(rows, 1):
            source_id: int | None = None
            try:
                source_id = int(row.get("signal_id", ""))
                if source_id <= 0:
                    source_id = None
            except (TypeError, ValueError):
                pass
            flag = row.get("ensemble_selected")
            if flag not in ("True", "False"):
                dispositions[row_index - 1] = SignalDisposition(row_index, source_id, self.config.instrument_allowlist[0], None,
                    row.get("strategy"), None, "rejected", "INVALID_SIGNAL")
                continue
            if source_id is None:
                dispositions[row_index - 1] = SignalDisposition(row_index, None, self.config.instrument_allowlist[0], None,
                    row.get("strategy"), flag == "True", "rejected", "INVALID_SIGNAL")
                continue
            seen_ids[source_id].append(row_index - 1)
            if flag == "False":
                dispositions[row_index - 1] = SignalDisposition(row_index, source_id, self.config.instrument_allowlist[0], None,
                    row.get("strategy"), False, "filtered", "NOT_ENSEMBLE_SELECTED")
                continue
            try:
                if not row.get("strategy"):
                    raise ValueError("strategy missing")
                signal_time = parse_utc(row["timestamp"])
                available_at = parse_utc(row["available_at"])
                if available_at != signal_time + timedelta(minutes=self.config.execution.candle_minutes):
                    dispositions[row_index - 1] = SignalDisposition(row_index, source_id,
                        self.config.instrument_allowlist[0], available_at, row.get("strategy"), True,
                        "rejected", "TIMESTAMP_JOIN_MISMATCH")
                    continue
                joined = candle_by_time.get(signal_time)
                if joined is None:
                    raise LookupError("signal candle not found")
                candle_index, candle = joined
                reference = D(row["reference_price"])
                if not math.isclose(float(reference), candle.close, rel_tol=1e-12, abs_tol=1e-9):
                    raise LookupError("reference close mismatch")
                if candle_index + 1 >= len(candles):
                    dispositions[row_index - 1] = SignalDisposition(row_index, source_id, self.config.instrument_allowlist[0],
                        available_at, row.get("strategy"), True, "rejected", "ORDER_EXPIRED")
                    continue
                side = Side(row["direction"])
                confidence, stop = D(row["confidence"]), D(row["stop_price"])
                target = D(row["take_profit"]) if row.get("take_profit", "") else None
                if confidence <= 0 or confidence > 1 or reference <= 0 or stop <= 0 or (target is not None and target <= 0):
                    raise ValueError("invalid signal number")
                signal = FrozenSignal(source_id, self.config.instrument_allowlist[0], signal_time, available_at,
                    row["strategy"], side, confidence, reference, stop, target,
                    actual_signal_hash, actual_candle_hash, candle_index)
                normalized = {
                    "schema_version": "athena.frozen_signal.v1",
                    "source_signal_sha256": actual_signal_hash,
                    "source_candle_sha256": actual_candle_hash,
                    "instrument_id": signal.instrument_id,
                    "signal_id": signal.signal_id,
                    "candle_open_time": signal.candle_open_time.isoformat(),
                    "available_at": signal.available_at.isoformat(),
                    "strategy": signal.strategy,
                    "direction": signal.side.value,
                    "confidence": float(signal.confidence),
                    "reference_price": float(signal.reference_price),
                    "stop_price": float(signal.stop_price),
                    "take_profit": float(signal.take_profit) if signal.take_profit is not None else None,
                }
                validator.validate(normalized)
                parsed_selected[row_index - 1] = signal
                duplicate_keys[(available_at, signal.instrument_id)].append(row_index - 1)
            except LookupError as exc:
                reason = "TIMESTAMP_JOIN_MISMATCH"
                dispositions[row_index - 1] = SignalDisposition(row_index, source_id, self.config.instrument_allowlist[0],
                    None, row.get("strategy"), True, "rejected", reason)
            except Exception:
                dispositions[row_index - 1] = SignalDisposition(row_index, source_id, self.config.instrument_allowlist[0],
                    None, row.get("strategy"), True, "rejected", "INVALID_SIGNAL")

        for source_id, indices in seen_ids.items():
            if len(indices) > 1:
                for index in indices:
                    if rows[index].get("ensemble_selected") == "True":
                        row = rows[index]
                        dispositions[index] = SignalDisposition(index + 1, source_id, self.config.instrument_allowlist[0],
                            None, row.get("strategy"), True, "rejected", "INVALID_SIGNAL")
                        parsed_selected.pop(index, None)
        for (_available, instrument), indices in duplicate_keys.items():
            if len(indices) > 1:
                for index in indices:
                    signal = parsed_selected.pop(index, None)
                    source_id = int(rows[index]["signal_id"])
                    dispositions[index] = SignalDisposition(index + 1, source_id, instrument,
                        signal.available_at if signal else None, rows[index].get("strategy"), True,
                        "rejected", "DUPLICATE_SELECTED_SIGNAL")
        for index, signal in parsed_selected.items():
            dispositions[index] = SignalDisposition(index + 1, signal.signal_id, signal.instrument_id,
                signal.available_at, signal.strategy, True, "eligible")
            pending.append((index, signal))

        manifest = {
            "signal_path": str(Path(signal_path).resolve()), "signal_sha256": actual_signal_hash,
            "candle_path": str(Path(candle_path).resolve()), "candle_sha256": actual_candle_hash,
            "metadata_path": str(Path(metadata_path).resolve()), "metadata_sha256": metadata_hash,
            "signal_rows": len(rows), "selected_eligible_rows": len(pending),
            "not_ensemble_selected_rows": sum(row.get("ensemble_selected") == "False" for row in rows),
            "instrument_allowlist": list(self.config.instrument_allowlist),
            "filter": "ensemble_selected == True", "interval_minutes": self.config.execution.candle_minutes,
        }
        selected = [signal for _, signal in sorted(pending, key=lambda pair: (
            pair[1].available_at, self.config.strategy_rank(pair[1].strategy), pair[1].instrument_id, pair[1].signal_id
        ))]
        return selected, [d for d in dispositions if d is not None], manifest
