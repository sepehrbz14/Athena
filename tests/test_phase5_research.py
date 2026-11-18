from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from crypto_bot.models import Candle
from crypto_bot.phase5_research import (
    _band,
    _clustering_rows,
    _entry_timing,
    _moving_block_bootstrap,
    _predictive_rows,
    _split_audit,
    _trade_slice_rows,
    _weekly_monthly,
    _truth,
    summarize,
    wilson_interval,
)


class Phase5ResearchTests(unittest.TestCase):
    def test_scorecard_keeps_lane_and_signed_funding_contribution(self) -> None:
        rows = [{
            "gross_pnl": 12.0, "execution_gross_pnl": 10.0, "fees": 1.0, "slippage": 1.0,
            "funding_contribution": -0.5, "net_pnl": 8.5, "holding_hours": 2.0,
            "mfe_pct": 1.5, "mae_pct": -0.5, "exit_reason": "take_profit", "entry_week": "2020-W01",
            "gap_at_open": True, "initial_stop_budget_breach": True, "stop_distance_pct": 0.7,
        }]
        result = summarize(rows, "independent_counterfactual", "breakout")
        self.assertEqual(result["lane"], "independent_counterfactual")
        self.assertEqual(result["net_expectancy"], 8.5)
        self.assertEqual(result["funding_contribution"], -0.5)
        self.assertEqual(result["sample_warning"], "SMALL_SAMPLE")
        self.assertEqual(result["gap_stop_exit_count"], 1)
        self.assertEqual(result["stop_budget_breach_count"], 1)
        self.assertEqual(result["stop_distance_pct_mean"], 0.7)
        self.assertIn('"take_profit":1', result["exit_reasons"])

    def test_fixed_slice_bins_have_predeclared_boundaries(self) -> None:
        self.assertEqual(_band(0.55, ((0.0, 0.55, "a"), (0.55, 1.0, "b"))), "a")
        self.assertEqual(_band(0.550001, ((0.0, 0.55, "a"), (0.55, 1.0, "b"))), "b")

    def test_predictive_results_are_signal_label_lane_and_confidence_sliced(self) -> None:
        row = {
            "signal_id": "1", "timestamp": "2020-02-01T00:00:00+00:00", "strategy": "momentum",
            "confidence": "0.8", "reference_price": "100", "stop_price": "99", "direction": "long",
            "regime": "bull_trend", "ensemble_selected": "True", "features": '{"momentum_15m":0.02}',
            "forward_outcomes": '{"1":{"status":"complete","timestamp":"2020-02-01T00:15:00+00:00","direction_adjusted_return_pct":0.01,"mfe_pct":0.02,"mae_pct":-0.01}}',
        }
        rows = _predictive_rows([row])
        confidence = next(item for item in rows if item["dimension"] == "confidence_band")
        self.assertEqual(confidence["lane"], "signal_label_predictive")
        self.assertEqual(confidence["slice_value"], "(.70,.85]")
        self.assertEqual(confidence["sample_count"], 1)
        self.assertEqual(confidence["mean_direction_adjusted_return_pct"], 1.0)
        self.assertEqual(confidence["mean_mfe_pct"], 2.0)
        self.assertIn("not executable", confidence["observation_unit"])

    def test_split_audit_marks_existing_windows_as_observed_and_purges_crossing_labels(self) -> None:
        signal = {
            "signal_id": "1", "timestamp": "2022-12-31T23:45:00+00:00",
            "forward_outcomes": '{"1":{"status":"complete","timestamp":"2023-01-01T00:00:00+00:00"}}',
        }
        rows = _split_audit([signal], {"independent_counterfactual": []})
        edge = next(row for row in rows if row["window"] == "2020_2022" and row["lane"] == "signal_label_predictive" and row["horizon_candles"] == 1)
        self.assertEqual(edge["purged_cross_boundary_labels"], 1)
        self.assertEqual(edge["eligible_complete_labels"], 0)
        self.assertTrue(edge["holdout_status"].startswith("previously_observed"))
        self.assertEqual(rows[-1]["holdout_status"], "pending; no unseen data supplied")

    def test_chronological_predictive_slice_excludes_labels_crossing_window_end(self) -> None:
        signal = {"signal_id": "1", "timestamp": "2022-12-31T23:45:00+00:00", "strategy": "breakout",
            "confidence": "0.7", "reference_price": "100", "stop_price": "99", "direction": "long",
            "regime": "range", "ensemble_selected": "True", "features": "{}",
            "forward_outcomes": '{"1":{"status":"complete","timestamp":"2023-01-01T00:00:00+00:00","direction_adjusted_return_pct":0.01,"mfe_pct":0.02,"mae_pct":-0.01}}'}
        rows = _predictive_rows([signal])
        self.assertFalse(any(row["dimension"] == "chronological_window" for row in rows))

    def test_moving_block_bootstrap_is_seeded_and_warns_on_few_trade_weeks(self) -> None:
        rows = [{"entry_week": "2020-W01", "net_pnl": 2.0}, {"entry_week": "2020-W01", "net_pnl": -1.0}]
        first = _moving_block_bootstrap(rows, "executable_portfolio", "trend_following")
        second = _moving_block_bootstrap(rows, "executable_portfolio", "trend_following")
        self.assertEqual(first, second)
        self.assertEqual(first["sample_warning"], "SMALL_SAMPLE")
        self.assertEqual(first["occupied_weeks"], 1)

    def test_wilson_interval_is_bounded(self) -> None:
        lower, upper = wilson_interval(8, 10)
        self.assertLess(lower, 0.8)
        self.assertGreater(upper, 0.8)
        self.assertIsNone(wilson_interval(0, 0)[0])

    def test_csv_boolean_false_is_not_counted_as_a_true_stop_flag(self) -> None:
        self.assertFalse(_truth("False"))
        self.assertFalse(_truth("0"))
        self.assertTrue(_truth("True"))

    def test_after_cost_trade_slices_include_confidence_regime_and_period(self) -> None:
        row = {"strategy": "breakout", "decision_regime": "bull_trend", "direction": "long",
            "signal_window": "2023_2024", "entry_week": "2023-W01", "entry_month": "2023-01",
            "net_pnl": -4.0, "gross_pnl": 0.0, "execution_gross_pnl": -2.0, "fees": 1.0,
            "slippage": 1.0, "funding_contribution": 0.0, "holding_hours": 1.0,
            "mfe_pct": 0.5, "mae_pct": -0.5, "exit_reason": "stop_loss", "confidence": 0.8,
            "reference_price": 100.0, "stop_price": 99.0,
            "signal_characteristics_json": '{"ensemble_selected":"True","features":{"momentum_15m":0.02},"stop_distance_pct":0.01}'}
        rows = _trade_slice_rows({"independent_counterfactual": [row]})
        confidence = next(x for x in rows if x["dimension"] == "confidence_band")
        self.assertEqual(confidence["slice_value"], "(.70,.85]")
        self.assertEqual(confidence["net_pnl"], -4.0)
        self.assertEqual(confidence["sample_warning"], "SMALL_SAMPLE")

    def test_weekly_monthly_panels_retain_zero_trade_periods(self) -> None:
        from datetime import datetime, timezone
        weeks, months = _weekly_monthly({"executable_portfolio": []},
            datetime(2020, 1, 1, tzinfo=timezone.utc), datetime(2020, 1, 31, tzinfo=timezone.utc))
        self.assertTrue(weeks)
        self.assertTrue(months)
        self.assertTrue(all(row["no_trade_period"] for row in weeks + months))
        self.assertEqual({row["sample_warning"] for row in weeks + months}, {"NO_TRADES"})

    def test_trade_clustering_counts_overlap_and_peak_concurrency(self) -> None:
        rows = [
            {"strategy": "breakout", "entry_timestamp": "2020-01-01T00:00:00+00:00", "exit_timestamp": "2020-01-01T01:00:00+00:00", "entry_week": "2020-W01"},
            {"strategy": "breakout", "entry_timestamp": "2020-01-01T00:30:00+00:00", "exit_timestamp": "2020-01-01T02:00:00+00:00", "entry_week": "2020-W01"},
            {"strategy": "breakout", "entry_timestamp": "2020-01-01T02:00:00+00:00", "exit_timestamp": "2020-01-01T03:00:00+00:00", "entry_week": "2020-W01"},
        ]
        result = _clustering_rows({"independent_counterfactual": rows})
        all_rows = next(row for row in result if row["strategy"] == "all_strategies")
        self.assertEqual(all_rows["overlap_trade_count"], 2)
        self.assertEqual(all_rows["maximum_concurrent_trades"], 2)

    def test_entry_timing_funding_uses_phase4_open_gap_convention_for_both_sides(self) -> None:
        origin = datetime(2024, 1, 1, tzinfo=timezone.utc)
        metadata = {"configuration": {"fee_bps": 0, "slippage_bps": 0,
            "funding_bps_8h": 800, "max_participation_rate": 1}}

        scenarios = (
            ("opening_gap_stop", 60, (90, 92, 80, 85), (110, 120, 108, 115), -10.925, -8.925),
            ("survives_open", 15, (99, 102, 98, 100), (101, 102, 98, 100), -0.5, 0.5),
            ("intrabar_stop", 15, (100, 101, 94, 96), (100, 106, 99, 104), -5.49, -4.49),
        )
        for name, gap_minutes, long_ohlc, short_ohlc, long_expected, short_expected in scenarios:
            for direction, stop, ohlc, expected in (("long", 95, long_ohlc, long_expected),
                    ("short", 105, short_ohlc, short_expected)):
                with self.subTest(scenario=name, direction=direction):
                    base = Candle(origin, 100, 101, 99, 100, 10)
                    entry = Candle(origin + timedelta(minutes=15), 100, 101, 99, 100, 10)
                    exit_bar = Candle(origin + timedelta(minutes=15 + gap_minutes), *ohlc, 10)
                    candles = [base, entry, exit_bar]
                    times = {candle.timestamp.isoformat(): index for index, candle in enumerate(candles)}
                    row = {"strategy": "trend_following", "direction": direction,
                        "entry_timestamp": base.timestamp.isoformat(),
                        "exit_timestamp": exit_bar.timestamp.isoformat(), "quantity": 1.0,
                        "stop_price": stop, "target_price": ""}

                    result = _entry_timing([row], candles, times, metadata)
                    delayed = next(item for item in result if item["strategy"] == "trend_following"
                        and item["delay_bars"] == 1)
                    self.assertEqual(delayed["trade_count"], 1)
                    self.assertAlmostEqual(delayed["net_pnl"], expected, places=10)


if __name__ == "__main__":
    unittest.main()
