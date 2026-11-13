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
from typing import Any

from .execution import BarExecutionModel, ExecutionFill
from .models import Side


ZERO = Decimal("0")


def D(value: Decimal | int | float | str) -> Decimal:
    """Convert an external scalar without importing its binary expansion."""
    return value if isinstance(value, Decimal) else Decimal(str(value))


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

    def __post_init__(self) -> None:
        self.initial_cash = D(self.initial_cash)
        self.cash = ZERO
        self._post("INITIAL_DEPOSIT", self.initial_cash, datetime(1970, 1, 1, tzinfo=timezone.utc), "initial", memo="initial account funding")

    def _post(self, entry_type: str, amount: Decimal, when: datetime, event_id: str,
              *, instrument_id: str | None = None, position_id: str | None = None,
              order_id: str | None = None, trade_id: str | None = None, memo: str | None = None) -> CashJournalEntry:
        if when.tzinfo is None or when.utcoffset() is None:
            raise ValueError("journal event time must be timezone-aware")
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
        return self._post("DEPOSIT", amount, when, event_id)

    def withdraw(self, amount: Decimal | int | float | str, when: datetime, event_id: str) -> CashJournalEntry:
        amount = D(amount)
        if amount <= ZERO or amount > self.free_collateral: raise ValueError("withdrawal exceeds free collateral")
        return self._post("WITHDRAWAL", -amount, when, event_id)

    def reserve(self, order_id: str, amount: Decimal | int | float | str) -> Decimal:
        amount = D(amount)
        if amount < ZERO: raise ValueError("reservation cannot be negative")
        if order_id in self.reservations: raise ValueError("order already has a reservation")
        if amount > self.free_collateral: raise ValueError("reservation exceeds free collateral")
        self.reservations[order_id] = amount
        return amount

    def release_reservation(self, order_id: str) -> Decimal:
        return self.reservations.pop(order_id, ZERO)

    def apply_entry_fill(self, *, position_id: str, instrument_id: str, side: Side,
                         fill: ExecutionFill, leverage: Decimal | int | float | str,
                         when: datetime, event_id: str, order_id: str | None = None) -> LedgerPosition:
        qty, price, lev, fee = D(fill.quantity), D(fill.price), D(leverage), D(fill.fee)
        if qty <= ZERO or price <= ZERO or lev <= ZERO: raise ValueError("fill quantity, price, and leverage must be positive")
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
        return pos

    def apply_funding(self, position_id: str, amount: Decimal | int | float | str,
                      when: datetime, event_id: str) -> CashJournalEntry:
        pos = self.positions[position_id]
        amount = D(amount)
        pos.funding_paid += amount
        return self._post("FUNDING", amount, when, event_id, instrument_id=pos.instrument_id, position_id=position_id)

    def apply_exit_fill(self, position_id: str, fill: ExecutionFill, when: datetime,
                        event_id: str, *, order_id: str | None = None, trade_id: str | None = None) -> tuple[Decimal, bool]:
        pos = self.positions[position_id]
        qty, price, fee = D(fill.quantity), D(fill.price), D(fill.fee)
        if qty <= ZERO or qty > pos.quantity: raise ValueError("exit quantity must be positive and no greater than open quantity")
        gross = D(pos.side.sign) * qty * (price - pos.average_entry_price)
        pos.quantity -= qty
        pos.realized_gross_pnl += gross
        self._post("REALIZED_PNL", gross, when, event_id, instrument_id=pos.instrument_id, position_id=position_id, order_id=order_id, trade_id=trade_id)
        self._post("EXIT_FEE", -fee, when, event_id, instrument_id=pos.instrument_id, position_id=position_id, order_id=order_id, trade_id=trade_id)
        closed = pos.quantity == ZERO
        if closed: del self.positions[position_id]
        return gross, closed

    def mark(self, marks: dict[str, Decimal | int | float | str]) -> dict[str, Decimal]:
        unrealized = {pid: D(pos.side.sign) * pos.quantity * (D(marks[pid]) - pos.average_entry_price)
                      for pid, pos in self.positions.items() if pid in marks}
        return unrealized

    @property
    def unrealized_pnl(self) -> Decimal:
        raise ValueError("mark prices required; use equity(marks)")

    def equity(self, marks: dict[str, Decimal | int | float | str]) -> Decimal:
        return self.cash + sum(self.mark(marks).values(), ZERO)

    def used_initial_margin(self, marks: dict[str, Decimal | int | float | str]) -> Decimal:
        return sum((pos.quantity * D(marks[pid]) / pos.leverage for pid, pos in self.positions.items() if pid in marks), ZERO)

    @property
    def reserved_collateral(self) -> Decimal:
        return sum(self.reservations.values(), ZERO)

    def free_collateral_at(self, marks: dict[str, Decimal | int | float | str]) -> Decimal:
        return self.cash - self.used_initial_margin(marks) - self.reserved_collateral

    @property
    def free_collateral(self) -> Decimal:
        # Reservation/deposit operations without a mark use entry prices as the deterministic mark.
        marks = {pid: pos.average_entry_price for pid, pos in self.positions.items()}
        return self.free_collateral_at(marks)

    def reconcile(self, marks: dict[str, Decimal | int | float | str]) -> dict[str, Decimal]:
        journal_cash = sum((e.amount_usdt for e in self.journal), ZERO)
        cash_delta = self.cash - journal_cash
        equity = self.equity(marks)
        equation_delta = equity - (self.cash + sum(self.mark(marks).values(), ZERO))
        if cash_delta != ZERO or equation_delta != ZERO:
            raise AssertionError(f"ledger reconciliation failed: cash={cash_delta}, equity={equation_delta}")
        return {"cash_delta": cash_delta, "equity_delta": equation_delta, "cash": self.cash,
                "unrealized_gross_pnl": sum(self.mark(marks).values(), ZERO), "equity": equity}

    def execution_fill(self, model: BarExecutionModel, raw_price: float, order_side: Side, quantity: float) -> ExecutionFill:
        """Route trade fills through the canonical Phase 2 execution policy."""
        return model.fill(raw_price, order_side, quantity)
