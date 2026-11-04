"""Research-only causal signal outcomes and predeclared cost sensitivity grid."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from crypto_bot.backtest import BacktestConfig, IntrabarBacktester
from crypto_bot.controls import KillSwitch, KillSwitchConfig, KillSwitchMode
from crypto_bot.diagnostics import LogLevel
from crypto_bot.io import load_ohlcv_csv
from crypto_bot.reporting import assert_trade_reconciliation, normalize_trade
from crypto_bot.risk import PositionSizer, RiskConfig
from crypto_bot.strategies import StrategyEnsemble
from audit_trade_economics import compare_to_baseline
from run_chronological_windows import WINDOWS, INITIAL_EQUITY, RISK_PCT, LEVERAGE, FEE_RATE, SLIPPAGE_BPS, FUNDING_RATE_8H, PARTICIPATION


HORIZONS = (1, 4, 16, 64)
BOOTSTRAP_REPLICATES = 200
BOOTSTRAP_BLOCK_CANDLES = 64
CONFIDENCE_BANDS = ("<0.60", "0.60–<0.70", "0.70–<0.80", "0.80+")
COST_GRID = (
    {"scenario": "baseline_actual", "fee_bps_per_side": 5.0, "slippage_bps_per_side": 5.0, "status": "actual baseline"},
    {"scenario": "lower_cost_2p5_2p5", "fee_bps_per_side": 2.5, "slippage_bps_per_side": 2.5, "status": "hypothetical"},
    {"scenario": "lower_cost_1_2", "fee_bps_per_side": 1.0, "slippage_bps_per_side": 2.0, "status": "hypothetical"},
    {"scenario": "lower_cost_0p5_1", "fee_bps_per_side": 0.5, "slippage_bps_per_side": 1.0, "status": "hypothetical"},
)


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def confidence_band(value: float) -> str:
    return "<0.60" if value < 0.60 else "0.60–<0.70" if value < 0.70 else "0.70–<0.80" if value < 0.80 else "0.80+"


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    location = (len(ordered) - 1) * fraction
    lo, hi = math.floor(location), math.ceil(location)
    if lo == hi:
        return ordered[lo]
    return ordered[lo] * (hi - location) + ordered[hi] * (location - lo)


def causal_outcomes(signals: list[dict[str, str]], candles, window_name: str, start: datetime, end: datetime):
    index_by_time = {c.timestamp.isoformat(): i for i, c in enumerate(candles)}
    rows = []
    for signal in signals:
        timestamp = signal["timestamp"]
        if timestamp not in index_by_time:
            raise AssertionError(f"recorded signal candle not found in source: {timestamp}")
        index = index_by_time[timestamp]
        source = candles[index]
        side_sign = 1 if signal["side"] == "long" else -1
        confidence = float(signal["confidence"])
        base = {
            "window": window_name,
            "signal_id": int(signal["signal_id"]),
            "decision_candle_open_timestamp": timestamp,
            "decision_at_close_timestamp": (source.timestamp + timedelta(minutes=15)).isoformat(),
            "strategy": signal["strategy"], "side": signal["side"],
            "regime": signal["regime"], "confidence": confidence,
            "confidence_band": confidence_band(confidence),
            "decision_price_close": source.close,
            "decision_features": signal["indicator_values"],
            "rationale": signal["rationale"],
            "outcome_type": "forward market measurement; not executable trade P&L",
        }
        for horizon in HORIZONS:
            endpoint = index + horizon
            if endpoint >= len(candles) or candles[endpoint].timestamp >= end:
                continue
            future = candles[index + 1:endpoint + 1]
            if len(future) != horizon:
                continue
            forward_return = side_sign * (candles[endpoint].close / source.close - 1)
            favorable = max(
                side_sign * ((bar.high if side_sign > 0 else bar.low) / source.close - 1)
                for bar in future
            )
            adverse = min(
                side_sign * ((bar.low if side_sign > 0 else bar.high) / source.close - 1)
                for bar in future
            )
            rows.append({
                **base,
                "horizon_candles": horizon,
                "horizon_minutes": horizon * 15,
                "outcome_end_candle_open_timestamp": candles[endpoint].timestamp.isoformat(),
                "forward_direction_adjusted_return_pct": forward_return * 100,
                "maximum_favorable_excursion_pct": favorable * 100,
                "maximum_adverse_excursion_pct": adverse * 100,
            })
    return rows


def bootstrap_interval(block_values: list[list[float]], seed: int):
    rng = random.Random(seed)
    means, medians = [], []
    n_blocks = len(block_values)
    if not n_blocks:
        return None, None, None, None, 0
    for _ in range(BOOTSTRAP_REPLICATES):
        sample: list[float] = []
        total = 0.0
        count = 0
        for _block in range(n_blocks):
            chosen = rng.randrange(n_blocks)
            values = block_values[chosen]
            if values:
                sample.extend(values)
                total += sum(values)
                count += len(values)
        if count:
            means.append(total / count)
            medians.append(statistics.median(sample))
    return (
        percentile(means, 0.025), percentile(means, 0.975),
        percentile(medians, 0.025), percentile(medians, 0.975), len(means),
    )


def summarize_forward(outcomes: list[dict[str, object]], candles_by_window: dict[str, list], index_by_window_time: dict[str, dict[str, int]]):
    dimensions = (
        ("strategy",), ("side",), ("regime",), ("confidence_band",),
        ("strategy", "side"), ("strategy", "regime"), ("strategy", "confidence_band"),
    )
    grouped: dict[tuple, list[dict[str, object]]] = defaultdict(list)
    for row in outcomes:
        for dims in dimensions:
            grouped[(row["window"], row["horizon_candles"], dims, tuple(row[d] for d in dims))].append(row)

    output = []
    for (window, horizon, dims, values), members in grouped.items():
        returns = [float(r["forward_direction_adjusted_return_pct"]) for r in members]
        favorable = [float(r["maximum_favorable_excursion_pct"]) for r in members]
        adverse = [float(r["maximum_adverse_excursion_pct"]) for r in members]
        mean = statistics.fmean(returns)
        sd = statistics.stdev(returns) if len(returns) > 1 else None
        start_index = 0
        signal_indices = [index_by_window_time[window][str(r["decision_candle_open_timestamp"])] for r in members]
        by_block: dict[int, list[float]] = defaultdict(list)
        for row, index in zip(members, signal_indices):
            by_block[(index - start_index) // BOOTSTRAP_BLOCK_CANDLES].append(float(row["forward_direction_adjusted_return_pct"]))
        # Resample only occupied chronological blocks for this group. Empty
        # time blocks contain no observations and do not inform its conditional
        # return distribution; keeping each occupied block intact preserves
        # local dependence from overlapping forward horizons.
        occupied_blocks = [by_block[key] for key in sorted(by_block)]
        seed = 912_771 + sum(ord(c) for c in str(window)) + int(horizon) * 17 + sum(ord(c) for c in str(values))
        mean_lo, mean_hi, median_lo, median_hi, valid = bootstrap_interval(occupied_blocks, seed)
        output.append({
            "window": window,
            "group_type": "x".join(dims),
            **dict(zip(dims, values)),
            "horizon_candles": horizon,
            "horizon_minutes": int(horizon) * 15,
            "signal_count": len(returns),
            "mean_direction_adjusted_return_pct": mean,
            "median_direction_adjusted_return_pct": statistics.median(returns),
            "std_dev_pct": sd,
            "interquartile_range_pct": (percentile(returns, .75) - percentile(returns, .25)) if len(returns) > 1 else None,
            "p10_pct": percentile(returns, .10),
            "p90_pct": percentile(returns, .90),
            "block_bootstrap_mean_95ci_low_pct": mean_lo,
            "block_bootstrap_mean_95ci_high_pct": mean_hi,
            "block_bootstrap_median_95ci_low_pct": median_lo,
            "block_bootstrap_median_95ci_high_pct": median_hi,
            "bootstrap_replicates": BOOTSTRAP_REPLICATES,
            "bootstrap_valid_replicates": valid,
            "bootstrap_block_size_candles": BOOTSTRAP_BLOCK_CANDLES,
            "mean_mfe_pct": statistics.fmean(favorable),
            "median_mfe_pct": statistics.median(favorable),
            "mean_mae_pct": statistics.fmean(adverse),
            "median_mae_pct": statistics.median(adverse),
            "outcome_type": "forward market measurement; not executable trade P&L",
        })
    return sorted(output, key=lambda r: (r["window"], r["group_type"], str(r.get("strategy", "")), str(r.get("side", "")), str(r.get("regime", "")), str(r.get("confidence_band", "")), r["horizon_candles"]))


def cost_sensitivity(candles, window_name: str, start: datetime, end: datetime, root: Path):
    warmup = [c for c in candles if c.timestamp < start]
    scored = [c for c in candles if start <= c.timestamp < end]
    results = []
    for scenario in COST_GRID:
        fee_rate = scenario["fee_bps_per_side"] / 10_000
        slip_bps = scenario["slippage_bps_per_side"]
        risk = RiskConfig(
            risk_per_trade_pct=RISK_PCT, max_position_notional_pct=1.0,
            max_margin_fraction=0.8, leverage=LEVERAGE,
            # Keep baseline sizing cost reserve frozen to isolate execution-cost sensitivity.
            estimated_round_trip_cost_pct=2 * (FEE_RATE + SLIPPAGE_BPS / 10_000),
        )
        config = BacktestConfig(
            initial_equity=INITIAL_EQUITY, fee_rate=fee_rate, slippage_bps=slip_bps,
            funding_rate_per_8h=FUNDING_RATE_8H, candle_minutes=15,
            max_participation_rate=PARTICIPATION, force_close_at_end=True,
            logging_level=LogLevel.ERROR, diagnostic_mode=False,
            kill_switch_mode=KillSwitchMode.SAFETY,
        )
        engine = IntrabarBacktester(config, PositionSizer(risk), kill_switch=KillSwitch(KillSwitchConfig()))
        result = engine.run(scored, ensemble=StrategyEnsemble(), warmup_candles=warmup)
        ledger = [normalize_trade(trade, slip_bps / 10_000) for trade in result.trades]
        assert_trade_reconciliation(ledger)
        ledger_net = sum(float(row["net_pnl"]) for row in ledger)
        if not math.isclose(ledger_net, result.net_pnl, rel_tol=0, abs_tol=1e-7):
            raise AssertionError("cost-scenario ledger does not reconcile to account P&L")
        entry_notional = sum(t.quantity * t.entry_price for t in result.trades)
        exit_notional = sum(t.quantity * t.exit_price for t in result.trades)
        raw_gross = sum(float(row["raw_gross_pnl"]) for row in ledger)
        fees = sum(float(row["fees"]) for row in ledger)
        slip = sum(float(row["slippage_cost"]) for row in ledger)
        funding_paid = sum(t.funding_paid for t in result.trades if t.funding_paid > 0)
        funding_received = -sum(t.funding_paid for t in result.trades if t.funding_paid < 0)
        actual = scenario["scenario"] == "baseline_actual"
        if actual:
            compare_to_baseline(result, ROOT / "reports" / "chronological_windows" / window_name)
        row = {
            "window": window_name,
            **scenario,
            "fees_assumption_bps_per_side": scenario["fee_bps_per_side"],
            "slippage_assumption_bps_per_side": scenario["slippage_bps_per_side"],
            "hypothetical": not actual,
            "actual_baseline_reconciled": actual,
            "trades": len(result.trades),
            "gross_pnl_before_slippage": raw_gross,
            "execution_gross_after_slippage": raw_gross - slip,
            "fees": fees,
            "slippage": slip,
            "funding_paid": funding_paid,
            "funding_received": funding_received,
            "net_funding_paid": funding_paid - funding_received,
            "net_pnl": result.net_pnl,
            "final_equity": result.final_equity,
            "return_pct": result.return_pct,
            "max_drawdown_pct": result.max_drawdown_pct,
            "entry_notional": entry_notional,
            "exit_notional": exit_notional,
            "turnover_notional": entry_notional + exit_notional,
            "fees_pct_turnover": fees / (entry_notional + exit_notional) if entry_notional + exit_notional else None,
            "slippage_pct_turnover": slip / (entry_notional + exit_notional) if entry_notional + exit_notional else None,
            "kill_switch_activations": len(result.kill_events),
            "kill_events": json.dumps([[t.isoformat(), r, d] for t, r, d in result.kill_events]),
            "signals_generated": result.diagnostics["signals_generated"],
            "entries_accepted": result.diagnostics["entries_accepted"],
            "entries_skipped_kill_switch": result.diagnostics["entries_skipped_kill_switch"],
            "partial_fills": result.partial_fills,
            "funding_rate_per_8h_unchanged": FUNDING_RATE_8H,
            "sizing_cost_reserve_frozen_at": risk.estimated_round_trip_cost_pct,
            "max_participation_rate_unchanged": PARTICIPATION,
            "execution_model": "next open; prior completed candle volume; existing fills unchanged",
            "safety_mode": "safety",
        }
        results.append(row)
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "reports" / "signal_quality_cost_sensitivity_2026-10-15")
    args = parser.parse_args()
    source = args.csv.resolve()
    dataset_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    baseline_manifest = json.loads((ROOT / "reports" / "trade_economics_signal_audit" / "manifest.json").read_text(encoding="utf-8"))
    if baseline_manifest["dataset_sha256"] != dataset_hash:
        raise AssertionError("recorded causal signals were generated from a different dataset hash")
    candles = load_ohlcv_csv(source)
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=True)
    forward_rows: list[dict[str, object]] = []
    window_candles: dict[str, list] = {}
    window_index_by_time: dict[str, dict[str, int]] = {}
    cost_rows: list[dict[str, object]] = []
    source_signal_counts = {}
    for name, start, end in WINDOWS:
        signal_path = ROOT / "reports" / "trade_economics_signal_audit" / name / "signals_causal.csv"
        signals = list(csv.DictReader(signal_path.open(encoding="utf-8", newline="")))
        scored = [c for c in candles if start <= c.timestamp < end]
        if not scored:
            raise ValueError(f"empty scored window: {name}")
        window_candles[name] = scored
        window_index_by_time[name] = {c.timestamp.isoformat(): i for i, c in enumerate(scored)}
        source_signal_counts[name] = len(signals)
        forward_rows.extend(causal_outcomes(signals, candles, name, start, end))
        cost_rows.extend(cost_sensitivity(candles, name, start, end, root))

    grouped = summarize_forward(forward_rows, window_candles, window_index_by_time)
    write_csv(root / "forward_outcomes.csv", forward_rows)
    write_csv(root / "forward_summary.csv", grouped)
    write_csv(root / "cost_sensitivity.csv", cost_rows)
    manifest = {
        "dataset": str(source), "dataset_sha256": dataset_hash,
        "signal_source": "recorded causal signal events from reports/trade_economics_signal_audit",
        "frozen_baseline_configuration": {
            "initial_equity_per_independent_window": INITIAL_EQUITY,
            "risk_per_trade_pct": RISK_PCT, "leverage": LEVERAGE,
            "max_position_notional_pct": 1.0, "max_margin_fraction": 0.8,
            "fee_bps_per_side": 5.0, "slippage_bps_per_side": 5.0,
            "funding_rate_per_8h": FUNDING_RATE_8H,
            "max_participation_rate": PARTICIPATION,
            "max_daily_loss_pct": KillSwitchConfig().max_daily_loss_pct,
            "max_drawdown_pct": KillSwitchConfig().max_drawdown_pct,
            "max_atr_pct": KillSwitchConfig().max_atr_pct,
            "sizing_round_trip_cost_reserve_pct": 2 * (FEE_RATE + SLIPPAGE_BPS / 10_000),
        },
        "cost_grid_predeclared": list(COST_GRID),
        "cost_grid_policy": "only fee and slippage rates vary; funding, sizing reserve, fill assumptions, risk, leverage, and safety thresholds are frozen",
        "forward_horizons_candles": list(HORIZONS),
        "forward_return_anchor": "signal candle close; outcome closes at i+h; excursion path uses candles i+1 through i+h",
        "forward_returns_are_executable_pnl": False,
        "block_bootstrap": {"method": "resample occupied non-overlapping chronological time blocks for each group; blocks retain clusters from overlapping forward horizons", "block_candles": BOOTSTRAP_BLOCK_CANDLES, "replicates": BOOTSTRAP_REPLICATES, "seed_policy": "fixed deterministic seed by window/group/horizon"},
        "signals_by_window": source_signal_counts,
        "hypothetical_cost_scenarios": [s["scenario"] for s in COST_GRID if s["status"] == "hypothetical"],
        "baseline_scenario_asserted_equal_to_safety_risk_reports": True,
        "previous_2025_2026_window_status": "previously examined historical diagnostic window; not an untouched holdout",
    }
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    render_report(root, dataset_hash, grouped, cost_rows, forward_rows, source_signal_counts)
    print(root / "report.md")
    print(f"forward outcome rows={len(forward_rows)} grouped estimates={len(grouped)} cost runs={len(cost_rows)}")


def render_report(root: Path, dataset_hash: str, grouped, cost_rows, forward_rows, signal_counts):
    lines = [
        "# Signal-quality and cost-sensitivity research", "",
        f"Dataset SHA-256: `{dataset_hash}`. Source signals are recorded causal signal events. The frozen production configuration and all prior reports remain unchanged.", "",
        "## Causal forward-return diagnostics", "",
        "These are forward market outcomes measured from the signal candle close. Future prices are outcome labels only; they are not used in signal features or decisions, and the results are not executable portfolio P&L. MFE/MAE uses the future OHLC path through the stated horizon. Signal observations overlap; uncertainty uses a 200-replicate block bootstrap that resamples occupied 64-candle chronological blocks intact.", "",
        "Forward summaries are in `forward_summary.csv`; per-signal outcomes and the recorded decision features are in `forward_outcomes.csv`. Group dimensions include each of strategy, side, decision-time regime, and confidence band, plus strategy-by-side/regime/confidence slices. The CSV reports mean/median, standard deviation, IQR, p10/p90, block-bootstrap intervals, sample count, MFE, and MAE for 1/4/16/64-candle horizons.", "",
        "Signal counts used:", "",
    ]
    for window, count in signal_counts.items():
        lines.append(f"- {window}: {count:,} recorded signals")
    lines += ["", "Only signals with a fully observed forward horizon before that window's end are included for that horizon. Signals generated after safety locks remain descriptive signal observations, not trades or P&L. Bootstrap blocks retain local time clustering, but are approximate for irregularly spaced signals and cannot remove all selection or regime dependence.", "", "## Cost sensitivity", "", "The grid was declared before evaluating scenarios. Baseline is actual; all other rows are hypothetical. Only fees and slippage vary. Funding remains 0.0001 per 8 hours, the baseline 0.20% sizing cost reserve is frozen, and causal prior-volume fills, participation, risk sizing mechanics, leverage, safety thresholds, next-open timing, and force-close behavior remain fixed. Safety remains enabled in every run.", "", "| Window | Scenario | Fee / slip bps per side | Actual / hypothetical | Trades | Raw gross | Exec gross | Fees | Slippage | Net funding paid | Net P&L | Return | Max DD | Turnover | Kill activations | Skipped signals |", "|---|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for row in cost_rows:
        status = "Actual baseline" if not row["hypothetical"] else "Hypothetical"
        lines.append(
            f"| {row['window']} | {row['scenario']} | {row['fee_bps_per_side']:g} / {row['slippage_bps_per_side']:g} | {status} | {row['trades']} | ${row['gross_pnl_before_slippage']:,.2f} | ${row['execution_gross_after_slippage']:,.2f} | ${row['fees']:,.2f} | ${row['slippage']:,.2f} | ${row['net_funding_paid']:,.2f} | ${row['net_pnl']:,.2f} | {row['return_pct']:.2%} | {row['max_drawdown_pct']:.2%} | ${row['turnover_notional']:,.0f} | {row['kill_switch_activations']} | {row['entries_skipped_kill_switch']} |"
        )
    lines += [
        "", "The baseline row in each window was asserted against the preserved safety/risk trade ledger, equity curve, final equity, drawdown, and kill events. Lower-cost rows are independent hypothetical replays, not actual results and not combined with the safety-on baseline. Lower modeled costs can change equity, risk sizing in dollars, lock timing, and which signals become actual entries under the unchanged rules; the table reports those scenario outcomes as such.", "",
        "## Findings and limitations", "",
        "A descriptive forward-return distribution is not evidence of a tradable edge: it omits portfolio overlap, sizing, fees/funding, signal competition, market impact, and safety-state execution. Use confidence intervals and sample counts; small component/regime slices are especially uncertain. Cost sensitivity is assumption analysis, not a claim that lower costs are achievable.", "",
        "The 2025–August 2026 window was previously examined and is not an untouched holdout. Before strategy selection, reserve a genuinely future chronological period not inspected during prior reports or tuning; freeze the complete system first, then evaluate once with realistic costs and safety controls. If a future period is unavailable, use an untouched chronological segment only after verifying it has not been examined; do not reuse the 2025–2026 diagnostic window as holdout.", "",
        "No thresholds were tuned, no component was removed or promoted, and no skipped-signal P&L was assigned. The cost grid retains safety-on behavior and does not enable live trading.", "",
        "Reproduce with `python tools/run_signal_quality_cost_sensitivity.py tests/tests/combined.csv --output reports/signal_quality_cost_sensitivity_2026-10-15`.",
    ]
    (root / "report.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
