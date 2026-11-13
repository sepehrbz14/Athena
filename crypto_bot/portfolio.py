"""Deterministic frozen-signal, shared-account portfolio simulation.

This is deliberately separate from the historical strategy backtester. It
consumes only validated frozen signals and delegates all bar execution rules
to :class:`BarExecutionModel`; cash and position accounting remain owned by
PortfolioLedger.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Sequence

from .controls import KillSwitch, KillSwitchConfig
from .execution import BarExecutionModel, valid_prior_volume
from .models import Candle, Position, Side
from .portfolio_ledger import D, PortfolioLedger
from .portfolio_models import FrozenSignal, PortfolioConfig, PortfolioOrder, PortfolioPositionMeta, SignalDisposition


ZERO = Decimal("0")


@dataclass(slots=True)
class PortfolioResult:
    config_sha256: str
    source_manifest: dict[str, Any]
    dispositions: list[SignalDisposition]
    events: list[dict[str, Any]]
    equity_curve: list[dict[str, Any]]
    reconciliation: dict[str, Decimal]
    ledger: PortfolioLedger
    orders: list[dict[str, Any]]
    peak_equity: Decimal
    maximum_drawdown_pct: Decimal
    status: str
    checkpoint_sha256: str | None = None

    def as_record(self) -> dict[str, Any]:
        def dec(value: Any) -> Any:
            if isinstance(value, Decimal):
                return str(value)
            if isinstance(value, datetime):
                return value.isoformat()
            if isinstance(value, Side):
                return value.value
            if isinstance(value, dict):
                return {str(k): dec(v) for k, v in value.items()}
            if isinstance(value, (list, tuple)):
                return [dec(v) for v in value]
            return value
        record = dec({
            "schema_version": "athena.portfolio_result.v1", "config_sha256": self.config_sha256,
            "source_manifest": self.source_manifest,
            "dispositions": [d.as_record() for d in self.dispositions], "events": self.events,
            "equity_curve": self.equity_curve,
            "journal": [entry.as_schema_record() for entry in self.ledger.journal],
            "positions": [asdict(position) for position in self.ledger.positions.values()],
            "completed_positions": [asdict(position) for position in self.ledger.completed_positions],
            "reservations": self.ledger.reservations, "reconciliation": self.reconciliation,
            "orders": self.orders, "peak_equity": self.peak_equity,
            "maximum_drawdown_pct": self.maximum_drawdown_pct, "status": self.status,
        })
        from jsonschema import Draft202012Validator
        schema_path = Path(__file__).resolve().parents[1] / "schemas" / "athena2_portfolio_event_v1.schema.json"
        validator = Draft202012Validator(json.loads(schema_path.read_text(encoding="utf-8")),
            format_checker=Draft202012Validator.FORMAT_CHECKER)
        for event in record["events"]:
            validator.validate(event)
        cash_schema_path = Path(__file__).resolve().parents[1] / "schemas" / "athena2_cash_journal_entry_v1.schema.json"
        cash_validator = Draft202012Validator(json.loads(cash_schema_path.read_text(encoding="utf-8")),
            format_checker=Draft202012Validator.FORMAT_CHECKER)
        for journal_entry in record["journal"]:
            cash_validator.validate(journal_entry)
        json.dumps(record, allow_nan=False)
        return record


class PortfolioSimulator:
    """Run one deterministic portfolio account over completed UTC candles."""

    def __init__(self, config: PortfolioConfig, *, account_id: str = "athena-portfolio-v1") -> None:
        self.config = config
        self.account_id = account_id
        self.execution: BarExecutionModel = config.execution

    def run(self, candles: Sequence[Candle], signals: Sequence[FrozenSignal], *,
            source_manifest: dict[str, Any] | None = None,
            checkpoint_path: str | Path | None = None,
            resume_from: str | Path | None = None,
            stop_after_index: int | None = None) -> PortfolioResult:
        if not candles:
            raise ValueError("portfolio replay requires candles")
        self._validate_inputs(candles, signals)
        if stop_after_index is not None and not 0 <= stop_after_index < len(candles):
            raise ValueError("stop_after_index must identify a candle in the replay")
        ledger = PortfolioLedger(self.account_id, self.config.initial_equity_usdt)
        orders: dict[str, PortfolioOrder] = {}
        order_records: list[dict[str, Any]] = []
        meta: dict[str, PortfolioPositionMeta] = {}
        event_records: list[dict[str, Any]] = []
        equity_curve: list[dict[str, Any]] = []
        manifest = dict(source_manifest or {})
        dispositions = list(manifest.pop("_dispositions", manifest.pop("dispositions", [])))
        known_signal_keys = {(d.signal_id, d.available_at) for d in dispositions if d.signal_id is not None}
        for raw_row, signal in enumerate(signals, 1):
            if (signal.signal_id, signal.available_at) not in known_signal_keys:
                dispositions.append(SignalDisposition(raw_row, signal.signal_id, signal.instrument_id,
                    signal.available_at, signal.strategy, True, "eligible"))
                known_signal_keys.add((signal.signal_id, signal.available_at))
        disposition_by_row: dict[int, SignalDisposition] = {d.raw_row: d for d in dispositions}
        signals_by_available: dict[datetime, list[FrozenSignal]] = {}
        for signal in signals:
            signals_by_available.setdefault(signal.available_at, []).append(signal)
        for batch in signals_by_available.values():
            batch.sort(key=lambda s: (self.config.strategy_rank(s.strategy), s.instrument_id, s.signal_id))

        kill = KillSwitch(KillSwitchConfig(
            max_daily_loss_pct=float(self.config.daily_loss_limit_pct),
            max_drawdown_pct=float(self.config.maximum_drawdown_pct),
        ))
        peak = self.config.initial_equity_usdt
        day_open_equity: Decimal | None = None
        day_key = None
        status = "complete"
        equity_entry_locked = False
        prior_bar_index: dict[str, int] = {}
        mark_by_position: dict[str, Decimal] = {}
        last_index = -1
        filled_this_bar: dict[str, Decimal] = {}

        resume_checkpoint = None
        if resume_from is not None:
            resume_checkpoint = self._validate_checkpoint(resume_from, candles, signals)
            # Restart is intentionally a deterministic replay from the source
            # prefix; verify the checkpoint hash, then execute full replay. This
            # avoids deserializing mutable ledger graphs into a second format.

        for index, candle in enumerate(candles):
            last_index = index
            bar_time = candle.timestamp.astimezone(timezone.utc)
            available_at = bar_time + timedelta(minutes=self.execution.candle_minutes)
            if index and candle.timestamp - candles[index - 1].timestamp > timedelta(minutes=self.execution.candle_minutes):
                self._event(event_records, bar_time, "data_gap", self.account_id,
                    {"previous_candle": candles[index - 1].timestamp, "next_candle": bar_time,
                     "missing_intervals": max(0, int((candle.timestamp-candles[index-1].timestamp).total_seconds() //
                         (self.execution.candle_minutes * 60)) - 1)})

            # 1. Resolve only stop gaps at the observed open before allocating
            # pending entries. Gap-through pricing remains in BarExecutionModel.
            for pid in list(ledger.positions):
                position = ledger.positions[pid]
                state = meta[pid]
                equity_at_open = ledger.equity({pid: D(candle.open)})
                if equity_at_open <= ZERO:
                    raw_extreme = candle.low if position.side is Side.LONG else candle.high
                    exit_request = (raw_extreme, "insolvency")
                    status = "insolvent"
                    equity_entry_locked = True
                    self._cancel_all(ledger, orders, order_records, bar_time, event_records, "INSOLVENCY")
                else:
                    exit_request = self.execution.opening_stop_gap_request(position.side, float(state.stop_price), candle)
                if exit_request is not None:
                    raw_price, reason = exit_request
                    exit_side = Side.SHORT if position.side is Side.LONG else Side.LONG
                    fill = self.execution.fill(raw_price, exit_side, float(position.quantity))
                    ledger.apply_exit_fill(pid, fill, available_at,
                        f"exit:{pid}:{available_at.isoformat()}:{reason}", order_id=state.order_id)
                    state.exit_reason = reason
                    meta.pop(pid, None)
                    mark_by_position.pop(pid, None)
                    self._event(event_records, bar_time, "position_closed", pid,
                        {"reason": reason, "gap_at_open": True, "raw_price": D(raw_price),
                         "fill_price": D(fill.price), "quantity": D(fill.quantity)})
                    if state.order_id in orders:
                        self._cancel_order(ledger, orders, order_records, state.order_id,
                            "POSITION_CLOSED_BEFORE_REMAINDER_FILLED", bar_time, event_records)

            # Re-mark any surviving positions at the observed open before
            # calculating this bar's risk, margin and exposure availability.
            for pid in ledger.positions:
                mark_by_position[pid] = D(candle.open)

            # 3. Pending orders get an eligible next-open attempt only when the
            # immediate prior candle is contiguous and has positive volume.
            valid_volume, stale = valid_prior_volume(candles, index, self.execution.candle_minutes)
            eligible_attempt = index > 0 and not stale and valid_volume is not None and valid_volume > 0
            for order_id in list(orders):
                order = orders[order_id]
                allowed, _reason = kill.allow_new_trade()
                if not allowed:
                    self._cancel_order(ledger, orders, order_records, order_id, "KILL_SWITCH_ACTIVE", bar_time, event_records)
                    self._set_disposition(disposition_by_row, order.signal, "rejected", "KILL_SWITCH_ACTIVE")
                    continue
                if equity_entry_locked:
                    self._cancel_order(ledger, orders, order_records, order_id, "MINIMUM_EQUITY", bar_time, event_records)
                    self._set_disposition(disposition_by_row, order.signal, "rejected", "MINIMUM_EQUITY")
                    continue
                if not eligible_attempt:
                    self._event(event_records, bar_time, "order_waiting_for_liquidity", order_id,
                        {"stale_gap": stale, "prior_volume": valid_volume})
                    continue
                order.eligible_attempts += 1
                signal = order.signal
                side = signal.side
                raw_open = candle.open
                estimated_entry = D(self.execution.slipped_price(raw_open, side))
                if not self._stop_valid(side, estimated_entry, signal.stop_price):
                    self._cancel_order(ledger, orders, order_records, order_id, "INVALID_ACTUAL_STOP", bar_time, event_records)
                    self._set_disposition(disposition_by_row, signal, "rejected", "INVALID_ACTUAL_STOP")
                    continue
                participation = D(self.execution.participation_quantity(valid_volume))
                desired = min(order.remaining_quantity, participation)
                if desired < self.config.minimum_quantity_btc:
                    self._cancel_order(ledger, orders, order_records, order_id, "BELOW_MINIMUM_SIZE", bar_time, event_records)
                    self._set_disposition(disposition_by_row, signal, "rejected", "BELOW_MINIMUM_SIZE")
                    continue
                other_orders = {key: value for key, value in orders.items() if key != order_id}
                risk_left = self._risk_cap(ledger, mark_by_position) - self._open_stop_risk(ledger, meta, mark_by_position, other_orders)
                per_unit_risk = self._risk_per_unit(side, estimated_entry, signal.stop_price)
                qty_by_risk = max(ZERO, min(self.config.per_trade_risk_pct * self._equity(ledger, mark_by_position), risk_left) / per_unit_risk) if per_unit_risk else ZERO
                qty_by_margin = self._max_qty_for_cash(ledger, mark_by_position, estimated_entry,
                    own_reservation=order.reserved_collateral)
                qty_by_exposure = self._exposure_capacity(ledger, meta, mark_by_position,
                    side, estimated_entry, other_orders)
                desired = min(desired, qty_by_risk, qty_by_margin, qty_by_exposure)
                desired = self._floor_quantity(desired)
                if desired < self.config.minimum_quantity_btc:
                    self._cancel_order(ledger, orders, order_records, order_id, "ACTUAL_SLIPPAGE_SIZE_BELOW_MINIMUM", bar_time, event_records)
                    self._set_disposition(disposition_by_row, signal, "rejected", "ACTUAL_SLIPPAGE_SIZE_BELOW_MINIMUM")
                    continue
                fill = self.execution.fill(raw_open, side, float(desired))
                fill_qty = D(fill.quantity)
                if fill.price <= 0 or fill.fee < 0:
                    self._cancel_order(ledger, orders, order_records, order_id, "INVALID_EXECUTION_FILL", bar_time, event_records)
                    self._set_disposition(disposition_by_row, signal, "rejected", "INVALID_EXECUTION_FILL")
                    continue
                if any(p.position_id == signal.position_id for p in ledger.completed_positions):
                    self._cancel_order(ledger, orders, order_records, order_id, "POSITION_ID_COLLISION", bar_time, event_records)
                    self._set_disposition(disposition_by_row, signal, "rejected", "POSITION_ID_COLLISION")
                    continue
                # Fill accounting, reservation reduction and order state update
                # are one rollback boundary. Ledger calls themselves are atomic;
                # snapshot external orchestration state around the compound step.
                snapshot = self._snapshot(ledger, orders, order_records, meta, event_records)
                try:
                    pos = ledger.apply_entry_fill(position_id=signal.position_id, instrument_id=signal.instrument_id,
                        side=side, fill=fill, leverage=self.config.leverage, when=bar_time,
                        event_id=f"entry:{order_id}:{len(order_records)}", order_id=order_id)
                    order.remaining_quantity -= fill_qty
                    if order.remaining_quantity < self.config.minimum_quantity_btc:
                        order.remaining_quantity = ZERO
                    mark_by_position[signal.position_id] = D(candle.close)
                    state = meta.get(signal.position_id)
                    if state is None:
                        state = PortfolioPositionMeta(signal.position_id, order_id, signal,
                            signal.stop_price, signal.take_profit, bar_time, bar_time, pos.average_entry_price)
                        meta[signal.position_id] = state
                    filled_this_bar[signal.position_id] = filled_this_bar.get(signal.position_id, ZERO) + fill_qty
                    if order.remaining_quantity <= ZERO:
                        ledger.resize_reservation(order_id, ZERO, marks=mark_by_position)
                        orders.pop(order_id)
                    else:
                        reserve = self._reservation(order.remaining_quantity, estimated_entry)
                        ledger.resize_reservation(order_id, reserve, marks=mark_by_position)
                        order.reserved_collateral = reserve
                    order_records.append({"order_id": order_id, "event": "fill", "time": bar_time,
                        "quantity": fill_qty, "price": D(fill.price), "fee": D(fill.fee),
                        "remaining": order.remaining_quantity})
                    self._event(event_records, bar_time, "entry_fill", order_id,
                        {"position_id": signal.position_id, "signal_id": signal.signal_id, "quantity": fill_qty,
                         "price": D(fill.price), "fee": D(fill.fee), "slippage": D(fill.slippage),
                         "remaining": order.remaining_quantity})
                    self._set_disposition(disposition_by_row, signal, "accepted", None, order_id)
                    if signal.position_id not in ledger.positions and order_id in orders:
                        self._cancel_order(ledger, orders, order_records, order_id,
                            "POSITION_CLOSED_BEFORE_REMAINDER_FILLED", available_at, event_records)
                except Exception:
                    self._restore(snapshot, ledger, orders, order_records, meta, event_records)
                    raise
                if order_id in orders and order.eligible_attempts >= self.config.pending_order_expiry_eligible_candles:
                    self._cancel_order(ledger, orders, order_records, order_id, "ORDER_EXPIRED", available_at, event_records)

            # 4. Funding posts at close before intrabar stop/target handling.
            # The legacy BarExecutionModel value is a charge; the ledger takes
            # signed cash movement, so invert the sign exactly once. A fill at
            # this open receives only open-to-close elapsed time; carried size
            # receives its prior-close-to-close interval.
            for pid in list(ledger.positions):
                position = ledger.positions[pid]
                state = meta[pid]
                newly_filled = filled_this_bar.get(pid, ZERO)
                carried_qty = position.quantity - newly_filled
                elapsed_old = max(0.0, (available_at - state.last_funding_timestamp).total_seconds() / 3600)
                elapsed_new = max(0.0, (available_at - bar_time).total_seconds() / 3600)
                legacy_charge = self.execution.funding_amount(position.side, float(carried_qty), candle.close,
                    self.execution.funding_rate_per_8h, elapsed_old)
                legacy_charge += self.execution.funding_amount(position.side, float(newly_filled), candle.close,
                    self.execution.funding_rate_per_8h, elapsed_new)
                funding_cash = -D(legacy_charge)
                if funding_cash != ZERO:
                    ledger.apply_funding(pid, funding_cash, available_at,
                        f"funding:{pid}:{available_at.isoformat()}")
                    self._event(event_records, available_at, "funding", pid,
                        {"legacy_charge": D(legacy_charge), "ledger_cash_movement": funding_cash})
                state.last_funding_timestamp = available_at

            # 5. Resolve intrabar stop/target collisions after all open fills and
            # funding. The execution model keeps its adverse-stop-first policy.
            for pid in list(ledger.positions):
                position = ledger.positions[pid]
                state = meta[pid]
                view = Position(position.side, float(position.quantity), float(position.average_entry_price),
                    float(state.stop_price), float(state.take_profit) if state.take_profit is not None else None,
                    state.opened_at, state.signal.strategy, float(position.entry_fees_paid),
                    float(-position.funding_paid), signal_id=state.signal.signal_id,
                    last_funding_timestamp=state.last_funding_timestamp)
                worst_mark = candle.low if position.side is Side.LONG else candle.high
                if ledger.equity({pid: D(worst_mark)}) <= ZERO:
                    exit_request = (worst_mark, "insolvency")
                    status = "insolvent"
                    equity_entry_locked = True
                    self._cancel_all(ledger, orders, order_records, available_at, event_records, "INSOLVENCY")
                else:
                    exit_request = self.execution.position_exit_request(view, candle)
                if exit_request is not None:
                    raw_price, reason = exit_request
                    exit_side = Side.SHORT if position.side is Side.LONG else Side.LONG
                    fill = self.execution.fill(raw_price, exit_side, float(position.quantity))
                    ledger.apply_exit_fill(pid, fill, available_at,
                        f"exit:{pid}:{available_at.isoformat()}:{reason}", order_id=state.order_id)
                    meta.pop(pid, None)
                    mark_by_position.pop(pid, None)
                    self._event(event_records, available_at, "position_closed", pid,
                        {"reason": reason, "raw_price": D(raw_price), "fill_price": D(fill.price), "quantity": D(fill.quantity)})
                    if state.order_id in orders:
                        self._cancel_order(ledger, orders, order_records, state.order_id,
                            "POSITION_CLOSED_BEFORE_REMAINDER_FILLED", available_at, event_records)

            # 6. Mark account, update equity/drawdown and safety state.
            for pid in list(ledger.positions):
                mark_by_position[pid] = D(candle.close)
            equity = self._equity(ledger, mark_by_position)
            peak = max(peak, equity)
            drawdown = ZERO if peak <= 0 else max(ZERO, (peak - equity) / peak)
            equity_curve.append({"time": available_at, "cash": ledger.cash,
                "unrealized_gross_pnl": equity - ledger.cash, "equity": equity,
                "used_margin": ledger.used_initial_margin(mark_by_position),
                "reservations": ledger.reserved_collateral, "gross_exposure": self._gross_exposure(ledger, meta, mark_by_position, orders),
                "net_exposure": self._net_exposure(ledger, meta, mark_by_position, orders), "drawdown_pct": drawdown})
            self._event(event_records, available_at, "account_marked", self.account_id,
                {"cash": ledger.cash, "equity": equity, "unrealized_gross_pnl": equity - ledger.cash,
                 "used_margin": ledger.used_initial_margin(mark_by_position), "reserved_collateral": ledger.reserved_collateral,
                 "gross_exposure": self._gross_exposure(ledger, meta, mark_by_position, orders),
                 "net_exposure": self._net_exposure(ledger, meta, mark_by_position, orders), "drawdown_pct": drawdown})
            kill.observe(available_at, float(equity))
            if equity <= ZERO:
                status = "insolvent"
            if equity < self.config.minimum_equity_usdt:
                equity_entry_locked = True
                self._cancel_all(ledger, orders, order_records, available_at, event_records, "MINIMUM_EQUITY")
                if equity <= ZERO:
                    status = "insolvent"
                elif status == "complete":
                    status = "minimum_equity_locked"
                self._event(event_records, available_at, "entries_locked", self.account_id,
                        {"reason": "INSOLVENCY" if equity <= ZERO else "MINIMUM_EQUITY", "equity": equity})

            # 7. Batch-allocation at close, available for the next bar open.
            batch = signals_by_available.get(available_at, [])
            if batch:
                self._allocate_batch(batch, ledger, orders, meta, mark_by_position, order_records,
                    event_records, disposition_by_row, kill, available_at, equity, equity_entry_locked)

            if checkpoint_path is not None:
                self._write_checkpoint(checkpoint_path, candles, signals, index, equity_curve, ledger, orders,
                    event_records, meta=meta, marks=mark_by_position, kill=kill,
                    equity_entry_locked=equity_entry_locked)
            if resume_checkpoint is not None and not resume_checkpoint.get("final") and index == resume_checkpoint["cursor"]:
                actual_state = self._state_sha256(ledger, orders, meta, mark_by_position, equity_curve,
                    event_records, kill, equity_entry_locked)
                if actual_state != resume_checkpoint.get("state_sha256"):
                    raise ValueError("checkpoint replay state mismatch")
            if stop_after_index is not None and index == stop_after_index:
                status = "checkpointed"
                break

        reached_end = last_index == len(candles) - 1
        if self.config.force_close_at_end and candles and reached_end:
            final = candles[-1]
            final_time = final.timestamp + timedelta(minutes=self.execution.candle_minutes)
            for pid in list(ledger.positions):
                position = ledger.positions[pid]
                side = Side.SHORT if position.side is Side.LONG else Side.LONG
                fill = self.execution.fill(final.close, side, float(position.quantity))
                ledger.apply_exit_fill(pid, fill, final_time, f"exit:{pid}:{final_time.isoformat()}:end_of_data",
                    order_id=meta[pid].order_id)
                self._event(event_records, final_time, "position_closed", pid,
                    {"reason": "end_of_data", "fill_price": D(fill.price), "quantity": D(fill.quantity)})
                meta.pop(pid, None)
            self._cancel_all(ledger, orders, order_records, final_time, event_records, "END_OF_DATA")
        final_marks = {pid: mark_by_position[pid] for pid in ledger.positions}
        reconciliation = ledger.reconcile(final_marks)
        if self.config.force_close_at_end and reached_end:
            final_time = candles[-1].timestamp + timedelta(minutes=self.execution.candle_minutes)
            final_equity = ledger.equity(final_marks)
            peak = max(peak, final_equity)
            equity_curve.append({"time": final_time, "cash": ledger.cash,
                "unrealized_gross_pnl": final_equity - ledger.cash, "equity": final_equity,
                "used_margin": ledger.used_initial_margin(final_marks), "reservations": ledger.reserved_collateral,
                "gross_exposure": self._gross_exposure(ledger, meta, final_marks, orders),
                "net_exposure": self._net_exposure(ledger, meta, final_marks, orders),
                "drawdown_pct": ZERO if peak <= 0 else max(ZERO, (peak-final_equity)/peak)})
            self._event(event_records, final_time, "end_of_data", self.account_id,
                {"equity": final_equity, "cash": ledger.cash, "force_closed": True})
        max_dd = max((row["drawdown_pct"] for row in equity_curve), default=ZERO)
        result = PortfolioResult(self.config.sha256, manifest, list(disposition_by_row.values()), event_records,
            equity_curve, reconciliation, ledger, order_records, peak, max_dd, status)
        if checkpoint_path is not None and reached_end:
            result.checkpoint_sha256 = self._write_checkpoint(checkpoint_path, candles, signals,
                len(candles) - 1, equity_curve, ledger, orders, event_records,
                meta=meta, marks=final_marks, kill=kill, equity_entry_locked=equity_entry_locked, final=True)
        if resume_checkpoint is not None and resume_checkpoint.get("final"):
            actual_state = self._state_sha256(ledger, orders, meta, final_marks, equity_curve,
                event_records, kill, equity_entry_locked)
            if actual_state != resume_checkpoint.get("state_sha256"):
                raise ValueError("final checkpoint replay state mismatch")
        return result

    def _allocate_batch(self, batch, ledger, orders, meta, marks, order_records, events, dispositions,
                        kill, when, equity, equity_entry_locked):
        seen_in_batch: set[tuple[str, datetime]] = set()
        for signal in batch:
            key = (signal.instrument_id, signal.available_at)
            reason = None
            allowed, kill_reason = kill.allow_new_trade()
            if not allowed:
                reason = "KILL_SWITCH_ACTIVE"
            elif equity_entry_locked:
                reason = "MINIMUM_EQUITY"
            elif key in seen_in_batch:
                reason = "CONFLICTING_SIGNAL_BATCH"
            elif signal.instrument_id not in self.config.instrument_allowlist:
                reason = "INSTRUMENT_NOT_ALLOWED"
            elif signal.instrument_id in {p.instrument_id for p in ledger.positions.values()}:
                reason = "DUPLICATE_EXPOSURE" if any(p.side is signal.side for p in ledger.positions.values()) else "OPPOSING_SIGNAL_WHILE_EXPOSED"
            elif any(item.position_id == signal.position_id for item in ledger.completed_positions):
                reason = "POSITION_ID_COLLISION"
            elif any(order.signal.instrument_id == signal.instrument_id for order in orders.values()):
                reason = "DUPLICATE_PENDING_ORDER"
            elif len(ledger.positions) + len(orders) >= self.config.maximum_active_positions:
                reason = "MAX_ACTIVE_POSITIONS"
            elif not self._stop_valid(signal.side, signal.reference_price, signal.stop_price):
                reason = "INVALID_STOP"
            elif not signal.strategy:
                reason = "INVALID_SIGNAL"
            seen_in_batch.add(key)
            if reason:
                self._set_disposition(dispositions, signal, "rejected", reason)
                self._event(events, when, "signal_rejected", signal.order_id, {"signal_id": signal.signal_id, "reason": reason})
                continue
            equity_now = self._equity(ledger, marks)
            remaining_risk = max(ZERO, self._risk_cap(ledger, marks) - self._open_stop_risk(ledger, meta, marks, orders))
            risk_budget = min(equity_now * self.config.per_trade_risk_pct, remaining_risk)
            estimated_entry = D(self.execution.slipped_price(float(signal.reference_price), signal.side))
            risk_per_unit = self._risk_per_unit(signal.side, estimated_entry, signal.stop_price)
            qty_risk = risk_budget / risk_per_unit if risk_per_unit > 0 else ZERO
            qty_cash = self._max_qty_for_cash(ledger, marks, estimated_entry)
            qty_exposure = self._exposure_capacity(ledger, meta, marks, signal.side, estimated_entry, orders)
            quantity = self._floor_quantity(min(qty_risk, qty_cash, qty_exposure))
            if quantity < self.config.minimum_quantity_btc:
                reason = "SIZE_BELOW_MINIMUM" if qty_risk < self.config.minimum_quantity_btc else "INSUFFICIENT_COLLATERAL_OR_EXPOSURE"
                self._set_disposition(dispositions, signal, "rejected", reason)
                self._event(events, when, "signal_rejected", signal.order_id, {"signal_id": signal.signal_id, "reason": reason})
                continue
            collateral = self._reservation(quantity, estimated_entry)
            pending_risk = self._pending_stop_risk(signal.side, quantity, estimated_entry, signal.stop_price)
            if pending_risk + self._open_stop_risk(ledger, meta, marks, orders) > self._risk_cap(ledger, marks):
                self._set_disposition(dispositions, signal, "rejected", "AGGREGATE_STOP_RISK_CAP")
                continue
            try:
                ledger.reserve(signal.order_id, collateral, marks=marks)
            except ValueError:
                self._set_disposition(dispositions, signal, "rejected", "INSUFFICIENT_COLLATERAL")
                continue
            order = PortfolioOrder(signal.order_id, signal, when, quantity, quantity, collateral, pending_risk)
            orders[signal.order_id] = order
            order_records.append({"order_id": signal.order_id, "event": "reserved", "time": when,
                "quantity": quantity, "collateral": collateral, "stop_risk": pending_risk})
            self._set_disposition(dispositions, signal, "pending", None, signal.order_id)
            self._event(events, when, "order_reserved", signal.order_id,
                {"quantity": quantity, "collateral": collateral, "stop_risk": pending_risk})

    def _risk_per_unit(self, side: Side, entry: Decimal, stop: Decimal) -> Decimal:
        exit_side = Side.SHORT if side is Side.LONG else Side.LONG
        stop_fill = D(self.execution.slipped_price(float(stop), exit_side))
        gross = max(ZERO, D(side.sign) * (entry - stop_fill))
        fees = D(self.execution.fee_rate) * (entry + stop_fill)
        return gross + fees

    def _pending_stop_risk(self, side: Side, qty: Decimal, entry: Decimal, stop: Decimal) -> Decimal:
        return qty * self._risk_per_unit(side, entry, stop)

    def _open_stop_risk(self, ledger, meta, marks, orders=None) -> Decimal:
        total = ZERO
        for pid, position in ledger.positions.items():
            mark = marks[pid]
            stop_fill = D(self.execution.slipped_price(float(meta[pid].stop_price),
                Side.SHORT if position.side is Side.LONG else Side.LONG))
            total += position.quantity * (max(ZERO, D(position.side.sign) * (mark - stop_fill))
                + abs(stop_fill) * D(self.execution.fee_rate))
        for order in (orders or {}).values():
            total += order.reserved_stop_risk
        return total

    def _risk_cap(self, ledger, marks) -> Decimal:
        return max(ZERO, self._equity(ledger, marks) * self.config.aggregate_open_pending_stop_risk_pct)

    def _reservation(self, qty: Decimal, entry: Decimal) -> Decimal:
        return qty * entry / self.config.leverage + qty * entry * D(self.execution.fee_rate)

    def _max_qty_for_cash(self, ledger, marks, entry, own_reservation=ZERO):
        equity = self._equity(ledger, marks)
        cash_buffer = max(ZERO, equity * self.config.cash_buffer_pct)
        free = ledger.free_collateral_at(marks) + own_reservation - cash_buffer
        return max(ZERO, free / (entry / self.config.leverage + entry * D(self.execution.fee_rate)))

    def _exposure_capacity(self, ledger, meta, marks, side, entry, orders=None):
        equity = max(ZERO, self._equity(ledger, marks))
        gross, net = self._gross_exposure(ledger, meta, marks, orders), self._net_exposure(ledger, meta, marks, orders)
        unit = entry
        gross_room = max(ZERO, equity * self.config.maximum_gross_exposure_equity_multiple - gross) / unit
        signed_net = net
        net_room = max(ZERO, equity * self.config.maximum_net_exposure_equity_multiple - abs(signed_net)) / unit
        return min(gross_room, net_room)

    def _gross_exposure(self, ledger, meta, marks, orders=None):
        open_exposure = sum((p.quantity * marks[pid] for pid, p in ledger.positions.items()), ZERO)
        pending_exposure = sum((order.remaining_quantity * D(self.execution.slipped_price(
            float(order.signal.reference_price), order.signal.side)) for order in (orders or {}).values()), ZERO)
        return open_exposure + pending_exposure

    def _net_exposure(self, ledger, meta, marks, orders=None):
        open_exposure = sum((D(p.side.sign) * p.quantity * marks[pid] for pid, p in ledger.positions.items()), ZERO)
        pending_exposure = sum((D(order.signal.side.sign) * order.remaining_quantity * D(self.execution.slipped_price(
            float(order.signal.reference_price), order.signal.side)) for order in (orders or {}).values()), ZERO)
        return open_exposure + pending_exposure

    def _equity(self, ledger, marks):
        return ledger.equity(marks)

    def _floor_quantity(self, quantity: Decimal) -> Decimal:
        # The frozen contract defines a rejection threshold, not an exchange
        # lot-size increment. Do not invent venue quantity rounding here.
        return quantity if quantity > 0 else ZERO

    @staticmethod
    def _stop_valid(side, entry, stop):
        return stop < entry if side is Side.LONG else stop > entry

    @staticmethod
    def _insolvent(ledger, marks):
        return ledger.equity(marks) <= ZERO

    @staticmethod
    def _set_disposition(dispositions, signal, status, reason, order_id=None):
        if isinstance(dispositions, dict):
            for raw_row, old in tuple(dispositions.items()):
                if (old.signal_id == signal.signal_id and old.available_at == signal.available_at
                        and old.strategy == signal.strategy and old.selected is True):
                    dispositions[raw_row] = SignalDisposition(old.raw_row, old.signal_id, old.instrument_id,
                        old.available_at, old.strategy, old.selected, status, reason, order_id)

    def _event(self, events, when, event_type, object_id, payload):
        event_names = {
            "order_reserved": "ORDER_RESERVED", "order_waiting_for_liquidity": "ORDER_WAITING_FOR_LIQUIDITY",
            "entry_fill": "ORDER_PARTIALLY_FILLED", "funding": "FUNDING_POSTED",
            "position_closed": "POSITION_CLOSED", "signal_rejected": "ORDER_REJECTED",
            "order_cancelled": "ORDER_CANCELLED", "entries_locked": "KILL_SWITCH_ACTIVATED",
            "data_gap": "DATA_GAP", "account_marked": "ACCOUNT_MARKED", "end_of_data": "END_OF_DATA",
        }
        event_phases = {
            "order_reserved": "signal_batch", "order_waiting_for_liquidity": "pending_order_execution",
            "entry_fill": "pending_order_execution", "funding": "close_accounting",
            "position_closed": "intrabar_exit", "signal_rejected": "signal_batch",
            "order_cancelled": "end_of_data" if payload.get("reason") == "END_OF_DATA" else "pending_order_execution",
            "entries_locked": "safety_and_margin", "data_gap": "input", "account_marked": "safety_and_margin",
            "end_of_data": "end_of_data",
        }
        payload = dict(payload)
        signal_id = payload.get("signal_id")
        order_id = object_id if str(object_id).startswith("order:") else None
        position_id = object_id if str(object_id).startswith("position:") else payload.get("position_id")
        event_name = event_names[event_type]
        if event_type == "entry_fill" and payload.get("remaining") == ZERO:
            event_name = "ORDER_FILLED"
        if event_type == "entries_locked" and D(payload.get("equity", 0)) <= ZERO:
            event_name = "INSOLVENCY_DECLARED"
        phase = event_phases[event_type]
        if event_type == "position_closed" and payload.get("gap_at_open"):
            phase = "bar_open_exit"
        if event_type == "position_closed" and payload.get("reason") == "end_of_data":
            phase = "end_of_data"
        events.append({"schema_version": "athena.portfolio.event.v1",
            "event_id": f"{event_type}:{object_id}:{when.isoformat()}:{len(events)}",
            "event_time": when, "event_type": event_name,
            "phase": phase, "account_id": self.account_id,
            "instrument_id": "BTCUSDT" if position_id or order_id else None,
            "source_signal_id": signal_id, "order_id": order_id, "position_id": position_id,
            "reason_code": payload.get("reason"), "payload": payload})

    def _cancel_order(self, ledger, orders, records, order_id, reason, when, events):
        order = orders.pop(order_id)
        released = ledger.release_reservation(order_id)
        records.append({"order_id": order_id, "event": "cancelled", "time": when, "reason": reason, "released": released})
        self._event(events, when, "order_cancelled", order_id, {"reason": reason, "released_collateral": released})

    def _cancel_all(self, ledger, orders, records, when, events, reason):
        for order_id in list(orders):
            self._cancel_order(ledger, orders, records, order_id, reason, when, events)

    @staticmethod
    def _snapshot(ledger, orders, records, meta, events):
        from copy import deepcopy
        from dataclasses import fields
        ledger_state = {item.name: getattr(ledger, item.name) for item in fields(ledger)}
        return deepcopy((ledger_state, orders, records, meta, events))

    @staticmethod
    def _restore(snapshot, ledger, orders, records, meta, events):
        state, o, r, m, e = snapshot
        for name, value in state.items():
            setattr(ledger, name, value)
        orders.clear(); orders.update(o)
        records[:] = r
        meta.clear(); meta.update(m)
        events[:] = e

    def _validate_inputs(self, candles, signals):
        previous = None
        for candle in candles:
            if candle.timestamp.tzinfo is None or candle.timestamp.utcoffset() != timedelta(0):
                raise ValueError("candle timestamps must be timezone-aware UTC")
            numeric = (candle.open, candle.high, candle.low, candle.close)
            if any(not math.isfinite(value) or value <= 0 for value in numeric):
                raise ValueError("candle prices must be finite and positive")
            if candle.volume is not None and (not math.isfinite(candle.volume) or candle.volume < 0):
                raise ValueError("candle volume must be finite and nonnegative")
            if previous is not None and candle.timestamp <= previous:
                raise ValueError("candles must be strictly chronological")
            previous = candle.timestamp
        keys = set()
        for signal in signals:
            key = (signal.available_at, signal.instrument_id, signal.signal_id)
            if key in keys:
                raise ValueError("duplicate frozen signal ordering key")
            keys.add(key)
            if signal.instrument_id not in self.config.instrument_allowlist:
                raise ValueError("signal instrument is outside configuration allowlist")
            if not 0 <= signal.candle_index < len(candles):
                raise ValueError("signal candle index is outside source candles")
            candle = candles[signal.candle_index]
            if signal.candle_open_time != candle.timestamp:
                raise ValueError("signal candle timestamp does not join by exact identity")
            expected_available = candle.timestamp + timedelta(minutes=self.execution.candle_minutes)
            if signal.available_at != expected_available:
                raise ValueError("signal availability timestamp does not match candle close")
            if not math.isclose(float(signal.reference_price), candle.close, rel_tol=1e-12, abs_tol=1e-9):
                raise ValueError("signal reference price does not match joined candle close")
            if not isinstance(signal.side, Side) or not signal.strategy:
                raise ValueError("signal side and strategy are required")
            finite_values = (signal.confidence, signal.reference_price, signal.stop_price)
            if any(not D(value).is_finite() for value in finite_values):
                raise ValueError("signal financial values must be finite")
            if signal.take_profit is not None and not D(signal.take_profit).is_finite():
                raise ValueError("signal target must be finite")
            if not ZERO < signal.confidence <= 1 or signal.reference_price <= ZERO or signal.stop_price <= ZERO:
                raise ValueError("signal confidence and prices are outside valid ranges")
            if signal.take_profit is not None and signal.take_profit <= ZERO:
                raise ValueError("signal target must be positive")

    def _state_sha256(self, ledger, orders, meta, marks, equity_curve, events, kill, equity_entry_locked):
        def clean(value):
            if isinstance(value, Decimal): return str(value)
            if hasattr(value, "isoformat"): return value.isoformat()
            if isinstance(value, Side): return value.value
            if isinstance(value, dict): return {str(k): clean(v) for k, v in value.items()}
            if isinstance(value, (list, tuple)): return [clean(v) for v in value]
            if hasattr(value, "__dataclass_fields__"):
                return {name: clean(getattr(value, name)) for name in value.__dataclass_fields__}
            return value
        state = {
            "cash": ledger.cash, "journal": [(e.entry_id, e.event_id, e.event_time, e.entry_type,
                e.amount_usdt, e.cash_after_usdt) for e in ledger.journal],
            "positions": ledger.positions, "completed": ledger.completed_positions,
            "reservations": ledger.reservations, "released_orders": sorted(ledger.released_orders),
            "sequence": ledger._entry_sequence, "event_fingerprints": ledger._events,
            "orders": {key: {"signal_id": item.signal.signal_id, "accepted_at": item.accepted_at,
                "requested": item.requested_quantity, "remaining": item.remaining_quantity,
                "collateral": item.reserved_collateral, "risk": item.reserved_stop_risk,
                "attempts": item.eligible_attempts} for key, item in sorted(orders.items())},
            "meta": meta, "marks": marks, "equity_curve": equity_curve, "events": events,
            "kill": {"peak": kill.peak_equity, "day": kill.day, "day_start": kill.day_start_equity,
                "daily_locked": kill.daily_locked,
                "permanent_reason": kill.permanent_reason.value if kill.permanent_reason else None},
            "equity_entry_locked": equity_entry_locked,
        }
        encoded = json.dumps(clean(state), sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        return hashlib.sha256(encoded).hexdigest()

    def _write_checkpoint(self, path, candles, signals, index, equity_curve, ledger, orders, events,
                          *, meta, marks, kill, equity_entry_locked, final=False):
        checkpoint = {"schema_version": "athena.portfolio_checkpoint.v1", "config_sha256": self.config.sha256,
            "candle_count": len(candles), "signal_ids": [s.signal_id for s in signals], "cursor": index,
            "candle_stream_sha256": self._candle_stream_sha256(candles),
            "signal_stream_sha256": self._signal_stream_sha256(signals),
            "final": final, "prefix_sha256": hashlib.sha256("|".join(c.timestamp.isoformat() for c in candles[:index + 1]).encode()).hexdigest(),
            "state_sha256": self._state_sha256(ledger, orders, meta, marks, equity_curve, events, kill, equity_entry_locked)}
        data = json.dumps(checkpoint, sort_keys=True, separators=(",", ":"))
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_suffix(target.suffix + ".tmp")
        temp.write_text(data, encoding="utf-8")
        temp.replace(target)
        return hashlib.sha256(data.encode()).hexdigest()

    def _validate_checkpoint(self, path, candles, signals):
        checkpoint = json.loads(Path(path).read_text(encoding="utf-8"))
        if checkpoint.get("config_sha256") != self.config.sha256:
            raise ValueError("checkpoint configuration hash mismatch")
        if checkpoint.get("candle_count") != len(candles) or checkpoint.get("signal_ids") != [s.signal_id for s in signals]:
            raise ValueError("checkpoint source stream mismatch")
        if checkpoint.get("candle_stream_sha256") != self._candle_stream_sha256(candles):
            raise ValueError("checkpoint candle stream hash mismatch")
        if checkpoint.get("signal_stream_sha256") != self._signal_stream_sha256(signals):
            raise ValueError("checkpoint frozen signal stream hash mismatch")
        index = checkpoint.get("cursor")
        prefix = hashlib.sha256("|".join(c.timestamp.isoformat() for c in candles[:index + 1]).encode()).hexdigest()
        if prefix != checkpoint.get("prefix_sha256"):
            raise ValueError("checkpoint candle prefix mismatch")
        return checkpoint

    @staticmethod
    def _candle_stream_sha256(candles):
        body = [[c.timestamp.isoformat(), c.open, c.high, c.low, c.close, c.volume] for c in candles]
        return hashlib.sha256(json.dumps(body, separators=(",", ":"), allow_nan=False).encode()).hexdigest()

    @staticmethod
    def _signal_stream_sha256(signals):
        body = [[s.signal_id, s.instrument_id, s.candle_open_time.isoformat(), s.available_at.isoformat(),
            s.strategy, s.side.value, str(s.confidence), str(s.reference_price), str(s.stop_price),
            str(s.take_profit) if s.take_profit is not None else None, s.source_signal_sha256,
            s.source_candle_sha256, s.candle_index] for s in signals]
        return hashlib.sha256(json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def run_from_files(*, config_path: str | Path, signal_path: str | Path, metadata_path: str | Path,
                   candle_path: str | Path, expected_signal_sha256: str, expected_candle_sha256: str,
                   output_dir: str | Path, checkpoint_path: str | Path | None = None,
                   resume_from: str | Path | None = None) -> PortfolioResult:
    from .frozen_signals import FrozenSignalReader
    from .io import load_ohlcv_csv
    config = PortfolioConfig.from_path(config_path)
    candles = load_ohlcv_csv(candle_path)
    reader = FrozenSignalReader(config)
    signals, dispositions, manifest = reader.read(signal_path=signal_path, metadata_path=metadata_path,
        candle_path=candle_path, candles=candles, expected_signal_sha256=expected_signal_sha256,
        expected_candle_sha256=expected_candle_sha256)
    manifest["_dispositions"] = dispositions
    result = PortfolioSimulator(config).run(candles, signals, source_manifest=manifest,
        checkpoint_path=checkpoint_path, resume_from=resume_from)
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    record = result.as_record()
    (root / "portfolio_result_v1.json").write_text(json.dumps(record, sort_keys=True, indent=2, allow_nan=False), encoding="utf-8")
    (root / "portfolio_events_v1.jsonl").write_text("".join(json.dumps(row, sort_keys=True, allow_nan=False) + "\n" for row in record["events"]), encoding="utf-8")
    return result
