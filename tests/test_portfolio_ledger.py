from __future__ import annotations

import json
import math
import unittest
from dataclasses import replace
from unittest.mock import patch
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from jsonschema import Draft202012Validator

from crypto_bot.execution import BarExecutionModel
from crypto_bot.models import Side
from crypto_bot.portfolio_ledger import PortfolioLedger, ZERO


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

    def test_every_journal_record_is_strict_json_and_frozen_schema_valid(self):
        entry = self.ledger.deposit("1.25", NOW, "json-deposit")
        encoded = entry.as_schema_record()
        serialized = json.dumps(encoded, allow_nan=False)
        self.assertEqual(json.loads(serialized), encoded)
        schema = json.loads(Path("schemas/athena2_cash_journal_entry_v1.schema.json").read_text())
        Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER).validate(encoded)
        with self.assertRaises(ValueError):
            replace(entry, amount_usdt=Decimal("1e10000")).as_schema_record()
        with self.assertRaises(ValueError):
            replace(entry, cash_after_usdt=Decimal("NaN")).as_schema_record()

    def test_every_journal_record_is_strict_json_and_frozen_schema_valid(self):
        entry = self.ledger.deposit("1.25", NOW, "json-deposit")
        encoded = entry.as_schema_record()
        serialized = json.dumps(encoded, allow_nan=False)
        self.assertEqual(json.loads(serialized), encoded)
        schema = json.loads(Path("schemas/athena2_cash_journal_entry_v1.schema.json").read_text())
        Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER).validate(encoded)
        with self.assertRaises(ValueError):
            replace(entry, amount_usdt=Decimal("1e10000")).as_schema_record()
        with self.assertRaises(ValueError):
            replace(entry, cash_after_usdt=Decimal("NaN")).as_schema_record()

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

    def _snapshot(self, ledger):
        return (ledger.cash, tuple(ledger.journal), repr(ledger.positions), dict(ledger.reservations),
                ledger._entry_sequence, repr(ledger._events), tuple(ledger.completed_positions), frozenset(ledger.released_orders))

    def test_entry_funding_and_exit_failures_roll_back_all_ledger_state(self):
        entry_ledger = PortfolioLedger("entry-atomic", 1000)
        before = self._snapshot(entry_ledger)
        original_post = PortfolioLedger._post
        def post_then_fail(self, *args, **kwargs):
            original_post(self, *args, **kwargs)
            raise RuntimeError("injected entry journal failure after append")
        with patch.object(PortfolioLedger, "_post", autospec=True, side_effect=post_then_fail):
            with self.assertRaisesRegex(RuntimeError, "injected"):
                entry_ledger.apply_entry_fill(position_id="p", instrument_id="BTCUSDT", side=Side.LONG,
                    fill=self.fill(100, Side.LONG, 1), leverage=2, when=NOW, event_id="entry-fail")
        self.assertEqual(self._snapshot(entry_ledger), before)

        funding_ledger = PortfolioLedger("funding-atomic", 1000)
        funding_ledger.apply_entry_fill(position_id="p", instrument_id="BTCUSDT", side=Side.LONG,
            fill=self.fill(100, Side.LONG, 1), leverage=2, when=NOW, event_id="entry-ok")
        funding_ledger.reserve("r", "10", marks={"p": 100})
        before = self._snapshot(funding_ledger)
        def funding_post_then_fail(self, *args, **kwargs):
            original_post(self, *args, **kwargs)
            raise RuntimeError("injected funding journal failure after append")
        with patch.object(PortfolioLedger, "_post", autospec=True, side_effect=funding_post_then_fail):
            with self.assertRaisesRegex(RuntimeError, "injected"):
                funding_ledger.apply_funding("p", "-1", NOW, "funding-fail")
        self.assertEqual(self._snapshot(funding_ledger), before)

        exit_ledger = PortfolioLedger("exit-atomic", 1000)
        exit_ledger.apply_entry_fill(position_id="p", instrument_id="BTCUSDT", side=Side.LONG,
            fill=self.fill(100, Side.LONG, 1), leverage=2, when=NOW, event_id="entry-exit")
        exit_ledger.reserve("r", "10", marks={"p": 100})
        before = self._snapshot(exit_ledger)
        original_post = PortfolioLedger._post
        calls = 0
        def fail_second(self, *args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("injected second exit journal failure")
            return original_post(self, *args, **kwargs)
        with patch.object(PortfolioLedger, "_post", autospec=True, side_effect=fail_second):
            with self.assertRaisesRegex(RuntimeError, "second exit"):
                exit_ledger.apply_exit_fill("p", self.fill(110, Side.SHORT, 1), NOW, "exit-fail")
        self.assertEqual(calls, 2)
        self.assertEqual(self._snapshot(exit_ledger), before)

    def test_deposit_withdraw_reserve_release_failures_are_atomic(self):
        ledger = PortfolioLedger("other-atomic", 1000)
        original_post = PortfolioLedger._post
        def post_then_fail(self, *args, **kwargs):
            original_post(self, *args, **kwargs)
            raise RuntimeError("injected journal failure")
        for operation in (
            lambda: ledger.deposit(10, NOW, "deposit-fail"),
            lambda: ledger.withdraw(10, NOW, "withdraw-fail", marks={}),
        ):
            before = self._snapshot(ledger)
            with patch.object(PortfolioLedger, "_post", autospec=True, side_effect=post_then_fail):
                with self.assertRaisesRegex(RuntimeError, "injected journal"):
                    operation()
            self.assertEqual(self._snapshot(ledger), before)

        class MutateThenFailDict(dict):
            def __setitem__(self, key, value):
                super().__setitem__(key, value)
                raise RuntimeError("injected reservation mutation failure")
        ledger.reservations = MutateThenFailDict()
        before = self._snapshot(ledger)
        with self.assertRaisesRegex(RuntimeError, "reservation mutation"):
            ledger.reserve("reserve-fail", 10, marks={})
        self.assertEqual(self._snapshot(ledger), before)

        ledger.reservations = {"release-fail": Decimal("10")}
        class MutateThenFailSet(set):
            def add(self, value):
                super().add(value)
                raise RuntimeError("injected release mutation failure")
        ledger.released_orders = MutateThenFailSet()
        before = self._snapshot(ledger)
        with self.assertRaisesRegex(RuntimeError, "release mutation"):
            ledger.release_reservation("release-fail")
        self.assertEqual(self._snapshot(ledger), before)

    def test_slippage_is_validated_and_part_of_entry_and_exit_fingerprints(self):
        entry_fill = self.fill(100, Side.LONG, 1)
        self.ledger.apply_entry_fill(position_id="p", instrument_id="BTCUSDT", side=Side.LONG,
            fill=entry_fill, leverage=2, when=NOW, event_id="entry-slip")
        with self.assertRaisesRegex(ValueError, "conflicting replay"):
            self.ledger.apply_entry_fill(position_id="p", instrument_id="BTCUSDT", side=Side.LONG,
                fill=replace(entry_fill, slippage=entry_fill.slippage + 1), leverage=2, when=NOW, event_id="entry-slip")
        exit_fill = self.fill(110, Side.SHORT, 1)
        self.ledger.apply_exit_fill("p", exit_fill, NOW, "exit-slip")
        with self.assertRaisesRegex(ValueError, "conflicting replay"):
            self.ledger.apply_exit_fill("p", replace(exit_fill, slippage=exit_fill.slippage + 1), NOW, "exit-slip")
        bad_slippage = replace(self.fill(100, Side.LONG, 1), slippage=float("nan"))
        with self.assertRaises(ValueError):
            PortfolioLedger("bad-slip", 100).apply_entry_fill(position_id="p", instrument_id="BTCUSDT",
                side=Side.LONG, fill=bad_slippage, leverage=2, when=NOW, event_id="bad-slip")

    def test_closed_position_has_independent_immutable_accounting_reconciliation(self):
        self.ledger.apply_entry_fill(position_id="closed", instrument_id="BTCUSDT", side=Side.SHORT,
            fill=self.fill(100, Side.SHORT, 2), leverage=2, when=NOW, event_id="close-entry")
        self.ledger.apply_funding("closed", "-0.25", NOW, "close-funding")
        self.ledger.apply_exit_fill("closed", self.fill(90, Side.LONG, 1), NOW, "close-part")
        self.ledger.apply_exit_fill("closed", self.fill(80, Side.LONG, 1), NOW, "close-final")
        record = self.ledger.completed_positions[0]
        self.assertEqual(record.realized_gross_pnl, sum(
            e.amount_usdt for e in self.ledger.journal if e.position_id == "closed" and e.entry_type == "REALIZED_PNL"))
        self.assertEqual(record.entry_fees_paid, -sum(
            e.amount_usdt for e in self.ledger.journal if e.position_id == "closed" and e.entry_type == "ENTRY_FEE"))
        self.assertEqual(record.exit_fees_paid, -sum(
            e.amount_usdt for e in self.ledger.journal if e.position_id == "closed" and e.entry_type == "EXIT_FEE"))
        self.assertEqual(record.funding_paid, sum(
            e.amount_usdt for e in self.ledger.journal if e.position_id == "closed" and e.entry_type == "FUNDING"))
        self.assertEqual(record.final_net_pnl, record.realized_gross_pnl - record.entry_fees_paid - record.exit_fees_paid + record.funding_paid)
        before_cash_events = len(self.ledger.journal)
        reconciled = self.ledger.reconcile({})
        self.assertEqual(reconciled["completed_position_net_pnl"], record.final_net_pnl)
        self.assertEqual(len(self.ledger.journal), before_cash_events)

    def test_signed_funding_payments_and_receipts_with_multiple_events_and_partial_exits(self):
        self.ledger.apply_entry_fill(position_id="funded", instrument_id="BTCUSDT", side=Side.LONG,
            fill=self.fill(100, Side.LONG, 2), leverage=2, when=NOW, event_id="funded-entry")
        self.ledger.apply_funding("funded", "-0.75", NOW, "funding-payment-1")
        self.ledger.apply_exit_fill("funded", self.fill(110, Side.SHORT, 1), NOW, "funded-partial")
        self.ledger.apply_funding("funded", "0.25", NOW, "funding-receipt")
        self.ledger.apply_funding("funded", "-0.10", NOW, "funding-payment-2")
        self.ledger.apply_exit_fill("funded", self.fill(120, Side.SHORT, 1), NOW, "funded-final")
        record = self.ledger.completed_positions[0]
        self.assertEqual(record.funding_paid, Decimal("-0.60"))
        self.assertEqual(record.final_net_pnl, record.realized_gross_pnl - record.entry_fees_paid - record.exit_fees_paid + Decimal("-0.60"))
        funding_rows = [e for e in self.ledger.journal if e.position_id == "funded" and e.entry_type == "FUNDING"]
        self.assertEqual([e.amount_usdt for e in funding_rows], [Decimal("-0.75"), Decimal("0.25"), Decimal("-0.10")])
        self.assertEqual(self.ledger.reconcile({})["completed_position_net_pnl"], record.final_net_pnl)

    def test_rollback_preserves_event_position_object_reference(self):
        ledger = PortfolioLedger("reference-rollback", 1000)
        ledger.apply_entry_fill(position_id="p", instrument_id="BTCUSDT", side=Side.LONG,
            fill=self.fill(100, Side.LONG, 1), leverage=2, when=NOW, event_id="original-entry")
        self.assertIs(ledger._events["original-entry"][1], ledger.positions["p"])
        before = self._snapshot(ledger)
        with patch.object(PortfolioLedger, "_post", side_effect=RuntimeError("injected increase failure")):
            with self.assertRaisesRegex(RuntimeError, "increase failure"):
                ledger.apply_entry_fill(position_id="p", instrument_id="BTCUSDT", side=Side.LONG,
                    fill=self.fill(105, Side.LONG, 1), leverage=2, when=NOW, event_id="increase-failure")
        self.assertEqual(self._snapshot(ledger), before)
        self.assertIs(ledger._events["original-entry"][1], ledger.positions["p"])
        replayed = ledger.apply_entry_fill(position_id="p", instrument_id="BTCUSDT", side=Side.LONG,
            fill=self.fill(100, Side.LONG, 1), leverage=2, when=NOW, event_id="original-entry")
        self.assertIs(replayed, ledger.positions["p"])
        self.assertEqual(replayed.quantity, ledger.positions["p"].quantity)
        self.assertEqual(replayed.quantity, Decimal("1"))

    def test_completed_position_id_cannot_be_reopened_and_old_replays_remain_idempotent(self):
        entry = self.fill(100, Side.LONG, 1)
        self.ledger.apply_entry_fill(position_id="once", instrument_id="BTCUSDT", side=Side.LONG,
            fill=entry, leverage=2, when=NOW, event_id="once-entry")
        exit_fill = self.fill(110, Side.SHORT, 1)
        expected_exit = self.ledger.apply_exit_fill("once", exit_fill, NOW, "once-exit")
        before = self._snapshot(self.ledger)
        with self.assertRaisesRegex(ValueError, "completed position ID"):
            self.ledger.apply_entry_fill(position_id="once", instrument_id="BTCUSDT", side=Side.LONG,
                fill=entry, leverage=2, when=NOW, event_id="reopen")
        self.assertEqual(self._snapshot(self.ledger), before)
        self.assertEqual(self.ledger.reconcile({})["cash_delta"], ZERO)
        self.assertIs(self.ledger.apply_entry_fill(position_id="once", instrument_id="BTCUSDT", side=Side.LONG,
            fill=entry, leverage=2, when=NOW, event_id="once-entry"), self.ledger._events["once-entry"][1])
        self.assertEqual(self.ledger.apply_exit_fill("once", exit_fill, NOW, "once-exit"), expected_exit)
        self.assertEqual(self._snapshot(self.ledger), before)

    def test_reservation_lifecycle_retry_validation_and_no_cash_effect(self):
        cash = self.ledger.cash
        self.assertEqual(self.ledger.release_reservation("missing"), 0)
        self.assertEqual(self.ledger.reserve("order", "100", marks={}), Decimal("100"))
        journal = tuple(self.ledger.journal)
        self.assertEqual(self.ledger.reserve("order", "100", marks={}), Decimal("100"))
        with self.assertRaisesRegex(ValueError, "conflicting reservation"):
            self.ledger.reserve("order", "101", marks={})
        for invalid in ("-1", "NaN", "Infinity", float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                self.ledger.reserve(f"bad-{invalid}", invalid, marks={})
        self.assertEqual(self.ledger.cash, cash)
        self.assertEqual(tuple(self.ledger.journal), journal)
        self.ledger.apply_entry_fill(position_id="p", instrument_id="BTCUSDT", side=Side.LONG,
            fill=self.fill(100, Side.LONG, 1), leverage=2, when=NOW, event_id="reserve-entry")
        self.assertEqual(self.ledger.reservations["order"], Decimal("100"))
        self.ledger.apply_exit_fill("p", self.fill(110, Side.SHORT, 1), NOW, "reserve-close")
        self.assertEqual(self.ledger.reservations["order"], Decimal("100"))
        self.assertEqual(self.ledger.release_reservation("order"), Decimal("100"))
        self.assertEqual(self.ledger.release_reservation("order"), 0)
        with self.assertRaisesRegex(ValueError, "cannot be recreated"):
            self.ledger.reserve("order", 100, marks={})
        self.assertEqual(self.ledger.cash, sum((e.amount_usdt for e in self.ledger.journal), ZERO))

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

    def test_full_account_replay_fixture_matches_complete_ledger_twice(self):
        fixture = json.loads(Path("tests/fixtures/portfolio_parity/account_ledger_full_replay_v1.json").read_text())
        def replay():
            model = BarExecutionModel(**fixture["execution"])
            ledger = PortfolioLedger("full-replay", fixture["initial_cash_usdt"])
            ledger.deposit(fixture["deposit_usdt"], NOW, "deposit-1")
            for reservation in fixture["reservations"]:
                ledger.reserve(reservation["order_id"], reservation["amount_usdt"], marks={})
            for position in fixture["positions"]:
                side = Side(position["side"])
                for index, (price, qty) in enumerate(position["entries"]):
                    ledger.apply_entry_fill(position_id=position["position_id"], instrument_id=position["instrument_id"],
                        side=side, fill=model.fill(price, side, qty), leverage=position["leverage"], when=NOW,
                        event_id=f"{position['position_id']}-entry-{index}")
                for index, funding in enumerate(position["funding"]):
                    ledger.apply_funding(position["position_id"], funding, NOW, f"{position['position_id']}-funding-{index}")
                close_side = Side.SHORT if side is Side.LONG else Side.LONG
                for index, (price, qty) in enumerate(position["exits"]):
                    ledger.apply_exit_fill(position["position_id"], model.fill(price, close_side, qty), NOW,
                        f"{position['position_id']}-exit-{index}")
                with self.assertRaisesRegex(ValueError, "completed position ID"):
                    ledger.apply_entry_fill(position_id=position["position_id"], instrument_id=position["instrument_id"],
                        side=side, fill=model.fill(position["entries"][0][0], side, 1), leverage=position["leverage"],
                        when=NOW, event_id=f"{position['position_id']}-reopen")
            ledger.release_reservation("long-order")
            ledger.release_reservation("short-order")
            ledger.withdraw(fixture["withdrawal_usdt"], NOW, "withdrawal-1", marks={})
            with self.assertRaises(ValueError):
                ledger.reserve("too-large", ledger.cash + 1, marks={})
            reconciliation = ledger.reconcile({})
            journal = [entry.as_schema_record() for entry in ledger.journal]
            schema = json.loads(Path("schemas/athena2_cash_journal_entry_v1.schema.json").read_text())
            validator = Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)
            for record in journal:
                json.dumps(record, allow_nan=False)
                validator.validate(record)
            return {
                "journal": journal,
                "cash": str(ledger.cash),
                "positions": {pid: {"quantity": str(pos.quantity), "average_entry_price": str(pos.average_entry_price)}
                              for pid, pos in ledger.positions.items()},
                "completed": [{"position_id": p.position_id, "realized": str(p.realized_gross_pnl),
                    "entry_fees": str(p.entry_fees_paid), "exit_fees": str(p.exit_fees_paid),
                    "funding": str(p.funding_paid), "net": str(p.final_net_pnl)} for p in ledger.completed_positions],
                "reservations": {key: str(value) for key, value in ledger.reservations.items()},
                "reconciliation": {key: str(value) for key, value in reconciliation.items()},
            }
        first, second = replay(), replay()
        self.assertEqual(first, second)
        expected = fixture["expected"]
        self.assertEqual([p["position_id"] for p in first["completed"]], expected["completed_positions"])
        self.assertEqual(len(first["positions"]), expected["active_positions"])
        self.assertEqual(len(first["reservations"]), expected["active_reservations"])
        self.assertEqual(Decimal(first["reconciliation"]["cash_delta"]), ZERO)
        self.assertEqual(Decimal(first["reconciliation"]["equity_delta"]), ZERO)
        self.assertEqual(first["reconciliation"]["free_collateral"], first["cash"])


if __name__ == "__main__":
    unittest.main()
