"""Read-only Milestone 3 admission and frozen-input integrity checks.

This module does not start evaluations or change registry state. It admits only
valid, historical M1 attempts after M2 external integrity verification.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
from typing import Mapping

from . import experiment_registry as m1


class AdmissionError(RuntimeError):
    """An attempt or one of its pinned inputs cannot be admitted."""


@dataclass(frozen=True, slots=True)
class PinnedInput:
    key: str
    path: Path
    sha256: str
    size_bytes: int
    content: bytes


@dataclass(frozen=True, slots=True)
class AdmittedAttempt:
    attempt_id: str
    protocol_id: str
    candidate_id: str
    candidate_sha256: str
    protocol_sha256: str
    registry_tip_sha256: str
    checkpoint_sha256: str
    inputs: tuple[PinnedInput, ...]
    research_classification: str
    previously_observed: bool
    eligible_for_evaluation: bool
    authoritative_evaluation_authorized: bool


_REQUIRED_INPUTS = {
    "signals": "signals_sha256",
    "candles": "candles_sha256",
    "phase5_manifest": "phase5_manifest_sha256",
}


def _snapshot_file(path: Path) -> tuple[bytes, str]:
    with path.open("rb") as handle:
        before = os.fstat(handle.fileno())
        content = handle.read()
        after = os.fstat(handle.fileno())
    if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
        raise AdmissionError("pinned input changed while its immutable snapshot was read")
    return content, hashlib.sha256(content).hexdigest()


def _resolve_regular_file(value: str | Path) -> Path:
    supplied = Path(value).expanduser()
    if supplied.is_symlink():
        raise AdmissionError("pinned input path may not be a symlink")
    try:
        resolved = supplied.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise AdmissionError("pinned input path does not resolve") from exc
    if not resolved.is_file():
        raise AdmissionError("pinned input must resolve to a regular file")
    # Reject symlinked path components as well as a symlink leaf.
    cursor = supplied.absolute()
    for part in (cursor, *cursor.parents):
        if part.is_symlink():
            raise AdmissionError("pinned input path contains a symlink component")
    return resolved


def _event_payload(events: list[dict], event_type: str, attempt_id: str | None = None) -> dict:
    for event in events:
        if event["event_type"] != event_type:
            continue
        payload = event["payload"]
        if attempt_id is None or payload.get("attempt_id") == attempt_id:
            return payload
    raise AdmissionError(f"required registry event not found: {event_type}")


def admit_attempt(
    registry: m1.ExperimentRegistry,
    security_store: object,
    attempt_id: str,
    input_paths: Mapping[str, str | Path],
) -> AdmittedAttempt:
    """Verify provenance, lifecycle, candidate bytes and frozen input hashes.

    ``security_store`` must expose M2's ``verify_latest(allow_empty=False)``.
    This is a read-only preflight; it never records a lifecycle event or starts
    an evaluator. Holdout protocols are rejected before input paths are read.
    """
    if set(input_paths) != set(_REQUIRED_INPUTS):
        raise AdmissionError("input set must be exactly signals, candles, and phase5_manifest")

    try:
        registry_verification = registry.verify()
        checkpoint = security_store.verify_latest(allow_empty=False)  # type: ignore[attr-defined]
    except Exception as exc:
        raise AdmissionError("registry or trusted M2 checkpoint verification failed") from exc
    if not registry_verification.valid or checkpoint is None:
        raise AdmissionError("registry/checkpoint is not in a verifiable nonempty state")

    events = registry.events()
    states = registry.attempt_states()
    if states.get(attempt_id) != "VALIDATED":
        raise AdmissionError("attempt must be registered and M1-validated, with no prior evaluation")
    registration = _event_payload(events, "CANDIDATE_REGISTERED", attempt_id)
    attempt_event = next((
        event for event in events
        if event["event_type"] == "CANDIDATE_ATTEMPT_REGISTERED"
        and event["payload"].get("attempt_id") == attempt_id
    ), None)
    protocol_id = attempt_event.get("protocol_id") if attempt_event is not None else None
    if not isinstance(protocol_id, str):
        raise AdmissionError("attempt registration has no protocol identity")
    protocol_event = next((e for e in events if e["event_type"] == "PROTOCOL_PRE_REGISTERED" and e["protocol_id"] == protocol_id), None)
    if protocol_event is None:
        raise AdmissionError("registered protocol is missing")
    protocol = protocol_event["payload"]["protocol"]
    if protocol.get("research_classification") != "previously_observed_historical_diagnostic":
        raise AdmissionError("M3 ordinary evaluation admits historical diagnostics only")
    data = protocol.get("data", {})
    if data.get("previously_examined") is not True:
        raise AdmissionError("historical data must be explicitly classified as previously examined")
    if str(data.get("observed_through_utc", ""))[:10] >= "2026-08-20":
        raise AdmissionError("data extending beyond the frozen previously-observed period is not admitted")

    try:
        raw_candidate = registry.attempt_bytes(attempt_id)
        candidate = json.loads(raw_candidate.decode("utf-8"))
    except Exception as exc:
        raise AdmissionError("registered candidate bytes are unreadable") from exc
    candidate_sha = hashlib.sha256(raw_candidate).hexdigest()
    if candidate_sha != registration.get("candidate_spec_bytes_sha256"):
        raise AdmissionError("candidate bytes differ from the registered immutable digest")
    if candidate.get("candidate_id") != m1.compute_candidate_id(candidate):
        raise AdmissionError("candidate semantic identity is invalid")
    if candidate.get("experiment_id") != protocol_id:
        raise AdmissionError("candidate protocol identity differs from its M1 registration")
    if candidate.get("research_only") is not True:
        raise AdmissionError("candidate is not marked research-only")

    if checkpoint.get("protocol_id") != protocol_id:
        raise AdmissionError("trusted M2 checkpoint does not bind the attempt protocol")
    if checkpoint.get("event_tip_sha256") != registry_verification.tip_sha256:
        raise AdmissionError("trusted M2 checkpoint tip differs from verified M1 registry")

    pinned: list[PinnedInput] = []
    for key, protocol_field in _REQUIRED_INPUTS.items():
        path = _resolve_regular_file(input_paths[key])
        content, actual = _snapshot_file(path)
        expected = data.get(protocol_field)
        if not isinstance(expected, str) or actual != expected:
            raise AdmissionError(f"pinned input hash mismatch for {key}")
        pinned.append(PinnedInput(key, path, actual, len(content), content))

    protocol_digest = hashlib.sha256(m1.canonical_json_bytes(protocol)).hexdigest()
    return AdmittedAttempt(
        attempt_id=attempt_id,
        protocol_id=protocol_id,
        candidate_id=candidate["candidate_id"],
        candidate_sha256=candidate_sha,
        protocol_sha256=protocol_digest,
        registry_tip_sha256=registry_verification.tip_sha256 or "",
        checkpoint_sha256=checkpoint["checkpoint_sha256"],
        inputs=tuple(pinned),
        research_classification=protocol["research_classification"],
        previously_observed=True,
        eligible_for_evaluation=True,
        authoritative_evaluation_authorized=False,
    )

