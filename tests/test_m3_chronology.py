from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
from io import StringIO
import unittest

from crypto_bot.m3_chronology import (
    BAR, ChronologyError, build_fold_windows, build_opportunity_universe,
    is_entry_in_terminal_blackout, label_endpoint, label_survives_boundary,
    position_horizon_within_fold, warmup_ready,
)


UTC = timezone.utc


def stamp(year, month, day=1):
    return datetime(year, month, day, tzinfo=UTC)


def protocol_fixture():
    folds = []
    for n in range(6):
        test_start = datetime(2021 + (n * 6) // 12, (n * 6) % 12 + 1, 1, tzinfo=UTC)
        # Validation follows a one-month elapsed embargo; train history is 24m.
        test_end = _months(test_start, 6)
        val_end = _months(test_start, -1)
        val_start = _months(test_start, -7)
        train_end = _months(val_start, -1)
        train_start = _months(train_end, -24)
        folds.append({"fold_id": f"outer-{n+1}",
                      "train_start_utc": _iso(train_start), "train_end_utc": _iso(train_end),
                      "validation_start_utc": _iso(val_start), "validation_end_utc": _iso(val_end),
                      "outer_test_start_utc": _iso(test_start), "outer_test_end_utc": _iso(test_end)})
    return {"chronology": {
        "clock": "UTC_signal_availability", "fold_method": "nested_expanding_rolling_origin",
        "min_training_months": 24, "inner_validation_months": 6,
        "outer_test_months": 6, "outer_step_months": 6,
        "minimum_eligible_outer_folds": 6, "nonoverlapping_outer_tests": True,
        "max_label_horizon_bars": 96, "max_position_horizon_bars": 17664,
        "embargo_bars": 96, "terminal_entry_blackout_bars": 96, "folds": folds}}


def _months(value, count):
    x = value.year * 12 + value.month - 1 + count
    year, month = divmod(x, 12)
    return value.replace(year=year, month=month + 1)


def _iso(value):
    return value.isoformat().replace("+00:00", "Z")


def signal_csv(rows):
    fields = ["signal_id", "candle_index", "timestamp", "available_at", "strategy",
              "direction", "confidence", "regime", "reference_price", "stop_price",
              "take_profit", "forward_outcomes"]
    output = StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for row in rows:
        writer.writerow({"confidence": "0.7", "regime": "range", "reference_price": "100",
                         "stop_price": "95", "take_profit": "110", "forward_outcomes": "SECRET_FUTURE",
                         **row})
    return output.getvalue().encode()


class M3ChronologyTests(unittest.TestCase):
    def test_frozen_registered_folds_materialize_without_boundary_changes(self):
        windows = build_fold_windows(protocol_fixture())
        self.assertEqual(len(windows), 6)
        self.assertEqual(windows[0].outer_test[0], stamp(2021, 1))
        self.assertEqual(windows[0].train_validation_embargo[1] - windows[0].train_validation_embargo[0], timedelta(days=31))
        self.assertEqual(windows[0].validation_test_embargo[1] - windows[0].validation_test_embargo[0], timedelta(days=31))

    def test_rejects_short_or_overlapping_registered_fold_and_short_embargo(self):
        protocol = protocol_fixture()
        protocol["chronology"]["folds"][0]["validation_start_utc"] = protocol["chronology"]["folds"][0]["train_end_utc"]
        with self.assertRaises(ChronologyError):
            build_fold_windows(protocol)
        protocol = protocol_fixture()
        protocol["chronology"]["folds"][1]["outer_test_start_utc"] = _iso(
            _months(datetime.fromisoformat(
                protocol["chronology"]["folds"][0]["outer_test_end_utc"].replace("Z", "+00:00")), -1))
        with self.assertRaises(ChronologyError):
            build_fold_windows(protocol)

    def test_available_at_is_decision_time_but_does_not_shift_label_endpoint(self):
        times = [stamp(2024, 1, 1) + BAR * i for i in range(100)]
        endpoint = label_endpoint(times[0], 0, times)
        self.assertEqual(endpoint, times[96])
        self.assertNotEqual(endpoint, times[1] + BAR * 96)

    def test_label_purging_uses_strict_boundary_and_gaps_censor(self):
        times = [stamp(2024, 1, 1) + BAR * i for i in range(100)]
        endpoint = label_endpoint(times[0], 0, times)
        self.assertFalse(label_survives_boundary(endpoint, endpoint))
        self.assertTrue(label_survives_boundary(endpoint, endpoint + BAR))
        times[50] += BAR
        self.assertIsNone(label_endpoint(stamp(2024, 1, 1), 0, times))

    def test_opportunity_universe_is_sorted_union_and_ignores_forward_outcomes(self):
        t0 = stamp(2024, 2, 1)
        payload = signal_csv([
            {"signal_id": "2", "candle_index": "1", "timestamp": _iso(t0),
             "available_at": _iso(t0 + BAR), "strategy": "breakout", "direction": "long"},
            {"signal_id": "1", "candle_index": "1", "timestamp": _iso(t0),
             "available_at": _iso(t0 + BAR), "strategy": "trend_following", "direction": "short"},
            {"signal_id": "3", "candle_index": "2", "timestamp": _iso(t0 + BAR),
             "available_at": _iso(t0 + BAR * 2), "strategy": "momentum", "direction": "long"},
        ])
        opportunities = build_opportunity_universe(
            payload, instrument_id="BTCUSDT", dataset_manifest_sha256="a" * 64,
            eligible_interval=(t0, t0 + timedelta(days=1)))
        self.assertEqual(len(opportunities), 2)
        self.assertEqual(opportunities[0].available_at, t0 + BAR)
        self.assertEqual([s.strategy for s in opportunities[0].signals], ["breakout", "trend_following"])
        self.assertNotIn("SECRET_FUTURE", repr(opportunities))
        repeated = build_opportunity_universe(
            payload, instrument_id="BTCUSDT", dataset_manifest_sha256="a" * 64,
            eligible_interval=(t0, t0 + timedelta(days=1)))
        self.assertEqual([o.opportunity_id for o in opportunities], [o.opportunity_id for o in repeated])

    def test_duplicate_strategy_time_fails_and_missing_strategy_is_not_synthesized(self):
        t0 = stamp(2024, 2, 1)
        row = {"signal_id": "1", "candle_index": "0", "timestamp": _iso(t0),
               "available_at": _iso(t0 + BAR), "strategy": "breakout", "direction": "long"}
        duplicate = signal_csv([row, {**row, "signal_id": "2"}])
        with self.assertRaisesRegex(ChronologyError, "duplicate strategy"):
            build_opportunity_universe(duplicate, instrument_id="BTC", dataset_manifest_sha256="a"*64,
                                       eligible_interval=(t0, t0 + timedelta(days=1)))
        single = build_opportunity_universe(signal_csv([row]), instrument_id="BTC",
                                            dataset_manifest_sha256="a"*64,
                                            eligible_interval=(t0, t0 + timedelta(days=1)))
        self.assertEqual(len(single[0].signals), 1)
        self.assertEqual(single[0].signals[0].strategy, "breakout")

    def test_warmup_requires_816_contiguous_prior_bars_and_resets_after_gap(self):
        times = [stamp(2024, 1, 1) + BAR * i for i in range(817)]
        self.assertFalse(warmup_ready(815, times))
        self.assertTrue(warmup_ready(816, times))
        times[500] += BAR
        self.assertFalse(warmup_ready(816, times))

    def test_terminal_blackout_and_position_horizon_boundaries(self):
        end = stamp(2024, 7, 1)
        self.assertTrue(is_entry_in_terminal_blackout(end - timedelta(hours=24), end))
        self.assertFalse(is_entry_in_terminal_blackout(end - timedelta(hours=24) - BAR, end))
        self.assertTrue(position_horizon_within_fold(0, 17664))
        self.assertFalse(position_horizon_within_fold(0, 17665))


if __name__ == "__main__":
    unittest.main()

