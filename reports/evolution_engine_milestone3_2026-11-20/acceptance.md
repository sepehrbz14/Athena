# Milestone 3 Acceptance Criteria

This checklist is subordinate to [design.md](design.md). M0/M1/M2 contracts remain authoritative and unchanged. No historical evaluation or holdout access is part of implementation verification.

## Implementation batches

1. **Admission and chronology:** only valid registered M1 attempts with verified M2 provenance and exact byte snapshots enter the fold pipeline; exact M0 fold and opportunity boundaries apply.
2. **Evidence lanes:** frozen declarative decisions feed separate predictive labels, read-only independent counterfactuals, and fresh executable portfolio ledgers; rows are paired and reconciled on the shared opportunity universe.
3. **Statistics and publication:** raw M0 per-attempt uncertainty, sample gates, immutable dated packages, and public M1 lifecycle transitions are implemented. M4 retains persistent-family correction and qualification.
4. **Isolation and acceptance:** signed independent Linux proof and separate exact-scope human authorization gate any evaluator invocation. Full suite, clean-checkout reproducibility, CI, and preservation checks remain required.

## Scientific and integrity checks

- [x] Protocol owner approved weekly mark-to-market treatment for open exposure; the Sunday-entry/Monday-carry/Tuesday-exit fixture and no-exposure zero-week fixture pass.
- [x] The 96-bar label endpoint is anchored to original signal timestamp; `available_at` controls decision eligibility only. Strict purge, gap censoring, 96-bar embargo, 816-bar warm-up, terminal blackout, and 17,664-bar position horizon are enforced by synthetic chronology tests.
- [x] Candidate grammar can only select or abstain on records in the frozen union. Candidate and incumbent use identical opportunity IDs and UTC week keys; abstentions, missing counterfactual trades, and executable no-fills remain explicit.
- [x] Frozen rows are consumed from exact admitted in-memory bytes. No signal regeneration or substitution occurs; every package carries the source-generation provenance limitation.
- [x] Previously observed historical classification is checked by admission. Holdout protocols are rejected before inputs are read; isolation attestation requires `holdout_mounted=false`.
- [x] Predictive, independent counterfactual, and executable rows are validated against one candidate-independent opportunity order. Candidate/incumbent and each cost stress use independent fresh ledgers; the Phase 4 ledger is never loaded or changed.
- [x] Frozen M0 sample floors and raw bootstrap rules are implemented: paired non-circular four-week blocks, 100,000 replicates, paired UTC keys, 95% intervals, centered raw p-values, and four p=1 placeholders on insufficient samples. Regime floors apply only when making regime-specific claims. Raw fold concentration is recorded; no Holm correction, ranking, or qualification is performed by M3.
- [x] Lifecycle integration uses M1 public methods only: `VALIDATED → RUNNING → COMPLETED → INSUFFICIENT_EVIDENCE`; missing/corrupt recovery packages record `RUNNING → TECHNICAL_EVALUATION_FAILURE`; skipped attempts retain the existing M1 skip transition. The sealed `statistics.json` is the sole source for all four raw M0 p-values; insufficient evidence supplies four p=1 values to M1, with no Holm adjustment.
- [x] Package recovery verifies canonical manifest and every artifact hash; recovery finalizes only the same RUNNING attempt, never reruns it, and is idempotent after completion.
- [x] Package metadata can record source commit/dirty state, candidate/protocol/M2 identity, pinned input/configuration hashes, folds, M1-derived seeds, methodology, resources, and artifact hashes. The frozen 512 MiB attempt output cap is enforced.
- [x] Dated output paths are exclusive and cannot overwrite prior attempt packages or historical reports.
- [x] Admission remains read-only; it does not write M1, inspect forward outcomes, start evaluation, or create evaluation evidence.
- [x] Fold execution fails closed unless a signed isolation permit binds the exact admitted attempt, protocol, candidate, source commit, and input hashes, plus a separate one-attempt authorization.
- [ ] Independently provision and verify the restricted Linux VM, hypervisor boundary, immutable image digest, transfer path, output cap, runtime isolation, and adversarial tests. No authoritative VM has yet been verified.
- [x] Final schema validation and full unit suite passed on clean Ubuntu and Windows Python 3.13 runners: [push run 44726844462](https://github.com/sepehrbz14/Athena/actions/runs/44726844462), [pull-request run 44726853161](https://github.com/sepehrbz14/Athena/actions/runs/44726853161).
- [x] GitHub clean checkouts reproduced the schema/full-suite result for source commit `649a0cf0925f619b2b7c8bf29c40e55649d473db`; no historical evaluation or research result was launched or reproduced.

## Isolation gate

- [ ] Record independently verified hypervisor, host/guest configuration, reviewed immutable image digest, and verifier identity.
- [ ] Independently inspect read-only inputs, bounded exclusive outputs, disabled networking, absent credentials, unprivileged runtime, denied devices/host namespaces, and resource/time limits.
- [x] The signed gate requires adversarial evidence for network/DNS, host/secret/registry/holdout reads, input writes, path/symlink escapes, output overwrite, privilege escalation, device access, process escape, resource limits, timeout, and input/output transfer hashes.
- [x] The authorization verifier rejects expired, untrusted, malformed, replayed-to-another-attempt, or hash-mismatched proof; a separate authorization is required for each historical attempt.
- [ ] Execute the adversarial suite in the independently provisioned Linux VM. The local host and Windows CI do not establish equivalent isolation.
- [x] No authoritative evaluation was launched during development. No holdout was opened.

## Completion record

The implementation log records the batch commit, changed files, focused/full tests, platform CI, reproducibility evidence, and remaining infrastructure limits. M3 is not operationally ready for historical evaluation until every unchecked isolation and final acceptance item above is completed and the user separately authorizes evaluation.
