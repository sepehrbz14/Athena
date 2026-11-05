# Normal safety vs. full-period research comparison

Both runs used the same 232,608-candle dataset and baseline costs/configuration. Research results are hypothetical and are not an executable portfolio.

## Activity and outcomes

| Metric | Safety mode | Research mode |
|---|---:|---:|
| Signal opportunities | 28,701 | 8,507 |
| Trades closed/simulated | 92 | 8,454 |
| Entries skipped by kill switch | 28,609 | 0 |
| Kill-switch threshold events | 1 activation | 64 would-trigger |
| Net P&L | $-927.38 | $-9,999.90 |
| Final equity | $9,072.62 | $0.10 |
| Max drawdown | 10.09% | 100.00% |

Safety mode tripped on 2020-01-28 01:00 UTC and skipped 28,609 entry attempts afterward. Research mode recorded the threshold event and continued generating simulated trades through the dataset.

## Strategy performance (safety completed-trade ledger)

| Strategy | Trades | Win rate | Net P&L | Expectancy | Profit factor |
|---|---:|---:|---:|---:|---:|
| trend_following | 17 | 41.2% | $-163.31 | $-9.61 | 0.65 |
| breakout | 6 | 50.0% | $26.81 | $4.47 | 1.19 |
| mean_reversion | 59 | 39.0% | $-617.30 | $-10.46 | 0.59 |
| momentum | 10 | 30.0% | $-173.58 | $-17.36 | 0.47 |

## Monthly performance and comparability

Safety monthly returns and regime breakdown are available in `safety/monthly_returns.csv` and `safety/regime_performance.csv`; safety strategy details are in `safety/strategy_performance.csv`. Research event records support month-by-month closure aggregation in `comparison.json`, but its exported event stream does not provide equivalent completed-trade fee/slippage/funding and strategy attribution fields. Therefore strategy/regime/monthly net results between modes are not directly comparable from these artifacts. Research execution cost settings remain the same (5 bps fees and 5 bps slippage per side; 1 bp funding per 8h); the event-level export does not expose a reconciled cost ledger.

## Methodology

`--research-mode` in `crypto_bot/cli.py` selects `KillSwitchMode.OBSERVATIONAL`; default mode selects `SAFETY`. In `crypto_bot/backtest.py`, observational mode records kill-switch threshold events but does not block later entries. No kill-switch threshold or strategy parameter was changed. Both modes share signal logic, candle sequence, sizing configuration, and cost rates. Their equity paths diverge after safety stops entries, so research sizing and outcomes are hypothetical. See `comparison.json` for machine-readable summaries.