# Milestone 3 Implementation Log

**Status:** M3 code batch complete and CI verified. Operational evaluation remains disabled pending independent Linux VM verification and separate user authorization. M0/M1/M2 contracts and Phase 3–5 artifacts remain unchanged.

## Approved protocol-owner clarification

On 2026-11-20 the protocol owner approved weekly mark-to-market returns for any week with open exposure, including a carried position without a fill. A week with neither exposure nor trade activity remains an exact zero return. The Sunday-entry/Monday-carry/Tuesday-exit fixture passes. No M0 estimand or numeric constant changed.

## Completed implementation batches

| Batch | Status | Main components | Verification | Limitations |
|---|---|---|---|---|
| Admission and input integrity | Accepted; commits and CI recorded in prior entries below | Read-only M1/M2 provenance checks and exact in-memory snapshots | Slice 1 full CI: 210 passing tests on Ubuntu and Windows Python 3.13, run [44614025701](https://github.com/sepehrbz14/Athena/actions/runs/44614025701) | Admission only means eligible for evaluation. |
| Chronology and opportunity construction | Accepted; commit `fa00e20717de4c92ca887872fd33a89c2e9505b7`, follow-up branch CI verified by user | Frozen timestamp/timeline mapping, registered folds, shared opportunities, purge/embargo/warm-up/blackout | Slice 2 CI passed on Ubuntu and Windows, run [44625406039](https://github.com/sepehrbz14/Athena/actions/runs/44625406039) | Historical data remain read-only; no evaluation launched. |
| Integrated evidence and lifecycle batch | CI verified on source commit `649a0cf0925f619b2b7c8bf29c40e55649d473db` (implementation commit `4b26446a7d4780b2930b0fd194f6e850e3b784fa`; isolation-fixture correction `3e99b6a200aee33dca3b8d1d21f94f449b8cbf1d`; direct portfolio gate `649a0cf0925f619b2b7c8bf29c40e55649d473db`) | Predictive scoring correction; independent counterfactual and fresh executable ledgers; weekly pairing/accounting; raw statistics and fold concentration; cross-lane validation; immutable package sealing/recovery; signed isolation gate; fold pipeline | Clean GitHub runners passed schema validation and the complete unit suite on Ubuntu and Windows Python 3.13: [push run 44726844462](https://github.com/sepehrbz14/Athena/actions/runs/44726844462), [PR run 44726853161](https://github.com/sepehrbz14/Athena/actions/runs/44726853161). Focused clean-worktree suite: 80 M3 tests (4 signature tests skip locally because declared `cryptography` is absent); 24 M3 source/test files compile. Local full-suite attempt discovered 257 tests but could not import one existing M2 test module due to the missing local dependency; temporary files were redirected to avoid unrelated Windows sandbox cross-volume failures. | Linux VM not provisioned/independently verified. Historical evaluation not authorized or run. |

## Scientific correction: predictive abstentions

`score_predictive_decisions()` now resolves shared-opportunity label eligibility before assigning an abstention score. Complete eligible label plus abstention is `SCORED_ZERO`; purged labels remain `PURGED`; candle gaps, incomplete labels, or any incomplete member signal at the shared opportunity produce `CENSORED`. Each shared opportunity receives exactly one evidence row. Focused tests cover all cases, including mixed label completeness across multiple strategy signals.

## Integrated component map

- `m3_admission.py`: read-only M1/M2 and hash-pinned snapshot admission; snapshots include exact candidate/protocol and research inputs.
- `m3_chronology.py`, `m3_policy.py`: exact M0 chronology and finite declarative decisions over the candidate-independent opportunity union.
- `m3_predictive.py`: post-decision labels only, anchored to the original signal timestamp.
- `m3_counterfactual.py`: read-only selection from completed Phase 5 independent trades; no replay or shared capital.
- `m3_portfolio.py`: unchanged Phase 4 simulator, fresh independent candidate/incumbent and stress ledgers; frozen Phase 4 ledger is not opened.
- `m3_accounting.py`, `m3_evidence.py`, `m3_statistics.py`: approved weekly marks, opportunity-level reconciliation, exact M0 sample floors and raw/unadjusted bootstrap evidence. M4 retains family correction and qualification.
- `m3_engine.py`: integrates all registered outer folds, but refuses to proceed without a signed exact-scope isolation/authorization permit.
- `m3_lifecycle.py`: exclusive dated package publication, bounded output, artifact hash verification, M1 public transitions and crash recovery without rerunning a sealed attempt.
- `m3_isolation.py`: Ed25519 verification of external Linux VM attestation and separate one-attempt human authorization. It verifies evidence; it does not provision a VM.

The M1 completion path reads only the sealed raw M0 `statistics.json`, verifies the four frozen hypothesis identifiers and unadjusted status, and passes those raw p-values into the existing M1 completion API. Insufficient samples require four p=1 placeholders and trigger the existing `COMPLETED → INSUFFICIENT_EVIDENCE` transition. No family correction is performed.

## Local full-suite details

Command: `python -m unittest discover -s tests` (with `TEMP` and `TMP` redirected to a workspace-local temporary directory to avoid the Codex Windows sandbox's cross-volume `os.replace` failures in unrelated existing tests). Outcome: 257 tests discovered; all tests except the M2 test module passed, but that module failed to import because local Python 3.13 lacks the declared `cryptography>=42,<47` dependency. The four M3 isolation signature tests were skipped for the same missing dependency. No Phase 3/4/5 source was changed to work around the sandbox. GitHub CI installed declared dependencies and passed the complete suite on both platforms.

No historical signal input, Phase 4 ledger, Phase 5 report/output, or prospective holdout was opened for evaluation. No candidate was ranked, qualified, or promoted.

The first CI attempt caught an invalid synthetic SHA-256 image-digest fixture in the isolation-gate tests. The fixture was corrected to valid hexadecimal, and the subsequent push and pull-request matrices passed.
