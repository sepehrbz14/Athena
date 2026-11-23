# M4 Implementation Log

## Status

Integrated synthetic software implementation is complete. M4 does not run candidate evaluations. A separately versioned proposed M0 full-family Bonferroni-bound amendment is implemented and mathematically tested; qualification remains disabled pending explicit protocol-owner approval. No historical input or holdout was accessed.

## Implementation

- Added `crypto_bot/m4_validation.py`: M1/M2/M3 chain and package validation, cumulative family reconstruction, p=1 accounting, independent raw bootstrap reconstruction, standard Holm step-down adjusted p-values, and immutable family report publication.
- Added `schemas/athena_m4_weekly_statistical_input_v1.schema.json`: strict canonical weekly evidence intake schema needed because M3's package container is generic and does not define a weekly-vector serialization.
- Added `tests/test_m4_validation.py`: Holm mathematical vectors, full 144-hypothesis p=1 family, frozen sample floors, regime floor, qualification fail-closed behavior, M3/M4 bootstrap reproduction, and signed M1/M2 end-to-end scenarios for invalid, failed, skipped, and insufficient attempts plus missing/tampered package and registry identity cases.
- Added this M4 design, acceptance, and implementation record.
- Final integration fixes kept invalid attempts distinct from valid attempts, bound M3 packages to their original sealed M2 checkpoint prefix, and made the signed package fixture create its parent directory portably.
- Added a separate versioned M0 Bonferroni-bound amendment and M4 report v3. It uses full-family alpha/m inversion for numerical bounds while preserving Holm adjusted p-values for family testing. The amendment is hash-verified and remains proposed; calculated bounds are marked non-authoritative and qualification remains disabled.
- Added independent golden vectors for alpha/m inversion with tied hypotheses, unresolved p=1 placeholders, discrete resolution and family growth; retained the Holm-specific golden vectors and original proposed Holm amendment unchanged.

## Local verification so far

- `python -m compileall -q crypto_bot tests`: passed.
- `git diff --check`: passed.
- `python -m unittest tests.test_m4_validation -v`: 15 tests, passed; two signed integration tests skipped locally because this desktop Python lacks `cryptography`.
- `python -m unittest discover -s tests -v`: 272 tests; 5 environment-specific errors and 6 skips. One import error is due to absent `cryptography`; four atomic-replace errors are due to this desktop sandbox's redirected Windows temp volume. The clean GitHub runners install declared dependencies and use the workflow's workspace temp directory.
- Seven fast focused tests: passed.
- One independent M3/M4 bootstrap reproduction: passed on 156 UTC weeks across six synthetic outer folds (100,000 bootstrap iterations), exact seed map; runtime 45.1 seconds.
- Local Python lacks declared `cryptography`; signed M1/M2 integration fixtures are therefore verified in CI rather than locally.
- GitHub Actions run [#45101067300](https://github.com/sepehrbz14/Athena/actions/runs/45101067300) checked out commit `79492f0293b88f3e7aa760da18241fd09bd694d6` in clean Ubuntu and Windows Python 3.13 jobs. All 293 tests passed on each platform. The workflow also ran frozen-schema validation and deterministic replay.
- Earlier CI attempts caught fixture-only issues (frozen fold metadata, invalid lineage expectation, M2 checkpoint-prefix handling, and package parent-directory creation); all are corrected in the final green run. No frozen contract or implementation behavior changed.

## Open scientific decision

M0 v1 and the earlier proposed Holm-inversion amendment are unchanged. New amendment `athena.evolution.m0-bonferroni-bound-amendment.v1` (`sha256:693ba1f2f357788ebf9348685c44e4d03ef4915a322e31c72e1cd13654d2f1a9`) specifies the same frozen arbitrary-threshold marginal p-function and full-family alpha/m inverse bounds, with Holm adjusted p-values retained separately. Its strict JSON schema is `schemas/athena_m0_bonferroni_bound_amendment_v1.schema.json`. It preserves M0 constants, sample floors, and all p=1 attempts in the denominator. Under marginal test validity, the union bound gives at least 1-alpha simultaneous coverage without dependence assumptions. A p=1 placeholder has no finite bound itself but does not force other hypotheses' bounds to zero; family growth may still make attainable discrete p-values too coarse for a positive bound. Amendment and calculated bounds are not authoritative until protocol-owner approval. Qualification and historical evaluation remain disabled.
