"""Create a separate, safety-on trade-economics and signal-quality audit.

This reruns the frozen chronological windows with observer-only diagnostics,
asserts exact reconciliation to the preserved safety/risk baseline, and never
assigns P&L to signals skipped by the kill switch.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from crypto_bot.backtest import BacktestConfig, IntrabarBacktester
from crypto_bot.controls import KillSwitch, KillSwitchConfig, KillSwitchMode
from crypto_bot.diagnostics import LogLevel
from crypto_bot.io import load_ohlcv_csv
from crypto_bot.models import Side
from crypto_bot.reporting import assert_trade_reconciliation, normalize_trade
from crypto_bot.risk import PositionSizer, RiskConfig
from crypto_bot.strategies import StrategyEnsemble
from run_chronological_windows import WINDOWS, FEE_RATE, SLIPPAGE_BPS, FUNDING_RATE_8H, PARTICIPATION, INITIAL_EQUITY, RISK_PCT


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def side_name(value) -> str:
    return value.value if hasattr(value, "value") else str(value)


class SignalRecorder:
    """Observe chosen signals with their exact causal decision snapshot."""
    def __init__(self) -> None:
        self.delegate = StrategyEnsemble()
        self.rows: list[dict[str, object]] = []

    def decide(self, market):
        decision = self.delegate.decide(market)
        signal = decision.signal
        if signal is not None:
            candle = market.candles_15m[-1]
            regime = market.regime
            self.rows.append({
                "timestamp": candle.timestamp.isoformat(),
                "decision_at_close_timestamp": (candle.timestamp + timedelta(minutes=15)).isoformat(),
                "signal_id": len(self.rows) + 1,
                "strategy": signal.strategy,
                "side": signal.side.value,
                "confidence": signal.confidence,
                "price_at_signal": candle.close,
                "rationale": signal.rationale,
                "regime": regime.primary.value,
                "regime_trend_strength": regime.trend_strength,
                "regime_volatility_pct": regime.volatility_pct,
                "regime_momentum_pct": regime.momentum_pct,
                "indicator_values": json.dumps(asdict(market.features) if market.features else {}, sort_keys=True),
                "considered_strategies": json.dumps(list(decision.considered)),
                "rejected_strategies": json.dumps(list(decision.rejected)),
            })
        return decision


def norm_rows(result) -> list[dict[str, object]]:
    return [normalize_trade(trade, SLIPPAGE_BPS / 10_000) for trade in result.trades]


def compare_to_baseline(result, base_dir: Path) -> None:
    summary = json.loads((base_dir / "summary.json").read_text(encoding="utf-8"))
    expected = list(csv.DictReader((base_dir / "trades.csv").open(encoding="utf-8", newline="")))
    actual = result.trades
    if len(actual) != len(expected) or len(actual) != summary["trades"]:
        raise AssertionError("diagnostic rerun trade count differs from preserved baseline")
    fields = ("trade_id", "signal_id", "order_id", "strategy", "side", "entry_time", "exit_time", "entry_price", "exit_price", "quantity", "stop_price", "take_profit", "gross_pnl", "net_pnl", "entry_fee", "exit_fee", "funding_paid")
    for index, (trade, row) in enumerate(zip(actual, expected), start=1):
        for field in fields:
            if field not in row:
                continue
            left = getattr(trade, field)
            if hasattr(left, "value"):
                left = left.value
            if hasattr(left, "isoformat"):
                left = left.isoformat()
            right = row[field]
            if isinstance(left, (int, float)) and not isinstance(left, bool):
                if not math.isclose(float(left), float(right), rel_tol=0, abs_tol=1e-9):
                    raise AssertionError(f"baseline trade {index} {field} changed: {left} != {right}")
            elif str(left) != str(right):
                raise AssertionError(f"baseline trade {index} {field} changed: {left!r} != {right!r}")
    if not math.isclose(result.final_equity, float(summary["final_equity"]), rel_tol=0, abs_tol=1e-8):
        raise AssertionError("diagnostic rerun final equity differs from preserved baseline")
    if not math.isclose(result.max_drawdown_pct, float(summary["max_drawdown_pct"]), rel_tol=0, abs_tol=1e-10):
        raise AssertionError("diagnostic rerun drawdown differs from preserved baseline")
    expected_kills = [(e[0], e[1], e[2]) for e in summary["kill_events"]]
    actual_kills = [(t.isoformat(), getattr(r, "value", r), d) for t, r, d in result.kill_events]
    if expected_kills != actual_kills:
        raise AssertionError(f"diagnostic rerun kill events differ: {actual_kills!r} != {expected_kills!r}")
    curve = list(csv.DictReader((base_dir / "equity_drawdown.csv").open(encoding="utf-8", newline="")))
    if len(curve) != len(result.equity_curve):
        raise AssertionError("diagnostic rerun equity curve length differs from baseline")
    for point, row in zip(result.equity_curve, curve):
        if point.timestamp.isoformat() != row["candle_open_timestamp"] or not math.isclose(point.equity, float(row["equity"]), rel_tol=0, abs_tol=1e-8):
            raise AssertionError("diagnostic rerun equity curve differs from baseline")


def trade_record(trade, base_row: dict[str, object], signal: dict[str, object]) -> dict[str, object]:
    duration = (trade.exit_time - trade.entry_time).total_seconds() / 3600
    if duration < 1:
        duration_bin = "<1h"
    elif duration < 4:
        duration_bin = "1–<4h"
    elif duration < 24:
        duration_bin = "4–<24h"
    else:
        duration_bin = "24h+"
    confidence = float(signal["confidence"])
    confidence_band = "<0.60" if confidence < 0.60 else "0.60–<0.70" if confidence < 0.70 else "0.70–<0.80" if confidence < 0.80 else "0.80+"
    return {
        **base_row,
        "entry_regime": signal["regime"],
        "entry_regime_trend_strength": signal["regime_trend_strength"],
        "entry_regime_volatility_pct": signal["regime_volatility_pct"],
        "entry_confidence": confidence,
        "confidence_band": confidence_band,
        "signal_rationale": signal["rationale"],
        "holding_hours": duration,
        "holding_duration_bin": duration_bin,
        "entry_notional": trade.quantity * trade.entry_price,
        "exit_notional": trade.quantity * trade.exit_price,
        "turnover_notional": trade.quantity * (trade.entry_price + trade.exit_price),
    }


def wilson_interval(wins: int, count: int, z: float = 1.96) -> tuple[float | None, float | None]:
    if count == 0:
        return None, None
    p = wins / count
    denom = 1 + z * z / count
    center = (p + z * z / (2 * count)) / denom
    half = z * math.sqrt(p * (1 - p) / count + z * z / (4 * count * count)) / denom
    return max(0.0, center - half), min(1.0, center + half)


def summarize(rows: list[dict[str, object]], keys: tuple[str, ...], window: str, group_type: str) -> list[dict[str, object]]:
    groups: dict[tuple[object, ...], list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        groups[tuple(row[k] for k in keys)].append(row)
    output = []
    for key, members in sorted(groups.items(), key=lambda item: tuple(str(x) for x in item[0])):
        nets = [float(r["net_pnl"]) for r in members]
        raw = [float(r["raw_gross_pnl"]) for r in members]
        fees = sum(float(r["fees"]) for r in members)
        slippage = sum(float(r["slippage_cost"]) for r in members)
        funding = [float(r["funding_paid"]) for r in members]
        wins = [x for x in nets if x > 0]
        losses = [x for x in nets if x < 0]
        positive = sum(wins)
        negative_abs = abs(sum(losses))
        count = len(members)
        mean = sum(nets) / count
        std = math.sqrt(sum((x - mean) ** 2 for x in nets) / (count - 1)) if count > 1 else None
        ci_low, ci_high = wilson_interval(len(wins), count)
        turnover = sum(float(r["turnover_notional"]) for r in members)
        gross_abs = sum(abs(x) for x in raw)
        net_funding_paid = sum(funding)
        record: dict[str, object] = {"window": window, "group_type": group_type, "trades": count}
        record.update(dict(zip(keys, key)))
        record.update({
            "gross_pnl_before_slippage": sum(raw),
            "execution_gross_pnl_after_slippage": sum(float(r["gross_pnl"]) for r in members),
            "fees": fees,
            "slippage": slippage,
            "funding_net_paid": net_funding_paid,
            "funding_paid": sum(max(0.0, x) for x in funding),
            "funding_received": sum(max(0.0, -x) for x in funding),
            "net_pnl": sum(nets),
            "win_rate": len(wins) / count,
            "win_rate_95ci_low": ci_low,
            "win_rate_95ci_high": ci_high,
            "average_win": sum(wins) / len(wins) if wins else None,
            "average_loss": sum(losses) / len(losses) if losses else None,
            "profit_factor_net": positive / negative_abs if negative_abs else None,
            "expectancy_net": mean,
            "expectancy_standard_error": std / math.sqrt(count) if std is not None else None,
            "expectancy_normal_95ci_low": mean - 1.96 * std / math.sqrt(count) if std is not None else None,
            "expectancy_normal_95ci_high": mean + 1.96 * std / math.sqrt(count) if std is not None else None,
            "turnover_notional": turnover,
            "fees_pct_turnover": fees / turnover if turnover else None,
            "slippage_pct_turnover": slippage / turnover if turnover else None,
            "fees_pct_gross_absolute": fees / gross_abs if gross_abs else None,
            "slippage_pct_gross_absolute": slippage / gross_abs if gross_abs else None,
            "mean_holding_hours": sum(float(r["holding_hours"]) for r in members) / count,
        })
        output.append(record)
    return output


def run_window(name: str, start: datetime, end: datetime, candles, root: Path, dataset_hash: str):
    warmup = [c for c in candles if c.timestamp < start]
    scored = [c for c in candles if start <= c.timestamp < end]
    window_root = root / name
    raw_root = window_root / "diagnostic_capture"
    base_dir = ROOT / "reports" / "chronological_windows" / name
    risk = RiskConfig(
        risk_per_trade_pct=RISK_PCT, max_position_notional_pct=1.0,
        max_margin_fraction=0.8, leverage=2.0,
        estimated_round_trip_cost_pct=2 * (FEE_RATE + SLIPPAGE_BPS / 10_000),
    )
    config = BacktestConfig(
        initial_equity=INITIAL_EQUITY, fee_rate=FEE_RATE, slippage_bps=SLIPPAGE_BPS,
        funding_rate_per_8h=FUNDING_RATE_8H, candle_minutes=15,
        max_participation_rate=PARTICIPATION, force_close_at_end=True,
        logging_level=LogLevel.ERROR, diagnostic_mode=True, diagnostics_dir=raw_root,
        kill_switch_mode=KillSwitchMode.SAFETY,
    )
    recorder = SignalRecorder()
    engine = IntrabarBacktester(config, PositionSizer(risk), kill_switch=KillSwitch(KillSwitchConfig()))
    result = engine.run(scored, ensemble=recorder, warmup_candles=warmup)
    compare_to_baseline(result, base_dir)

    diag = json.loads((raw_root / "backtest_summary.json").read_text(encoding="utf-8"))
    diag_signals = list(csv.DictReader((raw_root / "signals.csv").open(encoding="utf-8", newline="")))
    if len(diag_signals) != len(recorder.rows):
        raise AssertionError("observer signal snapshots do not match diagnostics signal records")
    for row, recorded in zip(diag_signals, recorder.rows):
        for field, diagnostics_field in (("signal_id", "signal_id"), ("strategy", "strategy"), ("side", "direction"), ("confidence", "confidence")):
            left = row[diagnostics_field]
            right = recorded[field]
            if field == "confidence":
                if not math.isclose(float(left), float(right), rel_tol=0, abs_tol=1e-12):
                    raise AssertionError("causal signal confidence disagrees with diagnostics")
            elif str(left) != str(right):
                raise AssertionError(f"causal signal {field} disagrees with diagnostics")

    signal_by_id = {int(row["signal_id"]): row for row in recorder.rows}
    base_trades = norm_rows(result)
    assert_trade_reconciliation(base_trades)
    if not math.isclose(sum(float(row["net_pnl"]) for row in base_trades), result.net_pnl, rel_tol=0, abs_tol=1e-7):
        raise AssertionError("trade ledger does not reconcile to final equity")
    enriched_trades = []
    for trade, row in zip(result.trades, base_trades):
        sig = signal_by_id.get(int(trade.signal_id))
        if sig is None:
            raise AssertionError(f"trade {trade.trade_id} has no matching signal {trade.signal_id}")
        enriched_trades.append(trade_record(trade, row, sig))

    order_rows = list(csv.DictReader((raw_root / "orders.csv").open(encoding="utf-8", newline="")))
    outcome_by_signal: dict[int, str] = {}
    for order in order_rows:
        if not order.get("signal_id"):
            continue
        sid = int(float(order["signal_id"]))
        event = order.get("event_type", "")
        if event == "ENTRY_SKIPPED_KILL_SWITCH":
            outcome_by_signal[sid] = "skipped_kill_switch"
        elif event == "ENTRY_ACCEPTED":
            outcome_by_signal[sid] = "entry_accepted"
        elif event == "ENTRY_REJECTED":
            outcome_by_signal[sid] = "entry_rejected"
    actual_trade_ids = {int(t.signal_id) for t in result.trades}
    for sid in actual_trade_ids:
        outcome_by_signal[sid] = "executed_trade"

    signal_rows = []
    for source in recorder.rows:
        row = dict(source)
        row["window"] = name
        row["outcome"] = outcome_by_signal.get(int(row["signal_id"]), "no_completed_entry_outcome")
        # Deliberately no P&L field is assigned to a skipped signal.
        signal_rows.append(row)

    component_rows = summarize(enriched_trades, ("strategy",), name, "strategy_component")
    side_rows = summarize(enriched_trades, ("side",), name, "side")
    regime_rows = summarize(enriched_trades, ("entry_regime",), name, "entry_regime")
    confidence_rows = summarize(enriched_trades, ("confidence_band",), name, "confidence_band")
    side_component_rows = summarize(enriched_trades, ("strategy", "side"), name, "strategy_x_side")
    regime_component_rows = summarize(enriched_trades, ("strategy", "entry_regime"), name, "strategy_x_regime")
    exit_rows = summarize(enriched_trades, ("exit_reason",), name, "exit_reason")
    duration_rows = summarize(enriched_trades, ("holding_duration_bin",), name, "holding_duration")
    group_rows = component_rows + side_rows + regime_rows + confidence_rows + side_component_rows + regime_component_rows + exit_rows + duration_rows
    signal_groups = summarize_signals(signal_rows, name)

    write_csv(window_root / "trades_enriched.csv", enriched_trades)
    write_csv(window_root / "signals_causal.csv", signal_rows)
    write_csv(window_root / "economics_groups.csv", group_rows)
    write_csv(window_root / "signal_groups.csv", signal_groups)
    result_summary = {
        "window": name,
        "dataset_sha256": dataset_hash,
        "baseline_config_unchanged": True,
        "baseline_comparison": "exact trade fields, equity curve, final equity, max drawdown, and kill events asserted equal",
        "safety_mode": "safety",
        "diagnostic_mode": True,
        "diagnostics_are_observer_only": True,
        "signals_generated": len(signal_rows),
        "signals_skipped_kill_switch": sum(r["outcome"] == "skipped_kill_switch" for r in signal_rows),
        "signals_with_executed_trade": sum(r["outcome"] == "executed_trade" for r in signal_rows),
        "skipped_signal_pnl_assigned": False,
        "actual_trades": len(enriched_trades),
        "actual_net_pnl": result.net_pnl,
        "equity_curve_max_drawdown": result.max_drawdown_pct,
        "kill_events": [[t.isoformat(), r, d] for t, r, d in result.kill_events],
        "diagnostic_files": ["diagnostic_capture/signals.csv", "diagnostic_capture/orders.csv", "diagnostic_capture/trades.csv", "diagnostic_capture/events.jsonl"],
    }
    (window_root / "summary.json").write_text(json.dumps(result_summary, indent=2), encoding="utf-8")
    return result_summary, component_rows, signal_groups


def summarize_signals(rows: list[dict[str, object]], window: str) -> list[dict[str, object]]:
    groups: dict[tuple[object, ...], list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        confidence = float(row["confidence"])
        band = "<0.60" if confidence < 0.60 else "0.60–<0.70" if confidence < 0.70 else "0.70–<0.80" if confidence < 0.80 else "0.80+"
        key = (row["strategy"], row["side"], row["regime"], band)
        groups[key].append(row)
    output = []
    for (strategy, side, regime, band), members in sorted(groups.items()):
        output.append({
            "window": window, "strategy": strategy, "side": side,
            "decision_regime": regime, "confidence_band": band,
            "signals_generated": len(members),
            "executed_trades": sum(r["outcome"] == "executed_trade" for r in members),
            "kill_switch_skips": sum(r["outcome"] == "skipped_kill_switch" for r in members),
            "other_or_pending_outcomes": sum(r["outcome"] not in {"executed_trade", "skipped_kill_switch"} for r in members),
            "confidence_mean": sum(float(r["confidence"]) for r in members) / len(members),
            "pnl_assigned_to_skips": False,
        })
    return output


def fmt_money(value) -> str:
    if value is None:
        return "—"
    return f"${float(value):,.2f}"


def load_component_rows(path: Path) -> list[dict[str, object]]:
    output = []
    with path.open(encoding="utf-8", newline="") as handle:
        for raw in csv.DictReader(handle):
            if raw.get("group_type") != "strategy_component":
                continue
            row: dict[str, object] = {}
            for key, value in raw.items():
                if key in {"window", "group_type", "strategy"}:
                    row[key] = value
                elif not value:
                    row[key] = None
                else:
                    row[key] = float(value)
            if row.get("trades") is not None:
                row["trades"] = int(row["trades"])
            output.append(row)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "reports" / "trade_economics_signal_audit")
    parser.add_argument("--report-only", action="store_true", help="rebuild report.md from the existing generated CSVs without rerunning windows")
    args = parser.parse_args()
    source = args.csv.resolve()
    dataset_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=True)
    if args.report_only:
        runs = []
        for name, _, _ in WINDOWS:
            directory = root / name
            summary = json.loads((directory / "summary.json").read_text(encoding="utf-8"))
            signal_groups = list(csv.DictReader((directory / "signal_groups.csv").open(encoding="utf-8", newline="")))
            runs.append((summary, load_component_rows(directory / "economics_groups.csv"), signal_groups))
    else:
        candles = load_ohlcv_csv(source)
        runs = [run_window(name, start, end, candles, root, dataset_hash) for name, start, end in WINDOWS]

    lines = [
        "# Trade economics and signal-quality audit", "",
        f"Dataset SHA-256: `{dataset_hash}`. Frozen configuration and safety-on primary results are unchanged; diagnostic reruns were asserted equal to their preserved baseline trade ledger, equity curve, and kill events.", "",
        "This audit assigns realized P&L only to executed trades. Signals skipped by a safety lock remain signals-only records; no counterfactual fill, cost, position, or P&L is constructed. 2025–August 2026 remains a previously examined diagnostic window, not an untouched holdout.", "",
        "## Economics by strategy component", "",
        "Gross is shown both before modeled slippage and after slipped execution. Funding is shown as paid / received / net paid; funding received is not hidden inside a negative net cost.", "",
    ]
    for summary, components, signal_groups in runs:
        lines += [f"### {summary['window']}", "", "| Component | n | Raw gross | Execution gross | Fees | Slippage | Funding paid / received / net paid | Net P&L | Win rate (95% Wilson CI) | Avg win / loss | PF | Expectancy (95% normal CI) | Turnover |", "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
        for row in components:
            ci = f"{row['win_rate']:.1%} ({row['win_rate_95ci_low']:.1%}–{row['win_rate_95ci_high']:.1%})"
            exp_ci = "—" if row["expectancy_normal_95ci_low"] is None else f"{fmt_money(row['expectancy_net'])} ({fmt_money(row['expectancy_normal_95ci_low'])}–{fmt_money(row['expectancy_normal_95ci_high'])})"
            pf = "—" if row["profit_factor_net"] is None else f"{row['profit_factor_net']:.3f}"
            component_label = {"trend_following": "TrendFollower", "breakout": "Breakout", "momentum": "Momentum", "mean_reversion": "MeanReversion"}.get(str(row["strategy"]), row["strategy"])
            funding = f"{fmt_money(row['funding_paid'])} / {fmt_money(row['funding_received'])} / {fmt_money(row['funding_net_paid'])}"
            lines.append(f"| {component_label} | {row['trades']} | {fmt_money(row['gross_pnl_before_slippage'])} | {fmt_money(row['execution_gross_pnl_after_slippage'])} | {fmt_money(row['fees'])} | {fmt_money(row['slippage'])} | {funding} | {fmt_money(row['net_pnl'])} | {ci} | {fmt_money(row['average_win'])} / {fmt_money(row['average_loss'])} | {pf} | {exp_ci} | {fmt_money(row['turnover_notional'])} |")
        lines += ["", "| Component | Fees / turnover | Slippage / turnover | Fees / absolute raw gross | Slippage / absolute raw gross |", "|---|---:|---:|---:|---:|"]
        for row in components:
            component_label = {"trend_following": "TrendFollower", "breakout": "Breakout", "momentum": "Momentum", "mean_reversion": "MeanReversion"}.get(str(row["strategy"]), row["strategy"])
            lines.append(f"| {component_label} | {row['fees_pct_turnover']:.4%} | {row['slippage_pct_turnover']:.4%} | {row['fees_pct_gross_absolute']:.2%} | {row['slippage_pct_gross_absolute']:.2%} |")
        lines += ["", f"Signals generated: {summary['signals_generated']}; executed trades: {summary['actual_trades']}; kill-switch skips: {summary['signals_skipped_kill_switch']}. Skipped signals have no assigned P&L.", ""]

    lines += [
        "## Interpretation and limitations", "",
        "Fees and slippage are reported both as a share of turnover and of absolute raw gross P&L in `economics_groups.csv`. Comparing costs with absolute gross P&L is descriptive (and can exceed 100%); it is not a return denominator. For each group, the reconciliation is raw gross − slippage − fees − signed funding paid = net P&L.", "",
        "`trades_enriched.csv` adds only the causal signal-time regime/features, confidence, duration, and original signal rationale to the actual executed ledger. `signals_causal.csv` includes signals emitted while safety-locked and their outcome class, but intentionally no P&L for skipped signals. Regime is the snapshot available at signal close; confidence bands are descriptive buckets, not tuned thresholds.", "",
        "Win-rate uncertainty is shown with a 95% Wilson interval. Expectancy intervals use the sample standard error and a normal approximation; they are descriptive and can be unreliable for small or skewed samples. Profit factor is especially unstable for tiny groups. Do not interpret small component or condition slices as evidence of an edge.", "",
        "The diagnostic reruns retained safety mode. They do not continue generating signals while a position is open (the strategy is only queried when flat and without a pending order), and they do not simulate any skipped signal. Thus this is signal quality under the existing event loop, not an uncensored counterfactual strategy test.", "",
        "Each window directory includes enriched actual trades, causal signals, grouped economics, grouped signal counts, a result summary, and raw observer diagnostics. The existing safety/risk audit directory was not overwritten.", "",
        "Reproduce with: `python tools/audit_trade_economics.py tests/tests/combined.csv --output reports/trade_economics_signal_audit`. Rebuild only the Markdown from completed artifacts with `--report-only`.",
    ]
    (root / "report.md").write_text("\n".join(lines), encoding="utf-8")
    (root / "manifest.json").write_text(json.dumps({
        "dataset": str(source), "dataset_sha256": dataset_hash,
        "safety_risk_baseline_preserved": True,
        "windows": [row[0] for row in runs],
        "all_diagnostic_runs_asserted_equal_to_baseline": True,
        "skipped_signal_pnl_assigned": False,
    }, indent=2), encoding="utf-8")
    print(root / "report.md")
    for summary, _, _ in runs:
        print(f"{summary['window']}: signals={summary['signals_generated']} trades={summary['actual_trades']} skipped={summary['signals_skipped_kill_switch']} net={summary['actual_net_pnl']:.2f}")


if __name__ == "__main__":
    main()
