"""Offline, finite-template Milestone 2 candidate representation and intake.

This module does not import strategies, load research datasets, evaluate
signals, or execute generated code. Candidate documents remain declarative and
are registered through the immutable M1 lifecycle before validation.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Any, Mapping, Sequence

from jsonschema import Draft202012Validator, FormatChecker

from crypto_bot import experiment_registry as m1
from crypto_bot.evolution_registry_security import (
    CheckpointError,
    ExternalSecurityStore,
    ExternalSigner,
    SecurityError,
    TrustError,
    validate_m2_document,
)


_ROOT = Path(__file__).resolve().parent.parent
_M0_SCHEMA_DIR = _ROOT / "reports" / "evolution_engine_milestone0_2026-11-18"
_PHASE4_SIMULATOR_COMMIT = "414cd2705e069936f2dbe6ed6d054f8b5a62e918"
_PHASE4_PUBLICATION_COMMIT = "6b6d3b9e64c80ce99d284b76f86bc79126a09dd5"
_MAX_PER_FAMILY = 12
_MAX_TOTAL = 36
_RESEARCH_CLASS = "previously_observed_historical_diagnostic"
_PHASE4_HISTORICAL_DIRS = (
    "reports/phase3",
    "reports/phase4",
    "reports/phase5",
    "reports/phase3_",
    "reports/phase4_",
    "reports/phase5_",
)


class GenerationError(RuntimeError):
    """A bounded generation request is invalid or cannot be reproduced."""


def resolve_declared_regime_priority(
    ordered_members: Sequence[str],
    signal_snapshots: Mapping[str, Mapping[str, Any]],
) -> tuple[str, Mapping[str, Any]] | None:
    """Apply the catalog's pure first-eligible tie-break to supplied snapshots.

    This helper never calls a strategy, reads market data, or alters a signal.
    A missing member or an explicit ``abstain: true`` advances to the next
    declared member; malformed snapshots fail closed. The selected mapping is
    returned unchanged so its stop/target/provenance fields remain intact.
    """
    allowed = {"trend_following", "breakout", "momentum", "mean_reversion"}
    if (not isinstance(ordered_members, Sequence) or isinstance(ordered_members, (str, bytes))
            or not ordered_members or any(not isinstance(member, str) for member in ordered_members)
            or len(set(ordered_members)) != len(ordered_members)
            or any(member not in allowed for member in ordered_members)):
        raise GenerationError("regime priority must be a nonempty ordered list of unique incumbent strategies")
    if not isinstance(signal_snapshots, Mapping):
        raise GenerationError("regime priority signal snapshots must be a mapping")
    for member in ordered_members:
        if member not in signal_snapshots:
            continue
        snapshot = signal_snapshots[member]
        if not isinstance(snapshot, Mapping) or not isinstance(snapshot.get("abstain"), bool):
            raise GenerationError(f"malformed signal snapshot for priority member {member}")
        if snapshot["abstain"]:
            continue
        return member, snapshot
    return None


@dataclass(frozen=True)
class GeneratedBatch:
    candidates: tuple[dict[str, Any], ...]
    candidate_bytes: tuple[bytes, ...]
    candidate_ids: tuple[str, ...]
    families: tuple[str, ...]
    template_ids: tuple[str, ...]


@dataclass(frozen=True)
class SubmissionResult:
    protocol_id: str
    attempt_ids: tuple[str, ...]
    attempt_manifest_sha256: str
    generation_manifest_sha256: str
    checkpoint_sha256: str
    validation_statuses: tuple[str, ...]
    report_directory: Path | None


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _content_id(raw: bytes) -> str:
    return "sha256:" + _sha256(raw)


def _json_copy(value: Any) -> Any:
    return json.loads(json.dumps(value))


def _strict_schema(name: str, value: Mapping[str, Any]) -> None:
    schema_path = _M0_SCHEMA_DIR / name
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    errors = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(value))
    if errors:
        rendered = "; ".join(str(error.message) for error in errors[:8])
        raise GenerationError(f"document violates frozen M0 schema {name}: {rendered}")


def prepare_generation_protocol(
    protocol_base: Mapping[str, Any],
    *,
    catalog_id: str,
    catalog_version: int,
    template_ids: Sequence[str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Derive the frozen M0 protocol ID and its ordered M2 request identity."""
    if not template_ids or len(template_ids) > _MAX_TOTAL:
        raise GenerationError("generation request requires 1..36 finite template IDs")
    if len(set(template_ids)) != len(template_ids):
        raise GenerationError("generation request template IDs must be unique")
    protocol = m1.prepare_protocol_identity(protocol_base, attempt_count=len(template_ids))
    _strict_schema("experiment_protocol.schema.json", protocol)
    if protocol["research_classification"] != _RESEARCH_CLASS or protocol["research_only"] is not True:
        raise GenerationError("Milestone 2 permits only historical research-only protocols")
    if protocol["data"]["observed_through_utc"][:10] > "2026-08-19":
        raise GenerationError("historical generator cannot access later or prospective data")
    request = {
        "schema_version": "athena.evolution.generation_request.v1",
        "request_id": "sha256:" + "0" * 64,
        "protocol_id": protocol["protocol_id"],
        "catalog_id": catalog_id,
        "catalog_version": catalog_version,
        "template_ids": list(template_ids),
        "candidate_created_at_utc": protocol["locked_at_utc"],
        "research_only": True,
    }
    request["request_id"] = _content_id(m1.canonical_json_bytes({
        key: value for key, value in request.items() if key != "request_id"
    }))
    validate_m2_document("generation_request.schema.json", request)
    return protocol, request


def _generator_source_metadata() -> dict[str, Any]:
    paths = (
        _ROOT / "crypto_bot" / "evolution_engine_m2.py",
        _ROOT / "crypto_bot" / "evolution_registry_security.py",
    )
    hasher = hashlib.sha256()
    for path in paths:
        relative = path.relative_to(_ROOT).as_posix().encode("utf-8")
        raw = path.read_bytes()
        hasher.update(len(relative).to_bytes(4, "big"))
        hasher.update(relative)
        hasher.update(len(raw).to_bytes(8, "big"))
        hasher.update(raw)
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=_ROOT, check=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            timeout=5, shell=False,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain", "--", "crypto_bot/evolution_engine_m2.py",
             "crypto_bot/evolution_registry_security.py"], cwd=_ROOT, check=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            timeout=5, shell=False,
        ).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise GenerationError("cannot establish generator source commit and worktree state") from exc
    if len(commit) != 40:
        raise GenerationError("generator source commit is malformed")
    return {
        "commit": commit,
        "worktree_clean": not bool(status.strip()),
        "generator_source_sha256": hasher.hexdigest(),
        "serializer_id": "athena-rfc8785-jcs-v1",
    }


class BoundedCandidateGenerator:
    """Pure deterministic map from approved templates to M0 candidate bytes."""

    def build(
        self,
        protocol: Mapping[str, Any],
        request: Mapping[str, Any],
        catalog: Mapping[str, Any],
    ) -> GeneratedBatch:
        validate_m2_document("generation_request.schema.json", request)
        validate_m2_document("template_catalog.schema.json", catalog)
        _strict_schema("experiment_protocol.schema.json", protocol)
        if protocol.get("protocol_id") != request["protocol_id"]:
            raise GenerationError("request protocol ID differs from locked protocol")
        if protocol.get("research_classification") != _RESEARCH_CLASS or protocol.get("research_only") is not True:
            raise GenerationError("candidate generator is restricted to historical research protocols")
        if request["candidate_created_at_utc"] != protocol["locked_at_utc"]:
            raise GenerationError("candidate timestamp must equal the locked protocol timestamp")
        expected_request_id = _content_id(m1.canonical_json_bytes({
            key: value for key, value in request.items() if key != "request_id"
        }))
        if request["request_id"] != expected_request_id:
            raise GenerationError("generation request identity mismatch")
        if protocol["protocol_id"] != m1.compute_protocol_id(protocol):
            raise GenerationError("locked protocol semantic ID mismatch")
        if protocol["search_family_id"] != m1.compute_search_family_id(protocol):
            raise GenerationError("locked protocol search-family ID mismatch")
        if request["catalog_id"] != catalog["catalog_id"] or request["catalog_version"] != catalog["catalog_version"]:
            raise GenerationError("request catalog identity/version mismatch")
        by_id = {entry["template_id"]: entry for entry in catalog["entries"]}
        if len(by_id) != len(catalog["entries"]):
            raise GenerationError("catalog contains duplicate template IDs")
        families = set(protocol["families"])
        entries: list[Mapping[str, Any]] = []
        for template_id in request["template_ids"]:
            entry = by_id.get(template_id)
            if entry is None:
                raise GenerationError(f"unknown catalog template ID {template_id}")
            if entry["family"] not in families:
                raise GenerationError("catalog template family is not enabled by the protocol")
            entries.append(entry)
        family_counts = Counter(entry["family"] for entry in entries)
        if len(entries) > _MAX_TOTAL or any(count > _MAX_PER_FAMILY for count in family_counts.values()):
            raise GenerationError("request exceeds persistent 12-per-family/36-total attempt limits")
        if protocol["candidate_set"]["attempt_count"] != len(entries):
            raise GenerationError("locked protocol attempt count differs from requested candidate batch")

        source = protocol["source"]
        data = protocol["data"]
        candidates: list[dict[str, Any]] = []
        candidate_bytes: list[bytes] = []
        candidate_ids: list[str] = []
        for entry in entries:
            candidate: dict[str, Any] = {
                "schema_version": "athena.evolution.candidate.v1",
                "id_algorithm": "sha256_rfc8785_jcs_semantic_payload_v1",
                "candidate_revision": 1,
                "experiment_id": protocol["protocol_id"],
                "family": entry["family"],
                "created_at_utc": protocol["locked_at_utc"],
                "baseline": {
                    "source_commit": source["commit"],
                    "phase4_simulator_commit": _PHASE4_SIMULATOR_COMMIT,
                    "phase4_publication_commit": _PHASE4_PUBLICATION_COMMIT,
                    "strategy_source_sha256": source["strategy_source_sha256"],
                    "regime_source_sha256": source["regime_source_sha256"],
                    "execution_source_sha256": source["execution_source_sha256"],
                    "phase4_config_sha256": source["phase4_config_sha256"],
                    "frozen_input_manifest_sha256": data["phase5_manifest_sha256"],
                },
                "parents": _json_copy(entry["parents"]),
                "strategy_members": _json_copy(entry["strategy_members"]),
                "policy": _json_copy(entry["policy"]),
                "research_only": True,
                "display_name": entry["template_id"],
            }
            candidate["candidate_id"] = m1.compute_candidate_id(candidate)
            raw = m1.canonical_json_bytes(candidate)
            candidates.append(candidate)
            candidate_bytes.append(raw)
            candidate_ids.append(candidate["candidate_id"])
        return GeneratedBatch(
            tuple(candidates), tuple(candidate_bytes), tuple(candidate_ids),
            tuple(entry["family"] for entry in entries),
            tuple(entry["template_id"] for entry in entries),
        )


class M2RegistryCoordinator:
    """Serializes generation, M1 admission, validation and external anchoring."""

    def __init__(
        self,
        registry: m1.ExperimentRegistry,
        security: ExternalSecurityStore,
        *,
        checkpoint_signer: ExternalSigner,
        report_root: str | Path | None = None,
    ):
        if security.registry.root != registry.root:
            raise SecurityError("security store is bound to another registry")
        self._registry = registry
        self._security = security
        self._checkpoint_signer = checkpoint_signer
        self._report_root = Path(report_root) if report_root is not None else (
            _ROOT / "reports" / "evolution_engine_milestone2"
        )
        self._generator = BoundedCandidateGenerator()

    def freeze_catalog(self, catalog: Mapping[str, Any], receipt: Mapping[str, Any]) -> str:
        return self._security.freeze_catalog(catalog, receipt)

    def submit(
        self,
        protocol: Mapping[str, Any],
        request: Mapping[str, Any],
        *,
        fault_at: str | None = None,
        fault_registration_after_event: int | None = None,
        write_report: bool = True,
    ) -> SubmissionResult:
        """Register all candidate bytes before validation, then checkpoint each mutation."""
        with self._security.serialized():
            previous_checkpoint = self._security.verify_latest(allow_empty=True)
            previous_anchor = (
                self._security._read_json(self._security.anchor_path, "checkpoint anchor")
                if previous_checkpoint is not None else None
            )
            catalog, receipt, receipt_sha = self._security.latest_catalog()
            generated = self._generator.build(protocol, request, catalog)
            if self._security.trust.identity["search_family_id"] != protocol["search_family_id"]:
                raise SecurityError("protocol search-family ID differs from external registry identity")
            if protocol["search_family_id"] != m1.compute_search_family_id(protocol):
                raise GenerationError("protocol search-family identity is invalid")
            if protocol["protocol_id"] != m1.compute_protocol_id(protocol):
                raise GenerationError("protocol semantic ID is invalid")
            if request["protocol_id"] != protocol["protocol_id"]:
                raise GenerationError("request and protocol IDs differ")
            if protocol["candidate_set"]["attempt_count"] != len(generated.candidates):
                raise GenerationError("protocol candidate_set attempt count differs from generated batch")

            events = self._registry.events()
            registered = [event for event in events if event["event_type"] == "CANDIDATE_ATTEMPT_REGISTERED"]
            counts = Counter(event["payload"]["assigned_family"] for event in registered)
            if len(registered) + len(generated.candidates) > _MAX_TOTAL:
                raise m1.SearchFamilyLimitError("cumulative M2 attempt ceiling of 36 is exhausted")
            requested_counts = Counter(generated.families)
            if any(counts[family] + count > _MAX_PER_FAMILY for family, count in requested_counts.items()):
                raise m1.SearchFamilyLimitError("cumulative M2 family attempt ceiling of 12 is exhausted")
            start_ordinal = len(registered) + 1
            attempt_ids = tuple(
                m1.compute_attempt_id(
                    protocol["protocol_id"], start_ordinal + offset, family,
                    _sha256(raw),
                )
                for offset, (family, raw) in enumerate(zip(generated.families, generated.candidate_bytes))
            )
            manifest = {
                "schema_version": "athena.evolution.generation_manifest.v1",
                "protocol_id": protocol["protocol_id"],
                "request_sha256": _sha256(m1.canonical_json_bytes(request)),
                "catalog_id": catalog["catalog_id"],
                "catalog_version": catalog["catalog_version"],
                "catalog_freeze_receipt_sha256": receipt_sha,
                "registry_identity_sha256": self._security.trust.identity_sha256,
                "generator_source": _generator_source_metadata(),
                "outputs": [
                    {
                        "ordinal": start_ordinal + offset,
                        "template_id": template_id,
                        "family": family,
                        "candidate_id": candidate_id,
                        "candidate_spec_bytes_sha256": _sha256(raw),
                        "attempt_id": attempt_id,
                    }
                    for offset, (template_id, family, candidate_id, raw, attempt_id) in enumerate(zip(
                        generated.template_ids, generated.families, generated.candidate_ids,
                        generated.candidate_bytes, attempt_ids,
                    ))
                ],
            }
            validate_m2_document("generation_manifest.schema.json", manifest)
            generation_manifest_sha = self._security.store_generation_manifest(manifest)
            submissions = [
                m1.AttemptSubmission(family, raw, origin="pre_registered_generator_slot")
                for family, raw in zip(generated.families, generated.candidate_bytes)
            ]
            registered_protocol_id, registered_attempts, attempt_manifest_sha = self._registry.register_protocol_with_attempts(
                protocol,
                submissions,
                fault_after_event=fault_registration_after_event,
            )
            if registered_attempts != attempt_ids or registered_protocol_id != protocol["protocol_id"]:
                raise SecurityError("M1 registry assigned IDs different from deterministic generation manifest")
            if fault_at == "after_registry_commit":
                raise OSError("injected crash after M1 registry transaction commit")
            checkpoint = self._security.publish_after_registry_write(
                self._checkpoint_signer,
                expected_previous_anchor=previous_anchor,
                expected_previous_event_count=len(events),
                generation_manifest_sha256=generation_manifest_sha,
                fault_at=fault_at,
            )
            self._security.verify_latest(allow_empty=False)
            states = self._validate_attempt_batch(registered_attempts, generation_manifest_sha)
            checkpoint = self._security.verify_latest(allow_empty=False)
            report_directory = None
            if write_report:
                report_directory = self._write_report(
                    protocol_id=registered_protocol_id,
                    catalog=catalog,
                    receipt=receipt,
                    request=request,
                    manifest=manifest,
                    attempts=registered_attempts,
                    checkpoint=checkpoint,
                )
            return SubmissionResult(
                protocol_id=registered_protocol_id,
                attempt_ids=tuple(registered_attempts),
                attempt_manifest_sha256=attempt_manifest_sha,
                generation_manifest_sha256=generation_manifest_sha,
                checkpoint_sha256=checkpoint["checkpoint_sha256"],
                validation_statuses=tuple(states),
                report_directory=report_directory,
            )

    def validate_pending(self) -> tuple[str, ...]:
        """Resume validation after a crash that left a verified sealed batch."""
        with self._security.serialized():
            checkpoint = self._security.verify_latest(allow_empty=False)
            events = self._registry.events()
            protocol_id, _protocol = self._security._latest_protocol(events)
            protocol_attempts = [
                event["payload"]["attempt_id"] for event in events
                if event["event_type"] == "CANDIDATE_ATTEMPT_REGISTERED"
                and event["protocol_id"] == protocol_id
            ]
            if not any(self._registry.attempt_states().get(attempt) == "SUBMITTED" for attempt in protocol_attempts):
                return ()
            return tuple(self._validate_attempt_batch(protocol_attempts, checkpoint["generation_manifest_sha256"]))

    def _validate_attempt_batch(
        self, attempt_ids: Sequence[str], generation_manifest_sha256: str
    ) -> list[str]:
        if not attempt_ids:
            return []
        events = self._registry.events()
        candidates: list[dict[str, Any] | None] = []
        candidate_ids: list[str | None] = []
        for attempt_id in attempt_ids:
            try:
                raw = self._registry.attempt_bytes(attempt_id)
                candidate = m1._parse_json_bytes(raw)
                candidate_id = candidate.get("candidate_id") if isinstance(candidate, dict) else None
                if isinstance(candidate_id, str) and candidate_id == m1.compute_candidate_id(candidate):
                    candidates.append(candidate)
                    candidate_ids.append(candidate_id)
                else:
                    candidates.append(None)
                    candidate_ids.append(None)
            except (ValueError, TypeError, KeyError, json.JSONDecodeError, UnicodeDecodeError):
                candidates.append(None)
                candidate_ids.append(None)
        candidate_groups: dict[str, list[int]] = defaultdict(list)
        for index, candidate_id in enumerate(candidate_ids):
            if candidate_id is not None:
                candidate_groups[candidate_id].append(index)
        known_ids = {
            event["payload"].get("candidate_id")
            for event in events if event["event_type"] == "CANDIDATE_REGISTERED"
        }
        duplicate_indexes = {
            index for indexes in candidate_groups.values() if len(indexes) > 1 for index in indexes
        }
        duplicate_indexes.update(
            index for index, candidate_id in enumerate(candidate_ids)
            if candidate_id is not None and candidate_id in known_ids
        )
        statuses: list[str] = []
        anchor = self._security._read_json(self._security.anchor_path, "checkpoint anchor")
        for index, attempt_id in enumerate(attempt_ids):
            if self._registry.attempt_states().get(attempt_id) != "SUBMITTED":
                continue
            if index in duplicate_indexes:
                _protocol, registration, _raw, protocol_id = self._registry._protocol_and_attempt(attempt_id)
                self._registry._append_attempt_event(
                    protocol_id, attempt_id, "CANDIDATE_INVALID",
                    {
                        "attempt_id": attempt_id,
                        "reason_code": "DUPLICATE_SEMANTIC_CANDIDATE",
                        "detail": "semantic candidate ID was already present in this registered attempt batch or family",
                        "candidate_spec_bytes_sha256": registration["candidate_spec_bytes_sha256"],
                    },
                )
                status = "INVALID_CANDIDATE"
            else:
                status = self._registry.validate_attempt(attempt_id)
            statuses.append(status)
            self._security.publish_after_registry_write(
                self._checkpoint_signer,
                expected_previous_anchor=anchor,
                generation_manifest_sha256=generation_manifest_sha256,
            )
            anchor = self._security._read_json(self._security.anchor_path, "checkpoint anchor")
            self._security.verify_latest(allow_empty=False)
        return statuses

    def _write_report(
        self,
        *,
        protocol_id: str,
        catalog: Mapping[str, Any],
        receipt: Mapping[str, Any],
        request: Mapping[str, Any],
        manifest: Mapping[str, Any],
        attempts: Sequence[str],
        checkpoint: Mapping[str, Any],
    ) -> Path:
        root = self._report_root.expanduser().absolute()
        if root.is_symlink():
            raise GenerationError("report root cannot be a symlink")
        resolved = root.resolve(strict=False)
        if os.path.normcase(str(root)) != os.path.normcase(str(resolved)):
            raise GenerationError("report path resolves through a symlink")
        try:
            relative_root = resolved.relative_to(_ROOT.resolve())
        except ValueError:
            relative_root = None
        if relative_root is not None and relative_root != Path("reports/evolution_engine_milestone2"):
            raise GenerationError("repository outputs must use the dedicated dated Milestone 2 report root")
        for protected in _PHASE4_HISTORICAL_DIRS:
            protected_path = (_ROOT / protected).resolve(strict=False)
            try:
                resolved.relative_to(protected_path)
            except ValueError:
                pass
            else:
                raise GenerationError("report output cannot be written inside frozen historical directories")
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        target = resolved / date / protocol_id.replace(":", "_")
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() or target.is_symlink():
            raise GenerationError(f"refusing to overwrite existing M2 report directory {target}")
        staging = Path(tempfile.mkdtemp(prefix=f".{target.name}.", suffix=".staging", dir=target.parent))
        try:
            if os.name != "nt":
                os.chmod(staging, 0o700)
            (staging / "candidate_specs").mkdir()
            documents = {
                "catalog.json": catalog,
                "catalog_freeze_receipt.json": receipt,
                "generation_request.json": request,
                "generation_manifest.json": manifest,
                "checkpoint.json": checkpoint,
            }
            for name, value in documents.items():
                self._write_exclusive(staging / name, m1.canonical_json_bytes(value))
            manifest_rows = manifest["outputs"]
            for attempt_id, row in zip(attempts, manifest_rows):
                raw = self._registry.attempt_bytes(attempt_id)
                if _sha256(raw) != row["candidate_spec_bytes_sha256"]:
                    raise SecurityError("stored registered candidate bytes differ from generation manifest")
                self._write_exclusive(staging / "candidate_specs" / f"{row['ordinal']:03d}-{attempt_id[7:]}.json", raw)
            if os.name != "nt":
                fd = os.open(staging, os.O_RDONLY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
            if target.exists() or target.is_symlink():
                raise GenerationError(f"refusing to overwrite existing M2 report directory {target}")
            os.rename(staging, target)
            staging = None
            if os.name != "nt":
                fd = os.open(target.parent, os.O_RDONLY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
        finally:
            if staging is not None:
                shutil.rmtree(staging, ignore_errors=True)
        return target

    @staticmethod
    def _write_exclusive(path: Path, raw: bytes) -> None:
        with path.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())

