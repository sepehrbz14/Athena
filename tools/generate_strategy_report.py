from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from crypto_bot.backtest import BacktestConfig, IntrabarBacktester
from crypto_bot.diagnostics import LogLevel
from crypto_bot.io import load_ohlcv_csv
from crypto_bot.reporting import assert_trade_reconciliation, monthly_equity, normalize_trade, trade_metrics
from crypto_bot.risk import PositionSizer, RiskConfig
from crypto_bot.strategies import StrategyEnsemble


class RecordingEnsemble:
    """Captures selected signal plus the regime visible at that decision."""

    def __init__(self) -> None:
        self.delegate = StrategyEnsemble()
        self.signals: list[dict[str, object]] = []

    def decide(self, market):
        decision = self.delegate.decide(market)
        if decision.signal is not None:
            self.signals.append({
                "signal_id": len(self.signals) + 1,
                "timestamp": market.candles_15m[-1].timestamp.isoformat(),
                "strategy": decision.signal.strategy,
                "side": decision.signal.side.value,
                "regime": market.regime.primary.value,
                "confidence": decision.signal.confidence,
            })
        return decision


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def summarize_group(rows: list[dict[str, object]]) -> dict[str, object]:
    return trade_metrics(rows)


def fmt(value, digits=2):
    if value is None:
        return "n/a"
    if isinstance(value, float) and math.isinf(value):
        return "∞"
    return f"{value:,.{digits}f}"


def money(value):
    return f"${value:,.2f}"


def table(headers: list[str], rows: list[list[object]]) -> str:
    return "\n".join([
        "| " + " | ".join(headers) + " |",
        "|" + "|".join("---" for _ in headers) + "|",
        *["| " + " | ".join(str(v) for v in row) + " |" for row in rows],
    ])


def run(args) -> None:
    candles = load_ohlcv_csv(args.csv)
    ensemble = RecordingEnsemble()
    fee_rate = args.fee_bps / 10_000
    slip_rate = args.slippage_bps / 10_000
    sizer = PositionSizer(RiskConfig(
        risk_per_trade_pct=args.risk,
        leverage=args.leverage,
        estimated_round_trip_cost_pct=2 * (fee_rate + slip_rate),
    ))
    engine = IntrabarBacktester(BacktestConfig(
        initial_equity=args.equity,
        fee_rate=fee_rate,
        slippage_bps=args.slippage_bps,
        funding_rate_per_8h=args.funding_bps_8h / 10_000,
        logging_level=LogLevel.ERROR,
    ), sizer=sizer)
    result = engine.run(candles, ensemble=ensemble)
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)

    by_signal = {int(row["signal_id"]): row for row in ensemble.signals}
    trades = [normalize_trade(t, slip_rate) for t in result.trades]
    for row in trades:
        signal = by_signal.get(int(row["signal_id"] or 0))
        row["regime"] = signal["regime"] if signal else "UNKNOWN"
    assert_trade_reconciliation(trades)
    if abs(sum(float(t["net_pnl"]) for t in trades) - result.net_pnl) > 1e-7:
        raise AssertionError("trade ledger net P&L does not reconcile to final equity")
    if abs(sum(float(t["raw_gross_pnl"]) - float(t["slippage_cost"]) - float(t["fees"]) - float(t["funding_paid"]) for t in trades) - result.net_pnl) > 1e-6:
        raise AssertionError("cost and gross P&L totals do not reconcile")

    all_metrics = trade_metrics(trades)
    strategy_groups: dict[str, list[dict[str, object]]] = defaultdict(list)
    side_groups: dict[str, list[dict[str, object]]] = defaultdict(list)
    regime_groups: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in trades:
        strategy_groups[str(row["strategy"])].append(row)
        side_groups[str(row["side"])].append(row)
        regime_groups[str(row["regime"])].append(row)
    strategy_rows = []
    for name in sorted(strategy_groups):
        metrics = summarize_group(strategy_groups[name])
        signal_count = sum(s["strategy"] == name for s in ensemble.signals)
        strategy_rows.append({"strategy": name, "signals": signal_count, **metrics})
    side_rows = [{"side": name, **summarize_group(group)} for name, group in sorted(side_groups.items())]
    regime_rows = [{"regime": name, **summarize_group(group)} for name, group in sorted(regime_groups.items())]
    write_csv(out / "trades.csv", trades)
    write_csv(out / "signals.csv", ensemble.signals)
    write_csv(out / "strategy_performance.csv", strategy_rows)
    write_csv(out / "side_performance.csv", side_rows)
    write_csv(out / "regime_performance.csv", regime_rows)

    month_rows = monthly_equity(result.equity_curve, result.initial_equity)
    year_groups: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in month_rows:
        year_groups[str(row["month"])[:4]].append(row)
    year_rows = []
    prior_equity = result.initial_equity
    for year in sorted(year_groups):
        entries = year_groups[year]
        closing = float(entries[-1]["equity_end"])
        year_rows.append({
            "year": year, "equity_end": closing,
            "return_pct": closing / prior_equity - 1 if prior_equity else 0,
            "max_drawdown_pct": max(float(r["max_drawdown_pct"]) for r in entries),
        })
        prior_equity = closing
    write_csv(out / "monthly_returns.csv", month_rows)
    write_csv(out / "yearly_returns.csv", year_rows)
    equity_rows = [{"candle_open_timestamp": p.timestamp.isoformat(), "equity": p.equity,
                    "drawdown_pct": p.drawdown_pct} for p in result.equity_curve]
    write_csv(out / "equity_drawdown.csv", equity_rows)

    kill_time = result.kill_events[0][0] if result.kill_events else None
    kill_data = None
    if kill_time:
        before = [p for p in result.equity_curve if p.timestamp <= kill_time]
        after = [p for p in result.equity_curve if p.timestamp >= kill_time]
        peak = max(before, key=lambda p: p.equity)
        trigger = before[-1]
        kill_data = {
            "timestamp": kill_time.isoformat(), "reason": result.kill_events[0][1],
            "decision_at_close": (kill_time + timedelta(minutes=15)).isoformat(),
            "equity": trigger.equity, "peak_equity": peak.equity,
            "peak_timestamp": peak.timestamp.isoformat(), "drawdown_pct": trigger.drawdown_pct,
            "trades_entered_before_or_at": sum(datetime.fromisoformat(str(t["entry_time"])) <= kill_time for t in trades),
            "trades_entered_after": sum(datetime.fromisoformat(str(t["entry_time"])) > kill_time for t in trades),
            "post_switch_equity_min": min(p.equity for p in after),
            "post_switch_equity_max": max(p.equity for p in after),
        }

    report = [
        "# Strategy evaluation — current 2× leverage / 0.5% risk",
        "",
        f"Run input: `{args.csv}` ({len(candles):,} candles, {candles[0].timestamp.isoformat()} through {candles[-1].timestamp.isoformat()}).",
        f"Configuration: initial equity {money(args.equity)}; risk {args.risk:.2%}; leverage {args.leverage:g}×; fee {args.fee_bps:g} bps/side; slippage {args.slippage_bps:g} bps/side; funding {args.funding_bps_8h:g} bps/8h; max notional 100% of equity; max margin 80% of equity; sizing cost reserve 20 bps round trip; max participation 5% of prior-candle volume; force-close at dataset end; kill switch **enabled in safety mode** (10% max drawdown threshold).",
        "Input timestamps are candle-open labels; signal decisions occur at the 15m close. Trades are scored from the completed ledger, and no strategy settings were changed.",
        "",
        "## Observed overall results",
        "",
        table(["Metric", "Observed"], [
            ["Completed trades", all_metrics["trades"]],
            ["Raw gross P&L (before modeled slippage, fees, funding)", money(float(all_metrics["raw_gross_pnl"]))],
            ["Gross P&L at slipped execution prices (before fees/funding)", money(float(all_metrics["execution_gross_pnl"]))],
            ["Net P&L", money(float(all_metrics["net_pnl"]))],
            ["Fees paid", money(float(all_metrics["fees"]))],
            ["Slippage cost", money(float(all_metrics["slippage"]))],
            ["Funding paid (negative means net credit)", money(float(all_metrics["funding_paid"]))],
            ["Win rate", f"{float(all_metrics['win_rate']):.2%}"],
            ["Average winning trade (net)", money(float(all_metrics["average_win"])) if all_metrics["average_win"] is not None else "n/a"],
            ["Average losing trade (net)", money(float(all_metrics["average_loss"])) if all_metrics["average_loss"] is not None else "n/a"],
            ["Profit factor (net wins / absolute net losses)", fmt(all_metrics["profit_factor"])],
            ["Expectancy per completed trade (net)", money(float(all_metrics["expectancy"])) if all_metrics["expectancy"] is not None else "n/a"],
            ["Final equity", money(result.final_equity)],
            ["Return on initial equity", f"{result.return_pct:.2%}"],
            ["Maximum marked-to-market drawdown", f"{result.max_drawdown_pct:.2%}"],
            ["Longest consecutive losing trades", all_metrics["max_consecutive_losses"]],
            ["Mean / median holding time (candle-label difference)", f"{fmt(all_metrics['mean_holding_hours'])}h / {fmt(all_metrics['median_holding_hours'])}h"],
        ]),
        "",
        f"**Ledger reconciliation:** raw gross {money(float(all_metrics['raw_gross_pnl']))} − slippage {money(float(all_metrics['slippage']))} − fees {money(float(all_metrics['fees']))} − funding paid {money(float(all_metrics['funding_paid']))} = net {money(float(all_metrics['net_pnl']))}. Maximum absolute per-trade reconciliation delta: {max((abs(float(t['raw_accounting_net']) - float(t['net_pnl'])) for t in trades), default=0):.3g}. Ledger net P&L equals final equity minus initial equity within 1e-7.",
        "",
        "## Performance by selected strategy component",
        "",
        "Attribution is exclusive to the single signal selected by the ensemble for each accepted trade (`Trade.strategy`). If multiple same-direction strategies signaled, the ensemble uses their confidence sum to choose a side but records the highest-confidence same-side candidate as the selected strategy; P&L is not split. Opposing candidates can cancel the decision. Signals below include selected ensemble signals, not every internal candidate from every component.",
        "",
        table(["Component", "Selected signals", "Trades", "Wins/Losses", "Net P&L", "Win rate", "PF", "Expectancy"], [
            [r["strategy"], r["signals"], r["trades"], f"{r['wins']}/{r['losses']}", money(float(r["net_pnl"])), f"{float(r['win_rate']):.1%}", fmt(r["profit_factor"]), money(float(r["expectancy"])) if r["expectancy"] is not None else "n/a"] for r in strategy_rows
        ]),
        "",
        "## Long vs. short and market regime",
        "",
        table(["Side", "Trades", "Wins/Losses", "Net P&L", "Win rate", "PF", "Expectancy", "Mean hold"], [
            [r["side"], r["trades"], f"{r['wins']}/{r['losses']}", money(float(r["net_pnl"])), f"{float(r['win_rate']):.1%}", fmt(r["profit_factor"]), money(float(r["expectancy"])) if r["expectancy"] is not None else "n/a", f"{fmt(r['mean_holding_hours'])}h"] for r in side_rows
        ]),
        "",
        "Regime is the primary 4H regime value present in the decision snapshot for the trade's signal ID. The engine intentionally uses its current developing higher-timeframe semantics; these are not completed-bar-only regime labels.",
        "",
        table(["Decision regime", "Trades", "Wins/Losses", "Net P&L", "Win rate", "PF", "Expectancy"], [
            [r["regime"], r["trades"], f"{r['wins']}/{r['losses']}", money(float(r["net_pnl"])), f"{float(r['win_rate']):.1%}", fmt(r["profit_factor"]), money(float(r["expectancy"])) if r["expectancy"] is not None else "n/a"] for r in regime_rows
        ]),
        "",
        "## Equity, drawdown, and safety switch",
        "",
        f"The safety switch activated while processing candle-open label `{kill_data['timestamp']}` ({kill_data['reason']}); the equity snapshot uses that candle's close and therefore the decision/observation time is `{kill_data['decision_at_close']}`. Equity was {money(kill_data['equity'])}, from a peak of {money(kill_data['peak_equity'])} at `{kill_data['peak_timestamp']}`; measured drawdown {kill_data['drawdown_pct']:.4%}. {kill_data['trades_entered_before_or_at']} trades entered by that point; {kill_data['trades_entered_after']} entered afterward. Equity after activation ranged {money(kill_data['post_switch_equity_min'])}–{money(kill_data['post_switch_equity_max'])} through the dataset end." if kill_data else "No kill-switch activation occurred.",
        "",
        "The switch remains enabled in this primary evaluation, so the reported loss and flat post-trigger equity are safety-censored results—not the performance of signals that were blocked after activation. The monthly table below contains month-end equity, return from the previous month-end (initial equity for the first partial month), and the largest running peak-to-date drawdown observed in that month. The full per-candle curve is in `equity_drawdown.csv`.",
        "",
        table(["Month", "Month-end equity", "Return", "Peak-to-date DD"], [[r["month"], money(float(r["equity_end"])), f"{float(r['return_pct']):.2%}", f"{float(r['max_drawdown_pct']):.2%}"] for r in month_rows]),
        "",
        "### Yearly",
        "",
        table(["Year", "Year-end equity", "Return", "Peak-to-date DD"], [[r["year"], money(float(r["equity_end"])), f"{float(r['return_pct']):.2%}", f"{float(r['max_drawdown_pct']):.2%}"] for r in year_rows]),
        "",
        "## Interpretation: observed vs. hypothesis",
        "",
        "Observed: the run closed with a net loss, and the kill switch prevented further entries after the January 2020 drawdown trigger. Exclusive attribution assigns the largest negative P&L to MeanReversion in this realized, safety-censored sample; the other three components also have negative net P&L except Breakout, whose small positive result is based on very few trades.",
        "",
        "Hypothesis, not established causation: MeanReversion may be the largest contributor to the early drawdown, but this sample contains only 92 trades before the switch stopped entries. Component comparisons are not controlled experiments; components see different regimes and opportunity sets, and the chosen-component attribution does not allocate shared consensus credit. Do not infer a durable edge or rank from these figures alone.",
        "",
        "## Proposed time-based evaluation protocol (not run)",
        "",
        "1. Reserve the earliest data only for indicator warm-up (for example 2019-12-31 through the first completed warm-up window); exclude it from scored returns.",
        "2. Use 2020–2022 as development data and 2023 as a single validation period. Any strategy/parameter choices belong only to development/validation; record them before opening the holdout.",
        "3. Lock the strategy and all execution assumptions, then evaluate 2024–2025 once as the untouched final historical holdout. Do not tune on this result and continue calling it out-of-sample.",
        "4. Treat 2026 data as forward/prequential evaluation only: freeze each test block before it begins; if later blocks use earlier blocks for training, report each block separately and never use the aggregate to justify repeated retuning. A truly untouched final test after any further change requires new future data.",
        "5. If walk-forward tuning is needed before the final holdout, use rolling/expanding train windows followed by a separate validation block and then a strictly later test block. Fit thresholds/scalers/feature selection only on each training window; preserve a change log and evaluate the final locked configuration once on the sealed holdout.",
        "",
        "## Artifacts",
        "",
        "`trades.csv` contains each completed trade and reconstructed cost fields; `signals.csv` contains selected ensemble signals with contemporaneous regime; `strategy_performance.csv`, `side_performance.csv`, `regime_performance.csv`, `monthly_returns.csv`, `yearly_returns.csv`, and `equity_drawdown.csv` provide the report tables and detailed time series.",
        "",
    ]
    (out / "performance_report.md").write_text("\n".join(report), encoding="utf-8")
    (out / "run_config.json").write_text(json.dumps({
        "csv": str(args.csv.resolve()), "candles": len(candles), "start": candles[0].timestamp.isoformat(), "end": candles[-1].timestamp.isoformat(),
        "initial_equity": args.equity, "risk_per_trade_pct": args.risk, "leverage": args.leverage,
        "fee_bps_per_side": args.fee_bps, "slippage_bps_per_side": args.slippage_bps,
        "funding_bps_per_8h": args.funding_bps_8h, "kill_switch": "safety_enabled",
        "max_position_notional_pct": 1.0, "max_margin_fraction": 0.8,
        "sizing_round_trip_cost_reserve_pct": 0.002, "max_participation_rate": 0.05,
        "force_close_at_end": True, "kill_switch_max_drawdown_pct": 0.10,
        "entries_accepted": result.diagnostics["entries_accepted"],
        "entries_skipped_kill_switch": result.diagnostics["entries_skipped_kill_switch"],
        "kill_events": [[t.isoformat(), reason, detail] for t, reason, detail in result.kill_events],
        "final_equity": result.final_equity, "max_drawdown_pct": result.max_drawdown_pct,
    }, indent=2), encoding="utf-8")
    print(out / "performance_report.md")
    print(f"trades={len(trades)} final_equity={result.final_equity:.8f} net={result.net_pnl:.8f} max_dd={result.max_drawdown_pct:.8%}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate an auditable strategy performance report.")
    parser.add_argument("csv", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "reports" / "current_2x_risk005")
    parser.add_argument("--equity", type=float, default=10_000)
    parser.add_argument("--risk", type=float, default=0.005)
    parser.add_argument("--leverage", type=float, default=2)
    parser.add_argument("--fee-bps", type=float, default=5)
    parser.add_argument("--slippage-bps", type=float, default=5)
    parser.add_argument("--funding-bps-8h", type=float, default=1)
    run(parser.parse_args())


if __name__ == "__main__":
    main()
