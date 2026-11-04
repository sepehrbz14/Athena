import unittest
from datetime import datetime, timezone

from crypto_bot.models import EquityPoint
from crypto_bot.reporting import assert_trade_reconciliation, fill_cost_totals, monthly_equity, trade_metrics


class PerformanceReportingTests(unittest.TestCase):
    def test_metrics_and_cost_reconciliation(self):
        rows = [
            {"trade_id": 1, "raw_gross_pnl": 12.0, "gross_pnl": 11.0, "slippage_cost": 1.0,
             "fees": 0.5, "funding_paid": 0.0, "net_pnl": 10.5, "holding_hours": 1.0},
            {"trade_id": 2, "raw_gross_pnl": -8.0, "gross_pnl": -8.5, "slippage_cost": 0.5,
             "fees": 0.5, "funding_paid": -0.25, "net_pnl": -8.75, "holding_hours": 3.0},
        ]
        assert_trade_reconciliation(rows)
        metrics = trade_metrics(rows)
        self.assertEqual(metrics["trades"], 2)
        self.assertEqual(metrics["wins"], 1)
        self.assertEqual(metrics["losses"], 1)
        self.assertAlmostEqual(metrics["net_pnl"], 1.75)
        self.assertAlmostEqual(metrics["expectancy"], 0.875)
        self.assertEqual(metrics["max_consecutive_losses"], 1)

    def test_bad_trade_reconciliation_fails(self):
        with self.assertRaises(AssertionError):
            assert_trade_reconciliation([{"trade_id": 9, "raw_gross_pnl": 10, "slippage_cost": 1,
                                          "fees": 2, "funding_paid": 0, "net_pnl": 8}])

    def test_fill_costs_use_executed_notional_and_raw_reference_price(self):
        totals = fill_cost_totals([
            {"price": 101.0, "raw_price": 100.0, "quantity": 2.0},
            {"price": 99.0, "raw_price": 100.0, "quantity": 3.0},
        ], fee_rate=0.0005)
        self.assertAlmostEqual(totals["notional"], 499.0)
        self.assertAlmostEqual(totals["fees"], 0.2495)
        self.assertAlmostEqual(totals["slippage"], 5.0)

    def test_monthly_return_and_within_month_drawdown(self):
        points = [
            EquityPoint(datetime(2020, 1, 1, tzinfo=timezone.utc), 100.0, 0.0),
            EquityPoint(datetime(2020, 1, 2, tzinfo=timezone.utc), 90.0, 0.1),
            EquityPoint(datetime(2020, 1, 3, tzinfo=timezone.utc), 95.0, 0.05),
            EquityPoint(datetime(2020, 2, 1, tzinfo=timezone.utc), 110.0, 0.0),
        ]
        rows = monthly_equity(points, 100.0)
        self.assertAlmostEqual(rows[0]["return_pct"], -0.05)
        self.assertAlmostEqual(rows[0]["max_drawdown_pct"], 0.1)
        self.assertAlmostEqual(rows[1]["return_pct"], 110 / 95 - 1)


if __name__ == "__main__":
    unittest.main()
