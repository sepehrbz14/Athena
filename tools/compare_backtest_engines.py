"""Run legacy and optimized backtests with identical CLI-default parameters.

This is intentionally an investigation utility. It does not change any engine
decisions; it exports both trade ledgers and the first differing decision.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from dataclasses import asdict
from pathlib import Path
from time import monotonic
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from crypto_bot.backtest import BacktestConfig, IntrabarBacktester
from crypto_bot.diagnostics import LogLevel
from crypto_bot.io import load_ohlcv_csv
from crypto_bot.risk import PositionSizer, RiskConfig
from crypto_bot.strategies import StrategyEnsemble


class DecisionTrace:
    def __init__(self, path: Path) -> None:
        self.delegate = StrategyEnsemble()
        self.handle = path.open("w", encoding="utf-8")
        self.calls = 0

    @staticmethod
    def _candle_row(candle):  # noqa: ANN001
        if candle is None:
            return None
        return {
            "timestamp": candle.timestamp.isoformat(),
            "open": candle.open,
            "high": candle.high,
            "low": candle.low,
            "close": candle.close,
            "volume": candle.volume,
        }

    def decide(self, market):  # noqa: ANN001
        decision = self.delegate.decide(market)
        candle = market.candles_15m[-1]
        signal = decision.signal
        signal_row = None if signal is None else (
            signal.side.value,
            signal.stop_price,
            signal.take_profit,
            signal.confidence,
            signal.strategy,
            signal.rationale,
        )
        row = {
            "timestamp": candle.timestamp.isoformat(),
            "ohlcv": [candle.open, candle.high, candle.low, candle.close, candle.volume],
            "regime": {
                "primary": market.regime.primary.value,
                "trend_strength": market.regime.trend_strength,
                "volatility_pct": market.regime.volatility_pct,
                "momentum_pct": market.regime.momentum_pct,
                "detail": market.regime.detail,
            },
            "1h_count": len(market.candles_1h),
            "1h_latest": self._candle_row(market.candles_1h[-1] if market.candles_1h else None),
            "4h_count": len(market.candles_4h),
            "4h_latest": self._candle_row(market.candles_4h[-1] if market.candles_4h else None),
            "optimized_15m_features": asdict(market.features) if market.features else None,
            "signal": signal_row,
            "considered": decision.considered,
            "rejected": decision.rejected,
        }
        self.handle.write(json.dumps(row, separators=(",", ":")) + "\n")
        self.calls += 1
        return decision

    def close(self) -> None:
        self.handle.close()


def _trade_row(trade) -> dict[str, Any]:  # noqa: ANN001
    row = asdict(trade)
    for key in ("side",):
        row[key] = row[key].value
    for key in ("entry_time", "exit_time"):
        row[key] = row[key].isoformat()
    return row


def _write_trades(path: Path, trades) -> None:  # noqa: ANN001
    rows = [_trade_row(trade) for trade in trades]
    fields = list(rows[0]) if rows else list(TradeFieldNames)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


TradeFieldNames = (
    "side", "quantity", "entry_time", "exit_time", "entry_price", "exit_price",
    "stop_price", "take_profit", "strategy", "exit_reason", "gross_pnl",
    "entry_fee", "exit_fee", "funding_paid", "net_pnl", "trade_id", "signal_id", "order_id",
)


def _progress(label: str, total: int):
    last_done = [0]
    last_time = [monotonic()]

    def update(done: int, _total: int) -> None:
        now = monotonic()
        if done == total or now - last_time[0] >= 15:
            elapsed = now - last_time[0]
            rate = (done - last_done[0]) / elapsed if elapsed else 0.0
            print(f"{label}: {done:,}/{total:,} candles ({done / total:.1%}), {rate:,.0f}/s", flush=True)
            last_done[0] = done
            last_time[0] = now

    return update


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("csv", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("engine_comparison"))
    parser.add_argument("--max-candles", type=int, help="limit both runs to an identical input prefix")
    parser.add_argument("--equity", type=float, default=10_000.0)
    parser.add_argument("--risk", type=float, default=0.005)
    parser.add_argument("--leverage", type=float, default=1.0)
    parser.add_argument("--fee-bps", type=float, default=5.0)
    parser.add_argument("--slippage-bps", type=float, default=5.0)
    parser.add_argument("--funding-bps-8h", type=float, default=1.0)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(args.csv.read_bytes()).hexdigest()
    input_candles = load_ohlcv_csv(args.csv)
    candles = input_candles[:args.max_candles] if args.max_candles else input_candles
    fee_rate = args.fee_bps / 10_000

    # Values match the CLI defaults used by the reported optimized run.
    common = BacktestConfig(
        initial_equity=args.equity,
        fee_rate=fee_rate,
        slippage_bps=args.slippage_bps,
        funding_rate_per_8h=args.funding_bps_8h / 10_000,
        candle_minutes=15,
        max_participation_rate=0.05,
        force_close_at_end=True,
        logging_level=LogLevel.INFO,
    )
    sizer = PositionSizer(RiskConfig(
        risk_per_trade_pct=args.risk,
        leverage=args.leverage,
        estimated_round_trip_cost_pct=2 * (fee_rate + args.slippage_bps / 10_000),
    ))

    runs = {}
    traces = {}
    for name, legacy in (("legacy", True), ("optimized", False)):
        trace_path = args.output_dir / f"{name}_decisions.jsonl"
        trace = DecisionTrace(trace_path)
        config = BacktestConfig(**(asdict(common) | {"legacy_snapshot_mode": legacy}))
        print(f"Starting {name} run: {len(candles):,} candles", flush=True)
        result = IntrabarBacktester(config, sizer).run(
            candles,
            ensemble=trace,
            progress_callback=_progress(name, len(candles)),
        )
        trace.close()
        _write_trades(args.output_dir / f"{name}_trades.csv", result.trades)
        runs[name] = result
        traces[name] = (trace_path, trace.calls)
        print(f"Finished {name}: {len(result.trades)} trades, ${result.final_equity:,.2f}", flush=True)

    legacy, optimized = runs["legacy"], runs["optimized"]
    left_path, left_calls = traces["legacy"]
    right_path, right_calls = traces["optimized"]
    first_decision_difference = None
    first_decision = None
    first_state_difference = None
    with left_path.open(encoding="utf-8") as left, right_path.open(encoding="utf-8") as right:
        for index, (left_line, right_line) in enumerate(zip(left, right)):
            old, new = json.loads(left_line), json.loads(right_line)
            if first_decision is None and (old["signal"], old["considered"], old["rejected"]) != (new["signal"], new["considered"], new["rejected"]):
                first_decision_difference = index
                first_decision = {"legacy": old, "optimized": new}
            old_state = (old["timestamp"], old["ohlcv"], old["regime"], old["1h_count"], old["1h_latest"], old["4h_count"], old["4h_latest"])
            new_state = (new["timestamp"], new["ohlcv"], new["regime"], new["1h_count"], new["1h_latest"], new["4h_count"], new["4h_latest"])
            if first_state_difference is None and old_state != new_state:
                first_state_difference = {"decision_index": index, "legacy": old, "optimized": new}
        else:
            if left_calls != right_calls:
                first_decision_difference = min(left_calls, right_calls)
                if left_calls > right_calls:
                    first_decision = {"legacy_extra": next((json.loads(line) for i, line in enumerate(left_path.open(encoding="utf-8")) if i == first_decision_difference), None)}
                else:
                    first_decision = {"optimized_extra": next((json.loads(line) for i, line in enumerate(right_path.open(encoding="utf-8")) if i == first_decision_difference), None)}

    report = {
        "input_csv": str(args.csv.resolve()),
        "input_sha256": digest,
        "candles": len(candles),
        "input_candles": len(input_candles),
        "configuration": {
            "initial_equity": args.equity,
            "risk_per_trade_pct": args.risk,
            "leverage": args.leverage,
            "fee_rate": fee_rate,
            "slippage_bps": args.slippage_bps,
            "funding_rate_per_8h": args.funding_bps_8h / 10_000,
            "estimated_round_trip_cost_pct": 2 * (fee_rate + args.slippage_bps / 10_000),
            "max_participation_rate": 0.05,
            "force_close_at_end": True,
            "monte_carlo_iterations": 0,
        },
        "legacy": {
            "trades": len(legacy.trades),
            "final_equity": legacy.final_equity,
            "net_pnl": legacy.net_pnl,
            "max_drawdown_pct": legacy.max_drawdown_pct,
            "trades_csv": "legacy_trades.csv",
        },
        "optimized": {
            "trades": len(optimized.trades),
            "final_equity": optimized.final_equity,
            "net_pnl": optimized.net_pnl,
            "max_drawdown_pct": optimized.max_drawdown_pct,
            "trades_csv": "optimized_trades.csv",
        },
        "equivalent": {
            "trades": legacy.trades == optimized.trades,
            "equity_curve": legacy.equity_curve == optimized.equity_curve,
            "kill_switch_events": legacy.kill_events == optimized.kill_events,
            "rejected_orders": legacy.rejected_orders == optimized.rejected_orders,
            "partial_fills": legacy.partial_fills == optimized.partial_fills,
            "final_equity": legacy.final_equity == optimized.final_equity,
            "decision_trace": first_decision_difference is None and left_calls == right_calls,
            "higher_timeframe_and_candle_state": first_state_difference is None,
            "decision_trace_calls": [left_calls, right_calls],
        },
        "first_decision_difference": first_decision,
        "first_candle_or_timeframe_state_difference": first_state_difference,
    }
    (args.output_dir / "comparison.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    main()
