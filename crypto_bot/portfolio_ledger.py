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
from typing import Any

from .execution import BarExecutionModel, ExecutionFill
from .models import Side


ZERO = Decimal("0")


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
        return {k: (float(v) if isinstance(v, Decimal) else v) for k, v in {
            "schema_version": self.schema_version, "entry_id": self.entry_id,
            "event_id": self.event_id, "event_time": self.event_time,
            "account_id": self.account_id, "asset": self.asset,
            "entry_type": self.entry_type, "amount_usdt": self.amount_usdt,
            "cash_after_usdt": self.cash_after_usdt, "instrument_id": self.instrument_id,
            "source_signal_id": self.source_signal_id, "order_id": self.order_id,
            "position_id": self.position_id, "trade_id": self.trade_id, "memo": self.memo,
        }.items()}


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

    def __post_init__(self) -> None:
        self.initial_cash = D(self.initial_cash)
        if not self.account_id or self.initial_cash <= ZERO:
            raise ValueError("account ID and positive initial cash are required")
        self.cash = ZERO
        self._post("INITIAL_DEPOSIT", self.initial_cash, datetime(1970, 1, 1, tzinfo=timezone.utc), "initial", memo="initial account funding")
        self._events["initial"] = (("INITIAL_DEPOSIT", self.initial_cash), self.journal[0])

    def _post(self, entry_type: str, amount: Decimal, when: datetime, event_id: str,
              *, instrument_id: str | None = None, position_id: str | None = None,
              order_id: str | None = None, trade_id: str | None = None, memo: str | None = None) -> CashJournalEntry:
        if not event_id or when.tzinfo is None or when.utcoffset() is None:
            raise ValueError("journal event time must be timezone-aware")
        amount = D(amount)
        self.cash += amount
        self._entry_sequence += 1
        entry = CashJournalEntry("athena.cash_journal_entry.v1", f"{self.account_id}:cash:{self._entry_sequence:08d}",
            event_id, when.astimezone(timezone.utc).isoformat(), self.account_id, "USDT", entry_type,
            amount, self.cash, instrument_id, order_id=order_id, position_id=position_id, trade_id=trade_id, memo=memo)
        self.journal.append(entry)
        return entry

    def deposit(self, amount: Decimal | int | float | str, when: datetime, event_id: str) -> CashJournalEntry:
        amount = D(amount)
        if amount <= ZERO: raise ValueError("deposit must be positive")
        return self._event(event_id, ("DEPOSIT", amount, when), lambda: self._post("DEPOSIT", amount, when, event_id))

    def withdraw(self, amount: Decimal | int | float | str, when: datetime, event_id: str, *, marks: dict[str, Any]) -> CashJournalEntry:
        amount = D(amount)
        if amount <= ZERO: raise ValueError("withdrawal must be positive")
        marks = self._require_marks(marks)
        if amount > self.free_collateral_at(marks): raise ValueError("withdrawal exceeds free collateral")
        return self._event(event_id, ("WITHDRAWAL", amount, when), lambda: self._post("WITHDRAWAL", -amount, when, event_id))

    def reserve(self, order_id: str, amount: Decimal | int | float | str, *, marks: dict[str, Any]) -> Decimal:
        amount = D(amount)
        marks = self._require_marks(marks)
        if amount < ZERO: raise ValueError("reservation cannot be negative")
        if order_id in self.reservations: raise ValueError("order already has a reservation")
        if amount > self.free_collateral_at(marks): raise ValueError("reservation exceeds free collateral")
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

    def release_reservation(self, order_id: str) -> Decimal:
        return self.reservations.pop(order_id, ZERO)

    def apply_entry_fill(self, *, position_id: str, instrument_id: str, side: Side,
                         fill: ExecutionFill, leverage: Decimal | int | float | str,
                         when: datetime, event_id: str, order_id: str | None = None) -> LedgerPosition:
        qty, price, lev, fee = D(fill.quantity), D(fill.price), D(leverage), D(fill.fee)
        if qty <= ZERO or price <= ZERO or lev <= ZERO or fee < ZERO: raise ValueError("fill values must be positive with nonnegative fees")
        if fill.order_side is not side: raise ValueError("entry fill direction does not match position")
        fingerprint = ("ENTRY_FILL", position_id, instrument_id, side, qty, price, lev, fee, when, order_id)
        prior = self._events.get(event_id)
        if prior is not None:
            if prior[0] != fingerprint: raise ValueError(f"conflicting replay for event ID {event_id}")
            return self.positions.get(position_id, prior[1])
        pos = self.positions.get(position_id)
        if pos is None:
            pos = LedgerPosition(position_id, instrument_id, side, ZERO, ZERO, lev)
            self.positions[position_id] = pos
        if (pos.instrument_id, pos.side, pos.leverage) != (instrument_id, side, lev):
            raise ValueError("position increase must preserve instrument, side, and leverage")
        new_qty = pos.quantity + qty
        pos.average_entry_price = (pos.quantity * pos.average_entry_price + qty * price) / new_qty
        pos.quantity = new_qty
        pos.entry_fees_paid += fee
        self._post("ENTRY_FEE", -fee, when, event_id, instrument_id=instrument_id, position_id=position_id, order_id=order_id)
        self._events[event_id] = (fingerprint, pos)
        return pos

    def apply_funding(self, position_id: str, amount: Decimal | int | float | str,
                      when: datetime, event_id: str) -> CashJournalEntry:
        pos = self.positions[position_id]
        amount = D(amount)
        fingerprint = ("FUNDING", position_id, amount, when)
        prior = self._events.get(event_id)
        if prior is not None:
            if prior[0] != fingerprint: raise ValueError(f"conflicting replay for event ID {event_id}")
            return prior[1]
        pos.funding_paid += amount
        result = self._post("FUNDING", amount, when, event_id, instrument_id=pos.instrument_id, position_id=position_id)
        self._events[event_id] = (fingerprint, result)
        return result

    def apply_exit_fill(self, position_id: str, fill: ExecutionFill, when: datetime,
                        event_id: str, *, order_id: str | None = None, trade_id: str | None = None) -> tuple[Decimal, bool]:
        pos = self.positions[position_id]
        qty, price, fee = D(fill.quantity), D(fill.price), D(fill.fee)
        if qty <= ZERO or price <= ZERO or fee < ZERO or qty > pos.quantity: raise ValueError("exit fill values are invalid")
        expected_side = Side.SHORT if pos.side is Side.LONG else Side.LONG
        if fill.order_side is not expected_side: raise ValueError("exit fill direction must oppose position")
        fingerprint = ("EXIT_FILL", position_id, qty, price, fee, fill.order_side, when, order_id, trade_id)
        prior = self._events.get(event_id)
        if prior is not None:
            if prior[0] != fingerprint: raise ValueError(f"conflicting replay for event ID {event_id}")
            return prior[1]
        gross = D(pos.side.sign) * qty * (price - pos.average_entry_price)
        pos.quantity -= qty
        pos.realized_gross_pnl += gross
        self._post("REALIZED_PNL", gross, when, event_id, instrument_id=pos.instrument_id, position_id=position_id, order_id=order_id, trade_id=trade_id)
        self._post("EXIT_FEE", -fee, when, event_id, instrument_id=pos.instrument_id, position_id=position_id, order_id=order_id, trade_id=trade_id)
        closed = pos.quantity == ZERO
        if closed: del self.positions[position_id]
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
        journal_cash = sum((e.amount_usdt for e in self.journal), ZERO)
        cash_delta = self.cash - journal_cash
        equity = self.equity(marks)
        equation_delta = equity - (self.cash + sum(self.mark(marks).values(), ZERO))
        prior_cash = ZERO
        for entry in self.journal:
            prior_cash += entry.amount_usdt
            if entry.cash_after_usdt != prior_cash:
                raise AssertionError("journal running cash balance mismatch")
        for pid, pos in self.positions.items():
            if pos.quantity <= ZERO or pos.average_entry_price <= ZERO or pos.leverage <= ZERO:
                raise AssertionError(f"invalid open position accounting: {pid}")
            if pos.entry_fees_paid < ZERO or not pos.funding_paid.is_finite() or not pos.realized_gross_pnl.is_finite():
                raise AssertionError(f"invalid position financial totals: {pid}")
            position_entries = [entry for entry in self.journal if entry.position_id == pid]
            entry_fees = -sum((entry.amount_usdt for entry in position_entries if entry.entry_type == "ENTRY_FEE"), ZERO)
            funding = sum((entry.amount_usdt for entry in position_entries if entry.entry_type == "FUNDING"), ZERO)
            realized = sum((entry.amount_usdt for entry in position_entries if entry.entry_type == "REALIZED_PNL"), ZERO)
            if (entry_fees, funding, realized) != (pos.entry_fees_paid, pos.funding_paid, pos.realized_gross_pnl):
                raise AssertionError(f"position-to-journal accounting mismatch: {pid}")
        if any(value < ZERO or not value.is_finite() for value in self.reservations.values()):
            raise AssertionError("invalid reservation balance")
        journal_by_type = {kind: sum((e.amount_usdt for e in self.journal if e.entry_type == kind), ZERO)
                           for kind in ("REALIZED_PNL", "ENTRY_FEE", "EXIT_FEE", "FUNDING")}
        if any(not value.is_finite() for value in journal_by_type.values()):
            raise AssertionError("non-finite journal totals")
        used_margin = self.used_initial_margin(marks)
        free_collateral = equity - used_margin - self.reserved_collateral
        if cash_delta != ZERO or equation_delta != ZERO:
            raise AssertionError(f"ledger reconciliation failed: cash={cash_delta}, equity={equation_delta}")
        return {"cash_delta": cash_delta, "equity_delta": equation_delta, "cash": self.cash,
                "unrealized_gross_pnl": sum(self.mark(marks).values(), ZERO), "equity": equity,
                "realized_pnl": journal_by_type["REALIZED_PNL"], "entry_fees": journal_by_type["ENTRY_FEE"],
                "exit_fees": journal_by_type["EXIT_FEE"], "funding": journal_by_type["FUNDING"],
                "used_margin": used_margin, "reservations": self.reserved_collateral,
                "free_collateral": free_collateral}

    def execution_fill(self, model: BarExecutionModel, raw_price: float, order_side: Side, quantity: float) -> ExecutionFill:
        """Route trade fills through the canonical Phase 2 execution policy."""
        return model.fill(raw_price, order_side, quantity)
