from __future__ import annotations

import csv
import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from crypto_bot.io import load_ohlcv_csv
from crypto_bot.frozen_signals import FrozenSignalInputError, FrozenSignalReader
from crypto_bot.portfolio_models import PortfolioConfig


ROOT = Path(__file__).resolve().parents[1]


class FrozenSignalReaderTests(unittest.TestCase):
    def setUp(self):
        self.config = PortfolioConfig.from_path(ROOT / "configs/athena2_portfolio_baseline_v1.json")

    def _write_inputs(self, directory: Path, rows: list[dict[str, str]], candle_count: int = 3):
        candle_path = directory / "candles.csv"
        start = datetime(2025, 1, 1, tzinfo=timezone.utc)
        with candle_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["timestamp", "open", "high", "low", "close", "volume"])
            for index in range(candle_count):
                timestamp = start + timedelta(minutes=15 * index)
                writer.writerow([timestamp.isoformat(), 100, 102, 98, 100 + index, 1000])
        signal_path = directory / "signals.csv"
        columns = ["signal_id", "timestamp", "available_at", "strategy", "direction", "confidence",
                   "ensemble_selected", "reference_price", "stop_price", "take_profit", "forward_outcomes"]
        with signal_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns)
            writer.writeheader()
            writer.writerows(rows)
        metadata_path = directory / "metadata.json"
        metadata_path.write_text(json.dumps({"source": "synthetic-frozen-source", "candles": candle_count,
                                             "signals": len(rows)}), encoding="utf-8")
        return signal_path, metadata_path, candle_path, load_ohlcv_csv(candle_path)

    @staticmethod
    def row(signal_id=1, timestamp="2025-01-01T00:00:00+00:00", selected="True", strategy="breakout"):
        return {"signal_id": str(signal_id), "timestamp": timestamp,
                "available_at": (datetime.fromisoformat(timestamp) + timedelta(minutes=15)).isoformat(),
                "strategy": strategy, "direction": "long", "confidence": "0.8",
                "ensemble_selected": selected, "reference_price": "100", "stop_price": "95",
                "take_profit": "110", "forward_outcomes": "this column must be ignored"}

    def test_selects_only_frozen_ensemble_rows_and_records_every_raw_disposition(self):
        with TemporaryDirectory() as temp:
            directory = Path(temp)
            inputs = self._write_inputs(directory, [self.row(1), self.row(2, selected="False")])
            signal_path, metadata_path, candle_path, candles = inputs
            before = (signal_path.read_bytes(), metadata_path.read_bytes(), candle_path.read_bytes())
            reader = FrozenSignalReader(self.config)
            signals, dispositions, manifest = reader.read(signal_path=signal_path, metadata_path=metadata_path,
                candle_path=candle_path, candles=candles, expected_signal_sha256=reader.sha256(signal_path),
                expected_candle_sha256=reader.sha256(candle_path))
            self.assertEqual([signal.signal_id for signal in signals], [1])
            self.assertEqual([d.status for d in dispositions], ["eligible", "filtered"])
            self.assertEqual(dispositions[1].reason_code, "NOT_ENSEMBLE_SELECTED")
            self.assertEqual(manifest["selected_eligible_rows"], 1)
            self.assertEqual(manifest["not_ensemble_selected_rows"], 1)
            self.assertEqual((signal_path.read_bytes(), metadata_path.read_bytes(), candle_path.read_bytes()), before)

    def test_hash_metadata_boolean_timestamp_close_and_duplicates_fail_closed(self):
        with TemporaryDirectory() as temp:
            directory = Path(temp)
            signal_path, metadata_path, candle_path, candles = self._write_inputs(directory, [self.row()])
            reader = FrozenSignalReader(self.config)
            with self.assertRaisesRegex(FrozenSignalInputError, "SOURCE_HASH_MISMATCH"):
                reader.read(signal_path=signal_path, metadata_path=metadata_path, candle_path=candle_path,
                    candles=candles, expected_signal_sha256="0" * 64, expected_candle_sha256=reader.sha256(candle_path))

            duplicate_rows = [self.row(1), self.row(2, strategy="momentum")]
            signal_path, metadata_path, candle_path, candles = self._write_inputs(directory, duplicate_rows)
            signals, dispositions, _ = reader.read(signal_path=signal_path, metadata_path=metadata_path,
                candle_path=candle_path, candles=candles, expected_signal_sha256=reader.sha256(signal_path),
                expected_candle_sha256=reader.sha256(candle_path))
            self.assertEqual(signals, [])
            self.assertEqual([d.reason_code for d in dispositions], ["DUPLICATE_SELECTED_SIGNAL"] * 2)

            invalid_bool = [self.row(3, selected="true")]
            signal_path, metadata_path, candle_path, candles = self._write_inputs(directory, invalid_bool)
            _, dispositions, _ = reader.read(signal_path=signal_path, metadata_path=metadata_path,
                candle_path=candle_path, candles=candles, expected_signal_sha256=reader.sha256(signal_path),
                expected_candle_sha256=reader.sha256(candle_path))
            self.assertEqual(dispositions[0].reason_code, "INVALID_SIGNAL")

    def test_timestamp_close_and_end_of_data_have_explicit_reasons(self):
        with TemporaryDirectory() as temp:
            directory = Path(temp)
            invalid = self.row(1)
            invalid["reference_price"] = "100.1"
            last = self.row(2, timestamp="2025-01-01T00:30:00+00:00")
            last["reference_price"] = "102"
            signal_path, metadata_path, candle_path, candles = self._write_inputs(directory, [invalid, last])
            reader = FrozenSignalReader(self.config)
            signals, dispositions, _ = reader.read(signal_path=signal_path, metadata_path=metadata_path,
                candle_path=candle_path, candles=candles, expected_signal_sha256=reader.sha256(signal_path),
                expected_candle_sha256=reader.sha256(candle_path))
            self.assertEqual(signals, [])
            self.assertEqual(dispositions[0].reason_code, "TIMESTAMP_JOIN_MISMATCH")
            self.assertEqual(dispositions[1].reason_code, "ORDER_EXPIRED")


if __name__ == "__main__":
    unittest.main()
