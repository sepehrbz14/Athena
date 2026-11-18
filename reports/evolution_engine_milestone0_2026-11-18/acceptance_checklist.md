# Evolution Engine Milestone 0 — Review and Acceptance Checklist

## Milestone 0 deliverable review

- [ ] Confirm source baseline is `deef43c6c33e5c00711095a2fee63aa49c9f09c9` and current tracked files have no modifications.
- [ ] Confirm verified repository contracts and frozen artifact hashes in the specification.
- [ ] Approve or revise the three-family finite candidate grammar; no strategy parameter/threshold fields are allowed.
- [ ] Approve immutable candidate identity, semantic revision, parent lineage, and canonical JSON hash method.
- [ ] Approve protocol registration and append-only event lifecycle, including separate `REJECTED`, `INSUFFICIENT_EVIDENCE`, `TECHNICAL_EVALUATION_FAILURE`, `INVALID_CANDIDATE`, and `UNEVALUATED` dispositions.
- [ ] Confirm an immutable pre-validation attempt manifest records every generated/submitted candidate attempt (valid or invalid), raw-byte hash, family, globally monotonic ordinal, and attempt ID before evaluation; require `attempt_count == len(attempts)`, unique ordinals, global ordinal continuity across linked manifests, no more than 12 per family/36 total across the persistent search family, and retain invalid, failed, skipped, and unevaluated entries.
- [ ] Confirm the three evidence lanes remain separate and no cross-lane aggregation is permitted.
- [ ] Approve nested chronological fold lengths, UTC boundaries, label/trade purge, embargo, warm-up, preprocessing, account reset, and fold-end rule.
- [ ] Approve candidate/family limits, master seed, deterministic seed derivation, correction family, alpha, and multiple-testing method.
- [ ] Confirm every candidate-versus-incumbent comparison uses identical input hashes, fold boundaries, warm-up, scored calendar weeks, cost assumptions, execution model, Phase 4 config, and fold-end behavior; candidate and control ledger files are distinct.
- [ ] Approve primary metrics and require both absolute positive after-cost evidence and paired incremental improvement; relative rank among losing candidates never qualifies.
- [ ] Approve explicit lane and regime sample gates; unmet sample/coverage floors or uncertainty uncomputable from insufficient clusters return `INSUFFICIENT_EVIDENCE`; no separate confidence-width cutoff is used.
- [ ] Approve isolated candidate and incumbent-control ledgers under the byte-identical Phase 4 config. The frozen Phase 4 ledger is read-only reference material and is never reused as an evaluation ledger.
- [ ] Confirm 2026-08-19 and earlier can never be labeled untouched OOS; holdout status stays pending until custody and future data are approved.
- [ ] Confirm the generator, candidate selector, and ordinary evaluator have no holdout access; only a one-shot read-only confirmatory evaluator may be unlocked after protocol and full attempt-manifest hashes are frozen.
- [ ] Confirm deterministic content IDs and seed derivation from the candidate semantic hash, protocol semantic hash, attempt raw-byte hash, and frozen input hashes.
- [ ] Approve research-only statuses and confirm no paper/live or automatic promotion status exists.
- [ ] Explicitly approve or amend all 11 proposed decisions in the [D0 decision record](unresolved_decisions.md), including every numerical value and its rationale.
- [ ] Approve the future implementation test plan below.

**Milestone 0 acceptance condition:** the specification, schemas, decisions, and checklist are reviewed and approved. No code, historical evaluation, or candidate run is part of this milestone.

The proposed decisions requiring explicit approval are listed in [unresolved_decisions.md](unresolved_decisions.md). They are specific defaults for review; they do not authorize implementation.

## Future implementation test plan (not executed in Milestone 0)

### Contract and schema tests

- Validate every example document against the pinned JSON Schema dialect and reject unknown fields, unsupported versions, invalid enums, extra numeric strategy parameters, dynamic code fields, nonfinite values, invalid UTC timestamps, and invalid content IDs.
- Test canonical IDs against a published canonicalization vector; semantic change changes the ID, presentation-only label does not, and prior IDs remain resolvable.
- Verify `candidate_set.attempt_manifest_sha256` is excluded from protocol identity only to avoid the protocol-ID/attempt-ID/manifest-hash cycle; the full digest must be bound by the sealed hash-chain event, and changing attempt count or any protocol decision field must change the protocol ID.
- Test family-specific policy discriminators, parent types, parent existence, duplicate parents, cycles, and immutable baseline/config hashes.
- Ensure invalid protocols fail before candidate or data evaluation begins.
- Validate event-specific states so candidate rejection, insufficient evidence, technical failure, invalid candidate, and unevaluated status cannot be collapsed into one generic failure code.
- Validate reason-to-disposition mapping: malformed schema/lineage is `INVALID_CANDIDATE`; leakage, hash, reconciliation, or evaluator resource/invariant failures are `TECHNICAL_EVALUATION_FAILURE`; sample/coverage shortfall is `INSUFFICIENT_EVIDENCE`; completed scientific gate failures are `REJECTED`.

### Chronology and leakage tests

- Assert train end < validation start < validation end < outer test start < outer test end, all intervals half-open, and outer test windows do not overlap.
- Inject labels/trades crossing each boundary and prove they are purged; assert the fixed 96-expected-bar/24-elapsed-hour embargo at train-to-validation and validation-to-test boundaries, and prove data gaps do not shorten it.
- Perturb candles after a decision timestamp and prove earlier features, regime, raw signals, candidate decisions, and preprocessing fits do not change.
- Confirm only training observations fit transforms, no future labels appear in decision data, warm-up rows have no scored outcomes/account changes, and account/order/safety state resets per fold.
- Test incomplete labels, end-of-data, missing candles, gap bars, DST/timezone strings, duplicate timestamps, and boundary equality.
- Verify exactly 24 complete training months, 6-month inner validation, 6-month outer tests stepped by 6 months, and at least six nonoverlapping scored outer blocks for an executable edge claim; all existing data are classified previously observed.
- Verify `H_label=96`, 96-bar final-entry blackout, fold-end close, `H_position=N_interval` (maximum 17,664 bars in a 184-day interval), 96-bar elapsed-time embargo at each fitting/selection boundary, and fixed 816 prior contiguous warm-up bars.

### Evidence-lane and accounting tests

- Type- and schema-check lane labels through every output; reject sums or substitutions across predictive, counterfactual, and executable rows.
- Replay frozen Phase 4 inputs/config independently and require its protected result/event hashes and accounting reconciliation before candidate reporting.
- Require candidate portfolio output paths and ledgers to be distinct; compare input bytes before/after and verify Phase 3/4/5 artifacts and configs remain unchanged.
- Reconcile fee, fill slippage, funding sign/timing, gross/net P&L, equity, and positions under `BarExecutionModel` and the Phase 4 ledger. Test opening gaps and forced end-of-fold exits.
- Verify candidate policy is the only permitted upstream difference; risk caps, leverage, costs, concurrency, liquidity policy, and safety behavior remain the pinned baseline.
- For each candidate × fold, run a fresh incumbent-control replay with the same fold, inputs, warm-up, initial equity, costs, execution settings, end policy, and account constraints. Assert exact comparability metadata and separate candidate/control ledger paths.
- Assert the frozen Phase 4 ledger is opened read-only for reference only, is never passed as an evaluator output or seeded candidate ledger, and retains its original hashes.
- For every candidate-fold and incumbent-fold replay, allocate a fresh ledger path and fresh account state; assert candidate/control paths differ from each other and from the frozen Phase 4 baseline ledger. Injecting/reusing the frozen Phase 4 ledger must fail closed.
- Verify paired estimands use identical UTC week keys, zero-trade weeks are zero returns, each one-factor stress is separately labeled, and robust absolute executable return is the minimum of baseline and three stress-case means.

### Statistics, trials, and rejection tests

- Deterministically enumerate and seal every candidate attempt before parsing/validating it or reading validation/test outcomes. Include valid, malformed, invalid-lineage, failed, skipped, unevaluated, under-sampled, and metric-missing attempts in the attempt manifest and registered denominator.
- Inject candidate-set aborts at each stage and verify untouched attempts become `UNEVALUATED`, remain in the denominator, and receive no metrics; verify infrastructure breaks become technical failures rather than candidate rejections.
- Verify missing/invalid test statistics receive conservative `p=1` placeholders in the registered family; no failed or invalid attempt can be excluded to improve adjusted results.
- Assert one persistent global Holm family across all linked protocols, revisions, families, and attempts, with four confirmatory hypotheses per attempt (maximum 144 under the cumulative 36-attempt ceiling); failed, invalid, skipped, unevaluated, under-sampled, and missing-metric attempts retain p=1 and cannot be removed by reassignment or denominator reset.
- Prove post-result protocol changes create a new protocol linked to all prior attempts under the same persistent `search_family_id`; new revisions/campaigns cannot reset attempt budget, ordinal sequence, multiplicity denominator, or p-values.
- Verify every non-root protocol revision names its parent protocol IDs, retains the same search-family ID, and includes a manifest linked to the globally monotonic attempt ledger.
- Use fixed seeds to reproduce weekly moving-block resamples; preserve paired candidate/control weeks and report block and occupied-week counts.
- Compare raw centered-bootstrap p-values and Holm-adjusted inference with known fixtures; verify simultaneous-bound inversion, tied/undefined PF, all-win/all-loss cases, zero-trade weeks, Monte Carlo tail-count warnings, and too-few-cluster warnings.
- Reject candidates that outperform a losing baseline but have nonpositive absolute after-cost expectancy or a lower confidence bound that includes zero.
- Recompute the four mathematical estimands (absolute predictive, paired predictive, robust absolute executable, paired baseline executable); executable advancement requires both adjusted absolute and paired lower bounds above zero.
- Check sample floors exactly: predictive 200 opportunities/30 occupied weeks; counterfactual 100 completed trades/30 entry weeks; executable 100 positions/30 trade weeks/6 outer folds; regime claim 30 observations/12 occupied weeks plus whole-lane minima. Unmet floors yield `INSUFFICIENT_EVIDENCE` with visible warnings.
- Mark completed but under-sampled/under-covered or cluster-insufficient runs `INSUFFICIENT_EVIDENCE`; reject completed valid evidence that fails a substantive edge gate; mark evaluator/infrastructure aborts `TECHNICAL_EVALUATION_FAILURE` and publish no partial metrics as complete. Do not introduce an unregistered confidence-width cutoff.
- Reject or mark insufficient evidence for any unmet sample, fold, regime-coverage, cost robustness, absolute-edge, incremental-edge, multiplicity, causality, safety, or reconciliation gate.
- Verify valid `NO_QUALIFYING_CANDIDATE` and “no demonstrated edge on available evidence” conclusions without converting them to a universal impossibility claim.
- Validate a `NO_QUALIFYING_CANDIDATE` terminal event only after reconciling every sealed attempt; require `run_status=COMPLETED_VALID`, zero qualifiers, zero technical failures, zero unevaluated attempts, the fixed registered-gates reason, and `acceptance_criteria_unchanged=true`; reject error/failure status, incomplete attempts, extra payload fields, or any event that relaxes thresholds.

### Registry, immutability, safety, and reproducibility tests

- Verify sequence monotonicity, event-chain hashes, unique event IDs, payload hashes, event-specific payload schemas, and append-only behavior under concurrent/retried writes.
- Tamper with an old event or artifact and verify the next read fails closed; corrections must append new linked events.
- Refuse output paths inside protected historical/config/input roots, existing nonempty run paths, path traversal, symlink escape, and unknown drive/network destinations.
- Verify holdout storage cannot be mounted or read by candidate generation, candidate selection, or the ordinary evaluator, before or after protocol lock. Require pre-locked exact boundaries, 52 complete weeks plus 96-bar label tail and lane/regime sample floors (without six-fold partitioning); reject sample-driven extension; only the custodian can unlock the one-shot evaluator after checking protocol and full attempt-manifest hashes. Any access consumes and is logged.
- Prove data through 2026-08-19 can never be labeled untouched; allow exactly one read-only confirmatory opening only after protocol plus full attempt-manifest/candidate hashes lock; consume and log any opening, including failed reads, and reject a second opening or altered hashes.
- Exercise candidate/time/memory/output limits and confirm failures are recorded but partial output cannot masquerade as completed metrics.
- Run clean-checkout reproduction on Ubuntu and Windows Python 3.13; require identical candidate specs, manifest identities, seeded statistics, and output hashes for the same inputs and runtime policy.
- Verify deterministic reproduction from candidate semantic hash, protocol semantic hash, attempt raw-byte hash, and input-data hashes; publish golden ID/seed vectors and assert identical output hashes across clean environments.
- Confirm no network access, package installation, subprocess strategy execution, paper runner, or live integration is reachable from research execution.

## Final engine acceptance (requires separate implementation authorization)

- [ ] Full regression and clean-checkout reproducibility pass on both CI platforms.
- [ ] Frozen Phase 3/4/5 hashes and approved Phase 4 config hash are unchanged.
- [ ] Every registered attempt and failure is retained and included in multiplicity accounting.
- [ ] Invalid and unevaluated candidates remain in the sealed family denominator; technical failures are distinguished from insufficient evidence and evidence-based rejection.
- [ ] Candidate/control comparisons are paired under identical folds and execution assumptions, with independent ledgers and no Phase 4 ledger reuse.
- [ ] All three evidence lanes are reported separately with sample/uncertainty warnings.
- [ ] Both absolute after-cost edge and paired improvement gates pass; a less-negative candidate cannot qualify.
- [ ] Historical windows are explicitly marked previously observed; no prospective holdout claim is made without sealed new data.
- [ ] Generator, selector, and ordinary evaluator cannot access the holdout; only the hash-locked one-shot evaluator can score the frozen candidate set.
- [ ] Candidate cannot qualify on relative rank alone; absolute after-cost and paired gates both pass after correction.
- [ ] Human research approval is append-only and cannot trigger config/code/trading changes.
- [ ] If nothing meets the gates, report `NO_QUALIFYING_CANDIDATE` and preserve the negative result.

## Final Milestone 0 review gate

The D0 numeric values and decisions are captured in [frozen_protocol.md](frozen_protocol.md) and have been approved in principle. The final package acceptance checkbox remains pending until user review.

- [ ] User explicitly accepts the final frozen Milestone 0 package; this does not start Milestone 1 until separately authorized.
