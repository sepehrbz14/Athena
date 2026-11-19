# Milestone 3 Acceptance Criteria

This checklist is subordinate to [design.md](design.md). M0/M1/M2 contracts remain authoritative and unchanged.

## Implementation slices

1. **Admission and input integrity:** only valid registered M1 attempts with verified M2 provenance and exact protocol/input hashes enter internal evaluation code. Holdout and mismatched provenance denied.
2. **Chronology and opportunity construction:** exact frozen M0 folds, purge, embargo, warm-up, blackout, and label timing; deterministic candidate-independent universe; one row per opportunity including abstentions/rejections.
3. **Declarative evaluation engine:** finite M0/M2 grammar only; no generated-code execution; predictive/counterfactual/executable lanes remain separate.
4. **Accounting and evidence validation:** candidate/incumbent paired on identical ex-ante inputs and assumptions; separate fresh ledgers; all lane equations reconcile; M0 uncertainty outputs preserved; M4 alone does persistent-family correction/qualification.
5. **Lifecycle, recovery, and isolation:** M1 transitions only; crash recovery idempotent; incomplete output cannot be complete; Linux VM adversarial verification required before evaluation is enabled.
6. **Final acceptance:** full tests, clean-checkout reproduction, Ubuntu/Windows Python 3.13 CI, immutable output and preservation checks. Authoritative historical execution still requires separate explicit authorization.

## Scientific and integrity checks

- [x] Owner explicitly approves the carried-position weekly return decision before implementation; Sunday-entry/Monday-carry/Tuesday-exit and true no-exposure zero-week fixtures are specified. The fixture test remains to be implemented in the accounting slice.
- [x] 96-bar label is anchored at original signal timestamp/index; `available_at` only controls decision eligibility; strict label purge and minimum 96-bar/24-hour embargo checks are covered by deterministic synthetic tests. Frozen boundaries are materialized from the registered protocol without deriving or shifting them; warm-up, terminal blackout, maximum horizon and gap behavior are covered. (Slice 2 CI: [run 44624340208](https://github.com/sepehrbz14/Athena/actions/runs/44624340208), 218 tests passed on Ubuntu and Windows.)
- [ ] Candidate cannot manufacture signals or timestamps; all candidate/incumbent rows cover exactly the same opportunity IDs and UTC weekly keys, including abstentions, rejected entries, and no-fills.
- [ ] Historical signals are consumed only as exact hash-pinned frozen rows; no regeneration/substitution; source-generation provenance limitation appears in every package.
- [ ] All data through 2026-08-19 are marked previously observed; no holdout access or mount exists.
- [ ] Predictive, counterfactual, and executable reconciliation fixtures pass independently. Candidate and incumbent executable ledgers are fresh, separate, and distinct from the frozen Phase 4 ledger.
- [ ] M0 sample floors and warnings are exact. M3 emits raw/unadjusted evidence only; failed, invalid, skipped, insufficient, and unevaluated attempts remain in persistent M0 accounting, with required p=1 handling. No M3 Holm or qualification output.
- [ ] M1 transition tests use only the frozen public API and permitted states.
- [ ] Sealed-package recovery finalizes the same attempt without rerun; corrupted/unsealed/incomplete packages fail; repeated recovery is idempotent.
- [ ] Every experiment records source commit/dirty state, candidate/protocol/M2 identity, dataset/config hashes, image/runtime digest, fold plan, seeds, method, resource use, and per-artifact hashes.
- [ ] Outputs are exclusive, immutable, dated, and separate from all historical output directories.

## Isolation gate

- [ ] VM hypervisor, host/guest configuration, reviewed immutable image digest, and independent verifier are recorded.
- [ ] Read-only inputs, bounded exclusive output, offline network state, unprivileged identity, denied privilege/device/host namespace access, and resource limits are independently inspected.
- [ ] In-VM adversarial tests deny network/DNS, host/secret/registry/holdout reads, input writes, path/symlink escapes, output overwrite, privilege escalation, device access, process escape, resource exhaustion, and timeout escape.
- [ ] Transfer-in/out hashes are checked outside the VM; tampered image/input prevents launch.
- [ ] No authoritative evaluation can launch before this section is complete and a separate user evaluation authorization is recorded.

## Per-slice completion record

Each vertical slice reports: changed files; commit SHA; acceptance rows satisfied; focused/full test results; reproducibility evidence; unresolved limitations. No new approval is needed for ordinary engineering choices within this design. Material scientific, security, or frozen-contract changes stop the affected work for review.
