# M4 Statistical Validation

## Scope and authority

M4 is a read-only consumer of the frozen M1 registry, M2 external trust history, and immutable M3 evaluation packages. It does not execute strategies, access holdout data, modify M1/M2 records, or change M0–M3 contracts. Synthetic evidence is used for software verification only. The data through 2026-08-19 remain previously observed research data.

The family is reconstructed from the complete M1 event stream and database verification, all linked protocols, the cumulative sealed attempt manifests, and four confirmatory hypotheses per registered attempt. All 144 possible hypotheses remain in the persistent family; invalid, technical-failure, skipped, insufficient, missing, and unevaluated hypotheses contribute p=1. Completed rejected packages retain independently reconstructed raw p-values.

M2 external identity, catalog receipt, checkpoint chain, signatures, key transitions, generation manifests, and anchored M1 prefix are verified. M3 lifecycle events appended after that signed prefix are accepted only when they are permitted M1 evaluation lifecycle events. Every completed attempt's package checkpoint must be a member of the trusted historical chain and bind the package's M1 prefix.

M3 package hashes and M1 completion digests are checked. M4 requires canonical `weekly_evidence.json` conforming to `schemas/athena_m4_weekly_statistical_input_v1.schema.json`; the existing M3 generic package manifest permits artifacts but does not prescribe a weekly statistical serialization. The intake schema captures the M3 weekly vectors, folds, sample counts, and regime-claim flag needed to reconstruct the frozen M0 estimands without changing M3 behavior. Missing/noncanonical evidence fails closed.

M4 independently reconstructs the four raw M0 estimates, bootstrap percentiles, centered one-sided p-values, sample floors, and fold concentration from those weekly vectors and the immutable M1 `EVALUATION_STARTED` seed event. It compares the reconstruction to both `statistics.json` and the M1 completion p-values. Holm step-down adjusted p-values are then computed over the full persistent family in global ordinal and frozen hypothesis order. The report is canonical JSON, exclusively created, and refuses overwrite.

## Qualification gate

M0 requires simultaneous adjusted lower confidence bounds by inversion of Holm-adjusted tests but does not specify the inversion algorithm or the joint null-value rule needed to calculate those bounds. Different algorithms can change whether a candidate passes the absolute and paired edge gates. M4 therefore reports reconstructed statistics and Holm adjusted p-values but leaves qualification disabled. `qualify_family()` raises `QualificationBlocked`; the report marks qualification as blocked and never emits an authoritative `NO_QUALIFYING_CANDIDATE` result.

No Bonferroni substitution, pointwise interval, or other inference rule is used. M0 must receive a versioned protocol-owner clarification before qualification is enabled. This clarification must specify a deterministic mathematical mapping from the complete raw bootstrap distributions and full hypothesis family to simultaneous adjusted lower bounds, together with fixtures and acceptance vectors.

## Test boundary

Tests use generated 156-week, six-fold evidence and a signed synthetic M1/M2 registry. They do not open Phase 3–5 historical inputs or the prospective holdout, and they do not launch M3 evaluation. Runtime M4 invocation over historical evidence remains separately unauthorized.
