from __future__ import annotations

from dataclasses import dataclass

from .models import Side, SizingResult


@dataclass(frozen=True, slots=True)
class RiskConfig:
    """Risk limits expressed independently from leverage."""

    risk_per_trade_pct: float = 0.005
    max_position_notional_pct: float = 1.0
    max_margin_fraction: float = 0.80
    leverage: float = 1.0
    estimated_round_trip_cost_pct: float = 0.0012
    min_quantity: float = 0.000001

    def __post_init__(self) -> None:
        if not 0 < self.risk_per_trade_pct <= 0.05:
            raise ValueError("risk_per_trade_pct must be in (0, 0.05]")
        if not 0 < self.max_position_notional_pct:
            raise ValueError("max_position_notional_pct must be positive")
        if not 0 < self.max_margin_fraction <= 1:
            raise ValueError("max_margin_fraction must be in (0, 1]")
        if not 0 < self.leverage:
            raise ValueError("leverage must be positive")
        if self.estimated_round_trip_cost_pct < 0:
            raise ValueError("estimated_round_trip_cost_pct cannot be negative")


class PositionSizer:
    """Sizes from stop-loss risk, then verifies notional and collateral caps.

    Leverage is intentionally used only in `required_collateral`; it cannot
    increase the monetary loss allowed at the invalidation point.
    """

    def __init__(self, config: RiskConfig | None = None) -> None:
        self.config = config or RiskConfig()

    def size(
        self,
        *,
        equity: float,
        entry_price: float,
        stop_price: float,
        side: Side,
    ) -> SizingResult:
        if equity <= 0:
            raise ValueError("equity must be positive")
        if entry_price <= 0 or stop_price <= 0:
            raise ValueError("entry and stop must be positive")
        if side is Side.LONG and stop_price >= entry_price:
            raise ValueError("long stop must be below entry")
        if side is Side.SHORT and stop_price <= entry_price:
            raise ValueError("short stop must be above entry")

        risk_budget = equity * self.config.risk_per_trade_pct
        # A stop can slip and fees are paid on both sides. Reserve them in the
        # unit-risk calculation instead of treating the nominal stop as exact.
        price_risk = abs(entry_price - stop_price)
        cost_risk = entry_price * self.config.estimated_round_trip_cost_pct
        risk_per_unit = price_risk + cost_risk
        unconstrained_quantity = risk_budget / risk_per_unit

        max_notional = equity * self.config.max_position_notional_pct
        max_by_notional = max_notional / entry_price
        max_collateral = equity * self.config.max_margin_fraction
        max_by_margin = (max_collateral * self.config.leverage) / entry_price
        quantity = min(unconstrained_quantity, max_by_notional, max_by_margin)
        was_margin_capped = quantity < unconstrained_quantity and max_by_margin <= min(unconstrained_quantity, max_by_notional)
        if quantity < self.config.min_quantity:
            quantity = 0.0
        notional = quantity * entry_price
        collateral = notional / self.config.leverage
        expected_loss = quantity * risk_per_unit
        explanation = (
            f"Risk budget ${risk_budget:,.2f} / unit risk ${risk_per_unit:,.2f} = "
            f"{unconstrained_quantity:.8f} units; final {quantity:.8f} units. "
            f"Leverage {self.config.leverage:g}x changes collateral (${collateral:,.2f}), not risk budget."
        )
        return SizingResult(
            quantity=quantity,
            risk_budget=risk_budget,
            risk_per_unit=risk_per_unit,
            expected_loss_at_stop=expected_loss,
            notional=notional,
            required_collateral=collateral,
            was_margin_capped=was_margin_capped,
            explanation=explanation,
        )
