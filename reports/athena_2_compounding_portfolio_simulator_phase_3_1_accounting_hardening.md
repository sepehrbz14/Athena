# Athena 2.0 — Phase 3.1 Accounting Hardening

**Status:** Complete; awaiting review  
**Scope:** Ledger hardening only. No Phase 4 work started.

## Bugs addressed

1. **Collateral omitted unrealized P&L and silently marked at entry.** Removed implicit entry-price fallback. `free_collateral_at(marks)` now requires a positive current mark for every open position and returns `equity(marks) - used_initial_margin(marks) - reserved_collateral`. Reservation and withdrawal APIs require marks explicitly. Missing or zero marks fail closed.
2. **Financial event IDs could be replayed.** Financial operations now retain an event fingerprint and prior result. Replaying an identical event ID and payload returns the prior result without adding journal entries or changing balances/positions. Reusing an ID with a different event type or payload raises a conflicting replay error. A closure event that posts realized P&L and exit fee remains one idempotent event with two linked journal rows.
3. **Execution fills were insufficiently validated.** `ExecutionFill` now records the order side supplied by `BarExecutionModel`. The ledger checks positive finite price, quantity, and leverage; nonnegative finite fee; matching entry side; and opposing exit side. Invalid, non-finite and nonpositive marks/financial inputs are rejected. The execution model remains the sole fill-price/fee/slippage calculator.
4. **Reconciliation checked only cash and the equity identity.** Reconciliation now validates journal running cash, active position quantity/entry/leverage and finiteness, each open position's entry-fee/funding/partial-realization subtotals against its journal rows, finite journal totals by financial category, nonnegative reservations, required marks, used margin, and the resulting free collateral. It reports realized P&L, entry/exit fees, funding, margin, reservations, and free collateral.

## Defined behavior and invariants

- Exact duplicate event replay is idempotent; conflicting reuse of an event ID is an error and does not mutate ledger state.
- Each fill has a positive finite quantity and execution price, a finite nonnegative fee, and a direction consistent with its opening/closing position action.
- Every open position must have one explicitly supplied, positive current mark for equity, margin, free-collateral, or reconciliation calculations. Extra marks are accepted but ignored unless keyed to an open position.
- `equity = cash + Σ(side_sign × quantity × (mark − average_entry_price))`.
- `free_collateral = equity − used_initial_margin − active_reservations`.
- Reservations affect availability only; they never alter cash or journal entries.
- Journal running balances must equal the sum of signed journal amounts. Entry fees and funding are posted once and are not deducted again from equity.
- Long positions close only with short-side fills; short positions close only with long-side fills. Exit quantity must be positive and cannot exceed open quantity.
- Decimal conversion rejects NaN and infinities. Values are still converted with `Decimal(str(value))`; binary64 execution precision remains as documented in Phase 3.

## Changed files

- `crypto_bot/portfolio_ledger.py` — explicit mark requirements, current-equity collateral, event idempotency, fill/financial validation, and expanded reconciliation.
- `crypto_bot/execution.py` — `ExecutionFill` records its canonical order side so the ledger can validate entry and exit direction without duplicating execution behavior.
- `tests/test_portfolio_ledger.py` — adversarial replay, mark-loss, reservation, invalid-fill, long/short closure, and Decimal edge tests.
- `reports/athena_2_compounding_portfolio_simulator_phase_3_1_accounting_hardening.md` — this report.

The Phase 1 schemas and the Phase 3 `account_ledger_replay_v1.json` fixture are unchanged. The fixture still reproduces exact journal amounts, final cash/equity, and zero open positions on two independent replays.

## Adversarial and regression results

- Ledger-specific suite: **9 passed**.
- Deterministic replay fixture: both runs exactly matched expected journal entry types and amounts, cash **1,018.66002 USDT**, equity **1,018.66002 USDT**, zero open positions, and zero used margin.
- Reconciliation: zero journal-to-cash and equity identity deltas; position-level fee, funding, and realized P&L matched journal totals.
- Existing full suite retained: all 93 pre-hardening tests passed.
- Final full suite: **97 passed** (`python -m unittest discover -s tests -v`). The run used workspace-local `TEMP`/`TMP` for the existing Windows paper-test replacement constraint.
- Historical backtest and paper code paths were not changed. No historical reports, frozen signals, or datasets were written.

## Boundary

Signal allocation, concurrent portfolio execution, dynamic compounding, liquidation, and Evolution Engine functionality remain out of scope. No Phase 4 work has started.
