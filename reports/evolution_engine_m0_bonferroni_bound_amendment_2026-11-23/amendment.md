# M0 Amendment: Full-Family Bonferroni Numerical Bounds (Proposed)

**Current status:** Approved by the protocol owner on 2026-11-24, as recorded in [protocol_owner_decision.md](protocol_owner_decision.md). The amendment JSON retains its original `PROPOSED_FOR_PROTOCOL_OWNER_APPROVAL` field because that field is part of the immutable hashed proposal document; approval is recorded separately and does not alter its identity.

**Amendment schema:** `athena.evolution.m0-bonferroni-bound-amendment.v1`  
**Amendment ID:** `sha256:693ba1f2f357788ebf9348685c44e4d03ef4915a322e31c72e1cd13654d2f1a9`  
**Base:** frozen M0 v1. The original protocol and earlier Holm-inversion proposal are preserved unchanged.

## Rule

For the complete persistent family, let `m=4A`, where `A` counts every registered attempt, including invalid, failed, skipped, insufficient-evidence, and unevaluated attempts. For each available marginal bootstrap function, define

`p_i(delta) = [1 + sum_(b=1)^B I(e_ib >= theta_hat_i - delta)] / (B+1)`,

with the existing frozen 100,000 resamples and `e_ib = theta*_ib - theta_hat_i`. Its numerical lower bound is

`L_i = sup { delta : p_i(delta) <= alpha/m }`, with `alpha=0.05`.

Use the same full-family denominator for all hypotheses. An unresolved or below-floor hypothesis remains a `p=1` placeholder in `m` and receives no finite numerical bound. Holm step-down adjusted p-values are still calculated over the same complete family for family-wide hypothesis testing; Holm does not define these numerical bounds. The existing strict-positive executable-bound qualification rule remains unchanged.

## Coverage argument

Assume each marginal one-sided test is valid: for every true null `H_i(delta)`, `Pr[p_i(delta) <= u] <= u` for `u in [0,1]`. By the union bound, regardless of dependence, unequal distributions, or discreteness:

`Pr[any true null is rejected at alpha/m] <= sum_i alpha/m = alpha`.

Inverting each marginal test therefore yields simultaneous one-sided lower coverage of at least `1-alpha`. This is a single-step Bonferroni rectangle, not an exact Holm-compatible region. It allows a positive bound for a sufficiently informative hypothesis when another family member is unresolved, provided its marginal bootstrap p-function reaches the full-family threshold. It is conservative and family growth can make the threshold smaller than the bootstrap p-value resolution, yielding no finite/positive bound. This guarantee is conditional on marginal bootstrap-test validity; the amendment does not establish that validity independently.

## Preserved constants and limits

M0's alpha (0.05), 100,000 bootstrap replicates, 4-week moving blocks, 95% interval level, master seed, 36-attempt cumulative family cap, four hypotheses per attempt, sample floors, estimands, costs, concentration rule, and qualification criteria are unchanged. All registered attempts continue to count in the family. No family filtering or dropped placeholders are permitted.

## Operational state

M4 recognizes only the exact approved amendment ID, amendment-document digest, and pinned protocol-owner decision record. Holm p-values and full-family Bonferroni bounds may be reconstructed under the approved method. `qualify_family()` remains fail-closed because this methodological approval does not authorize historical family evaluation or qualification execution. Holdout access remains separately unauthorized. Bootstrap coverage remains conditional on valid marginal tests.

## Reproduction

The strict JSON schema is [athena_m0_bonferroni_bound_amendment_v1.schema.json](../../../schemas/athena_m0_bonferroni_bound_amendment_v1.schema.json). The canonical amendment object (excluding `amendment_id`) is hashed as UTF-8 JSON with sorted keys, no whitespace separators, and NaN disallowed. The ID above is SHA-256 of those bytes. Synthetic expected values are in `tests/fixtures/m4_bonferroni_bounds_golden.json` and `tests/fixtures/m4_holm_vs_simultaneous_bounds.json`.
