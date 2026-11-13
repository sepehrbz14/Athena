# Athena 2.0 — Phase 4 Portfolio Configuration Contract v1

**Status:** Frozen engineering baseline; not an optimization target. This contract is additive to the frozen Phase 1 event, cash-journal, and frozen-signal schemas. The baseline is stored in `configs/athena2_portfolio_baseline_v1.json` and validated by `schemas/athena2_portfolio_config_v1.schema.json`.

## Scope and fixed baseline

- Starting cross-margin equity: **10,000 USDT**.
- Market: linear USDT **BTCUSDT**, 15-minute candles. The implementation may use instrument-keyed collections, but the v1 allowlist admits only BTCUSDT.
- Risk per trade: **0.5% of current marked equity**.
- Aggregate open plus pending stop risk: **1.5% of current marked equity**.
- Leverage: **2×**.
- Maximum gross exposure: **2× current equity**.
- Maximum net exposure: **2× current equity**.
- Maximum active positions: **1**; pyramiding disabled; at most one live BTCUSDT entry order.
- Minimum equity for new risk: **1 USDT**. Equity below 1 locks entries and cancels pending orders. Equity at or below zero declares permanent insolvency and invokes the insolvency close policy below; negative cash is preserved and never clamped.
- Cash buffer: **5% of current marked equity**, held once at account level, not once per order.
- Minimum BTC quantity: **0.000001**, retaining `RiskConfig.min_quantity`.
- Pending expiry: one eligible candle, with eligibility defined below.
- Existing kill-switch defaults remain 3% daily loss and 10% maximum drawdown. Existing execution defaults remain 0.05% fee, 5 bps slippage, 0.0001 funding per 8 hours, 5% prior-volume participation, and force-close-at-end enabled for offline backtest parity. These settings are pinned to the run manifest and are not tuned against the frozen dataset.
- Same-time priority is fixed to the lexical strategy order `breakout`, `mean_reversion`, `momentum`, `trend_following`; subsequent tie-breaks are instrument ID ascending and numeric source signal ID ascending. Duplicate selected `(available_at, instrument_id)` rows are rejected before allocation, so BTCUSDT v1 normally has no same-symbol tie to allocate.

The exact machine-readable values are the JSON config and its schema. A change to a `const` field requires a new configuration version and review; it must not be selected by historical performance.

## Marked-equity and risk basis

At each decision point, require current marks for all open positions and calculate:

```text
E = PortfolioLedger.equity(current_marks)
cash_buffer = 0.05 × max(E, 0)
risk_cap = 0.015 × max(E, 0)
per_trade_cap = 0.005 × max(E, 0)
available_trade_risk = max(0, min(per_trade_cap, risk_cap - open_stop_risk - other_pending_stop_risk))
```

Open stop risk is the equity loss from the current mark to a stop exit, including adverse exit slippage and the exit fee; entry fees and signed funding already posted to cash are not counted again. Pending stop risk is estimated from the reference close to the stop, with adverse entry and exit slippage plus entry and exit fees. The open and pending risk totals are recomputed after marks, fees, realized P&L, or funding change equity. The 1.5% cap therefore scales with current equity; it is not a fixed dollar budget. Unrealized P&L affects the cap through `E` and through the open position’s mark-to-stop risk.

For side `s` (`+1` long, `−1` short), reference raw price `P`, stop raw price `S`, fee rate `f`, and the existing `BarExecutionModel` slippage function:

```text
P_entry_est = slipped_price(P, entry_side)
P_stop_est  = slipped_price(S, exit_side)
pending_stop_risk_per_unit = max(0, s × (P_entry_est − P_stop_est))
                           + f × (abs(P_entry_est) + abs(P_stop_est))
pending_stop_risk = remaining_quantity × pending_stop_risk_per_unit
```

For an open position of quantity `q` at current mark `M`:

```text
open_stop_risk = max(0, q × s × (M − P_stop_est)) + q × abs(P_stop_est) × f
```

Sizing is capped by the minimum of per-trade/aggregate remaining stop risk, remaining gross and net exposure, available collateral, and canonical execution participation. No quantity rounding is introduced beyond the existing 0.000001 BTC minimum.

## Reservation and cash-buffer formula

Risk reservation and collateral reservation are separate constraints; stop-risk dollars are never also debited as margin. For each pending order remainder `q_r`, leverage `L`, and estimated slipped entry `P_entry_est`:

```text
order_margin_reserve = q_r × abs(P_entry_est) / L
entry_fee_reserve    = q_r × abs(P_entry_est) × fee_rate
order_reservation    = order_margin_reserve + entry_fee_reserve
account_cash_buffer  = 0.05 × max(E, 0)                 # held once
free_for_orders      = ledger.free_collateral_at(marks)
                       − account_cash_buffer
```

An order is admissible only if its reservation fits `free_for_orders` and its stop risk fits the remaining risk cap. Reservations reduce available collateral but do not change cash or equity. The buffer is not included in each order’s reservation, avoiding multiplication by the number of orders. With v1’s single position/order, it remains an account-level floor.

After a fill, the filled quantity leaves pending stop risk and pending margin/fee reserve; its actual fee is posted once and used margin is calculated from the resulting position and current mark. The remaining order reserve is recomputed from its remaining quantity and current equity. Entry fill, fee posting, reservation resize/release, and order state transition must be one atomic ledger operation. A partial fill cannot temporarily free the unfilled quantity’s reservation or count filled quantity as both used margin and pending reserve.

## Actual next-open price and partial-fill policy

The signal close supplies the initial size/reservation estimate. At the next open, obtain the actual canonical `ExecutionFill` from `BarExecutionModel`, then recompute stop risk, fees, exposure, and collateral from its slipped fill price. If the acceptable quantity is smaller, reduce the order to the greatest quantity satisfying every cap. If that quantity is below 0.000001 BTC, reject the fill and release the order. If slippage makes the stop invalid relative to the actual fill (`long stop >= entry fill`, `short stop <= entry fill`), reject with `INVALID_STOP_AFTER_GAP` and release the reserve. Participation-limited partial fills retain the order identity through that candle; any remainder is cancelled at the candle close after its first eligible attempt.

## Expiry and data gaps

An accepted order becomes eligible at the next observed candle open. An **eligible candle** is one for which `valid_prior_volume()` confirms a contiguous prior 15-minute candle and `BarExecutionModel.participation_quantity(prior_volume)` is positive. A missing/noncontiguous prior candle or zero participation capacity does not consume the one-candle expiry; the order and its reservation wait for the first eligible attempt. On that attempt, fill up to the risk/collateral/exposure/participation-limited quantity, then cancel any remainder at the same candle close and release its reservation. If the end of data arrives first, cancel and release without manufacturing a fill. This expiry rule is portfolio-v1 policy; it does not change the legacy backtester’s pending-order behavior.

## Funding sign bridge

The Phase 2 `BarExecutionModel` returns the legacy funding charge convention: a positive value is subtracted by legacy trade P&L as funding paid; a negative value increases legacy net P&L as a receipt. The Phase 3 journal convention instead records **positive cash received** and **negative cash paid**, and completed net P&L adds that signed movement. Therefore the only valid bridge is:

```text
ledger_funding_cash_movement = −legacy_funding_charge
```

For positive funding rates this produces a debit for longs and a credit for shorts. Negative rates reverse those directions. Use the existing elapsed-time prorated estimate at the candle close before stop/target evaluation. It remains an approximation, may span candle gaps, and is not exchange settlement replay. Add parity tests proving legacy trade net P&L is unchanged while ledger cash and completed net P&L reconcile under both signs.

## Minimum equity and insolvency

If marked equity falls below 1 USDT, permanently lock new entries and cancel pending orders; do not silently liquidate open positions solely because of this floor. If equity reaches or falls below zero, set `insolvent=true`, permanently block entries, cancel/release pending orders, emit the frozen insolvency event, and close remaining positions at the worst observed price available in the current bar (low for a long exit, high for a short exit) through `BarExecutionModel` with normal slippage and fees. Preserve resulting negative cash/equity and reconcile every movement. This is an explicit modeled insolvency policy, not exchange-accurate maintenance-margin liquidation. Phase 1’s configurable maintenance-margin mechanism remains a separately versioned modeled feature; no maintenance percentage is introduced by this baseline.

## Preservation boundary

The baseline config is not applied to historical research in-place. Phase 4 reads the frozen signal and candle inputs and writes only to a new run directory. Legacy backtest, research evaluator, paper runner, Phase 3 replay fixtures, historical reports, frozen datasets, and the three approved Phase 1 schemas remain unchanged.
