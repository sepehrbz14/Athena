# Athena 2.0 — Compounding Portfolio Simulator

## Phase 0: repository audit and implementation plan

**Audit date:** 2026-11-12  
**Scope:** read-only inspection of the current repository; no source, parameter, research, or dataset changes were made for this audit.

## Executive summary

Athena already has a mature deterministic *single-position* OHLC backtester, an independent frozen-signal evaluator, reusable stop-risk sizing and safety controls, structured lifecycle diagnostics, and a restart-safe paper replay harness. It does not yet have a portfolio account ledger: the backtest keeps a single `Position`, represents realized account state as `closed_equity`, sizes one trade at a time, and does not debit/lock collateral or reserve cash for outstanding orders. The historical fixed-reference research evaluator deliberately gives every signal its own independent counterfactual trade and explicitly warns that its curve is not executable portfolio equity.

The simulator should therefore add a portfolio orchestration and account-ledger layer that reuses existing signal, execution policy, and control behavior through narrow interfaces. Execution math (fill price, fee, slippage, liquidity, stop/target/gap behavior) and cash accounting must each have one owner. In particular, the portfolio engine must not reproduce the existing execution rules in a second loop or treat per-signal independent-trade P&L as the portfolio ledger.

The repository contains no separate document describing an “approved Compounding Portfolio Simulator architecture.” This plan maps a recommended architecture to confirmed code and marks proposed choices and decisions still needing review. It does not infer an approval that is not recorded in the repository.

## 1. Current architecture and dependency map

```text
CSV OHLCV ── io.load_ohlcv_csv ── Candle
                                   │
                ┌──────────────────┴──────────────────┐
                │                                     │
     IntrabarBacktester                       research.capture_signals
     _IncrementalMarketData                    StrategyEnsemble.evaluate
     RegimeClassifier                          raw signals + selected flag
     StrategyEnsemble.decide                            │
                │                                signals.csv + metadata/hash
         _PendingEntry                                  │
                │                           research_evaluation.evaluate
      PositionSizer / RiskConfig              independent counterfactual trades
                │                           (not shared-capital portfolio P&L)
       one Position + closed_equity
                │
       fill / funding / exit methods
                │
       Trade + EquityPoint + diagnostics
          ┌─────┴────────────┐
     reporting.py        diagnostics.py

PaperOnlyRunner ── immutable warm-up + append-only prospective candle journal
     └── reconstructs IntrabarBacktester from scratch (paper-only; no exchange client)
```

### Main code responsibilities

| Concern | Existing location | Actual behavior and dependency |
| --- | --- | --- |
| OHLCV input | `crypto_bot/io.py` | Loads CSV to `Candle`; does not repair chronology or timestamps. Candle labels are UTC candle-open timestamps. |
| Market features | `crypto_bot/backtest.py::_IncrementalMarketData`, `crypto_bot/indicators.py`, `crypto_bot/regimes.py` | Incremental 15m/1h/4h state, mutable partial higher-timeframe buckets, ATR/EMA/regime. Resets market feature history after observed gaps without synthesizing bars. |
| Strategy selection | `crypto_bot/strategies.py::StrategyEnsemble` | Evaluates raw component signals; regime gates candidates, confidence sums decide side, opposing score can suppress decision, highest-confidence same-side candidate supplies the chosen signal. |
| Independent signal capture | `crypto_bot/research.py::capture_signals` | Captures all raw component signals regardless of portfolio state, records `ensemble_selected`, availability time, stops/targets, feature/gap metadata and forward outcomes. |
| Existing portfolio-like backtest | `crypto_bot/backtest.py::IntrabarBacktester` | Despite historical “portfolio” labels in some scripts, owns one open position and one pending entry maximum. It sizes at first executable open; cash is represented by closed equity plus marked P&L, not a cash/margin ledger. |
| Risk sizing | `crypto_bot/risk.py::PositionSizer` | Stop-distance plus configured round-trip cost sizing; caps notional and collateral. Leverage changes collateral capacity only, not risk budget. |
| Execution and trade accounting | `crypto_bot/backtest.py::_enter`, `_apply_slippage`, `_apply_funding`, `_intrabar_exit`, `_close`, `valid_prior_volume` | Fill, fee, slippage, partial fill, funding, stop/target, gap-through stop, and trade net P&L mechanics are embedded in the backtester. `valid_prior_volume` is already shared with independent research. |
| Safety gates | `crypto_bot/controls.py::KillSwitch` | Daily loss, peak drawdown, volatility, feed difference and exchange-error state. Backtest observes mark equity and blocks entries; it does not force-close an existing position. Feed/exchange methods are not wired to historical bar execution. |
| Diagnostics | `crypto_bot/diagnostics.py` | Structured signal/order/trade/event records, stable local IDs, summaries and CSV/JSONL export. Output schema is part of reproducibility expectations. |
| Reporting | `crypto_bot/reporting.py`, `crypto_bot/research_evaluation.py`, tools under `tools/` | Pure trade/equity calculations and frozen research reports. Independent research clearly states that overlapping trades do not share capital or aggregate liquidity. |
| Paper trading | `crypto_bot/paper.py`, `tools/run_paper.py` | Completed-candle, deterministic full replay from immutable warm-up and append-only prospective bars. Paper-only, safety-on, single instrument/strategy ensemble; no exchange order submission. |

### Existing limitations relevant to a portfolio

1. `BacktestConfig` has one account equity scalar. `_enter` receives `closed_equity`; an open position does not reduce cash or create a collateral liability. `INSUFFICIENT_CASH`, `MAX_POSITIONS_REACHED`, and margin rejection enum values are not evidence of an implemented account ledger.
2. One `Position | None` and one `_PendingEntry | None` are maintained in the run loop. The existing engine cannot hold concurrent instruments or reserve shared resources among pending orders.
3. `PositionSizer.size()` uses equity for risk, not current available cash or portfolio-level remaining risk. Its margin cap is `equity * max_margin_fraction * leverage`, suitable as a per-position sizing bound but not aggregate shared margin.
4. Existing funding is a continuous rate prorated by elapsed time and current candle close. It is not historical exchange settlement data. Funding is charged before the current bar’s stop/target check, so OHLC cannot establish exact funding/exit ordering inside a bar.
5. Partial entries aggregate into one position with VWAP and summed fees. The existing liquidity cap uses the prior completed contiguous candle’s volume. This policy and behavior need to be called once per portfolio fill, then aggregated across orders sharing an instrument.
6. Existing gaps do not create prices. Positions and pending entries survive in the historical backtester; after a gap, the next observed open is the next possible fill and the stop/target checks use that observed bar. This can produce gap losses beyond nominal stop risk. The paper runner instead rejects a missing interval and halts input.
7. Drawdown kill-switch behavior is an entry lock, not liquidation. A distinct maintenance-margin/liquidation rule does not exist.

## 2. Reuse, refactor, and create

### Reuse directly, behind tested interfaces

- `Candle`, `Signal`, `Side`, `Trade`, `EquityPoint`, `EntryRejectionReason`, and `SizingResult` as immutable input/result concepts; extend with portfolio identifiers only through backward-compatible fields or new portfolio-only types.
- `StrategyEnsemble` and the existing frozen selected-signal output as signal provenance, not as an execution/account engine.
- `PositionSizer`’s stop-risk equation and its independent leverage/notional intent. Add an account-aware constraint layer around it rather than silently changing historical sizing semantics.
- Existing fee/slippage, prior-volume liquidity policy, adverse stop/target collision rule, gap-through-stop pricing, `Trade` reconciliation identity, and timestamp conventions after extracting execution primitives.
- `KillSwitch` as an entry-control policy; adapt its equity input to shared account equity and keep forced liquidation separate from a kill-switch trip.
- `BacktestDiagnostics` event pattern and `reporting.py` pure report helpers where schemas remain compatible.
- `PaperOnlyRunner` journal, manifest/hash checks, replay and atomic output concepts for a future paper simulator adapter, subject to migration and compatibility tests.

### Refactor before portfolio orchestration

- Extract **one canonical execution-policy component** from `_enter`, `_apply_slippage`, `_apply_funding`, `_intrabar_exit`, `_close`, and prior-volume logic. It should accept order/position/candle inputs and emit fills, fees, funding estimates, and exit requests. `IntrabarBacktester` and the portfolio engine must call the same implementation.
- Extract the current single-position account calculations into a ledger/account boundary. Preserve old single-position outputs with golden regression fixtures before changing internals.
- Separate market feature/signal generation from account acceptance. This is needed so the frozen signal file can be replayed without strategy recomputation and so portfolio rejections never alter the signal dataset.
- Make signal ordering and simultaneous-event priority explicit; current serial candle iteration implies an order but has no multi-asset batch semantics.
- Add account-aware sizing inputs (equity, remaining risk budget, free collateral, existing exposure, pending reservations) without changing the standalone `PositionSizer` historical API by default.
- Define stateful control reset/persistence semantics for a continuous account before reusing `KillSwitch` in a portfolio engine.

### Create

- A portfolio/account ledger with cash, positions, open orders, reservations, margin, realized/unrealized P&L, exposure and insolvency state.
- A portfolio event model and deterministic dispatcher for multiple signals/orders/positions and stable tie-breaking.
- A frozen signal-stream reader/validator that only passes through ensemble-selected records and checks immutable source hashes/schema/time joins.
- Portfolio configuration/result schemas and additive portfolio reporting: account ledger, cash/equity/margin/exposure time series, order lifecycle, and reconciliation summaries.
- Golden parity fixtures and accounting/portfolio property tests. Avoid changing current reports’ files or running a recomputation into existing report directories.

## 3. Proposed file map

No files are changed in this Phase 0 audit. Proposed paths are staged and may be adjusted during design review.

| File | Change | Purpose |
| --- | --- | --- |
| `crypto_bot/execution.py` | New | Canonical, shared bar-level fill/fee/slippage/liquidity/funding/stop-target rules extracted from current backtester. |
| `crypto_bot/accounting.py` | New | Double-entry-like cash journal and derived account metrics; atomic fill, fee, funding, realized P&L, reserve and release operations. |
| `crypto_bot/portfolio.py` | New | Multi-position portfolio event loop, reservations, risk allocation, safety/insolvency handling and result assembly. |
| `crypto_bot/portfolio_models.py` | New | Account, position/order state, account snapshot, portfolio events, config, result and stable IDs. May remain in `models.py` only if it avoids cycles and keeps public concepts coherent. |
| `crypto_bot/signals.py` | New | Frozen selected signal CSV reader, schema/time validation, source manifest/hash validation, deterministic event ordering and read-only signal/candle join. |
| `crypto_bot/backtest.py` | Modify | Delegate fill/position mechanics to `execution.py` and preserve single-position API and output semantics during migration. |
| `crypto_bot/risk.py` | Modify | Add account-aware portfolio risk allocation interface or adapter; retain existing standalone sizing formula and defaults. |
| `crypto_bot/diagnostics.py` | Modify | Add portfolio/account event fields or portfolio-specific exporter without breaking existing CSV schemas or stable event meanings. |
| `crypto_bot/reporting.py` | Modify | Add portfolio account reconciliation and metrics while preserving current trade report calculations. |
| `crypto_bot/cli.py` / `tools/run_portfolio.py` | New or modify | Separate CLI entry point and artifact directory for the new simulator; current CLI stays reproducible. |
| `tests/test_execution.py`, `tests/test_accounting.py`, `tests/test_portfolio.py`, `tests/test_frozen_signals.py`, `tests/test_portfolio_reporting.py` | New | Narrow, deterministic tests per component and acceptance fixture. |
| `tests/test_backtest.py`, `tests/test_paper.py`, `tests/test_reporting.py` | Extend | Preserve legacy engine and paper/report behavior while routing through shared execution. |
| `reports/athena_2_compounding_portfolio_simulator_phase_0.md` | Added by this audit | Design artifact only; contains no results/data. |

## 4. Frozen ensemble-selected stream contract

The best existing input is the immutable signal capture from `reports/independent_signal_research_gap_parity_2026-11-05/signals.csv` plus `run_metadata.json`. It contains 51,292 raw strategy rows over 232,608 candles and an `ensemble_selected` boolean; the dataset metadata records a gap policy and source, while the fixed-reference evaluation metadata records hashes and explicitly describes its trades as independent counterfactuals. The full report’s selected-signal records may also be examined at review time, but do not treat its portfolio results as a canonical new input without verifying provenance.

Proposed portfolio input procedure:

1. Require explicit `signals.csv`, source metadata, and candle dataset paths. Read all inputs only; write new simulator outputs into a new run directory.
2. Verify SHA-256 for both input files against the run manifest; record actual code commit/worktree status, schema version, configuration and deterministic ordering in a new simulator manifest.
3. Parse booleans strictly and select rows where `ensemble_selected == true`. Do not re-run `StrategyEnsemble`, alter thresholds, derive replacement stops/targets, or use `forward_outcomes` as features. Reject malformed selected rows and duplicate selected `(timestamp, strategy, direction)` keys; report rejected input rows separately from portfolio risk/feasibility rejections.
4. Join `timestamp` to the exact signal candle and check `available_at == timestamp + candle_interval`, reference price matches that candle’s close within a declared tolerance, and following execution candle exists. Keep the current candle-open label and decision-at-close convention. Sort by `(available_at, instrument, signal_id)` using stable canonical types.
5. Preserve all frozen source bytes and existing reports. The simulator manifest records the source path, hash, signal count, selected-row count, excluded raw-row count, candle hash, date span, and filter rule. Never append portfolio eligibility, fills or outcomes back into the frozen research capture.
6. Baseline parity gate: when configured for the current one-position/one-symbol policy and matching settings, selected stream decisions should map to the same candidate signals as the current backtest; differences must be explained by source selection or execution/account policy, not hidden signal regeneration.

**Important:** `crypto_bot/research_evaluation.py::evaluate()` currently processes every input row and does not filter on `ensemble_selected`. Its per-signal ledger and parameters must not be reused as executable portfolio trades. The selected stream reader must implement the explicit filter independently and test it.

## 5. Exact event lifecycle

Use a deterministic event key `(event_time, phase_priority, instrument_key, source_signal_id, local_sequence)`. Process timestamps in UTC. For a bar-close decision timestamp, gather all eligible asset bars/signals for that timestamp before portfolio allocation; use stable sorting so input file row order cannot change fills. Do not let a same-timestamp signal observe another same-timestamp signal’s newly opened position except through the single documented batch-allocation policy.

1. **Input validation and capture:** validate candle chronology, time zone, duplicates, gaps and volume; load the frozen selected signal row(s). Signal event is immutable and available only at `available_at` (candle close). No account change.
2. **New bar open / existing stop gap:** for each open position, compare observed open to stop/target. A stop gapped through exits at the worse observed open vs stop according to existing side-aware rule; a target gap policy must be explicitly specified (recommend fill at observed open if more favorable, otherwise target). Apply exit slippage and fees through the shared execution component. Release margin and reservations; post realized P&L to cash once; close the position.
3. **Pending order execution:** process eligible next-open orders in stable order. Check kill state, signal freshness/stop validity at actual slipped entry price, prior contiguous candle volume, order remainder, shared free cash/margin, aggregate risk and exposure constraints. Execute at most available participation; partial fill retains the order ID and reservation for remainder. Insufficient resources reject/cancel deterministically and release reservation.
4. **Entry fill posting:** debit entry fees from cash; reserve initial margin and any pending order requirement; create/increase position with fill-level VWAP and aggregate fees. Margin is encumbered collateral, not an expense. Track each fill and its source signal/order/trade IDs.
5. **Intrabar risk exits:** evaluate every open position against current bar high/low after open fills. If stop and target both hit, choose stop as current code does. Apply exit price, slippage, fees, realize P&L, release margin, close and cancel that instrument’s unfilled entry remainder. Define portfolio-wide forced-liquidation priority before any optional target exit if equity breaches maintenance constraints.
6. **Close mark and carry:** accrue configured prorated funding using current policy and mark remaining positions to close. Record cash, unrealized gross P&L, unrealized net P&L, equity, used/reserved margin, free collateral, gross/net exposure and leverage. OHLC bars cannot identify exact within-bar exit/funding order; preserve the existing conservative timing policy for parity or adopt a separately versioned policy.
7. **Safety and insolvency observation:** update daily and peak equity references from the documented mark. A kill-switch locks new entries and cancels eligible pending entries per policy but does not itself liquidate. Independently evaluate maintenance margin/negative equity and enqueue forced liquidation as its own event.
8. **Close-time signal dispatch:** record/accept only frozen signals available at this close. Apply portfolio allocation in the deterministic batch order and create next-open entry requests/reservations. No signal may fill on its own decision candle.
9. **End of data:** cancel unfilled orders and release reserves. In research backtests, force-close at final close only if config says so; otherwise report open marked positions and distinguish cash/realized equity from mark equity. Paper replay must not force-close. Persist final ledger and reconciliation manifest in a new output directory.

### Tie-breaking and competition policy

Open design decision: with multiple symbols/signals at the same timestamp, first-come order is an accidental portfolio strategy. Recommended deterministic rule: batch eligible selected signals, compute proposed stop risk and required margin from a common pre-allocation account snapshot, then allocate by an explicit fixed policy (e.g. lexicographic instrument then signal ID, or confidence descending with stable tie-break) under shared caps. Freeze the chosen rule/config in the manifest. For exact temporal causality, all same-time orders execute at their next opens; no order gets account equity that includes unrealized same-bar fills unless the batch allocator explicitly models them.

## 6. Shared account and formulas

Use a single quote-currency cross-margin account for v1, with explicit per-position quantities and marks. Do not equate collateral reserve with cash expense.

For cash balance (C_t), signed positions (q_i) (positive long, negative short), average entry (p_i), mark (m_i), and separately tracked liabilities/fees already posted:

- `unrealized_gross_pnl = Σ q_i * (m_i - p_i)`.
- `cash` starts at initial deposit; entry/exit fees and funding are posted to cash when incurred; realized trade P&L is posted once on closure. If using unrealized entry-fee accrual instead, do not also deduct it from cash. Choose one ledger convention and enforce it.
- `equity = cash + unrealized_gross_pnl` (since posted charges are already reflected in cash). An equivalent reporting view may show unrealized net P&L, but must reconcile to this identity.
- `gross_notional = Σ abs(q_i * m_i)`; `net_notional = abs(Σ q_i * m_i)` for one common quote currency. Per-symbol exposure is reported separately.
- For linear USDT contracts with leverage (L_i), `initial_margin_i = abs(q_i * m_i) / L_i`. `used_margin = Σ initial_margin_i`; `reserved_order_margin = Σ margin required for accepted unfilled remainders`; `free_collateral = cash - used_margin - reserved_order_margin - other_cash_reserves`.
- `available_risk_budget` must respect both the configured per-entry risk and portfolio cap: `min(equity * risk_per_trade_pct, equity * portfolio_open_stop_risk_pct_cap - current_open_stop_risk - pending_stop_risk)`. Define stop risk against current quantity and adverse execution/cost assumption. If remaining budget ≤ 0, reject.
- Candidate unit risk retains the current intent: `u = abs(entry - stop) + entry * estimated_round_trip_cost_fraction`; `q_risk = max(0, available_risk_budget / u)`. Then cap by `q_notional = max_notional / entry`, aggregate margin capacity `q_margin = max(0, (free_collateral * L) / entry)`, symbol exposure, and liquidity. `q = min(q_risk, q_notional, q_margin, q_liquidity, q_exchange_rules)` with quantity rounding only when exchange constraints are modeled. The cost reserve must match the actual chosen fee/slippage assumptions.
- For fill quantity (Delta q), entry fee `fee_in = abs(Δq * fill_price) * fee_rate`; on close `realized_gross = q * side.sign * (exit_fill - average_entry)`, `fee_out = abs(q * exit_fill) * fee_rate`; net trade P&L = realized gross − entry fees − exit fees − funding paid. Funding credits may be negative “paid” under short/negative-rate cases, so name/sign semantics must be explicit.
- A pending order reservation is not a cash debit: reserve the worst-case initial margin plus estimated entry fees and any configured cash buffer; adjust reservation after each partial fill. Never count a filled position’s margin and the same filled quantity as pending reserve.

The simple leverage formula above is only appropriate for linear contracts with static leverage. Inverse contracts, tiered maintenance margin, exchange liquidation fees, isolated margin, cross-collateral haircuts and exchange settlement are outside v1 unless explicitly added as new instrument models.

## 7. Safety and insolvency rules

1. Entry kill switch: use account mark equity; preserve the current daily (3%) and permanent peak drawdown (10%) defaults unless a separately reviewed configuration changes them. A trip blocks new entries; cancel outstanding entries and release their reservations unless a specific policy says otherwise. The existing research observational mode remains research-only.
2. Portfolio limits: enforce max positions, per-symbol notional, total gross exposure/leverage, net exposure if configured, aggregate stop risk, used margin fraction, and free-collateral floor before each fill. Limits apply to filled plus reserved orders to prevent oversubscription.
3. Maintenance margin: v1 should include a configurable conservative threshold (for example equity versus maintenance requirement) and deterministic forced close order, but the threshold is not an exchange liquidation simulation. Report modeled liquidation separately from strategy stop exits.
4. Negative equity: never clamp an account to zero. Post actual modeled losses and allow negative equity in the ledger; set `insolvent=true`, permanently block all entries, cancel orders, force-close remaining positions at the worst available modeled bar price with normal costs, and preserve any residual negative balance as deficit. Emit one insolvency event and reconcile every cash movement.
5. Gap and stop overshoot: stop-risk sizing is an estimate, not a loss guarantee. A gap-through stop may lose more than the risk budget. Record planned stop risk, realized loss, overshoot, gap ID, and exit price. Do not generate synthetic prices or silently cap loss.
6. Data safety: duplicate/out-of-order candles and malformed signals fail closed. Missing bars reset feature state as current research capture specifies; open-position policy remains preserve-and-observe in historical simulation, while paper ingestion continues to halt on a missing interval.
7. Reproducibility safety: seed any stochastic reporting; simulator event processing itself should be deterministic and contain no randomness. Hash input data and configuration; emit outputs to unique/new destinations and never overwrite established frozen report folders.

## 8. Independently testable implementation milestones

Complexity is relative to this repository and assumes no exchange API integration.

| Milestone | Deliverable | Focused tests / acceptance gate | Complexity |
| --- | --- | --- | --- |
| **1. Contract and parity fixtures** | Freeze event schema, cash convention, signal filter, tie-break, gap/funding/end-of-run policies; create small synthetic golden fixtures from current engine. No behavioral refactor yet. | Tests prove old backtest trade rows, event order, timestamp conventions, cost identity and paper replay outputs remain stable on tiny fixtures; selected-row fixtures exclude raw non-selected signals; hashes detect any input-byte change. | **Small (2–4 days)** |
| **2. Canonical execution extraction** | Add `execution.py`; route current single-position backtester through shared fill, fee/slippage, volume, funding and exit rules while preserving APIs. | Table tests long/short slippage, stop/target collision, gaps through stop, target-gap policy, zero/stale volume, partial fills/VWAP, funding across normal intervals/gaps, fee and trade reconciliation; legacy golden parity. | **Medium (4–7 days)** |
| **3. Account ledger** | Add deterministic cash journal, positions, reserves and account snapshots; no multi-signal allocator yet. | Every operation reconciles cash and equity; entry fee/funding posting, partial fill reserve adjustment, margin release, close P&L, marks, cost signs, zero/negative equity; invariant/property-style bounded sequence tests. | **Medium (4–7 days)** |
| **4. Frozen signal ingestion** | Add read-only selected-signal stream adapter and manifest validation. | Hash/schema tampering rejection; strict bool filtering; exact timestamp/close joins; duplicate/missing selected events; stable ordering independent of file row order; no mutation of source bytes; future outcome columns never read. | **Small–medium (3–5 days)** |
| **5. Single-position parity on new stack** | Run a frozen selected stream through account + shared execution with one-position compatibility policy. This becomes the accounting bridge before concurrency. | Compare order/fill/stop/funding/trade P&L and marked equity to a reference backtest where streams/configs match; explain intentional differences; deterministic byte-identical reruns for canonical event and summary outputs. | **Medium (4–7 days)** |
| **6. Concurrent portfolio allocation** | Enable multiple positions/orders, common equity, portfolio risk/margin/exposure limits, deterministic batch allocation. | Two simultaneous signals cannot overspend cash/margin; reordering input rows gives identical output; conflicting same-symbol signals follow documented netting/rejection policy; per-symbol and aggregate liquidity; portfolio risk cap and order reservations hold through partial fills/cancels. | **Large (7–12 days)** |
| **7. Safety, liquidation, insolvency** | Add maintenance margin, forced liquidation and explicit insolvency transitions; connect kill switch to aggregate account. | Daily/drawdown locks block entries; controls do not silently close positions; maintenance breach liquidates in configured priority; gap losses can exceed stop risk; negative equity persists, entries stop and all orders/reserves clear. | **Medium–large (5–9 days)** |
| **8. Portfolio reporting and CLI** | New isolated runner/artifact format: ledger, fills/orders, positions, account curve, per-symbol and portfolio metrics, manifest. | Reconcile every event journal to final account, cash/equity series and trade ledger; open position final mark differs correctly from realized cash; no writes into legacy report directories; full reproducible rerun hashes match. | **Medium (4–7 days)** |
| **9. Paper replay integration review** | Optional later adapter allowing `PaperOnlyRunner` to replay the new portfolio engine from its journal. No exchange execution. | Restart/idempotency, atomic output, manifest mismatch and stale/gap rejection tests; old paper outputs remain unchanged on legacy path; accepted prospective bars reproduce deterministic account state. | **Medium (4–7 days)** |

Estimated engineering total through portfolio CLI: **33–58 engineer-days** including focused test design and review, excluding external data work and exchange-specific margin modeling. Paper integration adds about **4–7 days**. Milestones should be approved separately; no implementation is authorized by this Phase 0 deliverable.

## 9. Test strategy by property

- **Determinism:** same frozen bytes/config/code version produce identical sorted event records and numerical outputs; source CSV row order does not alter same-time allocation.
- **Ledger conservation:** beginning cash + all posted cash journal entries = ending cash; ending equity = ending cash + signed unrealized P&L; trade-level realized P&L + remaining marks reconcile with the account curve.
- **No double counting:** fees/funding posted exactly once; reserved margin is not equity expense; filled margin is removed from order reserve; position margin returns on close.
- **Causality:** signals unavailable before candle close; fills no earlier than following open; volume uses only prior completed contiguous bar; no lookahead from `forward_outcomes` or future signal rows.
- **Legacy compatibility:** current `IntrabarBacktester`, report APIs and paper harness continue to pass existing tests and synthetic golden output checks after extraction.
- **Risk/safety:** risk per entry and aggregate stop-risk cap hold at acceptance; no order bypasses kill/halt/insolvency; gap overshoots are represented rather than clipped.
- **Data integrity:** input hashes, timestamp joins, duplicates, gaps, malformed numbers/booleans and manifest/config mismatches are covered; all historical report/dataset inputs remain byte-identical.

Per developer instruction, this audit does not execute tests. The listed checks are for implementation milestones, not test results from this phase.

## 10. Known technical risks and unresolved design decisions

1. **Architecture approval artifact:** no approved simulator spec is present in repository. User review must settle the proposed model before Milestone 1 is treated as frozen.
2. **Exact frozen source:** select the authoritative signal capture/report and validate its final hash. The gap-parity capture is an apparent candidate; fixed-reference evaluation inputs include all component signals and are not a portfolio signal source by themselves.
3. **Portfolio meaning and scope:** one symbol with multiple strategies, or multiple symbols and instruments? This plan targets multiple linear instruments with a shared quote-currency account; exchange-specific contract classes are excluded.
4. **Same-timestamp competition:** choose batch allocation priority (fixed ID order, confidence, risk-adjusted score or another explicit rule). Do not use filesystem row order.
5. **Same-symbol positions:** choose netting/add-to-position/reject-opposite policy; define whether multiple selected rows per symbol/time can create pyramiding and how stop/target levels merge.
6. **Cash and margin convention:** approve cross-margin linear account formula, fee/funding posting convention, order reserve buffer, max total leverage and maintenance threshold. Static leverage alone does not reproduce exchange liquidation tiers.
7. **Funding:** constant prorated approximation is available; historical settlement rates/times are not in the candle dataset. Decide whether to retain parity behavior or add a separate funding dataset schema.
8. **Funding versus intrabar closure:** OHLC cannot identify exit instant. Existing code charges funding through the close before intrabar stop/target handling; changing this affects results and needs a versioned policy.
9. **Exit causality at bar open:** current engine fills pending entries at open before checking exits for the newly opened position. If stop is already crossed by a gap at entry, the later intrabar check exits in that same bar. Preserve or explicitly alter for portfolio parity.
10. **Data coverage and gaps:** repository includes reported discontinuities and historical dates already examined. Portfolio replay must retain disclosed gap behavior and cannot use stale input as a prospective paper stream.
11. **Floating-point reconciliation:** existing simulation uses Python floats and 1e-7-style tolerances; a shared portfolio ledger may need `Decimal` or documented quantization. Moving to decimal arithmetic can change existing reproducible reports, so isolate it or version outputs.
12. **Event volume/performance:** 232k candles and tens of thousands of raw signals are already routine. A complete per-event account journal can be large; require streaming/sorted aggregation or opt-in detail while preserving a deterministic summary.
13. **Identifier stability:** existing IDs are run-local incremental integers. Multi-asset concurrency requires stable composite provenance IDs or deterministic sequence assignment in sorted event order.
14. **Backtest and paper semantic coupling:** changing shared execution changes the paper runner’s replay output. Paper manifest config/schema versioning and compatibility behavior must be designed before routing the paper runner to new internals.
15. **Risk horizon:** stop risk ignores uncertain future funding, and stops can gap. The portfolio risk cap should be labeled planned stop risk, not guaranteed maximum loss.
16. **Testing/data protection:** do not regenerate reports in-place during implementation. Use temporary fixture directories, compare checksums before/after, and make legacy golden outputs read-only inputs.

## Phase 0 completion boundary

This deliverable records the repository audit, reuse/refactor/create map, proposed lifecycle/accounting/safety model, and milestone test plan. It does not change source code, strategy parameters, frozen signals, historical research results, or paper state. It does not implement a simulator, Evolution Engine, model-based decision layer, or live execution. Each implementation milestone remains subject to design review and explicit approval.
