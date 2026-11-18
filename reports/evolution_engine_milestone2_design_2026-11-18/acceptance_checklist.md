# Milestone 2 Design and Implementation Acceptance Checklist

This checklist is normative for the proposed Milestone 2 package. The D0-1 through D0-11 protocol remains authoritative and unchanged. These checks gate implementation and acceptance; passing the schema checks in this design review does not claim the future implementation has passed them.

## A. Frozen M0/M1 and candidate grammar

- [ ] Compare all D0-1 through D0-11 numbers, formulas, and terminal rules with `reports/evolution_engine_milestone0_2026-11-18/frozen_protocol.md`; no M2 document or schema may override them.
- [ ] Candidate specs validate against the exact frozen M0 `candidate_spec.schema.json`; no M0/M1 schema bytes, ID function, protocol function, attempt ID, seed derivation, registry golden vector, or test fixture changes.
- [ ] Accept only the three frozen families, four existing strategies, M0 regimes, and declared policy operators. Reject numeric strategy/risk parameters, thresholds, weights, code/expression fields, plugins, callbacks, arbitrary Python, dynamic imports, adaptation, or undeclared fields.
- [ ] Candidate `experiment_id` equals protocol ID; family equals sealed attempt family; candidate semantic ID and locked baseline hashes verify; candidate parents are already registered and lineage is same-family.
- [ ] A submitted duplicate semantic candidate is still sealed as an attempt before candidate validation. Its outcome is preserved and counts toward the persistent multiplicity family; it cannot be deduplicated away.
- [ ] Enforce exactly 12 attempts per family and 36 total cumulatively across protocols, commits, and reruns, without reset, truncation, or reallocation. Failed, invalid, rejected, skipped, and unevaluated attempts remain counted; missing p-values remain 1.
- [ ] Enforce frozen M0 resources unchanged: 1 worker, 1,800 seconds per attempt, 86,400 seconds per family, 8,192 MiB peak RSS, 512 MiB per attempt, and 10 GiB per family.

## B. Catalog governance and priority semantics

- [ ] Schema-validate every catalog entry against the matching family definition in the frozen M0 schema; enforce unique `template_id`, no duplicate semantic candidate generated from a request, no more than 12 catalog templates per family, and no more than 36 total templates.
- [ ] Catalog v1 has no parent; every later version increments exactly by one and names the immediately previous catalog ID. Any edit or reorder creates a new version and a different content hash; overwrite is rejected.
- [ ] Recompute `approved_content_sha256` from the specified canonical projection, verify reviewer signature using a pretrusted catalog-review public key, then recompute `catalog_id`; mutation of content, reviewer, signature, parent, or version is detected.
- [ ] Require catalog review and freeze before any generation. Verify the catalog review signature, independent-authority freeze receipt, parent chain, and external latest-catalog anchor before accepting a request. Do not add M2 catalog event types to the frozen M0/M1 event schema.
- [ ] After the first candidate attempt is registered in a search family, reject a new catalog version for that family. Before the first attempt, a correction requires a new reviewed version, receipt, parent link, content hash, and monotonic anchor advancement; the original remains intact.
- [ ] Verify the request references the exact frozen catalog ID and version; unknown, repeated, or reordered template IDs produce the documented deterministic result. Request ID commits the complete request projection and ordered template list.
- [ ] Independently test `athena.regime_priority.first_eligible_in_declared_order_v1`: no eligible member abstains; one eligible member selects it; simultaneous eligible members select the first listed; reversing the list reverses the winner; selected stop/target/signal fields remain byte-equivalent to the selected source signal; unknown regime abstains.
- [ ] Separately test combination ties abstain and highest-confidence ties abstain as required by M0. Priority order never leaks into or changes combination semantics.

## C. Registry lifecycle and immutable accounting

- [ ] Register each complete submitted raw candidate batch atomically before candidate schema/lineage validation. Inject faults at serialization, DB transaction, event append, manifest seal, checkpoint signing, and anchor advance; no partial attempt batch is visible. A committed but unanchored suffix blocks all trusted operations.
- [ ] Validation takes only a registered attempt ID and reads exact immutable bytes by hash; raw candidate bytes cannot be supplied directly to the validator.
- [ ] Only the coordinator has registry write capability. Generator, validator, future evaluator, and report reader cannot update/delete historical rows, candidate bytes, protocols, manifests, event history, or catalog freezes.
- [ ] Tamper with/delete/reorder/replace attempt bytes, events, protocol, catalog, or generation manifest; fail integrity verification.
- [ ] Every generated attempt has exactly one registration and terminal or valid lifecycle state. No failed, invalid, rejected, skipped, or unevaluated attempt can disappear from the manifest, search-family ceiling, or multiplicity denominator.
- [ ] Generation manifest commits source commit and dirty state, catalog ID/version/freeze-receipt hash, request hash, protocol ID/hash, registry identity digest, ordered templates, candidate raw-byte hashes/IDs, attempt IDs/order, serializer ID, and manifest digest.
- [ ] No evaluator, fitness calculation, strategy call/mutation, parameter optimization, paper/live integration, or holdout access exists in this milestone.

## D. External identity, signature, checkpoint, rotation, and recovery

- [ ] Bootstrap requires a registry identity document signed by an independent identity authority whose public-key fingerprint is pinned outside Git and the registry database. The random registry ID is generated by that authority before database creation and bound to one persistent search-family ID.
- [ ] A fresh, empty registry with the matching external identity can bootstrap; a replaced database claiming a different ID, self-issued identity, missing trust root, altered identity record, or untrusted authority key fails closed.
- [ ] Permit a missing registry checkpoint only for a truly empty registry with a valid external identity. Any DB content without a verifiable checkpoint fails closed.
- [ ] Checkpoint body identity digest and protocol identity/hash match the trusted identity and actual protocol. Checkpoint sequence, event count, exact chain tip, sealed manifest digest/count, predecessor link, key ID/epoch, and Ed25519 signature all verify.
- [ ] Checkpoint and generation manifest bind the exact externally signed catalog-freeze receipt hash; test substitution of an older, later, or other-search-family receipt.
- [ ] Recompute the state-core digest and complete envelope digest from their separate canonical JCS projections; verify the Ed25519 signature over the envelope digest. Reject malformed Base64, wrong key/domain, changed state field, substituted control record, and signature substitution.
- [ ] External latest anchor has separate write protection and compare-and-swap monotonic revision. Replay an older signed checkpoint, roll back or replace the pointer, replace the full SQLite file, shorten/extend/reorder/rewrite the chain, remove checkpoint files, or provide DB-ahead/DB-behind state; every case fails closed.
- [ ] Private signing keys never appear in repository, database, CI, evaluator, or generator. Registry writer cannot access or replace key material. Trusted identity, signer keys, and latest anchor are external and independently permissioned.
- [ ] Normal rotation requires the current signer, independent identity authority, two distinct authorized custodians, new-key proof of possession, a bound current anchor/tip, activation checkpoint, and incremented key epoch. Verify old public keys/control records remain usable for historical validation after old private-key revocation.
- [ ] Recovery requires explicit human authorization: exact expected anchor and proposed state-core digest/tip, incident evidence hash, reason, review record, identity-authority signature, and signatures from two distinct authorized human custodians. No auto-reconstruct, auto-truncate, auto-reanchor, replacement, or fallback is permitted.
- [ ] Crash after DB commit/before signature, after checkpoint creation/before anchor advance, and during compare-and-swap; registry becomes unavailable. Only a valid human-authorized recovery control record can permit explicit advancement; otherwise it stays unavailable.
- [ ] Missing anchor, unavailable signer, missing key, failed signature, unrecognized key epoch, identity mismatch, sequence gap, or stale checkpoint prevents generation, validation, and any mutation. Diagnostic mode may report the error but cannot expose trusted candidate content.

## E. Clean-checkout reproducibility and cross-platform acceptance

- [ ] In a clean checkout at a recorded source commit, verify the external identity and checkpoint chain using only the exported trusted public identity/key bundle; no private key or production secret is needed.
- [ ] Rebuild the same locked catalog/request fixture and compare exact canonical candidate bytes, candidate IDs, attempt IDs/order, generation-manifest bytes/hash, registry event-chain tip, and checkpoint verification result.
- [ ] Test fixture uses fixed catalog/protocol/request timestamps and deterministic test-only Ed25519 keys kept outside source artifacts. Production bootstrap identity remains externally issued; clean checkout verifies it rather than minting a replacement.
- [ ] Record source commit, clean/dirty state, Python/runtime/dependency lock hashes, M0 schema bundle hash, protocol/catalog/request hashes, serializer version, external identity digest, public-key IDs/epochs, manifest/output hashes, checkpoint hash, and test seed/source fixture.
- [ ] Reproduce candidate bytes and all non-secret hashes on Ubuntu and Windows with Python 3.13. Signatures/checkpoints are verified against immutable fixture keys and timestamps; no production private key is embedded or regenerated.
- [ ] Reproduction refuses dirty source, changed schema bytes, missing dependency lock, changed catalog, changed request order, changed protocol, unavailable trust bundle, or prospective holdout paths.
- [ ] All new artifacts are written only to a new exclusive dated Milestone 2 report directory. Path traversal, symlink escapes, overwrite, and any Phase 3/4/5 historical or configuration path are rejected.

## F. Adversarial and schema consistency audit

- [ ] Meta-validate every M2 JSON Schema as Draft 2020-12; resolve all external `$ref`s against the frozen M0 candidate schema without copying or editing that schema.
- [ ] Verify schema required fields and `additionalProperties:false` agree with the architecture's exact identities, signatures, version links, checkpoint fields, and provenance fields.
- [ ] Adversarial catalog tests: duplicate template IDs, per-family/total ceiling overflow, family-policy mismatch, unknown strategy/regime/operator, reordered catalog, broken parent version, invalid review key, stale signature, and post-freeze mutation.
- [ ] Adversarial registry identity tests: self-asserted ID, DB replacement, identity/search-family mismatch, trusted key changed in checkout, invalid authority signature, and stale external identity record.
- [ ] Adversarial checkpoint tests: full replacement, deletion, event reordering, history rewrite, stale pointer, pointer rollback, wrong key epoch, invalid rotation chain, unauthorized recovery, reused custodian identity, altered evidence hash, and crash windows.
- [ ] Adversarial generation tests: duplicate candidate bytes, duplicate semantic IDs, unknown/reordered request templates, cross-family parent, forward reference, source dirtiness, oversized output, Unicode/JCS edge cases, timestamp mutation, and attempted holdout/network/file access.
- [ ] Run the complete existing regression suite unchanged, preserve M1 golden vectors, and pass clean-checkout Ubuntu and Windows Python 3.13 CI before M2 implementation is submitted for acceptance.

## G. Review gate and preservation

- [ ] Verify Phase 3/4/5 code, configurations, datasets, ledgers, reports, and output hashes against the M1 baseline; all remain unchanged.
- [ ] Verify historical M0/M1 protocols, registry records, schemas, and golden vectors are unchanged; no historical evaluations run during M2.
- [ ] Resolve the five documented decisions before implementation. Final user review of this package is the implementation gate. After implementation authorization, complete the entire Milestone 2 scope and its full regression/reproducibility evidence before requesting milestone acceptance; do not begin Milestone 3/evaluation.
