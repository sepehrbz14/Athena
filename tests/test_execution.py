"""Execution policy tests and frozen pre-refactor compatibility checks."""
from __future__ import annotations

import hashlib
import json
import math
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from jsonschema import Draft202012Validator

from crypto_bot.backtest import BacktestConfig, IntrabarBacktester
from crypto_bot.diagnostics import LogLevel
from crypto_bot.execution import BarExecutionModel, valid_prior_volume
from crypto_bot.models import Candle, Side, Signal
from crypto_bot.paper import PaperOnlyRunner
from crypto_bot.risk import PositionSizer, RiskConfig
from crypto_bot.strategies import EnsembleDecision


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "portfolio_parity"


class OneSignal:
    def __init__(self, signal: Signal) -> None:
        self.signal = signal
        self.used = False

    def decide(self, _market):
        if self.used:
            return EnsembleDecision(None, (), ())
        self.used = True
        return EnsembleDecision(self.signal, (self.signal.strategy,), ())


def _candle(row: dict[str, object]) -> Candle:
    return Candle(
        datetime.fromisoformat(str(row["timestamp"])),
        float(row["open"]), float(row["high"]), float(row["low"]),
        float(row["close"]), float(row["volume"]),
    )


def _assert_close(test: unittest.TestCase, actual: float, expected: float) -> None:
    test.assertTrue(math.isclose(actual, expected, rel_tol=1e-12, abs_tol=1e-7),
                    f"{actual!r} != {expected!r}")


class ExecutionPolicyTests(unittest.TestCase):
    def test_directional_fill_price_fee_and_slippage(self):
        execution = BarExecutionModel(fee_rate=0.001, slippage_bps=10)
        long_fill = execution.fill(100.0, Side.LONG, 2.0)
        short_fill = execution.fill(100.0, Side.SHORT, 2.0)

        self.assertEqual(long_fill.raw_price, 100.0)
        self.assertAlmostEqual(long_fill.price, 100.1)
        self.assertAlmostEqual(long_fill.fee, 0.2002)
        self.assertAlmostEqual(long_fill.slippage, 0.2)
        self.assertAlmostEqual(short_fill.price, 99.9)
        self.assertAlmostEqual(short_fill.fee, 0.1998)
        self.assertAlmostEqual(short_fill.slippage, 0.2)

    def test_funding_amount_is_signed_and_prorated(self):
        half_hour_long = BarExecutionModel.funding_amount(Side.LONG, 2, 100, 0.0008, 0.5)
        half_hour_short = BarExecutionModel.funding_amount(Side.SHORT, 2, 100, 0.0008, 0.5)

        self.assertAlmostEqual(half_hour_long, 0.01)
        self.assertAlmostEqual(half_hour_short, -0.01)

    def test_prior_volume_rejects_stale_gap_volume(self):
        start = datetime(2025, 1, 1, tzinfo=timezone.utc)
        bars = [
            Candle(start, 100, 101, 99, 100, 20),
            Candle(start + timedelta(minutes=30), 100, 101, 99, 100, 40),
            Candle(start + timedelta(minutes=45), 100, 101, 99, 100, 50),
        ]

        self.assertEqual(valid_prior_volume(bars, 1), (None, True))
        self.assertEqual(valid_prior_volume(bars, 2), (40, False))
        execution = BarExecutionModel(max_participation_rate=0.25)
        self.assertEqual(execution.participation_quantity(None), 0)
        self.assertEqual(execution.participation_quantity(40), 10)

    def test_actual_json_schemas_accept_valid_and_reject_invalid_records(self):
        # Validate the three frozen Phase 1 contracts explicitly. Additive
        # versioned configuration schemas are validated in their own tests.
        schemas = [ROOT / "schemas" / name for name in (
            "athena2_portfolio_event_v1.schema.json",
            "athena2_cash_journal_entry_v1.schema.json",
            "athena2_frozen_signal_v1.schema.json",
        )]
        self.assertEqual(len(schemas), 3)
        validators = {}
        for path in schemas:
            schema = json.loads(path.read_text(encoding="utf-8"))
            Draft202012Validator.check_schema(schema)
            validators[schema["title"]] = Draft202012Validator(schema)

        event_validator = validators["Athena 2.0 portfolio event v1"]
        event = {
            "schema_version": "athena.portfolio.event.v1",
            "event_id": "evt-1",
            "event_type": "ACCOUNT_MARKED",
            "event_time": "2025-01-01T00:00:00Z",
            "phase": "close_accounting",
            "account_id": "acct-1",
            "payload": {"cash_usdt": 10000.0},
        }
        self.assertTrue(event_validator.is_valid(event))
        self.assertFalse(event_validator.is_valid({**event, "event_type": "UNKNOWN_EVENT"}))
        self.assertFalse(event_validator.is_valid({key: value for key, value in event.items() if key != "payload"}))

        cash_validator = validators["Athena 2.0 USDT cash journal entry v1"]
        cash = {
            "schema_version": "athena.cash_journal_entry.v1",
            "entry_id": "cash-1",
            "event_id": "evt-1",
            "event_time": "2025-01-01T00:00:00Z",
            "account_id": "acct-1",
            "asset": "USDT",
            "entry_type": "ENTRY_FEE",
            "amount_usdt": -0.5,
            "cash_after_usdt": 9999.5,
        }
        self.assertTrue(cash_validator.is_valid(cash))
        self.assertFalse(cash_validator.is_valid({**cash, "entry_type": "MARGIN_RESERVED"}))
        self.assertFalse(cash_validator.is_valid({**cash, "asset": "BTC"}))

        signal_validator = validators["Athena 2.0 normalized frozen selected signal v1"]
        signal = {
            "schema_version": "athena.frozen_signal.v1",
            "source_signal_sha256": "a" * 64,
            "source_candle_sha256": "b" * 64,
            "instrument_id": "BTCUSDT",
            "signal_id": 1,
            "candle_open_time": "2025-01-01T00:00:00Z",
            "available_at": "2025-01-01T00:15:00Z",
            "strategy": "fixture",
            "direction": "long",
            "confidence": 0.75,
            "reference_price": 100.0,
            "stop_price": 95.0,
            "take_profit": 110.0,
        }
        self.assertTrue(signal_validator.is_valid(signal))
        self.assertFalse(signal_validator.is_valid({**signal, "instrument_id": "ETHUSDT"}))
        self.assertFalse(signal_validator.is_valid({**signal, "source_signal_sha256": "bad-hash"}))

    def test_all_pre_refactor_execution_goldens_reproduce(self):
        fixture = json.loads((FIXTURE_DIR / "legacy_execution_cases_v1.json").read_text(encoding="utf-8"))
        self.assertEqual(fixture["provenance"]["git_commit"], "749a0db")
        self.assertEqual({case["name"] for case in fixture["cases"]}, {
            "short_target", "long_collision", "partial_fills", "funding_across_gap",
            "stale_liquidity", "end_of_data_open",
        })

        for case in fixture["cases"]:
            with self.subTest(case=case["name"]):
                config_values = dict(case["config"])
                risk_values = dict(case["risk"])
                signal_data = case["signal"]
                signal = Signal(
                    Side(signal_data["side"]), signal_data["stop_price"],
                    signal_data["take_profit"], signal_data["confidence"],
                    signal_data["strategy"], signal_data["rationale"],
                )
                candles = [_candle(row) for row in case["candles"]]
                with tempfile.TemporaryDirectory(dir=ROOT) as output:
                    config = BacktestConfig(
                        **config_values, diagnostic_mode=True, diagnostics_dir=output,
                        logging_level=LogLevel.DEBUG,
                    )
                    result = IntrabarBacktester(config, PositionSizer(RiskConfig(**risk_values))).run(
                        candles, OneSignal(signal),
                    )
                    events = [json.loads(line) for line in (Path(output) / "events.jsonl").read_text(encoding="utf-8").splitlines()]

                expected = case["expected"]
                _assert_close(self, result.final_equity, expected["final_equity"])
                self.assertEqual(len(result.trades), expected["trade_count"])
                self.assertEqual([event["event_type"] for event in events], expected["event_types"])
                for actual, wanted in zip(result.trades, expected["trades"]):
                    self.assertEqual(actual.side.value, wanted["side"])
                    self.assertEqual(actual.exit_reason, wanted["exit_reason"])
                    self.assertEqual(actual.entry_time.isoformat(), wanted["entry_time"])
                    self.assertEqual(actual.exit_time.isoformat(), wanted["exit_time"])
                    for field in ("quantity", "entry_price", "exit_price", "gross_pnl", "entry_fee", "exit_fee", "funding_paid", "net_pnl"):
                        _assert_close(self, getattr(actual, field), wanted[field])
                self.assertEqual(len(result.equity_curve), len(expected["equity_curve"]))
                for actual, wanted in zip(result.equity_curve, expected["equity_curve"]):
                    self.assertEqual(actual.timestamp.isoformat(), wanted["timestamp"])
                    _assert_close(self, actual.equity, wanted["equity"])
                    self.assertTrue(math.isclose(actual.drawdown_pct, wanted["drawdown_pct"], rel_tol=1e-12, abs_tol=1e-12))

                actual_details = [
                    {key: event[key] for key in wanted if key in event}
                    for event, wanted in zip(events, expected["event_details"])
                ]
                self.assertEqual(len(actual_details), len(expected["event_details"]))
                for actual, wanted in zip(actual_details, expected["event_details"]):
                    self.assertEqual(set(actual), set(wanted))
                    for key, expected_value in wanted.items():
                        if isinstance(expected_value, (float, int)) and not isinstance(expected_value, bool):
                            _assert_close(self, actual[key], expected_value)
                        else:
                            self.assertEqual(actual[key], expected_value)

    def test_paper_outputs_match_pre_refactor_baseline(self):
        baseline = json.loads((FIXTURE_DIR / "pre_refactor_baseline_v1.json").read_text(encoding="utf-8"))
        start = datetime(2025, 1, 1, tzinfo=timezone.utc)
        make_bar = lambda minute: Candle(start + timedelta(minutes=minute), 100, 101, 99, 100, 1000)
        warmup = [make_bar(0), make_bar(15)]
        prospective = [make_bar(30), make_bar(45)]
        dataset_hash = baseline["paper"]["summary"]["dataset_sha256"]

        with tempfile.TemporaryDirectory(dir=ROOT) as root:
            state = Path(root) / "paper_state"
            runner = PaperOnlyRunner(warmup, state, dataset_hash)
            runner.append_completed_candle(prospective[0], prospective[0].timestamp + timedelta(minutes=15, seconds=30))
            result = runner.append_completed_candle(
                prospective[1], prospective[1].timestamp + timedelta(minutes=15, seconds=30),
            )
            self.assertEqual(result, baseline["paper"]["result"])
            summary = json.loads((state / "paper_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary, baseline["paper"]["summary"])
            event_rows = [json.loads(line) for line in (state / "paper_events.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual([row["event_type"] for row in event_rows], baseline["paper"]["event_types"])
            self.assertEqual([row["event_id"] for row in event_rows], baseline["paper"]["event_ids"])
            for name, expected in baseline["paper"]["output_files"].items():
                data = (state / name).read_bytes()
                # The baseline was captured on Windows. Normalize text-file
                # newline conventions to CRLF before comparing its byte digest.
                data = data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
                self.assertEqual(len(data), expected["bytes"], name)
                self.assertEqual(hashlib.sha256(data).hexdigest(), expected["sha256"], name)


if __name__ == "__main__":
    unittest.main()
