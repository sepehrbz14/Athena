# M4 Acceptance Checklist

M0–M3 contracts and all historical artifacts remain unchanged. Synthetic implementation only; authoritative historical validation and holdout access remain prohibited.

- [x] Verify M1 SQLite registry and independently exported event-chain semantics, protocol lineage, attempt manifests, global ordinals, and four hypotheses per attempt.
- [x] Verify the M2 independently anchored identity, catalog, signed historical checkpoint chain, generation manifests, and exact checkpoint membership for each completed M3 package.
- [x] Permit only M3 lifecycle events after the M2 trusted prefix; reject unanchored protocol, attempt, or generation mutations.
- [x] Require exact candidate/protocol hashes, package manifest integrity, M1 completion digest, and M1 start-event seed reconstruction.
- [x] Require canonical weekly evidence with identical UTC week keys across candidate/incumbent and all four frozen cost scenarios.
- [x] Independently recompute M0 estimates, sample status/warnings, 100,000-replicate four-week moving-block bootstrap, centered one-sided raw p-values, 95% percentile intervals, and fold concentration.
- [x] Compare raw evidence to M3 `statistics.json`, M1 completion p-values, and persistent M1 multiplicity records.
- [x] Include invalid, failed, skipped, insufficient, missing, and unevaluated attempts; unresolved/unsuccessful entries stay at p=1 and in the full denominator.
- [x] Apply exact Holm step-down adjusted p-values to all 4×attempts, capped at 144; test golden values, ties, p=1 placeholders, and invalid p-values.
- [x] Publish canonical immutable family reports with exclusive creation and no overwrite.
- [ ] Enable qualification only after protocol-owner approval freezes the Holm-adjusted simultaneous lower-bound inversion and mathematical golden fixtures.
- [x] Keep authoritative NO_QUALIFYING_CANDIDATE emission disabled while the qualification gate is unresolved; retain it as the valid M0 terminal outcome after the approved method is implemented.
- [x] No evaluation execution, historical input access, holdout access, candidate ranking, promotion, or frozen M0–M3 mutation occurs during M4 software tests.
- [x] Full clean-checkout regression and frozen-schema/deterministic-replay workflow passed on Ubuntu and Windows Python 3.13 (GitHub Actions run [#99](https://github.com/sepehrbz14/Athena/actions/runs/45073700294), 288 tests per platform).
- [ ] Authoritative qualification and `NO_QUALIFYING_CANDIDATE` publication remain blocked until the protocol owner versions the simultaneous Holm-adjusted lower-bound inversion rule and its golden fixtures.

## Verification commands

- Focused deterministic tests: `python -m unittest tests.test_m4_validation -v`
- Full regression suite: `python -m unittest discover -s tests -v`
- CI: Ubuntu and Windows Python 3.13 through `.github/workflows/tests.yml`.
- Clean checkout: satisfied by the run above, which fetched the pushed commit into clean hosted workspaces and ran the full schema/replay/unit workflow.

The full CI jobs install `.[test]`, including `cryptography>=42,<47`. This local Windows environment did not have `cryptography`; signed-registry integration tests were therefore skipped locally and must pass in CI before implementation acceptance.
