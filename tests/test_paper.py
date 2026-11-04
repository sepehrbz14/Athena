import csv
import hashlib
import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from crypto_bot.backtest import BacktestConfig, IntrabarBacktester
from crypto_bot.controls import KillSwitchMode
from crypto_bot.diagnostics import LogLevel
from crypto_bot.models import Candle
from crypto_bot.paper import PaperInputError, PaperOnlyRunner
from crypto_bot.risk import PositionSizer, RiskConfig
from crypto_bot.models import Side, Signal
from crypto_bot.strategies import EnsembleDecision


class OneSignal:
    def __init__(self):
        self.used = False

    def decide(self, snapshot):
        if self.used:
            return EnsembleDecision(None, (), ())
        self.used = True
        return EnsembleDecision(Signal(Side.LONG, 90, 120, 0.75, "test", "audit"), ("test",), ())


class PaperRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.start = datetime(2026, 8, 19, 23, 30, tzinfo=timezone.utc)
        self.history = [
            Candle(self.start + timedelta(minutes=15 * i), 100, 101, 99, 100, 1000)
            for i in range(2)
        ]
        self.first_open = self.history[-1].timestamp + timedelta(minutes=15)
        self.digest = hashlib.sha256(b"frozen test data").hexdigest()
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name) / "paper_state"

    def runner(self):
        return PaperOnlyRunner(self.history, self.state, self.digest)

    def make_candle(self, timestamp: datetime, close: float = 100) -> Candle:
        return Candle(timestamp, close, close + 1, close - 1, close, 1000)

    def test_rejects_partial_late_missing_and_out_of_order_candles(self):
        candle = self.make_candle(self.first_open)
        with self.assertRaisesRegex(PaperInputError, "partial candle"):
            self.runner().append_completed_candle(candle, self.first_open + timedelta(minutes=14))
        with self.assertRaisesRegex(PaperInputError, "late candle"):
            self.runner().append_completed_candle(candle, self.first_open + timedelta(minutes=18))
        with self.assertRaisesRegex(PaperInputError, "missing candle"):
            self.runner().append_completed_candle(
                self.make_candle(self.first_open + timedelta(minutes=30)),
                self.first_open + timedelta(minutes=45, seconds=30),
            )
        runner = self.runner()
        runner.append_completed_candle(candle, self.first_open + timedelta(minutes=15, seconds=30))
        with self.assertRaisesRegex(PaperInputError, "strictly after"):
            runner.append_completed_candle(
                self.make_candle(self.first_open - timedelta(minutes=15)),
                self.first_open + timedelta(minutes=16),
            )

    def test_identical_duplicate_is_idempotent_and_conflicting_duplicate_rejected(self):
        candle = self.make_candle(self.first_open)
        received = self.first_open + timedelta(minutes=15, seconds=30)
        runner = self.runner()
        first = runner.append_completed_candle(candle, received)
        self.assertEqual(first["status"], "accepted_waiting_for_next_bar")
        restarted = self.runner()
        duplicate = restarted.append_completed_candle(candle, received)
        self.assertEqual(duplicate["status"], "duplicate_ignored")
        self.assertEqual(len(restarted.prospective), 1)
        with self.assertRaisesRegex(PaperInputError, "conflicting duplicate"):
            restarted.append_completed_candle(self.make_candle(self.first_open, 101), received)

    def test_replay_outputs_auditable_files_and_restart_does_not_duplicate_events(self):
        runner = self.runner()
        first = self.make_candle(self.first_open)
        second = self.make_candle(self.first_open + timedelta(minutes=15), 101)
        runner.append_completed_candle(first, first.timestamp + timedelta(minutes=15, seconds=30))
        result = runner.append_completed_candle(second, second.timestamp + timedelta(minutes=15, seconds=30))
        self.assertTrue(result["replayed"])
        expected = {
            "paper_manifest.json", "prospective_candles.csv", "paper_events.jsonl",
            "paper_summary.json", "paper_equity.csv", "paper_trades.csv",
        }
        self.assertTrue(expected.issubset({path.name for path in self.state.iterdir()}))
        summary = json.loads((self.state / "paper_summary.json").read_text(encoding="utf-8"))
        self.assertTrue(summary["paper_only"])
        self.assertFalse(summary["real_order_submission"])
        events_before = (self.state / "paper_events.jsonl").read_text(encoding="utf-8")
        event_ids_before = {json.loads(line)["event_id"] for line in events_before.splitlines()}

        restarted = self.runner()
        duplicate = restarted.append_completed_candle(second, second.timestamp + timedelta(minutes=15, seconds=30))
        self.assertFalse(duplicate["replayed"])
        events_after = (self.state / "paper_events.jsonl").read_text(encoding="utf-8")
        event_ids_after = {json.loads(line)["event_id"] for line in events_after.splitlines()}
        self.assertEqual(event_ids_before, event_ids_after)
        with (self.state / "prospective_candles.csv").open(newline="", encoding="utf-8") as handle:
            self.assertEqual(len(list(csv.DictReader(handle))), 2)
        self.assertIn("EQUITY_MARKED", events_after)
        self.assertIn("PAPER_CANDLE_ACCEPTED", events_after)

    def test_detailed_events_capture_features_fills_fees_funding_and_equity(self):
        bars = [
            Candle(self.start + timedelta(minutes=15 * i), 100, 101, 99, 100 + i / 10, 1000)
            for i in range(4)
        ]
        with TemporaryDirectory() as output:
            result = IntrabarBacktester(
                BacktestConfig(fee_rate=0.0005, slippage_bps=5, funding_rate_per_8h=0.0001,
                               diagnostic_mode=True, diagnostics_dir=output,
                               logging_level=LogLevel.DEBUG, force_close_at_end=True,
                               kill_switch_mode=KillSwitchMode.SAFETY),
                PositionSizer(RiskConfig(leverage=2, estimated_round_trip_cost_pct=0.002)),
            ).run(bars, OneSignal())
            lines = (Path(output) / "events.jsonl").read_text(encoding="utf-8").splitlines()
            events = [json.loads(line) for line in lines]
        types = [event["event_type"] for event in events]
        signal = next(event for event in events if event["event_type"] == "SIGNAL")
        self.assertIn("execution_atr", signal["indicator_values"])
        self.assertIn("latest_1h", signal["market_context"])
        fills = [event for event in events if event["event_type"] == "ORDER_FILLED"]
        self.assertTrue(fills)
        self.assertGreater(fills[0]["fee"], 0)
        self.assertGreater(fills[0]["slippage"], 0)
        funding = [event for event in events if event["event_type"] == "FUNDING_CHARGED"]
        self.assertEqual(len(funding), 3)
        self.assertTrue(all("trade_id" in event and "funding_charge" in event for event in funding))
        self.assertIn("EQUITY_MARKED", types)
        self.assertEqual(len(result.trades), 1)


if __name__ == "__main__":
    unittest.main()
