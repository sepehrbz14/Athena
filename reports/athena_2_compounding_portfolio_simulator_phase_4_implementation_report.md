# Athena 2.0 — Phase 4 Compounding Portfolio Simulator

**Implementation commit:** `d1e216b`\
**Configuration contract commit:** `830f1e6`\
**Status:** Local implementation and clean-checkout verification complete; GitHub Actions remains pending publication authorization.

## Architecture delivered

- `PortfolioConfig` reads the versioned, fixed BTCUSDT baseline and validates it against the additive Phase 4 configuration schema. No Phase 1 frozen schema was changed.
- `FrozenSignalReader` reads the selected-signal CSV without writing to it, checks pinned signal/candle SHA-256 values, checks source metadata counts, validates UTC chronology, joins exact candle-open times and closes, validates selected rows against the frozen signal schema, and produces one disposition for every raw row.
- `PortfolioSimulator` consumes only validated frozen signals. The batch allocator sorts by the frozen strategy priority, instrument, and numeric signal ID. Rejections and accepted/pending/filled signal states are retained in the result.
- `PortfolioLedger` remains the owner of financial entries, positions, realized/unrealized accounting, margin and cash collateral. Its new `resize_reservation()` operation supports partial fills without turning a reservation into a journal movement.
- All fills, costs, funding estimates, participation limits, stop/target collision behavior, and exit prices route through Phase 2 `BarExecutionModel`. The only addition there is an opening stop-gap helper used to process gap exits before new allocation.
- `PortfolioResult` exports the full journal, active/completed positions, dispositions, account events, order lifecycle, equity/exposure/margin curve, and reconciliation. Output events and cash journal rows are validated against the existing frozen Phase 1 schemas before JSON is written.
- `tools/run_portfolio.py` is a separate offline entry point. It writes only into the caller-selected new output directory; it is not connected to paper trading or an exchange.

The implementation keeps instrument-keyed state for extension, but this baseline has one allowlisted instrument and `maximum_active_positions=1`. It therefore does not exercise concurrent open positions. Pyramiding and Evolution Engine functionality remain disabled and absent.

## Frozen baseline and accounting rules

The fixed values are stored in `configs/athena2_portfolio_baseline_v1.json`: 10,000 USDT initial equity, 0.5% per-trade risk, 1.5% aggregate open-plus-pending stop-risk cap, 2× leverage and gross/net exposure ceilings, one active position, BTCUSDT only, no pyramiding, 1 USDT equity floor, 5% cash buffer, and one eligible-candle pending expiry. The config schema pins the values; they are not search parameters.

With current marked equity `E`, open positions `P`, and pending orders `O`:

```text
equity = cash + Σ(open-position signed gross unrealized P&L)
used_margin = Σ(open quantity × current mark / leverage)
free_collateral = equity − used_margin − Σ(active collateral reservations)
account_cash_buffer = 0.05 × max(equity, 0)       # subtracted once
risk_cap = 0.015 × max(equity, 0)
per_trade_cap = 0.005 × max(equity, 0)
available_trade_risk = max(0, min(per_trade_cap,
    risk_cap − open stop risk − other pending stop risk))
```

For each pending remainder `q`, the reservation is `q × slipped_entry / leverage + q × slipped_entry × fee_rate`. This reserves initial margin and the estimated entry fee only. Stop-risk is an independent limit, not a cash debit. Free order collateral is free collateral minus the single account-level buffer.

Pending and open stop-risk calculations include adverse canonical stop-exit slippage and fees. Entry sizing at the signal close is provisional. At the next observed open, the simulator marks existing positions at that open, recomputes equity, remaining stop risk, exposure and collateral, and reduces quantity to all applicable limits. It rejects and releases the order if the result is below 0.000001 BTC or the actual slipped entry invalidates the stop.

The execution model’s legacy funding amount is a charge (positive means paid); the ledger records signed cash movement (positive received, negative paid). The bridge is applied once: `ledger funding cash movement = -BarExecutionModel funding amount`. Funding keeps the elapsed-time prorated approximation, including its inability to reconstruct exchange settlement timestamps or intrabar settlement ordering.

At equity below 1 USDT, the account locks future entries and cancels pending orders. At equity at or below zero, it enters insolvency, releases pending reservations, and exits at the worst observed bar extreme through canonical slippage and fee execution; negative cash is preserved. This is not exchange-accurate maintenance-margin liquidation. Maintenance margin remains deferred.

## Event lifecycle

1. Validate candle chronology and register any data-gap event.
2. At the observed open, close positions whose stop was crossed by the gap. The opening-gap price is resolved by `BarExecutionModel`; pending remainder is canceled if its position closes.
3. Mark surviving positions at the observed open.
4. Process eligible pending orders in stable order. An eligible candle requires a contiguous prior candle and positive prior-volume participation. Gaps and zero participation do not consume expiry. Recompute actual-open sizing, fill through `BarExecutionModel`, atomically apply the entry and resize/release reservation, and cancel any remainder at that candle close after its first eligible attempt.
5. Post elapsed-time funding at bar close before intrabar stop/target evaluation. For fills opened during the bar, accrue only open-to-close elapsed time; carried quantity accrues from its prior funding timestamp.
6. Resolve intrabar stop/target exits through the shared conservative stop-first rule. If marked equity reaches zero at the worst observed extreme, apply the explicit insolvency exit policy.
7. Mark equity, margin, exposure, reservations and drawdown; update the kill switch and minimum-equity lock.
8. Gather the close-time frozen-signal batch and allocate the next-open order by the fixed priority.
9. At end of data, force-close positions and cancel orders according to the pinned baseline configuration, then reconcile and serialize outputs.

## Regression and replay results

- `python -m unittest discover -s tests`: **136 tests passed** on local Windows, Python 3.13.0.
- Clean source export of commit `d1e216b`: editable package install succeeded and the same full command passed **136 tests**.
- Historical backtest and paper behavior are covered by the existing golden and regression tests; no source under `backtest.py`, `research.py`, `research_evaluation.py`, or `paper.py` was changed.
- Phase 3 replay fixtures were not changed. The full test suite reproduced them.
- New `phase4_single_long_replay_v1.json` compares the complete result record, including all journal rows, positions, completed positions, dispositions, events, orders, balances and reconciliation.
- New synthetic parity test compares the Phase 4 single-position trade with `IntrabarBacktester` using matching sizing and execution assumptions: quantity, gross P&L, entry/exit fees, funding sign, net P&L and final equity reconcile within the test’s explicit floating-point tolerance.
- Focused tests cover both sides, positive and negative funding, next-open resizing, partial fills and expiry, stop/target collision, gap exits, identity collision, insolvency, reconciliation, report serialization, reservation resize and checkpoint/restart equivalence.
- The frozen research inputs were read and validated as-is: 232,608 candles, 51,292 raw signal rows and 28,757 selected eligible signals. The reader returned a disposition for every row. Hashes were checked against the actual file bytes. No source file was modified.
- Checkpoint resume replays the source prefix, verifies configuration/candle/signal hashes and a hash of ledger/orchestration state at the saved cursor, then proceeds deterministically. This favors exact reproducibility over fast deserialization of a mutable ledger snapshot.
- `git diff --check` and Python compilation passed before commit. The only untracked paths remaining in the workspace are pre-existing user artifacts under historical report directories; they were not staged or changed.

## Changed files

- Configuration and contract: `configs/athena2_portfolio_baseline_v1.json`, `schemas/athena2_portfolio_config_v1.schema.json`, `reports/athena_2_compounding_portfolio_simulator_phase_4_contract.md`, `tests/fixtures/portfolio_simulator/phase4_reservation_examples_v1.json`, `tests/test_portfolio_phase4_contract.py` (commit `830f1e6`).
- Simulator and validation: `crypto_bot/frozen_signals.py`, `crypto_bot/portfolio.py`, `crypto_bot/portfolio_models.py`, `crypto_bot/portfolio_ledger.py`, `crypto_bot/execution.py`, `pyproject.toml`, `tools/run_portfolio.py`.
- Tests and replay: `tests/test_frozen_signals.py`, `tests/test_portfolio_phase4_ledger.py`, `tests/test_portfolio_reporting.py`, `tests/test_portfolio_simulator.py`, `tests/test_execution.py`, `tests/fixtures/portfolio_simulator/phase4_single_long_replay_v1.json`.
- This report: `reports/athena_2_compounding_portfolio_simulator_phase_4_implementation_report.md`.

The existing `.github/workflows/tests.yml` already runs on pushes and pull requests on Ubuntu and Windows with Python 3.13, installs `.[test]`, and runs the full unittest suite (which includes frozen-schema, replay, accounting, and historical regression checks). No workflow change was needed. **No GitHub run links are available yet.** The attempted push of `codex/phase4-compounding-portfolio` to `https://github.com/sepehrbz14/Athena.git` was rejected by automatic approval review because it classified publishing the Phase 4 source and tests to that remote as an external action without explicit authorization to that destination. The branch is committed locally, but both CI jobs remain pending.

## Clean-checkout reproduction

On Python 3.13, from a clean checkout of `d1e216b`:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[test]"
python -m unittest discover -s tests
```

The same package installation and test command passed in a clean source export on this machine. GitHub Actions on both required operating systems is still required before final acceptance.

## Limitations and deferred work

- V1 is BTCUSDT-only with one active position. The shared account structures are instrument-keyed, but multi-instrument concurrency is not enabled or validated by this baseline.
- Checkpoint restart replays from the beginning to the saved cursor to prove state equivalence; it does not deserialize a persisted mutable ledger snapshot for fast continuation.
- Funding remains an elapsed-time approximation, and liquidation is only modeled for account insolvency at observed bar extremes. No maintenance-margin percentage is set.
- No live exchange execution, paper integration, signal regeneration, optimization, AI/ML, or Evolution Engine functionality was introduced.
- GitHub Actions results on Ubuntu and Windows are the remaining acceptance item. Phase 4 is not declared complete until those final-commit runs pass.
