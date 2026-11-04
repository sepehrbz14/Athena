import unittest
from datetime import datetime, timezone

from crypto_bot.controls import KillReason, KillSwitch, KillSwitchConfig
from crypto_bot.models import Side, Trade
from crypto_bot.validation import monte_carlo_trade_order


class ControlsAndValidationTests(unittest.TestCase):
    def test_daily_loss_locks_new_entries(self) -> None:
        stamp = datetime(2025, 1, 1, tzinfo=timezone.utc)
        switch = KillSwitch(KillSwitchConfig(max_daily_loss_pct=0.03))
        switch.observe(stamp, 100)
        switch.observe(stamp, 96.9)

        allowed, reason = switch.allow_new_trade()
        self.assertFalse(allowed)
        self.assertIn("daily_loss", reason)

    def test_feed_disagreement_halts_permanently(self) -> None:
        stamp = datetime(2025, 1, 1, tzinfo=timezone.utc)
        switch = KillSwitch(KillSwitchConfig(max_price_feed_difference_pct=0.005))
        switch.compare_prices(stamp, 100, 101)

        self.assertEqual(switch.permanent_reason, KillReason.DATA_DISAGREEMENT)

    def test_monte_carlo_reports_sequence_drawdown(self) -> None:
        stamp = datetime(2025, 1, 1, tzinfo=timezone.utc)
        trades = [
            Trade(Side.LONG, 1, stamp, stamp, 100, 100, 95, None, "x", "x", pnl, 0, 0, 0, pnl)
            for pnl in (20, -30, 25, -10, 15)
        ]
        result = monte_carlo_trade_order(trades, initial_equity=100, iterations=100, seed=4)

        self.assertEqual(result.iterations, 100)
        self.assertAlmostEqual(result.median_final_equity, 120)
        self.assertGreater(result.max_drawdown_p95, 0)
