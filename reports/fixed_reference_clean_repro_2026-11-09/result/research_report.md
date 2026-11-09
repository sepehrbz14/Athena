# Fixed-reference-risk independent-trade research evaluation

This report is a counterfactual per-signal research evaluation, not an executable portfolio. Signals overlap and do not share capital or aggregate liquidity. See `run_metadata.json` for the frozen inputs, hash, commit, seed and parameters.

Sizing targets a $50 initial-stop loss including adverse entry/exit slippage and entry/exit fees, subject to leverage collateral, notional, contract and per-trade volume caps. Variable funding is accrued from actual simulated elapsed holding time and included in net P&L, but excluded from ex-ante stop-risk sizing because exit time is unknown. Stop gaps can exceed the modeled stop loss and are flagged per trade.

## Predictive performance

Directional accuracy and forward returns are signal prediction measures, not trading profitability. Forward return, MFE and MAE sample sizes and means by strategy, regime and horizon are in `predictive_performance.csv` and `.json`.

## Trading performance

| Strategy | Trades | Win rate (Wilson 95%) | Gross P&L | Fees | Slippage | Funding paid | Net P&L | Expectancy (weekly block CI) | Profit factor (weekly block CI) | Diagnostic max DD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| mean_reversion | 27969 | 37.9% (37.4%–38.5%) | $-20,889.36 | $192,663.60 | $192,663.60 | $-154.21 | $-406,062.35 | $-14.52 (-15.19–-13.81) | 0.51 (0.49–0.53) | 4059.77% |
| breakout | 16288 | 39.3% (38.6%–40.1%) | $-1,355.24 | $102,072.59 | $102,072.59 | $218.41 | $-205,718.83 | $-12.63 (-13.48–-11.81) | 0.57 (0.55–0.60) | 2059.56% |
| trend_following | 2226 | 38.9% (36.9%–40.9%) | $-2,215.76 | $11,850.69 | $11,850.69 | $210.72 | $-26,127.85 | $-11.74 (-14.47–-9.17) | 0.62 (0.55–0.69) | 261.28% |
| momentum | 4809 | 41.2% (39.9%–42.6%) | $6,803.35 | $18,423.58 | $18,423.58 | $519.80 | $-30,563.61 | $-6.36 (-9.58–-3.10) | 0.78 (0.69–0.89) | 286.63% |

## Monthly and regime breakdowns

See `monthly_performance.csv` and `market_regime_performance.csv`. Monthly ledger reconciliation by strategy is in `evaluation.json` and has been checked to within $1e-7. Exit reason counts (stop, target, end of data) are also in `evaluation.json`.

## Clustering and exclusions
- Unique signal weeks: 347
- Trades overlapping another independent trade: 45424 (88.6%); maximum concurrent: 25.
- Excluded signals by reason: `{}`
- Weekly bootstrap: seed 20261105, 2000 iterations, ISO weeks resampled with replacement; trades within a week remain clustered. Intervals do not prove independence.

independent_trade_diagnostic_curve is a sum of overlapping independent-trade P&L. Trades do not share capital or aggregate liquidity; it is not executable portfolio equity.
