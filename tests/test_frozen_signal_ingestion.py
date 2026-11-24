"""Synthetic acceptance tests for the read-only frozen signal boundary."""
from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from crypto_bot.frozen_signal_ingestion import (
    FrozenSignalInputError,
    FrozenSignalIngestionConfig,
    ingest_frozen_signals,
)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class FrozenSignalIngestionTests(unittest.TestCase):
    columns = [
        "signal_id", "timestamp", "available_at", "strategy", "direction", "confidence",
        "ensemble_selected", "reference_price", "stop_price", "take_profit", "forward_outcomes",
    ]

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.signal_path = self.root / "signals.csv"
        self.metadata_path = self.root / "metadata.json"
        self.candle_path = self.root / "candles.csv"
        self.input_manifest_path = self.root / "input_manifest.json"
        self.candles = [
            {"timestamp": f"2024-01-01T00:{minute:02d}:00Z", "open": "100", "high": "102",
             "low": "99", "close": str(100 + minute / 100), "volume": "10"}
            for minute in (0, 15, 30, 45)
        ]
        self.signals = [
            self._signal(1, "2024-01-01T00:00:00Z", "True", reference="100.0"),
            self._signal(2, "2024-01-01T00:15:00Z", "False", reference="not-used",
                         direction="invalid", confidence="NaN"),
            self._signal(3, "2024-01-01T00:30:00Z", "True", reference="100.3"),
        ]
        self._write_inputs()

    def tearDown(self):
        self.temp.cleanup()

    def _signal(self, signal_id, timestamp, selected, *, reference="100.0",
                available=None, direction="long", confidence="0.8", strategy="breakout",
                stop="95", target="110", outcomes="{\"96\":{\"return\":0.2}}"):
        stamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        available = available or (stamp + timedelta(minutes=15)).isoformat().replace("+00:00", "Z")
        return {
            "signal_id": str(signal_id), "timestamp": timestamp, "available_at": available,
            "strategy": strategy, "direction": direction, "confidence": confidence,
            "ensemble_selected": selected, "reference_price": reference, "stop_price": stop,
            "take_profit": target, "forward_outcomes": outcomes,
        }

    def _write_csv(self, path, columns, rows):
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)

    def _write_inputs(self, *, signals=None, candles=None, metadata_override=None):
        self.signals = list(self.signals if signals is None else signals)
        self.candles = list(self.candles if candles is None else candles)
        self._write_csv(self.signal_path, self.columns, self.signals)
        candle_columns = ["timestamp", "open", "high", "low", "close", "volume"]
        self._write_csv(self.candle_path, candle_columns, self.candles)
        metadata = metadata_override or {"signals": len(self.signals), "candles": len(self.candles),
                                         "policy": "synthetic test only"}
        self.metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
        manifest = {
            "schema_version": "athena.frozen-signal-input-manifest.v1",
            "signal_schema_version": "athena.frozen_signal_csv.v1",
            "metadata_schema_version": "athena.frozen-signal-capture-metadata.v1",
            "candle_schema_version": "athena.ohlcv.csv.v1",
            "candle_interval_minutes": 15,
            "signals_sha256": _sha(self.signal_path.read_bytes()),
            "metadata_sha256": _sha(self.metadata_path.read_bytes()),
            "candles_sha256": _sha(self.candle_path.read_bytes()),
        }
        self.input_manifest_path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")

    def _ingest(self, **kwargs):
        return ingest_frozen_signals(
            signal_csv_path=self.signal_path,
            metadata_path=self.metadata_path,
            candle_csv_path=self.candle_path,
            input_manifest_path=self.input_manifest_path,
            **kwargs,
        )

    def test_selects_only_true_rows_and_returns_canonical_execution_adapter(self):
        result = self._ingest()
        self.assertEqual([event.signal_id for event in result.signals], [1, 3])
        self.assertEqual([event.execution_at.isoformat() for event in result.signals],
                         ["2024-01-01T00:15:00+00:00", "2024-01-01T00:45:00+00:00"])
        self.assertEqual(result.signals[0].to_signal().strategy, "breakout")
        self.assertEqual(result.signals[0].to_signal().stop_price, 95)
        self.assertEqual(result.manifest["total_raw_rows"], 3)
        self.assertEqual(result.manifest["selected_rows"], 2)
        self.assertEqual(result.manifest["excluded_nonselected_rows"], 1)
        self.assertEqual(result.manifest["invalid_input_count"], 0)
        self.assertEqual(result.manifest["ordering_policy"],
                         "ascending_utc_available_at_instrument_id_signal_id")
        self.assertEqual(result.manifest["normalized_signal_schema_version"], "athena.frozen_signal.v1")
        self.assertEqual(result.manifest["configuration"]["candle_interval_minutes"], 15)

    def test_hash_mismatch_for_each_committed_input_fails_before_parsing(self):
        for path, content in (
            (self.signal_path, b"not even a CSV"),
            (self.metadata_path, b"not JSON"),
            (self.candle_path, b"broken candle file"),
        ):
            with self.subTest(path=path.name):
                original = path.read_bytes()
                path.write_bytes(content)
                with self.assertRaises(FrozenSignalInputError) as caught:
                    self._ingest()
                self.assertIn("SHA-256 commitment mismatch", str(caught.exception))
                self.assertGreater(caught.exception.invalid_input_count, 0)
                path.write_bytes(original)

    def test_missing_metadata_and_invalid_manifest_fail_closed(self):
        self.metadata_path.unlink()
        with self.assertRaisesRegex(FrozenSignalInputError, "source metadata"):
            self._ingest()
        self._write_inputs()
        self.input_manifest_path.write_text('{"schema_version":"wrong"}', encoding="utf-8")
        with self.assertRaisesRegex(FrozenSignalInputError, "strict versioned schema"):
            self._ingest()
        self._write_inputs(metadata_override={"signals": "three", "candles": 4})
        with self.assertRaisesRegex(FrozenSignalInputError, "source metadata.*strict versioned schema"):
            self._ingest()

    def test_selected_malformed_timestamps_numeric_and_boolean_fail_closed(self):
        cases = (
            ("timestamp", "not-a-time"),
            ("timestamp", "2024-01-01T00:00:00"),
            ("available_at", "2024-01-01T00:14:00Z"),
            ("confidence", "NaN"),
            ("reference_price", "Infinity"),
            ("stop_price", "not-a-number"),
            ("take_profit", "-5"),
            ("ensemble_selected", "yes"),
            ("stop_price", "100"),
        )
        for field, bad_value in cases:
            with self.subTest(field=field, value=bad_value):
                rows = [dict(row) for row in self.signals]
                rows[0][field] = bad_value
                self._write_inputs(signals=rows)
                with self.assertRaises(FrozenSignalInputError) as caught:
                    self._ingest()
                self.assertGreater(caught.exception.invalid_input_count, 0)
                self.assertTrue(caught.exception.rejection_reasons)

    def test_duplicate_selected_ids_and_duplicate_selected_identity_are_rejected(self):
        duplicate_id = [dict(self.signals[0]), dict(self.signals[0])]
        with self.assertRaisesRegex(FrozenSignalInputError, "duplicate selected signal_id"):
            self._write_inputs(signals=duplicate_id)
            self._ingest()

        duplicate_identity = [dict(self.signals[0]), dict(self.signals[0])]
        duplicate_identity[1]["signal_id"] = "99"
        with self.assertRaisesRegex(FrozenSignalInputError, "duplicate selected"):
            self._write_inputs(signals=duplicate_identity)
            self._ingest()

    def test_missing_source_candle_following_execution_candle_and_reference_mismatch_fail(self):
        base_signal = dict(self.signals[0])
        rows = [dict(base_signal)]
        rows[0]["timestamp"] = "2024-01-02T00:00:00Z"
        rows[0]["available_at"] = "2024-01-02T00:15:00Z"
        self._write_inputs(signals=rows)
        with self.assertRaisesRegex(FrozenSignalInputError, "no exact source candle"):
            self._ingest()

        candles_without_next = [dict(row) for row in self.candles if row["timestamp"] != "2024-01-01T00:15:00Z"]
        self._write_inputs(signals=[dict(base_signal)], candles=candles_without_next)
        with self.assertRaisesRegex(FrozenSignalInputError, "no following execution candle"):
            self._ingest()

        rows = [dict(base_signal)]
        rows[0]["reference_price"] = "101"
        self._write_inputs(signals=rows)
        with self.assertRaisesRegex(FrozenSignalInputError, "differs from exact source candle close"):
            self._ingest()

    def test_duplicate_out_of_order_and_malformed_candles_are_rejected(self):
        cases = []
        duplicate = [dict(row) for row in self.candles]
        duplicate[1]["timestamp"] = duplicate[0]["timestamp"]
        cases.append(duplicate)
        reordered = [dict(row) for row in self.candles]
        reordered[0], reordered[1] = reordered[1], reordered[0]
        cases.append(reordered)
        malformed = [dict(row) for row in self.candles]
        malformed[0]["close"] = "nan"
        cases.append(malformed)
        for rows in cases:
            with self.subTest(rows=rows[0]):
                self._write_inputs(candles=rows)
                with self.assertRaises(FrozenSignalInputError):
                    self._ingest()

    def test_candle_epoch_milliseconds_match_repository_timestamp_convention(self):
        rows = [dict(row) for row in self.candles]
        for row in rows:
            stamp = datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00"))
            row["timestamp"] = f"{stamp.timestamp() * 1000:.1f}"
        self._write_inputs(candles=rows)
        result = self._ingest()
        self.assertEqual(result.candles[0].timestamp.isoformat(), "2024-01-01T00:00:00+00:00")
        self.assertEqual(result.signals[0].candle_open_time, result.candles[0].timestamp)

    def test_misaligned_candle_time_fails_against_declared_interval(self):
        rows = [dict(row) for row in self.candles]
        rows[1]["timestamp"] = "2024-01-01T00:16:00Z"
        self._write_inputs(candles=rows)
        with self.assertRaisesRegex(FrozenSignalInputError, "not aligned to the declared interval"):
            self._ingest()

    def test_signal_row_reordering_does_not_change_canonical_events(self):
        baseline = self._ingest()
        reordered = list(reversed(self.signals))
        self._write_inputs(signals=reordered)
        replay = self._ingest()
        project = lambda rows: [
            (event.signal_id, event.available_at, event.execution_at, event.strategy,
             event.direction, event.confidence, event.reference_price, event.stop_price,
             event.take_profit)
            for event in rows
        ]
        self.assertEqual(project(baseline.signals), project(replay.signals))

    def test_forward_outcome_column_is_not_consumed(self):
        baseline = self._ingest()
        changed = [dict(row) for row in self.signals]
        changed[0]["forward_outcomes"] = "arbitrary future result must be ignored"
        self._write_inputs(signals=changed)
        replay = self._ingest()
        self.assertEqual(baseline.signals[0].to_signal(), replay.signals[0].to_signal())
        self.assertEqual(baseline.signals[0].available_at, replay.signals[0].available_at)
        self.assertNotIn("forward_outcomes", json.dumps(replay.manifest))

    def test_inputs_remain_byte_identical_and_repeat_is_deterministic(self):
        originals = {path: path.read_bytes() for path in
                     (self.signal_path, self.metadata_path, self.candle_path, self.input_manifest_path)}
        first = self._ingest()
        second = self._ingest()
        self.assertEqual(first.manifest, second.manifest)
        self.assertEqual(first.signals, second.signals)
        self.assertEqual({path: path.read_bytes() for path in originals}, originals)
        output = self.root / "run" / "ingestion_manifest.json"
        written = self._ingest(output_manifest_path=output)
        self.assertEqual(json.loads(output.read_text(encoding="utf-8")), written.manifest)
        with self.assertRaisesRegex(FrozenSignalInputError, "refuses overwrite"):
            self._ingest(output_manifest_path=output)
        self.assertEqual({path: path.read_bytes() for path in originals}, originals)

    def test_config_tolerance_and_timestamp_offsets_are_explicit(self):
        rows = [dict(self.signals[0])]
        rows[0]["reference_price"] = "100.00000001"
        rows[0]["timestamp"] = "2024-01-01T03:30:00+03:30"
        rows[0]["available_at"] = "2024-01-01T03:45:00+03:30"
        self._write_inputs(signals=rows)
        with self.assertRaisesRegex(FrozenSignalInputError, "differs from exact source candle close"):
            self._ingest(config=FrozenSignalIngestionConfig(reference_price_tolerance_abs=1e-9))
        self.assertEqual(len(self._ingest(config=FrozenSignalIngestionConfig(
            reference_price_tolerance_abs=2e-8)).signals), 1)


if __name__ == "__main__":
    unittest.main()
