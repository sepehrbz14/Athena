# Athena Evolution Engine — Milestone 2 Design Proposal

**Status:** Design for review; no Milestone 2 code is authorized by this document.  
**Repository baseline:** `cada644334d81119a63a1c5ba5f745d73579f909` on `codex/evolution-engine-milestone1-immutable-registry`.  
**Scope:** Candidate representation/catalog, deterministic bounded generation, registry admission, and external registry checkpoint design. Candidate evaluation, strategy changes, optimization, paper trading, live trading, and holdout access are excluded.

## 1. Repository-grounded contracts

Milestone 2 reuses the frozen Milestone 0 files in `reports/evolution_engine_milestone0_2026-11-18/` and the append-only registry in `crypto_bot/experiment_registry.py`.

- `candidate_spec.schema.json` is the authoritative candidate document schema. It already limits strategies to `trend_following`, `breakout`, `momentum`, and `mean_reversion`, and exposes three families: `regime_specialization`, `signal_combination`, and `regime_selection_allocation`.
- Its finite operators are regime allowlists with `unknown=abstain`; the four declared signal combination rules with tie abstention and retained originating signal fields; and regime priority with the frozen Phase 4 risk budget and unknown-regime abstention. It has no weights, numeric strategy/risk parameters, thresholds, code fields, or adaptive operators.
- Candidate/protocol identity functions, attempt IDs, the RFC 8785 implementation, the unsigned 64-bit D0 seed derivation, M0 schemas, and the registry’s event lifecycle are authoritative. Existing golden values in `tests/test_experiment_registry.py` must stay byte-for-byte unchanged.
- `register_protocol_with_attempts()` is the current atomic admission path: protocol, exact candidate bytes and hashes, registration events, and the sealed attempt manifest become visible in one SQLite transaction. `validate_attempt()` requires an already sealed attempt.
- Persistent limits already encoded by D0 are 12 attempts per family and 36 total for the linked search family. The budget, four-hypothesis multiplicity accounting, frozen cost and chronology policy, and statuses cannot be reset by a generator request or registry checkpoint.

Candidate assembly must also respect current M1 validation: `experiment_id` is exactly the registered protocol ID; candidate `family` equals the sealed attempt family; `candidate_id` is recomputed by the unchanged semantic ID function; and candidate baseline source/configuration hashes must equal the locked protocol. Candidate parent candidates must already have been registered. A single generation batch therefore cannot create forward references or parent-child dependencies within the same batch.

The current store has SQLite append/delete triggers and a hash chain, but no stable external registry identity or independently protected latest-tip anchor. Milestone 2 must add an anchor outside the repository and registry directory without changing candidate/protocol/attempt golden vectors.

## 2. Component architecture and contracts

| Component | Contract | Must not do |
|---|---|---|
| Frozen Template Catalog | Immutable, content-addressed allowlist of candidate fragments using only M0 family grammar. Catalog hash is fixed before any result is accessible. | Read market/research data, generate code, contain arbitrary callbacks, change D0 rules. |
| Catalog Review and Freeze Authority | Verifies catalog reviewer signatures and issues an externally protected append-only freeze receipt and monotonic catalog anchor before generation. | Modify the registry DB, generate candidates, or approve a catalog from outcome data. |
| Generation Request | Immutable ordered list of catalog template IDs for one already locked protocol. Carries protocol identity and requested families; family counts and cumulative counts are checked against M0. | Carry thresholds, weights, per-candidate numeric values, outcomes, or a post-result selection rule. |
| Pure Bounded Generator | Maps `(locked protocol, catalog hash, request)` to exact canonical candidate JSON bytes and generation provenance. Deterministic order; no random candidate choice. | Import or execute strategy code, open data files, read the holdout, fit transforms, inspect results, call network, use `eval`/`exec`, or generate arbitrary Python. |
| Candidate Set Coordinator | The only Milestone 2 writer-facing component. Produces the entire byte batch, then calls the M1 atomic registry admission operation with a generation-manifest hash attached to the sealed event. It invokes schema/lineage validation only after the registration transaction commits and its external checkpoint verifies. | Partially validate candidates before the manifest is sealed or omit/rewrite an attempt. |
| Candidate Validator | Receives an attempt ID from the sealed registry and validates the already stored raw bytes against the frozen M0 schema, family, protocol, baseline hashes, and parent lineage. | Accept bytes directly from the generator or modify stored candidate bytes. |
| Registry Read View | Read-only protocol, attempt, manifest, lifecycle and integrity view for later consumers. | Provide direct mutable SQLite access to an evaluator. |
| Checkpoint Signer | Small separate process/API that signs monotonically advancing registry tips and updates the protected latest-checkpoint anchor. The private key is never exposed to the registry, generator, validator, or future evaluator. | Rewrite the registry, sign an older tip, sign a different registry identity, or bypass the external latest pointer. |
| Checkpoint Verifier | Verifies signatures, the externally stored latest checkpoint, registry identity, protocol identity, sequence, event count, and exact chain tip before registry use. | Fall back to an older checkpoint or continue with an unanchored suffix. |

The generator is a pure function over small declarative documents. The coordinator owns registry write capability. The validator sees only registered attempt IDs. A future evaluator must receive a read-only validated-attempt value and no registry write connection or signer capability. No evaluator is implemented in Milestone 2.

## 3. Candidate grammar and generation behavior

The M0 candidate schema remains unchanged. A separate catalog narrows the M0 grammar to a finite list of named templates before a protocol is run. It does not add grammar operators.

### Common candidate fields

- `family`, `strategy_members`, `parents`, and `policy` must be present in each catalog entry and fit the frozen M0 schema.
- Strategies and regimes must come from the M0 enums. Parent IDs must be a frozen incumbent component or a candidate already registered in the same family; no implicit parent inference.
- Baseline source, simulator/publication commits, Phase 4 configuration, and frozen input-manifest hashes are copied from the locked protocol inputs and verified unchanged.
- The generator sets candidate `created_at_utc` deterministically from the locked protocol timestamp. Candidate ID uses the M0 canonical semantic-ID function. The exact canonical bytes are retained because attempt ID is based on their raw-byte hash.
- Catalog entries are keyed by immutable `template_id`; duplicate template IDs are a malformed request and are rejected before submission. If two submitted templates generate the same semantic candidate ID, neither is deduplicated: both raw specifications are registered first, then duplicate semantics are recorded through the registry's invalid-attempt transition and both attempts remain in the multiplicity family. No random ordering, random sampling, or outcome-driven slot selection is permitted.

### Family operators

1. **Regime specialization:** exact allowlist maps over known M0 regimes and strategies; classifier hash remains the pinned repository classifier; unknown abstains. A catalog entry must define the mapping explicitly for each regime whose behavior is asserted. Omitted regime semantics must not be invented by the generator.
2. **Signal combination:** only `incumbent_ensemble`, `unanimous_direction`, `majority_direction`, or `highest_existing_confidence`; fixed declared members; ties abstain; originating stop/target/signal fields remain attached. The generator does not recalculate or compare confidence values.
3. **Regime selection/priority:** exact priority lists over existing strategies by regime; risk budget stays `frozen_phase4`; unknown abstains. No fractional weights or budget split is allowed. Frozen semantic ID `athena.regime_priority.first_eligible_in_declared_order_v1` means: read the immutable per-strategy signal snapshot at the same UTC decision time; walk the declared array left-to-right; a present, non-abstaining record that satisfies the frozen signal-source contract is eligible; the first eligible member wins. An absent/abstaining signal moves to the next member; a malformed signal input fails closed rather than being silently skipped. If none is eligible, abstain. The selected member supplies its original signal, stop, target, and related fields unchanged. The ordered list is the explicit tie-break; lexical sorting or unordered-map iteration is forbidden. Milestone 2 records and independently tests this pure selection rule over fixed fixtures but does not invoke strategy code or execute policies.

### Bounded generation algorithm

1. Verify the independent registry identity, exact latest frozen catalog receipt/anchor, and (if the registry already has events) the current external checkpoint. An empty registry with a valid external identity is permitted only for its first atomic registration. Require historical classification and `research_only=true` for the offline generation path.
2. Validate request-level structure against the catalog hash, protocol family declarations, template IDs, and cumulative attempt counts. A malformed request that has not submitted candidate bytes is rejected as a request and creates no candidate attempt. Reject requests exceeding 12 per family or 36 for the persistent search family. Do not silently truncate or reallocate.
3. Build all requested candidate documents in memory from exact catalog fragments. Canonicalize once using the M1 RFC 8785 serializer and derive candidate IDs using the unchanged M1 algorithm. Compute candidate byte hashes and a generation manifest containing the generator source commit/dirty flag, catalog hash, request hash, protocol ID, template order, candidate IDs, exact byte hashes, and output attempt order.
4. Pass the complete batch to the coordinator. The coordinator calls the registry registration transaction once; no candidate schema/lineage validation or evaluation starts until the transaction and external checkpoint both verify. A duplicate semantic candidate produced from two submitted templates is still registered as two raw-byte attempts before validator disposition; the M2 coordinator records the duplicate disposition through an append-only M1 lifecycle transition, never by editing candidate bytes or deleting an attempt.
5. After registration and checkpoint verification, validate each registered attempt by attempt ID. Invalid and failed attempts remain sealed and counted. No regeneration, retry substitution, or deletion is allowed; a new request requires a new linked protocol revision and consumes the same cumulative budget.

The request explicitly lists the template IDs and their order. The generator does not choose which templates to include based on outcomes. The D0 master seed remains reserved for its specified statistical use and is not used for candidate selection.

### New document IDs and canonical projections

- `catalog_id = "sha256:" + SHA256(JCS(catalog with catalog_id omitted))`. The catalog approval signature is included in this final ID; its signature signs the separate `approved_content_sha256` projection (all catalog fields except `catalog_id`, `approved_content_sha256`, and `review.signature`).
- The catalog reviewer signs `UTF8("ATHENA-CATALOG-APPROVAL-v1\0") || raw_32_byte_approved_content_sha256`; `approved_content_sha256` hashes JCS excluding `catalog_id`, `approved_content_sha256`, and only the review signature. The identity authority signs SHA-256 of JCS for the freeze-receipt body with `signature` omitted using the distinct `ATHENA-CATALOG-FREEZE-v1\0` domain. The receipt hash stored in the protected catalog anchor is SHA-256 of the complete canonical signed receipt. The latest-receipt anchor must match the receipt selected by the generation request, and receipt `freeze_sequence` must match its catalog version and parent chain.
- `request_id = "sha256:" + SHA256(JCS(request with request_id omitted))`; all remaining request fields, including ordered `template_ids` and the protocol-derived candidate timestamp, are identity-bearing.
- `request_sha256` and the generation-manifest digest are SHA-256 over the exact canonical UTF-8 JCS bytes. The generation-manifest digest is distinct from its output attempt-manifest digest.
- Generated candidate `experiment_id` equals the locked protocol ID, `candidate_revision` is `1`, `created_at_utc` equals the protocol's locked timestamp, and its baseline block is copied field-for-field from that protocol's source/configuration commitments. Candidate ID calculation follows M1's existing semantic projection exactly; no new identity algorithm is introduced.
- Catalog entry order is preserved as stored; request order is explicit. The catalog is frozen before the protocol's results are available. Reordering catalog entries changes the catalog ID; reordering request template IDs changes the request ID and deterministic output order.

## 4. External integrity checkpoint design

### Registry identity and trust separation

An independent identity authority creates a random 256-bit registry ID before the registry database is initialized, then issues the signed `registry_identity.schema.json` record binding that ID to exactly one persistent search family. The authority's public-key fingerprint is pinned in OS-managed trusted configuration outside both the repository and registry directory. The registry database is never permitted to mint or redefine its own identity. On bootstrap, the verifier must match the empty/new database to the externally trusted identity record; an existing database with a different identity is rejected. Only an empty database with the matching external identity may have no checkpoint; after any event exists, a missing checkpoint is a hard integrity failure. The first protocol and its hash are committed by the first signed checkpoint.

The registry ID is 32 bytes drawn from the identity authority's cryptographic random source and encoded as the M0 `sha256:`-shaped content-ID string; it is not calculated from a DB row. Identity signature digest is SHA-256 of JCS for the identity object with only `signature` omitted, signed over `UTF8("ATHENA-REGISTRY-IDENTITY-v1\0") || raw_digest`. `registry_identity_sha256` in downstream artifacts hashes the complete canonical signed identity document.

Ed25519 private checkpoint keys are held in an OS credential vault, hardware-backed key store, or custodian signing device outside the repository, registry database, CI, and evaluator environment. The registry writer cannot read or replace key material. The trusted public-key set and monotonic latest-checkpoint pointer are stored independently from the database and have separate write permissions. A signature alone cannot prevent replay; the protected latest pointer and trusted identity record are mandatory.

**Key rotation:** rotate before the current key's retirement date or suspected exposure. Create a new Ed25519 key in the protected key service; issue a `ROTATE_SIGNING_KEY` control record binding the registry identity, expected current anchor revision/tip, old and new key IDs, and activation checkpoint. The current signer, independent identity authority, and two distinct authorized human custodians sign the canonical control record. The verifier checks new-key proof of possession, advances the trusted key epoch and anchor with compare-and-swap, and accepts the new key only at the bound checkpoint. Retain old public keys and control records indefinitely for historical verification; destroy/revoke retired private keys after the transition is verified. If the current private key is unavailable or suspected compromised, use the recovery path instead of pretending the old signature is valid.

### Checkpoint body and signing

The signed `registry_checkpoint.schema.json` records:

- schema version, registry ID and trusted registry-identity digest, persistent `search_family_id`, latest protocol ID and protocol hash, latest catalog-freeze receipt digest, and latest generation-manifest digest;
- monotonically increasing checkpoint sequence and event count;
- exact latest `event_sha256` tip, latest sealed attempt-manifest digest, and cumulative attempt count;
- previous checkpoint digest, optional exact key-control-record digest, signer key ID and key epoch, UTC creation time, and signature algorithm;
- state-core digest, complete checkpoint-envelope digest, and Ed25519 signature over the domain-separated envelope digest.

The `checkpoint_anchor.schema.json` record stores the latest checkpoint sequence/digest, registry ID, registry-identity digest, signer key ID/epoch, and anchor revision in the protected external location, with compare-and-swap monotonic advancement. The separate `catalog_anchor.schema.json` stores the latest freeze-receipt sequence/hash and registry identity; it is protected and monotonic as well. Both anchors are outside the checkout and database. The public trust configuration pins the identity-authority key and the initial checkpoint signer key; later signer keys enter only through an authorized rotation record.

Canonical two-stage procedure avoids circular recovery authorization. First compute `checkpoint_core_sha256` over JCS of all state-bearing fields (registry/identity/search-family/protocol IDs and hashes, catalog receipt, sequence, event count/tip, manifest/count, predecessor, timestamp, signer key ID/epoch, algorithm), excluding both checkpoint digests, `control_record_sha256`, and signature. A rotation/recovery control record authorizes exactly this core digest. Next attach the signed control-record digest (or null), compute `checkpoint_sha256` over JCS of the complete checkpoint envelope with only `checkpoint_sha256` and `signature` omitted, then sign `UTF8("ATHENA-REGISTRY-CHECKPOINT-v1\0") || raw_32_byte_checkpoint_sha256` with Ed25519. The core digest commits every state-bearing field; the final digest commits the core and its authorization record. Encode the 64-byte signature using standard base64. `signer_key_id` is SHA-256 of the raw Ed25519 public key. Sequence starts at 1 and increments exactly once for each committed registry state change or explicitly authorized key-control transition; a key transition may produce a new checkpoint at the same event count and tip, with the control-record digest bound in the checkpoint. `previous_checkpoint_sha256` is the prior final envelope digest and is null only at sequence 1. The anchor's sequence, final digest, identity digest, and signer key epoch must equal the newest checkpoint. `anchor_revision` advances by one with compare-and-swap on every accepted update. Checkpoint timestamps are audit metadata and do not affect candidate identity or generation.

Rotation/recovery authorization digest is SHA-256 of JCS for `checkpoint_control_record` with all signature fields omitted; authority, current signer where required, and each custodian sign the same digest with the `ATHENA-CHECKPOINT-CONTROL-v1\0` domain. The new key separately proves possession over the control digest with `ATHENA-CHECKPOINT-KEY-POP-v1\0`. `authorized_checkpoint_sha256` names the exact state-bearing `checkpoint_core_sha256`; changing any state field changes that digest. The final checkpoint envelope includes the control-record hash and signer signature, so the authorization cannot be transferred to a later/different proposed state.

### Write and verify sequence

- Every registry write transaction is followed by checkpoint signing and protected-anchor advancement before another state-changing operation is permitted. The checkpoint signer re-reads and verifies the full event chain before signing.
- Verification requires the latest protected anchor and matching signed checkpoint. It verifies the signature, previous-checkpoint link, registry/search-family/protocol identities, full registry chain, exact event count, exact tip, and manifest/count commitments.
- Missing anchor, missing key, invalid signature, identity mismatch, stale anchor, database behind the anchor, database ahead of the anchor, altered history, or signer unavailability yields a fail-closed state. No validation, generation, or other mutation continues.
- A crash after the database transaction but before anchor advancement may leave an unanchored suffix. It is never truncated, reconstructed, replaced, or silently re-anchored. Recovery requires an explicit `RECOVER_CHECKPOINT` control record binding the current trusted anchor and exact proposed state-core digest/tip, an incident evidence digest and reason, signatures from the independent identity authority and two distinct authorized human custodians, and a documented human review decision. The verifier will not accept the recovered tip until authorization is verified and the external anchor is atomically advanced. Recovery authorizations and prior checkpoints remain immutable audit artifacts. If the exact state cannot be authenticated, the registry remains unavailable and a new registry identity/search family is required; prior history is retained.
- The registry is unavailable for all reads that authorize validation/generation and all writes when the external anchor or trusted key configuration cannot be verified. A diagnostic read may report the mismatch but cannot return trusted attempt/candidate content to a downstream consumer.
- Checkpoints are immutable records. A checkpoint file is created exclusively; only the external latest pointer advances. Old checkpoints remain available for audit.

This is a single-registry signature and monotonic-pointer mechanism. It does not require distributed consensus, replicated databases, a network service, or a blockchain.

## 5. Registry lifecycle preservation

Milestone 2 adds no direct-write path. Candidate bytes enter only through M1's atomic batch registration. To preserve the frozen M0/M1 event schema and protocol bytes, catalog approval is recorded as an external signed `catalog_freeze_receipt`, not as a new M1 event type. A protected catalog anchor advances before generation. The generation manifest includes the exact receipt digest; the first registry checkpoint commits both the normal M1 event-chain tip and receipt digest. The generator refuses a missing, stale, or untrusted receipt. The generator cannot call validation; the validator accepts only registered attempt IDs. Candidate bytes, protocol bytes, event rows, sealed manifests, catalog versions, and candidate parents are immutable. Attempt dispositions continue through the M1 transition API, and every failure remains in the M0 multiplicity family.

The generation manifest is an immutable sidecar artifact. Its digest is committed in an optional generation-provenance field of the `CANDIDATE_SET_SEALED` event within the same atomic admission operation. The existing M0 event envelope permits an object payload; the M2 verifier will require and cross-check this field for generated sets. The sidecar is stored in the new dated research output root and never in historical Phase 3/4/5 directories.

Existing M1 golden vectors remain unchanged. The M2 additions must not change `compute_candidate_id`, `compute_protocol_id`, `compute_attempt_id`, `derive_seed`, M0 schema bytes, or their expected values. If registry storage metadata is extended for the registry ID, do so as new immutable metadata; do not rewrite existing event rows or golden fixtures.

## 6. Failure and resource behavior

- Unknown schema version, family, template, strategy, regime, field, protocol, baseline hash, parent, or catalog digest is rejected before registration if it invalidates the *request/catalog*. Candidate document validation remains post-registration and produces `INVALID_CANDIDATE` for that already sealed attempt.
- Search-family/family limit failures reject the whole request without truncation. Every candidate byte batch that is submitted is registered atomically before candidate validation.
- Registry/checkpoint disagreement disables writes and candidate validation. The implementation must not repair, overwrite, or silently recreate the external anchor.
- Reuse the existing M0 ceilings unchanged: one worker; 1,800 seconds per attempt; 86,400 seconds per family; 8,192 MiB; 512 MiB per attempt; 10 GiB per family. Milestone 2 generation is small and offline; the generator enforces output byte limits. No evaluation CPU budget is consumed here.
- Outputs use a new exclusive dated report directory. Existing Phase 3/4/5 source, configs, datasets, ledgers, reports, and outputs remain read-only.

## 7. Scope exclusions

Milestone 2 does not implement a research evaluator, predictive or counterfactual scoring, executable portfolio replay, fitness/robustness calculations, candidate promotion, strategy parameter editing, adaptive search, paper trading, live trading, or prospective holdout access. Generated candidates remain declarative research artifacts only.

## 8. Finalized Milestone 2 decisions

1. **Key custody and rotation:** private keys are stored outside Git, the registry database, CI, and evaluator in a protected OS/hardware key service. Rotation binds old/new key IDs, identity, current anchor, activation checkpoint, and key epoch in a signed control record; current signer + independent identity authority + two distinct custodians authorize normal rotation. Old public keys and records are retained; old private keys are revoked after verification.
2. **Recovery:** no automatic reconstruction or replacement is allowed. Recovery requires authorization of the exact state-core digest (including exact event tip/count and all checkpoint identity fields), signed by the independent identity authority and two distinct human custodians, with a reason, evidence hash, and review record. The final checkpoint envelope binds that authorization without a hash cycle. Any unverified mismatch remains fail-closed.
3. **Priority:** `athena.regime_priority.first_eligible_in_declared_order_v1` selects the first valid eligible strategy in the declared list; that order breaks simultaneous-signal ties, no eligible strategy means abstain, and selected signal/stop/target fields are preserved. Tests independently exercise ties and order changes.
4. **Catalog governance:** each catalog is versioned, reviewed, signed, content-addressed, and frozen before generation. Its externally trusted receipt and protected catalog anchor must precede any generation request. Any byte or semantic change creates the next catalog version with a new hash and parent link; in-place modification is rejected. Once the first candidate attempt is registered in a search family, its catalog is sealed for that family; no later catalog version may be introduced based on candidate outcomes. A pre-generation correction must be reviewed and frozen as a new version before any candidate generation begins.
5. **Registry identity:** an independent authority generates a random registry ID and signs its binding to one search family before the database exists. The authority public key and identity record are trusted outside the repository and database. Registry contents cannot create, replace, or repair identity trust.

These decisions add no changes to D0-1 through D0-11. Existing M0/M1 schemas, protocols, golden vectors, Phase 3–5 files, and historical results remain immutable.
