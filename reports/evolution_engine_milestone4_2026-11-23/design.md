# M4 Statistical Validation

## Scope and authority

M4 is a read-only consumer of the frozen M1 registry, M2 external trust history, and immutable M3 evaluation packages. It does not execute strategies, access holdout data, modify M1/M2 records, or change M0–M3 contracts. Synthetic evidence is used for software verification only. The data through 2026-08-19 remain previously observed research data.

The family is reconstructed from the complete M1 event stream and database verification, all linked protocols, the cumulative sealed attempt manifests, and four confirmatory hypotheses per registered attempt. All 144 possible hypotheses remain in the persistent family; invalid, technical-failure, skipped, insufficient, missing, and unevaluated hypotheses contribute p=1. Completed rejected packages retain independently reconstructed raw p-values.

M2 external identity, catalog receipt, checkpoint chain, signatures, key transitions, generation manifests, and anchored M1 prefix are verified. M3 lifecycle events appended after that signed prefix are accepted only when they are permitted M1 evaluation lifecycle events. Every completed attempt's package checkpoint must be a member of the trusted historical chain and bind the package's M1 prefix.

M3 package hashes and M1 completion digests are checked. M4 requires canonical `weekly_evidence.json` conforming to `schemas/athena_m4_weekly_statistical_input_v1.schema.json`; the existing M3 generic package manifest permits artifacts but does not prescribe a weekly statistical serialization. The intake schema captures the M3 weekly vectors, folds, sample counts, and regime-claim flag needed to reconstruct the frozen M0 estimands without changing M3 behavior. Missing/noncanonical evidence fails closed.

M4 independently reconstructs the four raw M0 estimates, bootstrap percentiles, centered one-sided p-values, sample floors, and fold concentration from those weekly vectors and the immutable M1 `EVALUATION_STARTED` seed event. It compares the reconstruction to both `statistics.json` and the M1 completion p-values. Holm step-down adjusted p-values are then computed over the full persistent family in global ordinal and frozen hypothesis order. The report is canonical JSON, exclusively created, and refuses overwrite.

## Approved statistical bounds and qualification gate

The protocol owner approved amendment `athena.evolution.m0-bonferroni-bound-amendment.v1` with ID `sha256:693ba1f2f357788ebf9348685c44e4d03ef4915a322e31c72e1cd13654d2f1a9` on 2026-11-24. M0 v1 remains unchanged; the prior Holm-inversion proposal remains preserved separately. The approved amendment document keeps its original bytes and proposed-status field because those bytes determine its immutable identity. The separate protocol-owner decision record is authoritative and M4 pins its digest and the approved amendment identity.

At a finite edge threshold `delta`, the frozen marginal function remains `p_i(delta)=(1+sum_b I[e_ib >= theta_hat_i-delta])/(B+1)`, where `e_ib=theta*_ib-theta_hat_i`. Holm step-down adjusted p-values continue to control persistent-family hypothesis testing. Numerical lower bounds are computed separately by inverting each marginal p-function at `alpha/m`, with `m` including all four hypotheses for every registered attempt, including unresolved p=1 placeholders. This Bonferroni rectangle has simultaneous coverage of at least `1-alpha` by the union bound, assuming valid marginal tests, without requiring independence. A p=1 placeholder itself has no finite bound, but does not automatically force other hypotheses' numerical bounds to zero. Existing strict-positive executable gates and all other M0 criteria remain unchanged.

Bootstrap validity remains an explicit limitation: approval of the construction does not establish exact finite-sample validity of the marginal bootstrap tests. M4's `qualify_family()` remains fail-closed because methodology approval is distinct from authorization to run historical family evaluation or qualification. The prospective holdout remains inaccessible. No historical evaluation was run during this update.

## Test boundary

Tests use generated 156-week, six-fold evidence and a signed synthetic M1/M2 registry. They do not open Phase 3–5 historical inputs or the prospective holdout, and they do not launch M3 evaluation. Runtime M4 invocation over historical evidence remains separately unauthorized.
