from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class Side(str, Enum):
    LONG = "long"
    SHORT = "short"

    @property
    def sign(self) -> int:
        return 1 if self is Side.LONG else -1


class EntryRejectionReason(str, Enum):
    ALREADY_IN_POSITION = "ALREADY_IN_POSITION"
    KILL_SWITCH_ACTIVE = "KILL_SWITCH_ACTIVE"
    INSUFFICIENT_CASH = "INSUFFICIENT_CASH"
    POSITION_SIZE_ZERO = "POSITION_SIZE_ZERO"
    RISK_LIMIT_EXCEEDED = "RISK_LIMIT_EXCEEDED"
    MAX_POSITIONS_REACHED = "MAX_POSITIONS_REACHED"
    INVALID_PRICE = "INVALID_PRICE"
    INVALID_SIZE = "INVALID_SIZE"
    INVALID_SIGNAL = "INVALID_SIGNAL"
    ORDER_VALUE_TOO_SMALL = "ORDER_VALUE_TOO_SMALL"
    MARGIN_REQUIREMENT_FAILED = "MARGIN_REQUIREMENT_FAILED"


@dataclass(frozen=True, slots=True)
class Candle:
    """A completed OHLCV candle labeled by its opening timestamp.

    Volume is measured in the base asset. Backtests treat the candle as
    available at its close, one ``candle_minutes`` interval after this label.
    """

    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float | None = None

    def __post_init__(self) -> None:
        if min(self.open, self.high, self.low, self.close) <= 0:
            raise ValueError("OHLC prices must be positive")
        if self.low > min(self.open, self.close) or self.high < max(self.open, self.close):
            raise ValueError("Candle high/low do not contain open and close")
        if self.volume is not None and self.volume < 0:
            raise ValueError("Volume cannot be negative")


@dataclass(frozen=True, slots=True)
class Signal:
    """A signal emitted at a completed candle close and filled no earlier than next open."""

    side: Side
    stop_price: float
    take_profit: float | None
    confidence: float
    strategy: str
    rationale: str

    def __post_init__(self) -> None:
        if not 0 < self.confidence <= 1:
            raise ValueError("confidence must be in (0, 1]")
        if self.stop_price <= 0:
            raise ValueError("stop_price must be positive")
        if self.take_profit is not None and self.take_profit <= 0:
            raise ValueError("take_profit must be positive")


@dataclass(slots=True)
class Position:
    side: Side
    quantity: float
    entry_price: float
    stop_price: float
    take_profit: float | None
    opened_at: datetime
    strategy: str
    entry_fee: float
    funding_paid: float = 0.0
    trade_id: int | None = None
    signal_id: int | None = None
    order_id: int | None = None
    last_funding_timestamp: datetime | None = None

    @property
    def notional(self) -> float:
        return self.quantity * self.entry_price

    def unrealized_gross_pnl(self, mark_price: float) -> float:
        return self.side.sign * self.quantity * (mark_price - self.entry_price)


@dataclass(frozen=True, slots=True)
class Trade:
    side: Side
    quantity: float
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    stop_price: float
    take_profit: float | None
    strategy: str
    exit_reason: str
    gross_pnl: float
    entry_fee: float
    exit_fee: float
    funding_paid: float
    net_pnl: float
    trade_id: int | None = None
    signal_id: int | None = None
    order_id: int | None = None

    @property
    def total_cost(self) -> float:
        return self.entry_fee + self.exit_fee + self.funding_paid


@dataclass(frozen=True, slots=True)
class EquityPoint:
    timestamp: datetime
    equity: float
    drawdown_pct: float


@dataclass(frozen=True, slots=True)
class SizingResult:
    quantity: float
    risk_budget: float
    risk_per_unit: float
    expected_loss_at_stop: float
    notional: float
    required_collateral: float
    was_margin_capped: bool
    explanation: str


@dataclass(frozen=True, slots=True)
class RejectedOrder:
    timestamp: datetime
    strategy: str
    reason: EntryRejectionReason | str


@dataclass(slots=True)
class BacktestResult:
    initial_equity: float
    final_equity: float
    trades: list[Trade] = field(default_factory=list)
    equity_curve: list[EquityPoint] = field(default_factory=list)
    rejected_orders: list[RejectedOrder] = field(default_factory=list)
    kill_events: list[tuple[datetime, str, str]] = field(default_factory=list)
    partial_fills: int = 0
    diagnostics: dict[str, object] = field(default_factory=dict)

    @property
    def net_pnl(self) -> float:
        return self.final_equity - self.initial_equity

    @property
    def return_pct(self) -> float:
        return self.net_pnl / self.initial_equity if self.initial_equity else 0.0

    @property
    def win_rate(self) -> float:
        return sum(t.net_pnl > 0 for t in self.trades) / len(self.trades) if self.trades else 0.0

    @property
    def max_drawdown_pct(self) -> float:
        return max((point.drawdown_pct for point in self.equity_curve), default=0.0)
