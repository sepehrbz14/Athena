from __future__ import annotations

import base64
import importlib.util
import json
import unittest
from datetime import datetime, timedelta, timezone

from crypto_bot.m3_isolation import (
    IsolationGateError, _ATTESTATION_DOMAIN, _AUTHORIZATION_DOMAIN,
    verify_authoritative_evaluation_gate,
)

HAS_CRYPTOGRAPHY = importlib.util.find_spec("cryptography") is not None


@unittest.skipUnless(HAS_CRYPTOGRAPHY, "CI installs declared cryptography dependency")
class M3IsolationGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        cls.attest_private = Ed25519PrivateKey.generate()
        cls.auth_private = Ed25519PrivateKey.generate()
        cls.attest_public = cls.attest_private.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        cls.auth_public = cls.auth_private.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw)

    def signed(self, value, key, domain):
        from crypto_bot.m3_isolation import _canonical
        signature = key.sign(domain + __import__("hashlib").sha256(_canonical(value)).digest())
        return _canonical({**value, "signature_base64": base64.b64encode(signature).decode("ascii")})

    def controls(self):
        now = datetime(2026, 11, 20, tzinfo=timezone.utc)
        common = {"attempt_id": "a1", "protocol_id": "p1", "candidate_sha256": "c"*64,
                  "protocol_sha256": "p"*64, "source_commit": "commit", "input_hashes": {"signals": "s"*64},
                  "registry_tip_sha256": "r"*64, "checkpoint_sha256": "k"*64}
        attestation = {**common, "signing_key_id": "attest-key", "expires_at_utc": "2026-11-21T00:00:00Z",
            "platform": "linux_vm", "hypervisor_boundary": "KVM", "image_digest": "sha256:"+"i"*64,
            "network": "disabled", "credentials_present": False, "input_mount": "read_only",
            "output_mount": "bounded_exclusive", "holdout_mounted": False,
            "resource_limits": {"max_wall_seconds": 1800, "max_memory_mb": 8192,
                "max_output_bytes": 536870912, "max_workers": 1, "offline_only": True},
            "adversarial_tests": {name: True for name in {
                "network_dns_denied", "host_secret_registry_holdout_reads_denied", "input_writes_denied",
                "path_symlink_escape_denied", "output_overwrite_denied", "privilege_escalation_denied",
                "device_access_denied", "process_escape_denied", "resource_exhaustion_bounded",
                "timeout_escape_denied", "transfer_hashes_verified"}}}
        authorization = {**common, "signing_key_id": "auth-key", "expires_at_utc": "2026-11-21T00:00:00Z",
                         "action": "AUTHORIZE_ONE_HISTORICAL_EVALUATION", "holdout_access": False}
        return now, common, self.signed(attestation, self.attest_private, _ATTESTATION_DOMAIN), self.signed(
            authorization, self.auth_private, _AUTHORIZATION_DOMAIN)

    def verify(self, now, attestation, authorization, **overrides):
        common = {"attempt_id": "a1", "protocol_id": "p1", "candidate_sha256": "c"*64,
                  "protocol_sha256": "p"*64, "source_commit": "commit", "input_hashes": {"signals": "s"*64},
                  "registry_tip_sha256": "r"*64, "checkpoint_sha256": "k"*64}
        common.update(overrides)
        return verify_authoritative_evaluation_gate(attestation_bytes=attestation,
            authorization_bytes=authorization, trusted_attestation_key_id="attest-key",
            trusted_attestation_keys={"attest-key": self.attest_public},
            trusted_authorization_key_id="auth-key", trusted_authorization_keys={"auth-key": self.auth_public},
            now_utc=now, **common)

    def test_exact_signed_vm_proof_and_one_attempt_authorization(self):
        now, _, attestation, authorization = self.controls()
        result = self.verify(now, attestation, authorization)
        self.assertEqual(result.attempt_id, "a1")
        self.assertEqual(result.protocol_id, "p1")

    def test_wrong_attempt_or_input_hash_is_denied(self):
        now, _, attestation, authorization = self.controls()
        with self.assertRaises(IsolationGateError):
            self.verify(now, attestation, authorization, attempt_id="other")

    def test_missing_adversarial_test_and_network_are_denied(self):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        now, _, _, authorization = self.controls()
        value = {"attempt_id": "a1", "protocol_id": "p1", "candidate_sha256": "c"*64,
                 "protocol_sha256": "p"*64, "source_commit": "commit", "input_hashes": {"signals": "s"*64},
                 "signing_key_id": "attest-key", "expires_at_utc": "2026-11-21T00:00:00Z",
                 "platform": "linux_vm", "hypervisor_boundary": "KVM", "image_digest": "sha256:image",
                 "network": "enabled", "credentials_present": False, "input_mount": "read_only",
                 "output_mount": "bounded_exclusive", "holdout_mounted": False, "adversarial_tests": {}}
        attestation = self.signed(value, self.attest_private, _ATTESTATION_DOMAIN)
        with self.assertRaises(IsolationGateError):
            self.verify(now, attestation, authorization)

    def test_expired_control_and_tampered_signature_are_denied(self):
        now, _, attestation, authorization = self.controls()
        with self.assertRaisesRegex(IsolationGateError, "expired"):
            self.verify(now + timedelta(days=2), attestation, authorization)
        tampered = attestation[:-2] + b"AA"
        with self.assertRaises(IsolationGateError):
            self.verify(now, tampered, authorization)


if __name__ == "__main__":
    unittest.main()
