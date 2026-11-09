# Fixed-reference-risk independent-trade research evaluation

This report is a counterfactual per-signal research evaluation, not an executable portfolio. Signals overlap and do not share capital or aggregate liquidity. See `run_metadata.json` for the frozen inputs, hash, commit, seed and parameters.

Sizing targets a $50 initial-stop loss including adverse entry/exit slippage and entry/exit fees, subject to leverage collateral, notional, contract and per-trade volume caps. Variable funding is accrued from actual simulated elapsed holding time and included in net P&L, but excluded from ex-ante stop-risk sizing because exit time is unknown. Stop gaps can exceed the modeled stop loss and are flagged per trade.

## Predictive performance

Directional accuracy and forward returns are signal prediction measures, not trading profitability. Forward return, MFE and MAE sample sizes and means by strategy, regime and horizon are in `predictive_performance.csv` and `.json`.

## Trading performance

| Strategy | Trades | Win rate (Wilson 95%) | Gross P&L | Fees | Slippage | Funding paid | Net P&L | Expectancy (weekly block CI) | Profit factor (weekly block CI) | Diagnostic max DD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| mean_reversion | 27969 | 39.5% (38.9%–40.1%) | $-24,906.73 | $0.00 | $0.00 | $-176.12 | $-24,730.61 | $-0.88 (-1.66–-0.08) | 0.97 (0.94–1.00) | 250.26% |
| breakout | 16288 | 39.8% (39.0%–40.5%) | $-485.58 | $0.00 | $0.00 | $263.11 | $-748.69 | $-0.05 (-0.95–0.75) | 1.00 (0.96–1.03) | 57.44% |
| trend_following | 2226 | 38.9% (36.9%–41.0%) | $-2,733.39 | $0.00 | $0.00 | $258.33 | $-2,991.73 | $-1.34 (-4.43–1.62) | 0.95 (0.85–1.06) | 43.93% |
| momentum | 4809 | 41.2% (39.9%–42.6%) | $7,463.41 | $0.00 | $0.00 | $611.24 | $6,852.17 | $1.42 (-2.13–5.08) | 1.05 (0.93–1.18) | 46.20% |

## Monthly and regime breakdowns

See `monthly_performance.csv` and `market_regime_performance.csv`. Monthly ledger reconciliation by strategy is in `evaluation.json` and has been checked to within $1e-7. Exit reason counts (stop, target, end of data) are also in `evaluation.json`.

## Clustering and exclusions
- Unique signal weeks: 347
- Trades overlapping another independent trade: 45424 (88.6%); maximum concurrent: 25.
- Excluded signals by reason: `{}`
- Weekly bootstrap: seed 20261105, 2000 iterations, ISO weeks resampled with replacement; trades within a week remain clustered. Intervals do not prove independence.

independent_trade_diagnostic_curve is a sum of overlapping independent-trade P&L. Trades do not share capital or aggregate liquidity; it is not executable portfolio equity.
