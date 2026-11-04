"""Pure calculations used by the current-strategy evaluation report."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from statistics import mean, median
from typing import Iterable, Mapping, Sequence

from .models import EquityPoint, Trade


def trade_metrics(trades: Sequence[Mapping[str, object]]) -> dict[str, float | int | None]:
    """Summarize already normalized trade rows; net P&L is the scoring basis."""
    pnls = [float(row["net_pnl"]) for row in trades]
    wins = [pnl for pnl in pnls if pnl > 0]
    losses = [pnl for pnl in pnls if pnl < 0]
    gross = sum(float(row["raw_gross_pnl"]) for row in trades)
    slippage = sum(float(row["slippage_cost"]) for row in trades)
    fees = sum(float(row["fees"]) for row in trades)
    funding = sum(float(row["funding_paid"]) for row in trades)
    durations = [float(row["holding_hours"]) for row in trades]
    max_loss_streak = streak = 0
    for pnl in pnls:
        streak = streak + 1 if pnl < 0 else 0
        max_loss_streak = max(max_loss_streak, streak)
    return {
        "trades": len(trades),
        "raw_gross_pnl": gross,
        "execution_gross_pnl": sum(float(row["gross_pnl"]) for row in trades),
        "slippage": slippage,
        "fees": fees,
        "funding_paid": funding,
        "net_pnl": sum(pnls),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / len(pnls) if pnls else 0.0,
        "average_win": mean(wins) if wins else None,
        "average_loss": mean(losses) if losses else None,
        "profit_factor": sum(wins) / abs(sum(losses)) if losses else (float("inf") if wins else None),
        "expectancy": mean(pnls) if pnls else None,
        "max_consecutive_losses": max_loss_streak,
        "mean_holding_hours": mean(durations) if durations else None,
        "median_holding_hours": median(durations) if durations else None,
        "reconciliation_delta": gross - slippage - fees - funding - sum(pnls),
    }


def monthly_equity(points: Sequence[EquityPoint], initial_equity: float) -> list[dict[str, object]]:
    """Calendar-month close and within-month drawdown from the equity curve."""
    buckets: dict[str, list[EquityPoint]] = defaultdict(list)
    for point in points:
        buckets[point.timestamp.strftime("%Y-%m")].append(point)
    rows: list[dict[str, object]] = []
    previous = initial_equity
    for month in sorted(buckets):
        entries = buckets[month]
        closing = entries[-1].equity
        rows.append({
            "month": month,
            "equity_end": closing,
            "return_pct": closing / previous - 1 if previous else 0.0,
            "max_drawdown_pct": max(point.drawdown_pct for point in entries),
        })
        previous = closing
    return rows


def normalize_trade(trade: Trade, slippage_rate: float) -> dict[str, object]:
    """Reconstruct pre-slippage prices and assert the ledger accounting identity."""
    side_sign = trade.side.sign
    raw_entry = trade.entry_price / (1 + side_sign * slippage_rate)
    raw_exit = trade.exit_price / (1 - side_sign * slippage_rate)
    raw_gross = side_sign * trade.quantity * (raw_exit - raw_entry)
    slip = trade.quantity * (abs(trade.entry_price - raw_entry) + abs(trade.exit_price - raw_exit))
    fees = trade.entry_fee + trade.exit_fee
    duration = (trade.exit_time - trade.entry_time).total_seconds() / 3600
    net_expected = raw_gross - slip - fees - trade.funding_paid
    return {
        "trade_id": trade.trade_id,
        "signal_id": trade.signal_id,
        "order_id": trade.order_id,
        "strategy": trade.strategy,
        "side": trade.side.value,
        "entry_time": trade.entry_time.isoformat(),
        "exit_time": trade.exit_time.isoformat(),
        "entry_price": trade.entry_price,
        "exit_price": trade.exit_price,
        "quantity": trade.quantity,
        "exit_reason": trade.exit_reason,
        "gross_pnl": trade.gross_pnl,
        "raw_gross_pnl": raw_gross,
        "slippage_cost": slip,
        "entry_fee": trade.entry_fee,
        "exit_fee": trade.exit_fee,
        "fees": fees,
        "funding_paid": trade.funding_paid,
        "net_pnl": trade.net_pnl,
        "holding_hours": duration,
        "raw_accounting_net": net_expected,
        "regime": None,
    }


def assert_trade_reconciliation(rows: Iterable[Mapping[str, object]], tolerance: float = 1e-7) -> None:
    for row in rows:
        delta = float(row["raw_gross_pnl"]) - float(row["slippage_cost"]) - float(row["fees"])
        delta -= float(row["funding_paid"]) + float(row["net_pnl"])
        if abs(delta) > tolerance:
            raise AssertionError(f"trade {row.get('trade_id')} does not reconcile: {delta}")


def fill_cost_totals(fills: Iterable[Mapping[str, float]], fee_rate: float) -> dict[str, float]:
    """Calculate fees and modeled price impact independently from fill rows.

    Each row must contain the actual executed ``price``, causal/reference
    ``raw_price``, and ``quantity``. This deliberately does not read Trade
    accounting fields, so it can be used to reconcile an exported fill ledger.
    """
    notional = fees = slippage = 0.0
    for fill in fills:
        price = float(fill["price"])
        raw_price = float(fill["raw_price"])
        quantity = float(fill["quantity"])
        if min(price, raw_price, quantity) < 0:
            raise ValueError("fill prices and quantity cannot be negative")
        notional += price * quantity
        fees += price * quantity * fee_rate
        slippage += abs(price - raw_price) * quantity
    return {"notional": notional, "fees": fees, "slippage": slippage}

