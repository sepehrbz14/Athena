# Athena 2.0 — Compounding Portfolio Simulator

## Phase 1: contract and parity fixtures

**Status:** Contract v1 frozen for implementation review. This phase does not extract or refactor execution code and does not implement portfolio execution.  
**Scope:** BTCUSDT only; one linear USDT instrument in the initial validation path. The model carries an instrument identifier so a later version can support multiple linear USDT instruments in one account.  
**Legacy baseline:** existing `crypto_bot.backtest.IntrabarBacktester`, `crypto_bot.paper.PaperOnlyRunner`, floating-point arithmetic, and paper/research outputs remain authoritative until later milestones pass their parity gates.

## 1. Decisions resolved for v1

| Topic | Frozen decision |
| --- | --- |
| Initial validation | BTCUSDT, 15-minute OHLCV bars, one instrument, linear USDT contract model. |
| Account | One cross-margin USDT account; position margin is an encumbrance, not a cash expense. No inverse contracts, collateral haircuts, or exchange-specific tiers in v1. |
| Same-time allocation | Collect selected signals with the same `available_at` as a batch. Allocate using an explicit configurable strategy-priority rank map, then `instrument_id` ascending, then numeric `signal_id` ascending. Missing strategy ranks sort after configured ranks and tie alphabetically. Accepted reservations are reflected before evaluating the next candidate in that batch. No signal is regenerated from earlier allocations. |
| Same-symbol concurrency | One net position and at most one live entry order per instrument. No pyramiding. Reject duplicate exposure, same-time duplicate selected signals, and opposing signal while exposure/order exists with distinct reason codes. An exit and a new entry for the same symbol at the same event time are processed in lifecycle order; the new entry may only be requested after the exit is complete and fills no earlier than its following open. |
| Funding | Retain current continuous elapsed-time approximation: `side.sign * quantity * close_mark * rate_per_8h * elapsed_hours / 8`. Positive rate is paid by longs and credited to shorts. This is not exchange settlement replay and may span candle gaps. |
| Arithmetic | Python binary64 floats, with no new tick/lot quantization. Sort all operations deterministically. Do not change existing engine math in this phase. |
| Liquidation | Later milestone adds configurable modeled maintenance-margin checks and forced liquidation. The result/event reason must distinguish modeled liquidation from stop/target strategy exits. It is not exchange-accurate liquidation. No maintenance percentage is frozen in Phase 1. |
| Signal generation | Consume frozen selected rows only. Do not recompute ensemble decisions or alter strategy parameters; do not read `forward_outcomes` for simulation decisions. |
| Execution implementation | Existing backtester remains unchanged in this phase. Later extraction must route both old and portfolio engines through one canonical execution implementation and pass single-position parity first. |

## 2. Canonical input and source validation

The source of record is the gap-parity independent signal capture at `reports/independent_signal_research_gap_parity_2026-11-05/signals.csv`, paired with its `run_metadata.json` and the exact candle file named there. That CSV contains raw component signals and an `ensemble_selected` flag; it is not itself a selected-only stream.

The ingestion contract for a later reader is:

1. Read the source signal CSV, source metadata, and candle CSV read-only. Compute SHA-256 over exact input bytes and store each digest, repository commit, worktree status, run configuration, schema version, and row counts in a new simulator manifest.
2. Validate the expected capture schema and strict boolean `ensemble_selected`; in the legacy CSV its canonical values are `True` and `False`. Normalize only `True` rows into `athena.frozen_signal.v1`. Exclude raw-but-not-selected rows before order creation and count them separately.
3. Require `instrument_id=BTCUSDT` in normalized rows. The legacy source has no instrument column; for this single-instrument source, BTCUSDT is supplied by the explicitly pinned input dataset/config, never inferred from a strategy name.
4. Validate positive finite prices, direction, confidence, unique source signal IDs and unique selected `(available_at, instrument_id)` rows. Duplicate/competing selected rows for BTCUSDT/time are rejected with `DUPLICATE_SELECTED_SIGNAL` before portfolio allocation; source file order does not resolve conflicts.
5. Require ISO-8601 timezone-aware UTC values. `available_at` must equal `candle_open_time + 15 minutes`; `reference_price` must equal the joined signal candle close under `abs_tol=1e-9 USDT, rel_tol=1e-12`; the next execution candle must exist for an entry request.
6. Validate the source signal file hash using the pinned run manifest. If the source metadata does not record the signal file's own hash (the existing capture metadata records source dataset and counts, not necessarily its output file hash), the new run manifest records the actual observed hash and the operator pins that exact digest before the run is accepted. A mismatch fails closed.
7. Never write selected flags, eligibility decisions, orders, fills or outcomes into source files. All new outputs go to a new run directory. The independent fixed-reference evaluator is not a portfolio input: it evaluates all rows as independent counterfactuals and does not share capital.

`schemas/athena2_frozen_signal_v1.schema.json` specifies the normalized record used at the simulator boundary. It intentionally excludes the source CSV's `forward_outcomes`, feature and raw-signal fields.

## 3. Versioned event and journal schemas

The schemas below are frozen as v1 contracts. They are declarative JSON Schemas only in this milestone; no event producer, account journal, or JSON Schema runtime dependency is added.

- `schemas/athena2_portfolio_event_v1.schema.json`: one append-only event envelope with schema version, stable ID, UTC event time, phase, account, optional instrument/source/order/position identifiers, reason code and typed event payload. `event_id` is deterministic from the canonical event key and payload, not wall-clock execution time.
- `schemas/athena2_cash_journal_entry_v1.schema.json`: one signed USDT cash movement linked to exactly one event and account. `cash_after_usdt` is a reconciliation aid. Entries are emitted in event order; no reserve/margin-only operation is a cash journal item.
- `schemas/athena2_frozen_signal_v1.schema.json`: normalized selected signal plus the signal and candle source hashes and BTCUSDT instrument identity.

Portfolio `event_type` and `phase` vocabulary, plus cash `entry_type` values, are closed enums in these files. Payload-specific fields will be specified by the emitting milestone before that event type is implemented; unknown envelope fields are rejected to avoid silent schema drift.

### Rejection reason vocabulary (frozen names)

`INVALID_SIGNAL`, `SOURCE_HASH_MISMATCH`, `SOURCE_SCHEMA_INVALID`, `TIMESTAMP_JOIN_MISMATCH`, `DUPLICATE_SELECTED_SIGNAL`, `DUPLICATE_EXPOSURE`, `OPPOSING_SIGNAL_WHILE_EXPOSED`, `MAX_POSITIONS_REACHED`, `KILL_SWITCH_ACTIVE`, `INSUFFICIENT_FREE_COLLATERAL`, `AGGREGATE_RISK_LIMIT`, `SYMBOL_EXPOSURE_LIMIT`, `INVALID_STOP_AFTER_GAP`, `NO_VALID_PRIOR_VOLUME`, `ORDER_EXPIRED`, `INSOLVENT_ACCOUNT`. Rejections preserve the source signal ID where available and do not mutate the frozen source row.

## 4. Cash journal, reservations and equity convention

### Cash movement convention

The journal models a linear USDT-margined contract. There is no principal exchange at entry: opening notional is represented by position quantity and mark-to-market P&L, while initial margin is encumbered from cash availability. Cash changes only for deposits/withdrawals, entry/exit fees, funding and realized gross P&L.

For ordered journal entries (J_k):

- `cash_0 = initial_deposit_usdt`.
- `cash_n = cash_0 + Σ amount_usdt[k]`.
- `equity_t = cash_t + Σ signed_quantity_i * (mark_i,t - average_entry_i)`.
- `gross_unrealized_pnl_t = Σ signed_quantity_i * (mark_i,t - average_entry_i)`.
- `realized_pnl_to_date = Σ REALIZED_PNL.amount_usdt`.
- `fees_paid = -Σ ENTRY_FEE.amount_usdt - Σ EXIT_FEE.amount_usdt` (a positive magnitude; fee journal movements are stored negative).
- `posted_funding = Σ FUNDING.amount_usdt` (positive means cash credit; negative means cash debit).
- Thus `cash = initial_deposit + deposits - withdrawals + realized_pnl - fees_paid + posted_funding`, where `posted_funding = Σ FUNDING.amount_usdt`; the direct sum of signed journal entries is authoritative.
- Trade net P&L remains `realized gross P&L + signed entry/exit fee movements + signed funding movements`. It reconciles to the total account cash change after all positions are closed, less deposits/withdrawals.

The event/journal is append-only. An entry fee is posted once on each fill. Funding is posted on each bar-close accrual. Realized gross P&L and exit fee are posted once at closure. Entry notional is never debited; margin reservation is never posted as a cash movement. `equity` therefore includes posted entry fees/funding through cash and adds only unrealized gross P&L; do not subtract those posted costs a second time.

### Margin and order reservation semantics

- `used_initial_margin = Σ abs(quantity_i * current_mark_i) / configured_leverage_i` for open BTCUSDT linear positions.
- `reserved_order_collateral = Σ (remaining_order_notional_estimate / leverage + estimated_entry_fee_reserve + explicit_cash_buffer)` for accepted, not-yet-filled entry remainder.
- `free_collateral = cash - used_initial_margin - reserved_order_collateral - other_explicit_cash_reserves`.
- Reservations are encumbrances only: they reduce free collateral but not cash or equity. Emit `MARGIN_RESERVED`/`MARGIN_RELEASED` account events when changed.
- At signal batch allocation, estimate order notional from the signal reference close, reserve risk budget and estimated initial margin plus the configured entry-fee reserve. At next open, revalidate against the actual slipped price and account state. If the actual requirement exceeds free collateral, reduce size to the allowed amount or reject; do not borrow against another order's reservation.
- A partial fill atomically moves the filled portion from reserved order collateral to used initial margin and posts its entry fee; keep reserve for only the remaining quantity. Cancel/reject/expire releases the remaining reserve. Closing releases used margin. No unit of quantity may be counted in both buckets.
- `PositionSizer` keeps its current stop-risk unit calculation. Portfolio entry quantity is additionally capped by remaining aggregate risk, free collateral, configured notional/exposure and current valid prior-candle liquidity. In v1, no exchange lot/tick rounding is introduced.

### Reconciliation tolerances

Use deterministic operation order and Python `float`. The ledger publishes `arithmetic_policy="python_binary64_v1"` and:

- trade ledger identity: `abs(delta_usdt) <= 1e-7` (matches current report reconciliation tolerance);
- account cash/equity identity: `abs(delta_usdt) <= 1e-6 + 1e-12 * max(abs(lhs), abs(rhs))`;
- joined price identity: `math.isclose(rel_tol=1e-12, abs_tol=1e-9)`;
- quantity/price fixture comparisons: `math.isclose(rel_tol=1e-12, abs_tol=1e-12)` unless the field is exact integer/string/timestamp data.

These are validation tolerances, not a rounding instruction. Report the actual delta and never silently round a failing balance into tolerance.

## 5. Exact event lifecycle and ordering

All times are UTC. Candle timestamps remain candle-open labels; decision availability is the open label plus 15 minutes. The core ordering key is `(event_time, phase_priority, configured_priority_rank, instrument_id, source_signal_id, local_sequence)`. Input CSV order has no effect.

For each candle interval, the portfolio lifecycle follows the current engine's causal order:

1. **Input / discontinuity:** validate the candle and source rows. On a data gap, emit `DATA_GAP`, synthesize no bars/prices, reset market feature history. Existing positions and pending orders persist in offline replay. A pending order cannot use stale/noncontiguous prior volume; it waits until a contiguous completed prior bar supplies liquidity.
2. **Market snapshot:** update observed bar and feature state. Signals from this close are not available until its close time.
3. **Observed open / pending entry fill:** execute eligible entry orders at the current bar open, using existing directional slippage and immediately prior contiguous completed candle volume. Size/reservations are validated against actual slipped price. Partial fills retain order identity and aggregate into the same position/trade in legacy compatibility mode.
4. **Carry:** apply the existing elapsed-time prorated funding to any open position using the current bar close as mark and current configured rate. This occurs before current-bar stop/target evaluation; even a position closed during this OHLC bar receives the current bar's full approximation. This conservative/approximate sequencing is frozen for parity; exact intrabar settlement is unknowable from OHLC.
5. **Stop/target evaluation:** test high/low. Stop wins if stop and target both touch in one bar. A long stop fills at `min(stop_price, bar.open)`; a short stop at `max(stop_price, bar.open)`, then exit-side slippage and fee apply. A target fills at the target price even if the bar opens through it; then exit-side slippage and fee apply. There is no synthetic path or sub-bar timestamp.
6. **Account mark and controls:** after exits, mark remaining positions to close. Apply posted cash fees/funding once and calculate unrealized gross/equity under the cash convention. Existing kill switch observes marked equity; it locks new entries only and does not force-close. Phase 1 does not implement maintenance liquidation.
7. **Signal batch at close:** consume normalized frozen selected rows with `available_at` equal to this decision close. Reject invalid/duplicate/opposing exposure per the vocabulary. Batch sort by configured strategy priority, instrument ID and signal ID; walk the sorted batch while atomically adding each accepted order's risk/margin reservation. Accepted entry order becomes eligible at the next observed bar open only.
8. **End of data:** no signal is created if no following execution bar exists. Cancel remaining entry remainders and release their reservations. With legacy backtest config `force_close_at_end=true`, close remaining positions at final candle close using normal exit slippage/fees after the bar's regular funding accrual. With paper semantics (`false`), leave position open and report mark equity separately from cash/realized equity.

### No pyramiding / duplicate exposure

For BTCUSDT v1, a selected entry row is rejected if a BTCUSDT position or unfilled BTCUSDT entry order exists. Multiple selected rows for the same symbol/availability time fail input validation as duplicates before portfolio allocation. An opposite-direction signal while exposed has `OPPOSING_SIGNAL_WHILE_EXPOSED`; same-direction signal has `DUPLICATE_EXPOSURE`. A future explicit reduce/close signal contract would be separate. Existing stop/target exits remain able to close the current position.

## 6. Golden parity fixture and regression gates

`tests/fixtures/portfolio_parity/legacy_stop_gap_funding_v1.json` is a four-candle synthetic case captured from the unmodified `IntrabarBacktester`:

- one long signal available at the first bar close, entry at next open;
- 5 bp per-side fee and slippage, elapsed-time prorated funding;
- adverse stop-gap execution at the observed open on the final candle;
- expected trade ledger fields and per-bar marked equity;
- expected stable diagnostic lifecycle event sequence.

The fixture must be treated as an input to regression tests, not regenerated automatically during tests. Any intentional contract change requires a separately reviewed fixture version and explanation; never overwrite v1 to silence a failing parity test.

Existing `tests/test_backtest.py` already covers next-open entry, fees, long/short stop gaps, stop-first collisions, partial fills, liquidity waits, funding, gaps and kill-switch behavior. Existing `tests/test_paper.py` covers paper-only inputs, missing/late/out-of-order rejection, idempotent duplicate replay, restart event stability, funding, fills, and marked equity. `tests/test_portfolio_phase1_contracts.py` additionally checks the versioned schema vocabularies and replays the fixed JSON golden twice, requiring exact diagnostic event bytes and matching trade/equity outputs.

### Phase 1 changed files and verification

| File | Change |
| --- | --- |
| `reports/athena_2_compounding_portfolio_simulator_phase_1_contract.md` | Frozen design decisions, source-validation rules, event/cash schemas, lifecycle, policies, tolerances, remaining deferred decisions and milestone boundary. |
| `schemas/athena2_portfolio_event_v1.schema.json` | Declarative versioned event envelope and event/phase vocabulary. |
| `schemas/athena2_cash_journal_entry_v1.schema.json` | Declarative USDT cash journal entry schema and signed amount semantics. |
| `schemas/athena2_frozen_signal_v1.schema.json` | Declarative normalized selected-signal schema pinned to BTCUSDT and source hashes. |
| `tests/fixtures/portfolio_parity/legacy_stop_gap_funding_v1.json` | Synthetic golden captured from the unmodified legacy backtester. |
| `tests/test_portfolio_phase1_contracts.py` | Schema vocabulary and deterministic golden replay regression tests. |

Verification on 2026-11-12: pre-change full suite **80 tests passed**; Phase 1 targeted contract suite **2 tests passed**; post-change full suite **82 tests passed** (`python -m unittest discover -s tests -v`). The existing backtest and paper regression tests all passed in the post-change run. No historical report, frozen signal file, or dataset was used as an output target or modified. No runtime/source implementation, execution extraction, strategy, parameter, paper-runner, or exchange-execution file changed.

## 7. Liquidation boundary

No liquidation implementation or threshold is part of Phase 1. In the later maintenance-margin milestone, the contract requires (a) a configurable modeled requirement for each open linear position, (b) portfolio mark equity and free-collateral check before new risk and at each bar mark, (c) deterministic liquidation priority and costs, and (d) distinct `MAINTENANCE_BREACH` / `FORCED_LIQUIDATION` events and `modeled_liquidation` exit reason. It must carry an explicit label that static OHLC replay cannot model exchange liquidation price, maintenance tiers, insurance fund, order-book depth or liquidation fees accurately.

## 8. Remaining unresolved decisions

The Phase 1 contract is complete for BTCUSDT parity fixtures. Items below are deliberately deferred and do not block this milestone:

1. Numerical maintenance-margin rates, liquidation fee, and liquidation priority across several open instruments. Set and test these in the later liquidation milestone, with user review before implementation.
2. Which additional linear USDT instruments will enter multi-asset scope and whether they share one global leverage setting or have per-instrument settings.
3. Whether the configurable strategy-priority map should be fixed per run or support dynamic priority schedules; v1 contract allows only a static map recorded in run config.
4. Longer-term source packaging/versioning for frozen signal files. Current inputs are pinned by file hashes in each simulator run manifest; no canonical immutable release bundle exists yet.
5. Whether future paper replay should use incremental account checkpoints or continue deterministic full replay. This does not change the current paper runner in Phase 1.

There are no unresolved Phase 1 choices for cash posting, reservation treatment, signal filtering, deterministic order tie-breaks, current bar/gap/funding semantics, stop/target ordering, or end-of-data behavior.

## 9. Phase boundary

Phase 1 creates contract documents/schemas and legacy-engine parity tests only. It does not add a portfolio simulator, change strategy selection/parameters, modify research or historical outputs, extract execution methods, alter paper behavior, implement maintenance liquidation, add AI/ML, or add exchange execution. Phase 2 begins only after explicit review and approval of this milestone.
