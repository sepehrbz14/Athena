# M4 Acceptance Checklist

M0–M3 contracts and all historical artifacts remain unchanged. Synthetic implementation only; authoritative historical validation and holdout access remain prohibited.

- [x] Verify M1 SQLite registry and independently exported event-chain semantics, protocol lineage, attempt manifests, global ordinals, and four hypotheses per attempt.
- [x] Verify the M2 independently anchored identity, catalog, signed historical checkpoint chain, generation manifests, and exact checkpoint membership for each completed M3 package.
- [x] Permit only M3 lifecycle events after the M2 trusted prefix; reject unanchored protocol, attempt, or generation mutations.
- [x] Require exact candidate/protocol hashes, package manifest integrity, M1 completion digest, and M1 start-event seed reconstruction.
- [x] Require canonical weekly evidence with identical UTC week keys across candidate/incumbent and all four frozen cost scenarios.
- [x] Independently recompute M0 estimates, sample status/warnings, 100,000-replicate four-week moving-block bootstrap, centered one-sided raw p-values, 95% percentile intervals, and fold concentration.
- [x] Compare raw evidence to M3 `statistics.json`, M1 completion p-values, and persistent M1 multiplicity records.
- [x] Include invalid, failed, skipped, insufficient, missing, and unevaluated attempts; unresolved/unsuccessful entries stay at p=1 and in the full denominator.
- [x] Apply exact Holm step-down adjusted p-values to all 4×attempts, capped at 144; test golden values, ties, p=1 placeholders, and invalid p-values.
- [x] Publish canonical immutable family reports with exclusive creation and no overwrite.
- [x] Preserve the earlier Holm-inversion proposal and M0 v1 unchanged; keep the Bonferroni amendment bytes immutable and record protocol-owner approval separately.
- [x] Compute numerical lower bounds by inverting each marginal p-function at alpha/m across the complete persistent family; retain Holm adjusted p-values independently for family testing.
- [x] Verify the union-bound argument and test arbitrary-threshold add-one p-values, Holm ties, Bonferroni ties, discrete boundaries, unresolved p=1 placeholders, family growth, amendment hashing/schema, and strict positivity behavior with synthetic fixtures.
- [x] M4 recognizes only the exact approved amendment ID/document digest and pinned protocol-owner decision record; tampered or mismatched approval records fail closed.
- [ ] Qualification execution remains disabled until separate historical-evaluation authorization; the methodological approval alone cannot authorize a family run.
- [x] Keep authoritative NO_QUALIFYING_CANDIDATE emission disabled while the qualification gate is unresolved; retain it as the valid M0 terminal outcome after the approved method is implemented.
- [x] No evaluation execution, historical input access, holdout access, candidate ranking, promotion, or frozen M0–M3 mutation occurs during M4 software tests.
- [x] Full clean-checkout regression and frozen-schema/deterministic-replay workflow passed on Ubuntu and Windows Python 3.13 (baseline M4 run [#45101067300](https://github.com/sepehrbz14/Athena/actions/runs/45101067300), 293 tests per platform; Bonferroni-amendment integration run [#45254237778](https://github.com/sepehrbz14/Athena/actions/runs/45254237778), 298 tests per platform).
- [x] Qualification and authoritative `NO_QUALIFYING_CANDIDATE` publication remain fail-closed while the amendment is proposed; synthetic calculated bounds are explicitly non-authoritative.

## Current amendment identity

- Schema: `athena.evolution.m0-bonferroni-bound-amendment.v1`
- Original amendment payload status: `PROPOSED_FOR_PROTOCOL_OWNER_APPROVAL` (preserved as immutable hash input)
- Protocol-owner decision: `APPROVED` on 2026-11-24; see `protocol_owner_decision.json`.
- ID: `sha256:693ba1f2f357788ebf9348685c44e4d03ef4915a322e31c72e1cd13654d2f1a9`
- Holm adjusted p-values remain the family-wide testing result; numeric confidence bounds use full-family Bonferroni inversion.
- Coverage remains conditional on valid marginal bootstrap tests; this approval does not establish exact finite-sample validity.
- The full report schema is versioned as `athena.m4.persistent-family-report.v3`; prior report schemas are preserved.

## Verification commands

- Focused deterministic tests: `python -m unittest tests.test_m4_validation -v`
- Full regression suite: `python -m unittest discover -s tests -v`
- CI: Ubuntu and Windows Python 3.13 through `.github/workflows/tests.yml`.
- Clean checkout: satisfied by the run above, which fetched the pushed commit into clean hosted workspaces and ran the full schema/replay/unit workflow.

The full CI jobs install `.[test]`, including `cryptography>=42,<47`. This local Windows environment did not have `cryptography`; signed-registry integration tests were therefore skipped locally and must pass in CI before implementation acceptance.
