from __future__ import annotations

import csv
import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from crypto_bot.frozen_signals import FrozenSignalReader
from crypto_bot.portfolio import run_from_files
from crypto_bot.portfolio_models import PortfolioConfig


ROOT = Path(__file__).resolve().parents[1]


class PortfolioReportingTests(unittest.TestCase):
    def test_run_from_frozen_files_writes_valid_auditable_outputs_without_mutating_sources(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            candles_path, signals_path, metadata_path = root / "candles.csv", root / "signals.csv", root / "metadata.json"
            start = datetime(2025, 1, 1, tzinfo=timezone.utc)
            with candles_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(["timestamp", "open", "high", "low", "close", "volume"])
                for index in range(5):
                    timestamp = start + timedelta(minutes=15 * index)
                    writer.writerow([timestamp.isoformat(), 100, 101, 99, 100, 1000])
            columns = ["signal_id", "timestamp", "available_at", "strategy", "direction", "confidence",
                "ensemble_selected", "reference_price", "stop_price", "take_profit", "forward_outcomes"]
            with signals_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=columns)
                writer.writeheader()
                writer.writerow({"signal_id": 1, "timestamp": start.isoformat(),
                    "available_at": (start + timedelta(minutes=15)).isoformat(), "strategy": "breakout",
                    "direction": "long", "confidence": .8, "ensemble_selected": "True",
                    "reference_price": 100, "stop_price": 95, "take_profit": 120,
                    "forward_outcomes": "must not be consumed"})
            metadata_path.write_text(json.dumps({"source": "synthetic", "candles": 5, "signals": 1}), encoding="utf-8")
            originals = (signals_path.read_bytes(), candles_path.read_bytes(), metadata_path.read_bytes())
            reader = FrozenSignalReader(PortfolioConfig.from_path(ROOT / "configs/athena2_portfolio_baseline_v1.json"))
            output = root / "run"
            result = run_from_files(config_path=ROOT / "configs/athena2_portfolio_baseline_v1.json",
                signal_path=signals_path, metadata_path=metadata_path, candle_path=candles_path,
                expected_signal_sha256=reader.sha256(signals_path), expected_candle_sha256=reader.sha256(candles_path),
                output_dir=output)
            record = json.loads((output / "portfolio_result_v1.json").read_text(encoding="utf-8"))
            event_lines = (output / "portfolio_events_v1.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(record["schema_version"], "athena.portfolio_result.v1")
            self.assertEqual(len(record["dispositions"]), 1)
            self.assertEqual(len(record["journal"]), len(result.ledger.journal))
            self.assertTrue(event_lines)
            self.assertEqual((signals_path.read_bytes(), candles_path.read_bytes(), metadata_path.read_bytes()), originals)
            self.assertEqual(result.reconciliation["cash_delta"], 0)
            self.assertEqual(result.reconciliation["equity_delta"], 0)


if __name__ == "__main__":
    unittest.main()
