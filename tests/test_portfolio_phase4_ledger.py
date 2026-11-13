from __future__ import annotations

import unittest
from decimal import Decimal

from crypto_bot.portfolio_ledger import PortfolioLedger


class Phase4ReservationResizeTests(unittest.TestCase):
    def test_resize_is_atomic_non_cash_and_terminal_release_cannot_be_reused(self):
        ledger = PortfolioLedger("resize-acct", 1000)
        initial_cash = ledger.cash
        initial_journal = list(ledger.journal)
        ledger.reserve("order-1", 100, marks={})
        ledger.resize_reservation("order-1", 150, marks={})
        self.assertEqual(ledger.reservations["order-1"], Decimal("150"))
        ledger.resize_reservation("order-1", 50, marks={})
        self.assertEqual(ledger.reservations["order-1"], Decimal("50"))
        before_fail = (ledger.cash, list(ledger.journal), dict(ledger.reservations), set(ledger.released_orders))
        with self.assertRaisesRegex(ValueError, "free collateral"):
            ledger.resize_reservation("order-1", 2000, marks={})
        self.assertEqual((ledger.cash, ledger.journal, ledger.reservations, ledger.released_orders), before_fail)
        ledger.resize_reservation("order-1", 0, marks={})
        self.assertNotIn("order-1", ledger.reservations)
        with self.assertRaisesRegex(ValueError, "released"):
            ledger.reserve("order-1", 10, marks={})
        self.assertEqual(ledger.cash, initial_cash)
        self.assertEqual(ledger.journal, initial_journal)
        self.assertEqual(ledger.reconcile({})["cash_delta"], Decimal("0"))

    def test_resize_requires_an_active_order_and_finite_nonnegative_amount(self):
        ledger = PortfolioLedger("resize-validation", 1000)
        with self.assertRaisesRegex(ValueError, "nonexistent"):
            ledger.resize_reservation("absent", 1, marks={})
        ledger.reserve("order-1", 10, marks={})
        for invalid in (-1, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                ledger.resize_reservation("order-1", invalid, marks={})
        self.assertEqual(ledger.reservations["order-1"], Decimal("10"))
        self.assertEqual(ledger.reconcile({})["cash_delta"], Decimal("0"))


if __name__ == "__main__":
    unittest.main()
