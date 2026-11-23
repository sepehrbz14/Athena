# Protocol-owner decision: M0 Bonferroni-bound amendment

**Decision date:** 2026-11-24  
**Decision:** Approved  
**Approved amendment:** `athena.evolution.m0-bonferroni-bound-amendment.v1`  
**Amendment ID:** `sha256:693ba1f2f357788ebf9348685c44e4d03ef4915a322e31c72e1cd13654d2f1a9`  
**Document SHA-256:** `0245084071fdd3f56ac1bc5f122dbe246612bca62b8d2919338b0505819e7a07`

## Decision

The protocol owner approved the amendment's full-family Bonferroni construction for simultaneous numerical lower bounds at `alpha/m`, while retaining Holm-adjusted p-values for persistent-family hypothesis testing. Every registered attempt remains in `m`, including unresolved `p=1` placeholders. Existing strict-positive executable qualification requirements and all other M0 acceptance criteria remain unchanged.

This approval does not modify frozen M0 v1. The earlier Holm-inversion amendment remains preserved as a separate historical proposal. The Bonferroni amendment JSON is retained byte-for-byte with its original proposed-status field so its approved identity and hash do not change; this decision record is the authoritative approval state recognized by M4.

## Validity limitation and authorization boundary

The coverage argument is conditional on valid marginal bootstrap tests. This decision does not establish exact finite-sample validity of the marginal bootstrap tests.

This methodological approval does **not** authorize a historical family evaluation, qualification run, or prospective-holdout access. Those remain separately gated. No historical evaluation or holdout data were accessed while recording this decision.

## Machine-readable record

See [protocol_owner_decision.json](protocol_owner_decision.json). M4 pins and verifies both this record and the approved amendment ID/document digest before reporting that the method is approved.
