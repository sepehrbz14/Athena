from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

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

    def verify(self):
        return m1.VerificationResult(True, 3, 1, 1, "sha256:" + "1" * 64, "a" * 64)

    def events(self):
        return self._events

    def attempt_states(self):
        return {self.attempt_id: "VALIDATED"}

    def attempt_bytes(self, _attempt_id):
        return self.candidate_bytes


class _Security:
    def __init__(self, protocol_id: str):
        self.protocol_id = protocol_id

    def verify_latest(self, *, allow_empty):
        assert allow_empty is False
        return {"protocol_id": self.protocol_id, "event_tip_sha256": "a" * 64,
                "checkpoint_sha256": "b" * 64}


def _inputs(tmp_path: Path):
    paths = {}
    hashes = {}
    for key in ("signals", "candles", "phase5_manifest"):
        path = tmp_path / f"{key}.bin"
        path.write_bytes(key.encode())
        paths[key] = path
        hashes[key + "_sha256"] = hashlib.sha256(key.encode()).hexdigest()
    return paths, hashes


def _registry(tmp_path: Path, classification="previously_observed_historical_diagnostic"):
    inputs, data_hashes = _inputs(tmp_path)
    protocol_id = "sha256:" + "2" * 64
    protocol = {"protocol_id": protocol_id, "research_classification": classification,
                "data": {**data_hashes, "previously_examined": True,
                         "observed_through_utc": "2026-08-19T23:45:00Z"}}
    candidate = {"candidate_id": "placeholder", "experiment_id": protocol_id, "research_only": True}
    candidate["candidate_id"] = m1.compute_candidate_id(candidate)
    return _Registry(protocol, candidate), _Security(protocol_id), inputs


def test_admission_verifies_valid_attempt_and_exact_pinned_inputs(tmp_path):
    registry, security, inputs = _registry(tmp_path)
    admitted = admit_attempt(registry, security, "attempt-1", inputs)
    assert admitted.attempt_id == "attempt-1"
    assert admitted.research_classification == "previously_observed_historical_diagnostic"
    assert admitted.previously_observed is True
    assert admitted.eligible_for_evaluation is True
    assert admitted.authoritative_evaluation_authorized is False
    assert {record.key for record in admitted.inputs} == {"signals", "candles", "phase5_manifest"}


def test_admission_rejects_holdout_before_reading_inputs(tmp_path):
    registry, security, _inputs_map = _registry(tmp_path, "prospective_holdout_confirmatory")
    with pytest.raises(AdmissionError, match="historical diagnostics only"):
        admit_attempt(registry, security, "attempt-1", {
            "signals": tmp_path / "missing", "candles": tmp_path / "missing",
            "phase5_manifest": tmp_path / "missing",
        })


def test_admission_rejects_modified_input_bytes(tmp_path):
    registry, security, inputs = _registry(tmp_path)
    inputs["signals"].write_bytes(b"changed")
    with pytest.raises(AdmissionError, match="hash mismatch"):
        admit_attempt(registry, security, "attempt-1", inputs)


def test_admission_rejects_nonvalidated_attempt(tmp_path):
    registry, security, inputs = _registry(tmp_path)
    registry.attempt_states = lambda: {"attempt-1": "RUNNING"}
    with pytest.raises(AdmissionError, match="M1-validated"):
        admit_attempt(registry, security, "attempt-1", inputs)


def test_admission_rejects_checkpoint_mismatch(tmp_path):
    registry, security, inputs = _registry(tmp_path)
    security.verify_latest = lambda **_kwargs: {"protocol_id": "wrong", "event_tip_sha256": "a" * 64}
    with pytest.raises(AdmissionError, match="does not bind"):
        admit_attempt(registry, security, "attempt-1", inputs)


def test_admission_rejects_untrusted_checkpoint_exception(tmp_path):
    registry, security, inputs = _registry(tmp_path)
    security.verify_latest = lambda **_kwargs: (_ for _ in ()).throw(ValueError("bad signature"))
    with pytest.raises(AdmissionError, match="checkpoint verification failed"):
        admit_attempt(registry, security, "attempt-1", inputs)


def test_admission_rejects_missing_m1_registration(tmp_path):
    registry, security, inputs = _registry(tmp_path)
    registry._events = [e for e in registry._events if e["event_type"] != "CANDIDATE_REGISTERED"]
    with pytest.raises(AdmissionError, match="required registry event not found"):
        admit_attempt(registry, security, "attempt-1", inputs)


def test_admission_rejects_invalid_m1_attempt_state(tmp_path):
    registry, security, inputs = _registry(tmp_path)
    registry.attempt_states = lambda: {"attempt-1": "INVALID_CANDIDATE"}
    with pytest.raises(AdmissionError, match="M1-validated"):
        admit_attempt(registry, security, "attempt-1", inputs)


def test_admission_rejects_candidate_byte_mismatch(tmp_path):
    registry, security, inputs = _registry(tmp_path)
    registry._events[-1]["payload"]["candidate_spec_bytes_sha256"] = "0" * 64
    with pytest.raises(AdmissionError, match="candidate bytes differ"):
        admit_attempt(registry, security, "attempt-1", inputs)


@pytest.mark.parametrize("input_key", ["signals", "candles", "phase5_manifest"])
def test_admission_rejects_each_pinned_input_hash_mismatch(tmp_path, input_key):
    registry, security, inputs = _registry(tmp_path)
    inputs[input_key].write_bytes(b"tampered-" + input_key.encode())
    with pytest.raises(AdmissionError, match=f"hash mismatch for {input_key}"):
        admit_attempt(registry, security, "attempt-1", inputs)


def test_preflight_is_read_only_and_has_no_evaluation_side_effects(tmp_path):
    registry, security, inputs = _registry(tmp_path)
    before = json.dumps(registry.events(), sort_keys=True)
    admitted = admit_attempt(registry, security, "attempt-1", inputs)
    assert json.dumps(registry.events(), sort_keys=True) == before
    assert admitted.authoritative_evaluation_authorized is False
    assert not hasattr(registry, "record_evaluation_started_called")


def test_admission_rejects_symlinked_input(tmp_path):
    registry, security, inputs = _registry(tmp_path)
    target = inputs["signals"]
    link = tmp_path / "signals-link.bin"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on this runner")
    inputs["signals"] = link
    with pytest.raises(AdmissionError, match="symlink"):
        admit_attempt(registry, security, "attempt-1", inputs)
