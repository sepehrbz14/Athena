"""Crash-safe M3 package sealing and M1 lifecycle recovery.

Only the M1 public lifecycle API is used. Packages are immutable, written to a
fresh staging directory and atomically published after a canonical manifest is
sealed. Recovery never reruns an evaluator.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import tempfile
from typing import Mapping

from . import experiment_registry as m1
from .m3_admission import AdmittedAttempt
from .m3_isolation import VerifiedEvaluationPermit

FROZEN_MAX_PACKAGE_BYTES = 536_870_912
FROZEN_MAX_WALL_SECONDS = 1_800
FROZEN_MAX_MEMORY_MB = 8_192


class PackageError(RuntimeError):
    """An M3 output package is incomplete, conflicting, or unverifiable."""


@dataclass(frozen=True, slots=True)
class RecoveryResult:
    attempt_id: str
    state: str
    package_sha256: str | None
    action: str


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


_FROZEN_CONFIRMATORY_HYPOTHESES = {
    "predictive_absolute_weekly_score", "predictive_paired_weekly_delta",
    "robust_executable_absolute_weekly_return", "executable_paired_weekly_delta",
}


def _raw_statistics(package_path: Path) -> tuple[dict[str, float], str, tuple[str, ...]]:
    """Extract M3 raw p-values from the sealed evidence; M4 adjusts later."""
    try:
        value = json.loads((package_path / "statistics.json").read_text(encoding="utf-8"))
        rows = value["estimands"]
        adjustment = value["multiplicity_adjustment_applied"]
        decision = value["qualification_decision"]
        sample_status = value["sample_status"]
        warnings = value["sample_warnings"]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise PackageError("sealed package lacks the required raw M0 statistical evidence") from exc
    if adjustment is not False or decision != "NOT_MADE_BY_M3" or not isinstance(rows, list):
        raise PackageError("M3 statistical artifact applies correction or contains a qualification decision")
    parsed = {}
    for row in rows:
        try:
            key = row["estimand_id"]
            raw_p = row["raw_one_sided_p"]
            status = row["status"]
        except (KeyError, TypeError) as exc:
            raise PackageError("M3 statistical estimand row is malformed") from exc
        if key in parsed or key not in _FROZEN_CONFIRMATORY_HYPOTHESES:
            raise PackageError("M3 statistical artifact has duplicate or unknown frozen estimands")
        if isinstance(raw_p, bool) or not isinstance(raw_p, (int, float)) or not math.isfinite(raw_p) or not 0 <= raw_p <= 1:
            raise PackageError("M3 raw p-value must be finite and within [0,1]")
        if status not in {"RAW_UNADJUSTED", "INSUFFICIENT_EVIDENCE_P1_PLACEHOLDER"}:
            raise PackageError("M3 raw p-value status is outside the frozen vocabulary")
        parsed[key] = float(raw_p)
    if set(parsed) != _FROZEN_CONFIRMATORY_HYPOTHESES:
        raise PackageError("M3 statistical artifact must contain all four frozen hypotheses")
    if sample_status not in {"SAMPLE_FLOORS_MET", "INSUFFICIENT_EVIDENCE"} or not isinstance(warnings, list) or any(not isinstance(item, str) for item in warnings):
        raise PackageError("M3 sample status or warnings are malformed")
    if sample_status == "INSUFFICIENT_EVIDENCE" and any(p != 1 for p in parsed.values()):
        raise PackageError("insufficient evidence must preserve conservative p=1 placeholders")
    return parsed, sample_status, tuple(warnings)


def _raw_p_values(package_path: Path) -> dict[str, float]:
    return _raw_statistics(package_path)[0]


def _safe_name(name: str) -> str:
    if not name or name in {".", "..", "manifest.json"} or "/" in name or "\\" in name:
        raise PackageError("artifact names must be simple filenames and cannot replace the manifest")
    return name


def publish_package(root: str | Path, *, attempt_id: str, protocol_id: str,
                    candidate_sha256: str, protocol_sha256: str,
                    registry_tip_sha256: str, checkpoint_sha256: str,
                    input_hashes: Mapping[str, str], source_commit: str,
                    source_dirty: bool, fold_plan: object, seed_records: object,
                    resource_usage: Mapping[str, int],
                    methodology: Mapping[str, object], artifacts: Mapping[str, bytes]) -> tuple[Path, str]:
    """Seal and atomically publish one exclusive, dated attempt package."""
    _safe_name(attempt_id)
    if not artifacts or not attempt_id or not protocol_id:
        raise PackageError("attempt identity and at least one evidence artifact are required")
    if any(not isinstance(item, bytes) for item in artifacts.values()):
        raise PackageError("artifact contents must be immutable bytes")
    if sum(map(len, artifacts.values())) > FROZEN_MAX_PACKAGE_BYTES:
        raise PackageError("package exceeds the frozen 512 MiB per-attempt output ceiling")
    required_resources = {"wall_seconds", "peak_memory_mb", "worker_count"}
    if set(resource_usage) != required_resources or any(
            not isinstance(resource_usage[key], int) or isinstance(resource_usage[key], bool) or resource_usage[key] < 0
            for key in required_resources):
        raise PackageError("package must record integer wall, memory and worker resource usage")
    if (resource_usage["wall_seconds"] > FROZEN_MAX_WALL_SECONDS or
            resource_usage["peak_memory_mb"] > FROZEN_MAX_MEMORY_MB or resource_usage["worker_count"] != 1):
        raise PackageError("package resource usage exceeds frozen M0 limits")
    if not isinstance(source_dirty, bool) or not source_commit:
        raise PackageError("package must bind a source commit and explicit dirty-worktree state")
    target_root = Path(root).resolve()
    target_root.mkdir(parents=True, exist_ok=True)
    day = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).strftime("%Y-%m-%d")
    date_dir = target_root / day
    date_dir.mkdir(exist_ok=True)
    final = date_dir / attempt_id
    if final.exists():
        raise PackageError("attempt package already exists and cannot be overwritten")
    stage = Path(tempfile.mkdtemp(prefix=f".{attempt_id}.", dir=date_dir))
    try:
        listed = {}
        for name, content in sorted(artifacts.items()):
            filename = _safe_name(name)
            path = stage / filename
            with path.open("xb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            listed[filename] = {"sha256": _sha(content), "size_bytes": len(content)}
        manifest = {
            "schema": "athena.m3.evaluation-package.v1", "attempt_id": attempt_id,
            "protocol_id": protocol_id, "candidate_sha256": candidate_sha256,
            "protocol_sha256": protocol_sha256, "registry_tip_sha256": registry_tip_sha256,
            "checkpoint_sha256": checkpoint_sha256, "input_hashes": dict(sorted(input_hashes.items())),
            "source_commit": source_commit, "source_dirty": source_dirty,
            "fold_plan": fold_plan, "seed_records": seed_records,
            "resource_usage": dict(sorted(resource_usage.items())),
            "methodology": dict(methodology), "signal_generation_provenance":
                "hash-pinned frozen signal rows; source-generation provenance not reconstructed",
            "artifacts": listed,
        }
        manifest_bytes = _canonical(manifest)
        with (stage / "manifest.json").open("xb") as handle:
            handle.write(manifest_bytes)
            handle.flush()
            os.fsync(handle.fileno())
        # Persist the directory entry before publishing; final path is exclusive.
        try:
            directory_fd = os.open(stage, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            pass
        if final.exists():
            raise PackageError("attempt package was concurrently published")
        os.replace(stage, final)
        return final, _sha(manifest_bytes)
    except Exception:
        if stage.exists():
            shutil.rmtree(stage)
        raise


def verify_package(path: str | Path, *, expected_attempt_id: str | None = None) -> tuple[dict, str]:
    root = Path(path)
    if not root.is_dir() or root.is_symlink():
        raise PackageError("published package directory is missing or unsafe")
    try:
        manifest_bytes = (root / "manifest.json").read_bytes()
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PackageError("package manifest is missing or malformed") from exc
    if _canonical(manifest) != manifest_bytes or manifest.get("schema") != "athena.m3.evaluation-package.v1":
        raise PackageError("package manifest is not canonical or uses an unknown schema")
    if expected_attempt_id is not None and manifest.get("attempt_id") != expected_attempt_id:
        raise PackageError("package attempt identity differs from recovery target")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict) or not artifacts:
        raise PackageError("package manifest contains no artifacts")
    expected_names = set(artifacts) | {"manifest.json"}
    actual_names = {item.name for item in root.iterdir()}
    if actual_names != expected_names:
        raise PackageError("package contains missing or unmanifested files")
    for name, item in artifacts.items():
        filename = _safe_name(name)
        artifact = root / filename
        if artifact.is_symlink() or not artifact.is_file():
            raise PackageError("package artifact is not a regular file")
        data = artifact.read_bytes()
        if len(data) != item.get("size_bytes") or _sha(data) != item.get("sha256"):
            raise PackageError("package artifact hash or size mismatch")
    return manifest, _sha(manifest_bytes)


def begin_authorized_evaluation(registry, admission: AdmittedAttempt,
                                permit: VerifiedEvaluationPermit,
                                fold_ids: tuple[str, ...], metric_ids: tuple[str, ...]):
    """Record M1 RUNNING only after exact signed isolation/authorization proof."""
    if not admission.eligible_for_evaluation:
        raise PackageError("M1 evaluation start requires read-only validated admission")
    input_hashes = {item.key: item.sha256 for item in admission.inputs}
    input_digest = _sha(_canonical(dict(sorted(input_hashes.items()))))
    if (not isinstance(permit, VerifiedEvaluationPermit) or permit.attempt_id != admission.attempt_id or
            permit.protocol_id != admission.protocol_id or permit.candidate_sha256 != admission.candidate_sha256 or
            permit.protocol_sha256 != admission.protocol_sha256 or permit.input_hashes_sha256 != input_digest):
        raise PackageError("verified Linux permit does not bind this admitted attempt and its exact inputs")
    try:
        protocol = json.loads(admission.protocol_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PackageError("admitted protocol snapshot is malformed") from exc
    if permit.source_commit != protocol.get("source", {}).get("commit"):
        raise PackageError("verified Linux permit source commit differs from admitted protocol")
    if permit.registry_tip_sha256 != admission.registry_tip_sha256 or permit.checkpoint_sha256 != admission.checkpoint_sha256:
        raise PackageError("verified Linux permit does not bind trusted M1/M2 integrity identities")
    if not fold_ids or len(fold_ids) != len(set(fold_ids)) or not metric_ids or len(metric_ids) != len(set(metric_ids)):
        raise PackageError("registered fold and metric identities must be nonempty and unique")
    return registry.record_evaluation_started(admission.attempt_id, fold_ids, metric_ids)


def publish_and_complete(registry, *, package_arguments: Mapping,
                         wall_seconds: int, peak_memory_mb: int, artifact_bytes: int) -> RecoveryResult:
    """Publish the sealed immutable package, then use only M1 public completion APIs."""
    usage = package_arguments.get("resource_usage", {})
    if usage != {"wall_seconds": wall_seconds, "peak_memory_mb": peak_memory_mb, "worker_count": 1}:
        raise PackageError("M1 resource report differs from the sealed package resource record")
    path, digest = publish_package(**package_arguments)
    manifest, verified = verify_package(path)
    if verified != digest:
        raise PackageError("published package failed post-publication verification")
    actual_bytes = sum(item["size_bytes"] for item in manifest["artifacts"].values()) + len((path / "manifest.json").read_bytes())
    if artifact_bytes < actual_bytes:
        raise PackageError("M1 resource report undercounts sealed package bytes")
    attempt_id = manifest["attempt_id"]
    p_values, sample_status, warnings = _raw_statistics(path)
    result = registry.complete_evaluation(attempt_id, result_sha256=digest,
        wall_seconds=wall_seconds, peak_memory_mb=peak_memory_mb, artifact_bytes=artifact_bytes,
        worker_count=1, confirmatory_p_values=p_values)
    if result == "TECHNICAL_EVALUATION_FAILURE":
        raise PackageError("M1 rejected completion on its frozen resource/lifecycle constraints")
    if sample_status == "INSUFFICIENT_EVIDENCE":
        registry.mark_insufficient(attempt_id, "FROZEN_SAMPLE_FLOOR", "; ".join(warnings))
    return RecoveryResult(attempt_id, registry.attempt_states().get(attempt_id), digest, "PUBLISHED_AND_COMPLETED")


def recover_attempt(registry, attempt_id: str, package_path: str | Path | None, *,
                    expected_protocol_id: str, expected_candidate_sha256: str,
                    expected_protocol_sha256: str, expected_registry_tip_sha256: str,
                    expected_checkpoint_sha256: str) -> RecoveryResult:
    """Idempotently finalize an existing sealed package or record failure.

    A RUNNING M1 attempt is never executed again. Missing/corrupt package means
    technical failure. A package cannot finalize a VALIDATED (not-started)
    attempt because the public M1 lifecycle permits completion only from RUNNING.
    """
    state = registry.attempt_states().get(attempt_id)
    if state in {"COMPLETED", "INSUFFICIENT_EVIDENCE", "TECHNICAL_EVALUATION_FAILURE"}:
        if package_path is None:
            if state == "TECHNICAL_EVALUATION_FAILURE":
                return RecoveryResult(attempt_id, state, None, "ALREADY_TERMINAL_FAILURE")
            raise PackageError("completed attempt has no package to verify")
        _manifest, digest = verify_package(package_path, expected_attempt_id=attempt_id)
        return RecoveryResult(attempt_id, state, digest, "ALREADY_TERMINAL_VERIFIED")
    if state != "RUNNING":
        raise PackageError("only an M1 RUNNING attempt can be recovered")
    if package_path is None:
        registry.fail_attempt(attempt_id, "M3_PACKAGE_MISSING", "recovery found no sealed package")
        return RecoveryResult(attempt_id, "TECHNICAL_EVALUATION_FAILURE", None, "FAILED_MISSING_PACKAGE")
    try:
        manifest, digest = verify_package(package_path, expected_attempt_id=attempt_id)
        expected = {"protocol_id": expected_protocol_id,
                    "candidate_sha256": expected_candidate_sha256,
                    "protocol_sha256": expected_protocol_sha256,
                    "registry_tip_sha256": expected_registry_tip_sha256,
                    "checkpoint_sha256": expected_checkpoint_sha256}
        if any(manifest.get(key) != value for key, value in expected.items()):
            raise PackageError("sealed package provenance differs from registered attempt")
    except PackageError as exc:
        registry.fail_attempt(attempt_id, "M3_PACKAGE_INVALID", str(exc))
        return RecoveryResult(attempt_id, "TECHNICAL_EVALUATION_FAILURE", None, "FAILED_INVALID_PACKAGE")
    usage = manifest.get("resource_usage", {})
    try:
        wall_seconds, peak_memory_mb, worker_count = usage["wall_seconds"], usage["peak_memory_mb"], usage["worker_count"]
    except (KeyError, TypeError) as exc:
        registry.fail_attempt(attempt_id, "M3_PACKAGE_INVALID", "package has no resource usage record")
        return RecoveryResult(attempt_id, "TECHNICAL_EVALUATION_FAILURE", None, "FAILED_INVALID_PACKAGE")
    try:
        p_values, sample_status, warnings = _raw_statistics(Path(package_path))
    except PackageError as exc:
        registry.fail_attempt(attempt_id, "M3_STATISTICS_INVALID", str(exc))
        return RecoveryResult(attempt_id, "TECHNICAL_EVALUATION_FAILURE", None, "FAILED_INVALID_PACKAGE")
    complete_status = registry.complete_evaluation(attempt_id, result_sha256=digest,
        wall_seconds=wall_seconds, peak_memory_mb=peak_memory_mb,
        artifact_bytes=sum(item["size_bytes"] for item in manifest["artifacts"].values()) + len((Path(package_path) / "manifest.json").read_bytes()),
        worker_count=worker_count, confirmatory_p_values=p_values)
    final_state = registry.attempt_states().get(attempt_id)
    if complete_status == "COMPLETED" and sample_status == "INSUFFICIENT_EVIDENCE":
        registry.mark_insufficient(attempt_id, "FROZEN_SAMPLE_FLOOR", "; ".join(warnings))
        final_state = registry.attempt_states().get(attempt_id)
    return RecoveryResult(attempt_id, final_state, digest, "FINALIZED_EXISTING_PACKAGE")
