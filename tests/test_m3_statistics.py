from __future__ import annotations

import unittest

from crypto_bot.m3_statistics import (
    BOOTSTRAP_ITERATIONS, BLOCK_LENGTH_WEEKS, StatisticalEvidenceError,
    evaluate_confirmatory_estimands, sample_gate,
    seeds_from_m1_started_event,
)


def fixture():
    weeks = tuple(f"2025-W{i:02d}" for i in range(1, 31))
    fold_by_week = {week: f"fold-{(i//5)+1}" for i, week in enumerate(weeks)}
    counts = {"predictive_opportunities": 200, "predictive_occupied_weeks": 30,
              "counterfactual_completed_trades": 100, "counterfactual_occupied_weeks": 30,
              "executable_positions": 100, "executable_occupied_weeks": 30,
              "executable_outer_folds": 6, "regime_observations": 30,
              "regime_occupied_weeks": 12}
    return weeks, fold_by_week, counts


class M3StatisticsTests(unittest.TestCase):
    def test_m1_seed_event_roundtrips_decimal_u64_and_rejects_missing_seed(self):
        metrics = ("m1", "m2")
        rows = [{"fold_id": fold, "metric_id": metric,
                 "seed_u64_decimal": str((2**64)-1 if fold == "f1" and metric == "m1" else 7)}
                for fold in ("f1", "f2") for metric in metrics]
        event = {"event_type": "EVALUATION_STARTED", "protocol_id": "p",
                 "payload": {"attempt_id": "a", "seed_records": rows}}
        parsed = seeds_from_m1_started_event(event, attempt_id="a", protocol_id="p",
            fold_ids=("f1", "f2"), metric_ids=metrics)
        self.assertEqual(parsed["m1"]["f1"], 2**64-1)
        with self.assertRaises(StatisticalEvidenceError):
            seeds_from_m1_started_event({**event, "payload": {"attempt_id": "a", "seed_records": rows[:-1]}},
                attempt_id="a", protocol_id="p", fold_ids=("f1", "f2"), metric_ids=metrics)

    def test_regime_floor_applies_only_to_regime_specific_claims(self):
        _, _, counts = fixture()
        counts["regime_observations"] = 4
        counts["regime_occupied_weeks"] = 2
        self.assertEqual(sample_gate(counts)[0], "SAMPLE_FLOORS_MET")
        status, warnings = sample_gate(counts, regime_claim_requested=True)
        self.assertEqual(status, "INSUFFICIENT_EVIDENCE")
        self.assertEqual(len(warnings), 2)

    def test_frozen_bootstrap_is_reproducible_paired_and_unadjusted(self):
        weeks, folds, counts = fixture()
        prediction = {week: 0.01 for week in weeks}
        incumbent = {week: 0.0 for week in weeks}
        scenarios = {name: {week: value for week in weeks} for name, value in (
            ("baseline", 0.01), ("fee_10bps_per_side_only", 0.008),
            ("slippage_10bps_per_side_only", 0.007), ("funding_2bps_per_8h_only", 0.006))}
        metrics = ("predictive_absolute_weekly_score", "predictive_paired_weekly_delta",
                   "robust_executable_absolute_weekly_return", "executable_paired_weekly_delta")
        seeds = {metric: {f"fold-{i}": i*100 + j for i in range(1, 7)}
                 for j, metric in enumerate(metrics, 1)}
        kwargs = dict(week_keys=weeks, fold_by_week=folds, predictive_candidate=prediction,
            predictive_incumbent=incumbent, executable_candidate_scenarios=scenarios,
            executable_incumbent_baseline=incumbent, seeds_by_metric_and_fold=seeds, sample_counts=counts)
        first = evaluate_confirmatory_estimands(**kwargs)
        second = evaluate_confirmatory_estimands(**kwargs)
        self.assertEqual(first, second)
        self.assertEqual(len(first.estimands), 4)
        self.assertTrue(all(item.status == "RAW_UNADJUSTED" for item in first.estimands))
        self.assertTrue(all(item.bootstrap_iterations == BOOTSTRAP_ITERATIONS for item in first.estimands))
        self.assertTrue(all(item.block_length_weeks == BLOCK_LENGTH_WEEKS for item in first.estimands))
        self.assertFalse(first.multiplicity_adjustment_applied)
        self.assertEqual(first.qualification_decision, "NOT_MADE_BY_M3")
        self.assertEqual(first.estimands[2].estimate, 0.006)
        self.assertAlmostEqual(first.max_positive_fold_share, 1/6)

    def test_insufficient_samples_keep_four_conservative_p_one_rows(self):
        weeks, folds, counts = fixture()
        counts["executable_positions"] = 99
        zeros = {week: 0.0 for week in weeks}
        scenarios = {name: zeros for name in ("baseline", "fee_10bps_per_side_only",
            "slippage_10bps_per_side_only", "funding_2bps_per_8h_only")}
        metrics = ("predictive_absolute_weekly_score", "predictive_paired_weekly_delta",
                   "robust_executable_absolute_weekly_return", "executable_paired_weekly_delta")
        seeds = {metric: {f"fold-{i}": i for i in range(1, 7)} for metric in metrics}
        result = evaluate_confirmatory_estimands(week_keys=weeks, fold_by_week=folds,
            predictive_candidate=zeros, predictive_incumbent=zeros,
            executable_candidate_scenarios=scenarios, executable_incumbent_baseline=zeros,
            seeds_by_metric_and_fold=seeds, sample_counts=counts)
        self.assertEqual(result.sample_status, "INSUFFICIENT_EVIDENCE")
        self.assertEqual([item.raw_one_sided_p for item in result.estimands], [1.0]*4)
        self.assertTrue(all(item.estimate is None for item in result.estimands))
        self.assertIsNone(result.max_positive_fold_share)

    def test_all_lanes_require_identical_week_keys_and_registered_seeds(self):
        weeks, folds, counts = fixture()
        zeros = {week: 0.0 for week in weeks}
        scenarios = {name: zeros for name in ("baseline", "fee_10bps_per_side_only",
            "slippage_10bps_per_side_only", "funding_2bps_per_8h_only")}
        with self.assertRaisesRegex(StatisticalEvidenceError, "identical UTC week keys"):
            evaluate_confirmatory_estimands(week_keys=weeks, fold_by_week=folds,
                predictive_candidate={**zeros, "extra": 1.0}, predictive_incumbent=zeros,
                executable_candidate_scenarios=scenarios, executable_incumbent_baseline=zeros,
                seeds_by_metric_and_fold={}, sample_counts=counts)


if __name__ == "__main__":
    unittest.main()
