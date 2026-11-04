from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path

from .models import Candle


def load_ohlcv_csv(path: str | Path) -> list[Candle]:
    """Load `timestamp,open,high,low,close,volume` CSV data.

    Timestamp accepts ISO-8601 (recommended) or Unix seconds/milliseconds.
    The input must be chronological; duplicate/out-of-order data is rejected by
    the backtester instead of silently repaired.
    """
    with Path(path).open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        required = {"timestamp", "open", "high", "low", "close"}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise ValueError("CSV needs timestamp,open,high,low,close columns (volume optional)")
        return [
            Candle(
                timestamp=_parse_timestamp(row["timestamp"]),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["volume"]) if row.get("volume") else None,
            )
            for row in reader
        ]


def _parse_timestamp(value: str) -> datetime:
    try:
        numeric = float(value)
        if numeric > 10_000_000_000:
            numeric /= 1000
        return datetime.fromtimestamp(numeric, tz=timezone.utc)
    except ValueError:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
