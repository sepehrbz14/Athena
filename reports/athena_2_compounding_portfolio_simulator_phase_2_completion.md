# Athena 2.0 Compounding Portfolio Simulator — Phase 2 Completion

**Phase:** Canonical Execution Extraction  
**Baseline commit:** `749a0db`  
**Status:** Complete; awaiting review before Phase 3

## Result

Execution policy now has one implementation in `crypto_bot/execution.py`. The legacy backtester delegates fill pricing, fees, slippage, participation limits, elapsed-time funding, stop/target selection, and trade closure to `BarExecutionModel`. The research evaluator uses the same policy for those calculations. Backtester orchestration, position sizing, and account/result bookkeeping remain where they were. No portfolio concurrency, dynamic equity sizing, liquidation, live exchange execution, or Evolution Engine code was added.

The legacy backtester APIs and `BacktestConfig` remain usable; `valid_prior_volume` remains importable from `crypto_bot.backtest` as before. The execution policy preserves the existing floating-point formulas and behavioral choices, including stop-first collision handling and elapsed-time prorated funding.

## Changed and added files

- `crypto_bot/execution.py` — new canonical execution policy and fill value object.
- `crypto_bot/backtest.py` — delegates existing execution calculations to the policy while retaining its orchestration and public configuration/API.
- `crypto_bot/research_evaluation.py` — delegates shared fill, funding, liquidity, and exit calculations to the policy.
- `pyproject.toml` — declares the optional `test` dependency on `jsonschema`.
- `tests/test_execution.py` — execution-policy regression and JSON Schema validation tests, plus paper replay baseline comparison.
- `tests/test_backtest.py` — historical baseline checks for key summary values, trade ledger digest, and all 3,000 equity points.
- `tests/fixtures/portfolio_parity/legacy_execution_cases_v1.json` — six synthetic golden cases captured against the pre-refactor engine.
- `tests/fixtures/portfolio_parity/pre_refactor_baseline_v1.json` — pre-refactor suite, historical backtest, and paper replay reference outputs.
- `tests/fixtures/portfolio_parity/pre_refactor_3000_equity_curve_v1.json` — historical 3,000-point equity reference curve.

The Phase 1 schemas and stop/gap/funding golden fixture remain unchanged. Schema tests validate actual records with Draft 2020-12 `jsonschema` validators, including valid examples and deliberately invalid examples for each schema.

## Regression baseline and comparisons

The baseline record was captured from commit `749a0db` before source extraction. It records the 82-test suite result, a 3,000-candle historical BTC backtest, the existing synthetic stop/gap/funding case, and deterministic paper replay output (summary, event IDs/types, artifact sizes, and SHA-256 digests).

The historical run uses the frozen configuration and dataset identity recorded in the baseline fixture. Pre-refactor and post-refactor scalar backtest results and closed-trade ledger digest match. The test compares all equity points to the captured curve at `rel_tol=1e-12`, `abs_tol=1e-7`, and maximum drawdown at `abs_tol=1e-12`. The six synthetic cases also reproduce within those documented numeric tolerances:

| Case | Behavior captured |
|---|---|
| `short_target` | Short position closes at target |
| `long_collision` | Stop and target touched in one candle; adverse stop wins |
| `partial_fills` | Entry participation caps, accumulated fills, and remainder cancellation |
| `funding_across_gap` | Funding accrues by elapsed time across a missing-candle gap |
| `stale_liquidity` | Stale prior volume blocks entry until fresh contiguous liquidity is available |
| `end_of_data_open` | Open position remains unclosed at end of data |

Paper replay output is compared with the pre-refactor record, including deterministic artifact hashes. No historical report or frozen dataset was overwritten. Repository tracked historical artifacts were checked for content diffs and were unchanged. Existing untracked historical report artifacts were left untouched.

## Verification

- Pre-refactor full suite: **82 passed**.
- Final full suite: **88 passed** (`python -m unittest discover -s tests -v`).
- The final run includes actual JSON Schema validation, execution golden cases, backtest baseline reconciliation, and paper replay output comparison.
- On Windows, `TEMP` and `TMP` were pointed at a temporary directory inside the workspace for the suite; this avoids a sandbox cross-volume `os.replace` failure in existing paper tests.

## Remaining decisions and scope

Phase 2 intentionally does not settle or implement portfolio allocation, concurrent positions, dynamic equity-based sizing, cross-margin reservation enforcement, modeled liquidation, multi-instrument scheduling, or live execution. Those remain future design/implementation work subject to the approved Phase 1 contract. The current elapsed-time funding method is retained for parity and remains an approximation of discrete exchange funding events. Floating-point arithmetic is retained; reconciliation uses the tolerances above.

No Phase 3 work has started. The branch is ready for review at this milestone boundary.
