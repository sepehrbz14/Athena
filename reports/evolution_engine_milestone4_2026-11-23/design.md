# M4 Statistical Validation

## Scope and authority

M4 is a read-only consumer of the frozen M1 registry, M2 external trust history, and immutable M3 evaluation packages. It does not execute strategies, access holdout data, modify M1/M2 records, or change M0–M3 contracts. Synthetic evidence is used for software verification only. The data through 2026-08-19 remain previously observed research data.

The family is reconstructed from the complete M1 event stream and database verification, all linked protocols, the cumulative sealed attempt manifests, and four confirmatory hypotheses per registered attempt. All 144 possible hypotheses remain in the persistent family; invalid, technical-failure, skipped, insufficient, missing, and unevaluated hypotheses contribute p=1. Completed rejected packages retain independently reconstructed raw p-values.

M2 external identity, catalog receipt, checkpoint chain, signatures, key transitions, generation manifests, and anchored M1 prefix are verified. M3 lifecycle events appended after that signed prefix are accepted only when they are permitted M1 evaluation lifecycle events. Every completed attempt's package checkpoint must be a member of the trusted historical chain and bind the package's M1 prefix.

M3 package hashes and M1 completion digests are checked. M4 requires canonical `weekly_evidence.json` conforming to `schemas/athena_m4_weekly_statistical_input_v1.schema.json`; the existing M3 generic package manifest permits artifacts but does not prescribe a weekly statistical serialization. The intake schema captures the M3 weekly vectors, folds, sample counts, and regime-claim flag needed to reconstruct the frozen M0 estimands without changing M3 behavior. Missing/noncanonical evidence fails closed.

M4 independently reconstructs the four raw M0 estimates, bootstrap percentiles, centered one-sided p-values, sample floors, and fold concentration from those weekly vectors and the immutable M1 `EVALUATION_STARTED` seed event. It compares the reconstruction to both `statistics.json` and the M1 completion p-values. Holm step-down adjusted p-values are then computed over the full persistent family in global ordinal and frozen hypothesis order. The report is canonical JSON, exclusively created, and refuses overwrite.

## Qualification gate

M0 v1 requires simultaneous adjusted lower confidence bounds by inversion of Holm-adjusted tests but does not specify the arbitrary-threshold p-value functions or the simultaneous-bound construction. The additive, separately versioned proposed amendment `athena.evolution.m0-holm-inversion-amendment.v1` now defines both. Its canonical identity is recorded in the dated amendment package. The original M0 v1 remains unchanged, and the amendment is not yet approved.

At a finite edge threshold `delta`, M4 uses `p_i(delta)=(1+sum_b I[e_ib >= theta_hat_i-delta])/(B+1)`, where `e_ib=theta*_ib-theta_hat_i`. At `delta=0` this is exactly the frozen M0 raw p-value; p=1 placeholders remain 1 at all finite thresholds. For any threshold vector, the complete persistent family is sorted by raw p then stable attempt ordinal and frozen hypothesis order. Holm compares one-based rank `r` with `alpha/(m-r+1)`, stops at the first failure, and reports adjusted values `min(1,max_{s<=r}((m-s+1)p_(s)))`.

The simultaneous-bound amendment uses the equal-weight Holm-compatible closed-testing construction specified in `amendment.md`: find the Holm rejection set at zero. If all family tests reject, invert each marginal p-function at `alpha/m` and take the nonnegative part. If any tests do not reject, set the lower bound for rejected hypotheses to zero, and invert the nonrejected hypotheses at `alpha/|A|`, where `A` is the nonrejected set. A placeholder has no finite marginal bound and remains in `m`. This rule is conservative by construction: with any p=1 placeholder, the family cannot be fully rejected, so rejected hypotheses receive lower bound zero and the frozen strict-positive qualification gate cannot pass. M4 records this consequence and does not remove attempts or weaken gates.

No standalone Bonferroni replacement or pointwise qualification rule is used. The amendment remains `PROPOSED_FOR_PROTOCOL_OWNER_APPROVAL`; calculated bounds are labeled non-authoritative. `qualify_family()` continues to raise `QualificationBlocked`, and no authoritative `NO_QUALIFYING_CANDIDATE` result can be emitted until approval and all separate infrastructure and evaluation gates are satisfied. The bootstrap p-functions remain approximate; this procedure does not claim exact finite-sample coverage.

## Test boundary

Tests use generated 156-week, six-fold evidence and a signed synthetic M1/M2 registry. They do not open Phase 3–5 historical inputs or the prospective holdout, and they do not launch M3 evaluation. Runtime M4 invocation over historical evidence remains separately unauthorized.
