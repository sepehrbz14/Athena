"""Canonical deterministic OHLC execution policy shared by Athena simulators.

This module owns bar-level fill prices, fees, slippage, funding accrual,
participation limits, and conservative stop/target resolution. It has no
portfolio state and does not decide whether an order is accepted or how an
account allocates capital.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Sequence

from .models import Candle, Position, Side, Trade


@dataclass(frozen=True, slots=True)
class ExecutionFill:
    """One simulated fill and its explicit execution costs."""

    raw_price: float
    price: float
    quantity: float
    fee: float
    slippage: float
    order_side: Side


def valid_prior_volume(
    candles: Sequence[Candle], index: int, candle_minutes: int = 15,
) -> tuple[float | None, bool]:
    """Return only the immediately prior completed bar's contiguous volume.

    The second value marks a stale preceding bar after a data discontinuity.
    This causal participation policy is shared by the backtester and research
    evaluator.
    """
    if index <= 0:
        return None, False
    prior, current = candles[index - 1], candles[index]
    if current.timestamp - prior.timestamp != timedelta(minutes=candle_minutes):
        return None, True
    return prior.volume, False


@dataclass(frozen=True, slots=True)
class BarExecutionModel:
    """Immutable execution parameters and shared OHLC execution rules.

    Construct one from the legacy ``BacktestConfig`` values or equivalent
    portfolio execution configuration. Rates are decimal fractions except
    ``slippage_bps``.
    """

    fee_rate: float = 0.0005
    slippage_bps: float = 5.0
    funding_rate_per_8h: float = 0.0001
    candle_minutes: int = 15
    max_participation_rate: float = 0.05

    def slipped_price(self, raw_price: float, order_side: Side) -> float:
        adjustment = self.slippage_bps / 10_000
        return raw_price * (1 + adjustment if order_side is Side.LONG else 1 - adjustment)

    def fill(self, raw_price: float, order_side: Side, quantity: float) -> ExecutionFill:
        price = self.slipped_price(raw_price, order_side)
        return ExecutionFill(
            raw_price=raw_price,
            price=price,
            quantity=quantity,
            fee=quantity * price * self.fee_rate,
            slippage=abs(price - raw_price) * quantity,
            order_side=order_side,
        )

    def participation_quantity(self, prior_volume: float | None) -> float:
        return (prior_volume or 0.0) * self.max_participation_rate

    @staticmethod
    def funding_amount(
        side: Side,
        quantity: float,
        mark_price: float,
        funding_rate_per_8h: float,
        elapsed_hours: float,
    ) -> float:
        """Signed funding cash amount: positive is a credit, negative a debit."""
        return side.sign * quantity * mark_price * funding_rate_per_8h * max(0.0, elapsed_hours) / 8

    def accrue_funding(self, position: Position, candle: Candle) -> float:
        """Apply the existing elapsed-time funding approximation to a position."""
        previous = position.last_funding_timestamp or position.opened_at
        funding_timestamp = candle.timestamp + timedelta(minutes=self.candle_minutes)
        elapsed_hours = max(0.0, (funding_timestamp - previous).total_seconds() / 3600)
        charge = self.funding_amount(
            position.side,
            position.quantity,
            candle.close,
            self.funding_rate_per_8h,
            elapsed_hours,
        )
        position.funding_paid += charge
        position.last_funding_timestamp = funding_timestamp
        return charge

    @staticmethod
    def intrabar_exit_request(
        side: Side,
        stop_price: float,
        take_profit: float | None,
        candle: Candle,
    ) -> tuple[float, str] | None:
        """Resolve OHLC stop/target touches, preferring the adverse stop."""
        if side is Side.LONG:
            stopped = candle.low <= stop_price
            targeted = take_profit is not None and candle.high >= take_profit
            if stopped:
                return min(stop_price, candle.open), "stop_loss"
            if targeted:
                return take_profit or candle.high, "take_profit"
        else:
            stopped = candle.high >= stop_price
            targeted = take_profit is not None and candle.low <= take_profit
            if stopped:
                return max(stop_price, candle.open), "stop_loss"
            if targeted:
                return take_profit or candle.low, "take_profit"
        return None

    @staticmethod
    def opening_stop_gap_request(side: Side, stop_price: float, candle: Candle) -> tuple[float, str] | None:
        """Resolve only a stop crossed at the observed bar open.

        This lets portfolio orchestration close risk before allocating pending
        entries while keeping gap-through pricing in the canonical execution
        component. Intrabar stop/target collisions still use
        :meth:`intrabar_exit_request`.
        """
        if side is Side.LONG and candle.open <= stop_price:
            return min(stop_price, candle.open), "stop_loss"
        if side is Side.SHORT and candle.open >= stop_price:
            return max(stop_price, candle.open), "stop_loss"
        return None

    def position_exit_request(self, position: Position, candle: Candle) -> tuple[float, str] | None:
        return self.intrabar_exit_request(position.side, position.stop_price, position.take_profit, candle)

    def close_position(
        self,
        position: Position,
        timestamp: datetime,
        raw_price: float,
        reason: str,
    ) -> Trade:
        exit_side = Side.SHORT if position.side is Side.LONG else Side.LONG
        fill = self.fill(raw_price, exit_side, position.quantity)
        gross_pnl = position.side.sign * position.quantity * (fill.price - position.entry_price)
        net_pnl = gross_pnl - position.entry_fee - fill.fee - position.funding_paid
        return Trade(
            side=position.side,
            quantity=position.quantity,
            entry_time=position.opened_at,
            exit_time=timestamp,
            entry_price=position.entry_price,
            exit_price=fill.price,
            stop_price=position.stop_price,
            take_profit=position.take_profit,
            strategy=position.strategy,
            exit_reason=reason,
            gross_pnl=gross_pnl,
            entry_fee=position.entry_fee,
            exit_fee=fill.fee,
            funding_paid=position.funding_paid,
            net_pnl=net_pnl,
            trade_id=position.trade_id,
            signal_id=position.signal_id,
            order_id=position.order_id,
        )
