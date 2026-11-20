from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from crypto_bot.m3_lifecycle import (PackageError, begin_authorized_evaluation,
    publish_and_complete, publish_package, recover_attempt, verify_package)
from crypto_bot.m3_isolation import VerifiedEvaluationPermit
from crypto_bot.m3_admission import AdmittedAttempt, PinnedInput
from pathlib import Path


class FakeRegistry:
    def __init__(self, state="RUNNING"):
        self.state = state
        self.failures = []
        self.completions = []
        self.started = []

    def attempt_states(self):
        return {"attempt-1": self.state}

    def fail_attempt(self, attempt_id, reason_code, detail=""):
        self.failures.append((attempt_id, reason_code, detail))
        self.state = "TECHNICAL_EVALUATION_FAILURE"

    def complete_evaluation(self, attempt_id, **kwargs):
        self.completions.append((attempt_id, kwargs))
        self.state = "COMPLETED"
        return "COMPLETED"

    def mark_insufficient(self, attempt_id, reason_code, detail=""):
        self.state = "INSUFFICIENT_EVIDENCE"

    def record_evaluation_started(self, attempt_id, fold_ids, metric_ids):
        self.started.append((attempt_id, fold_ids, metric_ids))
        self.state = "RUNNING"


PROVENANCE = dict(attempt_id="attempt-1", protocol_id="protocol-1",
    candidate_sha256="c"*64, protocol_sha256="p"*64,
    registry_tip_sha256="r"*64, checkpoint_sha256="k"*64,
    input_hashes={"signals": "s"*64}, source_commit="commit", source_dirty=False,
    fold_plan={"folds": ["f1"]}, seed_records=[], resource_usage={"wall_seconds": 2,
        "peak_memory_mb": 64, "worker_count": 1}, methodology={"name": "synthetic"})


class M3LifecycleTests(unittest.TestCase):
    def _package(self, root):
        stats = {"multiplicity_adjustment_applied": False,
            "qualification_decision": "NOT_MADE_BY_M3", "sample_status": "SAMPLE_FLOORS_MET",
            "sample_warnings": [],
            "estimands": [{"estimand_id": name, "raw_one_sided_p": 0.25, "status": "RAW_UNADJUSTED"}
                for name in ("predictive_absolute_weekly_score", "predictive_paired_weekly_delta",
                    "robust_executable_absolute_weekly_return", "executable_paired_weekly_delta")]}
        return publish_package(root, **PROVENANCE,
            artifacts={"evidence.json": b'{"synthetic":true}', "weekly.csv": b"week,value\n",
                "statistics.json": json.dumps(stats, sort_keys=True, separators=(",", ":")).encode()})

    def _recover(self, registry, path):
        return recover_attempt(registry, "attempt-1", path,
            expected_protocol_id="protocol-1", expected_candidate_sha256="c"*64,
            expected_protocol_sha256="p"*64, expected_registry_tip_sha256="r"*64,
            expected_checkpoint_sha256="k"*64)

    def test_seal_is_hash_verified_and_cannot_be_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            path, digest = self._package(temp)
            manifest, verified = verify_package(path, expected_attempt_id="attempt-1")
            self.assertEqual(digest, verified)
            self.assertEqual(manifest["signal_generation_provenance"],
                "hash-pinned frozen signal rows; source-generation provenance not reconstructed")
            with self.assertRaises(PackageError):
                self._package(temp)

    def test_package_output_limit_is_enforced_before_publication(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as temp:
            with patch("crypto_bot.m3_lifecycle.FROZEN_MAX_PACKAGE_BYTES", 4):
                with self.assertRaisesRegex(PackageError, "512 MiB"):
                    publish_package(temp, **PROVENANCE, artifacts={"too-large.bin": b"12345"})
            self.assertFalse(list(Path(temp).rglob("manifest.json")))

    def test_resource_limits_are_frozen_and_recorded_for_recovery(self):
        with tempfile.TemporaryDirectory() as temp:
            args = dict(PROVENANCE)
            args["resource_usage"] = {"wall_seconds": 1801, "peak_memory_mb": 64, "worker_count": 1}
            with self.assertRaisesRegex(PackageError, "resource usage"):
                publish_package(temp, **args, artifacts={"evidence.json": b"{}"})
            args["resource_usage"] = {"wall_seconds": 2, "peak_memory_mb": 64, "worker_count": 1}
            path, _ = publish_package(temp, **args, artifacts={"evidence.json": b"{}"})
            manifest, _ = verify_package(path)
            self.assertEqual(manifest["resource_usage"]["worker_count"], 1)

    def test_recovery_finalizes_the_existing_package_once(self):
        with tempfile.TemporaryDirectory() as temp:
            path, digest = self._package(temp)
            registry = FakeRegistry()
            result = self._recover(registry, path)
            self.assertEqual(result.action, "FINALIZED_EXISTING_PACKAGE")
            self.assertEqual(result.package_sha256, digest)
            self.assertEqual(registry.state, "COMPLETED")
            self.assertEqual(len(registry.completions), 1)
            self.assertEqual(set(registry.completions[0][1]["confirmatory_p_values"]), {
                "predictive_absolute_weekly_score", "predictive_paired_weekly_delta",
                "robust_executable_absolute_weekly_return", "executable_paired_weekly_delta"})
            again = self._recover(registry, path)
            self.assertEqual(again.action, "ALREADY_TERMINAL_VERIFIED")
            self.assertEqual(len(registry.completions), 1)

    def test_missing_or_corrupt_package_becomes_terminal_failure(self):
        registry = FakeRegistry()
        result = self._recover(registry, None)
        self.assertEqual(result.state, "TECHNICAL_EVALUATION_FAILURE")
        self.assertEqual(len(registry.failures), 1)
        with tempfile.TemporaryDirectory() as temp:
            path, _ = self._package(temp)
            (path / "evidence.json").write_bytes(b"tampered")
            registry = FakeRegistry()
            result = self._recover(registry, path)
            self.assertEqual(result.action, "FAILED_INVALID_PACKAGE")
            self.assertEqual(registry.state, "TECHNICAL_EVALUATION_FAILURE")
            self.assertFalse(registry.completions)

    def test_cannot_recover_attempt_that_never_started(self):
        registry = FakeRegistry("VALIDATED")
        with self.assertRaisesRegex(PackageError, "RUNNING"):
            self._recover(registry, None)
        self.assertFalse(registry.failures)

    def test_package_provenance_must_match_registered_attempt(self):
        with tempfile.TemporaryDirectory() as temp:
            path, _ = self._package(temp)
            registry = FakeRegistry()
            result = recover_attempt(registry, "attempt-1", path,
                expected_protocol_id="protocol-1", expected_candidate_sha256="x"*64,
                expected_protocol_sha256="p"*64, expected_registry_tip_sha256="r"*64,
                expected_checkpoint_sha256="k"*64)
            self.assertEqual(result.action, "FAILED_INVALID_PACKAGE")
            self.assertEqual(registry.state, "TECHNICAL_EVALUATION_FAILURE")
            self.assertFalse(registry.completions)

    def test_statistics_artifact_preserves_all_raw_m0_p_values_without_correction(self):
        from crypto_bot.m3_lifecycle import _raw_p_values
        with tempfile.TemporaryDirectory() as temp:
            path, _ = self._package(temp)
            p_values = _raw_p_values(path)
            self.assertEqual(set(p_values), {
                "predictive_absolute_weekly_score", "predictive_paired_weekly_delta",
                "robust_executable_absolute_weekly_return", "executable_paired_weekly_delta"})
            self.assertTrue(all(value == 0.25 for value in p_values.values()))

    def test_package_completion_passes_sealed_raw_p_values_to_m1(self):
        with tempfile.TemporaryDirectory() as temp:
            registry = FakeRegistry()
            usage = {"wall_seconds": 2, "peak_memory_mb": 64, "worker_count": 1}
            args = {"root": temp, **PROVENANCE, "resource_usage": usage,
                    "artifacts": {"statistics.json": self._package_statistics()}}
            result = publish_and_complete(registry, package_arguments=args,
                wall_seconds=2, peak_memory_mb=64, artifact_bytes=10000)
            self.assertEqual(result.state, "COMPLETED")
            self.assertEqual(set(registry.completions[0][1]["confirmatory_p_values"].values()), {0.25})

    @staticmethod
    def _package_statistics():
        stats = {"multiplicity_adjustment_applied": False,
            "qualification_decision": "NOT_MADE_BY_M3", "sample_status": "SAMPLE_FLOORS_MET",
            "sample_warnings": [],
            "estimands": [{"estimand_id": name, "raw_one_sided_p": 0.25, "status": "RAW_UNADJUSTED"}
                for name in ("predictive_absolute_weekly_score", "predictive_paired_weekly_delta",
                    "robust_executable_absolute_weekly_return", "executable_paired_weekly_delta")]}
        return json.dumps(stats, sort_keys=True, separators=(",", ":")).encode()

    def test_recovery_preserves_insufficient_evidence_lifecycle_and_p_one(self):
        with tempfile.TemporaryDirectory() as temp:
            stats = {"multiplicity_adjustment_applied": False,
                "qualification_decision": "NOT_MADE_BY_M3", "sample_status": "INSUFFICIENT_EVIDENCE",
                "sample_warnings": ["executable_positions below frozen floor"],
                "estimands": [{"estimand_id": name, "raw_one_sided_p": 1.0,
                    "status": "INSUFFICIENT_EVIDENCE_P1_PLACEHOLDER"}
                    for name in ("predictive_absolute_weekly_score", "predictive_paired_weekly_delta",
                        "robust_executable_absolute_weekly_return", "executable_paired_weekly_delta")]}
            args = dict(PROVENANCE)
            args["artifacts"] = {"statistics.json": json.dumps(stats, sort_keys=True,
                separators=(",", ":")).encode()}
            path, _ = publish_package(temp, **args)
            registry = FakeRegistry()
            result = self._recover(registry, path)
            self.assertEqual(result.state, "INSUFFICIENT_EVIDENCE")
            self.assertEqual(set(registry.completions[0][1]["confirmatory_p_values"].values()), {1.0})

    def test_m1_start_requires_exact_verified_permit(self):
        import hashlib
        import json
        raw = b"frozen"
        pinned = PinnedInput("signals", Path("unused"), hashlib.sha256(raw).hexdigest(), len(raw), raw)
        admission = AdmittedAttempt("attempt-1", "protocol-1", "candidate", "c"*64, "p"*64,
            "r"*64, "k"*64, (pinned,), "previously_observed_historical_diagnostic", True, True, False)
        admission = __import__("dataclasses").replace(admission,
            protocol_bytes=json.dumps({"source": {"commit": "commit"}}).encode())
        digest = hashlib.sha256(json.dumps({"signals": pinned.sha256}, sort_keys=True,
            separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
        permit = VerifiedEvaluationPermit("attempt-1", "protocol-1", "c"*64, "p"*64, "commit",
            digest, "r"*64, "k"*64, "image", "attest", "authorization")
        registry = FakeRegistry("VALIDATED")
        begin_authorized_evaluation(registry, admission, permit, ("fold-1",), ("metric-1",))
        self.assertEqual(len(registry.started), 1)
        bad = VerifiedEvaluationPermit("attempt-other", "protocol-1", "c"*64, "p"*64, "commit",
            digest, "r"*64, "k"*64, "image", "attest", "authorization")
        with self.assertRaisesRegex(PackageError, "does not bind"):
            begin_authorized_evaluation(registry, admission, bad, ("fold-1",), ("metric-1",))
        self.assertEqual(len(registry.started), 1)


if __name__ == "__main__":
    unittest.main()
