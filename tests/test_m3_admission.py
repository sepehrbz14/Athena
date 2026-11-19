from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from crypto_bot import experiment_registry as m1
from crypto_bot.m3_admission import AdmissionError, admit_attempt


class _Registry:
    def __init__(self, protocol: dict, candidate: dict, attempt_id: str = "attempt-1"):
        self.attempt_id = attempt_id
        self.protocol_id = protocol["protocol_id"]
        self.candidate_bytes = json.dumps(candidate, sort_keys=True, separators=(",", ":")).encode()
        spec_sha = hashlib.sha256(self.candidate_bytes).hexdigest()
        self._events = [
            {"event_type": "PROTOCOL_PRE_REGISTERED", "protocol_id": self.protocol_id,
             "payload": {"protocol": protocol}},
            {"event_type": "CANDIDATE_ATTEMPT_REGISTERED", "protocol_id": self.protocol_id,
             "payload": {"attempt_id": attempt_id}},
            {"event_type": "CANDIDATE_REGISTERED", "protocol_id": self.protocol_id,
             "payload": {"attempt_id": attempt_id, "candidate_spec_bytes_sha256": spec_sha}},
        ]
        self.write_calls = 0

    def verify(self):
        return m1.VerificationResult(True, 3, 1, 1, "sha256:" + "1" * 64, "a" * 64)

    def events(self):
        return self._events

    def attempt_states(self):
        return {self.attempt_id: "VALIDATED"}

    def attempt_bytes(self, _attempt_id):
        return self.candidate_bytes

    def record_evaluation_started(self, *_args, **_kwargs):
        self.write_calls += 1

    def complete_evaluation(self, *_args, **_kwargs):
        self.write_calls += 1

    def write_evidence(self, *_args, **_kwargs):
        self.write_calls += 1


class _Security:
    def __init__(self, protocol_id: str):
        self.protocol_id = protocol_id

    def verify_latest(self, *, allow_empty):
        if allow_empty:
            raise AssertionError("M3 must require an anchored, nonempty registry")
        return {"protocol_id": self.protocol_id, "event_tip_sha256": "a" * 64,
                "checkpoint_sha256": "b" * 64}


class M3AdmissionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.registry, self.security, self.inputs = self._make_fixture()

    def _make_fixture(self, classification="previously_observed_historical_diagnostic"):
        input_paths = {}
        hashes = {}
        for key in ("signals", "candles", "phase5_manifest"):
            path = self.root / f"{key}.bin"
            path.write_bytes(key.encode())
            input_paths[key] = path
            hashes[key + "_sha256"] = hashlib.sha256(key.encode()).hexdigest()
        protocol_id = "sha256:" + "2" * 64
        protocol = {"protocol_id": protocol_id, "research_classification": classification,
                    "data": {**hashes, "previously_examined": True,
                             "observed_through_utc": "2026-08-19T23:45:00Z"}}
        candidate = {"candidate_id": "placeholder", "experiment_id": protocol_id, "research_only": True}
        candidate["candidate_id"] = m1.compute_candidate_id(candidate)
        return _Registry(protocol, candidate), _Security(protocol_id), input_paths

    def test_admission_verifies_valid_attempt_and_exact_pinned_inputs(self):
        admitted = admit_attempt(self.registry, self.security, "attempt-1", self.inputs)
        self.assertEqual(admitted.attempt_id, "attempt-1")
        self.assertEqual(admitted.research_classification, "previously_observed_historical_diagnostic")
        self.assertTrue(admitted.previously_observed)
        self.assertTrue(admitted.eligible_for_evaluation)
        self.assertFalse(admitted.authoritative_evaluation_authorized)
        self.assertEqual({item.key for item in admitted.inputs}, set(self.inputs))
        signal_snapshot = next(item for item in admitted.inputs if item.key == "signals")
        old_bytes = signal_snapshot.content
        signal_snapshot.path.write_bytes(b"replaced after verification")
        self.assertEqual(signal_snapshot.content, old_bytes)

    def test_admission_rejects_holdout_before_reading_inputs(self):
        registry, security, _ = self._make_fixture("prospective_holdout_confirmatory")
        absent = {key: self.root / "missing" for key in self.inputs}
        with self.assertRaisesRegex(AdmissionError, "historical diagnostics only"):
            admit_attempt(registry, security, "attempt-1", absent)

    def test_admission_rejects_modified_input_bytes(self):
        self.inputs["signals"].write_bytes(b"changed")
        with self.assertRaisesRegex(AdmissionError, "hash mismatch for signals"):
            admit_attempt(self.registry, self.security, "attempt-1", self.inputs)

    def test_admission_rejects_nonvalidated_attempt(self):
        self.registry.attempt_states = lambda: {"attempt-1": "RUNNING"}
        with self.assertRaisesRegex(AdmissionError, "M1-validated"):
            admit_attempt(self.registry, self.security, "attempt-1", self.inputs)

    def test_admission_rejects_checkpoint_mismatch(self):
        self.security.verify_latest = lambda **_kwargs: {
            "protocol_id": "wrong", "event_tip_sha256": "a" * 64}
        with self.assertRaisesRegex(AdmissionError, "does not bind"):
            admit_attempt(self.registry, self.security, "attempt-1", self.inputs)

    def test_admission_rejects_untrusted_checkpoint_exception(self):
        def fail_checkpoint(**_kwargs):
            raise ValueError("bad signature")
        self.security.verify_latest = fail_checkpoint
        with self.assertRaisesRegex(AdmissionError, "checkpoint verification failed"):
            admit_attempt(self.registry, self.security, "attempt-1", self.inputs)

    def test_admission_rejects_missing_m1_registration(self):
        self.registry._events = [e for e in self.registry._events
                                 if e["event_type"] != "CANDIDATE_REGISTERED"]
        with self.assertRaisesRegex(AdmissionError, "required registry event not found"):
            admit_attempt(self.registry, self.security, "attempt-1", self.inputs)

    def test_admission_rejects_invalid_m1_attempt_state(self):
        self.registry.attempt_states = lambda: {"attempt-1": "INVALID_CANDIDATE"}
        with self.assertRaisesRegex(AdmissionError, "M1-validated"):
            admit_attempt(self.registry, self.security, "attempt-1", self.inputs)

    def test_admission_rejects_candidate_byte_mismatch(self):
        self.registry._events[-1]["payload"]["candidate_spec_bytes_sha256"] = "0" * 64
        with self.assertRaisesRegex(AdmissionError, "candidate bytes differ"):
            admit_attempt(self.registry, self.security, "attempt-1", self.inputs)

    def test_admission_rejects_each_pinned_input_hash_mismatch(self):
        for key in ("signals", "candles", "phase5_manifest"):
            with self.subTest(input_key=key):
                registry, security, inputs = self._make_fixture()
                inputs[key].write_bytes(b"tampered-" + key.encode())
                with self.assertRaisesRegex(AdmissionError, f"hash mismatch for {key}"):
                    admit_attempt(registry, security, "attempt-1", inputs)

    def test_preflight_is_read_only_and_never_starts_evaluation(self):
        before = json.dumps(self.registry.events(), sort_keys=True)
        result = admit_attempt(self.registry, self.security, "attempt-1", self.inputs)
        self.assertEqual(json.dumps(self.registry.events(), sort_keys=True), before)
        self.assertFalse(result.authoritative_evaluation_authorized)
        self.assertEqual(self.registry.write_calls, 0)

    def test_admission_rejects_symlinked_input(self):
        link = self.root / "signals-link.bin"
        try:
            link.symlink_to(self.inputs["signals"])
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symlinks unavailable: {exc}")
        self.inputs["signals"] = link
        with self.assertRaisesRegex(AdmissionError, "symlink"):
            admit_attempt(self.registry, self.security, "attempt-1", self.inputs)


if __name__ == "__main__":
    unittest.main()

