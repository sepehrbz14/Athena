from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
from io import StringIO
import unittest

from crypto_bot.m3_chronology import (
    BAR, CandleTimeline, ChronologyError, build_fold_windows, build_opportunity_universe,
    candle_timeline_from_bytes, complete_utc_week_keys,
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
        "embargo_bars": 96, "terminal_entry_blackout_bars": 96,
        "purge_policy": "purge_label_or_trade_outcome_crossing_boundary",
        "warmup_policy": "816_prior_contiguous_15m_bars_reset_after_gap_causal_only_unscored",
        "state_reset": "fresh_account_orders_and_safety_state_per_fold",
        "fold_end_policy": "frozen_phase4_force_close_at_end_with_costs_reported",
        "folds": folds}}


def _months(value, count):
    x = value.year * 12 + value.month - 1 + count
    year, month = divmod(x, 12)
    return value.replace(year=year, month=month + 1)


def _iso(value):
    return value.isoformat().replace("+00:00", "Z")


def signal_csv(rows):
    fields = ["signal_id", "timestamp", "available_at", "strategy",
              "direction", "confidence", "regime", "reference_price", "stop_price",
              "take_profit", "ensemble_selected", "forward_outcomes"]
    output = StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for row in rows:
        writer.writerow({"confidence": "0.7", "regime": "range", "reference_price": "100",
                         "stop_price": "95", "take_profit": "110", "ensemble_selected": "False",
                         "forward_outcomes": "SECRET_FUTURE",
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
        timeline = CandleTimeline.from_timestamps(times)
        endpoint = label_endpoint(times[0], timeline)
        self.assertEqual(endpoint, times[96])
        self.assertNotEqual(endpoint, times[1] + BAR * 96)

    def test_label_purging_uses_strict_boundary_and_gaps_censor(self):
        times = [stamp(2024, 1, 1) + BAR * i for i in range(100)]
        timeline = CandleTimeline.from_timestamps(times)
        endpoint = label_endpoint(times[0], timeline)
        self.assertFalse(label_survives_boundary(endpoint, endpoint))
        self.assertTrue(label_survives_boundary(endpoint, endpoint + BAR))
        del times[50]
        self.assertIsNone(label_endpoint(stamp(2024, 1, 1), CandleTimeline.from_timestamps(times)))

    def test_opportunity_universe_is_sorted_union_and_ignores_forward_outcomes(self):
        t0 = stamp(2024, 2, 1)
        payload = signal_csv([
            {"signal_id": "2", "timestamp": _iso(t0),
             "available_at": _iso(t0 + BAR), "strategy": "breakout", "direction": "long"},
            {"signal_id": "1", "timestamp": _iso(t0),
             "available_at": _iso(t0 + BAR), "strategy": "trend_following", "direction": "short"},
            {"signal_id": "3", "timestamp": _iso(t0 + BAR),
             "available_at": _iso(t0 + BAR * 2), "strategy": "momentum", "direction": "long"},
        ])
        opportunities = build_opportunity_universe(
            payload, instrument_id="BTCUSDT", dataset_manifest_sha256="a" * 64,
            eligible_interval=(t0, t0 + timedelta(days=1)))
        self.assertEqual(len(opportunities.opportunities), 2)
        self.assertEqual(opportunities.opportunities[0].available_at, t0 + BAR)
        self.assertEqual([s.strategy for s in opportunities.opportunities[0].signals], ["breakout", "trend_following"])
        self.assertNotIn("SECRET_FUTURE", repr(opportunities))
        repeated = build_opportunity_universe(
            payload, instrument_id="BTCUSDT", dataset_manifest_sha256="a" * 64,
            eligible_interval=(t0, t0 + timedelta(days=1)))
        self.assertEqual([o.opportunity_id for o in opportunities.opportunities],
                         [o.opportunity_id for o in repeated.opportunities])
        # Both eventual candidate and incumbent consumers are handed this one tuple.
        self.assertEqual(tuple(o.opportunity_id for o in opportunities.opportunities),
                         tuple(o.opportunity_id for o in repeated.opportunities))

    def test_duplicate_strategy_time_fails_and_missing_strategy_is_not_synthesized(self):
        t0 = stamp(2024, 2, 1)
        row = {"signal_id": "1", "timestamp": _iso(t0),
               "available_at": _iso(t0 + BAR), "strategy": "breakout", "direction": "long"}
        duplicate = signal_csv([row, {**row, "signal_id": "2"}])
        with self.assertRaisesRegex(ChronologyError, "duplicate strategy"):
            build_opportunity_universe(duplicate, instrument_id="BTC", dataset_manifest_sha256="a"*64,
                                       eligible_interval=(t0, t0 + timedelta(days=1)))
        single = build_opportunity_universe(signal_csv([row]), instrument_id="BTC",
                                            dataset_manifest_sha256="a"*64,
                                            eligible_interval=(t0, t0 + timedelta(days=1)))
        self.assertEqual(len(single.opportunities[0].signals), 1)
        self.assertEqual(single.opportunities[0].signals[0].strategy, "breakout")

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

    def test_signal_schema_does_not_require_non_schema_candle_index(self):
        t0 = stamp(2024, 2, 1)
        row = {"signal_id": "1", "timestamp": _iso(t0), "available_at": _iso(t0 + BAR),
               "strategy": "breakout", "direction": "long"}
        universe = build_opportunity_universe(signal_csv([row]), instrument_id="BTCUSDT",
                                              dataset_manifest_sha256="a"*64,
                                              eligible_interval=(t0, t0 + timedelta(days=1)))
        self.assertEqual(universe.opportunities[0].signals[0].signal_timestamp, t0)

    def test_duplicate_signal_ids_fail_even_when_strategy_and_time_differ(self):
        t0 = stamp(2024, 2, 1)
        rows = [
            {"signal_id": "7", "timestamp": _iso(t0), "available_at": _iso(t0 + BAR),
             "strategy": "breakout", "direction": "long"},
            {"signal_id": "7", "timestamp": _iso(t0 + BAR), "available_at": _iso(t0 + BAR*2),
             "strategy": "momentum", "direction": "short"},
        ]
        with self.assertRaisesRegex(ChronologyError, "duplicate frozen signal_id"):
            build_opportunity_universe(signal_csv(rows), instrument_id="BTCUSDT",
                                       dataset_manifest_sha256="a"*64,
                                       eligible_interval=(t0, t0 + timedelta(days=1)))

    def test_invalid_signal_numeric_fields_fail_closed(self):
        t0 = stamp(2024, 2, 1)
        base = {"signal_id": "1", "timestamp": _iso(t0), "available_at": _iso(t0 + BAR),
                "strategy": "breakout", "direction": "long"}
        for field, bad in (("signal_id", "1.5"), ("confidence", "NaN"),
                           ("confidence", "1.01"), ("reference_price", "Infinity"),
                           ("stop_price", "0"), ("take_profit", "not-a-number")):
            with self.subTest(field=field, bad=bad):
                with self.assertRaises(ChronologyError):
                    build_opportunity_universe(signal_csv([{**base, field: bad}]),
                                               instrument_id="BTCUSDT", dataset_manifest_sha256="a"*64,
                                               eligible_interval=(t0, t0 + timedelta(days=1)))

    def test_invalid_signal_timestamps_fail_closed(self):
        t0 = stamp(2024, 2, 1)
        base = {"signal_id": "1", "timestamp": _iso(t0), "available_at": _iso(t0 + BAR),
                "strategy": "breakout", "direction": "long"}
        for field, bad in (("timestamp", "2024-02-01T00:00:00"),
                           ("available_at", "not-a-timestamp")):
            with self.subTest(field=field):
                with self.assertRaises(ChronologyError):
                    build_opportunity_universe(signal_csv([{**base, field: bad}]),
                                               instrument_id="BTCUSDT", dataset_manifest_sha256="a"*64,
                                               eligible_interval=(t0, t0 + timedelta(days=1)))

    def test_missing_signal_timestamp_cell_and_duplicate_headers_fail_closed(self):
        t0 = stamp(2024, 2, 1)
        base = {"signal_id": "1", "timestamp": _iso(t0), "available_at": _iso(t0 + BAR),
                "strategy": "breakout", "direction": "long"}
        payload = signal_csv([{**base, "timestamp": None}])
        with self.assertRaises(ChronologyError):
            build_opportunity_universe(payload, instrument_id="BTC", dataset_manifest_sha256="a"*64,
                                       eligible_interval=(t0, t0 + timedelta(days=1)))
        duplicate_header = payload.replace(b"signal_id,timestamp,available_at",
                                           b"signal_id,timestamp,available_at,timestamp")
        with self.assertRaisesRegex(ChronologyError, "duplicate column"):
            build_opportunity_universe(duplicate_header, instrument_id="BTC", dataset_manifest_sha256="a"*64,
                                       eligible_interval=(t0, t0 + timedelta(days=1)))

    def test_exact_pinned_candle_timeline_resolves_by_signal_timestamp(self):
        t0 = stamp(2024, 1, 1)
        raw = "timestamp,open,high,low,close,volume\n" + "".join(
            f"{int((t0 + BAR*i).timestamp()*1000)},1,1,1,1,1\n" for i in range(100))
        timeline = candle_timeline_from_bytes(raw.encode())
        # No signal-row index is provided; resolve from the pinned time sequence.
        self.assertEqual(timeline.resolve(t0), 0)
        self.assertEqual(label_endpoint(t0, timeline), t0 + BAR*96)
        with self.assertRaisesRegex(ChronologyError, "disagrees"):
            label_endpoint(t0, timeline, recorded_signal_index=1)
        with self.assertRaisesRegex(ChronologyError, "absent"):
            timeline.resolve(t0 - BAR)

    def test_duplicate_or_reordered_pinned_candle_timestamps_fail_closed(self):
        t0 = stamp(2024, 1, 1)
        with self.assertRaisesRegex(ChronologyError, "strictly chronological"):
            CandleTimeline.from_timestamps([t0, t0])
        with self.assertRaisesRegex(ChronologyError, "strictly chronological"):
            CandleTimeline.from_timestamps([t0 + BAR, t0])

    def test_missing_or_duplicate_pinned_candle_timestamp_column_fails_closed(self):
        with self.assertRaises(ChronologyError):
            candle_timeline_from_bytes(b"timestamp,open\\n,1\\n")
        with self.assertRaisesRegex(ChronologyError, "duplicate column"):
            candle_timeline_from_bytes(b"timestamp,timestamp,open\\n1,1,1\\n")

    def test_utc_week_calendar_includes_empty_weeks_and_normalizes_offsets(self):
        start = datetime(2024, 2, 5, 3, tzinfo=timezone(timedelta(hours=3)))
        end = datetime(2024, 3, 4, tzinfo=UTC)
        self.assertEqual(complete_utc_week_keys((start, end)),
                         ("2024-W06", "2024-W07", "2024-W08", "2024-W09"))
        empty = build_opportunity_universe(signal_csv([]), instrument_id="BTCUSDT",
                                           dataset_manifest_sha256="a"*64,
                                           eligible_interval=(start, end))
        self.assertEqual(empty.opportunities, ())
        self.assertEqual(len(empty.utc_week_keys), 4)


if __name__ == "__main__":
    unittest.main()

