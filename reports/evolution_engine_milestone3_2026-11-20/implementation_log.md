# Milestone 3 Implementation Log

**Status:** Integrated implementation batch in progress. Synthetic evaluation components are implemented and tested. Historical evaluation remains prohibited pending independent Linux VM verification and separate user authorization. M0/M1/M2 contracts and Phase 3–5 artifacts remain unchanged.

## Approved protocol-owner clarification

On 2026-11-20 the protocol owner approved weekly mark-to-market returns for any week with open exposure, including a carried position without a fill. A week with neither exposure nor trade activity remains an exact zero return. The Sunday-entry/Monday-carry/Tuesday-exit fixture passes. No M0 estimand or numeric constant changed.

## Completed implementation batches

| Batch | Status | Main components | Verification | Limitations |
|---|---|---|---|---|
| Admission and input integrity | Accepted; commits and CI recorded in prior entries below | Read-only M1/M2 provenance checks and exact in-memory snapshots | Slice 1 full CI: 210 passing tests on Ubuntu and Windows Python 3.13, run [44614025701](https://github.com/sepehrbz14/Athena/actions/runs/44614025701) | Admission only means eligible for evaluation. |
| Chronology and opportunity construction | Accepted; commit `fa00e20717de4c92ca887872fd33a89c2e9505b7`, follow-up branch CI verified by user | Frozen timestamp/timeline mapping, registered folds, shared opportunities, purge/embargo/warm-up/blackout | Slice 2 CI passed on Ubuntu and Windows, run [44625406039](https://github.com/sepehrbz14/Athena/actions/runs/44625406039) | Historical data remain read-only; no evaluation launched. |
| Integrated evidence and lifecycle batch | Implemented locally; GitHub connector unavailable, so push/CI pending | Predictive scoring correction; independent counterfactual and fresh executable ledgers; weekly pairing/accounting; raw statistics and fold concentration; cross-lane validation; immutable package sealing/recovery; signed isolation gate; fold pipeline | Focused local suite: 75 M3 tests (4 Ed25519 tests skipped because the local Python environment lacks declared `cryptography`); 24 source/test files compile. Full local suite: 252 tests discovered; one M2 test module cannot import because local Python lacks declared `cryptography>=42,<47`, with four M3 signature tests skipped for the same reason. Redirecting temporary files into the workspace avoided unrelated Windows sandbox cross-volume failures; every test that could run passed. | Linux VM not provisioned/independently verified. Historical evaluation not authorized or run. Final Ubuntu/Windows CI and clean-checkout record pending publish. |

## Scientific correction: predictive abstentions

`score_predictive_decisions()` now resolves shared-opportunity label eligibility before assigning an abstention score. Complete eligible label plus abstention is `SCORED_ZERO`; purged labels remain `PURGED`; candle gaps, incomplete labels, or any incomplete member signal at the shared opportunity produce `CENSORED`. Each shared opportunity receives exactly one evidence row. Focused tests cover all cases, including mixed label completeness across multiple strategy signals.

## Integrated component map

- `m3_admission.py`: read-only M1/M2 and hash-pinned snapshot admission; snapshots include exact candidate/protocol and research inputs.
- `m3_chronology.py`, `m3_policy.py`: exact M0 chronology and finite declarative decisions over the candidate-independent opportunity union.
- `m3_predictive.py`: post-decision labels only, anchored to the original signal timestamp.
- `m3_counterfactual.py`: read-only selection from completed Phase 5 independent trades; no replay or shared capital.
- `m3_portfolio.py`: unchanged Phase 4 simulator, fresh independent candidate/incumbent and stress ledgers; frozen Phase 4 ledger is not opened.
- `m3_accounting.py`, `m3_evidence.py`, `m3_statistics.py`: approved weekly marks, opportunity-level reconciliation, exact M0 sample floors and raw/unadjusted bootstrap evidence. M4 retains family correction and qualification.
- `m3_engine.py`: integrates one registered fold, but refuses to proceed without a signed exact-scope isolation/authorization permit.
- `m3_lifecycle.py`: exclusive dated package publication, bounded output, artifact hash verification, M1 public transitions and crash recovery without rerunning a sealed attempt.
- `m3_isolation.py`: Ed25519 verification of external Linux VM attestation and separate one-attempt human authorization. It verifies evidence; it does not provision a VM.

The M1 completion path reads only the sealed raw M0 `statistics.json`, verifies the four frozen hypothesis identifiers and unadjusted status, and passes those raw p-values into the existing M1 completion API. Insufficient samples require four p=1 placeholders and trigger the existing `COMPLETED → INSUFFICIENT_EVIDENCE` transition. No family correction is performed.

## Local full-suite details

Command: `python -m unittest discover -s tests` (with `TEMP` and `TMP` redirected to a workspace-local temporary directory to avoid the Codex Windows sandbox's cross-volume `os.replace` failures in unrelated existing tests). Outcome: 250 tests discovered; all tests except the M2 test module passed, but that module failed to import because local Python 3.13 lacks the declared `cryptography>=42,<47` dependency. The four M3 isolation signature tests were skipped for the same missing dependency. No Phase 3/4/5 source was changed to work around the sandbox. GitHub CI installs declared dependencies and is the required final verification.

No historical signal input, Phase 4 ledger, Phase 5 report/output, or prospective holdout was opened for evaluation. No candidate was ranked, qualified, or promoted.
