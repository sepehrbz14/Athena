# Athena crypto trading research bot

Athena is a **paper-first** crypto strategy research and backtesting foundation. It deliberately has no exchange credentials or live-order endpoint: a strategy must pass realistic research, independent review, and paper trading before anyone considers connecting capital.

It is not financial advice and it does not claim that a historical edge will persist.

## What is built

| Requirement | Implementation |
| --- | --- |
| Position sizing | `PositionSizer` risks a fixed percentage of equity at the stop, including a configurable cost reserve. Leverage only changes collateral required. |
| Leverage discipline | `RiskConfig.leverage` cannot increase the risk budget. Notional and margin caps are separate guardrails. |
| Regime detection | 4H EMA/ATR classifier identifies bull trend, bear trend, range, high volatility, low volatility, or a price-based event/shock proxy. |
| Multi-timeframe logic | 4H establishes context, 1H describes structure, and 15m supplies entries. |
| Volatility stops | Each included strategy uses a 15m ATR-multiple stop; wider volatility means the sizer returns fewer units. |
| Strategy ensemble | Trend following, breakout, momentum, and mean reversion are allowed only in compatible regimes. Opposing signals are suppressed. |
| Cost-aware simulation | Per-side fees, directional slippage, pro-rated 8-hour funding, and volume participation caps affect net P&L. |
| Honest OHLC testing | Signals form at a close and enter at the following open. Intrabar stop/target collisions choose the adverse stop. |
| Walk-forward | Repeated train-only optimization followed by unseen test windows. |
| Monte Carlo | Thousands of reshuffled trade orders quantify sequence-dependent drawdown. |
| Kill switch | Blocks new entries after daily loss, max drawdown, feed disagreement, repeated exchange errors, or abnormal ATR. |

## Install and test

Python 3.10+ is sufficient; the core intentionally has no third-party runtime dependencies.

```powershell
python -m unittest discover -s tests -v
```

## Run a backtest

Supply 15-minute OHLCV CSV data with the following header. `timestamp` is the
candle's **open time** in UTC. The bundled Binance-style daily archive confirms
this convention: its first timestamp matches the corresponding row in
`combined.csv`, and its close-time field is 14m59.999s later. Athena does not
infer or shift timestamps during loading.

```csv
timestamp,open,high,low,close,volume
2025-01-01T00:00:00Z,93500,93600,93450,93550,120.4
```

```powershell
python -m crypto_bot.cli .\data\BTCUSDT-15m.csv --equity 10000 --risk 0.005 --leverage 2 --fee-bps 5 --slippage-bps 5 --funding-bps-8h 1 --monte-carlo 10000
```

CLI backtests show a live dataset progress bar on stderr, for example
`Dataset: [########--------] 120,000/240,000 candles ( 50.0%)`. Use
`--no-progress` to disable it for scripted or redirected runs.

Add `--profile-performance` to print opt-in wall-clock timings for data loading,
multi-timeframe calculations, 15m signal generation, sizing, execution,
accounting, and optional Monte Carlo simulation. The timing collector observes
the run only; it does not affect trading decisions.

Use `--research-mode` to keep the kill switch observational during an offline
backtest: every would-trigger is logged, but entries continue. The default is
safety mode, which records `ENTRY_SKIPPED_KILL_SWITCH` rather than treating a
safety gate as an order rejection. Backtest diagnostics include per-strategy
signals, entries, wins, losses, P&L, expectancy, profit factor, and the dollar
loss contributed during the worst realized drawdown window.

## Diagnostics

Every run returns a structured `BacktestResult.diagnostics` summary with signal,
entry, rejection, fill, position, and kill-switch counters. Entry rejections use
the `EntryRejectionReason` enum and retain the first five context-rich examples
per reason. Normal CLI runs print only the aggregate breakdown.

For a forensic export, enable diagnostic mode:

```powershell
python -m crypto_bot.cli .\data\BTCUSDT-15m.csv --diagnostic-mode --diagnostics-dir .\diagnostics --log-level DEBUG
```

This writes `backtest_summary.json`, `rejections.csv`, `signals.csv`,
`orders.csv`, `trades.csv`, and `events.jsonl`. Each detailed lifecycle event
uses stable `signal_id`, `order_id`, and `trade_id` values. Logging is
observer-only: changing its level does not alter fills or P&L.

Event `timestamp` fields retain the source candle-open label for compatibility.
Signal events additionally include `candle_open_timestamp` and
`decision_at_close_timestamp` (open time plus 15 minutes). Entry execution
events identify the execution candle's open and carry the originating signal's
close-decision time. OHLC-based exit events identify the candle containing the
exit; they cannot claim an exact intrabar timestamp. The summary's
`timestamp_convention` records these rules. `Trade.entry_time` and
`Trade.exit_time` remain candle labels, not exact tick times.

At a next-open order, the participation cap uses the preceding completed
candle's base-asset volume, multiplied by `max_participation_rate`. A partial
entry retains one order ID and its remaining quantity is retried at later
opens, using the newly completed prior candle's volume each time. Fills
aggregate into one position/trade with a volume-weighted entry price and fees
summed per fill. Zero or missing prior volume means no estimated liquidity:
the order waits, and any remainder is canceled if the position exits or the
backtest ends. This is a causal bar-level estimate, not an order-book fill
model.

The current 1H and 4H bars remain developing: each decision can use the OHLCV
and EMA/ATR/regime calculated from 15m candles received so far in that bucket.
No later constituents are included, but the higher-timeframe values can change
before that bucket closes. Completed-bar-only signals remain a separate future
research decision.

The default drawdown kill switch remains a 10% permanent entry gate measured
from the highest observed mark-to-market equity: it trips when
`equity <= peak_equity * (1 - 0.10)`. The backtester observes it after the
current candle's close-side accounting and before generating that candle's
signal; it blocks new entries but does not force-close an existing position.
Its threshold and safety behavior are unchanged by the causal fill model.

The `--risk 0.005` argument is the fraction of equity lost at the stop (0.5%), not the amount allocated to the trade. For the example in the request—$10,000 equity, $60,000 entry, $59,500 stop—the sizer returns 0.1 BTC before costs and caps:

```python
from crypto_bot.models import Side
from crypto_bot.risk import PositionSizer, RiskConfig

sizer = PositionSizer(RiskConfig(risk_per_trade_pct=0.005, estimated_round_trip_cost_pct=0))
print(sizer.size(equity=10_000, entry_price=60_000, stop_price=59_500, side=Side.LONG).quantity)
# 0.1
```

## Research workflow

1. Keep a fully untouched out-of-sample dataset. Do not inspect it while choosing strategy parameters.
2. Calibrate parameters with `walk_forward()` only on rolling training windows, then inspect the unseen tests.
3. Run `monte_carlo_trade_order()` on completed trades. Base risk and collateral on a pessimistic drawdown percentile, not the smooth historical equity curve.
4. Run the same configuration in paper trading while recording actual fills, latency, spread, funding, and API errors.
5. If live execution is ever considered, use a separate audited adapter with read-only monitoring first, exchange-specific minimum/notional rules, persistent order reconciliation, secure secrets management, and a human-controlled enablement switch.

## Key files

- `crypto_bot/risk.py` — stop-risk sizing and collateral caps.
- `crypto_bot/regimes.py` and `crypto_bot/strategies.py` — regime detection, multi-timeframe strategy logic, and ensemble gating.
- `crypto_bot/backtest.py` — next-open, conservative intrabar simulator with execution costs.
- `crypto_bot/controls.py` — kill-switch controls.
- `crypto_bot/validation.py` — walk-forward and Monte Carlo analysis.
- `crypto_bot/cli.py` — CSV backtest entry point.

## Important limitations

OHLC intrabar simulation is intentionally conservative but cannot reproduce tick-by-tick queue position, order-book depth, exchange matching behavior, outages, partial cancels, liquidation mechanics, or news gaps. Use tick/order-book data and exchange-specific execution tests for any serious evaluation. The default strategies are reference implementations, not an assertion of profitability.

`NEWS_SHOCK` is deliberately a price-based proxy (a move several ATRs wide), not a claim to understand the cause. It disables the included strategies; a true news classifier requires a separately vetted news feed and timestamp-aligned event data.
