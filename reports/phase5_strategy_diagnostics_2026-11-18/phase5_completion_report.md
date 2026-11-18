# Athena 2.0 — Phase 5 Completion Report

**Status:** Phase 5 research and validation implementation complete for review.
**Date:** 2026-11-18
**Authoritative run:** [run_20261118T120809Z](run_20261118T120809Z/report.md)
**Reproducibility evidence:** [phase5_reproducibility_verification.json](phase5_reproducibility_verification.json)
**Phase 4 replay:** `phase4_reproduction_current_6b6d3b9/`

## Scope completed

Phase 5 is an offline research milestone. The implementation did not change strategies, parameters, thresholds, adaptive behavior, historical result files, or the Phase 4 ledger. It did not start Phase 6, optimization, Evolution Engine work, or live/paper integration.

The report keeps three result types separate:

- **Executable portfolio:** 91 completed, account-constrained positions from the Phase 4 ledger, joined to its preserved fill and close events and then to frozen signals.
- **Independent counterfactual:** 51,292 per-signal trades with independent sizing and overlap, using the existing completed-trade ledger.
- **Predictive labels:** signal-close directional returns and excursions at 1, 4, 16, and 96 candles. Future observations are labels only; these are not executable P&L.

## Main findings

The Phase 4 account ends at **$9,051.51 from $10,000**, a loss of **$948.49**, with **zero insolvency deficit**. The current-source replay reproduced the preserved audit result and event stream exactly: `portfolio_result_v1.json` SHA-256 `f0111014caa1baf99aa9d3e4145eeba582ed99820d0e8f3502e8b66d543b539e`; `portfolio_events_v1.jsonl` SHA-256 `0dfd36a24813dc090fb579d102eef67762755dc779987daf6a666cf093f717e1`.

At the existing fixed-reference 5 bps fee / 5 bps slippage / 1 bp per 8 hours funding assumptions, the independent counterfactual lane has negative net expectancy for each strategy:

| Strategy | Trades | Net expectancy | Profit factor | Win rate |
| --- | ---: | ---: | ---: | ---: |
| Trend following | 2,226 | -$11.74 | 0.62 | 38.9% |
| Breakout | 16,288 | -$12.63 | 0.57 | 39.3% |
| Momentum | 4,809 | -$6.36 | 0.78 | 41.2% |
| Mean reversion | 27,969 | -$14.52 | 0.51 | 37.9% |

These trades overlap and do not share account cash or aggregate liquidity. Their total P&L is not an executable portfolio result.

The executable Phase 4 strategy counts are sparse: trend following 17 trades, breakout 6, momentum 10, and mean reversion 58. Breakout’s positive `$23.74` contribution comes from only six positions and is marked small-sample; the other strategy contributions are negative. The 91-position total reconciles to the Phase 4 account loss. Strategy-level values are attribution of a single constrained portfolio, not standalone strategy portfolios.

There were 10, 3, 7, and 33 stop-loss exits respectively for trend following, breakout, momentum, and mean reversion. No executable position was marked as an opening-gap stop, consistent with the Phase 4 audit. Net losses exceeded the reserved initial stop-risk amount in 5 trend-following, 0 breakout, 4 momentum, and 10 mean-reversion positions. These counts are descriptive of realized paths and account sizing; they are not evidence that a different stop setting would improve results.

Direct overlap and peak concurrency diagnostics are in `trade_clustering.csv`. The independent counterfactual lane has overlapping hypothetical holding intervals; executable concurrency is account-constrained by the Phase 4 baseline.

Across all strategies, 45,424 of 51,292 independent counterfactual trades (88.6%) overlap another trade interval, with peak concurrency of 25. The executable account has no overlapping positions and a peak of one. This confirms that independent counterfactual aggregate dollars cannot be read as a portfolio return.

The 96-candle (24-hour) predictive-label slices are mixed. Breakout direction accuracy is 46.9%; mean reversion is 52.8%; momentum is 50.3%; and trend following is 46.9%. Weekly-block confidence intervals for mean directional return include zero for all four strategies. The accuracy interval is above 50% for mean reversion and below 50% for breakout, while momentum and trend following intervals include 50%. This does not establish robust profitability: the forward-label series overlap, there are many tested slices, and the data are previously observed.

## Research and reproducibility controls

- The Phase 4 ledger and independent trade ledger remain separate. Each scorecard/table labels its lane and uses lane-specific gross and funding conventions.
- The frozen signal SHA-256 is `0135f4f2329303662ffcb8d63a4d8aa954cde77cfb65accc51c815de5d9fc6f7`; the candle SHA-256 is `5a9a149c5e35cd543280ca432daacf05fe3842ff063358df3d386e8140e1cf3d`. The Phase 4 config and all input/output hashes are recorded in the authoritative run manifest.
- The Phase 5 manifest records source commit `6b6d3b9e64c80ce99d284b76f86bc79126a09dd5`, dirty-worktree state, source-file hashes, input hashes, scenario configuration, seed `20261118`, 2,000 bootstrap iterations, four-week moving blocks, methodology, and output hashes.
- A second run over identical inputs and source code produced identical SHA-256 values for all 12 CSV outputs. Input and source-file hashes also matched. The verification record includes every table hash; duplicate table files are not needed to reproduce the comparison.
- All inputs were hash-checked before and after the run. Position-to-signal/event joins and per-trade net P&L identities passed. Aggregate executable P&L matches the Phase 4 account result within `1e-7`.
- The final full regression suite passed **156 tests**. The first default Windows sandbox run encountered temporary-directory `os.replace` errors; rerunning with `TEMP` and `TMP` on the workspace volume passed the entire suite.

## Chronology and uncertainty

The data end on **2026-08-19** and have been examined in previous research. The 2020–2022, 2023–2024, and 2025–2026-08-19 rows are historical diagnostics, not an untouched test. Phase 5 reads decision-time features from the frozen causal signal capture; it does not regenerate or refit them. Signal labels crossing a window’s end are excluded from that chronological window; trades exiting after the assigned window are excluded from its scorecard. Earlier candles may seed the original causal features but are not scored as observations in a later window. A future sealed holdout remains **pending**.

Weekly and monthly panels include zero-trade periods. The report marks groups below 30 observations or 30 occupied weeks. This is a warning threshold, not a guarantee of statistical reliability. Strategy-level predictive intervals use weekly moving-block resampling; subgroup Wilson intervals are marked as marginal because overlapping labels weaken their independence assumption. The executable lane has only 1–3 occupied trade weeks per strategy in this run, so its bootstrap intervals are exploratory and not reliable evidence.

Cost sensitivity isolates fee, slippage, and funding one at a time at fixed quantity, and separately includes the preserved full cost-aware re-sizing scenarios. Entry-delay scenarios retain original stops/targets and fixed quantity; counts change when an entry becomes invalid or occurs after the original exit. Every scenario is hypothetical. Zero-cost results are not executable performance.

MFE/MAE uses OHLC extrema over entry and exit candles, so intrabar ordering is unknown. These diagnostics describe historical paths; they do not identify causal sources of loss or guarantee future performance. No result was selected to improve historical profitability.

## Artifacts

The [authoritative run report](run_20261118T112013Z/report.md) summarizes the scorecards. Its directory contains the reproducible manifest and:

- `strategy_scorecard.csv`, `trade_slice_performance.csv`, `regime_performance.csv`
- `completed_trade_attribution.csv`
- `predictive_performance.csv`
- `cost_sensitivity.csv`, `entry_timing_sensitivity.csv`
- `weekly_performance.csv`, `monthly_performance.csv`
- `trade_clustering.csv`
- `bootstrap_intervals.csv`, `split_audit.csv`

The research-plan implementation record is in [the approved Phase 5 plan](../athena_2_phase_5_strategy_diagnostics_validation_plan.md). All new outputs are under this dated Phase 5 report root. Frozen inputs, previous historical reports, and pre-existing experiment outputs were not changed.
