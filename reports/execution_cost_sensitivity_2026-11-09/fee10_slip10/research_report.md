# Fixed-reference-risk independent-trade research evaluation

This report is a counterfactual per-signal research evaluation, not an executable portfolio. Signals overlap and do not share capital or aggregate liquidity. See `run_metadata.json` for the frozen inputs, hash, commit, seed and parameters.

Sizing targets a $50 initial-stop loss including adverse entry/exit slippage and entry/exit fees, subject to leverage collateral, notional, contract and per-trade volume caps. Variable funding is accrued from actual simulated elapsed holding time and included in net P&L, but excluded from ex-ante stop-risk sizing because exit time is unknown. Stop gaps can exceed the modeled stop loss and are flagged per trade.

## Predictive performance

Directional accuracy and forward returns are signal prediction measures, not trading profitability. Forward return, MFE and MAE sample sizes and means by strategy, regime and horizon are in `predictive_performance.csv` and `.json`.

## Trading performance

| Strategy | Trades | Win rate (Wilson 95%) | Gross P&L | Fees | Slippage | Funding paid | Net P&L | Expectancy (weekly block CI) | Profit factor (weekly block CI) | Diagnostic max DD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| mean_reversion | 27969 | 31.3% (30.8%–31.9%) | $-16,706.07 | $305,982.92 | $305,982.91 | $-96.87 | $-628,575.03 | $-22.47 (-23.16–-21.77) | 0.28 (0.26–0.29) | 6285.80% |
| breakout | 16288 | 36.6% (35.8%–37.3%) | $-1,001.44 | $163,265.40 | $163,265.40 | $172.70 | $-327,704.95 | $-20.12 (-20.97–-19.29) | 0.34 (0.32–0.36) | 3278.56% |
| trend_following | 2226 | 38.2% (36.2%–40.2%) | $-1,733.75 | $19,245.88 | $19,245.88 | $172.83 | $-40,398.34 | $-18.15 (-20.61–-15.80) | 0.41 (0.35–0.47) | 403.98% |
| momentum | 4809 | 41.2% (39.9%–42.6%) | $6,228.83 | $31,664.86 | $31,664.86 | $453.69 | $-57,554.58 | $-11.97 (-14.92–-9.05) | 0.59 (0.52–0.68) | 543.39% |

## Monthly and regime breakdowns

See `monthly_performance.csv` and `market_regime_performance.csv`. Monthly ledger reconciliation by strategy is in `evaluation.json` and has been checked to within $1e-7. Exit reason counts (stop, target, end of data) are also in `evaluation.json`.

## Clustering and exclusions
- Unique signal weeks: 347
- Trades overlapping another independent trade: 45424 (88.6%); maximum concurrent: 25.
- Excluded signals by reason: `{}`
- Weekly bootstrap: seed 20261105, 2000 iterations, ISO weeks resampled with replacement; trades within a week remain clustered. Intervals do not prove independence.

independent_trade_diagnostic_curve is a sum of overlapping independent-trade P&L. Trades do not share capital or aggregate liquidity; it is not executable portfolio equity.
