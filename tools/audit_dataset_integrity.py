"""Read-only integrity audit for the merged and daily BTCUSDT 15m CSVs."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from crypto_bot.io import _parse_timestamp


INTERVAL = timedelta(minutes=15)
OHLCV = ("open", "high", "low", "close", "volume")


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def inspect_csv(path: Path, timestamp_field: str) -> tuple[list[tuple[datetime, tuple[float, ...], int]], list[dict]]:
    rows = []
    issues = []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        first = next(reader, None)
        if first is None:
            return [], [{"file": str(path), "row": 1, "issue": "empty file"}]
        has_header = timestamp_field in first
        if has_header:
            header = first
            records = enumerate(reader, start=2)
            timestamp_index = header.index(timestamp_field)
            value_indices = [header.index(field) for field in OHLCV]
        else:
            # Binance daily klines are commonly headerless and use the standard
            # 12-column order: open time, OHLCV, close time, ...
            if path.name == "combined.csv":
                return [], [{"file": str(path), "row": 1, "issue": "combined CSV missing its header"}]
            records = iter([(1, first), *enumerate(reader, start=2)])
            timestamp_index = 0
            value_indices = [1, 2, 3, 4, 5]
        for line, fields in records:
            try:
                if len(fields) <= max([timestamp_index, *value_indices]):
                    raise ValueError("row has fewer than six columns")
                timestamp = _parse_timestamp(fields[timestamp_index])
                values = tuple(float(fields[index]) for index in value_indices)
            except (TypeError, ValueError, OverflowError) as error:
                issues.append({"file": str(path), "row": line, "issue": f"unparseable timestamp/OHLCV: {error}"})
                continue
            o, h, low, close, volume = values
            if not all(math.isfinite(value) for value in values):
                issues.append({"file": str(path), "row": line, "timestamp": timestamp.isoformat(), "issue": "non-finite OHLCV"})
            if min(o, h, low, close) <= 0:
                issues.append({"file": str(path), "row": line, "timestamp": timestamp.isoformat(), "issue": "non-positive price"})
            if h < max(o, close) or low > min(o, close) or low > h:
                issues.append({"file": str(path), "row": line, "timestamp": timestamp.isoformat(), "issue": "inconsistent OHLC bounds"})
            if volume < 0:
                issues.append({"file": str(path), "row": line, "timestamp": timestamp.isoformat(), "issue": "negative volume"})
            rows.append((timestamp, values, line))
    return rows, issues


def sequence_issues(rows: list[tuple[datetime, tuple[float, ...], int]], label: str) -> tuple[list[dict], list[dict]]:
    counts = Counter(timestamp for timestamp, _, _ in rows)
    duplicates = [{"dataset": label, "timestamp": stamp.isoformat(), "count": count}
                  for stamp, count in counts.items() if count > 1]
    gaps = []
    ordered = sorted(rows, key=lambda item: item[0])
    for previous, current in zip(ordered, ordered[1:]):
        delta = current[0] - previous[0]
        if delta != INTERVAL:
            missing = delta / INTERVAL - 1 if delta > INTERVAL and delta % INTERVAL == timedelta(0) else None
            gaps.append({"dataset": label, "previous_timestamp": previous[0].isoformat(),
                         "next_timestamp": current[0].isoformat(), "interval": str(delta),
                         "missing_15m_candles": int(missing) if missing is not None else None,
                         "classification": "missing_candles" if missing is not None else "unexpected_interval_or_order"})
    return duplicates, gaps


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("combined_csv", type=Path)
    parser.add_argument("daily_dir", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    combined, combined_issues = inspect_csv(args.combined_csv, "timestamp")
    daily_files = sorted(args.daily_dir.glob("BTCUSDT-15m-*.csv"))
    daily_rows = []
    issues = list(combined_issues)
    for path in daily_files:
        rows, file_issues = inspect_csv(path, "open_time")
        daily_rows.extend(rows)
        issues.extend(file_issues)
    combined_dupes, combined_gaps = sequence_issues(combined, "combined")
    daily_dupes, daily_gaps = sequence_issues(daily_rows, "daily_source_files")

    combined_map: dict[datetime, tuple[float, ...]] = {}
    for stamp, values, _ in combined:
        combined_map.setdefault(stamp, values)
    daily_map: dict[datetime, tuple[float, ...]] = {}
    for stamp, values, _ in daily_rows:
        daily_map.setdefault(stamp, values)
    missing_from_combined = sorted(set(daily_map) - set(combined_map))
    missing_from_daily = sorted(set(combined_map) - set(daily_map))
    mismatched = [{"timestamp": stamp.isoformat(), "combined_ohlcv": combined_map[stamp], "daily_ohlcv": daily_map[stamp]}
                 for stamp in sorted(set(combined_map) & set(daily_map)) if combined_map[stamp] != daily_map[stamp]]

    timestamps = [stamp for stamp, _, _ in combined]
    missing_dates = []
    if timestamps:
        day = timestamps[0].date()
        end_day = timestamps[-1].date()
        file_dates = {path.stem.removeprefix("BTCUSDT-15m-") for path in daily_files}
        while day <= end_day:
            if day.isoformat() not in file_dates:
                missing_dates.append(day.isoformat())
            day += timedelta(days=1)

    report = {
        "combined_file": str(args.combined_csv.resolve()),
        "combined_sha256": digest(args.combined_csv),
        "combined_rows": len(combined),
        "combined_first_timestamp": min(timestamps).isoformat() if timestamps else None,
        "combined_last_timestamp": max(timestamps).isoformat() if timestamps else None,
        "daily_file_count": len(daily_files),
        "daily_source_rows": len(daily_rows),
        "daily_missing_dates": missing_dates,
        "combined_duplicates": combined_dupes,
        "combined_gaps_and_unexpected_intervals": combined_gaps,
        "daily_duplicates": daily_dupes,
        "daily_gaps_and_unexpected_intervals": daily_gaps,
        "invalid_ohlcv_or_parse_issues": issues,
        "rows_present_in_daily_but_missing_from_combined": [stamp.isoformat() for stamp in missing_from_combined],
        "rows_present_in_combined_but_missing_from_daily": [stamp.isoformat() for stamp in missing_from_daily],
        "ohlcv_value_mismatches": mismatched,
        "interpretation": "Read-only audit. No missing candles are synthesized or filled.",
    }
    (args.output_dir / "integrity_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    gap_rows = combined_gaps + daily_gaps
    with (args.output_dir / "all_gaps.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["dataset", "previous_timestamp", "next_timestamp", "interval", "missing_15m_candles", "classification"])
        writer.writeheader()
        writer.writerows(gap_rows)
    print(json.dumps({key: report[key] for key in (
        "combined_rows", "daily_file_count", "daily_source_rows", "daily_missing_dates",
        "combined_duplicates", "combined_gaps_and_unexpected_intervals", "daily_duplicates",
        "daily_gaps_and_unexpected_intervals", "invalid_ohlcv_or_parse_issues",
        "rows_present_in_daily_but_missing_from_combined", "rows_present_in_combined_but_missing_from_daily",
        "ohlcv_value_mismatches")}, indent=2))


if __name__ == "__main__":
    main()
