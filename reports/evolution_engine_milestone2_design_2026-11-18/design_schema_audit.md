# Milestone 2 Design-to-Schema Consistency Audit

**Audit date:** 2026-11-19  
**Repository baseline reviewed:** `cada644334d81119a63a1c5ba5f745d73579f909`  
**Scope:** Architecture, draft M2 schemas, frozen M0 protocol/schemas, and M1 registry contracts. No runtime implementation, source edits, historical evaluation, or holdout access.

## 1. Audit conclusion

The revised design is internally consistent after resolving a compatibility issue found during this audit: frozen M0 `registry_event.schema.json` has a closed event-type enum, and M1 registry code rejects unknown event types. Therefore catalog freezing is represented by a separately signed and externally anchored `catalog_freeze_receipt`, not a new registry event. The normal M1 event schema, protocol bytes, registry lifecycle, and golden vectors remain unchanged. The receipt digest is bound into the generation manifest and the signed registry checkpoint.

All ten draft M2 schemas pass Draft 2020-12 meta-validation, and their external candidate-policy references resolve to the frozen M0 candidate schema. Structural adversarial fixtures pass for catalog family-policy matching, successor parent requirements, unknown fields, initial receipt parent rules, key-rotation signature requirements, recovery shape, and duplicate custodian IDs. Cryptographic signature verification, registry behavior, cross-platform reproduction, and CI remain implementation acceptance work; they are not claimed complete by this design-only audit.

## 2. Decision traceability

| Finalized decision | Architecture | Schema contract | Acceptance tests |
|---|---|---|---|
| Private checkpoint keys external; controlled rotation and recovery | §4 key custody, rotation, recovery | `registry_identity`, `registry_checkpoint`, `checkpoint_anchor`, `checkpoint_control_record` | Checklist D: key isolation, rotation, recovery, crash cases |
| No automatic checkpoint reconstruction/replacement | §4 fail-closed/recovery procedure and two-stage core/envelope hash | `checkpoint_control_record` authorizes exact state-core digest and expected anchor; `registry_checkpoint` envelope binds control-record digest | Checklist D and adversarial checkpoint tests |
| Deterministic priority and tie-breaking | §3 family operators; semantic ID `athena.regime_priority.first_eligible_in_declared_order_v1` | `template_catalog.regime_priority_semantics_id` and frozen M0 ordered `regime_priority` arrays | Checklist B tests one/multiple eligible, reordered ties, abstention, preserved originating fields |
| Versioned, reviewed, frozen catalog | §3 canonical projection; §5 lifecycle; §8 decision 4 | `template_catalog`, `catalog_freeze_receipt`, `catalog_anchor`, request and generation manifest | Checklist B tests parent chain, signatures, order/hash changes, freeze-before-generation, post-attempt lock |
| Registry identity independent from DB contents | §4 bootstrap and trust boundary | `registry_identity` signed by independent authority; identity digest included by checkpoint and anchor | Checklist D tests self-issued IDs, DB replacement, trust-root changes, empty-only bootstrap |

## 3. Frozen protocol compatibility audit

- Frozen M0 `candidate_spec.schema.json` remains the only candidate grammar. The catalog schema references its regime-specialization, combination, and regime-allocation policy definitions by their existing `$id` and `$defs` names.
- Frozen M0 `registry_event.schema.json` and its event enum remain unchanged. The design does not add `TEMPLATE_CATALOG_FROZEN` or other event types to M0/M1.
- M1 `register_protocol_with_attempts()` remains the candidate admission contract. Candidate raw bytes are atomically registered and sealed before candidate schema/lineage validation; external checkpoint verification follows each committed registry mutation.
- Protocol ID, candidate ID, attempt ID, schema-bundle digest, RFC 8785 serialization, seed derivation, and golden-vector bytes remain unchanged. Catalog and request identities are new M2-specific IDs, separate from frozen IDs.
- The catalog ID and receipt are carried in M2 generation provenance and the signed checkpoint rather than inserted into the frozen M0 protocol. The protocol hash remains independently committed by the checkpoint.
- The M0 values remain authoritative: 12 attempts per family; 36 cumulative attempts per search family; all four hypotheses and all failed/invalid/skipped/unevaluated attempts retained; resource limits 1 worker, 1,800 seconds per attempt, 86,400 seconds per family, 8,192 MiB RSS, 512 MiB per attempt and 10 GiB per family. Fold, purge, warm-up, sample, bootstrap, multiplicity, cost, and holdout rules are not restated or changed by M2.
- Historical data through 2026-08-19 remains previously observed; M2 has no evaluator and no holdout capability.

Frozen-file SHA-256 values observed in this checkout for preservation reference:

| File | SHA-256 |
|---|---|
| M0 `candidate_spec.schema.json` | `34dbaee1084a46d17afd25ff1989fb20b0cb357e4540e74fab06d38ef4b3a1d5` |
| M0 `registry_event.schema.json` | `fb8cf11c16f0b22fa087e2b5412d3cbdff7239f6e35c6a840ec673a69466e8be` |
| M0 `experiment_protocol.schema.json` | `ad52f830d6dd9918fe9cf6d795942e294ceb9da3ce33b6514e40c7136c024975` |
| M0 `frozen_protocol.md` | `a1c9e4bf1bfbf99d4b3709e4d7cdae16c7164629c26dae8f7d1d77007cef7b8a` |
| M1 `tests/test_experiment_registry.py` | `66a2f751761e79214c52dd23d87308e8feba2698f572b4e392c2b57bde137d06` |

## 4. Schema inventory and consistency checks

The M2 schema set is:

1. `template_catalog.schema.json` — versioned finite catalog, family-specific M0 policies, content/review hashes, signed review, parent link, freeze time, fixed priority semantic ID.
2. `catalog_freeze_receipt.schema.json` — independent-authority signature, registry/search-family binding, monotonic receipt sequence and predecessor.
3. `catalog_anchor.schema.json` — protected pointer to the latest catalog receipt.
4. `generation_request.schema.json` — protocol/catalog/version binding and ordered template IDs.
5. `generation_manifest.schema.json` — source state, protocol/request/catalog receipt/identity hashes and exact output IDs/bytes.
6. `registry_identity.schema.json` — independently issued registry/search-family identity.
7. `registry_checkpoint.schema.json` — protocol and identity binding, catalog receipt, event tip, manifest/count, signer key epoch, signature.
8. `checkpoint_anchor.schema.json` — protected current checkpoint pointer and signer epoch.
9. `checkpoint_control_record.schema.json` — signed key-rotation or exact-tip recovery authorization with distinct human custodians.
10. `trust_bundle.schema.json` — external public identity, initial signer, catalog reviewer, and custodian keys; private keys are excluded.

Validation executed with Python 3.13 and `jsonschema` Draft 2020-12: all 10 M2 schemas and the frozen M0 candidate schema loaded; all schemas meta-validated; all `$ref`s resolved. Structural fixture checks accepted a valid catalog, freeze receipt, rotation control, and recovery control, and rejected family/policy mismatch, missing successor parent, unknown catalog field, invalid initial receipt parent, rotation missing current-signer authorization, and duplicate custodian IDs.

Structural rules too relational for JSON Schema are explicitly runtime checks: per-family catalog count ≤12; unique template IDs; parent catalog version increments by one; receipt/anchor sequence monotonicity; receipt matches latest anchor and registry identity; source family matches candidate; same-family candidate parents already registered; signature/key identity checks; protocol and candidate hashes; cumulative 36-attempt limit; exact chain/event/manifest reconciliation; key epoch transition; and absence of a catalog change after first candidate registration in that search family.

All M2 schemas use `additionalProperties:false` at document boundaries. Signature schemas validate encoding shape only; cryptographic validity is an independent verifier obligation and is covered by implementation acceptance tests.

## 5. Adversarial review covered by the acceptance plan

- Full database replacement with a newly self-asserted registry ID, copied valid old DB under another identity, missing/replaced authority key, or modified identity record.
- Event deletion, insertion, reordering, payload rewrite, candidate-byte rewrite, manifest replacement, full chain rewrite, checkpoint replay, pointer rollback, DB ahead/behind anchor, and orphan suffix after a crash.
- Catalog edits with unchanged claimed hash, changed review key/signature, same-version overwrite, reordered entries, duplicate IDs, wrong-family policy, invalid predecessor, stale or nonlatest receipt, and a catalog switch after attempt registration.
- Rotation with wrong current key/epoch/anchor, no new-key proof, duplicate human approver identity, invalid authority signature, or replayed control record; recovery with altered evidence/tip, no explicit human approval, unavailable trust authority, or an unauthorized replacement checkpoint.
- Candidate batches with duplicate semantic IDs, duplicate raw bytes, forward/cross-family lineage, wrong protocol/family/baseline, over-ceiling family counts, path traversal, symlink escape, dirty source, noncanonical Unicode/JCS values, unexpected timestamp, network/data/holdout access, or generated-code fields.
- A clean-checkout fixture reproduces exact catalog/request/candidate/attempt/manifest nonsecret hashes and verifies the event chain/checkpoint using only externally exported public trust material. It never recreates production identity or private keys.

## 6. Clean-checkout reproducibility contract

The eventual M2 run record must capture source commit and clean/dirty status, Python 3.13/runtime and locked dependency hashes, unchanged M0 schema-bundle hash, frozen protocol ID/hash, catalog ID/version/review/receipt hashes, catalog-anchor revision, request ID/hash/order, generator source hash, canonical serializer ID, candidate bytes and IDs, attempt IDs/order, generation manifest hash, registry identity digest, public-key IDs and epochs, checkpoint sequence/hash, anchor revisions, and all output hashes.

Clean-checkout reproduction uses fixed fixture timestamps, frozen input fixtures and test-only deterministic key material outside source artifacts. It regenerates declarative candidate bytes and nonsecret hashes, then verifies signatures with the fixture public-key bundle. It verifies production identity/checkpoints from externally supplied public trust records but does not mint replacement IDs or require production private keys. Ubuntu and Windows Python 3.13 must produce identical canonical candidate, attempt, manifest, and event-chain hashes.

## 7. Audit limits and preservation

This review ran JSON parsing, Draft 2020-12 schema checks, reference resolution, and structural fixture checks only. It did not run the historical test suite, Phase 3/4/5 replay, candidate generation against repository data, or CI. No tracked source files or frozen historical artifacts were changed. Only the new dated Milestone 2 design package is modified; other pre-existing untracked reports were left untouched.
