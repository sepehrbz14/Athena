# Athena 2.0 — Phase 5 Strategy Diagnostics and Validation

Run created: `2026-11-18T12:09:47.862911+00:00`. Source commit: `6b6d3b9e64c80ce99d284b76f86bc79126a09dd5`.

## Scope and interpretation

This report evaluates the existing fixed strategies for predictive and executable potential. It does not optimize or alter strategy parameters. The Phase 4 executable account ledger and independent counterfactual trade ledger remain separate. Signal-label metrics describe predictive behavior, not trade or portfolio P&L.

All supplied market history ends on 2026-08-19 and has been examined in prior research. The chronological windows below are historical diagnostics, not untouched out-of-sample evidence. A genuinely future holdout is pending.

## Strategy scorecards

Gross in the executable lane is reconstructed before estimated adverse slippage; execution gross is the Phase 4 realized gross from fill prices. Research gross is raw reference-price P&L and execution gross includes slippage. Fees are positive costs; funding contribution is signed cash to the account. Net identities are checked per trade.

| Lane | Strategy | Trades | Gross exp. | Exec. gross exp. | Net exp. | Win rate | PF | Avg win | Avg loss | Fees | Slippage | Funding cash | Hold h | MFE % | MAE % | Stop exits | Gap stops | Stop budget breaches | Warning |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| independent_counterfactual | trend_following | 2226 | -1.00 | -6.32 | -11.74 | 38.9% | 0.62 | 48.36 | -50.01 | 11850.69 | 11850.69 | -210.72 | 2.93 | 0.99 | -0.94 | 1359 | 1 | 0 | — |
| independent_counterfactual | breakout | 16288 | -0.08 | -6.35 | -12.63 | 39.3% | 0.57 | 43.07 | -48.75 | 102072.59 | 102072.59 | -218.41 | 2.71 | 0.81 | -0.76 | 9807 | 1 | 0 | — |
| independent_counterfactual | momentum | 4809 | 1.41 | -2.42 | -6.36 | 41.2% | 0.78 | 55.98 | -50.09 | 18423.58 | 18423.58 | -519.80 | 4.72 | 1.38 | -1.20 | 2826 | 0 | 0 | — |
| independent_counterfactual | mean_reversion | 27969 | -0.75 | -7.64 | -14.52 | 37.9% | 0.51 | 39.48 | -47.51 | 192663.60 | 192663.60 | 154.21 | 2.45 | 0.65 | -0.74 | 16925 | 0 | 0 | — |
| executable_portfolio | trend_following | 17 | 1.04 | -4.44 | -10.09 | 41.2% | 0.65 | 45.46 | -48.98 | 93.27 | 93.27 | -2.77 | 2.19 | 0.69 | -0.78 | 10 | 0 | 5 | SMALL_SAMPLE |
| executable_portfolio | breakout | 6 | 12.50 | 8.01 | 3.96 | 50.0% | 1.16 | 56.28 | -48.36 | 26.89 | 26.89 | 2.55 | 9.50 | 1.39 | -0.92 | 3 | 0 | 0 | SMALL_SAMPLE |
| executable_portfolio | momentum | 10 | -9.81 | -13.81 | -18.01 | 30.0% | 0.47 | 54.27 | -48.99 | 40.05 | 40.05 | -1.94 | 4.33 | 1.14 | -1.26 | 7 | 0 | 4 | SMALL_SAMPLE |
| executable_portfolio | mean_reversion | 58 | 3.31 | -3.68 | -10.70 | 39.7% | 0.61 | 41.44 | -44.96 | 405.45 | 405.45 | -1.62 | 2.55 | 0.73 | -0.63 | 33 | 0 | 10 | — |

The independent counterfactual rows overlap and assume independent fixed-reference sizing; they do not share account cash or aggregate liquidity. Their summed P&L, win rate, or profit factor must not be interpreted as executable portfolio performance. The executable lane contains the account-constrained Phase 4 positions only.

## Predictive signal results

The source signal dataset contains 340 grouped label rows across strategy, decision-time regime, fixed confidence bands, direction, ensemble selection, stop-distance bands, momentum bands, and chronological windows. Fixed signal-label horizons are 1, 4, 16, and 96 candles. These use future candles only as outcome labels. Confidence intervals and sample counts are in `predictive_performance.csv`; groups under 30 observations or 30 occupied weeks are marked `SMALL_SAMPLE`. After-cost completed-trade slices by confidence, regime, direction, ensemble selection, stop distance, momentum and chronological period are in `trade_slice_performance.csv` (127 rows).
## Costs, entry timing, and robustness

Cost tables contain 60 rows: fixed-quantity one-factor cost re-marking plus preserved full cost-aware re-sizing scenarios. Zero cost is a hypothetical reference. Entry timing has 12 fixed-quantity delay scenarios; the original stop/target remains unchanged. All are counterfactual analyses, not altered historical results.

Weekly cluster bootstrap uses seed 20261118, 2000 replicates, four-week non-circular moving blocks, and 95% percentile intervals. See `bootstrap_intervals.csv`; fewer than 30 occupied trade weeks is flagged. Direct overlap and peak concurrency are in `trade_clustering.csv`: independent counterfactual trades overlap for 45424 trades and peak at 25 concurrent counterfactuals; the account-constrained portfolio peaks at 1. The 91-position executable lane has limited independent time clusters, so any interval is exploratory.

## Chronological controls

Splits use UTC signal/availability chronology. Earlier observations may seed causal warm-up features but are not scored. Forward outcome labels crossing a scored-window boundary are purged. Completed trades remain assigned to the signal-time window and are excluded from window metrics if their exit crosses its end. Every listed historical window was previously observed; none is the final holdout.

## Reproducibility and limits

Frozen input hashes, source commit, Phase 4 reference, methodology, and output hashes are recorded in `manifest.json`. Input hashes were checked before and after processing. Phase 4 reference: 91 closed positions, final equity 9051.510973339474987446164828 USDT, insolvency deficit 0.

MFE/MAE uses OHLC extrema over entry and exit candles; the order of high/low moves within a candle is unknown. Attribution is descriptive and does not prove that a signal, entry, exit, or regime caused the observed outcome. Sparse slices and block-bootstrap assumptions limit inference. No results are tuned or presented to make historical returns appear profitable.
