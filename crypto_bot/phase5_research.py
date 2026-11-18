"""Read-only Phase 5 strategy diagnostics and chronological research artifacts.

This module consumes preserved input ledgers and writes only derived outputs to
a caller-selected new directory. It does not generate or change strategy
signals, parameters, thresholds, or portfolio results.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import random
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Iterable

from .execution import BarExecutionModel
from .io import load_ohlcv_csv
from .models import Candle, Side


STRATEGIES = ("trend_following", "breakout", "momentum", "mean_reversion")
WINDOWS = (
    ("2020_2022", "2020-01-01T00:00:00+00:00", "2023-01-01T00:00:00+00:00"),
    ("2023_2024", "2023-01-01T00:00:00+00:00", "2025-01-01T00:00:00+00:00"),
    ("2025_2026-08-19_previously_examined", "2025-01-01T00:00:00+00:00", "2026-08-20T00:00:00+00:00"),
)
CONFIDENCE_BANDS = ((0.0, 0.55, "(0,.55]"), (0.55, 0.70, "(.55,.70]"),
                    (0.70, 0.85, "(.70,.85]"), (0.85, 1.0000001, "(.85,1]"))
STOP_DISTANCE_BANDS = ((0.0, 0.0025, "<=0.25%"), (0.0025, 0.005, "0.25-0.5%"),
                       (0.005, 0.01, "0.5-1%"), (0.01, math.inf, ">1%"))
MOMENTUM_BANDS = ((-math.inf, -0.01, "<-1%"), (-0.01, 0.0, "-1%-0%"),
                  (0.0, 0.01, "0-1%"), (0.01, math.inf, ">1%"))
HORIZONS = (1, 4, 16, 96)
BOOTSTRAP_SEED = 20261118
BOOTSTRAP_ITERATIONS = 2000
BOOTSTRAP_BLOCK_WEEKS = 4
SMALL_SAMPLE_N = 30


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fields is None:
        fields = list(dict.fromkeys(key for row in rows for key in row))
    if not fields:
        fields = ["empty"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _number(value: Any, default: float | None = None) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _truth(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "1", "yes")


def _dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"timestamp lacks timezone: {value}")
    return parsed.astimezone(timezone.utc)


def _window(value: str) -> str:
    stamp = _dt(value)
    for name, start, end in WINDOWS:
        if _dt(start) <= stamp < _dt(end):
            return name
    return "outside_declared_windows"


def _band(value: float | None, ranges: tuple[tuple[float, float, str], ...]) -> str:
    if value is None:
        return "unknown"
    for lower, upper, label in ranges:
        if lower < value <= upper:
            return label
    return "unknown"


def _finite_band_metadata(ranges: tuple[tuple[float, float, str], ...]) -> list[dict[str, Any]]:
    def bound(value: float) -> float | str:
        if math.isinf(value):
            return "Infinity" if value > 0 else "-Infinity"
        return value
    return [{"lower_exclusive": bound(lower), "upper_inclusive": bound(upper), "label": label}
            for lower, upper, label in ranges]


def wilson_interval(wins: int, count: int, z: float = 1.95996398454) -> tuple[float | None, float | None]:
    if count <= 0:
        return None, None
    rate = wins / count
    denom = 1 + z * z / count
    center = (rate + z * z / (2 * count)) / denom
    half = z * math.sqrt(rate * (1 - rate) / count + z * z / (4 * count * count)) / denom
    return max(0.0, center - half), min(1.0, center + half)


def summarize(rows: list[dict[str, Any]], lane: str, strategy: str, window: str = "all") -> dict[str, Any]:
    net = [float(row["net_pnl"]) for row in rows]
    winners = [pnl for pnl in net if pnl > 0]
    losers = [pnl for pnl in net if pnl < 0]
    gross = [float(row["gross_pnl"]) for row in rows]
    execution_gross = [float(row["execution_gross_pnl"]) for row in rows]
    fees = [float(row["fees"]) for row in rows]
    slippage = [float(row["slippage"]) for row in rows]
    funding = [float(row["funding_contribution"]) for row in rows]
    n = len(rows)
    win_low, win_high = wilson_interval(len(winners), n)
    total_profit = sum(winners)
    total_loss = -sum(losers)
    def avg(field: str) -> float | None:
        vals = [float(row[field]) for row in rows if _number(row.get(field)) is not None]
        return mean(vals) if vals else None
    return {
        "lane": lane, "strategy": strategy, "window": window, "trade_count": n,
        "sample_warning": "NO_TRADES" if n == 0 else ("SMALL_SAMPLE" if n < SMALL_SAMPLE_N else ""),
        "gross_expectancy": mean(gross) if gross else None,
        "execution_gross_expectancy": mean(execution_gross) if execution_gross else None,
        "net_expectancy": mean(net) if net else None,
        "gross_pnl": sum(gross), "execution_gross_pnl": sum(execution_gross), "net_pnl": sum(net),
        "win_rate": len(winners) / n if n else None, "win_rate_wilson95_low": win_low,
        "win_rate_wilson95_high": win_high, "profit_factor": total_profit / total_loss if total_loss else ("Infinity" if total_profit else None),
        "average_winner": mean(winners) if winners else None,
        "average_loser": mean(losers) if losers else None,
        "zero_pnl_count": sum(pnl == 0 for pnl in net),
        "fees": sum(fees), "average_fees": mean(fees) if fees else None,
        "slippage": sum(slippage), "average_slippage": mean(slippage) if slippage else None,
        "funding_contribution": sum(funding), "average_funding_contribution": mean(funding) if funding else None,
        "holding_hours_mean": avg("holding_hours"), "mfe_pct_mean": avg("mfe_pct"),
        "mae_pct_mean": avg("mae_pct"),
        "stop_distance_pct_mean": avg("stop_distance_pct"),
        "stop_loss_exit_count": sum(str(row.get("exit_reason", "")) == "stop_loss" for row in rows),
        "gap_stop_exit_count": sum(_truth(row.get("gap_at_open")) or _truth(row.get("stop_gap_exit")) for row in rows),
        "stop_budget_breach_count": sum(_truth(row.get("initial_stop_budget_breach")) or
            (_number(row.get("realized_loss_to_initial_stop_budget_ratio")) or 0) > 1 for row in rows),
        "exit_reasons": json.dumps(dict(sorted(Counter(str(row.get("exit_reason", "unknown")) for row in rows).items())), sort_keys=True, separators=(",", ":")),
        "unique_calendar_weeks": len({row.get("entry_week") for row in rows if row.get("entry_week")}),
    }


def _excursions(rows: list[dict[str, Any]], candles: list[Candle], index_by_time: dict[str, int]) -> None:
    for row in rows:
        entry_index = index_by_time.get(str(row.get("entry_timestamp", "")))
        exit_index = index_by_time.get(str(row.get("exit_timestamp", "")))
        entry = _number(row.get("entry_execution_price"))
        if entry is None:
            entry = _number(row.get("raw_entry_price"))
        side = str(row.get("direction", ""))
        if side not in ("long", "short") and row.get("side") in ("long", "short"):
            side = str(row["side"])
        if entry_index is None or exit_index is None or entry is None or exit_index < entry_index or side not in ("long", "short"):
            row["mfe_pct"] = row["mae_pct"] = None
            row["excursion_candles"] = 0
            continue
        path = candles[entry_index:exit_index + 1]
        if side == "long":
            favorable = [100 * (bar.high / entry - 1) for bar in path]
            adverse = [100 * (bar.low / entry - 1) for bar in path]
        else:
            favorable = [100 * (entry / bar.low - 1) for bar in path]
            adverse = [100 * (entry / bar.high - 1) for bar in path]
        row["mfe_pct"] = max(favorable) if favorable else None
        row["mae_pct"] = min(adverse) if adverse else None
        row["excursion_candles"] = len(path)
        row["excursion_method"] = "OHLC extrema inclusive of entry/exit candles; intrabar order unknown"


def _normalize_independent(path: Path) -> list[dict[str, Any]]:
    result = []
    for raw in _read_csv(path):
        row = dict(raw)
        row.update({
            "lane": "independent_counterfactual",
            "signal_id": int(raw["signal_id"]),
            "strategy": raw["strategy"], "direction": raw["direction"],
            "signal_timestamp": raw["signal_timestamp"], "entry_timestamp": raw["entry_timestamp"],
            "exit_timestamp": raw["exit_timestamp"], "entry_execution_price": float(raw["entry_execution_price"]),
            "raw_entry_price": float(raw["raw_entry_price"]), "raw_exit_price": float(raw["raw_exit_price"]),
            "gross_pnl": float(raw["gross_pnl"]), "execution_gross_pnl": float(raw["execution_gross_pnl"]),
            "fees": float(raw["fees"]), "slippage": float(raw["slippage"]),
            "funding_contribution": -float(raw["funding_paid"]), "net_pnl": float(raw["net_pnl"]),
            "exit_reason": raw["exit_reason"], "holding_hours": float(raw["holding_hours"]),
            "quantity": float(raw["quantity"]), "stop_price": float(raw["stop_price"]),
            "target_price": float(raw["target_price"]) if raw.get("target_price") else None,
            "entry_week": _dt(raw["entry_timestamp"]).strftime("%G-W%V"),
            "entry_month": _dt(raw["entry_timestamp"]).strftime("%Y-%m"),
            "decision_regime": raw.get("regime") or "unknown",
        })
        identity = row["execution_gross_pnl"] - row["fees"] + row["funding_contribution"]
        row["net_reconciliation_delta"] = row["net_pnl"] - identity
        result.append(row)
    return result


def _normalize_portfolio(root: Path, signals: dict[int, dict[str, Any]], candles: list[Candle]) -> list[dict[str, Any]]:
    position_events: dict[str, dict[str, Any]] = {}
    position_entries: dict[str, list[dict[str, Any]]] = defaultdict(list)
    reserved_risk: dict[str, float] = {}
    for event in _jsonl(root / "order_ledger_decimal_v1.jsonl"):
        if event.get("event") == "reserved" and event.get("order_id"):
            reserved_risk[str(event["order_id"])] = float(event.get("stop_risk", 0))
    for event in _jsonl(root / "order_fill_event_ledger_v1.jsonl"):
        payload = event.get("payload", {})
        pos_id = payload.get("position_id") or event.get("position_id")
        if event.get("event_type") in ("ORDER_PARTIALLY_FILLED", "ORDER_FILLED") and pos_id:
            position_entries[str(pos_id)].append(event)
        if event.get("event_type") == "POSITION_CLOSED" and event.get("position_id"):
            position_events[str(event["position_id"])] = event
    time_index = {bar.timestamp.isoformat(): i for i, bar in enumerate(candles)}
    results = []
    for pos in _jsonl(root / "completed_positions_decimal_v1.jsonl"):
        position_id = str(pos["position_id"])
        entries = position_entries.get(position_id, [])
        close = position_events.get(position_id)
        if not entries or close is None:
            raise ValueError(f"Phase 4 position lacks linked entry/close event: {position_id}")
        fills = []
        signal_ids = set()
        for event in entries:
            payload = event.get("payload", {})
            fills.append((float(payload["quantity"]), float(payload["price"]), float(payload.get("slippage", 0))))
            if payload.get("signal_id") is not None:
                signal_ids.add(int(payload["signal_id"]))
        if len(signal_ids) != 1:
            raise ValueError(f"Phase 4 fills have missing/conflicting signal ids: {position_id}")
        signal_id = signal_ids.pop()
        signal = signals.get(signal_id)
        if signal is None:
            raise ValueError(f"Phase 4 signal {signal_id} absent from frozen signal file")
        quantity = sum(item[0] for item in fills)
        order_ids = {str(event.get("order_id")) for event in entries if event.get("order_id")}
        if len(order_ids) != 1:
            raise ValueError(f"Phase 4 position has missing/conflicting order ids: {position_id}")
        risk_budget = reserved_risk.get(next(iter(order_ids)))
        entry_price = sum(q * price for q, price, _ in fills) / quantity
        entry_slippage = sum(item[2] for item in fills)
        entry_time = min(_dt(event["event_time"]) for event in entries).isoformat()
        close_payload = close.get("payload", {})
        exit_fill = float(close_payload["fill_price"])
        exit_raw = float(close_payload["raw_price"])
        exit_qty = float(close_payload["quantity"])
        exit_slippage = max(0.0, -Side(pos["side"]).sign * (exit_fill - exit_raw) * exit_qty)
        gross = float(pos["realized_gross_pnl"])
        entry_fees = float(pos["entry_fees_paid"])
        exit_fees = float(pos["exit_fees_paid"])
        funding = float(pos["funding_paid"])
        net = float(pos["final_net_pnl"])
        stop_price = float(signal["stop_price"])
        raw_entry = entry_price - Side(pos["side"]).sign * entry_slippage / quantity
        entry_stamp = _dt(entry_time).isoformat()
        exit_stamp = _dt(close["event_time"]).isoformat()
        row = {
            "lane": "executable_portfolio", "position_id": position_id, "signal_id": signal_id,
            "strategy": signal["strategy"], "direction": pos["side"], "decision_regime": signal.get("regime", "unknown"),
            "confidence": float(signal["confidence"]), "signal_timestamp": signal["timestamp"],
            "entry_timestamp": entry_stamp, "exit_timestamp": exit_stamp,
            "entry_execution_price": entry_price, "raw_entry_price": raw_entry,
            "raw_exit_price": exit_raw, "exit_execution_price": exit_fill,
            "quantity": float(pos["quantity_closed"]), "gross_pnl": gross + entry_slippage + exit_slippage,
            "execution_gross_pnl": gross, "fees": entry_fees + exit_fees,
            "entry_fees": entry_fees, "exit_fees": exit_fees,
            "slippage": entry_slippage + exit_slippage, "funding_contribution": funding,
            "net_pnl": net, "exit_reason": close.get("reason_code") or close_payload.get("reason", "unknown"),
            "holding_hours": max(0.0, (_dt(exit_stamp) - _dt(entry_stamp)).total_seconds() / 3600),
            "stop_price": float(signal["stop_price"]),
            "target_price": float(signal["take_profit"]) if signal.get("take_profit") else None,
            "entry_week": _dt(entry_stamp).strftime("%G-W%V"), "entry_month": _dt(entry_stamp).strftime("%Y-%m"),
            "net_reconciliation_delta": net - (gross - entry_fees - exit_fees + funding),
            "stop_distance_pct": abs(entry_price - stop_price) / entry_price * 100,
            "gap_at_open": bool(close_payload.get("gap_at_open", False)),
            "stop_gap_exit": bool(close_payload.get("gap_at_open", False)) and str(close_payload.get("reason", "")) == "stop_loss",
            "stop_gap_overshoot_pct": (max(0.0, (stop_price - exit_raw) / stop_price * 100) if pos["side"] == "long" else
                max(0.0, (exit_raw - stop_price) / stop_price * 100)) if str(close_payload.get("reason", "")) == "stop_loss" else 0.0,
            "risk_budget": risk_budget,
            "realized_loss_to_initial_stop_budget_ratio": max(0.0, -net) / risk_budget if risk_budget else None,
            "initial_stop_budget_breach": bool(risk_budget and max(0.0, -net) > risk_budget),
            "ensemble_selected": signal.get("ensemble_selected"),
            "signal_characteristics_json": json.dumps({
                "regime": signal.get("regime"), "regime_allowed": signal.get("regime_allowed"),
                "ensemble_selected": signal.get("ensemble_selected"),
                "features": json.loads(signal.get("features") or "{}"),
                "stop_distance_pct": abs(float(signal["reference_price"]) - float(signal["stop_price"])) / float(signal["reference_price"]),
                "feature_readiness": json.loads(signal.get("feature_readiness") or "{}"),
                "gap_before_signal": signal.get("gap_before_signal"),
            }, sort_keys=True, separators=(",", ":")),
        }
        entry_index = time_index.get(entry_stamp)
        exit_index = time_index.get(exit_stamp)
        row["mfe_pct"] = row["mae_pct"] = None
        if entry_index is not None and exit_index is not None and exit_index >= entry_index:
            path = candles[entry_index:exit_index + 1]
            entry = row["entry_execution_price"]
            if pos["side"] == "long":
                row["mfe_pct"] = max((bar.high / entry - 1) * 100 for bar in path)
                row["mae_pct"] = min((bar.low / entry - 1) * 100 for bar in path)
            else:
                row["mfe_pct"] = max((entry / bar.low - 1) * 100 for bar in path)
                row["mae_pct"] = min((entry / bar.high - 1) * 100 for bar in path)
        results.append(row)
    return results


def _group_rows(rows: list[dict[str, Any]], lane: str, key: str) -> list[dict[str, Any]]:
    result = []
    for strategy in STRATEGIES:
        values = sorted({str(row.get(key, "unknown")) for row in rows if row["strategy"] == strategy})
        for value in values:
            selected = [row for row in rows if row["strategy"] == strategy and str(row.get(key, "unknown")) == value]
            record = summarize(selected, lane, strategy)
            record["slice"] = key
            record["slice_value"] = value
            result.append(record)
    return result


def _weekly_monthly(rows_by_lane: dict[str, list[dict[str, Any]]], first_timestamp: datetime,
                    last_timestamp: datetime) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    from datetime import date, timedelta
    weeks, months = [], []
    first_date, last_date = first_timestamp.date(), last_timestamp.date()
    first_iso = first_date.isocalendar()
    last_iso = last_date.isocalendar()
    cursor_week = date.fromisocalendar(first_iso.year, first_iso.week, 1)
    last_week = date.fromisocalendar(last_iso.year, last_iso.week, 1)
    week_keys = []
    while cursor_week <= last_week:
        iso = cursor_week.isocalendar()
        week_keys.append(f"{iso.year}-W{iso.week:02d}")
        cursor_week += timedelta(days=7)
    year, month = first_date.year, first_date.month
    month_keys = []
    while (year, month) <= (last_date.year, last_date.month):
        month_keys.append(f"{year:04d}-{month:02d}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    for lane, rows in rows_by_lane.items():
        for key, target, periods in (("entry_week", weeks, week_keys), ("entry_month", months, month_keys)):
            groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
            for row in rows:
                groups[(row["strategy"], str(row[key]))].append(row)
            for strategy in STRATEGIES:
                for period in periods:
                    members = groups.get((strategy, period), [])
                    record = summarize(members, lane, strategy, period)
                    record["period"] = period
                    record["no_trade_period"] = not bool(members)
                    target.append(record)
    return weeks, months


def _clustering_rows(rows_by_lane: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    result = []
    for lane, rows in rows_by_lane.items():
        groups = [("all_strategies", rows)] + [(strategy, [row for row in rows if row["strategy"] == strategy])
            for strategy in STRATEGIES]
        for strategy, members in groups:
            intervals = sorted((_dt(row["entry_timestamp"]), _dt(row["exit_timestamp"]), index)
                for index, row in enumerate(members))
            active: list[tuple[datetime, int]] = []
            overlapping: set[int] = set()
            peak = 0
            for start, end, index in intervals:
                active = [(active_end, active_index) for active_end, active_index in active if active_end > start]
                if active:
                    overlapping.add(index)
                    overlapping.update(active_index for _active_end, active_index in active)
                active.append((end, index))
                peak = max(peak, len(active))
            result.append({"lane": lane, "strategy": strategy, "trade_count": len(members),
                "overlap_trade_count": len(overlapping),
                "overlap_trade_pct": len(overlapping) / len(members) if members else None,
                "maximum_concurrent_trades": peak,
                "occupied_trade_weeks": len({row.get("entry_week") for row in members if row.get("entry_week")}),
                "sample_warning": "NO_TRADES" if not members else ("SMALL_SAMPLE" if len(members) < SMALL_SAMPLE_N else ""),
                "interpretation": "independent counterfactual overlap is not shared-capital executable concurrency" if lane == "independent_counterfactual" else "account-constrained executable position concurrency"})
    return result


def _moving_block_bootstrap(rows: list[dict[str, Any]], lane: str, strategy: str) -> dict[str, Any]:
    grouped: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        grouped[str(row["entry_week"])].append(float(row["net_pnl"]))
    keys = sorted(grouped)
    if not keys:
        return {"lane": lane, "strategy": strategy, "trade_count": 0, "occupied_weeks": 0,
                "seed": BOOTSTRAP_SEED, "iterations": BOOTSTRAP_ITERATIONS, "block_length_weeks": BOOTSTRAP_BLOCK_WEEKS,
                "expectancy_ci95_low": None, "expectancy_ci95_high": None, "sample_warning": "NO_TRADES"}
    # Reconstruct contiguous ISO week keys, including no-trade weeks.
    first_year, first_week = map(int, keys[0].split("-W"))
    last_year, last_week = map(int, keys[-1].split("-W"))
    from datetime import date, timedelta
    cursor = date.fromisocalendar(first_year, first_week, 1)
    end = date.fromisocalendar(last_year, last_week, 1)
    calendar = []
    while cursor <= end:
        iso = cursor.isocalendar()
        calendar.append(f"{iso.year}-W{iso.week:02d}")
        cursor += timedelta(days=7)
    clusters = [grouped.get(key, []) for key in calendar]
    rng = random.Random(BOOTSTRAP_SEED)
    expectancies, pnl_per_trade, win_rates, profit_factors = [], [], [], []
    length = min(BOOTSTRAP_BLOCK_WEEKS, len(clusters))
    for _ in range(BOOTSTRAP_ITERATIONS):
        sample: list[float] = []
        sampled_blocks = 0
        while sampled_blocks < len(calendar):
            index = rng.randint(0, len(calendar) - length)
            for cluster in clusters[index:index + length]:
                sample.extend(cluster)
            sampled_blocks += length
        if sample:
            expectancies.append(sum(sample) / len(sample))
            pnl_per_trade.append(sum(sample) / len(sample))
            win_rates.append(sum(x > 0 for x in sample) / len(sample))
            winners = sum(x for x in sample if x > 0)
            losers = -sum(x for x in sample if x < 0)
            profit_factors.append(winners / losers if losers else (math.inf if winners else 0.0))
    def bounds(values: list[float]) -> tuple[float | str | None, float | str | None]:
        if not values:
            return None, None
        values.sort()
        low, high = values[int(0.025 * (len(values) - 1))], values[int(0.975 * (len(values) - 1))]
        return ("Infinity" if math.isinf(low) else low, "Infinity" if math.isinf(high) else high)
    e_low, e_high = bounds(expectancies)
    p_low, p_high = bounds(pnl_per_trade)
    w_low, w_high = bounds(win_rates)
    pf_low, pf_high = bounds(profit_factors)
    unique_trade_weeks = len(grouped)
    return {"lane": lane, "strategy": strategy, "trade_count": sum(map(len, grouped.values())),
        "occupied_weeks": unique_trade_weeks, "calendar_weeks": len(calendar), "seed": BOOTSTRAP_SEED,
        "iterations": BOOTSTRAP_ITERATIONS, "block_length_weeks": length,
        "expectancy_ci95_low": e_low, "expectancy_ci95_high": e_high,
        "net_pnl_per_trade_ci95_low": p_low, "net_pnl_per_trade_ci95_high": p_high,
        "win_rate_ci95_low": w_low, "win_rate_ci95_high": w_high,
        "profit_factor_ci95_low": pf_low, "profit_factor_ci95_high": pf_high,
        "sample_warning": "SMALL_SAMPLE" if unique_trade_weeks < SMALL_SAMPLE_N else "",
        "method": "non-circular moving blocks of calendar-week clusters; percentile 95% intervals"}


def _predictive_rows(signals: list[dict[str, str]]) -> list[dict[str, Any]]:
    members: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for raw in signals:
        try:
            strategy = raw["strategy"]
            confidence = float(raw["confidence"])
            ref = float(raw["reference_price"])
            stop = float(raw["stop_price"])
            features = json.loads(raw.get("features") or "{}")
            outcomes = json.loads(raw.get("forward_outcomes") or "{}")
        except (ValueError, TypeError, KeyError):
            continue
        stop_pct = abs(ref - stop) / ref if ref else None
        momentum = _number(features.get("momentum_15m"))
        dimensions = [("strategy", strategy), ("regime", raw.get("regime") or "unknown"),
                      ("confidence_band", _band(confidence, CONFIDENCE_BANDS)),
                      ("direction", raw.get("direction") or "unknown"),
                      ("ensemble_selected", raw.get("ensemble_selected") or "unknown"),
                      ("stop_distance_band", _band(stop_pct, STOP_DISTANCE_BANDS)),
                      ("momentum_15m_band", _band(momentum, MOMENTUM_BANDS)),
                      ("chronological_window", _window(raw["timestamp"]))]
        for dimension, value in dimensions:
            for horizon in HORIZONS:
                outcome = outcomes.get(str(horizon))
                if not outcome or outcome.get("status") != "complete":
                    continue
                if dimension == "chronological_window":
                    bound = next((end for name, _start, end in WINDOWS if name == value), None)
                    if bound is None or _dt(outcome["timestamp"]) >= _dt(bound):
                        continue
                members[(dimension, value, str(horizon), strategy)].append({
                    # Frozen v1 fields are decimal return ratios despite their
                    # `_pct` suffix; convert to percentage points for reports.
                    "return": 100 * float(outcome["direction_adjusted_return_pct"]),
                    "mfe": 100 * float(outcome["mfe_pct"]), "mae": 100 * float(outcome["mae_pct"]),
                    "week": _dt(raw["timestamp"]).strftime("%G-W%V"),
                })
    rows = []
    for (dimension, value, horizon, strategy), values in sorted(members.items()):
        n = len(values)
        wins = sum(item["return"] > 0 for item in values)
        lo, hi = wilson_interval(wins, n)
        by_week: dict[str, list[tuple[bool, float]]] = defaultdict(list)
        for item in values:
            by_week[item["week"]].append((item["return"] > 0, item["return"]))
        week_keys = sorted(by_week)
        from datetime import date, timedelta
        first_year, first_week = map(int, week_keys[0].split("-W")) if week_keys else (0, 0)
        last_year, last_week = map(int, week_keys[-1].split("-W")) if week_keys else (0, 0)
        calendar = []
        if week_keys:
            cursor = date.fromisocalendar(first_year, first_week, 1)
            end_date = date.fromisocalendar(last_year, last_week, 1)
            while cursor <= end_date:
                iso = cursor.isocalendar()
                calendar.append(f"{iso.year}-W{iso.week:02d}")
                cursor += timedelta(days=7)
        block_len = min(BOOTSTRAP_BLOCK_WEEKS, len(calendar))
        seed = BOOTSTRAP_SEED + sum((i + 1) * ord(char) for i, char in enumerate("|".join((dimension, value, horizon, strategy))))
        rng = random.Random(seed)
        accuracy_samples, return_samples = [], []
        cluster_bootstrap_dimensions = {"strategy", "confidence_band", "regime", "chronological_window"}
        if calendar and dimension in cluster_bootstrap_dimensions:
            weekly_stats = [(len(by_week.get(week, [])),
                             sum(item[0] for item in by_week.get(week, [])),
                             sum(item[1] for item in by_week.get(week, []))) for week in calendar]
            for _ in range(BOOTSTRAP_ITERATIONS):
                sample_n = sample_wins = 0
                sample_return = 0.0
                blocks = 0
                while blocks < len(calendar):
                    start = rng.randint(0, len(calendar) - block_len)
                    for count, week_wins, week_return in weekly_stats[start:start + block_len]:
                        sample_n += count
                        sample_wins += week_wins
                        sample_return += week_return
                    blocks += block_len
                if sample_n:
                    accuracy_samples.append(sample_wins / sample_n)
                    return_samples.append(sample_return / sample_n)
        def percentile_interval(samples: list[float]) -> tuple[float | None, float | None]:
            if not samples:
                return None, None
            samples.sort()
            return (samples[int(0.025 * (len(samples) - 1))], samples[int(0.975 * (len(samples) - 1))])
        block_accuracy_low, block_accuracy_high = percentile_interval(accuracy_samples)
        block_return_low, block_return_high = percentile_interval(return_samples)
        rows.append({"lane": "signal_label_predictive", "strategy": strategy, "dimension": dimension,
            "slice_value": value, "horizon_candles": int(horizon), "sample_count": n,
            "sample_warning": "SMALL_SAMPLE" if n < SMALL_SAMPLE_N else "",
            "directional_accuracy": wins / n if n else None, "accuracy_wilson95_low": lo,
            "accuracy_wilson95_high": hi, "mean_direction_adjusted_return_pct": mean(x["return"] for x in values),
            "occupied_signal_weeks": len(week_keys), "block_bootstrap_seed": seed,
            "block_bootstrap_accuracy95_low": block_accuracy_low, "block_bootstrap_accuracy95_high": block_accuracy_high,
            "block_bootstrap_mean_return95_low": block_return_low, "block_bootstrap_mean_return95_high": block_return_high,
            "block_bootstrap_warning": ("SUBGROUP_WILSON_ONLY; OVERLAPPING_LABELS" if dimension not in cluster_bootstrap_dimensions else
                ("SMALL_SAMPLE" if len(week_keys) < SMALL_SAMPLE_N else "")),
            "mean_mfe_pct": mean(x["mfe"] for x in values), "mean_mae_pct": mean(x["mae"] for x in values),
            "observation_unit": "percent points; frozen return ratios multiplied by 100; signals overlap; predictive label, not executable P&L"})
    return rows


def _split_audit(signals: list[dict[str, str]], lane_rows: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    result = []
    max_horizon = max(HORIZONS)
    for name, start, end in WINDOWS:
        start_dt, end_dt = _dt(start), _dt(end)
        signal_rows = [row for row in signals if start_dt <= _dt(row["timestamp"]) < end_dt]
        for horizon in HORIZONS:
            complete = 0
            purged = 0
            for row in signal_rows:
                outcomes = json.loads(row.get("forward_outcomes") or "{}")
                outcome = outcomes.get(str(horizon), {})
                if outcome.get("status") != "complete":
                    continue
                if _dt(outcome["timestamp"]) >= end_dt:
                    purged += 1
                else:
                    complete += 1
            result.append({"window": name, "lane": "signal_label_predictive", "horizon_candles": horizon,
                "start_inclusive_utc": start, "end_exclusive_utc": end, "signal_count": len(signal_rows),
                "eligible_complete_labels": complete, "purged_cross_boundary_labels": purged,
                "warmup_policy": "strictly earlier candles/features only; no warm-up observations scored",
                "future_label_policy": "forward outcomes used only as labels; not loaded into signal decisions",
                "holdout_status": "previously_observed_historical_diagnostic; not untouched out-of-sample"})
        for lane, rows in lane_rows.items():
            dated = [row for row in rows if start_dt <= _dt(row["signal_timestamp"]) < end_dt]
            crosses = sum(_dt(row["exit_timestamp"]) >= end_dt for row in dated)
            result.append({"window": name, "lane": lane, "horizon_candles": "trade_exit",
                "start_inclusive_utc": start, "end_exclusive_utc": end, "signal_count": len(dated),
                "eligible_complete_labels": len(dated) - crosses, "purged_cross_boundary_labels": crosses,
                "warmup_policy": "earlier observations seed features only; fresh account state per scored window",
                "future_label_policy": "trade retained in signal-time window; boundary-crossing exit purged",
                "holdout_status": "previously_observed_historical_diagnostic; not untouched out-of-sample"})
    result.append({"window": "prospective_future_holdout", "lane": "all", "horizon_candles": "not_available",
        "start_inclusive_utc": "pending", "end_exclusive_utc": "pending", "signal_count": 0,
        "eligible_complete_labels": 0, "purged_cross_boundary_labels": 0,
        "warmup_policy": "protocol must be locked before data arrive", "future_label_policy": "sealed until code/config/protocol lock",
        "holdout_status": "pending; no unseen data supplied"})
    return result


def _trade_slice_rows(rows_by_lane: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    output = []
    dimensions = ("decision_regime", "confidence_band", "direction", "ensemble_selected",
                  "stop_distance_band", "momentum_15m_band", "signal_window")
    for lane, rows in rows_by_lane.items():
        for row in rows:
            confidence = _number(row.get("confidence"))
            stop_distance = None
            momentum = None
            if row.get("signal_characteristics_json"):
                details = json.loads(row["signal_characteristics_json"])
                stop_distance = details.get("stop_distance_pct")
                momentum = _number((details.get("features") or {}).get("momentum_15m"))
            if stop_distance is None:
                signal_ref = _number(row.get("reference_price"))
                stop = _number(row.get("stop_price"))
                if signal_ref and stop:
                    stop_distance = abs(signal_ref - stop) / signal_ref
            row["confidence_band"] = _band(confidence, CONFIDENCE_BANDS)
            row["stop_distance_band"] = _band(stop_distance, STOP_DISTANCE_BANDS)
            row["momentum_15m_band"] = _band(momentum, MOMENTUM_BANDS)
            if row.get("ensemble_selected") is None and row.get("signal_characteristics_json"):
                row["ensemble_selected"] = json.loads(row["signal_characteristics_json"]).get("ensemble_selected")
        for dimension in dimensions:
            groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
            for candidate in rows:
                groups[(candidate["strategy"], str(candidate.get(dimension, "unknown")))].append(candidate)
            for (strategy, value), members in sorted(groups.items()):
                metric = summarize(members, lane, strategy)
                metric["dimension"] = dimension
                metric["slice_value"] = value
                output.append(metric)
    return output


def _cost_sensitivity(rows: list[dict[str, Any]], metadata: dict[str, Any]) -> list[dict[str, Any]]:
    fee_base = float(metadata["configuration"]["fee_bps"])
    slip_base = float(metadata["configuration"]["slippage_bps"])
    funding_base = float(metadata["configuration"]["funding_bps_8h"])
    results = []
    for strategy in STRATEGIES:
        source = [row for row in rows if row["strategy"] == strategy]
        if not source:
            for axis in ("fee_only", "slippage_only", "funding_only"):
                results.append({"lane": "independent_counterfactual_hypothetical", "strategy": strategy,
                    "scenario_method": "fixed_quantity_one_factor", "axis": axis, "scenario_bps": None,
                    "trade_count": 0, "net_pnl": None, "net_expectancy": None, "sample_warning": "NO_TRADES"})
            continue
        for axis, levels in (("fee_only", (0, 2, 5, 10)), ("slippage_only", (0, 2, 5, 10)),
                             ("funding_only", (0, 1, 2))):
            for level in levels:
                pnls = []
                for row in source:
                    raw_entry = float(row["raw_entry_price"])
                    raw_exit = float(row["raw_exit_price"])
                    quantity = float(row["quantity"])
                    turn_notional = quantity * (raw_entry + raw_exit)
                    fees = float(row["fees"])
                    slip = float(row["slippage"])
                    funding = float(row["funding_contribution"])
                    if axis == "fee_only":
                        fees = fees * (level / fee_base) if fee_base else turn_notional * level / 10000
                    elif axis == "slippage_only":
                        slip = slip * (level / slip_base) if slip_base else turn_notional * level / 10000
                    else:
                        funding = funding * (level / funding_base) if funding_base else 0.0
                    pnls.append(float(row["gross_pnl"]) - fees - slip + funding)
                results.append({"lane": "independent_counterfactual_hypothetical", "strategy": strategy,
                    "scenario_method": "fixed_quantity_one_factor", "axis": axis, "scenario_bps": level,
                    "fee_baseline_bps": fee_base, "slippage_baseline_bps": slip_base,
                    "funding_baseline_bps_8h": funding_base, "trade_count": len(pnls),
                    "net_pnl": sum(pnls), "net_expectancy": mean(pnls) if pnls else None,
                    "sample_warning": "SMALL_SAMPLE" if len(pnls) < SMALL_SAMPLE_N else "",
                    "classification": "hypothetical sensitivity; fixed quantity; not executable performance"})
    return results


def _entry_timing(rows: list[dict[str, Any]], candles: list[Candle], index_by_time: dict[str, int],
                  metadata: dict[str, Any]) -> list[dict[str, Any]]:
    fee_rate = float(metadata["configuration"]["fee_bps"]) / 10000
    slip_bps = float(metadata["configuration"]["slippage_bps"])
    funding_rate = float(metadata["configuration"]["funding_bps_8h"]) / 10000
    execution = BarExecutionModel(fee_rate=fee_rate, slippage_bps=slip_bps,
        funding_rate_per_8h=funding_rate, candle_minutes=15,
        max_participation_rate=float(metadata["configuration"].get("max_participation_rate", 0.05)))
    result = []
    for strategy in STRATEGIES:
        for delay in (1, 2, 4):
            pnl_values = []
            reasons: Counter[str] = Counter()
            for row in rows:
                if row["strategy"] != strategy:
                    continue
                side = Side(row["direction"])
                base_index = index_by_time.get(str(row["entry_timestamp"]))
                end_index = index_by_time.get(str(row["exit_timestamp"]))
                if base_index is None or end_index is None:
                    reasons["entry_or_exit_timestamp_missing"] += 1
                    continue
                entry_index = base_index + delay
                if entry_index >= len(candles) or entry_index > end_index:
                    reasons["delay_after_original_exit_or_data_end"] += 1
                    continue
                entry_bar = candles[entry_index]
                entry_fill = execution.fill(entry_bar.open, side, float(row["quantity"]))
                stop = float(row["stop_price"])
                target = _number(row.get("target_price"))
                if (side is Side.LONG and entry_fill.price <= stop) or (side is Side.SHORT and entry_fill.price >= stop):
                    reasons["entry_invalidates_original_stop"] += 1
                    continue
                exit_raw, exit_reason, exit_index = None, None, None
                funding = 0.0
                prior_close = entry_bar.close
                for i in range(entry_index, end_index + 1):
                    bar = candles[i]
                    if i == entry_index:
                        elapsed_hours = 0.25
                    else:
                        elapsed_hours = 0.25
                    funding += execution.funding_amount(side, float(row["quantity"]), bar.close, funding_rate, elapsed_hours)
                    exit_request = execution.intrabar_exit_request(side, stop, target, bar)
                    if exit_request:
                        exit_raw, exit_reason = exit_request
                        exit_index = i
                        break
                    prior_close = bar.close
                if exit_raw is None:
                    end_bar = candles[end_index]
                    exit_raw = end_bar.close
                    exit_reason, exit_index = "original_exit_time_reached", end_index
                exit_side = Side.SHORT if side is Side.LONG else Side.LONG
                exit_fill = execution.fill(exit_raw, exit_side, float(row["quantity"]))
                gross = side.sign * float(row["quantity"]) * (exit_raw - entry_bar.open)
                net = gross - entry_fill.slippage - exit_fill.slippage - entry_fill.fee - exit_fill.fee - funding
                pnl_values.append(net)
                reasons[str(exit_reason)] += 1
            result.append({"lane": "independent_counterfactual_hypothetical", "strategy": strategy,
                "delay_bars": delay, "delay_minutes": delay * 15, "trade_count": len(pnl_values),
                "net_pnl": sum(pnl_values) if pnl_values else None,
                "net_expectancy": mean(pnl_values) if pnl_values else None,
                "sample_warning": "SMALL_SAMPLE" if len(pnl_values) < SMALL_SAMPLE_N else "",
                "excluded_reasons": json.dumps(dict(sorted(reasons.items())), separators=(",", ":")),
                "classification": "hypothetical fixed-quantity entry-delay diagnostic; original stop/target retained"})
    return result


def _cost_aware_existing_scenarios(root: Path) -> list[dict[str, Any]]:
    results = []
    scenario_root = root / "reports" / "execution_cost_sensitivity_2026-11-09"
    for tag, fee, slip in (("fee0_slip0", 0, 0), ("fee2_slip2", 2, 2),
                           ("fee5_slip5", 5, 5), ("fee10_slip10", 10, 10)):
        path = scenario_root / tag / "completed_trade_ledger.csv"
        if not path.exists():
            continue
        members = _normalize_independent(path)
        for strategy in STRATEGIES:
            subset = [row for row in members if row["strategy"] == strategy]
            metric = summarize(subset, "independent_counterfactual_hypothetical", strategy)
            results.append({"scenario_method": "preserved_existing_full_cost_aware_resizing",
                "scenario": tag, "fee_bps_per_side": fee, "slippage_bps_per_side": slip,
                "strategy": strategy, "trade_count": metric["trade_count"], "net_pnl": metric["net_pnl"],
                "net_expectancy": metric["net_expectancy"], "sample_warning": metric["sample_warning"],
                "classification": "hypothetical; source scenario re-sizes quantity under cost assumptions; not executable"})
    return results


def _git_metadata(root: Path) -> dict[str, Any]:
    def run(args: list[str]) -> str | None:
        try:
            return subprocess.check_output(args, cwd=root, text=True, stderr=subprocess.DEVNULL).strip()
        except (OSError, subprocess.CalledProcessError):
            return None
    status = run(["git", "status", "--porcelain"])
    return {"source_commit": run(["git", "rev-parse", "HEAD"]),
            "source_worktree_dirty": bool(status), "source_status_porcelain": status,
            "python": sys.version}


def run_phase5(*, root: Path, output: Path, signals_path: Path, signal_metadata_path: Path,
               candles_path: Path, independent_ledger_path: Path, phase4_root: Path) -> dict[str, Any]:
    root, output = root.resolve(), output.resolve()
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f"refusing to overwrite nonempty output directory: {output}")
    preserved_phase4 = (root / "reports" / "phase4_financial_audit_2026-11-14").resolve()
    if preserved_phase4 == output.resolve() or preserved_phase4 in output.resolve().parents:
        raise ValueError("Phase 5 output cannot be placed inside preserved Phase 4 audit directory")
    output.mkdir(parents=True, exist_ok=True)
    inputs = {"signals": signals_path.resolve(), "signal_metadata": signal_metadata_path.resolve(),
              "candles": candles_path.resolve(), "independent_completed_ledger": independent_ledger_path.resolve(),
              "independent_evaluator_metadata": (independent_ledger_path.parent / "run_metadata.json").resolve(),
              "phase4_baseline_config": (root / "configs" / "athena2_portfolio_baseline_v1.json").resolve(),
              "phase4_completed_positions": (phase4_root / "completed_positions_decimal_v1.jsonl").resolve(),
              "phase4_order_ledger": (phase4_root / "order_ledger_decimal_v1.jsonl").resolve(),
              "phase4_events": (phase4_root / "portfolio_events_decimal_v1.jsonl").resolve(),
              "phase4_order_fill_events": (phase4_root / "order_fill_event_ledger_v1.jsonl").resolve(),
              "phase4_manifest": (phase4_root / "run_manifest_v1.json").resolve()}
    for tag in ("fee0_slip0", "fee2_slip2", "fee5_slip5", "fee10_slip10"):
        candidate = root / "reports" / "execution_cost_sensitivity_2026-11-09" / tag / "completed_trade_ledger.csv"
        if candidate.is_file():
            inputs[f"preserved_cost_scenario_{tag}"] = candidate.resolve()
            metadata_candidate = candidate.parent / "run_metadata.json"
            if metadata_candidate.is_file():
                inputs[f"preserved_cost_scenario_metadata_{tag}"] = metadata_candidate.resolve()
    if any(not path.is_file() for path in inputs.values()):
        missing = [str(path) for path in inputs.values() if not path.is_file()]
        raise FileNotFoundError("required Phase 5 inputs missing: " + ", ".join(missing))
    hash_before = {key: sha256(path) for key, path in inputs.items()}
    signals = _read_csv(signals_path)
    candles = load_ohlcv_csv(candles_path)
    with signal_metadata_path.open(encoding="utf-8") as handle:
        signal_metadata = json.load(handle)
    with inputs["phase4_manifest"].open(encoding="utf-8") as handle:
        phase4_manifest = json.load(handle)
    if hash_before["signals"] != phase4_manifest["frozen_signal_sha256"]:
        raise ValueError("frozen signal SHA-256 differs from Phase 4 replay manifest")
    if hash_before["candles"] != phase4_manifest["candle_sha256"]:
        raise ValueError("candle SHA-256 differs from Phase 4 replay manifest")
    signal_by_id = {int(row["signal_id"]): row for row in signals}
    independent = _normalize_independent(independent_ledger_path)
    for row in independent:
        signal = signal_by_id.get(int(row["signal_id"]))
        if signal is None:
            raise ValueError(f"counterfactual signal {row['signal_id']} absent from frozen signal file")
        row["confidence"] = float(signal["confidence"])
        row["decision_regime"] = signal.get("regime") or "unknown"
        row["signal_characteristics_json"] = json.dumps({
            "regime": signal.get("regime"), "regime_allowed": signal.get("regime_allowed"),
            "ensemble_selected": signal.get("ensemble_selected"), "features": json.loads(signal.get("features") or "{}"),
            "stop_distance_pct": abs(float(signal["reference_price"]) - float(signal["stop_price"])) / float(signal["reference_price"]),
            "feature_readiness": json.loads(signal.get("feature_readiness") or "{}"),
            "gap_before_signal": signal.get("gap_before_signal"),
        }, sort_keys=True, separators=(",", ":"))
        reference = _number(row.get("reference_price"))
        stop = _number(row.get("stop_price"))
        row["stop_distance_pct"] = abs(reference - stop) / reference * 100 if reference and stop else None
    portfolio = _normalize_portfolio(phase4_root, signal_by_id, candles)
    time_index = {bar.timestamp.isoformat(): i for i, bar in enumerate(candles)}
    _excursions(independent, candles, time_index)
    lane_rows = {"independent_counterfactual": independent, "executable_portfolio": portfolio}
    all_trade_rows = independent + portfolio
    for row in all_trade_rows:
        row["signal_window"] = _window(row["signal_timestamp"])
        row["entry_timestamp"] = _dt(row["entry_timestamp"]).isoformat()
        row["exit_timestamp"] = _dt(row["exit_timestamp"]).isoformat()
        row["signal_timestamp"] = _dt(row["signal_timestamp"]).isoformat()
        row["entry_week"] = _dt(row["entry_timestamp"]).strftime("%G-W%V")
        row["entry_month"] = _dt(row["entry_timestamp"]).strftime("%Y-%m")
    predictive = _predictive_rows(signals)
    scorecard = []
    for lane, rows in lane_rows.items():
        for strategy in STRATEGIES:
            members = [row for row in rows if row["strategy"] == strategy]
            scorecard.append(summarize(members, lane, strategy))
            for name, start, end in WINDOWS:
                window_members = [row for row in members if start <= row["signal_timestamp"] < end and
                                  row["exit_timestamp"] < end]
                scorecard.append(summarize(window_members, lane, strategy, name))
    regime_rows = []
    for lane, rows in lane_rows.items():
        regime_rows.extend(_group_rows(rows, lane, "decision_regime"))
    trade_slices = _trade_slice_rows(lane_rows)
    weekly, monthly = _weekly_monthly(lane_rows, candles[0].timestamp, candles[-1].timestamp)
    clustering = _clustering_rows(lane_rows)
    bootstrap = [_moving_block_bootstrap([row for row in rows if row["strategy"] == strategy], lane, strategy)
                 for lane, rows in lane_rows.items() for strategy in STRATEGIES]
    split_audit = _split_audit(signals, lane_rows)
    # Metadata carries the evaluator's fixed-reference cost assumptions; do not use
    # the Phase 4 portfolio configuration to label independent-trade costs.
    evaluation_metadata_path = independent_ledger_path.parent / "run_metadata.json"
    if evaluation_metadata_path.exists():
        evaluator_metadata = json.loads(evaluation_metadata_path.read_text(encoding="utf-8"))
        cost_config = evaluator_metadata.get("configuration", {})
    else:
        cost_config = {"fee_bps": 5, "slippage_bps": 5, "funding_bps_8h": 1,
                       "max_participation_rate": 0.05}
        evaluator_metadata = {"configuration": cost_config}
    cost_rows = _cost_sensitivity(independent, evaluator_metadata)
    timing_rows = _entry_timing(independent, candles, time_index, evaluator_metadata)
    cost_rows.extend(_cost_aware_existing_scenarios(root))
    existing_cost_metadata = {}
    for tag in ("fee0_slip0", "fee2_slip2", "fee5_slip5", "fee10_slip10"):
        meta_path = root / "reports" / "execution_cost_sensitivity_2026-11-09" / tag / "run_metadata.json"
        if meta_path.is_file():
            existing_cost_metadata[tag] = json.loads(meta_path.read_text(encoding="utf-8"))
    write_rows = [
        ("completed_trade_attribution.csv", all_trade_rows), ("strategy_scorecard.csv", scorecard),
        ("regime_performance.csv", regime_rows), ("weekly_performance.csv", weekly),
        ("monthly_performance.csv", monthly), ("predictive_performance.csv", predictive),
        ("trade_slice_performance.csv", trade_slices),
        ("trade_clustering.csv", clustering),
        ("bootstrap_intervals.csv", bootstrap), ("split_audit.csv", split_audit),
        ("cost_sensitivity.csv", cost_rows), ("entry_timing_sensitivity.csv", timing_rows),
    ]
    for name, rows in write_rows:
        _write_csv(output / name, rows)
    hash_after = {key: sha256(path) for key, path in inputs.items()}
    if hash_before != hash_after:
        raise AssertionError("one or more frozen research inputs changed during Phase 5 run")
    output_hashes = {name: sha256(output / name) for name, _ in write_rows}
    manifest = {
        "phase": "Athena 2.0 Phase 5 Strategy Diagnostics and Validation",
        "classification": "historical diagnostics; independent counterfactual and signal-label results are not executable portfolio performance",
        "created_at_utc": datetime.now(timezone.utc).isoformat(), "source": _git_metadata(root),
        "input_paths_and_sha256": {key: {"path": str(path), "sha256": hash_before[key]} for key, path in inputs.items()},
        "dataset_period": {"start": candles[0].timestamp.isoformat(), "end": candles[-1].timestamp.isoformat(),
                           "candles": len(candles), "signals": len(signals)},
        "strategies": list(STRATEGIES), "windows": [{"name": name, "start_inclusive": start,
            "end_exclusive": end, "status": "previously_observed_historical_diagnostic"} for name, start, end in WINDOWS],
        "final_out_of_sample_holdout": "pending; no unseen data supplied; data through 2026-08-19 are previously examined",
        "methodology": {"future_outcomes_are_labels_only": True, "signal_label_horizons_candles": list(HORIZONS),
            "signal_label_percent_units": "frozen decimal ratios multiplied by 100 to report percentage points",
            "confidence_bands": _finite_band_metadata(CONFIDENCE_BANDS),
            "stop_distance_bands": _finite_band_metadata(STOP_DISTANCE_BANDS),
            "momentum_15m_bands": _finite_band_metadata(MOMENTUM_BANDS),
            "trade_excursions": "OHLC extrema inclusive of entry and exit candles; intrabar ordering is unknown",
            "portfolio_lane": "Phase 4 executable completed-position ledger joined to preserved fill/close events and frozen signal IDs",
            "independent_lane": "fixed-reference independent per-signal ledger; overlapping counterfactuals; no shared capital/liquidity",
            "funding_sign_normalization": {"executable_portfolio": "funding_contribution = signed ledger cash movement",
                "independent_counterfactual": "funding_contribution = -funding_paid (research evaluator field is positive charge)",
                "net_identity": "executable: realized gross - fees + funding cash contribution; counterfactual: execution gross - fees + funding contribution"},
            "sample_warning_threshold": SMALL_SAMPLE_N,
            "bootstrap": {"seed": BOOTSTRAP_SEED, "iterations": BOOTSTRAP_ITERATIONS,
                "block_length_calendar_weeks": BOOTSTRAP_BLOCK_WEEKS, "method": "non-circular moving block bootstrap over weekly trade clusters; 95% percentile intervals"},
            "cost_sensitivity": "fixed-quantity one-factor re-marking; zero cost is hypothetical; preserved all-in re-sizing scenarios separately identified",
            "entry_timing": "fixed quantity, delays of 1/2/4 bars, original stop/target retained; hypothetical",
            "parameter_changes_or_optimization": False,
            "historical_periods_are_untouched_holdout": False},
        "research_configuration": evaluator_metadata.get("configuration", {}),
        "preserved_cost_scenario_run_metadata": existing_cost_metadata,
        "counts": {"independent_counterfactual_trades": len(independent), "executable_portfolio_positions": len(portfolio),
                   "signal_rows": len(signals)},
        "phase4_reference": {"source_commit": phase4_manifest["code_commit"],
            "completed_positions": phase4_manifest["completed_positions"],
            "final_equity_usdt": phase4_manifest["final_summary"]["final_equity_usdt"],
            "insolvency_deficit_usdt": phase4_manifest["final_summary"]["insolvency_deficit_usdt"]},
        "output_sha256": output_hashes,
        "assertions": {"all_inputs_unchanged": True,
            "portfolio_positions_joined_to_signal_and_lifecycle_events": len(portfolio) == phase4_manifest["completed_positions"],
            "counterfactual_count_matches_evaluator_metadata": len(independent) == signal_metadata.get("signals", len(independent)),
            "all_trade_reconciliation_deltas_within_1e-7": all(abs(float(row["net_reconciliation_delta"])) <= 1e-7 for row in all_trade_rows)},
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, allow_nan=False), encoding="utf-8")
    code_paths = {"phase5_research.py": root / "crypto_bot" / "phase5_research.py",
        "run_phase5_diagnostics.py": root / "tools" / "run_phase5_diagnostics.py",
        "execution.py": root / "crypto_bot" / "execution.py", "portfolio.py": root / "crypto_bot" / "portfolio.py",
        "frozen_signals.py": root / "crypto_bot" / "frozen_signals.py",
        "frozen_signal_schema": root / "schemas" / "athena2_frozen_signal_v1.schema.json"}
    manifest["source"]["code_file_sha256"] = {name: sha256(path) for name, path in code_paths.items()}
    portfolio_net = sum(float(row["net_pnl"]) for row in portfolio)
    expected_net = float(phase4_manifest["final_summary"]["final_equity_usdt"]) - float(phase4_manifest["final_summary"]["initial_equity_usdt"])
    if abs(portfolio_net - expected_net) > 1e-7:
        raise AssertionError(f"Phase 4 completed-position sum {portfolio_net} != manifest net {expected_net}")
    manifest["assertions"]["portfolio_net_matches_phase4_manifest_within_1e-7"] = True
    _write_report(output, scorecard, predictive, bootstrap, split_audit, cost_rows, timing_rows, trade_slices, clustering, manifest)
    manifest["output_sha256"]["report.md"] = sha256(output / "report.md")
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, allow_nan=False), encoding="utf-8")
    return manifest


def _write_report(output: Path, scorecard: list[dict[str, Any]], predictive: list[dict[str, Any]],
                  bootstrap: list[dict[str, Any]], split_audit: list[dict[str, Any]],
                  cost_rows: list[dict[str, Any]], timing_rows: list[dict[str, Any]],
                  trade_slices: list[dict[str, Any]], clustering: list[dict[str, Any]], manifest: dict[str, Any]) -> None:
    rows = [row for row in scorecard if row["window"] == "all"]
    cluster_overview = {row["lane"]: row for row in clustering if row["strategy"] == "all_strategies"}
    lines = ["# Athena 2.0 — Phase 5 Strategy Diagnostics and Validation", "",
        f"Run created: `{manifest['created_at_utc']}`. Source commit: `{manifest['source'].get('source_commit')}`.", "",
        "## Scope and interpretation", "",
        "This report evaluates the existing fixed strategies for predictive and executable potential. It does not optimize or alter strategy parameters. The Phase 4 executable account ledger and independent counterfactual trade ledger remain separate. Signal-label metrics describe predictive behavior, not trade or portfolio P&L.", "",
        "All supplied market history ends on 2026-08-19 and has been examined in prior research. The chronological windows below are historical diagnostics, not untouched out-of-sample evidence. A genuinely future holdout is pending.", "",
        "## Strategy scorecards", "",
        "Gross in the executable lane is reconstructed before estimated adverse slippage; execution gross is the Phase 4 realized gross from fill prices. Research gross is raw reference-price P&L and execution gross includes slippage. Fees are positive costs; funding contribution is signed cash to the account. Net identities are checked per trade.", "",
        "| Lane | Strategy | Trades | Gross exp. | Exec. gross exp. | Net exp. | Win rate | PF | Avg win | Avg loss | Fees | Slippage | Funding cash | Hold h | MFE % | MAE % | Stop exits | Gap stops | Stop budget breaches | Warning |", "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|"]
    for row in rows:
        def fmt(key: str, spec: str = ".2f") -> str:
            value = row.get(key)
            if value is None: return "n/a"
            if isinstance(value, str): return value
            return format(value, spec)
        lines.append(f"| {row['lane']} | {row['strategy']} | {row['trade_count']} | {fmt('gross_expectancy')} | {fmt('execution_gross_expectancy')} | {fmt('net_expectancy')} | {fmt('win_rate', '.1%')} | {fmt('profit_factor')} | {fmt('average_winner')} | {fmt('average_loser')} | {fmt('fees')} | {fmt('slippage')} | {fmt('funding_contribution')} | {fmt('holding_hours_mean')} | {fmt('mfe_pct_mean')} | {fmt('mae_pct_mean')} | {row['stop_loss_exit_count']} | {row['gap_stop_exit_count']} | {row['stop_budget_breach_count']} | {row['sample_warning'] or '—'} |")
    lines += ["", "The independent counterfactual rows overlap and assume independent fixed-reference sizing; they do not share account cash or aggregate liquidity. Their summed P&L, win rate, or profit factor must not be interpreted as executable portfolio performance. The executable lane contains the account-constrained Phase 4 positions only.", "",
        "## Predictive signal results", "",
        f"The source signal dataset contains {len(predictive)} grouped label rows across strategy, decision-time regime, fixed confidence bands, direction, ensemble selection, stop-distance bands, momentum bands, and chronological windows. Fixed signal-label horizons are 1, 4, 16, and 96 candles. These use future candles only as outcome labels. Confidence intervals and sample counts are in `predictive_performance.csv`; groups under 30 observations or 30 occupied weeks are marked `SMALL_SAMPLE`. After-cost completed-trade slices by confidence, regime, direction, ensemble selection, stop distance, momentum and chronological period are in `trade_slice_performance.csv` ({len(trade_slices)} rows).",
        "## Costs, entry timing, and robustness", "",
        f"Cost tables contain {len(cost_rows)} rows: fixed-quantity one-factor cost re-marking plus preserved full cost-aware re-sizing scenarios. Zero cost is a hypothetical reference. Entry timing has {len(timing_rows)} fixed-quantity delay scenarios; the original stop/target remains unchanged. All are counterfactual analyses, not altered historical results.", "",
        f"Weekly cluster bootstrap uses seed {BOOTSTRAP_SEED}, {BOOTSTRAP_ITERATIONS} replicates, four-week non-circular moving blocks, and 95% percentile intervals. See `bootstrap_intervals.csv`; fewer than 30 occupied trade weeks is flagged. Direct overlap and peak concurrency are in `trade_clustering.csv`: independent counterfactual trades overlap for {cluster_overview.get('independent_counterfactual', {}).get('overlap_trade_count', 0)} trades and peak at {cluster_overview.get('independent_counterfactual', {}).get('maximum_concurrent_trades', 0)} concurrent counterfactuals; the account-constrained portfolio peaks at {cluster_overview.get('executable_portfolio', {}).get('maximum_concurrent_trades', 0)}. The 91-position executable lane has limited independent time clusters, so any interval is exploratory.", "",
        "## Chronological controls", "",
        "Splits use UTC signal/availability chronology. Earlier observations may seed causal warm-up features but are not scored. Forward outcome labels crossing a scored-window boundary are purged. Completed trades remain assigned to the signal-time window and are excluded from window metrics if their exit crosses its end. Every listed historical window was previously observed; none is the final holdout.", "",
        "## Reproducibility and limits", "",
        f"Frozen input hashes, source commit, Phase 4 reference, methodology, and output hashes are recorded in `manifest.json`. Input hashes were checked before and after processing. Phase 4 reference: {manifest['phase4_reference']['completed_positions']} closed positions, final equity {manifest['phase4_reference']['final_equity_usdt']} USDT, insolvency deficit {manifest['phase4_reference']['insolvency_deficit_usdt']}.", "",
        "MFE/MAE uses OHLC extrema over entry and exit candles; the order of high/low moves within a candle is unknown. Attribution is descriptive and does not prove that a signal, entry, exit, or regime caused the observed outcome. Sparse slices and block-bootstrap assumptions limit inference. No results are tuned or presented to make historical returns appear profitable.", ""]
    (output / "report.md").write_text("\n".join(lines), encoding="utf-8")
