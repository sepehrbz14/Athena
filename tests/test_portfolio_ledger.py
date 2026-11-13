from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from jsonschema import Draft202012Validator

from crypto_bot.execution import BarExecutionModel
from crypto_bot.models import Side
from crypto_bot.portfolio_ledger import PortfolioLedger


NOW = datetime(2025, 1, 1, tzinfo=timezone.utc)


class PortfolioLedgerTests(unittest.TestCase):
    def setUp(self):
        self.model = BarExecutionModel(fee_rate=0.001, slippage_bps=10)
        self.ledger = PortfolioLedger("acct-1", "1000")

    def fill(self, raw, side, qty):
        return self.model.fill(raw, side, qty)

    def test_journal_is_immutable_and_validates_against_frozen_schema(self):
        self.ledger.deposit("25", NOW, "evt-deposit")
        schema = json.loads(Path("schemas/athena2_cash_journal_entry_v1.schema.json").read_text())
        validator = Draft202012Validator(schema)
        for entry in self.ledger.journal:
            validator.validate(entry.as_schema_record())
        with self.assertRaises(Exception):
            self.ledger.journal[0].amount_usdt = Decimal("0")

    def test_partial_fills_increase_position_and_reservation_is_not_cash(self):
        cash_before = self.ledger.cash
        self.ledger.reserve("order-1", "100", marks={})
        self.assertEqual(self.ledger.cash, cash_before)
        self.assertEqual(self.ledger.free_collateral_at({}), Decimal("900"))
        self.ledger.apply_entry_fill(position_id="p1", instrument_id="BTCUSDT", side=Side.LONG,
            fill=self.fill(100, Side.LONG, 2), leverage=2, when=NOW, event_id="evt-fill-1", order_id="order-1")
        self.ledger.apply_entry_fill(position_id="p1", instrument_id="BTCUSDT", side=Side.LONG,
            fill=self.fill(110, Side.LONG, 1), leverage=2, when=NOW, event_id="evt-fill-2", order_id="order-1")
        pos = self.ledger.positions["p1"]
        self.assertEqual(pos.quantity, Decimal("3"))
        self.assertEqual(pos.average_entry_price, (Decimal("2") * Decimal(str(self.fill(100, Side.LONG, 2).price)) + Decimal(str(self.fill(110, Side.LONG, 1).price))) / 3)
        self.assertEqual(len([e for e in self.ledger.journal if e.entry_type == "ENTRY_FEE"]), 2)
        self.assertEqual(self.ledger.used_initial_margin({"p1": 110}), Decimal("165"))
        check = self.ledger.reconcile({"p1": 110})
        self.assertEqual(check["equity"], self.ledger.cash + check["unrealized_gross_pnl"])

    def test_funding_and_partial_then_complete_exit_post_once_and_release_margin(self):
        self.ledger.apply_entry_fill(position_id="p1", instrument_id="BTCUSDT", side=Side.LONG,
            fill=self.fill(100, Side.LONG, 2), leverage=2, when=NOW, event_id="entry")
        entry_fee_count = sum(e.entry_type == "ENTRY_FEE" for e in self.ledger.journal)
        self.ledger.apply_funding("p1", "-0.5", NOW, "funding")
        self.assertEqual(self.ledger.cash, Decimal("1000") - self.ledger.positions["p1"].entry_fees_paid - Decimal("0.5"))
        first_exit = self.fill(110, Side.SHORT, 1)
        gross, closed = self.ledger.apply_exit_fill("p1", first_exit, NOW, "exit-partial")
        self.assertFalse(closed)
        self.assertEqual(self.ledger.positions["p1"].quantity, Decimal("1"))
        self.assertEqual(self.ledger.used_initial_margin({"p1": 110}), Decimal(str(110 / 2)))
        last_exit = self.fill(120, Side.SHORT, 1)
        _, closed = self.ledger.apply_exit_fill("p1", last_exit, NOW, "exit-final")
        self.assertTrue(closed)
        self.assertNotIn("p1", self.ledger.positions)
        self.assertEqual(self.ledger.used_initial_margin({}), 0)
        self.assertEqual(sum(e.entry_type == "ENTRY_FEE" for e in self.ledger.journal), entry_fee_count)
        self.assertEqual(sum(e.entry_type == "FUNDING" for e in self.ledger.journal), 1)
        self.assertEqual(sum(e.entry_type == "REALIZED_PNL" for e in self.ledger.journal), 2)
        self.assertEqual(sum(e.entry_type == "EXIT_FEE" for e in self.ledger.journal), 2)
        result = self.ledger.reconcile({})
        self.assertEqual(result["unrealized_gross_pnl"], Decimal(0))
        self.assertEqual(result["equity"], self.ledger.cash)
        self.assertNotEqual(gross, 0)

    def test_reservation_release_and_invariants_reject_invalid_operations(self):
        self.ledger.reserve("o1", "100", marks={})
        self.assertEqual(self.ledger.reserved_collateral, Decimal(100))
        self.assertEqual(self.ledger.release_reservation("o1"), Decimal(100))
        self.assertEqual(self.ledger.reserved_collateral, 0)
        with self.assertRaises(ValueError):
            self.ledger.reserve("o2", "2000", marks={})
        self.ledger.apply_entry_fill(position_id="p1", instrument_id="BTCUSDT", side=Side.SHORT,
            fill=self.fill(100, Side.SHORT, 1), leverage=3, when=NOW, event_id="short-entry")
        with self.assertRaises(ValueError):
            self.ledger.apply_exit_fill("p1", self.fill(90, Side.LONG, 2), NOW, "oversized-exit")

    def test_identical_event_replay_is_idempotent_conflicting_replay_rejected(self):
        fill = self.fill(100, Side.LONG, 1)
        self.ledger.apply_entry_fill(position_id="p1", instrument_id="BTCUSDT", side=Side.LONG,
            fill=fill, leverage=2, when=NOW, event_id="same-entry")
        cash_once, qty_once, count_once = self.ledger.cash, self.ledger.positions["p1"].quantity, len(self.ledger.journal)
        self.ledger.apply_entry_fill(position_id="p1", instrument_id="BTCUSDT", side=Side.LONG,
            fill=fill, leverage=2, when=NOW, event_id="same-entry")
        self.assertEqual((self.ledger.cash, self.ledger.positions["p1"].quantity, len(self.ledger.journal)), (cash_once, qty_once, count_once))
        with self.assertRaisesRegex(ValueError, "conflicting replay"):
            self.ledger.apply_entry_fill(position_id="p1", instrument_id="BTCUSDT", side=Side.LONG,
                fill=self.fill(101, Side.LONG, 1), leverage=2, when=NOW, event_id="same-entry")
        self.ledger.apply_funding("p1", "-1", NOW, "same-funding")
        funding_cash = self.ledger.cash
        self.ledger.apply_funding("p1", "-1", NOW, "same-funding")
        self.assertEqual(self.ledger.cash, funding_cash)

    def test_current_unrealized_loss_reduces_collateral_and_marks_are_mandatory(self):
        self.ledger.apply_entry_fill(position_id="p1", instrument_id="BTCUSDT", side=Side.LONG,
            fill=self.fill(100, Side.LONG, 4), leverage=2, when=NOW, event_id="entry-loss")
        with self.assertRaisesRegex(ValueError, "current mark required"):
            self.ledger.free_collateral_at({})
        marks = {"p1": 80}
        equity = self.ledger.equity(marks)
        expected_unrealized = Decimal(4) * (Decimal(80) - self.ledger.positions["p1"].average_entry_price)
        self.assertEqual(equity, self.ledger.cash + expected_unrealized)
        free = self.ledger.free_collateral_at(marks)
        self.assertEqual(free, equity - Decimal(160))
        with self.assertRaises(ValueError):
            self.ledger.reserve("too-large", free + Decimal("0.01"), marks=marks)

    def test_multiple_reservations_share_current_equity_capacity(self):
        self.ledger.reserve("r1", 300, marks={})
        self.ledger.reserve("r2", 400, marks={})
        self.assertEqual(self.ledger.reserved_collateral, 700)
        with self.assertRaises(ValueError):
            self.ledger.reserve("r3", 301, marks={})

    def test_invalid_fill_values_exit_direction_and_decimal_edges_rejected(self):
        for value in (float("nan"), float("inf"), "NaN", "Infinity"):
            with self.assertRaises(ValueError):
                self.ledger.deposit(value, NOW, f"invalid-{value}")
        with self.assertRaises(ValueError):
            self.ledger.deposit("-0.1", NOW, "negative")
        self.ledger.apply_entry_fill(position_id="long", instrument_id="BTCUSDT", side=Side.LONG,
            fill=self.fill(100, Side.LONG, 1), leverage=2, when=NOW, event_id="long-open")
        with self.assertRaisesRegex(ValueError, "exit fill direction"):
            self.ledger.apply_exit_fill("long", self.fill(110, Side.LONG, 1), NOW, "wrong-long-exit")
        self.ledger.apply_entry_fill(position_id="short", instrument_id="BTCUSDT", side=Side.SHORT,
            fill=self.fill(100, Side.SHORT, 1), leverage=2, when=NOW, event_id="short-open")
        with self.assertRaisesRegex(ValueError, "exit fill direction"):
            self.ledger.apply_exit_fill("short", self.fill(90, Side.SHORT, 1), NOW, "wrong-short-exit")
        with self.assertRaises(ValueError):
            self.ledger.equity({"long": "0", "short": 100})

    def test_versioned_replay_fixture_is_deterministic(self):
        fixture = json.loads(Path("tests/fixtures/portfolio_parity/account_ledger_replay_v1.json").read_text())
        outputs = []
        for _ in range(2):
            model = BarExecutionModel(**fixture["execution"])
            ledger = PortfolioLedger(fixture["account_id"], fixture["initial_cash_usdt"])
            for step in fixture["scenario"]:
                if step["op"] == "entry_fill":
                    ledger.apply_entry_fill(position_id=step["position_id"], instrument_id=step["instrument_id"],
                        side=Side(step["side"]), fill=model.fill(step["raw_price"], Side(step["side"]), step["quantity"]),
                        leverage=step["leverage"], when=NOW, event_id=step["event_id"])
                elif step["op"] == "funding":
                    ledger.apply_funding(step["position_id"], step["amount_usdt"], NOW, step["event_id"])
                else:
                    ledger.apply_exit_fill(step["position_id"], model.fill(step["raw_price"], Side(step["order_side"]), step["quantity"]),
                        NOW, step["event_id"])
            outputs.append((str(ledger.cash), str(ledger.equity({})), [e.entry_type for e in ledger.journal],
                [str(e.amount_usdt) for e in ledger.journal], len(ledger.positions), str(ledger.used_initial_margin({}))))
        expected = fixture["expected"]
        wanted = (expected["cash_usdt"], expected["equity_usdt"], expected["journal_types"],
                  expected["journal_amounts_usdt"], expected["open_positions"], expected["used_margin_usdt"])
        self.assertEqual(outputs[0], wanted)
        self.assertEqual(outputs[1], wanted)


if __name__ == "__main__":
    unittest.main()
