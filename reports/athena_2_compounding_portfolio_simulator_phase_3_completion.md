# Athena 2.0 Compounding Portfolio Simulator — Phase 3 Completion

**Phase:** Account Ledger  
**Status:** Complete; awaiting review before Phase 4

## Architecture

`crypto_bot/portfolio_ledger.py` adds an isolated cross-margin USDT accounting component. It owns the append-only cash journal, open position accounting, mark-to-market equity, initial margin usage, order collateral reservations, and reconciliation invariants. It does not allocate signals or run a portfolio lifecycle.

Each execution fill is produced by the Phase 2 `BarExecutionModel`; the ledger accepts its `ExecutionFill` and does not reimplement fill prices, fees, or slippage. Entry fills create or increase a position using quantity-weighted average entry price. Exit fills realize gross P&L on the exited quantity, post the exit fee once, and remove the position only when its remaining quantity reaches zero. Funding is posted once when supplied by the caller.

Cash journal entries are frozen dataclasses, append-only in ledger operation, sequence-numbered, linked to a deterministic event ID, and carry the resulting cash balance. The initial cash deposit is journaled as the first entry. Reservations and margin are state encumbrances only and never enter the cash journal.

## Precision and equations

The frozen contract requires Python binary64 execution behavior. For accounting, external numeric values are converted using `Decimal(str(value))`; accounting operations then use Decimal without quantization. This avoids importing a float's full binary expansion into subsequent journal arithmetic, but it cannot restore precision already lost in the float execution layer. V1 schema serialization emits JSON numbers, converted to float at that boundary.

For each open position `i`:

```text
unrealized_gross_i = side_sign_i × quantity_i × (mark_i − average_entry_i)
used_initial_margin_i = quantity_i × mark_i ÷ leverage_i
equity = cash + Σ unrealized_gross_i
free_collateral = cash − Σ used_initial_margin_i − Σ active_order_reservations
```

Cash is the running sum of signed journal amounts. Entry fee and funding are posted directly to cash exactly once. They are not subtracted again from equity. Entry notional and margin reservations do not change cash. An exit posts gross realized P&L and the exit fee as separate entries. Partial exits realize only their closed quantity; remaining quantity stays marked as unrealized exposure.

**Example from the replay fixture:** Start with 1,000 USDT; buy 2 units at the canonical slipped fill price 100.1 with 0.2002 entry fee; post -0.5 funding; sell 2 units at slipped exit 109.89 with 0.21978 exit fee. Gross realized P&L is 19.58. The journal sums to final cash and equity of **1,018.66002 USDT**. With a 100 USDT reservation, cash remains 1,000 while free collateral becomes 900. Opening the example position uses 100.1 USDT initial margin at 2x leverage; closing releases it without a cash journal entry.

## Invariants and reconciliation

- Journal cash sum equals ledger cash exactly in Decimal arithmetic.
- Every entry carries the post-entry `cash_after_usdt`; timestamps must be timezone-aware and serialize in UTC.
- Journal entry records validate against the existing frozen `athena2_cash_journal_entry_v1` schema.
- Entry/exit fills require positive quantities and prices; exits cannot exceed open quantity.
- Position increases must retain instrument, side, and leverage.
- Reservations must be nonnegative, unique by order ID, and within free collateral; releasing a reservation changes no cash.
- Equity is calculated as cash plus gross unrealized P&L. `reconcile()` checks this identity and the journal balance.
- Margin is calculated at supplied marks; reservation-only operations without supplied marks use average entry prices as the deterministic mark for availability checks.

## Changed files

- `crypto_bot/portfolio_ledger.py` — Decimal ledger, immutable journal entries, positions, reservations, margin, mark/equity and reconciliation.
- `tests/test_portfolio_ledger.py` — deterministic ledger tests, frozen schema validation, and replay comparison.
- `tests/fixtures/portfolio_parity/account_ledger_replay_v1.json` — versioned ledger replay input and expected journal/balances.
- `reports/athena_2_compounding_portfolio_simulator_phase_3_completion.md` — this milestone report.

No Phase 1 schemas were changed. No historical report or frozen dataset was an output target.

## Replay and test results

The replay is executed twice and compared exactly against the fixture for cash, equity, journal entry types and amounts, open position count, and used margin. Partial entry fills aggregate at weighted average price; a partial exit leaves the remainder open; final exit removes it and releases margin. Funding, entry fees, exit fees, and realized P&L each produce individual journal movements. Reconciliation deltas are exactly **0 USDT** for both journal-to-cash and equity-to-cash-plus-unrealized checks in the replay and unit tests.

- New ledger-specific suite: **5 passed**.
- Full suite: **93 passed** (`python -m unittest discover -s tests -v`), including the existing 88 tests.
- The full run used a workspace-local `TEMP`/`TMP` directory to avoid the Windows sandbox cross-volume replacement issue in existing paper tests.

## Scope boundary

This milestone does not implement signal allocation, concurrent portfolio execution, dynamic compounding, liquidation, live exchange execution, or Evolution Engine functionality. Reservations are a ledger primitive only; portfolio policy for assigning them remains a later milestone. Funding amount calculation continues to be supplied by the shared execution layer under the existing elapsed-time approximation. The Phase 2 binary64 behavior remains the authoritative execution behavior.

No Phase 4 work has started. This milestone is ready for review.
