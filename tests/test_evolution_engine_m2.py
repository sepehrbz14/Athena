"""Adversarial Milestone 2 protocol, generation, and checkpoint tests."""
from __future__ import annotations

import base64
from collections import Counter
import copy
import hashlib
import json
import os
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from jsonschema import Draft202012Validator
from referencing import Registry as SchemaRegistry, Resource

from crypto_bot import experiment_registry as m1
from crypto_bot import evolution_registry_security as security_module
from crypto_bot.evolution_engine_m2 import (
    GenerationError,
    M2RegistryCoordinator,
    prepare_generation_protocol,
    resolve_declared_regime_priority,
)
from crypto_bot.evolution_registry_security import (
    AuthorizationError,
    CheckpointError,
    ExternalSecurityStore,
    ExternalTrust,
    SecurityError,
    TrustError,
    _DOMAIN_CATALOG_APPROVAL,
    _DOMAIN_CATALOG_FREEZE,
    _DOMAIN_CONTROL,
    _DOMAIN_IDENTITY,
    _DOMAIN_KEY_POP,
    _sha256,
)
from test_experiment_registry import protocol_document


FIXED_TIME = "2026-11-19T00:00:00.000000Z"
CONTROL_DOMAIN = _DOMAIN_CONTROL


def _public_bytes(key: Ed25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


def _key_id(key: Ed25519PrivateKey) -> str:
    return hashlib.sha256(_public_bytes(key)).hexdigest()


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _sign_digest(key: Ed25519PrivateKey, domain: bytes, digest: bytes) -> str:
    return _b64(key.sign(domain + digest))


def _unsigned_digest(document: dict, excluded: set[str]) -> bytes:
    return hashlib.sha256(m1.canonical_json_bytes({key: value for key, value in document.items() if key not in excluded})).digest()


class FixtureSigner:
    def __init__(self, seed: bytes):
        self.private = Ed25519PrivateKey.from_private_bytes(seed)
        self.key_id = _key_id(self.private)

    def sign(self, message: bytes) -> tuple[str, bytes]:
        return self.key_id, self.private.sign(message)

    def trusted_key(self) -> dict:
        return {"key_id": self.key_id, "public_key_base64": _b64(_public_bytes(self.private))}


class UnavailableSigner:
    def sign(self, message: bytes) -> tuple[str, bytes]:
        raise TrustError("injected external signing outage")


def _catalog(entries: list[dict], reviewer: FixtureSigner, *, version: int = 1, parent: str | None = None) -> dict:
    catalog = {
        "schema_version": "athena.evolution.template_catalog.v2",
        "catalog_id": "sha256:" + "0" * 64,
        "catalog_version": version,
        "parent_catalog_id": parent,
        "candidate_schema_id": "urn:athena:schemas:evolution:candidate_spec:v1",
        "regime_priority_semantics_id": "athena.regime_priority.first_eligible_in_declared_order_v1",
        "approved_content_sha256": "0" * 64,
        "review": {
            "reviewer_key_id": reviewer.key_id,
            "approved_at_utc": FIXED_TIME,
            "signature_algorithm": "Ed25519",
            "signature": "",
        },
        "frozen_at_utc": FIXED_TIME,
        "entries": entries,
    }
    projection = copy.deepcopy(catalog)
    projection.pop("catalog_id")
    projection.pop("approved_content_sha256")
    projection["review"].pop("signature")
    approved_digest = hashlib.sha256(m1.canonical_json_bytes(projection)).digest()
    catalog["approved_content_sha256"] = approved_digest.hex()
    catalog["review"]["signature"] = _sign_digest(reviewer.private, _DOMAIN_CATALOG_APPROVAL, approved_digest)
    id_projection = copy.deepcopy(catalog)
    id_projection.pop("catalog_id")
    catalog["catalog_id"] = "sha256:" + hashlib.sha256(m1.canonical_json_bytes(id_projection)).hexdigest()
    return catalog


def _regime_entry(template_id: str, strategies: list[str]) -> dict:
    return {
        "template_id": template_id,
        "family": "regime_specialization",
        "parents": [{"reference_type": "incumbent_component", "reference_id": strategies[0], "relation": "baseline"}],
        "strategy_members": strategies,
        "policy": {
            "kind": "regime_specialization",
            "classifier": "repository_regime_classifier_pinned_by_baseline_hash",
            "regime_allowlist": {"bull_trend": strategies},
            "unknown_policy": "abstain",
        },
    }


class M2Fixture:
    def __init__(self, directory: Path, *, catalog_entries: int = 1):
        self.directory = directory
        self.registry_root = directory / "registry"
        self.external_root = directory / "external-trust"
        self.registry = m1.ExperimentRegistry.create(self.registry_root)
        self.root_authority = FixtureSigner(bytes(range(1, 33)))
        self.initial_signer = FixtureSigner(bytes(range(33, 65)))
        self.reviewer = FixtureSigner(bytes(range(65, 97)))
        self.custodians = [FixtureSigner(bytes(range(97, 129))), FixtureSigner(bytes(range(129, 161)))]
        self.protocol, self.request, self.catalog = self.prepare(catalog_entries)
        self.identity = {
            "schema_version": "athena.evolution.registry_identity.v1",
            "registry_id": "sha256:" + "f" * 64,
            "search_family_id": self.protocol["search_family_id"],
            "identity_authority_key_id": self.root_authority.key_id,
            "issued_at_utc": FIXED_TIME,
            "signature_algorithm": "Ed25519",
            "signature": "",
        }
        identity_digest = _unsigned_digest(self.identity, {"signature"})
        self.identity["signature"] = _sign_digest(self.root_authority.private, _DOMAIN_IDENTITY, identity_digest)
        self.bundle = {
            "schema_version": "athena.evolution.trust_bundle.v1",
            "identity_authority": self.root_authority.trusted_key(),
            "initial_checkpoint_signer": self.initial_signer.trusted_key(),
            "catalog_reviewers": [self.reviewer.trusted_key()],
            "custodians": [signer.trusted_key() for signer in self.custodians],
        }
        ExternalSecurityStore.provision_external_trust(
            self.external_root, self.bundle, self.identity, registry_root=self.registry_root,
        )
        self.trust = ExternalTrust(self.bundle, self.identity)
        self.security = ExternalSecurityStore(
            self.registry, self.external_root, self.trust, clock=lambda: FIXED_TIME,
        )
        self.receipt = self.make_receipt(self.catalog)
        self.coordinator = M2RegistryCoordinator(
            self.registry, self.security, checkpoint_signer=self.initial_signer,
            report_root=self.directory / "reports",
        )

    def prepare(self, catalog_entries: int) -> tuple[dict, dict, dict]:
        strategies = ["trend_following", "breakout", "momentum", "mean_reversion"]
        entries = [_regime_entry(f"regime.{index:02d}", [strategies[index % 4]]) for index in range(catalog_entries)]
        catalog = _catalog(entries, self.reviewer)
        protocol, request = prepare_generation_protocol(
            protocol_document(), catalog_id=catalog["catalog_id"], catalog_version=1,
            template_ids=[entry["template_id"] for entry in entries[:1]],
        )
        return protocol, request, catalog

    def make_receipt(self, catalog: dict) -> dict:
        receipt = {
            "schema_version": "athena.evolution.catalog_freeze_receipt.v1",
            "registry_id": self.identity["registry_id"],
            "registry_identity_sha256": self.trust.identity_sha256,
            "search_family_id": self.identity["search_family_id"],
            "catalog_id": catalog["catalog_id"],
            "catalog_version": catalog["catalog_version"],
            "parent_catalog_id": catalog["parent_catalog_id"],
            "approved_content_sha256": catalog["approved_content_sha256"],
            "catalog_review_signature_sha256": hashlib.sha256(
                base64.b64decode(catalog["review"]["signature"])
            ).hexdigest(),
            "freeze_sequence": catalog["catalog_version"],
            "previous_receipt_sha256": None,
            "frozen_at_utc": FIXED_TIME,
            "identity_authority_key_id": self.root_authority.key_id,
            "signature_algorithm": "Ed25519",
            "signature": "",
        }
        digest = _unsigned_digest(receipt, {"signature"})
        receipt["signature"] = _sign_digest(self.root_authority.private, _DOMAIN_CATALOG_FREEZE, digest)
        return receipt

    def freeze(self) -> None:
        self.coordinator.freeze_catalog(self.catalog, self.receipt)

    def prepare_request(self, *, protocol_revision: int = 1, locked_at: str = FIXED_TIME, count: int = 1) -> tuple[dict, dict]:
        base = protocol_document(attempt_count=count)
        base["protocol_revision"] = protocol_revision
        base["locked_at_utc"] = locked_at
        base["created_at_utc"] = locked_at
        if protocol_revision > 1:
            protocols = [e for e in self.registry.events() if e["event_type"] == "PROTOCOL_PRE_REGISTERED"]
            base["protocol_parent_ids"] = [protocols[-1]["protocol_id"]]
        template_ids = [entry["template_id"] for entry in self.catalog["entries"][:count]]
        return prepare_generation_protocol(
            base, catalog_id=self.catalog["catalog_id"], catalog_version=1,
            template_ids=template_ids,
        )

    def signer(self, key: FixtureSigner | None = None) -> FixtureSigner:
        return key or self.initial_signer

    def make_control(
        self,
        *,
        action: str,
        core: dict,
        new_signer: FixtureSigner,
        expected_anchor: dict | None,
        expected_epoch: int,
        include_old_signer: FixtureSigner | None = None,
        duplicate_custodian: bool = False,
    ) -> dict:
        record = {
            "schema_version": "athena.evolution.checkpoint_control_record.v1",
            "action": action,
            "registry_id": self.identity["registry_id"],
            "registry_identity_sha256": self.trust.identity_sha256,
            "expected_anchor_revision": expected_anchor["anchor_revision"] if expected_anchor else 0,
            "expected_checkpoint_sha256": expected_anchor["checkpoint_sha256"] if expected_anchor else None,
            "expected_signer_key_epoch": expected_epoch,
            "authorized_checkpoint_sha256": core["checkpoint_core_sha256"],
            "issued_at_utc": FIXED_TIME,
            "identity_authority_key_id": self.root_authority.key_id,
            "identity_authority_signature": "",
            "approvals": [],
            "new_signer_key_id": new_signer.key_id,
            "new_signer_key_epoch": core["signer_key_epoch"],
            "new_public_key_base64": _b64(_public_bytes(new_signer.private)),
            "new_key_possession_signature": "",
        }
        if action == "ROTATE_SIGNING_KEY":
            if include_old_signer is None:
                raise ValueError("rotation fixture requires current signer")
            record["current_signer_key_id"] = include_old_signer.key_id
            record["current_signer_signature"] = ""
        else:
            record["reason"] = "explicit human review of the exact proposed checkpoint state"
            record["incident_evidence_sha256"] = "b" * 64
            record["human_review_record_sha256"] = "c" * 64
        for index, custodian in enumerate(self.custodians):
            record["approvals"].append({
                "custodian_key_id": custodian.key_id,
                "signature_algorithm": "Ed25519",
                "signature": "",
            })
        if duplicate_custodian:
            record["approvals"][1]["custodian_key_id"] = record["approvals"][0]["custodian_key_id"]
        unsigned = copy.deepcopy(record)
        unsigned["identity_authority_signature"] = ""
        unsigned.pop("current_signer_signature", None)
        unsigned.pop("new_key_possession_signature", None)
        for approval in unsigned["approvals"]:
            approval.pop("signature")
        control_digest = hashlib.sha256(m1.canonical_json_bytes(unsigned)).digest()
        record["identity_authority_signature"] = _sign_digest(self.root_authority.private, CONTROL_DOMAIN, control_digest)
        if include_old_signer is not None:
            record["current_signer_signature"] = _sign_digest(include_old_signer.private, CONTROL_DOMAIN, control_digest)
        record["new_key_possession_signature"] = _sign_digest(new_signer.private, _DOMAIN_KEY_POP, control_digest)
        for index, custodian in enumerate(self.custodians):
            signer = self.custodians[0] if duplicate_custodian and index == 1 else custodian
            record["approvals"][index]["signature"] = _sign_digest(signer.private, CONTROL_DOMAIN, control_digest)
        return record


class EvolutionEngineM2Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.fixture = M2Fixture(Path(self.temp.name))
        self.fixture.freeze()
        self.protocol, self.request = self.fixture.prepare_request()

    def tearDown(self):
        self.temp.cleanup()

    def test_all_m2_schemas_meta_validate_and_candidate_references_resolve(self):
        root = Path(__file__).resolve().parents[1]
        schema_paths = sorted((root / "reports/evolution_engine_milestone2_design_2026-11-18/schemas").glob("*.schema.json"))
        documents = [json.loads(path.read_text(encoding="utf-8")) for path in schema_paths]
        documents.append(json.loads((root / "reports/evolution_engine_milestone0_2026-11-18/candidate_spec.schema.json").read_text(encoding="utf-8")))
        registry = SchemaRegistry()
        for schema in documents:
            Draft202012Validator.check_schema(schema)
            registry = registry.with_resource(schema["$id"], Resource.from_contents(schema))
        for schema in documents:
            Draft202012Validator(schema, registry=registry)
        self.assertEqual(len(schema_paths), 10)

    def test_immutable_checkpoint_publication_has_no_partial_destination(self):
        target = Path(self.temp.name) / "external" / "checkpoints" / ("a" * 64 + ".json")
        with mock.patch.object(security_module.os, "link", side_effect=OSError("injected publication crash")):
            with self.assertRaises(CheckpointError):
                security_module._publish_immutable(target, b"complete-record")
        self.assertFalse(target.exists())
        self.assertEqual(list(target.parent.glob("*.staging")), [])

    def test_report_failure_never_publishes_a_partial_dated_directory(self):
        report_root = self.fixture.directory / "reports"
        self.fixture.coordinator._report_root = report_root
        with mock.patch.object(M2RegistryCoordinator, "_write_exclusive", side_effect=OSError("injected report crash")):
            with self.assertRaises(OSError):
                self.fixture.coordinator.submit(self.protocol, self.request, write_report=True)
        self.fixture.security.verify_latest(allow_empty=False)
        self.assertEqual(list(report_root.rglob(self.protocol["protocol_id"].replace(":", "_"))), [])
        self.assertEqual(list(report_root.rglob("*.staging")), [])

    def test_regime_priority_ties_follow_declared_order_and_preserve_snapshot(self):
        first = {"abstain": False, "direction": "long", "stop": 91.25, "target": 108.5, "origin": "tf"}
        second = {"abstain": False, "direction": "short", "stop": 109.0, "target": 89.0, "origin": "breakout"}
        winner, selected = resolve_declared_regime_priority(
            ["trend_following", "breakout"],
            {"trend_following": first, "breakout": second},
        )
        self.assertEqual(winner, "trend_following")
        self.assertIs(selected, first)
        reversed_winner, reversed_selected = resolve_declared_regime_priority(
            ["breakout", "trend_following"],
            {"trend_following": first, "breakout": second},
        )
        self.assertEqual(reversed_winner, "breakout")
        self.assertIs(reversed_selected, second)
        self.assertIsNone(resolve_declared_regime_priority(
            ["trend_following", "breakout"],
            {"trend_following": {"abstain": True}, "breakout": {"abstain": True}},
        ))

    def test_regime_priority_malformed_snapshot_fails_closed(self):
        with self.assertRaises(GenerationError):
            resolve_declared_regime_priority(["trend_following"], {"trend_following": {"direction": "long"}})
        with self.assertRaises(GenerationError):
            resolve_declared_regime_priority(["breakout", "breakout"], {})

    def test_generation_registers_before_validation_and_writes_dated_report(self):
        result = self.fixture.coordinator.submit(self.protocol, self.request, write_report=True)
        self.assertEqual(result.validation_statuses, ("VALIDATED",))
        self.assertEqual(len(self.fixture.registry.events()), 5)
        self.assertIsNotNone(result.report_directory)
        self.assertTrue((result.report_directory / "generation_manifest.json").is_file())
        self.assertTrue((result.report_directory / "candidate_specs").is_dir())
        verified = self.fixture.security.verify_latest(allow_empty=False)
        self.assertEqual(verified["checkpoint_sha256"], result.checkpoint_sha256)

    def test_clean_fixture_reproduces_exact_events_and_checkpoint(self):
        def run(directory: Path) -> tuple[bytes, str, str, bytes]:
            fixture = M2Fixture(directory)
            fixture.freeze()
            protocol, request = fixture.prepare_request()
            with mock.patch.object(m1, "_now_utc", return_value=FIXED_TIME):
                result = fixture.coordinator.submit(protocol, request, write_report=False)
            events = fixture.registry.export_events_jsonl()
            candidate = fixture.registry.attempt_bytes(result.attempt_ids[0])
            return events, result.generation_manifest_sha256, result.checkpoint_sha256, candidate

        first = run(Path(self.temp.name) / "repro-a")
        second = run(Path(self.temp.name) / "repro-b")
        self.assertEqual(first, second)

    def test_rejects_untrusted_identity_and_modified_trust_bundle(self):
        bad_identity = copy.deepcopy(self.fixture.identity)
        bad_identity["registry_id"] = "sha256:" + "e" * 64
        with self.assertRaises(TrustError):
            ExternalTrust(self.fixture.bundle, bad_identity)
        bundle_path = self.fixture.external_root / "trust_bundle.json"
        original = bundle_path.read_bytes()
        changed_bundle = json.loads(original)
        changed_bundle["initial_checkpoint_signer"]["public_key_base64"] = _b64(bytes(32))
        bundle_path.write_bytes(m1.canonical_json_bytes(changed_bundle))
        with self.assertRaises(TrustError):
            ExternalSecurityStore.from_files(
                self.fixture.registry, self.fixture.external_root,
                expected_trust_bundle_sha256=_sha256(original),
            )

    def test_catalog_freeze_rejects_duplicate_ids_wrong_policy_and_stale_receipt(self):
        bad_catalog = copy.deepcopy(self.fixture.catalog)
        bad_catalog["entries"][0]["family"] = "signal_combination"
        with self.assertRaises(SecurityError):
            self.fixture.coordinator.freeze_catalog(bad_catalog, self.fixture.receipt)
        with self.assertRaises(SecurityError):
            self.fixture.coordinator.freeze_catalog(self.fixture.catalog, {**self.fixture.receipt, "freeze_sequence": 2})
        # A catalog is sealed once the first registered attempt exists.
        self.fixture.coordinator.submit(self.protocol, self.request, write_report=False)
        with self.assertRaises(SecurityError):
            self.fixture.coordinator.freeze_catalog(self.fixture.catalog, self.fixture.receipt)

    def test_malformed_request_fails_before_attempt_registration(self):
        invalid = copy.deepcopy(self.request)
        invalid["request_id"] = "sha256:" + "0" * 64
        with self.assertRaises(GenerationError):
            self.fixture.coordinator.submit(self.protocol, invalid, write_report=False)
        self.assertEqual(self.fixture.registry.events(), [])

    def test_registration_transaction_rollback_is_empty_and_retryable(self):
        with self.assertRaises(RuntimeError):
            self.fixture.coordinator.submit(
                self.protocol, self.request, write_report=False,
                fault_registration_after_event=2,
            )
        self.assertEqual(self.fixture.registry.events(), [])
        self.assertIsNone(self.fixture.security.verify_latest())
        result = self.fixture.coordinator.submit(self.protocol, self.request, write_report=False)
        self.assertEqual(result.validation_statuses, ("VALIDATED",))

    def test_crash_after_registry_commit_or_checkpoint_file_fails_closed(self):
        for fault in ("after_registry_commit", "after_checkpoint_publish"):
            with self.subTest(fault=fault):
                fixture = M2Fixture(Path(self.temp.name) / fault)
                fixture.freeze()
                protocol, request = fixture.prepare_request()
                with self.assertRaises(OSError):
                    fixture.coordinator.submit(protocol, request, write_report=False, fault_at=fault)
                with self.assertRaises(CheckpointError):
                    fixture.security.verify_latest()
                # A subsequent ordinary request cannot self-repair the anchor.
                with self.assertRaises((CheckpointError, SecurityError)):
                    fixture.coordinator.submit(protocol, request, write_report=False)

    def test_crash_after_anchor_publication_leaves_verifiable_pending_attempt(self):
        with self.assertRaises(OSError):
            self.fixture.coordinator.submit(
                self.protocol, self.request, write_report=False, fault_at="after_anchor_publish"
            )
        self.fixture.security.verify_latest(allow_empty=False)
        self.assertEqual(Counter(self.fixture.registry.attempt_states().values()), {"SUBMITTED": 1})
        self.assertEqual(self.fixture.coordinator.validate_pending(), ("VALIDATED",))
        self.fixture.security.verify_latest(allow_empty=False)

    def test_signer_outage_after_registry_commit_fails_closed(self):
        self.fixture.coordinator._checkpoint_signer = UnavailableSigner()
        with self.assertRaises(TrustError):
            self.fixture.coordinator.submit(self.protocol, self.request, write_report=False)
        self.assertGreater(len(self.fixture.registry.events()), 0)
        with self.assertRaises(CheckpointError):
            self.fixture.security.verify_latest()
        # No ordinary retry can silently repair the event suffix.
        with self.assertRaises(CheckpointError):
            self.fixture.coordinator.submit(self.protocol, self.request, write_report=False)

    def test_rollback_to_valid_historical_anchor_fails_closed(self):
        self.fixture.coordinator.submit(self.protocol, self.request, write_report=False)
        current = self.fixture.security._read_json(self.fixture.security.anchor_path, "anchor")
        self.assertGreater(current["checkpoint_sequence"], 1)
        first = next(
            json.loads(path.read_text(encoding="utf-8"))
            for path in self.fixture.security.checkpoints_dir.glob("*.json")
            if json.loads(path.read_text(encoding="utf-8"))["checkpoint_sequence"] == 1
        )
        stale = {
            "schema_version": "athena.evolution.checkpoint_anchor.v1",
            "registry_id": first["registry_id"],
            "registry_identity_sha256": first["registry_identity_sha256"],
            "checkpoint_sequence": first["checkpoint_sequence"],
            "checkpoint_sha256": first["checkpoint_sha256"],
            "previous_checkpoint_sha256": first["previous_checkpoint_sha256"],
            "signer_key_id": first["signer_key_id"],
            "signer_key_epoch": first["signer_key_epoch"],
            "anchor_revision": first["checkpoint_sequence"],
        }
        self.fixture.security.anchor_path.write_bytes(m1.canonical_json_bytes(stale))
        with self.assertRaises(CheckpointError):
            self.fixture.security.verify_latest()

    def test_symlinked_checkpoint_anchor_is_rejected(self):
        self.fixture.coordinator.submit(self.protocol, self.request, write_report=False)
        anchor_path = self.fixture.security.anchor_path
        saved = self.fixture.directory / "saved-anchor.json"
        saved.write_bytes(anchor_path.read_bytes())
        anchor_path.unlink()
        try:
            anchor_path.symlink_to(saved)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation is unavailable on this runner")
        with self.assertRaises(CheckpointError):
            self.fixture.security.verify_latest()

    def test_missing_corrupt_stale_conflicting_or_deleted_checkpoint_fails_closed(self):
        result = self.fixture.coordinator.submit(self.protocol, self.request, write_report=False)
        anchor_path = self.fixture.security.anchor_path
        original = anchor_path.read_bytes()
        checkpoint_path = self.fixture.security.checkpoints_dir / f"{result.checkpoint_sha256}.json"
        original_checkpoint = checkpoint_path.read_bytes()
        # Missing checkpoint file.
        checkpoint_path.unlink()
        with self.assertRaises(CheckpointError):
            self.fixture.security.verify_latest()
        checkpoint_path.write_bytes(original_checkpoint)
        # Conflicting registry identity in the trusted pointer.
        anchor = json.loads(original)
        anchor["registry_id"] = "sha256:" + "d" * 64
        anchor_path.write_bytes(m1.canonical_json_bytes(anchor))
        with self.assertRaises(CheckpointError):
            self.fixture.security.verify_latest()
        anchor_path.write_bytes(original)
        # Full database replacement with an empty M1 registry cannot be trusted.
        replacement = m1.ExperimentRegistry.create(Path(self.temp.name) / "replacement")
        replacement_security = ExternalSecurityStore(
            replacement, self.fixture.external_root, self.fixture.trust, clock=lambda: FIXED_TIME,
        )
        with self.assertRaises(CheckpointError):
            replacement_security.verify_latest()
        # Checkpoint body rewriting is detected even if the file remains present.
        cp = json.loads(original_checkpoint)
        cp["event_count"] += 1
        checkpoint_path.write_bytes(m1.canonical_json_bytes(cp))
        with self.assertRaises(CheckpointError):
            self.fixture.security.verify_latest()

    def test_concurrent_requests_cannot_exceed_persistent_family_limit(self):
        fixture = M2Fixture(Path(self.temp.name) / "concurrent", catalog_entries=11)
        fixture.freeze()
        protocol, request = fixture.prepare_request(count=11)
        first = fixture.coordinator.submit(protocol, request, write_report=False)
        self.assertEqual(len(first.attempt_ids), 11)
        # Two concurrent revisions compete for the single remaining family slot.
        bases = []
        for minute in (10, 11):
            base = protocol_document(attempt_count=1)
            base["protocol_revision"] = 2
            base["protocol_parent_ids"] = [first.protocol_id]
            locked = f"2026-11-19T00:{minute:02d}:00Z"
            base["locked_at_utc"] = locked
            base["created_at_utc"] = locked
            bases.append(prepare_generation_protocol(
                base, catalog_id=fixture.catalog["catalog_id"], catalog_version=1,
                template_ids=[fixture.catalog["entries"][0]["template_id"]],
            ))
        outcomes: list[str] = []
        guard = threading.Lock()
        def submit(item: tuple[dict, dict]) -> None:
            try:
                fixture.coordinator.submit(*item, write_report=False)
                value = "ok"
            except m1.SearchFamilyLimitError:
                value = "limit"
            except m1.RegistryError:
                value = "registry-rejected"
            with guard:
                outcomes.append(value)
        threads = [threading.Thread(target=submit, args=(item,)) for item in bases]
        for thread in threads: thread.start()
        for thread in threads: thread.join(timeout=20)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(Counter(outcomes), Counter({"ok": 1, "limit": 1}))
        attempts = [event for event in fixture.registry.events() if event["event_type"] == "CANDIDATE_ATTEMPT_REGISTERED"]
        self.assertEqual(len(attempts), 12)
        self.assertEqual(fixture.registry.verify().attempt_count, 12)

    def test_key_rotation_retains_historical_verification(self):
        result = self.fixture.coordinator.submit(self.protocol, self.request, write_report=False)
        current_anchor = self.fixture.security._read_json(self.fixture.security.anchor_path, "anchor")
        new_signer = FixtureSigner(bytes(range(161, 193)))
        core = self.fixture.security.propose_control_checkpoint_core(new_signer.key_id, 2)
        control = self.fixture.make_control(
            action="ROTATE_SIGNING_KEY", core=core, new_signer=new_signer,
            expected_anchor=current_anchor, expected_epoch=1,
            include_old_signer=self.fixture.initial_signer,
        )
        rotated = self.fixture.security.apply_control_checkpoint(control, core, new_signer)
        self.assertEqual(rotated["signer_key_epoch"], 2)
        self.assertEqual(self.fixture.security.verify_latest()["checkpoint_sha256"], rotated["checkpoint_sha256"])
        # The verifier walks earlier checkpoints using epoch 1, then the signed transition and epoch 2.
        self.assertEqual(rotated["previous_checkpoint_sha256"], result.checkpoint_sha256)
        third_signer = FixtureSigner(bytes(range(193, 225)))
        duplicate_core = self.fixture.security.propose_control_checkpoint_core(
            third_signer.key_id, 3,
        )
        # The signatures bind the exact core. Re-sign the deliberately invalid
        # duplicate-identity control for this core so rejection tests identity,
        # not just a stale core digest.
        duplicate_approvals = self.fixture.make_control(
            action="ROTATE_SIGNING_KEY", core=duplicate_core,
            new_signer=third_signer,
            expected_anchor=self.fixture.security._read_json(self.fixture.security.anchor_path, "anchor"),
            expected_epoch=2, include_old_signer=new_signer, duplicate_custodian=True,
        )
        with self.assertRaises(AuthorizationError):
            self.fixture.security.apply_control_checkpoint(
                duplicate_approvals, duplicate_core, third_signer,
            )
        invalid = copy.deepcopy(control)
        invalid["authorized_checkpoint_sha256"] = "f" * 64
        with self.assertRaises(AuthorizationError):
            self.fixture.security.apply_control_checkpoint(invalid, core, new_signer)

    def test_recovery_requires_two_humans_and_exact_current_database_tip(self):
        first = self.fixture.coordinator.submit(self.protocol, self.request, write_report=False)
        second_protocol, second_request = self.fixture.prepare_request(protocol_revision=2, locked_at="2026-11-19T00:10:00Z")
        with self.assertRaises(OSError):
            self.fixture.coordinator.submit(
                second_protocol, second_request, write_report=False, fault_at="after_registry_commit"
            )
        with self.assertRaises(CheckpointError):
            self.fixture.security.verify_latest()
        core = self.fixture.security.propose_control_checkpoint_core(
            "0" * 64, 2, recovery=True,
        )
        new_signer = FixtureSigner(bytes(range(161, 193)))
        core["signer_key_id"] = new_signer.key_id
        core["checkpoint_core_sha256"] = self.fixture.security.checkpoint_core_digest(core)
        control = self.fixture.make_control(
            action="RECOVER_CHECKPOINT", core=core, new_signer=new_signer,
            expected_anchor=json.loads((self.fixture.security.anchor_path).read_text()),
            expected_epoch=1,
        )
        with self.assertRaises(AuthorizationError):
            bad = copy.deepcopy(control)
            bad["authorized_checkpoint_sha256"] = "9" * 64
            self.fixture.security.apply_control_checkpoint(bad, core, new_signer)
        recovered = self.fixture.security.apply_control_checkpoint(control, core, new_signer)
        self.assertEqual(recovered["event_tip_sha256"], self.fixture.registry.verify().tip_sha256)
        self.assertEqual(self.fixture.security.verify_latest()["checkpoint_sha256"], recovered["checkpoint_sha256"])
        self.assertNotEqual(first.checkpoint_sha256, recovered["checkpoint_sha256"])

    def test_anchorless_recovery_requires_explicit_signed_control(self):
        result = self.fixture.coordinator.submit(self.protocol, self.request, write_report=False)
        self.fixture.security.anchor_path.unlink()
        with self.assertRaises(CheckpointError):
            self.fixture.security.verify_latest()
        new_signer = FixtureSigner(bytes(range(161, 193)))
        core = self.fixture.security.propose_control_checkpoint_core(new_signer.key_id, 1, recovery=True)
        control = self.fixture.make_control(
            action="RECOVER_CHECKPOINT", core=core, new_signer=new_signer,
            expected_anchor=None, expected_epoch=0,
        )
        recovered = self.fixture.security.apply_control_checkpoint(control, core, new_signer)
        self.assertEqual(recovered["checkpoint_sequence"], 1)
        self.assertEqual(recovered["previous_checkpoint_sha256"], None)
        self.assertEqual(self.fixture.security.verify_latest()["checkpoint_sha256"], recovered["checkpoint_sha256"])
        self.assertNotEqual(result.checkpoint_sha256, recovered["checkpoint_sha256"])

    def test_duplicate_semantic_candidates_are_registered_then_invalidated(self):
        # Two templates with the same policy produce the same semantic ID.
        catalog = copy.deepcopy(self.fixture.catalog)
        catalog["entries"].append(copy.deepcopy(catalog["entries"][0]))
        catalog["entries"][1]["template_id"] = "regime.duplicate"
        catalog = _catalog(catalog["entries"], self.fixture.reviewer)
        protocol, request = prepare_generation_protocol(
            protocol_document(attempt_count=2), catalog_id=catalog["catalog_id"], catalog_version=1,
            template_ids=[entry["template_id"] for entry in catalog["entries"]],
        )
        receipt = self.fixture.make_receipt(catalog)
        # Freeze the replacement catalog before any attempt is registered.
        self.fixture.coordinator.freeze_catalog(catalog, receipt)
        result = self.fixture.coordinator.submit(protocol, request, write_report=False)
        self.assertEqual(len(result.attempt_ids), 2)
        self.assertEqual(result.validation_statuses, ("INVALID_CANDIDATE", "INVALID_CANDIDATE"))
        self.assertEqual(len([e for e in self.fixture.registry.events() if e["event_type"] == "CANDIDATE_ATTEMPT_REGISTERED"]), 2)
        self.assertEqual(set(self.fixture.registry.attempt_states().values()), {"INVALID_CANDIDATE"})


if __name__ == "__main__":
    unittest.main()
