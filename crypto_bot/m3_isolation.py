"""Fail-closed verification of external Linux isolation and human authorization.

The module verifies signed attestations; it does not provision a VM or start an
evaluation. Trusted public keys and authorization bytes must be supplied from
outside the repository and registry.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Mapping


class IsolationGateError(RuntimeError):
    """The independent Linux attestation or scoped authorization is absent/invalid."""


_ATTESTATION_DOMAIN = b"ATHENA-M3-LINUX-ATTESTATION-v1\x00"
_AUTHORIZATION_DOMAIN = b"ATHENA-M3-HISTORICAL-AUTHORIZATION-v1\x00"
_REQUIRED_TESTS = {
    "network_dns_denied", "host_secret_registry_holdout_reads_denied", "input_writes_denied",
    "path_symlink_escape_denied", "output_overwrite_denied", "privilege_escalation_denied",
    "device_access_denied", "process_escape_denied", "resource_exhaustion_bounded",
    "timeout_escape_denied", "transfer_hashes_verified",
}


@dataclass(frozen=True, slots=True)
class VerifiedEvaluationPermit:
    attempt_id: str
    protocol_id: str
    candidate_sha256: str
    protocol_sha256: str
    source_commit: str
    input_hashes_sha256: str
    registry_tip_sha256: str
    checkpoint_sha256: str
    image_digest: str
    attestation_sha256: str
    authorization_sha256: str


def _canonical(value: Mapping) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def _parse_signed(raw: bytes, signature_field: str, trusted_key_id: str,
                  trusted_public_keys: Mapping[str, bytes], domain: bytes) -> dict:
    try:
        value = json.loads(raw.decode("utf-8"))
        signature = base64.b64decode(value.pop(signature_field), validate=True)
        key_id = value["signing_key_id"]
        key = trusted_public_keys[key_id]
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise IsolationGateError("signed isolation control is malformed or untrusted") from exc
    if key_id != trusted_key_id or len(key) != 32 or len(signature) != 64:
        raise IsolationGateError("signed isolation control key identity is not independently trusted")
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        Ed25519PublicKey.from_public_bytes(key).verify(signature, domain + hashlib.sha256(_canonical(value)).digest())
    except ImportError as exc:
        raise IsolationGateError("Ed25519 verification dependency is unavailable; evaluation remains disabled") from exc
    except Exception as exc:
        raise IsolationGateError("isolation control signature verification failed") from exc
    if _canonical({**value, signature_field: base64.b64encode(signature).decode("ascii")}) != raw:
        raise IsolationGateError("signed isolation control is not canonical")
    return value


def verify_authoritative_evaluation_gate(
    *, attestation_bytes: bytes, authorization_bytes: bytes,
    trusted_attestation_key_id: str, trusted_attestation_keys: Mapping[str, bytes],
    trusted_authorization_key_id: str, trusted_authorization_keys: Mapping[str, bytes],
    now_utc: datetime, attempt_id: str, protocol_id: str, candidate_sha256: str,
    protocol_sha256: str, source_commit: str, input_hashes: Mapping[str, str],
    registry_tip_sha256: str, checkpoint_sha256: str,
) -> VerifiedEvaluationPermit:
    """Verify independent VM proof and a separate one-attempt user permit.

    This is a gate predicate only. A caller must still use the external VM
    launcher and bounded transfer protocol described by the approved design.
    """
    if now_utc.tzinfo is None or now_utc.utcoffset() != timedelta(0):
        raise IsolationGateError("gate clock must be timezone-aware UTC")
    attestation = _parse_signed(attestation_bytes, "signature_base64", trusted_attestation_key_id,
                                trusted_attestation_keys, _ATTESTATION_DOMAIN)
    authorization = _parse_signed(authorization_bytes, "signature_base64", trusted_authorization_key_id,
                                  trusted_authorization_keys, _AUTHORIZATION_DOMAIN)
    expected = {
        "attempt_id": attempt_id, "protocol_id": protocol_id,
        "candidate_sha256": candidate_sha256, "protocol_sha256": protocol_sha256,
        "source_commit": source_commit, "input_hashes": dict(sorted(input_hashes.items())),
        "registry_tip_sha256": registry_tip_sha256, "checkpoint_sha256": checkpoint_sha256,
    }
    if any(attestation.get(key) != value for key, value in expected.items()):
        raise IsolationGateError("Linux attestation does not bind exact attempt, code, protocol, and inputs")
    if (attestation.get("platform") != "linux_vm" or not attestation.get("hypervisor_boundary") or
            not re.fullmatch(r"sha256:[a-f0-9]{64}", str(attestation.get("image_digest", ""))) or
            attestation.get("network") != "disabled" or
            attestation.get("credentials_present") is not False or
            attestation.get("input_mount") != "read_only" or
            attestation.get("output_mount") != "bounded_exclusive" or
            attestation.get("holdout_mounted") is not False):
        raise IsolationGateError("Linux VM attestation does not satisfy the frozen isolation boundary")
    expected_resources = {"max_wall_seconds": 1800, "max_memory_mb": 8192,
                          "max_output_bytes": 536870912, "max_workers": 1, "offline_only": True}
    if attestation.get("resource_limits") != expected_resources:
        raise IsolationGateError("Linux VM attestation does not bind exact frozen M0 resource limits")
    if not isinstance(attestation.get("adversarial_tests"), dict) or any(
            attestation["adversarial_tests"].get(name) is not True for name in _REQUIRED_TESTS):
        raise IsolationGateError("Linux VM attestation lacks successful independent adversarial tests")
    auth_expected = {**expected, "action": "AUTHORIZE_ONE_HISTORICAL_EVALUATION",
                     "holdout_access": False}
    if any(authorization.get(key) != value for key, value in auth_expected.items()):
        raise IsolationGateError("human authorization does not match this exact historical attempt")
    for control in (attestation, authorization):
        try:
            expires = datetime.fromisoformat(control["expires_at_utc"].replace("Z", "+00:00"))
        except (KeyError, TypeError, ValueError) as exc:
            raise IsolationGateError("signed control lacks an expiration") from exc
        if expires.tzinfo is None or expires.utcoffset() != timedelta(0) or now_utc >= expires:
            raise IsolationGateError("signed control has expired or has an invalid UTC expiry")
    return VerifiedEvaluationPermit(attempt_id, protocol_id, candidate_sha256, protocol_sha256,
        source_commit, hashlib.sha256(_canonical(dict(sorted(input_hashes.items())))).hexdigest(),
        registry_tip_sha256, checkpoint_sha256,
        attestation["image_digest"], hashlib.sha256(attestation_bytes).hexdigest(),
        hashlib.sha256(authorization_bytes).hexdigest())
