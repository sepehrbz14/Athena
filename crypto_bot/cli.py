from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from .backtest import BacktestConfig, IntrabarBacktester
from .controls import KillSwitchMode
from .diagnostics import LogLevel, PerformanceTimings
from .io import load_ohlcv_csv
from .risk import PositionSizer, RiskConfig
from .validation import monte_carlo_trade_order


class ProgressBar:
    def __init__(self, label: str = "Backtest", width: int = 32, min_interval: float = 0.1) -> None:
        self.label = label
        self.width = width
        self.min_interval = min_interval
        self.last_update = 0.0
        self.finished = False

    def update(self, processed: int, total: int) -> None:
        now = time.monotonic()
        if processed < total and now - self.last_update < self.min_interval:
            return
        self.last_update = now
        ratio = processed / total if total else 1.0
        filled = min(self.width, int(self.width * ratio))
        bar = "#" * filled + "-" * (self.width - filled)
        percent = ratio * 100
        sys.stderr.write(f"\r{self.label}: [{bar}] {processed:,}/{total:,} candles ({percent:5.1f}%)")
        sys.stderr.flush()
        if processed >= total and not self.finished:
            sys.stderr.write("\n")
            sys.stderr.flush()
            self.finished = True


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Athena's conservative 15m OHLCV backtest.")
    parser.add_argument("csv", help="CSV with timestamp,open,high,low,close,volume columns")
    parser.add_argument("--equity", type=float, default=10_000)
    parser.add_argument("--risk", type=float, default=0.005, help="fraction of equity risked per trade")
    parser.add_argument("--leverage", type=float, default=1.0, help="collateral efficiency only; does not increase risk")
    parser.add_argument("--fee-bps", type=float, default=5.0, help="per-side taker fee in basis points")
    parser.add_argument("--slippage-bps", type=float, default=5.0)
    parser.add_argument("--funding-bps-8h", type=float, default=1.0, help="positive funding paid by longs every 8h")
    parser.add_argument("--monte-carlo", type=int, default=0, metavar="N")
    parser.add_argument("--log-level", choices=[level.value for level in LogLevel], default=LogLevel.INFO.value)
    parser.add_argument("--diagnostic-mode", action="store_true", help="export detailed signals, orders, trades, rejections, and JSONL events")
    parser.add_argument("--diagnostics-dir", type=Path, help="directory for diagnostic exports")
    parser.add_argument("--no-progress", action="store_true", help="disable the dataset progress bar")
    parser.add_argument("--profile-performance", action="store_true", help="print per-stage wall-clock timings")
    parser.add_argument("--research-mode", action="store_true", help="observe and log kill-switch trips without blocking entries")
    args = parser.parse_args()

    timings = PerformanceTimings(args.profile_performance)
    with timings.measure("data_loading"):
        candles = load_ohlcv_csv(args.csv)
    fee_rate = args.fee_bps / 10_000
    slip_rate = args.slippage_bps / 10_000
    sizer = PositionSizer(RiskConfig(
        risk_per_trade_pct=args.risk,
        leverage=args.leverage,
        estimated_round_trip_cost_pct=2 * (fee_rate + slip_rate),
    ))
    bot = IntrabarBacktester(
        BacktestConfig(
            initial_equity=args.equity,
            fee_rate=fee_rate,
            slippage_bps=args.slippage_bps,
            funding_rate_per_8h=args.funding_bps_8h / 10_000,
            logging_level=LogLevel(args.log_level),
            diagnostic_mode=args.diagnostic_mode,
            diagnostics_dir=args.diagnostics_dir,
            profile_performance=args.profile_performance,
            kill_switch_mode=KillSwitchMode.OBSERVATIONAL if args.research_mode else KillSwitchMode.SAFETY,
        ),
        sizer=sizer,
    )
    progress = None if args.no_progress else ProgressBar("Dataset")
    result = bot.run(candles, progress_callback=progress.update if progress else None, performance_timings=timings)
    print(f"Trades: {len(result.trades)} | Win rate: {result.win_rate:.1%}")
    print(f"Net P&L: ${result.net_pnl:,.2f} | Final equity: ${result.final_equity:,.2f}")
    print(f"Return: {result.return_pct:.2%} | Max drawdown: {result.max_drawdown_pct:.2%}")
    diagnostics = result.diagnostics
    print("=== BACKTEST DIAGNOSTICS ===")
    print(f"Candles processed: {diagnostics['candles_processed']}")
    print(f"Signals generated: {diagnostics['signals_generated']}")
    print(f"Entry attempts: {diagnostics['entry_attempts']}")
    print(f"Entries accepted: {diagnostics['entries_accepted']}")
    print(f"Entries rejected: {diagnostics['entries_rejected']}")
    print(f"Entries skipped by safety kill switch: {diagnostics['entries_skipped_kill_switch']}")
    print("Rejection reasons:")
    for reason, count in diagnostics["rejection_reasons"].items():
        print(f"  {reason}: {count}")
    print(f"Trades opened: {diagnostics['trades_opened']} | Trades closed: {diagnostics['trades_closed']}")
    print(f"Partial fills: {diagnostics['partial_fills']} | Rejected orders: {diagnostics['rejected_orders']}")
    print(f"Kill-switch activations: {diagnostics['kill_switch_activations']}")
    print(f"Kill-switch would-trigger events: {diagnostics['kill_switch_would_trigger']}")
    if result.kill_events:
        print("Kill-switch events:")
        for timestamp, reason, detail in result.kill_events:
            print(f"  {timestamp.isoformat()} {reason}: {detail}")
    if diagnostics["strategy_performance"]:
        print("Strategy performance:")
        for strategy, values in diagnostics["strategy_performance"].items():
            profit_factor = values["profit_factor"]
            pf_text = f"{profit_factor:.2f}" if profit_factor is not None else "n/a"
            expectancy = values["expectancy"]
            expectancy_text = f"${expectancy:,.2f}" if expectancy is not None else "n/a"
            print(
                f"  {strategy}: signals {values['signals']} | entries {values['entries']} | "
                f"wins {values['wins']} | losses {values['losses']} | P&L ${values['net_pnl']:,.2f} | "
                f"expectancy {expectancy_text} | PF {pf_text} | "
                f"drawdown contribution ${values['drawdown_contribution']:,.2f}"
            )
    if args.profile_performance:
        print("=== PERFORMANCE TIMINGS ===")
        for stage, values in diagnostics["performance_timings"].items():
            print(f"{stage}: {values['seconds']:.3f}s across {values['calls']} calls ({values['average_ms']:.3f}ms avg)")
    if args.monte_carlo:
        if not result.trades:
            print("Monte Carlo skipped: no completed trades.")
        else:
            with timings.measure("monte_carlo"):
                simulation = monte_carlo_trade_order(
                    result.trades, initial_equity=args.equity, iterations=args.monte_carlo,
                    diagnostic_mode=args.diagnostic_mode,
                )
            # Monte Carlo runs after the backtest summary was created, so expose
            # its timing in the printed report as well.
            if args.profile_performance:
                values = timings.summary()["monte_carlo"]
                print(f"monte_carlo: {values['seconds']:.3f}s across {values['calls']} calls ({values['average_ms']:.3f}ms avg)")
            print(
                "Monte Carlo: "
                f"95th-percentile drawdown {simulation.max_drawdown_p95:.2%}; "
                f"5th-percentile ending equity ${simulation.final_equity_p05:,.2f}"
            )


if __name__ == "__main__":
    main()
