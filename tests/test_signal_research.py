import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from run_signal_quality_cost_sensitivity import bootstrap_interval, causal_outcomes
from crypto_bot.models import Candle


class SignalResearchTests(unittest.TestCase):
    def test_forward_outcomes_start_after_signal_close_and_are_direction_adjusted(self):
        start = datetime(2025, 1, 1, tzinfo=timezone.utc)
        candles = [
            Candle(start + timedelta(minutes=15 * i), 100, 100, 100, 100, 10)
            for i in range(5)
        ]
        candles[1] = Candle(start + timedelta(minutes=15), 100, 103, 99, 102, 10)
        candles[2] = Candle(start + timedelta(minutes=30), 102, 105, 98, 104, 10)
        signal = {
            "timestamp": candles[0].timestamp.isoformat(), "signal_id": "1",
            "strategy": "test", "side": "long", "confidence": "0.75",
            "regime": "range", "indicator_values": '{"feature": 7}', "rationale": "test signal",
        }
        outcomes = causal_outcomes([signal], candles, "test_window", start, start + timedelta(hours=2))

        one = next(row for row in outcomes if row["horizon_candles"] == 1)
        four = next(row for row in outcomes if row["horizon_candles"] == 4)
        self.assertAlmostEqual(one["forward_direction_adjusted_return_pct"], 2.0)
        self.assertAlmostEqual(one["maximum_favorable_excursion_pct"], 3.0)
        self.assertAlmostEqual(one["maximum_adverse_excursion_pct"], -1.0)
        self.assertEqual(one["outcome_end_candle_open_timestamp"], candles[1].timestamp.isoformat())
        self.assertAlmostEqual(four["forward_direction_adjusted_return_pct"], 0.0)
        self.assertEqual(four["decision_features"], '{"feature": 7}')
        self.assertIn("not executable trade P&L", four["outcome_type"])

    def test_short_forward_return_and_excursions_reverse_direction(self):
        start = datetime(2025, 1, 1, tzinfo=timezone.utc)
        candles = [Candle(start + timedelta(minutes=15 * i), 100, 100, 100, 100, 1) for i in range(2)]
        candles[1] = Candle(start + timedelta(minutes=15), 100, 104, 97, 98, 1)
        signal = {"timestamp": candles[0].timestamp.isoformat(), "signal_id": "1", "strategy": "test",
                  "side": "short", "confidence": "0.7", "regime": "range", "indicator_values": "{}", "rationale": ""}
        outcome = causal_outcomes([signal], candles, "test_window", start, start + timedelta(hours=1))[0]
        self.assertAlmostEqual(outcome["forward_direction_adjusted_return_pct"], 2.0)
        self.assertAlmostEqual(outcome["maximum_favorable_excursion_pct"], 3.0)
        self.assertAlmostEqual(outcome["maximum_adverse_excursion_pct"], -4.0)

    def test_block_bootstrap_is_deterministic(self):
        blocks = [[1.0, 2.0], [-1.0], [0.5]]
        first = bootstrap_interval(blocks, seed=42)
        second = bootstrap_interval(blocks, seed=42)
        self.assertEqual(first, second)
        self.assertEqual(first[-1], 200)


if __name__ == "__main__":
    unittest.main()
