# Athena 2.0 — Phase 5 Strategy Diagnostics and Validation Plan

**Status:** Approved implementation baseline after final repository and Phase 4 audit review.
**Prepared:** 2026-11-18
**Scope:** Research architecture, evaluation protocol, report contracts, and acceptance criteria.

## 1. Purpose and boundaries

Phase 5 will explain historical strategy outcomes and test whether the fixed, existing strategies and configuration behave consistently across time. It will not search for better parameters, create strategies, change the Evolution Engine, or convert hypothetical analyses into executable results.

Phase 4 is approved for closure under the user's direction, with its audit and reproducibility artifacts retained unchanged. The primary audit is [the 2026-11-14 financial audit](phase4_financial_audit_2026-11-14/PHASE4_FINANCIAL_AUDIT.md), its full replay is under `reports/phase4_financial_audit_2026-11-14/final_c97b39f/`, and the funding-timing addendum is [the 2026-11-18 report](phase4_financial_audit_2026-11-18.md). The Phase 4 manifest pins the frozen signal SHA-256 `0135f4f2…d9fc6f7`, candle SHA-256 `5a9a149c…40e1cf3d`, baseline config SHA-256 `bb7e286d…76aa8e859`, source commit `c97b39f`, 91 completed positions, final equity `$9,051.510973…`, and zero insolvency deficit. Preserve these paths and bytes; a new Phase 5 run must write to a new, versioned output directory.

## 2. Repository baseline and reusable components

| Existing component or artifact | Reuse in Phase 5 | Boundary or gap |
| --- | --- | --- |
| `crypto_bot/frozen_signals.py` and `schemas/athena2_frozen_signal_v1.schema.json` | Read-only, SHA-256-validated signals; exact candle join; UTC chronology; per-row disposition. | Normalized v1 deliberately excludes extra research fields. For every analysis, explicitly allowlist decision-time fields and exclude `forward_outcomes` and other future labels from features/decisions. |
| `crypto_bot/portfolio.py`, `crypto_bot/portfolio_ledger.py`, `crypto_bot/portfolio_models.py`, `crypto_bot/execution.py` | Phase 4 executable portfolio replay, accounting, order/fill lifecycle, canonical bar execution, signed funding bridge, and reconciled ledger. | Historical executable lane uses the pinned baseline and shared account constraints; do not reconstruct its results from independent-trade aggregates. |
| `crypto_bot/research_evaluation.py` and `tools/run_fixed_reference_research.py` | Frozen-signal independent counterfactual ledger, cost components, regime/month summaries, forward outcomes, and clustered uncertainty. | Its 51,292 trades are independent counterfactuals with overlaps and do not share capital/liquidity. This lane is not executable performance. |
| `tools/run_execution_cost_sensitivity.py` | Existing fixed cost grid `(0,0), (2,2), (5,5), (10,10)` bps per side, funding held fixed, per-scenario artifacts. | Current report is a portfolio of counterfactual signals. The zero-cost case is analytical only. Extend by strategy and isolate one assumption at a time; preserve these historical outputs. |
| `tools/run_signal_quality_cost_sensitivity.py` | Forward signal outcomes at fixed 1/4/16/64-candle horizons; decision-time regime and confidence grouping; block-bootstrap uncertainty. | Outcomes begin at signal-close reference price and are not trade-ledger P&L. Treat them strictly as labels and report separately. |
| `tools/run_chronological_windows.py`, `reports/chronological_windows/` | Chronological window mechanics, warm-up-only candles, independent resets, fresh per-window state, manifests, and boundary checks. | Existing periods are diagnostic only. Manifest explicitly says the 2025–2026-08-19 window was previously examined and is not an untouched holdout. |
| `crypto_bot/validation.py` | Basic rolling walk-forward window types and trade-order Monte Carlo. | The generic `walk_forward()` accepts an optimizer callback and slices candles, but does not itself enforce preprocessing, purge/embargo, final holdout secrecy, or parameter governance. Phase 5 must not invoke parameter search. Trade shuffling does not measure edge uncertainty. |
| `crypto_bot/diagnostics.py`, `crypto_bot/regimes.py`, `crypto_bot/strategies.py`, reporting and CLI modules | Existing observer-only diagnostics, regime taxonomy, strategy names, and report conventions. | Diagnostics do not yet emit a joined, versioned strategy attribution dataset or all requested trade-path excursions. |
| Existing completed ledgers | Phase 4 `completed_positions_decimal_v1.jsonl` supplies 91 selected, account-constrained completed positions; fixed-reference `completed_trade_ledger.csv` supplies 51,292 independent hypothetical trades with gross, execution gross, fees, slippage, funding, net, duration, regime, and exit reason. | They represent different populations and funding conventions. Retain separate schemas, denominators, and labels throughout. Existing ledgers do not provide completed-trade MFE/MAE. |

The four fixed strategy identifiers in the current evaluation are `trend_following`, `breakout`, `momentum`, and `mean_reversion`. The Phase 5 output must include all four even when an executable window has zero trades for one strategy; report zero count and undefined rates/expectancy where appropriate. The Phase 4 completed-position rows are compact: attribute strategy and signal through `position_id`/`order_id` to the preserved entry-fill event's `signal_id`, then join that ID to the frozen signal file. Join the close event on `position_id` for exit time/reason. Do not assume the compact row contains those fields.

## 3. Proposed architecture and new components

Keep Phase 5 as an offline research layer. Reuse the validated frozen-signal reader and canonical execution/portfolio engines; do not fork fill or accounting rules. Add the following components in a later implementation phase:

1. **Input and lineage manifest:** read-only hashes for frozen signals, signal metadata, candles, baseline config, schemas, source commit, runtime, and any regime/feature code; explicit lane (`executable_portfolio`, `independent_counterfactual`, or `signal_label`); row counts and time bounds; scenario parameters; deterministic seeds; clean/dirty source status. Output only to a new run directory.
2. **Completed-trade normalization and attribution:** a lane-aware reader that normalizes field names and cost signs without rewriting source files; joins each trade to its originating frozen signal and decision-time regime; records entry/exit timestamps, exit reason, stop distance/gap, entry timing, and reconciliation identity. Never combine lanes in a single metric.
3. **Excursion and decomposition engine:** calculate trade-path MFE/MAE from entry execution through the modeled exit using only the observed OHLC bars and directional extrema. Also retain a distinct signal-close forward-label view. Record candle-level ambiguity and gap/stop semantics; do not claim tick ordering from OHLC. Add descriptive stop-distance, stop-gap overshoot, favorable excursion surrendered before exit, and time-to-MFE/MAE fields. These are attribution fields, not optimization targets.
4. **Scenario runner:** independent per-strategy one-factor cost and entry-delay scenarios, with identical signals and declared sizing/capital policy within each comparison. Report dropped/invalidated trades and changed trade counts; scenarios are counterfactual and never rewrite frozen ledgers. This phase does not perturb or tune strategy parameters.
5. **Chronological evaluation controller:** fixed-config train/validation/walk-forward diagnostics and a separately sealed future holdout. It must enforce allowed-input boundaries, warm-up policy, purge/embargo, run isolation, and a holdout access log. No parameter selection is in scope.
6. **Stability and uncertainty module:** weekly/monthly panels, regime dependence, clustering and coverage statistics, and moving-block bootstrap intervals by strategy and lane. Preserve a fixed seed and method; include sparse-sample caveats.
7. **Report builder:** generate a human-readable Markdown report plus machine-readable JSON/CSV tables and a checksummed manifest. The report must state the lane and whether values are historical, executable, signal-label, or hypothetical on every table.

Suggested future files: `crypto_bot/strategy_diagnostics.py`, `crypto_bot/chronological_validation.py`, `tools/run_strategy_diagnostics.py`, and versioned output under `reports/strategy_diagnostics_<date>_<run-id>/`. Exact names can be finalized during implementation. This plan does not create any of them.

## 4. Evaluation methodology

### 4.1 Separate populations and estimands

- **Executable portfolio lane:** reproduce the pinned Phase 4 run as-is when needed for audit linkage. Measure account-constrained realized outcomes only from its completed-position and order/fill ledgers. Strategy attribution is conditional on entries accepted by that single account, kill switch, fixed risk caps, liquidity limit, and strategy priority. Rejections and kill-switch skips are reported as selection/censoring context, not assigned hypothetical P&L.
- **Independent counterfactual lane:** use the frozen signal set and existing completed-trade ledgers to study per-signal outcomes under the evaluator's declared fixed-reference sizing and costs. The trades overlap and have no shared cash or aggregate liquidity. Label all results “counterfactual research”; do not compare their compounded diagnostic curves to portfolio equity.
- **Signal-label lane:** use predeclared directional outcomes from the frozen signal dataset at fixed horizons. Measure directional performance and MFE/MAE from signal reference close. Do not call this entry/exit strategy profitability.

### 4.2 Strategy profitability decomposition

For each strategy, report the following in each applicable lane, with raw counts and per-trade values where useful:

- Gross expectancy before modeled costs; execution gross after slippage; net expectancy after fees, slippage, and funding. Define the exact gross convention for each source ledger so slippage is not deducted twice (Phase 4 realized gross already reflects execution prices; fixed-reference ledger separately exposes raw gross and execution gross).
- Win rate based on net trade P&L; profit factor based on positive versus negative net P&L; average net winner and average net loser; zero-P&L and undefined cases.
- Fee, slippage, and signed funding contribution in currency and per-trade terms. Normalize funding to a clearly named cash contribution; retain source-native fields and conversion formula in metadata because Phase 4 and research evaluators use different sign conventions.
- Holding duration; exit-reason mix; entry gap from signal reference; stop-gap exits and realized loss versus intended stop budget.
- Trade-level MFE/MAE measured from execution entry to modeled exit, and signal-label MFE/MAE separately at the frozen dataset's 1/4/16/96-candle horizons. The latter may use future candles only as outcome labels. Neither is an input to historical decision replay.
- Performance by decision-time regime, fixed confidence bands, direction, ensemble-selected status, gap/warm-up/feature-readiness markers, and deterministic signal characteristics (including stop distance and already-recorded decision features) plus calendar month/week. Show sample count and coverage before interpreting slices.

Attribution is descriptive. A loss decomposition can show that the observed net result reflects signal path, entry gap/timing, exit/stop policy, modeled fees/slippage, funding and market context. It cannot establish causality from observational historical trades alone. Any counterfactual changes to timing, costs, exits, or stops must be separately labeled and must not replace the pinned result.

### 4.3 Cost and entry-timing sensitivity

Run cost effects separately by strategy. Preserve the existing symmetric fee/slippage grid for comparability, and add one-factor grids that vary fees while holding slippage/funding fixed, vary slippage while holding fees/funding fixed, and vary funding while holding fees/slippage fixed. Include the canonical 5/5 bps and 1 bp/8h baseline. Keep zero cost as a mathematical reference only and mark it non-executable. Costs may change the risk-aware quantity, so publish both (a) fixed-quantity re-marking where only transaction debits are changed and (b) the evaluator's full cost-aware re-sizing, clearly distinguished.

Entry timing scenarios should delay an otherwise identical selected signal by a predeclared 1, 2, and 4 completed 15-minute bars. Report price drift, stop invalidation, missed/late entries, changed holding interval, cost, and resulting trade count. Define mechanically whether the original stop/target remains fixed and keep it fixed for the primary timing diagnostic. Do not optimize the delay or select the best scenario. Report “underlying signal path” from signal-close fixed-horizon labels separately from executed-entry trade results.

### 4.4 Chronological evaluation and holdout governance

All current repository history through **2026-08-19** has been examined in prior reports; it is not an untouched final test. Use it for fixed-config diagnostics and research development only. Reserve a genuinely future period that does not yet exist in the examined data as the final prospective holdout. Before it begins, publish or checksum a protocol that fixes its start/end rule, minimum duration/coverage, data source, and release criteria. Keep its outcomes inaccessible to analysis authors until the diagnostic specification and code/config hashes are locked. If new unseen data are unavailable, report “holdout pending”; do not relabel an examined window.

For historical chronology, use non-overlapping calendar blocks for descriptive diagnostics and rolling-origin walk-forward evaluation with one frozen configuration. A candidate development split is 2020–2022 development, 2023–2024 validation, and 2025–2026-08-19 historical diagnostic only; this is not an unbiased final acceptance sequence because those periods have been examined. Report each window independently; do not concatenate or compound window equity curves.

Controls for every split:

- Split by signal decision/availability time in UTC. Keep a trade and all its outcomes in one split. Purge boundary-crossing training observations whose holding path or outcome horizon enters validation/test; embargo at least the maximum outcome horizon and any longer modeled holding window before the next scored segment. Report purged counts and reasons.
- Reuse only earlier candles to warm indicators. Warm-up rows can seed deterministic trailing features/regime state, but produce no scored signal, trade, equity change, kill-switch observation, or metric. Reset portfolio, sizer, pending orders, and kill-switch state at each independent window unless the protocol explicitly studies state carry.
- Freeze feature definitions, regime mapping, strategy implementation, parameters, risk/execution configuration, and preprocessing before each scored block. No full-series normalization, imputation, scaling, regime thresholds, or feature selection. Any estimated transform must fit on train only and apply unchanged forward.
- Compute 1H/4H context from data available at the decision timestamp. Document developing-bar semantics and timestamp availability; never use later constituents, future labels, or exit outcomes in decision-time joins.
- Record exact inclusive/exclusive boundaries, warm-up interval, input hashes, code/config/schema hashes, purge/embargo details, and all exclusions. A validation-driven configuration change starts a new research version and cannot reuse that validation period as unseen test.

### 4.5 Robustness and uncertainty

- Weekly and monthly panels: return/P&L, expectancy, profit factor, win rate, trade counts, zero-trade periods, and losing-period fraction, separately per strategy and lane. Executable portfolio period returns use account equity; independent-trade panels use per-trade sums and are not portfolio returns.
- Regime dependence: group by the frozen signal's decision-time regime, not a regime label computed with future data; include strategy × regime, sample size, exposure and absent regimes. Avoid claiming stable regime edges from sparse cells.
- Trade clustering: report overlap fraction, peak concurrent independent counterfactuals, signal clusters, and trades per week/month. For uncertainty, resample intact calendar-week (and sensitivity-check monthly) clusters with moving blocks so nearby trades remain together. Do not rely on iid per-trade bootstrap or shuffled trade-order Monte Carlo as edge-confidence intervals.
- Bootstrap uncertainty: predeclare seed, iterations, block length, interval method, and resampling unit. Report intervals for expectancy, net P&L per trade, win rate, and profit factor where defined; expose sample/block counts. For the executable lane, resample time blocks or independent contiguous periods rather than treating 91 positions as exchangeable. State that bootstrap intervals quantify sampling uncertainty under the chosen resampling assumptions, not future guarantees.
- Parameter perturbation: after separate approval, define a small, predeclared local neighborhood around the existing parameters and measure degradation without choosing a winner. In the current approved Phase 5 scope, document the method and sensitivity dimensions only; do not change parameters or implement an optimization/search loop. Changes to strategy parameters/configuration require separate approval.

## 5. Report structure and artifacts

Each Phase 5 run directory should contain:

- `manifest.json`: lane, run ID, timestamps, source commit/worktree state, input/config/schema hashes, strategy/config identity, costs, seeds, boundaries, warm-up, purge/embargo, excluded records, code version, and output hashes.
- `report.md`: scope and classification; executive summary; lineage/reproducibility; strategy scorecards; decomposition; costs and entry timing; exit/stop diagnostics; weekly/monthly stability; regime results; uncertainty and robustness; chronological validation and holdout state; limitations and interpretation.
- `strategy_scorecard.csv`: one row per lane × strategy × window, including gross/net expectancy, count, win rate, profit factor, average winner/loser, fee/slippage/funding, duration, MFE/MAE, exit reasons, and regime coverage.
- `completed_trade_attribution.csv`: trade/signal identity, source lane, UTC timestamps, causal entry and exit prices, costs, signed funding, duration, stop/target details, MFE/MAE, regime, reason, and reconciliation checks. Derived artifact only; source ledgers remain unchanged.
- `cost_sensitivity.csv`, `entry_timing_sensitivity.csv`, `weekly_performance.csv`, `monthly_performance.csv`, `regime_performance.csv`, `bootstrap_intervals.csv`, and `split_audit.csv`.
- `holdout_protocol.json` and access/release record when prospective holdout is staged. Do not include protected holdout outcomes in development reports.

## 6. Acceptance criteria for a later implementation phase

1. Every input is read-only and hash-verified; all outputs are written to a new run directory. The Phase 4 audit and reproduction files retain their recorded hashes.
2. All four strategies appear in every scorecard with explicit empty/undefined values as needed. Executable portfolio, independent counterfactual, and signal-label metrics cannot be accidentally mixed; every table declares its lane.
3. Trade and aggregate P&L identities reconcile exactly within declared numeric tolerance; entry/exit fee, slippage, and funding signs reconcile to each lane's net P&L without double-counting execution slippage.
4. Replayed fixed configuration reproduces pinned historical outputs before any derived analysis is accepted. Reproduction failure blocks attribution publication.
5. MFE/MAE definitions, OHLC ambiguity, gap handling, entry/exit bounds, and horizon censoring are explicit and reproducible. Future labels never enter decisions/features.
6. Cost and timing scenarios retain the same frozen signal IDs, publish scenario exclusions and count changes, and are labeled hypothetical. Zero-cost output is never described as executable.
7. Split audits prove chronological ordering, warm-up-only inputs, no cross-boundary outcomes after purging, frozen preprocessing/configuration, and untouched holdout status. An examined 2020–2026 period cannot be accepted as the final holdout.
8. Weekly/monthly/regime tables include counts and sparse-sample context; uncertainty procedures are seeded and cluster-aware; the report separates edge uncertainty from order-sequence risk.
9. No parameter optimization, new strategy, or Evolution Engine code is included. Parameter perturbation beyond a written protocol waits for separate approval.

## 7. Decisions fixed for this implementation

The implementation uses a warning threshold of fewer than 30 independent time clusters (weeks for the primary bootstrap) or fewer than 30 observations in a displayed slice; this is a caution marker, not a claim that 30 observations prove reliability. Bootstrap uses 2,000 replicates, seed `20261118`, a four-week non-circular moving-block bootstrap, and 95% percentile intervals; outputs include occupied weeks and unique trade weeks. Cost scenarios use fixed-quantity one-factor re-marking as the primary isolation and the existing four-point symmetric fee/slippage grid as a separate full cost-aware re-sizing comparison. Confidence groups and signal-feature bins are fixed before scoring and not estimated from later periods.

Source already contains forward outcomes for 1/4/16/96 candles, so these are the signal-label horizons; the earlier 64-candle reference is superseded by the source's reproducible 96-candle field. Historical descriptive split labels are 2020–2022, 2023–2024, and 2025–2026-08-19, all marked previously examined. Purge at least 96 candles (one day) from any training portion before a scored boundary for label-based analysis, and purge any completed trade whose exit crosses its assigned split. These periods are not a final test. The prospective final holdout remains pending, and this implementation will not create or inspect future data.

Implementation is divided into four coherent stages: (1) lock lineage/split controls and reproduce Phase 4; (2) build lane-separated completed-trade and predictive signal scorecards; (3) add cost/timing, stability and cluster-aware uncertainty; (4) produce the consolidated report, run the full regression suite, and repeat deterministic artifact/hash/reconciliation checks. No strategy parameters, thresholds, adaptive behavior, strategies, Phase 6, Evolution Engine, or live/paper integration are in scope. No approval checkpoint is required between these stages. The report must state that the objective is to assess historical predictive and executable potential, not to make historical results profitable.

## 8. Implementation completion record

All four stages completed on 2026-11-18. The current-source Phase 4 portfolio replay reproduced the audited `portfolio_result_v1.json` and `portfolio_events_v1.jsonl` SHA-256 values exactly. Phase 5 produced separate executable, independent-counterfactual, and predictive-label outputs; populated zero-trade calendar weeks/months; attributed stop exits and risk-budget breaches; quantified overlap/concurrency; purged labels crossing chronological window ends; and stored a pending status for the future holdout. The full test suite passed 156 tests with the Windows test temporary directory placed on the workspace volume. A second Phase 5 run reproduced all 12 CSV output hashes, source code hashes, and input hashes. See `reports/phase5_strategy_diagnostics_2026-11-18/phase5_completion_report.md` and its authoritative run directory for results and limitations.
