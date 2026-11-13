# Athena 2.0 — Phase 3.2 Ledger Atomicity and Closed Reconciliation

**Status:** Complete; awaiting review. Phase 4 has not started.

## Fixes

- Wrapped `apply_entry_fill()`, `apply_exit_fill()`, and `apply_funding()` in a ledger transaction boundary. Any exception restores cash, journal rows, position objects, reservations, journal sequence, event fingerprints/results, and completed-position records to their pre-operation state.
- Added `fill.slippage` to entry and exit event fingerprints. Slippage is validated as finite and nonnegative. Reusing an event ID with changed slippage raises a conflicting replay error. Identical exit replay remains idempotent after the position has closed.
- Added immutable `CompletedPositionAccounting` records at full closure. Each record captures total closed quantity, realized gross P&L, entry fees, exit fees, signed funding, and final net P&L. This record is accounting history only; it posts no cash and creates no journal movement.
- Extended `reconcile()` to independently compare each completed record's realized P&L, entry fees, exit fees, and signed funding with that position's journal rows, then verify `net = realized gross − entry fees − exit fees + signed funding`.

## Review correction: signed funding and rollback identity

Review found that signed funding cash movements were being subtracted as if `funding_paid` were always a positive debit. The canonical convention is now positive = cash receipt and negative = cash payment. Completed net P&L and reconciliation use `realized gross − entry fees − exit fees + signed funding`. Tests include multiple positive and negative funding events spanning partial exits.

The atomic snapshot now deep-copies the complete ledger object graph in one operation. This preserves shared object identity between an active position and a cached entry-event result when the snapshot is restored. The rollback test creates a position, injects an increase failure after mutation starts, verifies state and reference identity, then replays the original event and confirms it returns the active ledger position.

## New invariants

- A failed multi-step operation leaves all observable ledger state unchanged, including event registration.
- One fill event's canonical fingerprint includes quantity, execution price, fee, slippage, side, timestamp, and applicable position/order/trade identity.
- Slippage is finite and nonnegative for entry and exit fills.
- Each fully closed position has exactly one immutable completed accounting record and is absent from active positions.
- Completed records reconcile to their per-position journal entries; they never alter cash independently.
- Signed funding is applied once to cash and included once in trade net P&L with its cash sign: receipts increase net P&L and payments decrease it.
- Rollback restores alias relationships between `positions` and cached event results as well as values.
- Exact event replay returns the cached operation result; conflicting reuse fails without mutation.

## Tests and replay

- Injected failures after mutation begins: entry journal failure, funding journal failure, and second exit journal failure. All three verify unchanged cash, positions, journal, reservations, sequence, completed records, and event fingerprints.
- Slippage tests cover entry and exit fingerprint conflicts plus NaN rejection.
- Closed-position test checks the four gross/cost components and final net P&L against per-position journal rows, while confirming reconciliation adds no cash journal entry.
- Focused ledger suite: **12 passed**.
- Current focused ledger suite after review corrections: **14 passed**.
- Deterministic Phase 3 replay fixture: unchanged and reproduced exactly; the fixture file was not modified.
- Full suite at initial Phase 3.2 completion: **100 passed**. After signed-funding and reference-identity corrections, the final suite passed **102 tests**, preserving all prior tests. The workspace-local temporary directory workaround was used for Windows paper tests.
- `crypto_bot/backtest.py` and `crypto_bot/research_evaluation.py` were not modified; no historical reports or datasets were written.

## Files changed

- `crypto_bot/portfolio_ledger.py`
- `tests/test_portfolio_ledger.py`
- `reports/athena_2_compounding_portfolio_simulator_phase_3_2_atomicity_and_closed_reconciliation.md`

Phase 1 contracts/schemas and Phase 3 replay fixture are preserved. No Phase 4 work has started.

The unchanged `account_ledger_replay_v1.json` fixture was replayed twice during final verification. Both runs exactly matched the expected journal amounts/types, cash, equity, open-position count, and margin. The historical backtest and research source files have no diff.
