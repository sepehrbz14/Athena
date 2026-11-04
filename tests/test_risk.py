import unittest

from crypto_bot.models import Side
from crypto_bot.risk import PositionSizer, RiskConfig


class PositionSizerTests(unittest.TestCase):
    def test_sizes_from_account_risk_not_notional_guess(self) -> None:
        sizer = PositionSizer(RiskConfig(
            risk_per_trade_pct=0.005,
            leverage=1,
            max_position_notional_pct=1,
            max_margin_fraction=0.8,
            estimated_round_trip_cost_pct=0,
        ))
        result = sizer.size(equity=10_000, entry_price=60_000, stop_price=59_500, side=Side.LONG)

        self.assertAlmostEqual(result.risk_budget, 50)
        self.assertAlmostEqual(result.risk_per_unit, 500)
        self.assertAlmostEqual(result.quantity, 0.1)
        self.assertAlmostEqual(result.expected_loss_at_stop, 50)

    def test_leverage_changes_collateral_not_stop_risk(self) -> None:
        one_x = PositionSizer(RiskConfig(
            risk_per_trade_pct=0.005, leverage=1, max_position_notional_pct=1,
            max_margin_fraction=0.8, estimated_round_trip_cost_pct=0,
        )).size(equity=10_000, entry_price=60_000, stop_price=59_500, side=Side.LONG)
        five_x = PositionSizer(RiskConfig(
            risk_per_trade_pct=0.005, leverage=5, max_position_notional_pct=1,
            max_margin_fraction=0.8, estimated_round_trip_cost_pct=0,
        )).size(equity=10_000, entry_price=60_000, stop_price=59_500, side=Side.LONG)

        self.assertAlmostEqual(one_x.quantity, five_x.quantity)
        self.assertAlmostEqual(one_x.expected_loss_at_stop, five_x.expected_loss_at_stop)
        self.assertAlmostEqual(one_x.required_collateral / 5, five_x.required_collateral)

    def test_rejects_stop_on_wrong_side(self) -> None:
        with self.assertRaises(ValueError):
            PositionSizer().size(equity=10_000, entry_price=100, stop_price=101, side=Side.LONG)
