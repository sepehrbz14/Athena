"""Deterministic cross-margin USDT ledger for portfolio simulator milestones.

Execution prices and quantities arrive as binary64 values from
``BarExecutionModel``. The ledger converts each input with ``Decimal(str(x))``
and performs subsequent accounting with Decimal (no quantization). This gives
stable decimal operations over the textual float value, but cannot recover
precision discarded by the execution layer. Serialized v1 journal amounts
are JSON numbers (float at the schema boundary), matching the frozen schema.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from decimal import InvalidOperation
import math
from copy import deepcopy
from functools import wraps
import json
from typing import Any

from .execution import BarExecutionModel, ExecutionFill
from .models import Side


ZERO = Decimal("0")


def _atomic_financial(method):
    """Rollback all ledger state if a multi-step financial operation fails."""
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        # Keep references handed to callers stable too. The previous graph
        # snapshot restored equivalent position objects inside the ledger, but
        # callers holding a LedgerPosition returned by apply_entry_fill() saw
        # that object left half-mutated after a failed later operation.
        original_positions: dict[str, LedgerPosition] = dict(self.positions)
        for fingerprint, result in self._events.values():
            if isinstance(result, LedgerPosition):
                original_positions.setdefault(result.position_id, result)
        # Copy the graph as one object so shared references (notably an entry
        # event's cached position result and positions[position_id]) stay shared.
        snapshot = deepcopy({
            "cash": self.cash, "journal": self.journal, "positions": self.positions,
            "reservations": self.reservations, "sequence": self._entry_sequence,
            "events": self._events, "completed": self.completed_positions,
            "released_orders": self.released_orders,
        })
        try:
            return method(self, *args, **kwargs)
        except Exception:
            self.cash = snapshot["cash"]
            self.journal = snapshot["journal"]
            restored_positions: dict[str, LedgerPosition] = {}
            for position_id, restored in snapshot["positions"].items():
                original = original_positions.get(position_id)
                if original is not None:
                    for name in LedgerPosition.__dataclass_fields__:
                        setattr(original, name, getattr(restored, name))
                    restored_positions[position_id] = original
                else:
                    restored_positions[position_id] = restored
            self.positions = restored_positions
            self.reservations = snapshot["reservations"]
            self._entry_sequence = snapshot["sequence"]
            self._events = snapshot["events"]
            for event_id, (fingerprint, result) in tuple(self._events.items()):
                if isinstance(result, LedgerPosition):
                    original = original_positions.get(result.position_id)
                    if original is not None:
                        self._events[event_id] = (fingerprint, original)
            self.completed_positions = snapshot["completed"]
            self.released_orders = snapshot["released_orders"]
            raise
    return wrapped


def D(value: Decimal | int | float | str) -> Decimal:
    """Convert an external scalar without importing its binary expansion."""
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("financial values must be finite")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("invalid financial value") from exc
    if not result.is_finite():
        raise ValueError("financial values must be finite")
    return result


@dataclass(frozen=True, slots=True)
class CashJournalEntry:
    schema_version: str
    entry_id: str
    event_id: str
    event_time: str
    account_id: str
    asset: str
    entry_type: str
    amount_usdt: Decimal
    cash_after_usdt: Decimal
    instrument_id: str | None = None
    source_signal_id: str | int | None = None
    order_id: str | int | None = None
    position_id: str | int | None = None
    trade_id: str | int | None = None
    memo: str | None = None

    def as_schema_record(self) -> dict[str, Any]:
        """Return the frozen v1 envelope; Decimal is encoded as JSON number."""
        record = {k: (_decimal_json_number(v) if isinstance(v, Decimal) else v) for k, v in {
            "schema_version": self.schema_version, "entry_id": self.entry_id,
            "event_id": self.event_id, "event_time": self.event_time,
            "account_id": self.account_id, "asset": self.asset,
            "entry_type": self.entry_type, "amount_usdt": self.amount_usdt,
            "cash_after_usdt": self.cash_after_usdt, "instrument_id": self.instrument_id,
            "source_signal_id": self.source_signal_id, "order_id": self.order_id,
            "position_id": self.position_id, "trade_id": self.trade_id, "memo": self.memo,
        }.items()}
        # Fail closed if any value cannot be represented as strict JSON.
        json.dumps(record, allow_nan=False, separators=(",", ":"))
        return record


def _decimal_json_number(value: Decimal) -> float:
    if not value.is_finite():
        raise ValueError("journal Decimal must be finite")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("journal Decimal is outside finite JSON number range")
    return result


@dataclass(slots=True)
class LedgerPosition:
    position_id: str
    instrument_id: str
    side: Side
    quantity: Decimal
    average_entry_price: Decimal
    leverage: Decimal
    entry_fees_paid: Decimal = ZERO
    funding_paid: Decimal = ZERO
    realized_gross_pnl: Decimal = ZERO
    exit_fees_paid: Decimal = ZERO
    total_entry_quantity: Decimal = ZERO


@dataclass(frozen=True, slots=True)
class CompletedPositionAccounting:
    position_id: str
    instrument_id: str
    side: Side
    quantity_closed: Decimal
    realized_gross_pnl: Decimal
    entry_fees_paid: Decimal
    exit_fees_paid: Decimal
    funding_paid: Decimal
    final_net_pnl: Decimal


@dataclass(slots=True)
class PortfolioLedger:
    account_id: str
    initial_cash: Decimal | int | float | str
    cash: Decimal = field(init=False)
    journal: list[CashJournalEntry] = field(default_factory=list)
    positions: dict[str, LedgerPosition] = field(default_factory=dict)
    reservations: dict[str, Decimal] = field(default_factory=dict)
    _entry_sequence: int = 0
    _events: dict[str, tuple[tuple[Any, ...], Any]] = field(default_factory=dict)
    completed_positions: list[CompletedPositionAccounting] = field(default_factory=list)
    released_orders: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        self.initial_cash = D(self.initial_cash)
        if not isinstance(self.account_id, str) or not self.account_id or self.initial_cash <= ZERO:
            raise ValueError("account ID and positive initial cash are required")
        self.cash = ZERO
        self._post("INITIAL_DEPOSIT", self.initial_cash, datetime(1970, 1, 1, tzinfo=timezone.utc), "initial", memo="initial account funding")
        self._events["initial"] = (("INITIAL_DEPOSIT", self.initial_cash), self.journal[0])

    def _post(self, entry_type: str, amount: Decimal, when: datetime, event_id: str,
              *, instrument_id: str | None = None, position_id: str | None = None,
              order_id: str | None = None, trade_id: str | None = None, memo: str | None = None) -> CashJournalEntry:
        if (not isinstance(event_id, str) or not event_id or not isinstance(when, datetime)
                or when.tzinfo is None or when.utcoffset() is None):
            raise ValueError("journal event time must be timezone-aware")
        amount = D(amount)
        self.cash += amount
        self._entry_sequence += 1
        entry = CashJournalEntry("athena.cash_journal_entry.v1", f"{self.account_id}:cash:{self._entry_sequence:08d}",
            event_id, when.astimezone(timezone.utc).isoformat(), self.account_id, "USDT", entry_type,
            amount, self.cash, instrument_id, order_id=order_id, position_id=position_id, trade_id=trade_id, memo=memo)
        self.journal.append(entry)
        return entry

    @_atomic_financial
    def deposit(self, amount: Decimal | int | float | str, when: datetime, event_id: str) -> CashJournalEntry:
        amount = D(amount)
        if amount <= ZERO: raise ValueError("deposit must be positive")
        return self._event(event_id, ("DEPOSIT", amount, when), lambda: self._post("DEPOSIT", amount, when, event_id))

    @_atomic_financial
    def withdraw(self, amount: Decimal | int | float | str, when: datetime, event_id: str, *, marks: dict[str, Any]) -> CashJournalEntry:
        amount = D(amount)
        if amount <= ZERO: raise ValueError("withdrawal must be positive")
        marks = self._require_marks(marks)
        def withdraw_once():
            if amount > self.free_collateral_at(marks): raise ValueError("withdrawal exceeds free collateral")
            return self._post("WITHDRAWAL", -amount, when, event_id)
        return self._event(event_id, ("WITHDRAWAL", amount, when), withdraw_once)

    @_atomic_financial
    def reserve(self, order_id: str, amount: Decimal | int | float | str, *, marks: dict[str, Any]) -> Decimal:
        amount = D(amount)
        marks = self._require_marks(marks)
        if amount < ZERO: raise ValueError("reservation cannot be negative")
        if not isinstance(order_id, str) or not order_id: raise ValueError("order ID is required")
        if order_id in self.reservations:
            if self.reservations[order_id] == amount: return amount
            raise ValueError("conflicting reservation retry for order ID")
        if order_id in self.released_orders: raise ValueError("released order reservation cannot be recreated")
        if amount > self.free_collateral_at(marks): raise ValueError("reservation exceeds free collateral")
        self.reservations[order_id] = amount
        return amount

    @_atomic_financial
    def resize_reservation(self, order_id: str, amount: Decimal | int | float | str, *, marks: dict[str, Any]) -> Decimal:
        """Atomically replace a live collateral reservation after a partial fill.

        Reservations are account encumbrances, never journal cash movements.
        A released order ID remains terminal and cannot be reopened.
        """
        amount = D(amount)
        marks = self._require_marks(marks)
        if not isinstance(order_id, str) or not order_id:
            raise ValueError("order ID is required")
        if amount < ZERO:
            raise ValueError("reservation cannot be negative")
        if order_id in self.released_orders:
            raise ValueError("released order reservation cannot be recreated")
        prior = self.reservations.get(order_id)
        if prior is None:
            if amount == ZERO:
                return ZERO
            raise ValueError("cannot resize a nonexistent reservation")
        if prior == amount:
            return amount
        # free_collateral includes the old reservation; add it back for the
        # replacement affordability check.
        available = self.free_collateral_at(marks) + prior
        if amount > available:
            raise ValueError("reservation exceeds free collateral")
        if amount == ZERO:
            del self.reservations[order_id]
            self.released_orders.add(order_id)
            return ZERO
        self.reservations[order_id] = amount
        return amount

    def _event(self, event_id: str, fingerprint: tuple[Any, ...], action):
        if not event_id: raise ValueError("event ID is required")
        prior = self._events.get(event_id)
        if prior is not None:
            if prior[0] != fingerprint: raise ValueError(f"conflicting replay for event ID {event_id}")
            return prior[1]
        result = action()
        self._events[event_id] = (fingerprint, result)
        return result

    @_atomic_financial
    def release_reservation(self, order_id: str) -> Decimal:
        if order_id not in self.reservations:
            return ZERO
        amount = self.reservations.pop(order_id)
        self.released_orders.add(order_id)
        return amount

    @_atomic_financial
    def apply_entry_fill(self, *, position_id: str, instrument_id: str, side: Side,
                         fill: ExecutionFill, leverage: Decimal | int | float | str,
                         when: datetime, event_id: str, order_id: str | None = None) -> LedgerPosition:
        qty, price, lev, fee = D(fill.quantity), D(fill.price), D(leverage), D(fill.fee)
        slippage = D(fill.slippage)
        if (not isinstance(position_id, str) or not position_id or not isinstance(instrument_id, str)
                or not instrument_id or not isinstance(side, Side)):
            raise ValueError("position ID, instrument ID, and side are required")
        if qty <= ZERO or price <= ZERO or lev <= ZERO or fee < ZERO or slippage < ZERO: raise ValueError("fill values must be positive with nonnegative fees and slippage")
        if fill.order_side is not side: raise ValueError("entry fill direction does not match position")
        fingerprint = ("ENTRY_FILL", position_id, instrument_id, side, qty, price, lev, fee, slippage, when, order_id)
        prior = self._events.get(event_id)
        if prior is not None:
            if prior[0] != fingerprint: raise ValueError(f"conflicting replay for event ID {event_id}")
            return self.positions.get(position_id, prior[1])
        pos = self.positions.get(position_id)
        if pos is None:
            if any(item.position_id == position_id for item in self.completed_positions):
                raise ValueError("completed position ID cannot be reused")
            pos = LedgerPosition(position_id, instrument_id, side, ZERO, ZERO, lev)
            self.positions[position_id] = pos
        if (pos.instrument_id, pos.side, pos.leverage) != (instrument_id, side, lev):
            raise ValueError("position increase must preserve instrument, side, and leverage")
        new_qty = pos.quantity + qty
        pos.average_entry_price = (pos.quantity * pos.average_entry_price + qty * price) / new_qty
        pos.quantity = new_qty
        pos.total_entry_quantity += qty
        pos.entry_fees_paid += fee
        self._post("ENTRY_FEE", -fee, when, event_id, instrument_id=instrument_id, position_id=position_id, order_id=order_id)
        self._events[event_id] = (fingerprint, pos)
        return pos

    @_atomic_financial
    def apply_funding(self, position_id: str, amount: Decimal | int | float | str,
                      when: datetime, event_id: str) -> CashJournalEntry:
        amount = D(amount)
        if not isinstance(position_id, str) or not position_id:
            raise ValueError("position ID is required")
        fingerprint = ("FUNDING", position_id, amount, when)
        prior = self._events.get(event_id)
        if prior is not None:
            if prior[0] != fingerprint: raise ValueError(f"conflicting replay for event ID {event_id}")
            return prior[1]
        pos = self.positions[position_id]
        pos.funding_paid += amount
        result = self._post("FUNDING", amount, when, event_id, instrument_id=pos.instrument_id, position_id=position_id)
        self._events[event_id] = (fingerprint, result)
        return result

    @_atomic_financial
    def apply_exit_fill(self, position_id: str, fill: ExecutionFill, when: datetime,
                        event_id: str, *, order_id: str | None = None, trade_id: str | None = None) -> tuple[Decimal, bool]:
        qty, price, fee = D(fill.quantity), D(fill.price), D(fill.fee)
        slippage = D(fill.slippage)
        if not isinstance(position_id, str) or not position_id:
            raise ValueError("position ID is required")
        if qty <= ZERO or price <= ZERO or fee < ZERO or slippage < ZERO: raise ValueError("exit fill values are invalid")
        fingerprint = ("EXIT_FILL", position_id, qty, price, fee, slippage, fill.order_side, when, order_id, trade_id)
        prior = self._events.get(event_id)
        if prior is not None:
            if prior[0] != fingerprint: raise ValueError(f"conflicting replay for event ID {event_id}")
            return prior[1]
        pos = self.positions[position_id]
        if qty > pos.quantity: raise ValueError("exit fill quantity exceeds open position")
        expected_side = Side.SHORT if pos.side is Side.LONG else Side.LONG
        if fill.order_side is not expected_side: raise ValueError("exit fill direction must oppose position")
        gross = D(pos.side.sign) * qty * (price - pos.average_entry_price)
        pos.quantity -= qty
        pos.realized_gross_pnl += gross
        self._post("REALIZED_PNL", gross, when, event_id, instrument_id=pos.instrument_id, position_id=position_id, order_id=order_id, trade_id=trade_id)
        self._post("EXIT_FEE", -fee, when, event_id, instrument_id=pos.instrument_id, position_id=position_id, order_id=order_id, trade_id=trade_id)
        pos.exit_fees_paid += fee
        closed = pos.quantity == ZERO
        if closed:
            self.completed_positions.append(CompletedPositionAccounting(
                position_id, pos.instrument_id, pos.side,
                pos.total_entry_quantity,
                pos.realized_gross_pnl, pos.entry_fees_paid, pos.exit_fees_paid, pos.funding_paid,
                pos.realized_gross_pnl - pos.entry_fees_paid - pos.exit_fees_paid + pos.funding_paid,
            ))
            del self.positions[position_id]
        result = (gross, closed)
        self._events[event_id] = (fingerprint, result)
        return result

    def mark(self, marks: dict[str, Decimal | int | float | str]) -> dict[str, Decimal]:
        marks = self._require_marks(marks)
        unrealized = {pid: D(pos.side.sign) * pos.quantity * (D(marks[pid]) - pos.average_entry_price)
                      for pid, pos in self.positions.items()}
        return unrealized

    def _require_marks(self, marks: dict[str, Decimal | int | float | str]) -> dict[str, Decimal]:
        missing = set(self.positions) - set(marks)
        if missing:
            raise ValueError(f"current mark required for every open position: {sorted(missing)}")
        converted = {pid: D(value) for pid, value in marks.items()}
        if any(value <= ZERO for value in converted.values()):
            raise ValueError("mark prices must be positive")
        return converted

    @property
    def unrealized_pnl(self) -> Decimal:
        raise ValueError("mark prices required; use equity(marks)")

    def equity(self, marks: dict[str, Decimal | int | float | str]) -> Decimal:
        return self.cash + sum(self.mark(marks).values(), ZERO)

    def used_initial_margin(self, marks: dict[str, Decimal | int | float | str]) -> Decimal:
        marks = self._require_marks(marks)
        return sum((pos.quantity * marks[pid] / pos.leverage for pid, pos in self.positions.items()), ZERO)

    @property
    def reserved_collateral(self) -> Decimal:
        return sum(self.reservations.values(), ZERO)

    def free_collateral_at(self, marks: dict[str, Decimal | int | float | str]) -> Decimal:
        marks = self._require_marks(marks)
        return self.equity(marks) - self.used_initial_margin(marks) - self.reserved_collateral

    def reconcile(self, marks: dict[str, Decimal | int | float | str]) -> dict[str, Decimal]:
        marks = self._require_marks(marks)
        if not self.cash.is_finite() or not self.initial_cash.is_finite():
            raise AssertionError("cash balances must be finite")
        journal_cash = sum((e.amount_usdt for e in self.journal), ZERO)
        cash_delta = self.cash - journal_cash
        equity = self.equity(marks)
        equation_delta = equity - (self.cash + sum(self.mark(marks).values(), ZERO))
        prior_cash = ZERO
        entry_ids = set()
        for sequence, entry in enumerate(self.journal, 1):
            if entry.entry_id in entry_ids:
                raise AssertionError("journal entry IDs must be unique")
            entry_ids.add(entry.entry_id)
            if entry.entry_id != f"{self.account_id}:cash:{sequence:08d}":
                raise AssertionError("journal sequence does not match entry IDs")
            try:
                timestamp = datetime.fromisoformat(entry.event_time.replace("Z", "+00:00"))
            except (TypeError, ValueError) as exc:
                raise AssertionError("journal timestamps must be valid ISO date-times") from exc
            if timestamp.tzinfo is None or timestamp.utcoffset() != timezone.utc.utcoffset(timestamp):
                raise AssertionError("journal timestamps must be normalized to UTC")
            if not entry.amount_usdt.is_finite() or not entry.cash_after_usdt.is_finite():
                raise AssertionError("journal amounts and balances must be finite")
            try:
                entry.as_schema_record()
            except (TypeError, ValueError) as exc:
                raise AssertionError("journal entries must serialize as strict JSON") from exc
            prior_cash += entry.amount_usdt
            if entry.cash_after_usdt != prior_cash:
                raise AssertionError("journal running cash balance mismatch")
        if self._entry_sequence != len(self.journal):
            raise AssertionError("journal sequence counter does not match journal length")

        # Rebuild every position from immutable event fingerprints. This checks
        # quantities and weighted averages independently of the live position
        # objects, then reconstructs the exact expected journal stream so an
        # orphaned or missing financial movement cannot hide in aggregate sums.
        replayed: dict[str, dict[str, Any]] = {}
        expected_rows: list[tuple[str, str, Decimal, str | None]] = []
        for event_id, (fingerprint, _result) in self._events.items():
            operation = fingerprint[0]
            if operation == "INITIAL_DEPOSIT":
                expected_rows.append((event_id, "INITIAL_DEPOSIT", D(fingerprint[1]), None))
            elif operation == "DEPOSIT":
                expected_rows.append((event_id, "DEPOSIT", D(fingerprint[1]), None))
            elif operation == "WITHDRAWAL":
                expected_rows.append((event_id, "WITHDRAWAL", -D(fingerprint[1]), None))
            elif operation == "ENTRY_FILL":
                _, pid, instrument, side, qty, price, leverage, fee, _slippage, _when, _order = fingerprint
                if pid not in replayed:
                    replayed[pid] = {"instrument": instrument, "side": side, "leverage": leverage,
                        "quantity": ZERO, "average": ZERO, "total_entry": ZERO, "entry_fees": ZERO,
                        "exit_fees": ZERO, "funding": ZERO, "realized": ZERO}
                state = replayed[pid]
                if (state["instrument"], state["side"], state["leverage"]) != (instrument, side, leverage):
                    raise AssertionError(f"position identity changed across entry events: {pid}")
                qty, price, fee = D(qty), D(price), D(fee)
                new_quantity = state["quantity"] + qty
                state["average"] = (state["quantity"] * state["average"] + qty * price) / new_quantity
                state["quantity"] = new_quantity
                state["total_entry"] += qty
                state["entry_fees"] += fee
                expected_rows.append((event_id, "ENTRY_FEE", -fee, pid))
            elif operation == "FUNDING":
                _, pid, amount, _when = fingerprint
                if pid not in replayed or replayed[pid]["quantity"] <= ZERO:
                    raise AssertionError(f"funding event has no open position: {pid}")
                amount = D(amount)
                replayed[pid]["funding"] += amount
                expected_rows.append((event_id, "FUNDING", amount, pid))
            elif operation == "EXIT_FILL":
                _, pid, qty, price, fee, _slippage, exit_side, _when, _order, _trade = fingerprint
                if pid not in replayed or replayed[pid]["quantity"] <= ZERO:
                    raise AssertionError(f"exit event has no open position: {pid}")
                state = replayed[pid]
                qty, price, fee = D(qty), D(price), D(fee)
                if qty > state["quantity"]:
                    raise AssertionError(f"exit events exceed entered quantity: {pid}")
                expected_exit_side = Side.SHORT if state["side"] is Side.LONG else Side.LONG
                if exit_side is not expected_exit_side:
                    raise AssertionError(f"exit side is inconsistent with position: {pid}")
                gross = D(state["side"].sign) * qty * (price - state["average"])
                state["quantity"] -= qty
                state["realized"] += gross
                state["exit_fees"] += fee
                expected_rows.append((event_id, "REALIZED_PNL", gross, pid))
                expected_rows.append((event_id, "EXIT_FEE", -fee, pid))
            else:
                raise AssertionError(f"unknown financial event fingerprint: {operation}")

        if len(expected_rows) != len(self.journal):
            raise AssertionError("journal contains missing or unregistered financial movements")
        for expected, actual in zip(expected_rows, self.journal):
            event_id, entry_type, amount, position_id = expected
            if (actual.event_id, actual.entry_type, actual.amount_usdt, actual.position_id) != (
                event_id, entry_type, amount, position_id
            ):
                raise AssertionError("journal stream does not match registered financial events")

        completed_by_id = {item.position_id: item for item in self.completed_positions}
        if len(completed_by_id) != len(self.completed_positions):
            raise AssertionError("completed position identities must be unique")
        if set(self.reservations).intersection(self.released_orders):
            raise AssertionError("released reservations cannot remain active")
        for pid, pos in self.positions.items():
            values = (pos.quantity, pos.average_entry_price, pos.leverage, pos.entry_fees_paid,
                      pos.exit_fees_paid, pos.funding_paid, pos.realized_gross_pnl, pos.total_entry_quantity)
            if any(not value.is_finite() for value in values):
                raise AssertionError(f"position values must be finite: {pid}")
            if pos.quantity <= ZERO or pos.average_entry_price <= ZERO or pos.leverage <= ZERO:
                raise AssertionError(f"invalid open position accounting: {pid}")
            if pos.entry_fees_paid < ZERO or pos.exit_fees_paid < ZERO or pos.total_entry_quantity < pos.quantity:
                raise AssertionError(f"invalid position financial totals: {pid}")
            state = replayed.get(pid)
            if state is None or (pos.quantity, pos.average_entry_price, pos.total_entry_quantity,
                    pos.entry_fees_paid, pos.exit_fees_paid, pos.funding_paid, pos.realized_gross_pnl) != (
                    state["quantity"], state["average"], state["total_entry"], state["entry_fees"],
                    state["exit_fees"], state["funding"], state["realized"]):
                raise AssertionError(f"position lifecycle does not reconcile to event history: {pid}")
            position_entries = [entry for entry in self.journal if entry.position_id == pid]
            entry_fees = -sum((entry.amount_usdt for entry in position_entries if entry.entry_type == "ENTRY_FEE"), ZERO)
            funding = sum((entry.amount_usdt for entry in position_entries if entry.entry_type == "FUNDING"), ZERO)
            realized = sum((entry.amount_usdt for entry in position_entries if entry.entry_type == "REALIZED_PNL"), ZERO)
            exit_fees = -sum((entry.amount_usdt for entry in position_entries if entry.entry_type == "EXIT_FEE"), ZERO)
            if (entry_fees, exit_fees, funding, realized) != (pos.entry_fees_paid, pos.exit_fees_paid, pos.funding_paid, pos.realized_gross_pnl):
                raise AssertionError(f"position-to-journal accounting mismatch: {pid}")
        for completed in self.completed_positions:
            if completed.position_id in self.positions:
                raise AssertionError("completed position identity is duplicated or still active")
            state = replayed.get(completed.position_id)
            completed_values = (completed.quantity_closed, completed.realized_gross_pnl,
                completed.entry_fees_paid, completed.exit_fees_paid, completed.funding_paid, completed.final_net_pnl)
            if any(not value.is_finite() for value in completed_values):
                raise AssertionError("completed position values must be finite")
            if state is None or state["quantity"] != ZERO or completed.quantity_closed != state["total_entry"]:
                raise AssertionError(f"completed position quantity does not reconcile: {completed.position_id}")
            if (completed.realized_gross_pnl, completed.entry_fees_paid, completed.exit_fees_paid,
                    completed.funding_paid) != (state["realized"], state["entry_fees"], state["exit_fees"], state["funding"]):
                raise AssertionError(f"completed position lifecycle does not reconcile: {completed.position_id}")
            entries = [entry for entry in self.journal if entry.position_id == completed.position_id]
            realized = sum((entry.amount_usdt for entry in entries if entry.entry_type == "REALIZED_PNL"), ZERO)
            entry_fees = -sum((entry.amount_usdt for entry in entries if entry.entry_type == "ENTRY_FEE"), ZERO)
            exit_fees = -sum((entry.amount_usdt for entry in entries if entry.entry_type == "EXIT_FEE"), ZERO)
            funding = sum((entry.amount_usdt for entry in entries if entry.entry_type == "FUNDING"), ZERO)
            net = realized - entry_fees - exit_fees + funding
            if (realized, entry_fees, exit_fees, funding, net) != (
                completed.realized_gross_pnl, completed.entry_fees_paid, completed.exit_fees_paid,
                completed.funding_paid, completed.final_net_pnl
            ):
                raise AssertionError(f"completed position-to-journal reconciliation mismatch: {completed.position_id}")
        if set(replayed) != set(self.positions).union(completed_by_id):
            raise AssertionError("position event history has no matching active or completed position")
        if any(value < ZERO or not value.is_finite() for value in self.reservations.values()):
            raise AssertionError("invalid reservation balance")
        journal_by_type = {kind: sum((e.amount_usdt for e in self.journal if e.entry_type == kind), ZERO)
                           for kind in ("REALIZED_PNL", "ENTRY_FEE", "EXIT_FEE", "FUNDING")}
        if any(not value.is_finite() for value in journal_by_type.values()):
            raise AssertionError("non-finite journal totals")
        used_margin = self.used_initial_margin(marks)
        free_collateral = equity - used_margin - self.reserved_collateral
        active_realized = sum((pos.realized_gross_pnl for pos in self.positions.values()), ZERO)
        active_entry_fees = sum((pos.entry_fees_paid for pos in self.positions.values()), ZERO)
        active_exit_fees = sum((pos.exit_fees_paid for pos in self.positions.values()), ZERO)
        active_funding = sum((pos.funding_paid for pos in self.positions.values()), ZERO)
        completed_realized = sum((pos.realized_gross_pnl for pos in self.completed_positions), ZERO)
        completed_entry_fees = sum((pos.entry_fees_paid for pos in self.completed_positions), ZERO)
        completed_exit_fees = sum((pos.exit_fees_paid for pos in self.completed_positions), ZERO)
        completed_funding = sum((pos.funding_paid for pos in self.completed_positions), ZERO)
        realized_delta = journal_by_type["REALIZED_PNL"] - active_realized - completed_realized
        entry_fees_delta = -journal_by_type["ENTRY_FEE"] - active_entry_fees - completed_entry_fees
        exit_fees_delta = -journal_by_type["EXIT_FEE"] - active_exit_fees - completed_exit_fees
        funding_delta = journal_by_type["FUNDING"] - active_funding - completed_funding
        completed_net_delta = sum((pos.final_net_pnl - (pos.realized_gross_pnl - pos.entry_fees_paid
            - pos.exit_fees_paid + pos.funding_paid) for pos in self.completed_positions), ZERO)
        lifecycle_quantity_delta = sum((
            self.positions[pid].quantity - state["quantity"] if pid in self.positions else state["quantity"]
            for pid, state in replayed.items()
        ), ZERO)
        deltas = (cash_delta, equation_delta, realized_delta, entry_fees_delta, exit_fees_delta,
                  funding_delta, completed_net_delta, lifecycle_quantity_delta)
        if any(delta != ZERO for delta in deltas):
            raise AssertionError(f"ledger reconciliation failed: deltas={deltas}")
        return {"cash_delta": cash_delta, "equity_delta": equation_delta, "cash": self.cash,
                "unrealized_gross_pnl": sum(self.mark(marks).values(), ZERO), "equity": equity,
                "realized_pnl": journal_by_type["REALIZED_PNL"], "entry_fees": journal_by_type["ENTRY_FEE"],
                "exit_fees": journal_by_type["EXIT_FEE"], "funding": journal_by_type["FUNDING"],
                "realized_pnl_delta": realized_delta, "entry_fees_delta": entry_fees_delta,
                "exit_fees_delta": exit_fees_delta, "funding_delta": funding_delta,
                "completed_net_pnl_delta": completed_net_delta,
                "lifecycle_quantity_delta": lifecycle_quantity_delta,
                "used_margin": used_margin, "reservations": self.reserved_collateral,
                "free_collateral": free_collateral,
                "completed_position_net_pnl": sum((p.final_net_pnl for p in self.completed_positions), ZERO)}

    def execution_fill(self, model: BarExecutionModel, raw_price: float, order_side: Side, quantity: float) -> ExecutionFill:
        """Route trade fills through the canonical Phase 2 execution policy."""
        return model.fill(raw_price, order_side, quantity)
