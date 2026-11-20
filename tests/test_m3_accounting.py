from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
import unittest

from crypto_bot.m3_accounting import AccountingEvidenceError, pair_weekly_account_evidence, weekly_account_evidence


UTC = timezone.utc


def dt(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def fake_result(marks, events):
    return SimpleNamespace(
        equity_curve=[{"time": dt(time), "equity": Decimal(value)} for time, value in marks],
        events=events,
        ledger=SimpleNamespace(journal=[SimpleNamespace(entry_type="INITIAL_DEPOSIT", amount_usdt=Decimal("1000"))]),
    )


class M3WeeklyAccountingTests(unittest.TestCase):
    def test_sunday_entry_monday_carry_tuesday_exit_uses_boundary_marks(self):
        result = fake_result([
            ("2026-11-23T00:00:00Z", "1000"),
            ("2026-11-30T00:00:00Z", "1010"),
            ("2026-12-01T00:00:00Z", "1012"),
            ("2026-12-07T00:00:00Z", "1012"),
        ], [
            {"event_type": "ORDER_FILLED", "event_time": "2026-11-22T12:00:00Z", "position_id": "p1"},
            {"event_type": "POSITION_CLOSED", "event_time": "2026-12-01T00:00:00Z", "position_id": "p1"},
        ])
        week48 = weekly_account_evidence(result, scored_interval=(dt("2026-11-23T00:00:00Z"),
                                                                      dt("2026-11-30T00:00:00Z")))
        week49 = weekly_account_evidence(result, scored_interval=(dt("2026-11-30T00:00:00Z"),
                                                                      dt("2026-12-07T00:00:00Z")))
        self.assertEqual(week48[0].utc_week, "2026-W48")
        self.assertEqual(week48[0].start_equity, "1000")
        self.assertEqual(week48[0].end_equity, "1010")
        self.assertEqual(week48[0].return_decimal, "0.01")
        self.assertTrue(week48[0].position_exposure)
        self.assertFalse(week48[0].trade_activity)
        self.assertEqual(week49[0].utc_week, "2026-W49")
        self.assertEqual(week49[0].start_equity, "1010")
        self.assertEqual(week49[0].end_equity, "1012")
        self.assertEqual(week49[0].return_decimal, str(Decimal("1012")/Decimal("1010")-1))
        self.assertTrue(week49[0].trade_activity)

    def test_zero_activity_week_is_exactly_zero_and_empty_calendar_is_retained(self):
        result = fake_result([("2026-11-23T00:00:00Z", "1000"),
                              ("2026-11-30T00:00:00Z", "1000")], [])
        rows = weekly_account_evidence(result, scored_interval=(dt("2026-11-23T00:00:00Z"),
                                                                 dt("2026-11-30T00:00:00Z")))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].return_decimal, "0")
        self.assertFalse(rows[0].position_exposure)
        self.assertFalse(rows[0].trade_activity)

    def test_pairing_requires_identical_calendar_keys(self):
        result = fake_result([("2026-11-23T00:00:00Z", "1000"),
                              ("2026-11-30T00:00:00Z", "1000")], [])
        rows = weekly_account_evidence(result, scored_interval=(dt("2026-11-23T00:00:00Z"),
                                                                 dt("2026-11-30T00:00:00Z")))
        self.assertEqual(pair_weekly_account_evidence(rows, rows)[0].paired_return_delta, "0")
        with self.assertRaises(AccountingEvidenceError):
            pair_weekly_account_evidence(rows, ())


if __name__ == "__main__":
    unittest.main()
