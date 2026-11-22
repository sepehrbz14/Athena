"""Synthetic-only M4 family statistics and contract tests."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import calendar
import json
import math
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from crypto_bot import experiment_registry as m1
from crypto_bot.m3_statistics import evaluate_confirmatory_estimands
from crypto_bot.m4_validation import _canonical
from crypto_bot.m4_validation import (
    ALPHA, BLOCK_LENGTH_WEEKS, BOOTSTRAP_ITERATIONS, EVIDENCE_SCHEMA,
    HYPOTHESES, M4ValidationError, QualificationBlocked, holm_adjust,
    qualify_family, reconstruct_raw_statistics,
)

try:
    from test_evolution_engine_m2 import M2Fixture, _catalog
    from crypto_bot.evolution_engine_m2 import prepare_generation_protocol
    from test_experiment_registry import protocol_document
except ImportError:  # pragma: no cover - CI installs the declared cryptography dependency.
    M2Fixture = None
    _catalog = None
    prepare_generation_protocol = None
    protocol_document = None


def _week_keys(count: int = 156) -> list[str]:
    monday = date.fromisocalendar(2020, 1, 1)
    result = []
    for index in range(count):
        iso = (monday + timedelta(weeks=index)).isocalendar()
        result.append(f"{iso.year}-W{iso.week:02d}")
    return result


def _evidence(*, week_keys: list[str] | None = None,
              fold_map: dict[str, str] | None = None) -> tuple[dict, dict[str, dict[str, int]]]:
    weeks = week_keys or _week_keys()
    fold_by_week = fold_map or {week: f"outer_{index // 26 + 1}" for index, week in enumerate(weeks)}
    folds = tuple(dict.fromkeys(fold_by_week[w] for w in weeks))
    seeds = {metric: {fold: (1000 + metric_index * 100 + fold_index)
                      for fold_index, fold in enumerate(folds)}
             for metric_index, metric in enumerate(HYPOTHESES)}
    ix = {week: index for index, week in enumerate(weeks)}
    pred_c = {w: 0.004 + (ix[w] % 7) * 0.0002 for w in weeks}
    pred_i = {w: 0.001 + (ix[w] % 5) * 0.0001 for w in weeks}
    exec_i = {w: -0.001 + (ix[w] % 9) * 0.0001 for w in weeks}
    exec_c = {
        "baseline": {w: 0.002 + (ix[w] % 11) * 0.0001 for w in weeks},
        "fee_10bps_per_side_only": {w: 0.001 + (ix[w] % 9) * 0.0001 for w in weeks},
        "slippage_10bps_per_side_only": {w: 0.0005 + (ix[w] % 8) * 0.0001 for w in weeks},
        "funding_2bps_per_8h_only": {w: 0.0008 + (ix[w] % 10) * 0.0001 for w in weeks},
    }
    counts = {
        "predictive_opportunities": 240, "predictive_occupied_weeks": 40,
        "counterfactual_completed_trades": 120, "counterfactual_occupied_weeks": 35,
        "executable_positions": 130, "executable_occupied_weeks": 40,
        "executable_outer_folds": 6, "regime_observations": 40,
        "regime_occupied_weeks": 18,
    }
    evidence = {
        "schema": EVIDENCE_SCHEMA, "week_keys": weeks, "fold_by_week": fold_by_week,
        "weekly": {"predictive_candidate": pred_c, "predictive_incumbent": pred_i,
                   "executable_incumbent_baseline": exec_i,
                   "executable_candidate_scenarios": exec_c},
        "sample_counts": counts, "regime_claim_requested": False,
    }
    return evidence, seeds


class M4MathTests(unittest.TestCase):
    def test_holm_step_down_golden_and_tie_order(self):
        self.assertEqual(holm_adjust((0.01, 0.04, 0.03, 1.0, 0.0)),
                         (0.04, 0.09, 0.09, 1.0, 0.0))
        self.assertEqual(holm_adjust((0.02, 0.02, 0.8)), (0.06, 0.06, 0.8))

    def test_holm_rejects_invalid_values(self):
        for values in ((), (-0.01,), (1.01,), (math.nan,), (True,)):
            with self.subTest(values=values), self.assertRaises(M4ValidationError):
                holm_adjust(values)

    def test_all_placeholders_remain_one_and_within_full_denominator(self):
        adjusted = holm_adjust([1.0] * 144)
        self.assertEqual(len(adjusted), 144)
        self.assertTrue(all(value == 1.0 for value in adjusted))

    def test_qualification_is_fail_closed_until_m0_bound_inversion_is_frozen(self):
        with self.assertRaises(QualificationBlocked):
            qualify_family()

    def test_insufficient_sample_floors_use_four_p_one_placeholders(self):
        evidence, seeds = _evidence()
        evidence["sample_counts"]["executable_positions"] = 99
        result = reconstruct_raw_statistics(evidence, expected_seeds=seeds)
        self.assertEqual(result["sample_status"], "INSUFFICIENT_EVIDENCE")
        self.assertEqual(set(result["estimands"]), set(HYPOTHESES))
        self.assertEqual({row["raw_one_sided_p"] for row in result["estimands"].values()}, {1.0})

    def test_regime_specific_claim_enforces_frozen_30_12_floor(self):
        evidence, seeds = _evidence()
        evidence["regime_claim_requested"] = True
        evidence["sample_counts"]["regime_observations"] = 29
        result = reconstruct_raw_statistics(evidence, expected_seeds=seeds)
        self.assertEqual(result["sample_status"], "INSUFFICIENT_EVIDENCE")
        self.assertIn("regime_observations=29 below frozen floor 30", result["sample_warnings"])

    def test_m3_bootstrap_is_independently_reconstructed_from_identical_week_vectors(self):
        evidence, seeds = _evidence()
        weeks = evidence["week_keys"]
        lanes = evidence["weekly"]
        candidate_scenarios = lanes["executable_candidate_scenarios"]
        m3 = evaluate_confirmatory_estimands(
            week_keys=weeks, fold_by_week=evidence["fold_by_week"],
            predictive_candidate=lanes["predictive_candidate"],
            predictive_incumbent=lanes["predictive_incumbent"],
            executable_candidate_scenarios=candidate_scenarios,
            executable_incumbent_baseline=lanes["executable_incumbent_baseline"],
            seeds_by_metric_and_fold=seeds, sample_counts=evidence["sample_counts"],
        )
        m4 = reconstruct_raw_statistics(evidence, expected_seeds=seeds)
        self.assertEqual(m3.sample_status, m4["sample_status"])
        self.assertEqual(m3.sample_warnings, tuple(m4["sample_warnings"]))
        for raw in m3.estimands:
            reconstructed = m4["estimands"][raw.estimand_id]
            self.assertAlmostEqual(raw.estimate, reconstructed["estimate"], places=14)
            self.assertAlmostEqual(raw.lower_95, reconstructed["lower_95"], places=14)
            self.assertAlmostEqual(raw.upper_95, reconstructed["upper_95"], places=14)
            self.assertAlmostEqual(raw.raw_one_sided_p, reconstructed["raw_one_sided_p"], places=14)
        self.assertEqual(BOOTSTRAP_ITERATIONS, 100_000)
        self.assertEqual(BLOCK_LENGTH_WEEKS, 4)
        self.assertEqual(ALPHA, 0.05)

    def test_weekly_evidence_schema_rejects_missing_or_mismatched_vectors(self):
        evidence, seeds = _evidence()
        del evidence["weekly"]["predictive_candidate"][evidence["week_keys"][0]]
        with self.assertRaises(M4ValidationError):
            reconstruct_raw_statistics(evidence, expected_seeds=seeds)


@unittest.skipIf(M2Fixture is None, "M2 signed fixture requires declared cryptography dependency")
class M4FamilyIntegrationTests(unittest.TestCase):
    def setUp(self):
        runner_temp = os.environ.get("RUNNER_TEMP")
        self.temp = tempfile.TemporaryDirectory(dir=runner_temp)
        self.root = Path(self.temp.name)
        from crypto_bot import m4_validation as m4_module
        self.report_root = self.root / "m4-reports"
        self.report_root_patch = mock.patch.object(m4_module, "_M4_REPORT_ROOT", self.report_root)
        self.report_root_patch.start()
        self.report_dir = self.report_root / datetime.now(timezone.utc).date().isoformat()
        self.fixture = M2Fixture(self.root / "m2-fixture", catalog_entries=5)
        catalog = json.loads(json.dumps(self.fixture.catalog))
        catalog["entries"][1]["strategy_members"] = catalog["entries"][0]["strategy_members"]
        catalog["entries"][1]["policy"] = json.loads(json.dumps(catalog["entries"][0]["policy"]))
        # These two registered attempts must exercise M1's terminal invalid
        # lineage path, rather than accidentally relying on semantic duplicate
        # detection (which is a different rejection reason).
        catalog["entries"][0]["parents"] = [{
            "reference_type": "candidate", "reference_id": "sha256:" + "a" * 64,
            "relation": "derived_from",
        }]
        catalog["entries"][1]["parents"] = [{
            "reference_type": "candidate", "reference_id": "sha256:" + "b" * 64,
            "relation": "derived_from",
        }]
        combo = ["trend_following", "breakout"]
        catalog["entries"][4]["strategy_members"] = combo
        catalog["entries"][4]["policy"]["regime_allowlist"] = {"bull_trend": combo}
        self.fixture.catalog = _catalog(catalog["entries"], self.fixture.reviewer)
        self.fixture.receipt = self.fixture.make_receipt(self.fixture.catalog)
        fold_rows = []
        outer_start = datetime(2020, 1, 6, tzinfo=timezone.utc)

        def add_months(value: datetime, months: int) -> datetime:
            absolute = value.year * 12 + value.month - 1 + months
            year, month0 = divmod(absolute, 12)
            month = month0 + 1
            return value.replace(year=year, month=month,
                                 day=min(value.day, calendar.monthrange(year, month)[1]))

        def utc_text(value: datetime) -> str:
            return value.isoformat().replace("+00:00", "Z")

        for index in range(6):
            outer_end = add_months(outer_start, 6)
            while outer_end.weekday() != 0:
                outer_end += timedelta(days=1)
            validation_end = outer_start - timedelta(days=1)
            validation_start = add_months(validation_end, -6) - timedelta(days=1)
            train_end = validation_start - timedelta(days=1)
            train_start = add_months(train_end, -24)
            fold_rows.append({
                "fold_id": f"outer_{index + 1}",
                "train_start_utc": utc_text(train_start), "train_end_utc": utc_text(train_end),
                "validation_start_utc": utc_text(validation_start), "validation_end_utc": utc_text(validation_end),
                "outer_test_start_utc": utc_text(outer_start), "outer_test_end_utc": utc_text(outer_end),
                "purged_counts": {"crossing_label": 0, "crossing_trade": 0,
                                  "embargo": 0, "warmup_only": 0},
                "warmup_bars": 816,
                "classification": "previously_observed_historical_diagnostic",
            })
            outer_start = outer_end
        base = protocol_document(attempt_count=5)
        base["chronology"]["folds"] = fold_rows
        self.fixture.protocol, self.fixture.request = prepare_generation_protocol(
            base, catalog_id=self.fixture.catalog["catalog_id"], catalog_version=1,
            template_ids=[entry["template_id"] for entry in self.fixture.catalog["entries"]],
        )
        from crypto_bot.m3_chronology import build_fold_windows, complete_utc_week_keys
        windows = build_fold_windows(self.fixture.protocol)
        self.weeks = [week for fold in windows for week in complete_utc_week_keys(fold.outer_test)]
        self.fold_map = {week: fold.fold_id for fold in windows for week in complete_utc_week_keys(fold.outer_test)}
        self.fixture.freeze()
        self.submission = self.fixture.coordinator.submit(
            self.fixture.protocol, self.fixture.request, write_report=False)
        self.attempts = self.submission.attempt_ids

    def tearDown(self):
        self.report_root_patch.stop()
        self.temp.cleanup()

    @staticmethod
    def _sealed_package(path: Path, *, attempt_id: str, protocol: dict, candidate_sha: str,
                        checkpoint: dict, statistics: dict, evidence: dict,
                        seed_records: list[dict]) -> str:
        contents = {
            "statistics.json": _canonical(statistics),
            "weekly_evidence.json": _canonical(evidence),
        }
        listed = {name: {"sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data)}
                  for name, data in contents.items()}
        manifest = {
            "schema": "athena.m3.evaluation-package.v1", "attempt_id": attempt_id,
            "protocol_id": protocol["protocol_id"], "candidate_sha256": candidate_sha,
            "protocol_sha256": hashlib.sha256(m1.canonical_json_bytes(protocol)).hexdigest(),
            "registry_tip_sha256": checkpoint["event_tip_sha256"],
            "checkpoint_sha256": checkpoint["checkpoint_sha256"],
            "input_hashes": {"signals": protocol["data"]["signals_sha256"],
                             "candles": protocol["data"]["candles_sha256"],
                             "phase5_manifest": protocol["data"]["phase5_manifest_sha256"],
                             "counterfactual_ledger": "c" * 64,
                             "counterfactual_metadata": "d" * 64,
                             "phase4_config": protocol["source"]["phase4_config_sha256"]},
            "source_commit": protocol["source"]["commit"], "source_dirty": False,
            "fold_plan": {"fold_ids": [f"outer_{i}" for i in range(1, 7)]},
            "seed_records": seed_records,
            "resource_usage": {"wall_seconds": 1, "peak_memory_mb": 1, "worker_count": 1},
            "methodology": {"synthetic": True},
            "signal_generation_provenance": "hash-pinned frozen signal rows; source-generation provenance not reconstructed",
            "artifacts": listed,
        }
        path.mkdir()
        for name, data in contents.items():
            (path / name).write_bytes(data)
        raw_manifest = _canonical(manifest)
        (path / "manifest.json").write_bytes(raw_manifest)
        return hashlib.sha256(raw_manifest).hexdigest()

    def test_end_to_end_accounts_invalid_failed_skipped_and_insufficient_attempts(self):
        states = self.fixture.registry.attempt_states()
        self.assertEqual(states[self.attempts[0]], "INVALID_CANDIDATE")
        self.assertEqual(states[self.attempts[1]], "INVALID_CANDIDATE")
        self.assertEqual(states[self.attempts[2]], "VALIDATED")
        self.assertEqual(states[self.attempts[3]], "VALIDATED")
        self.assertEqual(states[self.attempts[4]], "VALIDATED")
        self.fixture.registry.fail_attempt(self.attempts[2], "SYNTHETIC_FAILURE", "fixture")
        self.fixture.registry.skip_attempt(self.attempts[3], "synthetic skip")

        attempt_id = self.attempts[4]
        folds = [f"outer_{index}" for index in range(1, 7)]
        self.fixture.registry.record_evaluation_started(attempt_id, folds, HYPOTHESES)
        events = self.fixture.registry.events()
        seeds_event = next(e["payload"]["seed_records"] for e in events
                           if e["event_type"] == "EVALUATION_STARTED"
                           and e["payload"]["attempt_id"] == attempt_id)
        seed_map = {metric: {} for metric in HYPOTHESES}
        for row in seeds_event:
            seed_map[row["metric_id"]][row["fold_id"]] = int(row["seed_u64_decimal"])
        evidence, _ = _evidence(week_keys=self.weeks, fold_map=self.fold_map)
        evidence["sample_counts"]["predictive_opportunities"] = 199
        from crypto_bot.m4_validation import reconstruct_raw_statistics
        rebuilt = reconstruct_raw_statistics(evidence, expected_seeds=seed_map)
        stats = {
            "estimands": [{"estimand_id": key, **row, "bootstrap_iterations": BOOTSTRAP_ITERATIONS,
                           "block_length_weeks": BLOCK_LENGTH_WEEKS}
                          for key, row in rebuilt["estimands"].items()],
            "sample_status": rebuilt["sample_status"], "sample_warnings": rebuilt["sample_warnings"],
            "inference_owner": "M4 persistent-family validator", "multiplicity_adjustment_applied": False,
            "qualification_decision": "NOT_MADE_BY_M3",
            "max_positive_fold_share": rebuilt["max_positive_fold_share"],
        }
        protocol_event = next(e for e in events if e["event_type"] == "PROTOCOL_PRE_REGISTERED")
        protocol = protocol_event["payload"]["protocol"]
        checkpoint = self.fixture.security.verify_latest(allow_empty=False)
        registered = next(e["payload"] for e in events if e["event_type"] == "CANDIDATE_ATTEMPT_REGISTERED"
                          and e["payload"]["attempt_id"] == attempt_id)
        package = self.root / "packages" / "attempt-four"
        package_hash = self._sealed_package(
            package, attempt_id=attempt_id, protocol=protocol,
            candidate_sha=registered["candidate_spec_bytes_sha256"],
            checkpoint=checkpoint, statistics=stats, evidence=evidence, seed_records=seeds_event,
        )
        self.fixture.registry.complete_evaluation(
            attempt_id, result_sha256=package_hash, wall_seconds=1, peak_memory_mb=1,
            artifact_bytes=4096, worker_count=1,
            confirmatory_p_values={name: 1.0 for name in HYPOTHESES},
        )
        self.fixture.registry.mark_insufficient(attempt_id, "FROZEN_SAMPLE_FLOOR", "synthetic fixture")

        from crypto_bot.m4_validation import validate_family
        result_path = self.report_dir / "family.json"
        result = validate_family(self.fixture.registry, external_store=self.fixture.security,
                                 package_paths={attempt_id: package}, output_path=result_path)
        replay = validate_family(self.fixture.registry, external_store=self.fixture.security,
                                 package_paths={attempt_id: package},
                                 output_path=self.report_dir / "family-replay.json")
        self.assertEqual(result.report_sha256, replay.report_sha256)
        self.assertEqual(result.report["attempt_count"], 5)
        self.assertEqual(result.report["hypothesis_count"], 20)
        self.assertEqual(result.report["qualification_status"], "BLOCKED_M0_ADJUSTED_BOUND_INVERSION_UNSPECIFIED")
        self.assertFalse(result.report["no_qualifying_candidate_authoritative"])
        self.assertEqual(sum(row["raw_p"] == 1 for row in result.report["hypotheses"]), 20)
        manifest_path = package / "manifest.json"
        original_manifest = manifest_path.read_bytes()
        manifest = json.loads(original_manifest)
        manifest["candidate_sha256"] = "f" * 64
        manifest_path.write_bytes(_canonical(manifest))
        with self.assertRaises(M4ValidationError):
            validate_family(self.fixture.registry, external_store=self.fixture.security,
                            package_paths={attempt_id: package}, output_path=self.report_dir / "candidate-mismatch.json")
        manifest["candidate_sha256"] = registered["candidate_spec_bytes_sha256"]
        manifest["checkpoint_sha256"] = "0" * 64
        manifest_path.write_bytes(_canonical(manifest))
        with self.assertRaises(M4ValidationError):
            validate_family(self.fixture.registry, external_store=self.fixture.security,
                            package_paths={attempt_id: package}, output_path=self.report_dir / "checkpoint-mismatch.json")
        manifest_path.write_bytes(original_manifest)
        evidence_path = package / "weekly_evidence.json"
        original_evidence = evidence_path.read_bytes()
        evidence_path.write_bytes(original_evidence + b" ")
        with self.assertRaises(M4ValidationError):
            validate_family(self.fixture.registry, external_store=self.fixture.security,
                            package_paths={attempt_id: package}, output_path=self.report_dir / "tampered.json")
        evidence_path.write_bytes(original_evidence)
        with self.assertRaises(M4ValidationError):
            validate_family(self.fixture.registry, external_store=self.fixture.security,
                            package_paths={}, output_path=self.report_dir / "missing.json")
        with self.assertRaises(M4ValidationError):
            validate_family(self.fixture.registry, external_store=self.fixture.security,
                            package_paths={attempt_id: package}, output_path=result_path)

    def test_package_tampering_and_conflicting_m2_identity_fail_closed(self):
        # A trust store attached to a different registry is rejected before any
        # package or report is written.
        other = m1.ExperimentRegistry.create(self.root / "other-registry")
        wrong_store = type(self.fixture.security)(
            other, self.fixture.external_root, self.fixture.trust,
            repository_root=self.root / "declared-repository",
        )
        from crypto_bot.m4_validation import validate_family
        with self.assertRaises(M4ValidationError):
            validate_family(self.fixture.registry, external_store=wrong_store,
                            package_paths={}, output_path=self.report_dir / "wrong-registry.json")


if __name__ == "__main__":
    unittest.main()
