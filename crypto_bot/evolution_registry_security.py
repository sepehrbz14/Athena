"""External trust, signing, catalog-anchor, and checkpoint controls for M2.

Private keys are never loaded by this module. Production signing is delegated
to an external command/key service; cryptographic verification uses public
keys from an independently provisioned trust bundle.
"""
from __future__ import annotations

from contextlib import contextmanager
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
from typing import Any, Iterator, Mapping, Protocol, Sequence

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry as SchemaRegistry, Resource

from crypto_bot import experiment_registry as m1


_REPO_ROOT = Path(__file__).resolve().parent.parent
_M2_SCHEMA_ROOT = _REPO_ROOT / "reports" / "evolution_engine_milestone2_design_2026-11-18" / "schemas"
_M0_SCHEMA_ROOT = _REPO_ROOT / "reports" / "evolution_engine_milestone0_2026-11-18"
_DOMAIN_IDENTITY = b"ATHENA-REGISTRY-IDENTITY-v1\0"
_DOMAIN_CATALOG_APPROVAL = b"ATHENA-CATALOG-APPROVAL-v1\0"
_DOMAIN_CATALOG_FREEZE = b"ATHENA-CATALOG-FREEZE-v1\0"
_DOMAIN_CHECKPOINT = b"ATHENA-REGISTRY-CHECKPOINT-v1\0"
_DOMAIN_CONTROL = b"ATHENA-CHECKPOINT-CONTROL-v1\0"
_DOMAIN_KEY_POP = b"ATHENA-CHECKPOINT-KEY-POP-v1\0"
_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


class SecurityError(RuntimeError):
    """Base class for trusted-state failures; callers must fail closed."""


class TrustError(SecurityError):
    """A signature, identity, or external trust configuration is invalid."""


class CheckpointError(SecurityError):
    """The current registry tip cannot be authenticated."""


class AuthorizationError(SecurityError):
    """A requested recovery or rotation lacks exact trusted authorization."""


class ExternalSigner(Protocol):
    """Signing capability that does not expose private key material."""

    def sign(self, message: bytes) -> tuple[str, bytes]:
        """Return ``(key_id, signature)`` for the exact supplied message."""


class CommandEd25519Signer:
    """Adapter for an operator-provided OS/hardware key-service command.

    The command receives a JSON request over stdin and returns a JSON response
    over stdout. It is invoked without a shell. The request contains the message
    to sign, never a key path or private material.
    """

    def __init__(self, command: Sequence[str], expected_key_id: str, *, timeout: float = 15):
        if not command or any(not isinstance(part, str) or not part for part in command):
            raise ValueError("signer command must be a nonempty argument vector")
        self._command = tuple(command)
        self.expected_key_id = expected_key_id
        self.timeout = timeout

    def sign(self, message: bytes) -> tuple[str, bytes]:
        request = {
            "operation": "ed25519_sign_v1",
            "expected_key_id": self.expected_key_id,
            "message_base64": base64.b64encode(message).decode("ascii"),
        }
        try:
            result = subprocess.run(
                self._command,
                input=json.dumps(request, separators=(",", ":")).encode("utf-8"),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.timeout,
                check=False,
                shell=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise TrustError(f"external signer unavailable: {exc}") from exc
        if result.returncode != 0:
            raise TrustError("external signer returned a failure status")
        try:
            response = _strict_json_loads(result.stdout)
            key_id = response["key_id"]
            signature = _decode_b64(response["signature_base64"], expected_length=64)
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise TrustError("external signer returned malformed JSON or signature") from exc
        if key_id != self.expected_key_id:
            raise TrustError("external signer returned an unexpected key ID")
        return key_id, signature


def _strict_json_loads(raw: bytes | str) -> Any:
    def unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON property {key!r}")
            result[key] = value
        return result

    return json.loads(raw, object_pairs_hook=unique_pairs, parse_constant=lambda value: (_ for _ in ()).throw(
        ValueError(f"non-finite JSON constant {value} is prohibited")
    ))


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _content_id(raw: bytes) -> str:
    return "sha256:" + _sha256(raw)


def _decode_b64(value: str, *, expected_length: int) -> bytes:
    if not isinstance(value, str):
        raise ValueError("base64 field must be a string")
    raw = base64.b64decode(value.encode("ascii"), validate=True)
    if len(raw) != expected_length:
        raise ValueError(f"decoded base64 value must contain {expected_length} bytes")
    return raw


def _encode_b64(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _signing_digest(document: Mapping[str, Any], excluded: set[str]) -> bytes:
    projection = {key: value for key, value in document.items() if key not in excluded}
    return hashlib.sha256(m1.canonical_json_bytes(projection)).digest()


def _verify_ed25519(public_key: bytes, signature: bytes, message: bytes, label: str) -> None:
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(signature, message)
    except (InvalidSignature, ValueError) as exc:
        raise TrustError(f"invalid Ed25519 signature for {label}") from exc


def _key_id(public_key: bytes) -> str:
    return _sha256(public_key)


def _load_schema(name: str) -> dict[str, Any]:
    try:
        return json.loads((_M2_SCHEMA_ROOT / name).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SecurityError(f"cannot load Milestone 2 schema {name}: {exc}") from exc


def validate_m2_document(name: str, value: Any) -> None:
    """Validate one M2 document, resolving family policy refs to frozen M0."""
    schema = _load_schema(name)
    resources: SchemaRegistry = SchemaRegistry()
    for candidate_path in (_M0_SCHEMA_ROOT / "candidate_spec.schema.json", _M2_SCHEMA_ROOT / name):
        item = json.loads(candidate_path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(item)
        resources = resources.with_resource(item["$id"], Resource.from_contents(item))
    try:
        Draft202012Validator(schema, registry=resources, format_checker=FormatChecker()).validate(value)
    except Exception as exc:
        raise SecurityError(f"document violates {name}: {exc}") from exc


def _path_without_symlinks(path: Path) -> Path:
    absolute = Path(os.path.abspath(path.expanduser()))
    for part in (absolute, *absolute.parents):
        if part.exists() and part.is_symlink():
            raise SecurityError(f"trusted external path contains a symlink: {part}")
    resolved = absolute.resolve(strict=False)
    if os.path.normcase(str(absolute)) != os.path.normcase(str(resolved)):
        raise SecurityError("trusted external path resolution changed")
    return resolved


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _atomic_replace(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise SecurityError(f"refusing to replace symlinked trusted state: {path}")
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            if os.name != "nt":
                os.chmod(temp, 0o600)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
        _fsync_directory(path.parent)
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


def _publish_immutable(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise CheckpointError(f"refusing symlinked immutable artifact: {path}")
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".staging", dir=path.parent)
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            if os.name != "nt":
                os.chmod(temp, 0o600)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        # The destination becomes visible only after the complete immutable
        # bytes are durable. Hard-link creation is atomic and cannot overwrite.
        os.link(temp, path)
        _fsync_directory(path.parent)
    except FileExistsError:
        if path.is_symlink():
            raise CheckpointError(f"refusing symlinked immutable artifact: {path}")
        try:
            old = path.read_bytes()
        except OSError as exc:
            raise CheckpointError(f"immutable artifact exists but cannot be read: {path}") from exc
        if old != raw:
            raise CheckpointError(f"refusing to overwrite immutable artifact {path}")
    except OSError as exc:
        raise CheckpointError(f"cannot atomically publish immutable artifact {path}") from exc
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


@contextmanager
def _exclusive_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    key = os.path.normcase(str(path.resolve(strict=False)))
    with _LOCKS_GUARD:
        thread_lock = _LOCKS.setdefault(key, threading.RLock())
    with thread_lock:
        with path.open("a+b") as stream:
            if os.name == "nt":
                import msvcrt
                stream.seek(0, os.SEEK_END)
                if stream.tell() == 0:
                    stream.write(b"\0")
                    stream.flush()
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
                try:
                    yield
                finally:
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


class ExternalTrust:
    """Validated public trust bundle and independently signed registry ID."""

    def __init__(self, bundle: Mapping[str, Any], identity: Mapping[str, Any]):
        validate_m2_document("trust_bundle.schema.json", bundle)
        validate_m2_document("registry_identity.schema.json", identity)
        self.bundle = json.loads(json.dumps(bundle))
        self.identity = json.loads(json.dumps(identity))
        self.identity_authority = self._load_key(bundle["identity_authority"])
        self.initial_signer = self._load_key(bundle["initial_checkpoint_signer"])
        self.reviewers = {item["key_id"]: self._load_key(item) for item in bundle["catalog_reviewers"]}
        self.custodians = {item["key_id"]: self._load_key(item) for item in bundle["custodians"]}
        if identity["identity_authority_key_id"] != self.identity_authority[0]:
            raise TrustError("registry identity names an untrusted identity authority")
        signature = _decode_b64(identity["signature"], expected_length=64)
        digest = _signing_digest(identity, {"signature"})
        _verify_ed25519(self.identity_authority[1], signature, _DOMAIN_IDENTITY + digest,
                        "registry identity")
        self.identity_sha256 = _sha256(m1.canonical_json_bytes(identity))

    @staticmethod
    def _load_key(value: Mapping[str, Any]) -> tuple[str, bytes]:
        public = _decode_b64(value["public_key_base64"], expected_length=32)
        key_id = _key_id(public)
        if value["key_id"] != key_id:
            raise TrustError("public key ID does not match its raw key bytes")
        return key_id, public


class ExternalSecurityStore:
    """External durable files and checkpoint verification for one M1 registry."""

    def __init__(
        self,
        registry: m1.ExperimentRegistry,
        external_root: str | Path,
        trust: ExternalTrust,
        *,
        repository_root: str | Path | None = None,
        clock: Any | None = None,
    ):
        self.registry = registry
        self.root = _path_without_symlinks(Path(external_root))
        repo = _path_without_symlinks(Path(repository_root) if repository_root else _REPO_ROOT)
        registry_root = _path_without_symlinks(registry.root)
        if _is_within(self.root, repo) or _is_within(self.root, registry_root):
            raise SecurityError("external trust/checkpoint directory must be outside the repository and registry")
        if _is_within(repo, self.root) or _is_within(registry_root, self.root):
            raise SecurityError("external trust directory cannot contain the repository or registry")
        self.trust = trust
        self._clock = clock or m1._now_utc
        if trust.identity["search_family_id"] == "":
            raise TrustError("registry identity has an empty search family ID")
        self.checkpoints_dir = self.root / "checkpoints"
        self.catalogs_dir = self.root / "catalogs"
        self.receipts_dir = self.root / "catalog_receipts"
        self.generation_manifests_dir = self.root / "generation_manifests"
        self.controls_dir = self.root / "control_records"
        self.anchor_path = self.root / "checkpoint_anchor.json"
        self.catalog_anchor_path = self.root / "catalog_anchor.json"
        self.lock_path = self.root / ".external-state.lock"
        for directory in (self.checkpoints_dir, self.catalogs_dir, self.receipts_dir,
                          self.generation_manifests_dir, self.controls_dir):
            directory.mkdir(parents=True, exist_ok=True)
            if directory.is_symlink():
                raise SecurityError(f"external state directory is a symlink: {directory}")
            if os.name != "nt":
                os.chmod(directory, 0o700)

    @classmethod
    def from_files(
        cls,
        registry: m1.ExperimentRegistry,
        external_root: str | Path,
        *,
        expected_trust_bundle_sha256: str,
        repository_root: str | Path | None = None,
        clock: Any | None = None,
    ) -> "ExternalSecurityStore":
        root = _path_without_symlinks(Path(external_root))
        try:
            for name in ("trust_bundle.json", "registry_identity.json"):
                if (root / name).is_symlink():
                    raise TrustError(f"external trust file is a symlink: {name}")
            bundle = _strict_json_loads((root / "trust_bundle.json").read_bytes())
            identity = _strict_json_loads((root / "registry_identity.json").read_bytes())
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise TrustError("external trust bundle or identity is missing/corrupt") from exc
        if _sha256(m1.canonical_json_bytes(bundle)) != expected_trust_bundle_sha256:
            raise TrustError("external public trust bundle differs from the independently pinned digest")
        return cls(registry, root, ExternalTrust(bundle, identity), repository_root=repository_root, clock=clock)

    @classmethod
    def provision_external_trust(
        cls,
        external_root: str | Path,
        bundle: Mapping[str, Any],
        identity: Mapping[str, Any],
        *,
        repository_root: str | Path | None = None,
        registry_root: str | Path | None = None,
    ) -> Path:
        """Install externally issued public trust material exactly once.

        This accepts signed public documents; it does not generate IDs or
        signatures. Production provisioning should be performed by the
        identity authority/custodian, outside the registry process.
        """
        root = _path_without_symlinks(Path(external_root))
        repo = _path_without_symlinks(Path(repository_root) if repository_root else _REPO_ROOT)
        registry = _path_without_symlinks(Path(registry_root)) if registry_root else None
        if (_is_within(root, repo) or _is_within(repo, root)
                or (registry and (_is_within(root, registry) or _is_within(registry, root)))):
            raise SecurityError("external trust material must be outside repository and registry")
        trust = ExternalTrust(bundle, identity)
        root.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            os.chmod(root, 0o700)
        _publish_immutable(root / "trust_bundle.json", m1.canonical_json_bytes(trust.bundle))
        _publish_immutable(root / "registry_identity.json", m1.canonical_json_bytes(trust.identity))
        return root

    @contextmanager
    def serialized(self) -> Iterator[None]:
        with _exclusive_lock(self.lock_path):
            yield

    def _read_json(self, path: Path, what: str) -> dict[str, Any]:
        if path.is_symlink():
            raise CheckpointError(f"{what} is a symlink and cannot be trusted: {path}")
        try:
            value = _strict_json_loads(path.read_bytes())
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise CheckpointError(f"{what} is missing or corrupt: {path}") from exc
        if not isinstance(value, dict):
            raise CheckpointError(f"{what} must be a JSON object")
        return value

    def _validate_identity_match(self, registry_id: str, identity_sha: str) -> None:
        if registry_id != self.trust.identity["registry_id"]:
            raise CheckpointError("external checkpoint registry ID differs from independent identity")
        if identity_sha != self.trust.identity_sha256:
            raise CheckpointError("external checkpoint identity digest differs from trusted identity")

    def _verify_catalog_document(self, catalog: Mapping[str, Any]) -> str:
        validate_m2_document("template_catalog.schema.json", catalog)
        approved_projection = json.loads(json.dumps(catalog))
        approved_projection.pop("catalog_id", None)
        approved_projection.pop("approved_content_sha256", None)
        approved_projection["review"].pop("signature", None)
        approved_digest = _sha256(m1.canonical_json_bytes(approved_projection))
        if approved_digest != catalog["approved_content_sha256"]:
            raise TrustError("catalog approved-content digest mismatch")
        reviewer_id = catalog["review"]["reviewer_key_id"]
        if reviewer_id not in self.trust.reviewers:
            raise TrustError("catalog reviewer key is not in the external trust bundle")
        review_signature = _decode_b64(catalog["review"]["signature"], expected_length=64)
        _verify_ed25519(
            self.trust.reviewers[reviewer_id][1], review_signature,
            _DOMAIN_CATALOG_APPROVAL + bytes.fromhex(approved_digest), "catalog review",
        )
        id_projection = json.loads(json.dumps(catalog))
        id_projection.pop("catalog_id", None)
        expected_id = _content_id(m1.canonical_json_bytes(id_projection))
        if expected_id != catalog["catalog_id"]:
            raise TrustError("catalog content ID mismatch")
        # Structural/runtime constraints not expressible in JSON Schema.
        entries = catalog["entries"]
        ids = [entry["template_id"] for entry in entries]
        if len(ids) != len(set(ids)):
            raise SecurityError("catalog contains duplicate template IDs")
        from collections import Counter
        family_counts = Counter(entry["family"] for entry in entries)
        if len(entries) > 36 or any(count > 12 for count in family_counts.values()):
            raise SecurityError("catalog exceeds frozen family or total attempt ceilings")
        if catalog["review"]["approved_at_utc"] > catalog["frozen_at_utc"]:
            raise SecurityError("catalog freeze timestamp precedes approval")
        return catalog["catalog_id"]

    def _verify_receipt(self, receipt: Mapping[str, Any], catalog: Mapping[str, Any]) -> str:
        validate_m2_document("catalog_freeze_receipt.schema.json", receipt)
        self._validate_identity_match(receipt["registry_id"], receipt["registry_identity_sha256"])
        if receipt["search_family_id"] != self.trust.identity["search_family_id"]:
            raise TrustError("catalog receipt belongs to another persistent search family")
        for field in ("catalog_id", "catalog_version", "parent_catalog_id", "approved_content_sha256"):
            if receipt[field] != catalog[field]:
                raise TrustError(f"catalog receipt {field} differs from catalog")
        review_sig_hash = _sha256(_decode_b64(catalog["review"]["signature"], expected_length=64))
        if receipt["catalog_review_signature_sha256"] != review_sig_hash:
            raise TrustError("catalog receipt does not bind its reviewer signature")
        if receipt["identity_authority_key_id"] != self.trust.identity_authority[0]:
            raise TrustError("catalog receipt names an untrusted authority")
        digest = _signing_digest(receipt, {"signature"})
        _verify_ed25519(
            self.trust.identity_authority[1],
            _decode_b64(receipt["signature"], expected_length=64),
            _DOMAIN_CATALOG_FREEZE + digest,
            "catalog freeze receipt",
        )
        return _sha256(m1.canonical_json_bytes(receipt))

    def freeze_catalog(self, catalog: Mapping[str, Any], receipt: Mapping[str, Any]) -> str:
        """Publish a reviewed catalog/receipt before the first candidate attempt."""
        with self.serialized():
            result = self.registry.verify()
            if result.attempt_count:
                raise SecurityError("catalog is sealed after the first candidate attempt")
            catalog_id = self._verify_catalog_document(catalog)
            receipt_sha = self._verify_receipt(receipt, catalog)
            existing_anchor = None
            if self.catalog_anchor_path.exists():
                self.latest_catalog()  # authenticate the current receipt chain before advancing it
                existing_anchor = self._read_json(self.catalog_anchor_path, "catalog anchor")
                validate_m2_document("catalog_anchor.schema.json", existing_anchor)
                self._validate_identity_match(existing_anchor["registry_id"], existing_anchor["registry_identity_sha256"])
                if existing_anchor["search_family_id"] != self.trust.identity["search_family_id"]:
                    raise CheckpointError("catalog anchor is for another search family")
                expected_parent = existing_anchor["receipt_sha256"]
                expected_version = existing_anchor["freeze_sequence"] + 1
            else:
                expected_parent = None
                expected_version = 1
            if receipt["previous_receipt_sha256"] != expected_parent:
                raise CheckpointError("catalog receipt does not extend the currently trusted catalog chain")
            if receipt["freeze_sequence"] != expected_version or receipt["catalog_version"] != expected_version:
                raise CheckpointError("catalog version/receipt sequence is not the next monotonic version")
            if receipt["parent_catalog_id"] != (existing_anchor and self._latest_catalog_id(existing_anchor)):
                raise CheckpointError("catalog parent ID does not match the previous frozen catalog")
            _publish_immutable(self.catalogs_dir / f"{catalog_id}.json", m1.canonical_json_bytes(catalog))
            _publish_immutable(self.receipts_dir / f"{receipt_sha}.json", m1.canonical_json_bytes(receipt))
            anchor = {
                "schema_version": "athena.evolution.catalog_anchor.v1",
                "registry_id": self.trust.identity["registry_id"],
                "registry_identity_sha256": self.trust.identity_sha256,
                "search_family_id": self.trust.identity["search_family_id"],
                "freeze_sequence": expected_version,
                "receipt_sha256": receipt_sha,
                "anchor_revision": (existing_anchor["anchor_revision"] + 1) if existing_anchor else 1,
            }
            validate_m2_document("catalog_anchor.schema.json", anchor)
            _atomic_replace(self.catalog_anchor_path, m1.canonical_json_bytes(anchor))
            return receipt_sha

    def _latest_catalog_id(self, anchor: Mapping[str, Any]) -> str | None:
        receipt = self._read_json(self.receipts_dir / f"{anchor['receipt_sha256']}.json", "catalog receipt")
        return receipt.get("catalog_id")

    def latest_catalog(self) -> tuple[dict[str, Any], dict[str, Any], str]:
        anchor = self._read_json(self.catalog_anchor_path, "catalog anchor")
        validate_m2_document("catalog_anchor.schema.json", anchor)
        self._validate_identity_match(anchor["registry_id"], anchor["registry_identity_sha256"])
        if anchor["search_family_id"] != self.trust.identity["search_family_id"]:
            raise CheckpointError("catalog anchor search family mismatch")
        if anchor["anchor_revision"] != anchor["freeze_sequence"]:
            raise CheckpointError("catalog anchor revision is stale or inconsistent")
        receipt = self._read_json(self.receipts_dir / f"{anchor['receipt_sha256']}.json", "catalog receipt")
        catalog = self._read_json(self.catalogs_dir / f"{receipt['catalog_id']}.json", "frozen catalog")
        catalog_id = self._verify_catalog_document(catalog)
        receipt_sha = self._verify_receipt(receipt, catalog)
        if receipt_sha != anchor["receipt_sha256"] or receipt["freeze_sequence"] != anchor["freeze_sequence"]:
            raise CheckpointError("catalog receipt does not match protected anchor")
        # Walk the immutable receipt chain to sequence one. This catches gaps and
        # replacement even when the latest receipt itself is validly signed.
        current = receipt
        child_catalog = catalog
        while current["freeze_sequence"] > 1:
            previous_hash = current["previous_receipt_sha256"]
            expected_version = current["catalog_version"] - 1
            previous_receipt = self._read_json(self.receipts_dir / f"{previous_hash}.json", "prior catalog receipt")
            if previous_receipt["catalog_version"] != expected_version or previous_receipt["freeze_sequence"] != expected_version:
                raise CheckpointError("catalog version/receipt sequence has a gap")
            previous_catalog = self._read_json(self.catalogs_dir / f"{previous_receipt['catalog_id']}.json", "prior frozen catalog")
            if child_catalog["parent_catalog_id"] != previous_receipt["catalog_id"]:
                raise CheckpointError("catalog parent ID differs from prior receipt")
            self._verify_catalog_document(previous_catalog)
            if self._verify_receipt(previous_receipt, previous_catalog) != previous_hash:
                raise CheckpointError("catalog receipt chain hash mismatch")
            child_catalog = previous_catalog
            current = previous_receipt
        if current["catalog_version"] != 1 or current["previous_receipt_sha256"] is not None or child_catalog["parent_catalog_id"] is not None:
            raise CheckpointError("catalog receipt chain has an invalid root")
        return catalog, receipt, receipt_sha

    @staticmethod
    def _checkpoint_core(checkpoint: Mapping[str, Any]) -> dict[str, Any]:
        excluded = {"checkpoint_core_sha256", "checkpoint_sha256", "control_record_sha256", "signature"}
        return {key: value for key, value in checkpoint.items() if key not in excluded}

    @classmethod
    def checkpoint_core_digest(cls, checkpoint: Mapping[str, Any]) -> str:
        return _sha256(m1.canonical_json_bytes(cls._checkpoint_core(checkpoint)))

    @staticmethod
    def checkpoint_envelope_digest(checkpoint: Mapping[str, Any]) -> str:
        excluded = {"checkpoint_sha256", "signature"}
        projection = {key: value for key, value in checkpoint.items() if key not in excluded}
        return _sha256(m1.canonical_json_bytes(projection))

    def _control_digest(self, control: Mapping[str, Any]) -> bytes:
        signature_fields = {
            "identity_authority_signature", "current_signer_signature", "new_key_possession_signature",
        }
        fields = {key: value for key, value in control.items() if key not in signature_fields}
        fields["approvals"] = [
            {key: value for key, value in approval.items() if key != "signature"}
            for approval in control["approvals"]
        ]
        return hashlib.sha256(m1.canonical_json_bytes(fields)).digest()

    def _verify_control(
        self,
        control: Mapping[str, Any],
        *,
        current_signer: tuple[str, bytes] | None,
        expected_anchor: Mapping[str, Any] | None,
        checkpoint: Mapping[str, Any],
    ) -> tuple[str, bytes]:
        validate_m2_document("checkpoint_control_record.schema.json", control)
        if control["registry_id"] != self.trust.identity["registry_id"] or control["registry_identity_sha256"] != self.trust.identity_sha256:
            raise AuthorizationError("control record targets a different registry identity")
        expected_revision = expected_anchor["anchor_revision"] if expected_anchor else 0
        expected_hash = expected_anchor["checkpoint_sha256"] if expected_anchor else None
        if control["expected_anchor_revision"] != expected_revision or control["expected_checkpoint_sha256"] != expected_hash:
            raise AuthorizationError("control record does not bind the exact currently trusted anchor")
        core_sha = self.checkpoint_core_digest(checkpoint)
        if control["authorized_checkpoint_sha256"] != core_sha:
            raise AuthorizationError("control record does not authorize the exact proposed checkpoint core")
        if checkpoint["control_record_sha256"] != _sha256(m1.canonical_json_bytes(control)):
            raise AuthorizationError("checkpoint does not bind the exact signed control record")
        digest = self._control_digest(control)
        authority_id, authority_public = self.trust.identity_authority
        if control["identity_authority_key_id"] != authority_id:
            raise AuthorizationError("control record identity authority is not trusted")
        _verify_ed25519(authority_public,
                        _decode_b64(control["identity_authority_signature"], expected_length=64),
                        _DOMAIN_CONTROL + digest, "control identity-authority approval")
        approval_ids: set[str] = set()
        for approval in control["approvals"]:
            key_id = approval["custodian_key_id"]
            if key_id in approval_ids or key_id not in self.trust.custodians:
                raise AuthorizationError("control record has duplicate or untrusted custodians")
            approval_ids.add(key_id)
            _verify_ed25519(self.trust.custodians[key_id][1],
                            _decode_b64(approval["signature"], expected_length=64),
                            _DOMAIN_CONTROL + digest, "human custodian approval")
        action = control["action"]
        if control["new_signer_key_epoch"] != checkpoint["signer_key_epoch"]:
            raise AuthorizationError("checkpoint signer epoch differs from authorized new key epoch")
        if action == "ROTATE_SIGNING_KEY":
            if current_signer is None or control["current_signer_key_id"] != current_signer[0]:
                raise AuthorizationError("rotation is not authorized by the current signer")
            if control["expected_signer_key_epoch"] != checkpoint["signer_key_epoch"] - 1:
                raise AuthorizationError("rotation key epoch is not the next epoch")
            _verify_ed25519(current_signer[1],
                            _decode_b64(control["current_signer_signature"], expected_length=64),
                            _DOMAIN_CONTROL + digest, "current signer rotation approval")
        elif action == "RECOVER_CHECKPOINT":
            if expected_anchor is None and (control["expected_checkpoint_sha256"] is not None or control["expected_anchor_revision"] != 0):
                raise AuthorizationError("anchorless recovery must explicitly name the missing anchor")
            if expected_anchor is not None and control["expected_signer_key_epoch"] != expected_anchor["signer_key_epoch"]:
                raise AuthorizationError("recovery expected signer epoch does not match trusted anchor")
            if expected_anchor is None and control["expected_signer_key_epoch"] != 0:
                raise AuthorizationError("anchorless recovery must declare signer epoch zero")
            if checkpoint["signer_key_epoch"] != control["expected_signer_key_epoch"] + 1:
                raise AuthorizationError("recovery must activate exactly the next signer epoch")
        else:  # schema validator also enforces the enum
            raise AuthorizationError("unknown checkpoint control action")
        new_public = _decode_b64(control["new_public_key_base64"], expected_length=32)
        if _key_id(new_public) != control["new_signer_key_id"]:
            raise AuthorizationError("new signer key ID does not match its public key")
        _verify_ed25519(new_public,
                        _decode_b64(control["new_key_possession_signature"], expected_length=64),
                        _DOMAIN_KEY_POP + digest, "new signer proof of possession")
        if checkpoint["signer_key_id"] != control["new_signer_key_id"]:
            raise AuthorizationError("authorized checkpoint is not signed by the new key")
        return control["new_signer_key_id"], new_public

    def _load_checkpoint_by_hash(self, digest: str) -> dict[str, Any]:
        path = self.checkpoints_dir / f"{digest}.json"
        checkpoint = self._read_json(path, "checkpoint")
        validate_m2_document("registry_checkpoint.schema.json", checkpoint)
        if checkpoint["checkpoint_sha256"] != digest or self.checkpoint_envelope_digest(checkpoint) != digest:
            raise CheckpointError("checkpoint envelope digest mismatch")
        if self.checkpoint_core_digest(checkpoint) != checkpoint["checkpoint_core_sha256"]:
            raise CheckpointError("checkpoint core digest mismatch")
        return checkpoint

    def _checkpoint_chain(self, anchor: Mapping[str, Any]) -> list[dict[str, Any]]:
        chain: list[dict[str, Any]] = []
        digest = anchor["checkpoint_sha256"]
        expected_sequence = anchor["checkpoint_sequence"]
        while digest is not None:
            checkpoint = self._load_checkpoint_by_hash(digest)
            if checkpoint["checkpoint_sequence"] != expected_sequence:
                raise CheckpointError("checkpoint sequence gap or reorder")
            chain.append(checkpoint)
            digest = checkpoint["previous_checkpoint_sha256"]
            expected_sequence -= 1
            if expected_sequence < 0:
                raise CheckpointError("checkpoint chain is longer than its sequence")
        chain.reverse()
        if not chain or chain[0]["checkpoint_sequence"] != 1 or chain[0]["previous_checkpoint_sha256"] is not None:
            raise CheckpointError("checkpoint chain does not have a valid root")
        return chain

    def _trusted_anchor_without_database(self) -> tuple[dict[str, Any], dict[str, Any]]:
        """Authenticate the old anchor/chain even when SQLite has an unanchored suffix."""
        anchor = self._read_json(self.anchor_path, "checkpoint anchor")
        validate_m2_document("checkpoint_anchor.schema.json", anchor)
        self._validate_identity_match(anchor["registry_id"], anchor["registry_identity_sha256"])
        self.latest_catalog()
        events = self.registry.events()
        chain = self._checkpoint_chain(anchor)
        active = self.trust.initial_signer
        epoch = 1
        previous_anchor: Mapping[str, Any] | None = None
        for checkpoint in chain:
            self._validate_identity_match(checkpoint["registry_id"], checkpoint["registry_identity_sha256"])
            if checkpoint["search_family_id"] != self.trust.identity["search_family_id"]:
                raise CheckpointError("historical checkpoint search family mismatch")
            control_sha = checkpoint["control_record_sha256"]
            if control_sha is None:
                if checkpoint["signer_key_epoch"] != epoch or checkpoint["signer_key_id"] != active[0]:
                    raise CheckpointError("historical checkpoint signer/epoch mismatch")
            else:
                control = self._read_json(self.controls_dir / f"{control_sha}.json", "checkpoint control record")
                if _sha256(m1.canonical_json_bytes(control)) != control_sha:
                    raise CheckpointError("historical control-record hash mismatch")
                next_signer = self._verify_control(
                    control, current_signer=active, expected_anchor=previous_anchor, checkpoint=checkpoint
                )
                expected_epoch = 1 if previous_anchor is None and control["action"] == "RECOVER_CHECKPOINT" else epoch + 1
                if checkpoint["signer_key_epoch"] != expected_epoch:
                    raise CheckpointError("historical control checkpoint has an invalid key epoch")
                active = next_signer
                epoch = expected_epoch
            if checkpoint["signer_key_id"] != active[0]:
                raise CheckpointError("historical checkpoint uses the wrong signer")
            _verify_ed25519(
                active[1], _decode_b64(checkpoint["signature"], expected_length=64),
                _DOMAIN_CHECKPOINT + bytes.fromhex(checkpoint["checkpoint_sha256"]),
                "historical checkpoint",
            )
            previous_anchor = {
                "checkpoint_sequence": checkpoint["checkpoint_sequence"],
                "checkpoint_sha256": checkpoint["checkpoint_sha256"],
                "anchor_revision": checkpoint["checkpoint_sequence"],
                "signer_key_id": checkpoint["signer_key_id"],
                "signer_key_epoch": checkpoint["signer_key_epoch"],
                "previous_checkpoint_sha256": checkpoint["previous_checkpoint_sha256"],
            }
        last = chain[-1]
        for key in ("checkpoint_sequence", "checkpoint_sha256", "signer_key_id", "signer_key_epoch", "previous_checkpoint_sha256"):
            if anchor[key] != last[key if key != "checkpoint_sha256" else "checkpoint_sha256"]:
                raise CheckpointError("external anchor differs from its signed tip")
        if anchor["anchor_revision"] != last["checkpoint_sequence"]:
            raise CheckpointError("external anchor revision differs from signed checkpoint sequence")
        self._verify_checkpoint_prefixes(chain, events)
        return last, anchor

    def verify_latest(self, *, allow_empty: bool = True) -> dict[str, Any] | None:
        """Verify external identity, catalog anchor, full checkpoint chain and DB tip."""
        result = self.registry.verify()
        events = self.registry.events()
        if not self.anchor_path.exists():
            if allow_empty and not events:
                # A signed external identity, not DB contents, is the only
                # bootstrap trust when the database has no events.
                return None
            raise CheckpointError("trusted checkpoint anchor is missing")
        anchor = self._read_json(self.anchor_path, "checkpoint anchor")
        validate_m2_document("checkpoint_anchor.schema.json", anchor)
        self._validate_identity_match(anchor["registry_id"], anchor["registry_identity_sha256"])
        catalog, receipt, receipt_sha = self.latest_catalog()
        if not events:
            raise CheckpointError("checkpoint anchor exists for an empty registry")
        chain = self._checkpoint_chain(anchor)
        active = self.trust.initial_signer
        active_epoch = 1
        previous_anchor: Mapping[str, Any] | None = None
        for checkpoint in chain:
            self._validate_identity_match(checkpoint["registry_id"], checkpoint["registry_identity_sha256"])
            if checkpoint["search_family_id"] != self.trust.identity["search_family_id"]:
                raise CheckpointError("checkpoint search family differs from independent identity")
            if checkpoint["catalog_freeze_receipt_sha256"] != receipt_sha:
                raise CheckpointError("checkpoint catalog receipt differs from protected latest catalog")
            control_sha = checkpoint["control_record_sha256"]
            if control_sha is not None:
                control = self._read_json(self.controls_dir / f"{control_sha}.json", "checkpoint control record")
                if _sha256(m1.canonical_json_bytes(control)) != control_sha:
                    raise CheckpointError("control record digest mismatch")
                next_signer = self._verify_control(
                    control,
                    current_signer=active,
                    expected_anchor=previous_anchor,
                    checkpoint=checkpoint,
                )
                expected_epoch = 1 if previous_anchor is None and control["action"] == "RECOVER_CHECKPOINT" else active_epoch + 1
                if checkpoint["signer_key_epoch"] != expected_epoch:
                    raise CheckpointError("control checkpoint did not advance exactly one signer epoch")
                active = next_signer
                active_epoch = expected_epoch
            else:
                if checkpoint["signer_key_epoch"] != active_epoch:
                    raise CheckpointError("checkpoint signer key epoch is not the expected epoch")
                if checkpoint["signer_key_id"] != active[0]:
                    raise CheckpointError("checkpoint uses an unapproved signer key")
            if checkpoint["signer_key_id"] != active[0]:
                raise CheckpointError("checkpoint signature key differs from trusted active key")
            envelope_digest = bytes.fromhex(checkpoint["checkpoint_sha256"])
            _verify_ed25519(
                active[1], _decode_b64(checkpoint["signature"], expected_length=64),
                _DOMAIN_CHECKPOINT + envelope_digest, "registry checkpoint",
            )
            if previous_anchor is not None:
                if checkpoint["checkpoint_sequence"] != previous_anchor["checkpoint_sequence"] + 1:
                    raise CheckpointError("checkpoint sequence does not advance by one")
            previous_anchor = {
                "checkpoint_sequence": checkpoint["checkpoint_sequence"],
                "checkpoint_sha256": checkpoint["checkpoint_sha256"],
                "anchor_revision": checkpoint["checkpoint_sequence"],
                "signer_key_epoch": checkpoint["signer_key_epoch"],
            }

        last = chain[-1]
        if (anchor["checkpoint_sequence"] != last["checkpoint_sequence"]
                or anchor["checkpoint_sha256"] != last["checkpoint_sha256"]
                or anchor["signer_key_id"] != last["signer_key_id"]
                or anchor["signer_key_epoch"] != last["signer_key_epoch"]):
            raise CheckpointError("external latest anchor conflicts with the signed checkpoint")
        if anchor["previous_checkpoint_sha256"] != last["previous_checkpoint_sha256"]:
            raise CheckpointError("external anchor predecessor differs from signed checkpoint")
        if anchor["anchor_revision"] != last["checkpoint_sequence"]:
            raise CheckpointError("external anchor revision is stale or non-monotonic")
        if result.event_count != last["event_count"] or result.tip_sha256 != last["event_tip_sha256"]:
            raise CheckpointError("registry database is ahead/behind or differs from trusted checkpoint")
        if result.attempt_count != last["attempt_count"]:
            raise CheckpointError("registry attempt count differs from checkpoint")
        if last["protocol_id"] != self._latest_protocol(events)[0]:
            raise CheckpointError("checkpoint protocol identity is not the latest registered protocol")
        if last["attempt_manifest_sha256"] != self._latest_manifest(events):
            raise CheckpointError("checkpoint attempt-manifest commitment differs from registry")
        self._verify_checkpoint_prefixes(chain, events)
        self._verify_generation_manifest(last, events)
        return last

    @staticmethod
    def _latest_protocol(events: Sequence[Mapping[str, Any]]) -> tuple[str, Mapping[str, Any]]:
        for event in reversed(events):
            if event["event_type"] == "PROTOCOL_PRE_REGISTERED":
                return event["protocol_id"], event["payload"]["protocol"]
        raise CheckpointError("registry has no registered protocol")

    @staticmethod
    def _latest_manifest(events: Sequence[Mapping[str, Any]]) -> str:
        for event in reversed(events):
            if event["event_type"] == "CANDIDATE_SET_SEALED":
                return event["payload"]["attempt_manifest_sha256"]
        raise CheckpointError("registry has no sealed attempt manifest")

    def _verify_checkpoint_prefixes(
        self, chain: Sequence[Mapping[str, Any]], events: Sequence[Mapping[str, Any]]
    ) -> None:
        for checkpoint in chain:
            count = checkpoint["event_count"]
            if count < 1 or count > len(events):
                raise CheckpointError("checkpoint event count is outside current registry history")
            if events[count - 1]["event_sha256"] != checkpoint["event_tip_sha256"]:
                raise CheckpointError("historical checkpoint tip is not a prefix of the current registry")
            prefix = events[:count]
            protocol_id, protocol = self._latest_protocol(prefix)
            if protocol_id != checkpoint["protocol_id"]:
                raise CheckpointError("historical checkpoint protocol differs from registry prefix")
            protocol_sha = _sha256(m1.canonical_json_bytes(protocol))
            if protocol_sha != checkpoint["protocol_sha256"]:
                raise CheckpointError("protocol hash differs from historical checkpoint")
            if checkpoint["attempt_manifest_sha256"] != self._latest_manifest(prefix):
                raise CheckpointError("historical manifest differs from checkpoint")
            count_attempts = sum(event["event_type"] == "CANDIDATE_ATTEMPT_REGISTERED" for event in prefix)
            if count_attempts != checkpoint["attempt_count"]:
                raise CheckpointError("historical attempt count differs from checkpoint")
            self._verify_generation_manifest(checkpoint, prefix)

    def _verify_generation_manifest(
        self, checkpoint: Mapping[str, Any], events: Sequence[Mapping[str, Any]]
    ) -> dict[str, Any]:
        digest = checkpoint["generation_manifest_sha256"]
        manifest = self._read_json(self.generation_manifests_dir / f"{digest}.json", "generation manifest")
        validate_m2_document("generation_manifest.schema.json", manifest)
        if _sha256(m1.canonical_json_bytes(manifest)) != digest:
            raise CheckpointError("generation manifest digest differs from checkpoint")
        protocol_id, _protocol = self._latest_protocol(events)
        if manifest["protocol_id"] != protocol_id:
            raise CheckpointError("generation manifest protocol differs from registry prefix")
        registrations = [event for event in events if event["event_type"] == "CANDIDATE_ATTEMPT_REGISTERED"
                         and event["protocol_id"] == protocol_id]
        outputs = manifest["outputs"]
        if [row["attempt_id"] for row in outputs] != [event["payload"]["attempt_id"] for event in registrations]:
            raise CheckpointError("generation manifest output attempts differ from registered batch")
        for row, event in zip(outputs, registrations):
            if (row["candidate_spec_bytes_sha256"] != event["payload"]["candidate_spec_bytes_sha256"]
                    or row["family"] != event["payload"]["assigned_family"]):
                raise CheckpointError("generation manifest candidate bytes/family differ from registry")
        sealed = next((event for event in reversed(events)
                       if event["event_type"] == "CANDIDATE_SET_SEALED" and event["protocol_id"] == protocol_id), None)
        if sealed is None:
            raise CheckpointError("generation manifest protocol is not sealed")
        if manifest["catalog_id"] != self._catalog_id_for_receipt(checkpoint["catalog_freeze_receipt_sha256"]):
            raise CheckpointError("generation manifest catalog differs from checkpoint receipt")
        return manifest

    def _catalog_id_for_receipt(self, receipt_sha: str) -> str:
        receipt = self._read_json(self.receipts_dir / f"{receipt_sha}.json", "catalog receipt")
        return receipt["catalog_id"]

    def store_generation_manifest(self, manifest: Mapping[str, Any]) -> str:
        validate_m2_document("generation_manifest.schema.json", manifest)
        digest = _sha256(m1.canonical_json_bytes(manifest))
        _publish_immutable(self.generation_manifests_dir / f"{digest}.json", m1.canonical_json_bytes(manifest))
        return digest

    def _find_generation_manifest_for_current_registry(self) -> str:
        events = self.registry.events()
        protocol_id, _protocol = self._latest_protocol(events)
        matches: list[str] = []
        for path in self.generation_manifests_dir.glob("*.json"):
            try:
                value = self._read_json(path, "generation manifest")
                if value.get("protocol_id") != protocol_id:
                    continue
                digest = _sha256(m1.canonical_json_bytes(value))
                if path.stem == digest:
                    matches.append(digest)
            except (CheckpointError, SecurityError):
                continue
        if len(matches) != 1:
            raise CheckpointError("recovery requires exactly one intact generation manifest for the latest protocol")
        return matches[0]

    def _make_checkpoint_core(
        self,
        *,
        signer_key_id: str,
        signer_epoch: int,
        sequence: int,
        previous_sha: str | None,
        generation_manifest_sha256: str,
    ) -> dict[str, Any]:
        events = self.registry.events()
        verification = self.registry.verify()
        protocol_id, protocol = self._latest_protocol(events)
        _catalog, _receipt, receipt_sha = self.latest_catalog()
        checkpoint: dict[str, Any] = {
            "schema_version": "athena.evolution.registry_checkpoint.v1",
            "registry_id": self.trust.identity["registry_id"],
            "registry_identity_sha256": self.trust.identity_sha256,
            "search_family_id": self.trust.identity["search_family_id"],
            "protocol_id": protocol_id,
            "protocol_sha256": _sha256(m1.canonical_json_bytes(protocol)),
            "catalog_freeze_receipt_sha256": receipt_sha,
            "generation_manifest_sha256": generation_manifest_sha256,
            "control_record_sha256": None,
            "checkpoint_sequence": sequence,
            "event_count": verification.event_count,
            "event_tip_sha256": verification.tip_sha256,
            "attempt_manifest_sha256": self._latest_manifest(events),
            "attempt_count": verification.attempt_count,
            "previous_checkpoint_sha256": previous_sha,
            "created_at_utc": self._clock(),
            "signer_key_id": signer_key_id,
            "signer_key_epoch": signer_epoch,
            "signature_algorithm": "Ed25519",
        }
        checkpoint["checkpoint_core_sha256"] = self.checkpoint_core_digest(checkpoint)
        return checkpoint

    def _write_checkpoint(
        self,
        checkpoint: dict[str, Any],
        signer: ExternalSigner,
        *,
        previous_anchor: Mapping[str, Any] | None,
        fault_at: str | None = None,
    ) -> dict[str, Any]:
        envelope_sha = self.checkpoint_envelope_digest(checkpoint)
        checkpoint["checkpoint_sha256"] = envelope_sha
        key_id, signature = signer.sign(_DOMAIN_CHECKPOINT + bytes.fromhex(envelope_sha))
        if key_id != checkpoint["signer_key_id"]:
            raise TrustError("signer service key ID does not match checkpoint")
        public_key = self._active_public_for(checkpoint, previous_anchor)
        _verify_ed25519(public_key, signature, _DOMAIN_CHECKPOINT + bytes.fromhex(envelope_sha),
                        "new checkpoint signature")
        checkpoint["signature"] = _encode_b64(signature)
        validate_m2_document("registry_checkpoint.schema.json", checkpoint)
        _publish_immutable(self.checkpoints_dir / f"{envelope_sha}.json", m1.canonical_json_bytes(checkpoint))
        if fault_at == "after_checkpoint_publish":
            raise OSError("injected crash after immutable checkpoint publication")
        expected_revision = previous_anchor["anchor_revision"] if previous_anchor else 0
        current = self._read_json(self.anchor_path, "checkpoint anchor") if self.anchor_path.exists() else None
        if current != previous_anchor:
            raise CheckpointError("checkpoint anchor changed concurrently; refusing stale publication")
        anchor = {
            "schema_version": "athena.evolution.checkpoint_anchor.v1",
            "registry_id": checkpoint["registry_id"],
            "registry_identity_sha256": checkpoint["registry_identity_sha256"],
            "checkpoint_sequence": checkpoint["checkpoint_sequence"],
            "checkpoint_sha256": envelope_sha,
            "previous_checkpoint_sha256": checkpoint["previous_checkpoint_sha256"],
            "signer_key_id": checkpoint["signer_key_id"],
            "signer_key_epoch": checkpoint["signer_key_epoch"],
            "anchor_revision": expected_revision + 1,
        }
        validate_m2_document("checkpoint_anchor.schema.json", anchor)
        _atomic_replace(self.anchor_path, m1.canonical_json_bytes(anchor))
        if fault_at == "after_anchor_publish":
            raise OSError("injected crash after external anchor advancement")
        return checkpoint

    def _active_public_for(
        self, checkpoint: Mapping[str, Any], previous_anchor: Mapping[str, Any] | None
    ) -> bytes:
        # On ordinary writes the active key is already named by the latest
        # verified checkpoint; on initial writes it is the external trust key.
        if checkpoint["signer_key_id"] == self.trust.initial_signer[0]:
            return self.trust.initial_signer[1]
        control_sha = checkpoint.get("control_record_sha256")
        if control_sha:
            control = self._read_json(self.controls_dir / f"{control_sha}.json", "checkpoint control record")
            return _decode_b64(control["new_public_key_base64"], expected_length=32)
        if previous_anchor is None:
            raise TrustError("noninitial checkpoint requires an externally trusted active signer")
        active = self._active_signer_public(previous_anchor)
        if active[0] != checkpoint["signer_key_id"]:
            raise TrustError("no verified active public key for checkpoint signer")
        return active[1]

    def publish_after_registry_write(
        self,
        signer: ExternalSigner,
        *,
        expected_previous_anchor: Mapping[str, Any] | None,
        generation_manifest_sha256: str | None = None,
        fault_at: str | None = None,
    ) -> dict[str, Any]:
        """Checkpoint a committed M1 mutation; caller holds ``serialized()``."""
        anchor = self._read_json(self.anchor_path, "checkpoint anchor") if self.anchor_path.exists() else None
        if anchor != expected_previous_anchor:
            raise CheckpointError("registry write began from a different external anchor")
        if generation_manifest_sha256 is None:
            if anchor is None:
                raise CheckpointError("initial checkpoint requires a generation manifest commitment")
            previous_checkpoint = self._load_checkpoint_by_hash(anchor["checkpoint_sha256"])
            generation_manifest_sha256 = previous_checkpoint["generation_manifest_sha256"]
        if anchor is None:
            if self.registry.events():
                raise CheckpointError("registry has events but no trusted checkpoint")
            epoch = 1
            signer_id = self.trust.initial_signer[0]
            sequence = 1
            previous_hash = None
        else:
            epoch = anchor["signer_key_epoch"]
            signer_id = anchor["signer_key_id"]
            sequence = anchor["checkpoint_sequence"] + 1
            previous_hash = anchor["checkpoint_sha256"]
        core = self._make_checkpoint_core(
            signer_key_id=signer_id, signer_epoch=epoch,
            sequence=sequence, previous_sha=previous_hash,
            generation_manifest_sha256=generation_manifest_sha256,
        )
        return self._write_checkpoint(core, signer, previous_anchor=anchor, fault_at=fault_at)

    def propose_control_checkpoint_core(
        self,
        new_signer_key_id: str,
        new_signer_key_epoch: int,
        generation_manifest_sha256: str | None = None,
        *,
        recovery: bool = False,
    ) -> dict[str, Any]:
        """Prepare an exact state core for external human authorization.

        The returned canonical core is the object whose digest is placed in
        ``authorized_checkpoint_sha256``. It must be retained unchanged for
        retry and audit; apply will recheck it against current registry state.
        """
        with self.serialized():
            registry_changed_after_anchor = False
            try:
                last = self.verify_latest(allow_empty=True)
                anchor = self._read_json(self.anchor_path, "checkpoint anchor") if last else None
            except (CheckpointError, TrustError):
                if not recovery:
                    raise
                registry_changed_after_anchor = True
                try:
                    _last, anchor = self._trusted_anchor_without_database()
                except (CheckpointError, TrustError):
                    anchor = None
            if anchor is None:
                if not recovery:
                    raise AuthorizationError("missing checkpoint requires explicit recovery authorization")
                seq, prev, epoch = 1, None, 0
                if generation_manifest_sha256 is None:
                    generation_manifest_sha256 = self._find_generation_manifest_for_current_registry()
            else:
                seq, prev, epoch = anchor["checkpoint_sequence"] + 1, anchor["checkpoint_sha256"], anchor["signer_key_epoch"]
                if generation_manifest_sha256 is None:
                    if registry_changed_after_anchor:
                        generation_manifest_sha256 = self._find_generation_manifest_for_current_registry()
                    else:
                        latest = self._load_checkpoint_by_hash(anchor["checkpoint_sha256"])
                        generation_manifest_sha256 = latest["generation_manifest_sha256"]
            if generation_manifest_sha256 is None:
                raise CheckpointError("cannot propose a checkpoint without a generation manifest")
            if new_signer_key_epoch != epoch + 1:
                raise AuthorizationError("proposed control checkpoint must use exactly the next key epoch")
            core = self._make_checkpoint_core(
                signer_key_id=new_signer_key_id,
                signer_epoch=new_signer_key_epoch,
                sequence=seq,
                previous_sha=prev,
                generation_manifest_sha256=generation_manifest_sha256,
            )
            return core

    def _verify_control_signatures_only(
        self, control: Mapping[str, Any], *, current_signer: tuple[str, bytes] | None
    ) -> tuple[str, bytes]:
        validate_m2_document("checkpoint_control_record.schema.json", control)
        if control["registry_id"] != self.trust.identity["registry_id"] or control["registry_identity_sha256"] != self.trust.identity_sha256:
            raise AuthorizationError("control record targets a different identity")
        digest = self._control_digest(control)
        if control["identity_authority_key_id"] != self.trust.identity_authority[0]:
            raise AuthorizationError("control record authority is not trusted")
        _verify_ed25519(self.trust.identity_authority[1],
                        _decode_b64(control["identity_authority_signature"], expected_length=64),
                        _DOMAIN_CONTROL + digest, "control authority")
        ids: set[str] = set()
        for approval in control["approvals"]:
            key_id = approval["custodian_key_id"]
            if key_id in ids or key_id not in self.trust.custodians:
                raise AuthorizationError("control record needs two distinct trusted custodians")
            ids.add(key_id)
            _verify_ed25519(self.trust.custodians[key_id][1],
                            _decode_b64(approval["signature"], expected_length=64),
                            _DOMAIN_CONTROL + digest, "human custodian")
        if control["action"] == "ROTATE_SIGNING_KEY":
            if current_signer is None or current_signer[0] != control["current_signer_key_id"]:
                raise AuthorizationError("rotation requires the currently active signer")
            _verify_ed25519(current_signer[1],
                            _decode_b64(control["current_signer_signature"], expected_length=64),
                            _DOMAIN_CONTROL + digest, "current signer")
        new_public = _decode_b64(control["new_public_key_base64"], expected_length=32)
        if _key_id(new_public) != control["new_signer_key_id"]:
            raise AuthorizationError("new signer key ID mismatch")
        _verify_ed25519(new_public,
                        _decode_b64(control["new_key_possession_signature"], expected_length=64),
                        _DOMAIN_KEY_POP + digest, "new signer proof of possession")
        return control["new_signer_key_id"], new_public

    def apply_control_checkpoint(
        self,
        control: Mapping[str, Any],
        proposed_core: Mapping[str, Any],
        signer: ExternalSigner,
        *,
        fault_at: str | None = None,
    ) -> dict[str, Any]:
        """Apply a pre-authorized rotation/recovery to one exact checkpoint core.

        Normal operation is rotation with a valid current anchor. Recovery is
        explicit and may bootstrap a missing/corrupt anchor only when the
        control record names revision 0/null and authorizes the exact DB tip.
        """
        with self.serialized():
            try:
                old_checkpoint = self.verify_latest(allow_empty=True)
                old_anchor = self._read_json(self.anchor_path, "checkpoint anchor") if old_checkpoint else None
            except (CheckpointError, TrustError):
                if control.get("action") != "RECOVER_CHECKPOINT":
                    raise
                try:
                    old_checkpoint, old_anchor = self._trusted_anchor_without_database()
                except (CheckpointError, TrustError):
                    old_checkpoint = None
                    old_anchor = None
            if control.get("action") == "ROTATE_SIGNING_KEY" and old_checkpoint is None:
                raise AuthorizationError("signing-key rotation requires a verified current checkpoint")
            if control.get("action") == "RECOVER_CHECKPOINT" and self.anchor_path.exists() and old_anchor is None:
                # Preserve the corrupt pointer as immutable evidence. Explicit
                # authorization is required before a replacement is written.
                raw = self.anchor_path.read_bytes()
                _publish_immutable(self.root / "recovery_evidence" / f"corrupt-anchor-{_sha256(raw)}.bin", raw)
            if old_anchor is None:
                if control.get("action") != "RECOVER_CHECKPOINT":
                    raise AuthorizationError("only explicit recovery can establish a missing anchor")
                current_signer = None
                expected_anchor = None
                previous_epoch = 0
            else:
                current_signer = self._active_signer_public(old_anchor)
                expected_anchor = old_anchor
                previous_epoch = old_anchor["signer_key_epoch"]
            if control.get("expected_anchor_revision") != (expected_anchor["anchor_revision"] if expected_anchor else 0):
                raise AuthorizationError("control record expected anchor revision mismatch")
            if control.get("expected_checkpoint_sha256") != (expected_anchor["checkpoint_sha256"] if expected_anchor else None):
                raise AuthorizationError("control record expected checkpoint hash mismatch")
            if control.get("expected_signer_key_epoch") != previous_epoch:
                raise AuthorizationError("control record expected signer epoch mismatch")
            target_key_id = control["new_signer_key_id"]
            target_epoch = control["new_signer_key_epoch"]
            if target_epoch != previous_epoch + 1:
                raise AuthorizationError("new signer epoch must increment exactly once")
            core = json.loads(json.dumps(proposed_core))
            if core.get("signer_key_id") != target_key_id or core.get("signer_key_epoch") != target_epoch:
                raise AuthorizationError("proposed core signer does not match control record")
            fresh = self._make_checkpoint_core(
                signer_key_id=target_key_id,
                signer_epoch=target_epoch,
                sequence=core.get("checkpoint_sequence"),
                previous_sha=core.get("previous_checkpoint_sha256"),
                generation_manifest_sha256=core.get("generation_manifest_sha256"),
            )
            for field in fresh:
                if field == "created_at_utc":
                    continue
                if core.get(field) != fresh[field]:
                    raise AuthorizationError(f"proposed core no longer matches current state at {field}")
            control = json.loads(json.dumps(control))
            control_sha = _sha256(m1.canonical_json_bytes(control))
            core["control_record_sha256"] = control_sha
            # The core digest excludes the control-record reference by design.
            computed_core = self.checkpoint_core_digest(core)
            if control["authorized_checkpoint_sha256"] != computed_core:
                raise AuthorizationError("control record does not match exact current registry tip")
            self._verify_control(
                control, current_signer=current_signer,
                expected_anchor=expected_anchor, checkpoint=core,
            )
            _publish_immutable(self.controls_dir / f"{control_sha}.json", m1.canonical_json_bytes(control))
            if expected_anchor is None and self.anchor_path.exists():
                # Only reachable after explicit, fully validated recovery.
                self.anchor_path.unlink()
                _fsync_directory(self.root)
            return self._write_checkpoint(core, signer, previous_anchor=expected_anchor, fault_at=fault_at)

    def _active_signer_public(self, anchor: Mapping[str, Any]) -> tuple[str, bytes]:
        chain = self._checkpoint_chain(anchor)
        active = self.trust.initial_signer
        for item in chain:
            control_sha = item.get("control_record_sha256")
            if control_sha:
                control = self._read_json(self.controls_dir / f"{control_sha}.json", "checkpoint control")
                active = (control["new_signer_key_id"], _decode_b64(control["new_public_key_base64"], expected_length=32))
        if active[0] != anchor["signer_key_id"]:
            raise CheckpointError("active key derived from rotations differs from latest anchor")
        return active
