"""Run frozen, independent, safety-on chronological portfolio windows.

Earlier candles seed indicators only. Each scored segment gets a fresh engine,
equity balance, position state, and kill switch. Segment results are never
concatenated into a synthetic full-period curve.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from crypto_bot.backtest import BacktestConfig, IntrabarBacktester
from crypto_bot.controls import KillSwitch, KillSwitchConfig, KillSwitchMode
from crypto_bot.diagnostics import LogLevel
from crypto_bot.indicators import atr
from crypto_bot.io import load_ohlcv_csv
from crypto_bot.reporting import assert_trade_reconciliation, normalize_trade, trade_metrics
from crypto_bot.risk import PositionSizer, RiskConfig
from crypto_bot.strategies import StrategyEnsemble


UTC = timezone.utc
INITIAL_EQUITY = 10_000.0
RISK_PCT = 0.005
LEVERAGE = 2.0
FEE_RATE = 0.0005
SLIPPAGE_BPS = 5.0
FUNDING_RATE_8H = 0.0001
PARTICIPATION = 0.05
WINDOWS = (
    ("2020_2022", datetime(2020, 1, 1, tzinfo=UTC), datetime(2023, 1, 1, tzinfo=UTC)),
    ("2023_2024", datetime(2023, 1, 1, tzinfo=UTC), datetime(2025, 1, 1, tzinfo=UTC)),
    # This historical period has already been examined in the full-run report.
    ("2025_2026-08-19_previously_examined", datetime(2025, 1, 1, tzinfo=UTC), datetime(2026, 8, 20, tzinfo=UTC)),
)


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        if not rows:
            handle.write("\n")
            return
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def underwater_metrics(points) -> dict[str, int | float]:
    underwater_bars = longest_bars = current_bars = 0
    for point in points:
        if point.drawdown_pct > 0:
            underwater_bars += 1
            current_bars += 1
            longest_bars = max(longest_bars, current_bars)
        else:
            current_bars = 0
    return {
        "underwater_bars": underwater_bars,
        "underwater_hours": underwater_bars * 0.25,
        "underwater_pct_of_scored_bars": underwater_bars / len(points) if points else 0.0,
        "longest_underwater_bars": longest_bars,
        "longest_underwater_hours": longest_bars * 0.25,
    }


def portfolio_metrics(result, scored_candles) -> dict[str, object]:
    rows = [normalize_trade(trade, SLIPPAGE_BPS / 10_000) for trade in result.trades]
    assert_trade_reconciliation(rows)
    trade_net = sum(float(row["net_pnl"]) for row in rows)
    cost_reconciled_net = sum(
        float(row["raw_gross_pnl"]) - float(row["slippage_cost"]) - float(row["fees"])
        - float(row["funding_paid"]) for row in rows
    )
    if abs(trade_net - result.net_pnl) > 1e-7:
        raise AssertionError(f"trade ledger net {trade_net} != window net {result.net_pnl}")
    if abs(cost_reconciled_net - result.net_pnl) > 1e-6:
        raise AssertionError(f"window cost identity {cost_reconciled_net} != window net {result.net_pnl}")
    if not result.equity_curve or abs(result.equity_curve[-1].equity - result.final_equity) > 1e-7:
        raise AssertionError("window equity curve endpoint does not match final equity")
    curve_max_dd = max((p.drawdown_pct for p in result.equity_curve), default=0.0)
    if abs(curve_max_dd - result.max_drawdown_pct) > 1e-12:
        raise AssertionError("window drawdown does not reconcile to equity curve")

    index_by_time = {c.timestamp: i for i, c in enumerate(scored_candles)}
    exposure_bars = 0
    entry_notional = exit_notional = 0.0
    for trade in result.trades:
        start = index_by_time[trade.entry_time]
        end = index_by_time[trade.exit_time]
        if end < start:
            raise AssertionError(f"trade {trade.trade_id} exits before its entry")
        # OHLC cannot reveal intrabar duration; count every scored candle in
        # which the position was open, including its entry and exit candles.
        exposure_bars += end - start + 1
        entry_notional += trade.quantity * trade.entry_price
        exit_notional += trade.quantity * trade.exit_price
    exposure_bars = min(exposure_bars, len(scored_candles))
    metrics = trade_metrics(rows)
    if isinstance(metrics["profit_factor"], float) and math.isinf(metrics["profit_factor"]):
        metrics["profit_factor"] = "Infinity"
    metrics.update({
        "return_pct": result.return_pct,
        "final_equity": result.final_equity,
        "max_drawdown_pct": result.max_drawdown_pct,
        "scored_candles": len(scored_candles),
        "exposure_bars": exposure_bars,
        "exposure_hours": exposure_bars * 0.25,
        "exposure_pct_of_scored_bars": exposure_bars / len(scored_candles) if scored_candles else 0.0,
        "entry_notional": entry_notional,
        "exit_notional": exit_notional,
        "turnover_notional": entry_notional + exit_notional,
        "fees_pct_of_turnover": (float(metrics["fees"]) / (entry_notional + exit_notional)) if entry_notional + exit_notional else 0.0,
        "slippage_pct_of_turnover": (float(metrics["slippage"]) / (entry_notional + exit_notional)) if entry_notional + exit_notional else 0.0,
        "fees_pct_of_initial_equity": float(metrics["fees"]) / result.initial_equity,
        "slippage_pct_of_initial_equity": float(metrics["slippage"]) / result.initial_equity,
        "turnover_x_initial_equity": (entry_notional + exit_notional) / result.initial_equity,
        **underwater_metrics(result.equity_curve),
        "kill_switch_activations": len(result.kill_events),
        "kill_events": [[ts.isoformat(), reason, detail] for ts, reason, detail in result.kill_events],
        "signals_generated": result.diagnostics["signals_generated"],
        "entries_accepted": result.diagnostics["entries_accepted"],
        "entries_skipped_kill_switch": result.diagnostics["entries_skipped_kill_switch"],
        "ledger_net_delta": trade_net - result.net_pnl,
        "cost_identity_delta": cost_reconciled_net - result.net_pnl,
        "equity_endpoint_delta": result.equity_curve[-1].equity - result.final_equity,
        "equity_curve_max_drawdown_delta": curve_max_dd - result.max_drawdown_pct,
    })
    return metrics, rows


def safety_audit_rows(result, scored_candles, warmup_candles, switch_config):
    """Replay every close-time safety observation and retain its exact inputs."""
    replay = KillSwitch(switch_config)
    history = list(warmup_candles)
    event_rows = []
    for candle, point in zip(scored_candles, result.equity_curve):
        history.append(candle)
        current_atr = atr(history[-15:], 14)
        atr_pct = current_atr / candle.close if current_atr is not None else None
        next_day = replay.day != candle.timestamp.date()
        equity = point.equity
        if equity <= 0:
            peak_before = replay.peak_equity
        else:
            peak_before = replay.peak_equity
            replay.observe(candle.timestamp, equity, atr_pct)
        if equity <= 0:
            replay.observe(candle.timestamp, equity, atr_pct)
        peak = replay.peak_equity
        baseline = replay.day_start_equity
        event = replay.events[-1] if replay.events and replay.events[-1][0] == candle.timestamp else None
        event_reason = event[1].value if event else ""
        event_detail = event[2] if event else ""
        event_order = []
        if event_reason == "daily_loss_limit":
            event_order.append("daily_loss_check")
        if event_reason == "max_drawdown_limit":
            event_order.append("max_drawdown_check")
        if event_reason == "unexpected_volatility":
            event_order.append("atr_check")
        event_rows.append({
            "candle_open_timestamp": candle.timestamp.isoformat(),
            "decision_at_close_timestamp": (candle.timestamp + timedelta(minutes=15)).isoformat(),
            "close": candle.close,
            "equity_at_close": equity,
            "new_utc_day": next_day,
            "daily_baseline_equity": baseline,
            "daily_loss_limit_pct": switch_config.max_daily_loss_pct,
            "daily_loss_threshold_equity": baseline * (1 - switch_config.max_daily_loss_pct) if baseline is not None else None,
            "daily_loss_from_baseline_pct": (baseline - equity) / baseline if baseline else None,
            "daily_loss_triggered_this_candle": event_reason == "daily_loss_limit",
            "peak_before_observation": peak_before,
            "peak_equity_after_observation": peak,
            "drawdown_pct": 1 - equity / peak if peak else 0,
            "max_drawdown_limit_pct": switch_config.max_drawdown_pct,
            "max_drawdown_threshold_equity": peak * (1 - switch_config.max_drawdown_pct) if peak else None,
            "max_drawdown_triggered_this_candle": event_reason == "max_drawdown_limit",
            "atr14": current_atr,
            "atr_pct": atr_pct,
            "max_atr_pct": switch_config.max_atr_pct,
            "atr_triggered_this_candle": event_reason == "unexpected_volatility",
            "check_order": "daily loss; maximum drawdown; ATR" if not next_day else "reset daily baseline/lock; daily loss; maximum drawdown; ATR",
            "event_order": ";".join(event_order),
            "event_reason": event_reason,
            "event_detail": event_detail,
            "daily_lock_after": replay.daily_locked,
            "permanent_reason_after": replay.permanent_reason.value if replay.permanent_reason else "",
        })
    actual = [(ts, getattr(reason, "value", reason), detail) for ts, reason, detail in result.kill_events]
    observed = [(ts, reason.value, detail) for ts, reason, detail in replay.events]
    if actual != observed:
        raise AssertionError(f"safety replay differs from backtest: {actual!r} != {observed!r}")
    return event_rows, replay


def risk_trade_rows(result, scored_candles, sizer, initial_equity):
    """Re-size each accepted trade from entry equity and compare stop budget to realization."""
    index_by_time = {c.timestamp: i for i, c in enumerate(scored_candles)}
    equity = initial_equity
    rows = []
    for trade in result.trades:
        sizing = sizer.size(equity=equity, entry_price=trade.entry_price,
                            stop_price=trade.stop_price, side=trade.side)
        unconstrained_quantity = sizing.risk_budget / sizing.risk_per_unit
        notional_cap_quantity = equity * sizer.config.max_position_notional_pct / trade.entry_price
        margin_cap_quantity = equity * sizer.config.max_margin_fraction * sizer.config.leverage / trade.entry_price
        if abs(sizing.quantity - trade.quantity) > max(1e-10, abs(trade.quantity) * 1e-9):
            raise AssertionError(f"trade {trade.trade_id} sizing does not reproduce actual quantity")
        entry_index = index_by_time[trade.entry_time]
        candle = scored_candles[entry_index]
        raw_entry_open = candle.open
        pre_entry_signal_close = scored_candles[entry_index - 1].close if entry_index else None
        stop_was_hit = trade.exit_reason == "stop_loss"
        realized_loss = max(0.0, -trade.net_pnl)
        rows.append({
            "trade_id": trade.trade_id,
            "signal_id": trade.signal_id,
            "strategy": trade.strategy,
            "side": trade.side.value,
            "entry_time": trade.entry_time.isoformat(),
            "exit_time": trade.exit_time.isoformat(),
            "entry_equity": equity,
            "risk_per_trade_pct": sizer.config.risk_per_trade_pct,
            "risk_budget": sizing.risk_budget,
            "entry_price": trade.entry_price,
            "stop_price": trade.stop_price,
            "risk_per_unit_including_reserved_costs": sizing.risk_per_unit,
            "expected_loss_at_stop": sizing.expected_loss_at_stop,
            "expected_loss_pct_of_entry_equity": sizing.expected_loss_at_stop / equity,
            "sized_quantity": sizing.quantity,
            "actual_quantity": trade.quantity,
            "unconstrained_risk_quantity": unconstrained_quantity,
            "was_notional_capped": notional_cap_quantity < unconstrained_quantity and notional_cap_quantity <= margin_cap_quantity,
            "was_margin_capped": sizing.was_margin_capped,
            "notional_cap_quantity": notional_cap_quantity,
            "margin_cap_quantity": margin_cap_quantity,
            "entry_open_raw": raw_entry_open,
            "prior_signal_candle_close": pre_entry_signal_close,
            "entry_gap_from_prior_close_pct": (raw_entry_open / pre_entry_signal_close - 1) if pre_entry_signal_close else None,
            "exit_reason": trade.exit_reason,
            "stop_was_hit": stop_was_hit,
            "realized_loss": realized_loss,
            "realized_loss_pct_of_entry_equity": realized_loss / equity,
            "realized_loss_to_stop_budget_ratio": realized_loss / sizing.expected_loss_at_stop if sizing.expected_loss_at_stop else None,
            "gross_pnl": trade.gross_pnl,
            "entry_fee": trade.entry_fee,
            "exit_fee": trade.exit_fee,
            "funding_paid": trade.funding_paid,
            "net_pnl": trade.net_pnl,
            "equity_after_close": equity + trade.net_pnl,
        })
        equity += trade.net_pnl
    return rows


def run_window(name, start, end, candles, output: Path, dataset_hash: str) -> dict[str, object]:
    warmup = [c for c in candles if c.timestamp < start]
    scored = [c for c in candles if start <= c.timestamp < end]
    if len(scored) < 2:
        raise ValueError(f"window {name} has fewer than two scored candles")
    if any(c.timestamp >= start for c in warmup) or any(c.timestamp < start or c.timestamp >= end for c in scored):
        raise AssertionError("window split leaked candles across its boundary")

    risk = RiskConfig(
        risk_per_trade_pct=RISK_PCT,
        max_position_notional_pct=1.0,
        max_margin_fraction=0.8,
        leverage=LEVERAGE,
        estimated_round_trip_cost_pct=2 * (FEE_RATE + SLIPPAGE_BPS / 10_000),
    )
    config = BacktestConfig(
        initial_equity=INITIAL_EQUITY,
        fee_rate=FEE_RATE,
        slippage_bps=SLIPPAGE_BPS,
        funding_rate_per_8h=FUNDING_RATE_8H,
        candle_minutes=15,
        max_participation_rate=PARTICIPATION,
        force_close_at_end=True,
        logging_level=LogLevel.ERROR,
        diagnostic_mode=False,
        kill_switch_mode=KillSwitchMode.SAFETY,
    )
    engine = IntrabarBacktester(config, PositionSizer(risk), kill_switch=KillSwitch(KillSwitchConfig()))
    result = engine.run(scored, ensemble=StrategyEnsemble(), warmup_candles=warmup)
    metrics, trade_rows = portfolio_metrics(result, scored)
    risk_rows = risk_trade_rows(result, scored, engine.sizer, INITIAL_EQUITY)
    risk_by_id = {row["trade_id"]: row for row in risk_rows}
    for row in trade_rows:
        row.update(risk_by_id[row["trade_id"]])
    safety_rows, replayed_switch = safety_audit_rows(result, scored, warmup, engine.kill_switch.config)

    directory = output / name
    directory.mkdir(parents=True, exist_ok=True)
    equity_rows = [{
        "candle_open_timestamp": point.timestamp.isoformat(),
        "decision_at_close_timestamp": (point.timestamp + timedelta(minutes=15)).isoformat(),
        "equity": point.equity,
        "drawdown_pct": point.drawdown_pct,
    } for point in result.equity_curve]
    write_csv(directory / "trades.csv", trade_rows)
    write_csv(directory / "equity_drawdown.csv", equity_rows)
    write_csv(directory / "safety_checks.csv", safety_rows)
    write_csv(directory / "risk_ledger.csv", risk_rows)

    event_details = []
    for ts, reason, detail in result.kill_events:
        stamp = ts.isoformat()
        snapshot = next(row for row in safety_rows if row["candle_open_timestamp"] == stamp)
        event_details.append({"reason": getattr(reason, "value", reason), "detail": detail, **snapshot})
    metrics["kill_event_snapshots"] = event_details
    metrics["daily_lock_observations"] = sum(bool(row["daily_lock_after"]) for row in safety_rows)
    metrics["daily_lock_resets_at_utc_day_boundaries"] = sum(
        bool(row["new_utc_day"] and previous["daily_lock_after"])
        for previous, row in zip(safety_rows, safety_rows[1:])
    )
    metrics["sizing_audit"] = {
        "trade_count_checked": len(risk_rows),
        "quantity_reproductions_passed": len(risk_rows),
        "margin_capped_trades": sum(bool(row["was_margin_capped"]) for row in risk_rows),
        "notional_capped_trades": sum(bool(row["was_notional_capped"]) for row in risk_rows),
        "mean_intended_stop_risk_pct": sum(row["expected_loss_pct_of_entry_equity"] for row in risk_rows) / len(risk_rows) if risk_rows else 0.0,
        "min_intended_stop_risk_pct": min((row["expected_loss_pct_of_entry_equity"] for row in risk_rows), default=0.0),
        "max_intended_stop_risk_pct": max((row["expected_loss_pct_of_entry_equity"] for row in risk_rows), default=0.0),
        "trades_exceeding_stop_budget": sum(
            row["realized_loss_to_stop_budget_ratio"] is not None and row["realized_loss_to_stop_budget_ratio"] > 1.0
            for row in risk_rows
        ),
        "stop_loss_trades": sum(bool(row["stop_was_hit"]) for row in risk_rows),
    }

    metrics.update({
        "window": name,
        "scored_start_inclusive": start.isoformat(),
        "scored_end_exclusive": end.isoformat(),
        "first_scored_candle": scored[0].timestamp.isoformat(),
        "last_scored_candle": scored[-1].timestamp.isoformat(),
        "warmup_candles": len(warmup),
        "warmup_start": warmup[0].timestamp.isoformat() if warmup else None,
        "warmup_end": warmup[-1].timestamp.isoformat() if warmup else None,
        "dataset_sha256": dataset_hash,
        "safety_mode": config.kill_switch_mode.value,
        "configuration": {
            "initial_equity": INITIAL_EQUITY,
            "risk_per_trade_pct": RISK_PCT,
            "leverage": LEVERAGE,
            "max_position_notional_pct": risk.max_position_notional_pct,
            "max_margin_fraction": risk.max_margin_fraction,
            "fee_rate_per_side": FEE_RATE,
            "slippage_bps_per_side": SLIPPAGE_BPS,
            "funding_rate_per_8h": FUNDING_RATE_8H,
            "max_participation_rate": PARTICIPATION,
            "sizing_round_trip_cost_reserve_pct": risk.estimated_round_trip_cost_pct,
            "force_close_at_end": True,
            "kill_switch_max_drawdown_pct": KillSwitchConfig().max_drawdown_pct,
            "kill_switch_max_daily_loss_pct": KillSwitchConfig().max_daily_loss_pct,
            "kill_switch_max_atr_pct": KillSwitchConfig().max_atr_pct,
        },
        "boundary_policy": "prior candles seed indicators and ATR only; no pre-window signals, fills, account/equity changes, diagnostics, or kill-switch observation; fresh backtester/sizer/kill-switch for each scored window",
        "result_policy": "independent window only; do not append or compound into primary equity curve",
    })
    (directory / "summary.json").write_text(json.dumps(metrics, indent=2, allow_nan=False), encoding="utf-8")
    return metrics


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "reports" / "chronological_windows")
    args = parser.parse_args()
    source = args.csv.resolve()
    dataset_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    candles = load_ohlcv_csv(source)
    if any(a.timestamp >= b.timestamp for a, b in zip(candles, candles[1:])):
        raise ValueError("dataset must be strictly chronological")
    rows = [run_window(name, start, end, candles, args.output.resolve(), dataset_hash) for name, start, end in WINDOWS]
    (args.output.resolve() / "manifest.json").write_text(json.dumps({
        "dataset": str(source),
        "dataset_sha256": dataset_hash,
        "dataset_candles": len(candles),
        "dataset_start": candles[0].timestamp.isoformat(),
        "dataset_end": candles[-1].timestamp.isoformat(),
        "python": sys.version,
        "windows": [{"name": row["window"], "summary_file": f"{row['window']}/summary.json"} for row in rows],
        "no_combined_or_stitched_result": True,
        "2025_2026_status": "previously examined historical diagnostic window; not an untouched holdout",
    }, indent=2), encoding="utf-8")

    # Summary rows are separate scenarios, not a combined return series.
    lines = [
        "# Chronological safety-on window diagnostics",
        "",
        f"Dataset SHA-256: `{dataset_hash}`. Each row below is an independent account run; values are not compounded, stitched, or combined.",
        "",
        "The 2025–2026-08-19 window is a previously examined historical diagnostic period, not an untouched holdout. Warm-up candles are strictly earlier than each scored start and only seed indicators. Every window starts with fresh $10,000 equity and a fresh safety kill switch.",
        "",
        "| Window | Trades | Raw gross | Net P&L | Fees | Slippage | Funding | Expectancy | PF | Return | Max DD | Exposure | Turnover / initial | Underwater | Kill trips | Skipped signals |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        pf = row["profit_factor"]
        pf_text = "n/a" if pf is None else ("∞" if str(pf).lower() in ("infinity", "inf") else f"{float(pf):.3f}")
        exp = row["expectancy"]
        exp_text = "n/a" if exp is None else f"${float(exp):,.2f}"
        lines.append(
            f"| {row['window']} | {row['trades']} | ${row['raw_gross_pnl']:,.2f} | ${row['net_pnl']:,.2f} | "
            f"${row['fees']:,.2f} | ${row['slippage']:,.2f} | ${row['funding_paid']:,.2f} | "
            f"{exp_text} | {pf_text} | {row['return_pct']:.2%} | {row['max_drawdown_pct']:.2%} | "
            f"{row['exposure_pct_of_scored_bars']:.2%} ({row['exposure_hours']:.1f}h) | "
            f"{row['turnover_x_initial_equity']:.2f}× | {row['underwater_hours']:.1f}h "
            f"({row['underwater_pct_of_scored_bars']:.2%}; max spell {row['longest_underwater_hours']:.1f}h) | "
            f"{row['kill_switch_activations']} | {row['entries_skipped_kill_switch']} |"
        )
    lines += [
        "",
        "## Window interpretation",
        "",
        *[
            f"- **{row['window']}**: {row['trades']} trades, {row['kill_switch_activations']} kill-switch activation(s), {row['entries_skipped_kill_switch']} skipped signals. "
            + ("The switch activated and censored subsequent in-window opportunities; performance after activation is not assessed as taken trades." if row['kill_switch_activations'] else "No kill-switch activation was recorded within this scored window." )
            for row in rows
        ],
        "",
        "Each window directory contains its own `summary.json`, `trades.csv` (including post-close equity and sizing comparison), `risk_ledger.csv`, `safety_checks.csv` (every scored candle), and `equity_drawdown.csv`. Safety checks are replayed candle-by-candle and asserted equal to the engine's activation events. Trade net P&L reconciles to final equity, gross/cost/funding identity reconciles, and the equity-curve endpoint and maximum drawdown match. No synthetic full-period result is generated.",
        "",
    ]
    (args.output.resolve() / "diagnostic_report.md").write_text("\n".join(lines), encoding="utf-8")
    print(args.output.resolve() / "diagnostic_report.md")
    for row in rows:
        print(f"{row['window']}: trades={row['trades']} net={row['net_pnl']:.8f} max_dd={row['max_drawdown_pct']:.8%} kills={row['kill_switch_activations']} skipped={row['entries_skipped_kill_switch']}")


if __name__ == "__main__":
    main()
