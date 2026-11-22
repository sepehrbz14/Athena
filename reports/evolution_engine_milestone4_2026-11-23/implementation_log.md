# M4 Implementation Log

## Status

Integrated synthetic software implementation is complete. M4 does not run candidate evaluations. Qualification is deliberately disabled pending a protocol-owner decision on Holm-adjusted simultaneous lower-bound inversion. No historical input or holdout was accessed.

## Implementation

- Added `crypto_bot/m4_validation.py`: M1/M2/M3 chain and package validation, cumulative family reconstruction, p=1 accounting, independent raw bootstrap reconstruction, standard Holm step-down adjusted p-values, and immutable family report publication.
- Added `schemas/athena_m4_weekly_statistical_input_v1.schema.json`: strict canonical weekly evidence intake schema needed because M3's package container is generic and does not define a weekly-vector serialization.
- Added `tests/test_m4_validation.py`: Holm mathematical vectors, full 144-hypothesis p=1 family, frozen sample floors, regime floor, qualification fail-closed behavior, M3/M4 bootstrap reproduction, and signed M1/M2 end-to-end scenarios for invalid, failed, skipped, and insufficient attempts plus missing/tampered package and registry identity cases.
- Added this M4 design, acceptance, and implementation record.
- Final integration fixes kept invalid attempts distinct from valid attempts, bound M3 packages to their original sealed M2 checkpoint prefix, and made the signed package fixture create its parent directory portably.

## Local verification so far

- `python -m py_compile crypto_bot/m4_validation.py tests/test_m4_validation.py`: passed.
- Seven fast focused tests: passed.
- One independent M3/M4 bootstrap reproduction: passed on 156 UTC weeks across six synthetic outer folds (100,000 bootstrap iterations), exact seed map; runtime 45.1 seconds.
- Local Python lacks declared `cryptography`; signed M1/M2 integration fixtures are therefore verified in CI rather than locally.
- GitHub Actions run [#99](https://github.com/sepehrbz14/Athena/actions/runs/45073700294) checked out the pushed commit in clean Ubuntu and Windows Python 3.13 jobs. The full suite passed: 288 tests on each platform. The workflow also ran frozen-schema validation and deterministic replay.
- Earlier CI attempts caught fixture-only issues (frozen fold metadata, invalid lineage expectation, M2 checkpoint-prefix handling, and package parent-directory creation); all are corrected in the final green run. No frozen contract or implementation behavior changed.

## Open scientific decision

The frozen protocol specifies the raw centered bootstrap p-value at the zero-edge null, Holm FWER alpha 0.05 across up to 144 hypotheses, and requires simultaneous lower bounds derived by inversion of Holm-adjusted tests. It does not specify the p-value function at arbitrary null values or the joint inversion rule. Qualification remains fail-closed until that rule and its golden fixtures are approved and recorded as a versioned M0 decision.
