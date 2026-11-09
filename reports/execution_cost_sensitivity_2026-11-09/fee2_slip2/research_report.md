# Fixed-reference-risk independent-trade research evaluation

This report is a counterfactual per-signal research evaluation, not an executable portfolio. Signals overlap and do not share capital or aggregate liquidity. See `run_metadata.json` for the frozen inputs, hash, commit, seed and parameters.

Sizing targets a $50 initial-stop loss including adverse entry/exit slippage and entry/exit fees, subject to leverage collateral, notional, contract and per-trade volume caps. Variable funding is accrued from actual simulated elapsed holding time and included in net P&L, but excluded from ex-ante stop-risk sizing because exit time is unknown. Stop gaps can exceed the modeled stop loss and are flagged per trade.

## Predictive performance

Directional accuracy and forward returns are signal prediction measures, not trading profitability. Forward return, MFE and MAE sample sizes and means by strategy, regime and horizon are in `predictive_performance.csv` and `.json`.

## Trading performance

| Strategy | Trades | Win rate (Wilson 95%) | Gross P&L | Fees | Slippage | Funding paid | Net P&L | Expectancy (weekly block CI) | Profit factor (weekly block CI) | Diagnostic max DD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| mean_reversion | 27969 | 39.5% (38.9%–40.0%) | $-23,188.97 | $85,859.39 | $85,859.39 | $-175.18 | $-194,732.56 | $-6.96 (-7.68–-6.21) | 0.75 (0.72–0.77) | 1922.23% |
| breakout | 16288 | 39.8% (39.0%–40.5%) | $-983.59 | $45,784.40 | $45,784.40 | $248.64 | $-92,801.03 | $-5.70 (-6.56–-4.91) | 0.80 (0.77–0.82) | 914.49% |
| trend_following | 2226 | 38.9% (36.9%–41.0%) | $-2,572.20 | $5,372.28 | $5,372.28 | $238.87 | $-13,555.63 | $-6.09 (-9.04–-3.29) | 0.80 (0.71–0.89) | 135.41% |
| momentum | 4809 | 41.2% (39.9%–42.6%) | $7,189.86 | $8,187.89 | $8,187.89 | $570.79 | $-9,756.70 | $-2.03 (-5.44–1.48) | 0.93 (0.82–1.05) | 99.16% |

## Monthly and regime breakdowns

See `monthly_performance.csv` and `market_regime_performance.csv`. Monthly ledger reconciliation by strategy is in `evaluation.json` and has been checked to within $1e-7. Exit reason counts (stop, target, end of data) are also in `evaluation.json`.

## Clustering and exclusions
- Unique signal weeks: 347
- Trades overlapping another independent trade: 45424 (88.6%); maximum concurrent: 25.
- Excluded signals by reason: `{}`
- Weekly bootstrap: seed 20261105, 2000 iterations, ISO weeks resampled with replacement; trades within a week remain clustered. Intervals do not prove independence.

independent_trade_diagnostic_curve is a sum of overlapping independent-trade P&L. Trades do not share capital or aggregate liquidity; it is not executable portfolio equity.
