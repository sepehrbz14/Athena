"""Append-only, independently verifiable Milestone 1 experiment registry.

The registry records protocol and attempt events only. Candidate payloads are
stored as immutable bytes; validation and evaluation are separate operations.
SQLite transactions provide atomic publication on supported local filesystems,
and a SHA-256 event chain makes the record independently verifiable.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import sys
from typing import Any, Iterable, Mapping, Sequence

from jsonschema import Draft202012Validator, FormatChecker


_PACKAGE_ROOT = Path(__file__).resolve().parent.parent
_M0_SCHEMA_DIR = _PACKAGE_ROOT / "reports" / "evolution_engine_milestone0_2026-11-18"
_SCHEMA_NAMES = (
    "candidate_spec.schema.json",
    "candidate_attempt_manifest.schema.json",
    "experiment_protocol.schema.json",
    "registry_event.schema.json",
)
_ALLOWED_FAMILIES = (
    "regime_specialization",
    "signal_combination",
    "regime_selection_allocation",
)
_MAX_PER_FAMILY = 12
_MAX_TOTAL_ATTEMPTS = 36
_MAX_WALL_SECONDS_PER_ATTEMPT = 1800
_MAX_WALL_SECONDS_SEARCH_FAMILY = 86400
_MAX_MEMORY_MB = 8192
_MAX_ARTIFACT_BYTES_PER_ATTEMPT = 536870912
_MAX_ARTIFACT_BYTES_SEARCH_FAMILY = 10737418240
_MAX_WORKERS = 1
_MASTER_SEED = 20261118
_CONFIRMATORY_HYPOTHESES = (
    "predictive_absolute_weekly_score",
    "predictive_paired_weekly_delta",
    "robust_executable_absolute_weekly_return",
    "executable_paired_weekly_delta",
)


class RegistryError(RuntimeError):
    """Base class for registry contract failures."""


class RegistryIntegrityError(RegistryError):
    """Raised when persisted bytes do not match their integrity records."""


class InvalidTransitionError(RegistryError):
    """Raised when an event is not permitted from the current attempt state."""


class DuplicateIdentifierError(RegistryError):
    """Raised when an immutable protocol, attempt, or event ID is reused."""


class ProtocolError(RegistryError):
    """Raised when a protocol is inconsistent with the frozen M0 schemas."""


class SearchFamilyLimitError(RegistryError):
    """Raised when cumulative frozen search-family limits would be exceeded."""


class HoldoutAccessError(RegistryError):
    """M1 registry code is not permitted to access or release prospective data."""


@dataclass(frozen=True)
class AttemptSubmission:
    """Exact candidate bytes and their pre-validation family assignment."""

    assigned_family: str
    candidate_spec_bytes: bytes
    origin: str = "manually_submitted_for_registered_slot"


@dataclass(frozen=True)
class VerificationResult:
    valid: bool
    event_count: int
    protocol_count: int
    attempt_count: int
    search_family_id: str | None
    tip_sha256: str | None


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _json_string(value: str) -> str:
    # JCS uses JSON.stringify string escaping; ensure_ascii=False preserves
    # Unicode scalar values while Python escapes control characters identically.
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise ValueError("JSON strings must contain valid Unicode scalar values") from exc
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _jcs_number(value: int | float) -> str:
    """Serialize schema-supported JSON numbers with ECMAScript/JCS notation."""
    if isinstance(value, bool):
        raise TypeError("booleans are not numeric JCS values")
    if isinstance(value, int):
        # JCS numbers are IEEE-754 values; larger integers cannot be represented
        # unambiguously by the RFC 8785 data model.
        if abs(value) > 9007199254740991:
            raise ValueError("integer exceeds the exactly representable JCS range")
        return str(value)
    if not math.isfinite(value):
        raise ValueError("non-finite JSON numbers are prohibited")
    if value == 0:
        return "0"

    negative = value < 0
    text = repr(abs(value)).lower()
    if "e" in text:
        mantissa, exponent_text = text.split("e", 1)
        exponent = int(exponent_text)
    else:
        mantissa, exponent = text, 0
    whole, dot, fraction = mantissa.partition(".")
    raw_digits = whole + fraction
    leading_zero_count = len(raw_digits) - len(raw_digits.lstrip("0"))
    digits = raw_digits.lstrip("0") or "0"
    decimal_position = len(whole) + exponent - leading_zero_count
    # Remove insignificant trailing zeros without changing decimal position.
    while len(digits) > 1 and digits.endswith("0"):
        digits = digits[:-1]

    magnitude = abs(value)
    if 1e-6 <= magnitude < 1e21:
        if decimal_position <= 0:
            body = "0." + "0" * (-decimal_position) + digits
        elif decimal_position >= len(digits):
            body = digits + "0" * (decimal_position - len(digits))
        else:
            body = digits[:decimal_position] + "." + digits[decimal_position:]
    else:
        exponent = decimal_position - 1
        significand = digits[0] + ("." + digits[1:] if len(digits) > 1 else "")
        body = f"{significand}e{'+' if exponent >= 0 else ''}{exponent}"
    return ("-" if negative else "") + body


def canonical_json_bytes(value: Any) -> bytes:
    """Return RFC 8785 canonical JSON bytes for the frozen schema value model."""
    def encode(item: Any) -> str:
        if item is None:
            return "null"
        if item is True:
            return "true"
        if item is False:
            return "false"
        if isinstance(item, str):
            return _json_string(item)
        if isinstance(item, (int, float)):
            return _jcs_number(item)
        if isinstance(item, list):
            return "[" + ",".join(encode(child) for child in item) + "]"
        if isinstance(item, tuple):
            return "[" + ",".join(encode(child) for child in item) + "]"
        if isinstance(item, Mapping):
            if any(not isinstance(key, str) for key in item):
                raise TypeError("JSON object keys must be strings")
            # RFC 8785 orders property names by UTF-16 code units.
            keys = sorted(item, key=lambda key: key.encode("utf-16-be", errors="strict"))
            return "{" + ",".join(_json_string(key) + ":" + encode(item[key]) for key in keys) + "}"
        raise TypeError(f"unsupported JSON value type: {type(item).__name__}")

    return encode(value).encode("utf-8", errors="strict")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def content_id(payload: Any) -> str:
    return "sha256:" + sha256_hex(canonical_json_bytes(payload))


def schema_bundle_sha256(schema_dir: Path | None = None) -> str:
    """Hash named M0 schema bytes with Git-independent LF line endings.

    Git may materialize text files with CRLF on Windows. Normalizing only the
    line-ending convention keeps the schema bundle identity stable while all
    JSON content, whitespace within lines, names, and ordering remain hashed.
    """
    directory = schema_dir or _M0_SCHEMA_DIR
    hasher = hashlib.sha256()
    for name in _SCHEMA_NAMES:
        raw = (directory / name).read_bytes().replace(b"\r\n", b"\n")
        name_bytes = name.encode("utf-8")
        hasher.update(len(name_bytes).to_bytes(4, "big"))
        hasher.update(name_bytes)
        hasher.update(len(raw).to_bytes(8, "big"))
        hasher.update(raw)
    return hasher.hexdigest()


def compute_search_family_id(protocol: Mapping[str, Any]) -> str:
    """Derive the persistent family ID from the frozen root hypothesis/family."""
    source = protocol.get("source", {})
    hypothesis = protocol.get("hypothesis", {})
    candidate_set = protocol.get("candidate_set", {})
    return content_id({
        "root_hypothesis": hypothesis,
        "allowed_families": list(_ALLOWED_FAMILIES),
        "incumbent_control_id": candidate_set.get("incumbent_control_id"),
        "phase4_config_sha256": source.get("phase4_config_sha256"),
    })


def _protocol_semantic_payload(protocol: Mapping[str, Any]) -> dict[str, Any]:
    """Project identity fields away, including the derived manifest commitment.

    The attempt manifest contains attempt IDs, which contain protocol_id. Its
    digest therefore cannot participate in protocol_id without a hash cycle.
    The commitment is instead bound by the sealed-manifest event in the same
    atomic transaction. The attempt count, family, hypothesis, folds, costs,
    metrics, source, datasets, seeds, and resource limits remain in the ID.
    """
    payload = json.loads(json.dumps(protocol))
    payload.pop("protocol_id", None)
    payload.pop("created_at_utc", None)
    candidate_set = payload.get("candidate_set")
    if isinstance(candidate_set, dict):
        candidate_set.pop("attempt_manifest_sha256", None)
    return payload


def compute_protocol_id(protocol: Mapping[str, Any]) -> str:
    return content_id(_protocol_semantic_payload(protocol))


def prepare_protocol_identity(
    protocol: Mapping[str, Any], *, attempt_count: int | None = None
) -> dict[str, Any]:
    """Bind a protocol ID before candidate specs are serialized.

    ``attempt_manifest_sha256`` is set to a placeholder only while deriving the
    semantic protocol ID; registration replaces it with the actual sealed
    commitment in the same transaction.
    """
    prepared = json.loads(json.dumps(protocol))
    prepared.pop("protocol_id", None)
    if attempt_count is not None:
        prepared.setdefault("candidate_set", {})["attempt_count"] = attempt_count
    prepared.setdefault("candidate_set", {})["attempt_manifest_sha256"] = "0" * 64
    prepared["search_family_id"] = compute_search_family_id(prepared)
    prepared["protocol_id"] = compute_protocol_id(prepared)
    return prepared


def _candidate_semantic_payload(candidate: Mapping[str, Any]) -> dict[str, Any]:
    payload = json.loads(json.dumps(candidate))
    payload.pop("candidate_id", None)
    payload.pop("created_at_utc", None)
    payload.pop("display_name", None)
    return payload


def compute_candidate_id(candidate: Mapping[str, Any]) -> str:
    return content_id(_candidate_semantic_payload(candidate))


def compute_attempt_id(
    protocol_id: str,
    ordinal: int,
    assigned_family: str,
    candidate_spec_bytes_sha256: str,
) -> str:
    return content_id({
        "protocol_id": protocol_id,
        "ordinal": ordinal,
        "assigned_family": assigned_family,
        "candidate_spec_bytes_sha256": candidate_spec_bytes_sha256,
    })


def derive_seed(protocol_id: str, attempt_id: str, fold_id: str, metric_id: str) -> int:
    material = f"{protocol_id}|{attempt_id}|{fold_id}|{metric_id}|{_MASTER_SEED}".encode("utf-8")
    return int.from_bytes(hashlib.sha256(material).digest()[:8], "big", signed=False)


def _load_schema(name: str) -> dict[str, Any]:
    try:
        return json.loads((_M0_SCHEMA_DIR / name).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RegistryIntegrityError(f"cannot load authoritative Milestone 0 schema {name}: {exc}") from exc


def _validate(schema_name: str, value: Any, what: str) -> None:
    schema = _load_schema(schema_name)
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(value),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        rendered = "; ".join(
            f"{'.'.join(str(part) for part in error.absolute_path) or '<root>'}: {error.message}"
            for error in errors[:8]
        )
        raise ProtocolError(f"{what} violates {schema_name}: {rendered}")


def _parse_json_bytes(raw: bytes) -> Any:
    def reject_constant(value: str) -> None:
        raise ValueError(f"non-finite JSON constant {value} is prohibited")
    return json.loads(raw.decode("utf-8", errors="strict"), parse_constant=reject_constant)


def _utc_iso(value: datetime | None = None) -> str:
    value = value or datetime.now(timezone.utc)
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware UTC values")
    normalized = value.astimezone(timezone.utc)
    return normalized.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _event_hashes(
    sequence: int,
    event_time_utc: str,
    protocol_id: str,
    event_type: str,
    actor_type: str,
    payload: Mapping[str, Any],
    previous_event_sha256: str | None,
) -> dict[str, Any]:
    payload_hash = sha256_hex(canonical_json_bytes(payload))
    event_id_body = {
        "sequence": sequence,
        "event_time_utc": event_time_utc,
        "protocol_id": protocol_id,
        "event_type": event_type,
        "actor_type": actor_type,
        "payload_sha256": payload_hash,
        "previous_event_sha256": previous_event_sha256,
    }
    event_id = content_id(event_id_body)
    event: dict[str, Any] = {
        "schema_version": "athena.evolution.registry_event.v1",
        "sequence": sequence,
        "event_id": event_id,
        "event_time_utc": event_time_utc,
        "protocol_id": protocol_id,
        "event_type": event_type,
        "actor_type": actor_type,
        "payload_sha256": payload_hash,
        "previous_event_sha256": previous_event_sha256,
        "payload": dict(payload),
    }
    event["event_sha256"] = sha256_hex(canonical_json_bytes(event))
    return event


def _validate_event(event: Mapping[str, Any]) -> None:
    _validate("registry_event.schema.json", event, "registry event")
    payload = event["payload"]
    if sha256_hex(canonical_json_bytes(payload)) != event["payload_sha256"]:
        raise RegistryIntegrityError(f"event {event['sequence']} payload hash mismatch")
    event_id_body = {
        key: event[key]
        for key in (
            "sequence", "event_time_utc", "protocol_id", "event_type", "actor_type",
            "payload_sha256", "previous_event_sha256",
        )
    }
    if content_id(event_id_body) != event["event_id"]:
        raise RegistryIntegrityError(f"event {event['sequence']} event_id mismatch")
    body = {key: value for key, value in event.items() if key != "event_sha256"}
    if sha256_hex(canonical_json_bytes(body)) != event["event_sha256"]:
        raise RegistryIntegrityError(f"event {event['sequence']} event_sha256 mismatch")


def verify_event_stream(
    events: Iterable[Mapping[str, Any]], expected_tip_sha256: str | None = None
) -> VerificationResult:
    """Independently verify exported event records without opening the DB."""
    last_hash: str | None = None
    protocols: set[str] = set()
    attempts: set[str] = set()
    search_ids: set[str] = set()
    event_ids: set[str] = set()
    count = 0
    for expected_sequence, event in enumerate(events, start=1):
        _validate_event(event)
        if event["sequence"] != expected_sequence:
            raise RegistryIntegrityError(
                f"event sequence gap: expected {expected_sequence}, got {event['sequence']}"
            )
        if event["previous_event_sha256"] != last_hash:
            raise RegistryIntegrityError(f"event {expected_sequence} previous hash mismatch")
        if event["event_id"] in event_ids:
            raise RegistryIntegrityError(f"duplicate event id {event['event_id']}")
        event_ids.add(event["event_id"])
        payload = event["payload"]
        if event["event_type"] == "PROTOCOL_PRE_REGISTERED":
            protocol_id = event["protocol_id"]
            if protocol_id in protocols:
                raise RegistryIntegrityError(f"duplicate protocol id {protocol_id}")
            protocols.add(protocol_id)
            family_id = payload.get("protocol", {}).get("search_family_id")
            if family_id:
                search_ids.add(family_id)
        elif event["event_type"] == "CANDIDATE_ATTEMPT_REGISTERED":
            attempt_id = payload.get("attempt_id")
            if not isinstance(attempt_id, str) or attempt_id in attempts:
                raise RegistryIntegrityError(f"duplicate or missing attempt id {attempt_id!r}")
            attempts.add(attempt_id)
        last_hash = event["event_sha256"]
        count += 1
    if expected_tip_sha256 is not None and last_hash != expected_tip_sha256:
        raise RegistryIntegrityError("event-chain tip does not match the trusted expected hash")
    if len(search_ids) > 1:
        raise RegistryIntegrityError("registry event stream contains multiple search families")
    return VerificationResult(
        valid=True,
        event_count=count,
        protocol_count=len(protocols),
        attempt_count=len(attempts),
        search_family_id=next(iter(search_ids), None),
        tip_sha256=last_hash,
    )


class ExperimentRegistry:
    """Append-only SQLite-backed registry for one persistent search family."""

    _EVENTS = {
        "PROTOCOL_PRE_REGISTERED",
        "CANDIDATE_ATTEMPT_REGISTERED",
        "CANDIDATE_SET_SEALED",
        "CANDIDATE_REGISTERED",
        "CANDIDATE_INVALID",
        "EVALUATION_STARTED",
        "EVALUATION_COMPLETED",
        "EVALUATION_FAILED",
        "CANDIDATE_EVALUATION_SKIPPED",
        "CANDIDATE_TECHNICAL_FAILURE",
        "CANDIDATE_INSUFFICIENT_EVIDENCE",
        "CANDIDATE_REJECTED",
        "NO_QUALIFYING_CANDIDATE",
        "RESEARCH_ADVANCED_PENDING_PROSPECTIVE",
        "APPROVED_FOR_FURTHER_RESEARCH",
        "PROTOCOL_CLOSED",
    }

    def __init__(self, root: str | Path):
        unresolved = Path(root).expanduser().absolute()
        if unresolved.is_symlink():
            raise RegistryError("registry root must not be a symlink")
        self.root = unresolved.resolve(strict=True)
        if not self.root.is_dir():
            raise RegistryError("registry root must be a real directory, not a symlink")
        self.database_path = self.root / "registry.sqlite3"
        if self.database_path.is_symlink() or not self.database_path.is_file():
            raise RegistryError("registry database is missing or is a symlink")
        connection = sqlite3.connect(self.database_path.as_uri() + "?mode=ro", uri=True, timeout=30)
        try:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version != 1:
                raise RegistryIntegrityError(f"unsupported registry storage version {version}")
            triggers = {
                row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='trigger'"
                )
            }
            required = {
                "events_no_update", "events_no_delete",
                "attempt_specs_no_update", "attempt_specs_no_delete",
            }
            if not required.issubset(triggers):
                raise RegistryIntegrityError("append-only database triggers are missing")
        finally:
            connection.close()

    @classmethod
    def create(cls, root: str | Path) -> "ExperimentRegistry":
        """Create an empty registry directory; never reuse or overwrite a path."""
        path = Path(root).expanduser().absolute()
        if path.exists() or path.is_symlink():
            raise RegistryError(f"refusing to overwrite existing registry path: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.mkdir()
        db_path = path / "registry.sqlite3"
        try:
            connection = sqlite3.connect(db_path, timeout=30, isolation_level=None)
            try:
                connection.execute("PRAGMA journal_mode=DELETE")
                connection.execute("PRAGMA synchronous=FULL")
                connection.execute("PRAGMA foreign_keys=ON")
                connection.executescript(
                    """
                    CREATE TABLE events (
                        sequence INTEGER PRIMARY KEY CHECK(sequence > 0),
                        event_id TEXT NOT NULL UNIQUE,
                        event_sha256 TEXT NOT NULL UNIQUE,
                        protocol_id TEXT NOT NULL,
                        event_type TEXT NOT NULL,
                        event_json BLOB NOT NULL
                    );
                    CREATE TABLE attempt_specs (
                        attempt_id TEXT PRIMARY KEY,
                        candidate_spec_bytes_sha256 TEXT NOT NULL,
                        spec_bytes BLOB NOT NULL
                    );
                    CREATE TRIGGER events_no_update BEFORE UPDATE ON events
                    BEGIN SELECT RAISE(ABORT, 'registry events are append-only'); END;
                    CREATE TRIGGER events_no_delete BEFORE DELETE ON events
                    BEGIN SELECT RAISE(ABORT, 'registry events are append-only'); END;
                    CREATE TRIGGER attempt_specs_no_update BEFORE UPDATE ON attempt_specs
                    BEGIN SELECT RAISE(ABORT, 'registered candidate bytes are immutable'); END;
                    CREATE TRIGGER attempt_specs_no_delete BEFORE DELETE ON attempt_specs
                    BEGIN SELECT RAISE(ABORT, 'registered candidate bytes are immutable'); END;
                    PRAGMA user_version=1;
                    """
                )
            finally:
                connection.close()
            return cls(path)
        except Exception:
            # This path was created exclusively by this call. Cleanup never
            # touches a pre-existing report or registry.
            try:
                if db_path.exists():
                    db_path.unlink()
                path.rmdir()
            except OSError:
                pass
            raise

    def _connect(self, *, readonly: bool = False) -> sqlite3.Connection:
        if readonly:
            uri = self.database_path.as_uri() + "?mode=ro"
            connection = sqlite3.connect(uri, uri=True, timeout=30, isolation_level=None)
        else:
            connection = sqlite3.connect(self.database_path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        if readonly:
            connection.execute("PRAGMA query_only=ON")
        else:
            connection.execute("PRAGMA synchronous=FULL")
        return connection

    @staticmethod
    def _append_event(
        connection: sqlite3.Connection,
        *,
        sequence: int,
        protocol_id: str,
        event_type: str,
        payload: Mapping[str, Any],
        actor_type: str = "system",
        event_time_utc: str | None = None,
    ) -> dict[str, Any]:
        previous_row = connection.execute(
            "SELECT event_sha256 FROM events ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        previous = previous_row[0] if previous_row else None
        event = _event_hashes(
            sequence=sequence,
            event_time_utc=event_time_utc or _now_utc(),
            protocol_id=protocol_id,
            event_type=event_type,
            actor_type=actor_type,
            payload=payload,
            previous_event_sha256=previous,
        )
        _validate_event(event)
        try:
            connection.execute(
                "INSERT INTO events(sequence,event_id,event_sha256,protocol_id,event_type,event_json) "
                "VALUES(?,?,?,?,?,?)",
                (sequence, event["event_id"], event["event_sha256"], protocol_id,
                 event_type, canonical_json_bytes(event)),
            )
        except sqlite3.IntegrityError as exc:
            raise DuplicateIdentifierError(f"event identifier or sequence already exists: {exc}") from exc
        return event

    def _events(self, connection: sqlite3.Connection) -> list[dict[str, Any]]:
        events = [
            json.loads(bytes(row["event_json"]).decode("utf-8"))
            for row in connection.execute("SELECT event_json FROM events ORDER BY sequence")
        ]
        verify_event_stream(events)
        return events

    def _family_protocols(self, events: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
        protocols: dict[str, dict[str, Any]] = {}
        for event in events:
            if event["event_type"] == "PROTOCOL_PRE_REGISTERED":
                protocol = event["payload"].get("protocol")
                if not isinstance(protocol, dict):
                    raise RegistryIntegrityError("protocol registration event is missing protocol document")
                if event["protocol_id"] in protocols:
                    raise DuplicateIdentifierError(f"duplicate protocol ID {event['protocol_id']}")
                protocols[event["protocol_id"]] = protocol
        return protocols

    def _next_sequence(self, connection: sqlite3.Connection) -> int:
        row = connection.execute("SELECT COALESCE(MAX(sequence),0)+1 FROM events").fetchone()
        return int(row[0])

    def register_protocol_with_attempts(
        self,
        protocol: Mapping[str, Any],
        submissions: Sequence[AttemptSubmission],
        *,
        fault_after_event: int | None = None,
    ) -> tuple[str, tuple[str, ...], str]:
        """Atomically pre-register protocol, attempts, and sealed manifest.

        Candidate bytes are hashed and stored without parsing. Validation or
        evaluation cannot happen through this operation. A crash before commit
        exposes none of the transaction; a successful commit exposes the full
        attempt batch and its sealed manifest.
        """
        if not submissions:
            raise ProtocolError("a registered candidate set must contain at least one attempt")
        if len(submissions) > _MAX_TOTAL_ATTEMPTS:
            raise SearchFamilyLimitError("protocol attempt set exceeds the cumulative family limit")
        protocol_doc = json.loads(json.dumps(protocol))
        protocol_doc.pop("protocol_id", None)
        if protocol_doc.get("candidate_set", {}).get("attempt_count") != len(submissions):
            raise ProtocolError("protocol candidate_set.attempt_count must match this sealed attempt batch")
        protocol_doc["protocol_id"] = compute_protocol_id(protocol_doc)
        expected_search_id = compute_search_family_id(protocol_doc)
        if protocol_doc.get("search_family_id") != expected_search_id:
            raise ProtocolError("search_family_id does not match the frozen root-hypothesis derivation")

        connection = self._connect()
        staged_events = 0
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing_events = self._events(connection)
            protocols = self._family_protocols(existing_events)
            if protocol_doc["protocol_id"] in protocols:
                raise DuplicateIdentifierError(f"protocol ID already registered: {protocol_doc['protocol_id']}")
            if any(
                event["event_type"] == "NO_QUALIFYING_CANDIDATE"
                and event["protocol_id"] in protocols
                and protocols[event["protocol_id"]].get("search_family_id") == expected_search_id
                for event in existing_events
            ):
                raise RegistryError("the persistent search family is terminal; no later attempts are allowed")

            family_protocols = [
                value for value in protocols.values()
                if value.get("search_family_id") == expected_search_id
            ]
            revisions = [int(item["protocol_revision"]) for item in family_protocols]
            parent_ids = protocol_doc.get("protocol_parent_ids", [])
            if not family_protocols:
                if int(protocol_doc.get("protocol_revision", 0)) != 1 or parent_ids:
                    raise ProtocolError("root protocol must be revision 1 with no parent protocols")
            else:
                if not parent_ids or any(parent not in protocols for parent in parent_ids):
                    raise ProtocolError("protocol revision must reference registered parent protocol IDs")
                if any(protocols[parent].get("search_family_id") != expected_search_id for parent in parent_ids):
                    raise ProtocolError("protocol parent belongs to a different search family")
                if int(protocol_doc.get("protocol_revision", 0)) != max(revisions) + 1:
                    raise ProtocolError("protocol revision must increment the persistent family revision")

            registered_attempts = [
                event for event in existing_events
                if event["event_type"] == "CANDIDATE_ATTEMPT_REGISTERED"
                and event["payload"].get("search_family_id") == expected_search_id
            ]
            if len(registered_attempts) + len(submissions) > _MAX_TOTAL_ATTEMPTS:
                raise SearchFamilyLimitError("cumulative persistent search-family attempt ceiling is 36")
            family_counts = Counter(event["payload"]["assigned_family"] for event in registered_attempts)
            for submission in submissions:
                if submission.assigned_family not in _ALLOWED_FAMILIES:
                    raise ProtocolError(f"unsupported assigned family {submission.assigned_family!r}")
                if submission.assigned_family not in protocol_doc.get("families", []):
                    raise ProtocolError("submitted attempt family is not registered by this protocol")
                if submission.origin not in {
                    "pre_registered_generator_slot", "manually_submitted_for_registered_slot"
                }:
                    raise ProtocolError(f"unsupported attempt origin {submission.origin!r}")
                if not isinstance(submission.candidate_spec_bytes, bytes):
                    raise TypeError("candidate_spec_bytes must be bytes")
                if len(submission.candidate_spec_bytes) > _MAX_ARTIFACT_BYTES_PER_ATTEMPT:
                    raise SearchFamilyLimitError("candidate specification exceeds the frozen per-attempt byte cap")
                family_counts[submission.assigned_family] += 1
                if family_counts[submission.assigned_family] > _MAX_PER_FAMILY:
                    raise SearchFamilyLimitError(
                        f"cumulative {submission.assigned_family} attempt ceiling is 12"
                    )

            start_ordinal = len(registered_attempts) + 1
            attempt_rows: list[dict[str, Any]] = []
            attempt_bytes: dict[str, bytes] = {}
            created_at = _now_utc()
            for offset, submission in enumerate(submissions):
                raw = submission.candidate_spec_bytes
                raw_hash = sha256_hex(raw)
                ordinal = start_ordinal + offset
                attempt_id = compute_attempt_id(
                    protocol_doc["protocol_id"], ordinal, submission.assigned_family, raw_hash
                )
                if any(row["attempt_id"] == attempt_id for row in attempt_rows) or any(
                    event["event_type"] == "CANDIDATE_ATTEMPT_REGISTERED"
                    and event["payload"].get("attempt_id") == attempt_id
                    for event in existing_events
                ):
                    raise DuplicateIdentifierError(f"attempt ID already registered: {attempt_id}")
                attempt_rows.append({
                    "attempt_id": attempt_id,
                    "ordinal": ordinal,
                    "assigned_family": submission.assigned_family,
                    "candidate_spec_bytes_sha256": raw_hash,
                    "origin": submission.origin,
                    "submitted_at_utc": created_at,
                    "status_at_seal": "SUBMITTED_UNVALIDATED",
                })
                attempt_bytes[attempt_id] = raw

            manifest = {
                "schema_version": "athena.evolution.candidate_attempt_manifest.v1",
                "protocol_id": protocol_doc["protocol_id"],
                "search_family_id": expected_search_id,
                "attempt_id_algorithm": "sha256_rfc8785_jcs_protocol_global_search_ordinal_family_raw_sha256_v1",
                "created_at_utc": created_at,
                "sealed_at_utc": created_at,
                "attempt_count": len(attempt_rows),
                "attempts": attempt_rows,
            }
            _validate("candidate_attempt_manifest.schema.json", manifest, "candidate attempt manifest")
            manifest_hash = sha256_hex(canonical_json_bytes(manifest))

            protocol_doc["candidate_set"]["attempt_count"] = len(attempt_rows)
            protocol_doc["candidate_set"]["attempt_manifest_sha256"] = manifest_hash
            protocol_doc["candidate_set"]["registered_before_evaluation"] = True
            protocol_doc["candidate_set"]["includes_invalid_and_unevaluated_attempts"] = True
            # protocol_id excludes only the derived attempt-manifest digest; all
            # numeric and decision-affecting policy fields remain committed.
            if compute_protocol_id(protocol_doc) != protocol_doc["protocol_id"]:
                raise RegistryIntegrityError("protocol identity changed while binding the attempt manifest")
            _validate("experiment_protocol.schema.json", protocol_doc, "experiment protocol")
            if protocol_doc["source"]["schema_bundle_sha256"] != schema_bundle_sha256():
                raise ProtocolError("protocol schema_bundle_sha256 does not match the frozen M0 schema bytes")

            sequence = self._next_sequence(connection)
            event = self._append_event(
                connection, sequence=sequence, protocol_id=protocol_doc["protocol_id"],
                event_type="PROTOCOL_PRE_REGISTERED",
                payload={"protocol_id": protocol_doc["protocol_id"], "protocol": protocol_doc},
            )
            staged_events += 1
            if fault_after_event == staged_events:
                raise RuntimeError("injected interruption during atomic registration")
            sequence += 1

            for row in attempt_rows:
                payload = {
                    **row,
                    "protocol_id": protocol_doc["protocol_id"],
                    "search_family_id": expected_search_id,
                }
                event = self._append_event(
                    connection, sequence=sequence, protocol_id=protocol_doc["protocol_id"],
                    event_type="CANDIDATE_ATTEMPT_REGISTERED", payload=payload,
                )
                connection.execute(
                    "INSERT INTO attempt_specs(attempt_id,candidate_spec_bytes_sha256,spec_bytes) VALUES(?,?,?)",
                    (row["attempt_id"], row["candidate_spec_bytes_sha256"], attempt_bytes[row["attempt_id"]]),
                )
                staged_events += 1
                if fault_after_event == staged_events:
                    raise RuntimeError("injected interruption during atomic registration")
                sequence += 1

            sealed_payload = {
                "protocol_id": protocol_doc["protocol_id"],
                "search_family_id": expected_search_id,
                "attempt_manifest_sha256": manifest_hash,
                "attempt_count": len(attempt_rows),
                "attempt_manifest": manifest,
            }
            self._append_event(
                connection, sequence=sequence, protocol_id=protocol_doc["protocol_id"],
                event_type="CANDIDATE_SET_SEALED", payload=sealed_payload,
            )
            staged_events += 1
            if fault_after_event == staged_events:
                raise RuntimeError("injected interruption before registry transaction commit")
            connection.execute("COMMIT")
            return protocol_doc["protocol_id"], tuple(row["attempt_id"] for row in attempt_rows), manifest_hash
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    def _protocol_and_attempt(self, attempt_id: str) -> tuple[dict[str, Any], dict[str, Any], bytes, str]:
        connection = self._connect(readonly=True)
        try:
            events = self._events(connection)
            registered = next((
                event for event in events
                if event["event_type"] == "CANDIDATE_ATTEMPT_REGISTERED"
                and event["payload"].get("attempt_id") == attempt_id
            ), None)
            if registered is None:
                raise RegistryError(f"unknown attempt ID {attempt_id}")
            protocol_event = next((
                event for event in events
                if event["event_type"] == "PROTOCOL_PRE_REGISTERED"
                and event["protocol_id"] == registered["protocol_id"]
            ), None)
            if protocol_event is None:
                raise RegistryIntegrityError("attempt references a missing protocol")
            row = connection.execute(
                "SELECT candidate_spec_bytes_sha256,spec_bytes FROM attempt_specs WHERE attempt_id=?",
                (attempt_id,),
            ).fetchone()
            if row is None:
                raise RegistryIntegrityError(f"attempt {attempt_id} has no immutable candidate bytes")
            raw = bytes(row["spec_bytes"])
            digest = sha256_hex(raw)
            if digest != row["candidate_spec_bytes_sha256"] or digest != registered["payload"]["candidate_spec_bytes_sha256"]:
                raise RegistryIntegrityError(f"attempt {attempt_id} candidate bytes hash mismatch")
            return protocol_event["payload"]["protocol"], registered["payload"], raw, registered["protocol_id"]
        finally:
            connection.close()

    def validate_attempt(self, attempt_id: str) -> str:
        """Validate an already-registered payload and append valid/invalid event."""
        protocol, registration, raw, protocol_id = self._protocol_and_attempt(attempt_id)
        state = self.attempt_states().get(attempt_id)
        if state != "SUBMITTED":
            raise InvalidTransitionError(f"attempt validation requires SUBMITTED, found {state}")
        try:
            candidate = _parse_json_bytes(raw)
            _validate("candidate_spec.schema.json", candidate, "candidate specification")
            if candidate["experiment_id"] != protocol_id:
                raise ProtocolError("candidate experiment_id does not match its registered protocol_id")
            if candidate["family"] != registration["assigned_family"]:
                raise ProtocolError("candidate family differs from the family sealed in its attempt manifest")
            if candidate["candidate_id"] != compute_candidate_id(candidate):
                raise ProtocolError("candidate_id does not match the canonical semantic payload")
            baseline = candidate["baseline"]
            source = protocol["source"]
            expected_hashes = {
                "source_commit": source["commit"],
                "strategy_source_sha256": source["strategy_source_sha256"],
                "regime_source_sha256": source["regime_source_sha256"],
                "execution_source_sha256": source["execution_source_sha256"],
                "phase4_config_sha256": source["phase4_config_sha256"],
            }
            for key, expected in expected_hashes.items():
                if baseline.get(key) != expected:
                    raise ProtocolError(f"candidate baseline {key} differs from the locked protocol")
            known_candidate_ids = {
                event["payload"].get("candidate_id")
                for event in self.events()
                if event["event_type"] == "CANDIDATE_REGISTERED"
            }
            for parent in candidate.get("parents", []):
                if parent["reference_type"] == "candidate" and parent["reference_id"] not in known_candidate_ids:
                    raise ProtocolError(f"candidate lineage references unknown parent {parent['reference_id']}")
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError, ProtocolError, KeyError, TypeError) as exc:
            self._append_attempt_event(
                protocol_id, attempt_id, "CANDIDATE_INVALID",
                {"attempt_id": attempt_id, "reason_code": "INVALID_SPEC_OR_LINEAGE",
                 "detail": str(exc)[:2000],
                 "candidate_spec_bytes_sha256": registration["candidate_spec_bytes_sha256"]},
            )
            return "INVALID_CANDIDATE"
        self._append_attempt_event(
            protocol_id, attempt_id, "CANDIDATE_REGISTERED",
            {"attempt_id": attempt_id, "candidate_id": candidate["candidate_id"],
             "family": candidate["family"], "parents": candidate["parents"],
             "candidate_spec_bytes_sha256": registration["candidate_spec_bytes_sha256"]},
        )
        return "VALIDATED"

    def _append_attempt_event(
        self,
        protocol_id: str,
        attempt_id: str,
        event_type: str,
        payload: Mapping[str, Any],
        *,
        actor_type: str = "system",
    ) -> dict[str, Any]:
        if event_type not in self._EVENTS:
            raise RegistryError(f"event type is outside the Milestone 1 registry surface: {event_type}")
        if event_type in {"HOLDOUT_SEALED", "HOLDOUT_RELEASED", "HOLDOUT_CONSUMED"}:
            raise HoldoutAccessError("prospective holdout operations are not part of Milestone 1")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            events = self._events(connection)
            protocol_row = next((
                event for event in events
                if event["event_type"] == "PROTOCOL_PRE_REGISTERED"
                and event["protocol_id"] == protocol_id
            ), None)
            if protocol_row is None:
                raise RegistryError(f"unknown protocol ID {protocol_id}")
            states = _reconstruct_attempt_states(events)
            current = states.get(attempt_id)
            _assert_transition(event_type, current, attempt_id)
            sequence = self._next_sequence(connection)
            event = self._append_event(
                connection, sequence=sequence, protocol_id=protocol_id,
                event_type=event_type, payload=payload, actor_type=actor_type,
            )
            connection.execute("COMMIT")
            return event
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    def record_evaluation_started(
        self, attempt_id: str, fold_ids: Sequence[str], metric_ids: Sequence[str]
    ) -> dict[str, Any]:
        protocol, _registration, _raw, protocol_id = self._protocol_and_attempt(attempt_id)
        if protocol["research_classification"] == "prospective_holdout_confirmatory":
            raise HoldoutAccessError("Milestone 1 cannot start or access a holdout evaluation")
        seed_records = [
            {"fold_id": fold_id, "metric_id": metric_id,
             # Preserve every bit of the unsigned 64-bit D0 seed. JCS numbers
             # are IEEE-754; the canonical decimal string avoids rounding.
             "seed_u64_decimal": str(derive_seed(protocol_id, attempt_id, fold_id, metric_id))}
            for fold_id in fold_ids for metric_id in metric_ids
        ]
        return self._append_attempt_event(
            protocol_id, attempt_id, "EVALUATION_STARTED",
            {"attempt_id": attempt_id, "seed_policy": protocol["seed_policy"],
             "seed_records": seed_records, "source_commit": protocol["source"]["commit"],
             "worktree_clean": protocol["source"]["worktree_clean"],
             "dataset_hashes": {key: value for key, value in protocol["data"].items() if key.endswith("sha256")},
             "configuration_hashes": {
                 key: protocol["source"][key]
                 for key in (
                     "strategy_source_sha256", "regime_source_sha256",
                     "execution_source_sha256", "phase4_config_sha256",
                     "schema_bundle_sha256",
                 )
             }},
        )

    def complete_evaluation(
        self,
        attempt_id: str,
        *,
        result_sha256: str,
        wall_seconds: int,
        peak_memory_mb: int,
        artifact_bytes: int,
        worker_count: int = 1,
        confirmatory_p_values: Mapping[str, float] | None = None,
    ) -> str:
        protocol, _registration, _raw, protocol_id = self._protocol_and_attempt(attempt_id)
        payload = {
            "attempt_id": attempt_id,
            "result_sha256": result_sha256,
            "resource_usage": {
                "wall_seconds": wall_seconds,
                "peak_memory_mb": peak_memory_mb,
                "artifact_bytes": artifact_bytes,
                "worker_count": worker_count,
            },
            "confirmatory_p_values": dict(confirmatory_p_values or {}),
        }
        for hypothesis, p_value in payload["confirmatory_p_values"].items():
            if hypothesis not in _CONFIRMATORY_HYPOTHESES:
                raise ProtocolError(f"unknown confirmatory hypothesis {hypothesis!r}")
            if isinstance(p_value, bool) or not isinstance(p_value, (int, float)):
                raise ProtocolError(f"p-value for {hypothesis} must be numeric")
            if not math.isfinite(p_value) or not 0 <= p_value <= 1:
                raise ProtocolError(f"p-value for {hypothesis} must be finite and in [0,1]")
        if len(result_sha256) != 64 or any(c not in "0123456789abcdef" for c in result_sha256):
            raise ProtocolError("result_sha256 must be lowercase hexadecimal SHA-256")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            events = self._events(connection)
            states = _reconstruct_attempt_states(events)
            _assert_transition("EVALUATION_COMPLETED", states.get(attempt_id), attempt_id)
            violation = self._resource_violation(
                connection, protocol["search_family_id"], payload["resource_usage"]
            )
            event_type = "CANDIDATE_TECHNICAL_FAILURE" if violation else "EVALUATION_COMPLETED"
            if violation:
                payload = {**payload, "reason_code": "RESOURCE_LIMIT", "detail": violation}
            event = self._append_event(
                connection, sequence=self._next_sequence(connection), protocol_id=protocol_id,
                event_type=event_type, payload=payload,
            )
            connection.execute("COMMIT")
            return "TECHNICAL_EVALUATION_FAILURE" if violation else "COMPLETED"
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    def _resource_violation(
        self, connection: sqlite3.Connection, search_family_id: str, usage: Mapping[str, int]
    ) -> str | None:
        checks = (
            ("wall_seconds", 0, _MAX_WALL_SECONDS_PER_ATTEMPT),
            ("peak_memory_mb", 0, _MAX_MEMORY_MB),
            ("artifact_bytes", 0, _MAX_ARTIFACT_BYTES_PER_ATTEMPT),
            ("worker_count", 1, _MAX_WORKERS),
        )
        for key, minimum, maximum in checks:
            value = usage.get(key)
            if not isinstance(value, int) or isinstance(value, bool) or value < minimum or value > maximum:
                return f"{key} must be between {minimum} and {maximum}"
        aggregate_wall = 0
        aggregate_artifacts = 0
        protocols = {
            event["protocol_id"]: event["payload"]["protocol"]
            for event in self._events(connection)
            if event["event_type"] == "PROTOCOL_PRE_REGISTERED"
            and event["payload"]["protocol"].get("search_family_id") == search_family_id
        }
        for event in self._events(connection):
            if event["event_type"] not in {"EVALUATION_COMPLETED", "CANDIDATE_TECHNICAL_FAILURE"}:
                continue
            if event["protocol_id"] not in protocols:
                continue
            existing = event["payload"].get("resource_usage", {})
            aggregate_wall += int(existing.get("wall_seconds", 0))
            aggregate_artifacts += int(existing.get("artifact_bytes", 0))
        if aggregate_wall + usage["wall_seconds"] > _MAX_WALL_SECONDS_SEARCH_FAMILY:
            return "cumulative search-family wall-time cap of 86,400 seconds exceeded"
        if aggregate_artifacts + usage["artifact_bytes"] > _MAX_ARTIFACT_BYTES_SEARCH_FAMILY:
            return "cumulative search-family artifact cap of 10 GiB exceeded"
        return None

    def skip_attempt(self, attempt_id: str, reason: str) -> dict[str, Any]:
        protocol, _registration, _raw, protocol_id = self._protocol_and_attempt(attempt_id)
        return self._append_attempt_event(
            protocol_id, attempt_id, "CANDIDATE_EVALUATION_SKIPPED",
            {"attempt_id": attempt_id, "reason": reason[:2000]},
        )

    def fail_attempt(self, attempt_id: str, reason_code: str, detail: str = "") -> dict[str, Any]:
        _protocol, _registration, _raw, protocol_id = self._protocol_and_attempt(attempt_id)
        return self._append_attempt_event(
            protocol_id, attempt_id, "CANDIDATE_TECHNICAL_FAILURE",
            {"attempt_id": attempt_id, "reason_code": reason_code, "detail": detail[:2000]},
        )

    def mark_insufficient(self, attempt_id: str, reason_code: str, detail: str = "") -> dict[str, Any]:
        _protocol, _registration, _raw, protocol_id = self._protocol_and_attempt(attempt_id)
        return self._append_attempt_event(
            protocol_id, attempt_id, "CANDIDATE_INSUFFICIENT_EVIDENCE",
            {"attempt_id": attempt_id, "reason_code": reason_code, "detail": detail[:2000]},
        )

    def reject_attempt(self, attempt_id: str, reason_code: str, detail: str = "") -> dict[str, Any]:
        _protocol, _registration, _raw, protocol_id = self._protocol_and_attempt(attempt_id)
        return self._append_attempt_event(
            protocol_id, attempt_id, "CANDIDATE_REJECTED",
            {"attempt_id": attempt_id, "reason_code": reason_code, "detail": detail[:2000]},
        )

    def advance_for_research(self, attempt_id: str, evidence_sha256: str) -> dict[str, Any]:
        _protocol, _registration, _raw, protocol_id = self._protocol_and_attempt(attempt_id)
        return self._append_attempt_event(
            protocol_id, attempt_id, "RESEARCH_ADVANCED_PENDING_PROSPECTIVE",
            {"attempt_id": attempt_id, "evidence_sha256": evidence_sha256},
        )

    def approve_further_research(self, attempt_id: str, reviewer: str, rationale: str) -> dict[str, Any]:
        _protocol, _registration, _raw, protocol_id = self._protocol_and_attempt(attempt_id)
        return self._append_attempt_event(
            protocol_id, attempt_id, "APPROVED_FOR_FURTHER_RESEARCH",
            {"attempt_id": attempt_id, "reviewer": reviewer, "rationale": rationale[:2000]},
            actor_type="reviewer",
        )

    def close_with_no_qualifying_candidate(self) -> dict[str, Any]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            events = self._events(connection)
            protocols = self._family_protocols(events)
            if not protocols:
                raise RegistryError("cannot close an empty registry")
            latest_protocol_id, latest = max(
                protocols.items(), key=lambda item: int(item[1]["protocol_revision"])
            )
            family_id = latest["search_family_id"]
            family_attempt_events = [
                event for event in events
                if event["event_type"] == "CANDIDATE_ATTEMPT_REGISTERED"
                and event["payload"].get("search_family_id") == family_id
            ]
            states = _reconstruct_attempt_states(events)
            attempt_ids = [event["payload"]["attempt_id"] for event in family_attempt_events]
            if not attempt_ids or any(states.get(attempt_id) not in _TERMINAL_STATES for attempt_id in attempt_ids):
                raise InvalidTransitionError("NO_QUALIFYING_CANDIDATE requires every sealed attempt terminal")
            if any(states[attempt_id] in {"TECHNICAL_EVALUATION_FAILURE", "UNEVALUATED"} for attempt_id in attempt_ids):
                raise InvalidTransitionError("technical failures or unevaluated attempts preclude a valid no-qualifier result")
            if any(states[attempt_id] == "RESEARCH_ADVANCED_PENDING_PROSPECTIVE" for attempt_id in attempt_ids):
                raise InvalidTransitionError("a research-advanced candidate precludes NO_QUALIFYING_CANDIDATE")
            if any(event["event_type"] == "NO_QUALIFYING_CANDIDATE" for event in events):
                raise InvalidTransitionError("search family already has a terminal no-qualifier outcome")
            event = self._append_event(
                connection,
                sequence=self._next_sequence(connection),
                protocol_id=latest_protocol_id,
                event_type="NO_QUALIFYING_CANDIDATE",
                payload={
                    "terminal_outcome": "NO_QUALIFYING_CANDIDATE",
                    "run_status": "COMPLETED_VALID",
                    "qualifying_candidate_count": 0,
                    "registered_attempt_count": len(attempt_ids),
                    "all_attempts_resolved_terminally": True,
                    "technical_failure_count": 0,
                    "unevaluated_count": 0,
                    "reason": "no_candidate_met_all_preregistered_qualification_gates",
                    "acceptance_criteria_unchanged": True,
                },
            )
            connection.execute("COMMIT")
            return event
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    def append_holdout_event(self, *_args: Any, **_kwargs: Any) -> None:
        raise HoldoutAccessError("holdout sealing, release, and consumption are outside Milestone 1")

    def events(self) -> list[dict[str, Any]]:
        connection = self._connect(readonly=True)
        try:
            return self._events(connection)
        finally:
            connection.close()

    def attempt_states(self) -> dict[str, str]:
        return _reconstruct_attempt_states(self.events())

    def attempt_bytes(self, attempt_id: str) -> bytes:
        _protocol, _registration, raw, _protocol_id = self._protocol_and_attempt(attempt_id)
        return raw

    def multiplicity_entries(self) -> list[dict[str, Any]]:
        """Return every registered attempt × confirmatory hypothesis.

        Missing results and terminal invalid/failed/skipped/insufficient
        attempts conservatively receive p=1 and can never leave the family.
        """
        events = self.events()
        states = _reconstruct_attempt_states(events)
        results = {
            event["payload"].get("attempt_id"): event["payload"].get("confirmatory_p_values", {})
            for event in events if event["event_type"] == "EVALUATION_COMPLETED"
        }
        rows: list[dict[str, Any]] = []
        for event in events:
            if event["event_type"] != "CANDIDATE_ATTEMPT_REGISTERED":
                continue
            payload = event["payload"]
            attempt_id = payload["attempt_id"]
            p_values = results.get(attempt_id, {})
            terminal_or_missing = states.get(attempt_id) in {
                "INVALID_CANDIDATE", "UNEVALUATED", "TECHNICAL_EVALUATION_FAILURE",
                "INSUFFICIENT_EVIDENCE",
            }
            for hypothesis in _CONFIRMATORY_HYPOTHESES:
                value = 1.0 if terminal_or_missing else p_values.get(hypothesis, 1.0)
                rows.append({
                    "search_family_id": payload["search_family_id"],
                    "protocol_id": event["protocol_id"],
                    "attempt_id": attempt_id,
                    "ordinal": payload["ordinal"],
                    "hypothesis": hypothesis,
                    "p_value": float(value),
                    "placeholder": terminal_or_missing or hypothesis not in p_values,
                    "status": states.get(attempt_id),
                })
        return sorted(rows, key=lambda row: (row["ordinal"], row["hypothesis"]))

    def export_events_jsonl(self) -> bytes:
        return b"".join(canonical_json_bytes(event) + b"\n" for event in self.events())

    def verify(self, expected_tip_sha256: str | None = None) -> VerificationResult:
        return verify_registry(self.root, expected_tip_sha256=expected_tip_sha256)


_TERMINAL_STATES = {
    "INVALID_CANDIDATE",
    "UNEVALUATED",
    "TECHNICAL_EVALUATION_FAILURE",
    "INSUFFICIENT_EVIDENCE",
    "REJECTED",
    "RESEARCH_ADVANCED_PENDING_PROSPECTIVE",
}


def _assert_transition(event_type: str, current: str | None, attempt_id: str) -> None:
    allowed: dict[str, set[str]] = {
        "CANDIDATE_REGISTERED": {"SUBMITTED"},
        "CANDIDATE_INVALID": {"SUBMITTED"},
        "EVALUATION_STARTED": {"VALIDATED"},
        "EVALUATION_COMPLETED": {"RUNNING"},
        "CANDIDATE_EVALUATION_SKIPPED": {"SUBMITTED", "VALIDATED"},
        "CANDIDATE_TECHNICAL_FAILURE": {"VALIDATED", "RUNNING"},
        "CANDIDATE_INSUFFICIENT_EVIDENCE": {"COMPLETED"},
        "CANDIDATE_REJECTED": {"COMPLETED"},
        "RESEARCH_ADVANCED_PENDING_PROSPECTIVE": {"COMPLETED"},
        "APPROVED_FOR_FURTHER_RESEARCH": {"RESEARCH_ADVANCED_PENDING_PROSPECTIVE"},
    }
    if event_type not in allowed:
        raise RegistryError(f"event {event_type} is not an attempt-transition event")
    if current not in allowed[event_type]:
        raise InvalidTransitionError(
            f"cannot append {event_type} for {attempt_id} from state {current!r}"
        )


def _reconstruct_attempt_states(events: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    states: dict[str, str] = {}
    protocols = {
        event["protocol_id"]: event["payload"].get("protocol", {})
        for event in events if event["event_type"] == "PROTOCOL_PRE_REGISTERED"
    }
    attempt_protocols: dict[str, str] = {}
    sealed_protocols: set[str] = set()
    for event in events:
        event_type = event["event_type"]
        payload = event["payload"]
        if event_type == "CANDIDATE_ATTEMPT_REGISTERED":
            attempt_id = payload.get("attempt_id")
            if attempt_id in states:
                raise RegistryIntegrityError(f"duplicate attempt registration: {attempt_id}")
            if event["protocol_id"] not in protocols:
                raise RegistryIntegrityError(f"attempt references unregistered protocol {event['protocol_id']}")
            if event["protocol_id"] in sealed_protocols:
                raise RegistryIntegrityError("attempt was registered after its protocol manifest was sealed")
            states[attempt_id] = "SUBMITTED"
            attempt_protocols[attempt_id] = event["protocol_id"]
        elif event_type == "CANDIDATE_SET_SEALED":
            if event["protocol_id"] in sealed_protocols:
                raise RegistryIntegrityError("protocol has more than one candidate-set seal")
            manifest = payload.get("attempt_manifest")
            if not isinstance(manifest, dict):
                raise RegistryIntegrityError("candidate-set seal is missing its attempt manifest")
            if payload.get("protocol_id") != event["protocol_id"]:
                raise RegistryIntegrityError("candidate-set seal references the wrong protocol")
            if manifest.get("protocol_id") != event["protocol_id"]:
                raise RegistryIntegrityError("manifest document references the wrong protocol")
            if manifest.get("attempt_count") != len(manifest.get("attempts", [])):
                raise RegistryIntegrityError("manifest attempt_count differs from its rows")
            sealed_ids = [item["attempt_id"] for item in manifest["attempts"]]
            if len(set(sealed_ids)) != len(sealed_ids):
                raise RegistryIntegrityError("sealed manifest contains duplicate attempt IDs")
            if any(attempt_protocols.get(attempt_id) != event["protocol_id"] for attempt_id in sealed_ids):
                raise RegistryIntegrityError("sealed manifest includes an attempt from another protocol")
            registered_ids = {
                attempt_id for attempt_id, protocol_id in attempt_protocols.items()
                if protocol_id == event["protocol_id"] and states.get(attempt_id) == "SUBMITTED"
            }
            if set(sealed_ids) != registered_ids:
                raise RegistryIntegrityError("sealed manifest and registered attempts differ")
            sealed_protocols.add(event["protocol_id"])
        elif event_type in {
            "CANDIDATE_REGISTERED", "CANDIDATE_INVALID", "EVALUATION_STARTED",
            "EVALUATION_COMPLETED", "CANDIDATE_EVALUATION_SKIPPED",
            "CANDIDATE_TECHNICAL_FAILURE", "CANDIDATE_INSUFFICIENT_EVIDENCE",
            "CANDIDATE_REJECTED", "RESEARCH_ADVANCED_PENDING_PROSPECTIVE",
            "APPROVED_FOR_FURTHER_RESEARCH",
        }:
            attempt_id = payload.get("attempt_id")
            if event["protocol_id"] not in sealed_protocols:
                raise RegistryIntegrityError("attempt validation/evaluation occurred before manifest seal")
            if attempt_id not in states:
                raise RegistryIntegrityError(f"event {event_type} references missing attempt {attempt_id!r}")
            if attempt_protocols.get(attempt_id) != event["protocol_id"]:
                raise RegistryIntegrityError(f"event {event_type} references an attempt under the wrong protocol")
            current = states[attempt_id]
            _assert_transition(event_type, current, attempt_id)
            target = {
                "CANDIDATE_REGISTERED": "VALIDATED",
                "CANDIDATE_INVALID": "INVALID_CANDIDATE",
                "EVALUATION_STARTED": "RUNNING",
                "EVALUATION_COMPLETED": "COMPLETED",
                "CANDIDATE_EVALUATION_SKIPPED": "UNEVALUATED",
                "CANDIDATE_TECHNICAL_FAILURE": "TECHNICAL_EVALUATION_FAILURE",
                "CANDIDATE_INSUFFICIENT_EVIDENCE": "INSUFFICIENT_EVIDENCE",
                "CANDIDATE_REJECTED": "REJECTED",
                "RESEARCH_ADVANCED_PENDING_PROSPECTIVE": "RESEARCH_ADVANCED_PENDING_PROSPECTIVE",
                "APPROVED_FOR_FURTHER_RESEARCH": "RESEARCH_ADVANCED_PENDING_PROSPECTIVE",
            }[event_type]
            states[attempt_id] = target
    return states


def verify_registry(
    root: str | Path,
    *,
    expected_tip_sha256: str | None = None,
) -> VerificationResult:
    """Validate DB integrity, immutable payload hashes, manifests and transitions."""
    registry = ExperimentRegistry(root)
    connection = registry._connect(readonly=True)
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RegistryIntegrityError(f"SQLite integrity check failed: {integrity}")
        events = registry._events(connection)
        result = verify_event_stream(events, expected_tip_sha256)
        protocols: dict[str, dict[str, Any]] = {}
        manifests: dict[str, dict[str, Any]] = {}
        registrations: dict[str, tuple[dict[str, Any], bytes]] = {}
        for event in events:
            payload = event["payload"]
            event_type = event["event_type"]
            if event_type.startswith("HOLDOUT_"):
                raise RegistryIntegrityError("Milestone 1 registry contains a forbidden holdout operation")
            if event_type == "PROTOCOL_PRE_REGISTERED":
                protocol = payload.get("protocol")
                _validate("experiment_protocol.schema.json", protocol, "stored experiment protocol")
                if compute_protocol_id(protocol) != event["protocol_id"]:
                    raise RegistryIntegrityError("stored protocol semantic ID mismatch")
                if compute_search_family_id(protocol) != protocol["search_family_id"]:
                    raise RegistryIntegrityError("stored search-family ID mismatch")
                if protocol["source"]["schema_bundle_sha256"] != schema_bundle_sha256():
                    raise RegistryIntegrityError("registered protocol schema bundle differs from this verifier")
                protocols[event["protocol_id"]] = protocol
            elif event_type == "CANDIDATE_ATTEMPT_REGISTERED":
                attempt_id = payload["attempt_id"]
                row = connection.execute(
                    "SELECT candidate_spec_bytes_sha256,spec_bytes FROM attempt_specs WHERE attempt_id=?",
                    (attempt_id,),
                ).fetchone()
                if row is None:
                    raise RegistryIntegrityError(f"missing candidate bytes for {attempt_id}")
                raw = bytes(row["spec_bytes"])
                digest = sha256_hex(raw)
                if digest != row["candidate_spec_bytes_sha256"] or digest != payload["candidate_spec_bytes_sha256"]:
                    raise RegistryIntegrityError(f"candidate byte hash mismatch for {attempt_id}")
                expected_attempt_id = compute_attempt_id(
                    event["protocol_id"], payload["ordinal"], payload["assigned_family"], digest
                )
                if expected_attempt_id != attempt_id:
                    raise RegistryIntegrityError(f"attempt ID mismatch for {attempt_id}")
                registrations[attempt_id] = (payload, raw)
            elif event_type == "CANDIDATE_SET_SEALED":
                if event["protocol_id"] in manifests:
                    raise RegistryIntegrityError("protocol contains duplicate candidate-set seal events")
                manifest = payload.get("attempt_manifest")
                _validate("candidate_attempt_manifest.schema.json", manifest, "stored attempt manifest")
                digest = sha256_hex(canonical_json_bytes(manifest))
                if digest != payload.get("attempt_manifest_sha256"):
                    raise RegistryIntegrityError("attempt manifest hash mismatch")
                if digest != protocols[event["protocol_id"]]["candidate_set"]["attempt_manifest_sha256"]:
                    raise RegistryIntegrityError("protocol and seal manifest commitments differ")
                manifest_ids = {row["attempt_id"] for row in manifest["attempts"]}
                protocol_attempt_ids = {
                    attempt_id for attempt_id, (registration, _raw) in registrations.items()
                    if registration["protocol_id"] == event["protocol_id"]
                }
                if manifest_ids != protocol_attempt_ids:
                    raise RegistryIntegrityError("manifest is missing or adds registered attempts")
                for row in manifest["attempts"]:
                    registration, _raw = registrations[row["attempt_id"]]
                    for key in (
                        "ordinal", "assigned_family", "candidate_spec_bytes_sha256", "origin",
                        "submitted_at_utc", "status_at_seal",
                    ):
                        if row[key] != registration[key]:
                            raise RegistryIntegrityError(
                                f"manifest row {row['attempt_id']} differs from registration field {key}"
                            )
                manifests[event["protocol_id"]] = manifest
        if set(protocols) != set(manifests):
            raise RegistryIntegrityError("one or more registered protocols lack a sealed attempt manifest")
        stored_spec_ids = {
            row[0] for row in connection.execute("SELECT attempt_id FROM attempt_specs")
        }
        if stored_spec_ids != set(registrations):
            raise RegistryIntegrityError("candidate byte table contains missing or orphaned attempt records")
        states = _reconstruct_attempt_states(events)
        for protocol_id, protocol in protocols.items():
            parents = protocol.get("protocol_parent_ids", [])
            if any(parent not in protocols for parent in parents):
                raise RegistryIntegrityError(f"protocol {protocol_id} has a missing parent")
            if any(protocols[parent].get("search_family_id") != protocol["search_family_id"] for parent in parents):
                raise RegistryIntegrityError(f"protocol {protocol_id} has a cross-family parent")
        for event in events:
            if event["event_type"] == "CANDIDATE_REGISTERED":
                attempt_id = event["payload"]["attempt_id"]
                registration_event = next(
                    item for item in events
                    if item["event_type"] == "CANDIDATE_ATTEMPT_REGISTERED"
                    and item["payload"]["attempt_id"] == attempt_id
                )
                raw = registrations[attempt_id][1]
                candidate = _parse_json_bytes(raw)
                _validate("candidate_spec.schema.json", candidate, "registered candidate")
                if compute_candidate_id(candidate) != event["payload"].get("candidate_id"):
                    raise RegistryIntegrityError(f"candidate ID mismatch for attempt {attempt_id}")
                if candidate["experiment_id"] != registration_event["protocol_id"]:
                    raise RegistryIntegrityError(f"candidate protocol lineage mismatch for attempt {attempt_id}")
            if event["event_type"] == "NO_QUALIFYING_CANDIDATE":
                family_id = protocols[event["protocol_id"]]["search_family_id"]
                family_attempts = [
                    attempt_id for attempt_id, (registration, _raw) in registrations.items()
                    if registration["search_family_id"] == family_id
                ]
                if len(family_attempts) != event["payload"]["registered_attempt_count"]:
                    raise RegistryIntegrityError("NO_QUALIFYING_CANDIDATE count differs from sealed family attempts")
                if not family_attempts or any(states.get(attempt_id) not in _TERMINAL_STATES for attempt_id in family_attempts):
                    raise RegistryIntegrityError("NO_QUALIFYING_CANDIDATE has unresolved attempts")
                if any(states[attempt_id] in {
                    "TECHNICAL_EVALUATION_FAILURE", "UNEVALUATED", "RESEARCH_ADVANCED_PENDING_PROSPECTIVE"
                } for attempt_id in family_attempts):
                    raise RegistryIntegrityError("NO_QUALIFYING_CANDIDATE conflicts with attempt dispositions")
        # Enforce the family-wide attempt ceiling and per-family caps on replay.
        family_counts: dict[str, Counter[str]] = {}
        family_ordinals: dict[str, list[int]] = {}
        for attempt_id, (registration, _raw) in registrations.items():
            family_id = registration["search_family_id"]
            family_counts.setdefault(family_id, Counter())[registration["assigned_family"]] += 1
            family_ordinals.setdefault(family_id, []).append(registration["ordinal"])
        for family_id, counts in family_counts.items():
            if sum(counts.values()) > _MAX_TOTAL_ATTEMPTS or any(count > _MAX_PER_FAMILY for count in counts.values()):
                raise RegistryIntegrityError(f"search-family attempt limit violation for {family_id}")
            ordinals = sorted(family_ordinals[family_id])
            if ordinals != list(range(1, len(ordinals) + 1)):
                raise RegistryIntegrityError(f"search-family ordinal sequence has a gap for {family_id}")
        if len(family_counts) > 1:
            raise RegistryIntegrityError("registry contains more than one persistent search family")
        tip = result.tip_sha256
        return VerificationResult(
            valid=True,
            event_count=result.event_count,
            protocol_count=len(protocols),
            attempt_count=len(registrations),
            search_family_id=next(iter(family_counts), result.search_family_id),
            tip_sha256=tip,
        )
    finally:
        connection.close()


def _event_state(events: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    return _reconstruct_attempt_states(events)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify an immutable Athena experiment registry")
    parser.add_argument("registry", type=Path)
    parser.add_argument("--expected-tip-sha256", help="trusted event-chain tip checkpoint")
    arguments = parser.parse_args(argv)
    try:
        result = verify_registry(arguments.registry, expected_tip_sha256=arguments.expected_tip_sha256)
    except RegistryError as exc:
        print(json.dumps({"valid": False, "error": str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps({
        "valid": result.valid,
        "event_count": result.event_count,
        "protocol_count": result.protocol_count,
        "attempt_count": result.attempt_count,
        "search_family_id": result.search_family_id,
        "tip_sha256": result.tip_sha256,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
