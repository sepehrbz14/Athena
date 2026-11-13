from __future__ import annotations

import json
import unittest
from decimal import Decimal
from pathlib import Path

from jsonschema import Draft202012Validator

from crypto_bot.execution import BarExecutionModel
from crypto_bot.models import Side


ROOT = Path(__file__).resolve().parents[1]


class PortfolioPhase4ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = json.loads((ROOT / "configs/athena2_portfolio_baseline_v1.json").read_text(encoding="utf-8"))
        cls.schema = json.loads((ROOT / "schemas/athena2_portfolio_config_v1.schema.json").read_text(encoding="utf-8"))

    def test_frozen_baseline_config_validates_and_matches_approved_values(self):
        Draft202012Validator(self.schema, format_checker=Draft202012Validator.FORMAT_CHECKER).validate(self.config)
        self.assertEqual(self.config["initial_equity_usdt"], 10000)
        self.assertEqual(self.config["per_trade_risk_pct"], 0.005)
        self.assertEqual(self.config["aggregate_open_pending_stop_risk_pct"], 0.015)
        self.assertEqual(self.config["leverage"], 2)
        self.assertEqual(self.config["maximum_gross_exposure_equity_multiple"], 2)
        self.assertEqual(self.config["maximum_net_exposure_equity_multiple"], 2)
        self.assertEqual(self.config["maximum_active_positions"], 1)
        self.assertEqual(self.config["instrument_allowlist"], ["BTCUSDT"])
        self.assertFalse(self.config["pyramiding_enabled"])
        self.assertEqual(self.config["minimum_equity_usdt"], 1)
        self.assertEqual(self.config["cash_buffer_pct"], 0.05)
        self.assertEqual(self.config["pending_order_expiry_eligible_candles"], 1)

    def test_schema_rejects_baseline_drift_and_unknown_instruments(self):
        validator = Draft202012Validator(self.schema)
        altered_risk = {**self.config, "per_trade_risk_pct": 0.01}
        altered_universe = {**self.config, "instrument_allowlist": ["ETHUSDT"]}
        self.assertTrue(list(validator.iter_errors(altered_risk)))
        self.assertTrue(list(validator.iter_errors(altered_universe)))

    def test_reservation_and_stop_risk_examples_pin_execution_boundary(self):
        fixture = json.loads((ROOT / "tests/fixtures/portfolio_simulator/phase4_reservation_examples_v1.json").read_text(encoding="utf-8"))
        execution = BarExecutionModel(**{key: value for key, value in self.config["execution"].items()
            if key != "force_close_at_end"})
        fee_rate = Decimal(str(self.config["execution"]["fee_rate"]))
        leverage = Decimal(str(self.config["leverage"]))
        for case in fixture["cases"]:
            side = Side(case["side"])
            exit_side = Side.SHORT if side is Side.LONG else Side.LONG
            raw_entry, raw_stop = float(case["reference_price"]), float(case["stop_price"])
            entry_estimate = Decimal(str(execution.slipped_price(raw_entry, side)))
            stop_exit_estimate = Decimal(str(execution.slipped_price(raw_stop, exit_side)))
            quantity = Decimal(case["quantity"])
            margin = quantity * abs(entry_estimate) / leverage
            entry_fee = quantity * abs(entry_estimate) * fee_rate
            stop_risk = quantity * max(Decimal(0), Decimal(side.sign) * (entry_estimate - stop_exit_estimate))
            stop_risk += quantity * fee_rate * (abs(entry_estimate) + abs(stop_exit_estimate))
            cash_buffer = Decimal(case["equity_usdt"]) * Decimal(str(self.config["cash_buffer_pct"]))
            self.assertEqual(entry_estimate, Decimal(case["expected_entry_estimate"]))
            self.assertEqual(stop_exit_estimate, Decimal(case["expected_stop_exit_estimate"]))
            self.assertEqual(margin, Decimal(case["expected_margin_reserve"]))
            self.assertEqual(entry_fee, Decimal(case["expected_entry_fee_reserve"]))
            self.assertEqual(margin + entry_fee, Decimal(case["expected_order_reserve"]))
            self.assertEqual(cash_buffer, Decimal(case["expected_account_cash_buffer"]))
            self.assertEqual(stop_risk, Decimal(case["expected_pending_stop_risk"]))

    def test_legacy_funding_charge_is_inverted_once_for_signed_ledger_cash(self):
        for rate, expected in (
            (0.0008, {
                Side.LONG: (Decimal("0.01"), Decimal("-0.01")),
                Side.SHORT: (Decimal("-0.01"), Decimal("0.01")),
            }),
            (-0.0008, {
                Side.LONG: (Decimal("-0.01"), Decimal("0.01")),
                Side.SHORT: (Decimal("0.01"), Decimal("-0.01")),
            }),
        ):
            for side in (Side.LONG, Side.SHORT):
                legacy_charge = Decimal(str(BarExecutionModel.funding_amount(side, 2, 100, rate, 0.5)))
                ledger_cash_movement = -legacy_charge
                self.assertEqual((legacy_charge, ledger_cash_movement), expected[side])


if __name__ == "__main__":
    unittest.main()
