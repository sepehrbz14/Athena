# M4 Implementation Log

## Status

Integrated synthetic software implementation is in progress. M4 does not run candidate evaluations. Qualification is deliberately disabled pending a protocol-owner decision on Holm-adjusted simultaneous lower-bound inversion. No historical input or holdout was accessed.

## Implementation

- Added `crypto_bot/m4_validation.py`: M1/M2/M3 chain and package validation, cumulative family reconstruction, p=1 accounting, independent raw bootstrap reconstruction, standard Holm step-down adjusted p-values, and immutable family report publication.
- Added `schemas/athena_m4_weekly_statistical_input_v1.schema.json`: strict canonical weekly evidence intake schema needed because M3's package container is generic and does not define a weekly-vector serialization.
- Added `tests/test_m4_validation.py`: Holm mathematical vectors, full 144-hypothesis p=1 family, frozen sample floors, regime floor, qualification fail-closed behavior, M3/M4 bootstrap reproduction, and signed M1/M2 end-to-end scenarios for invalid, failed, skipped, and insufficient attempts plus missing/tampered package and registry identity cases.
- Added this M4 design, acceptance, and implementation record.

## Local verification so far

- `python -m py_compile crypto_bot/m4_validation.py tests/test_m4_validation.py`: passed.
- Seven fast focused tests: passed.
- One independent M3/M4 bootstrap reproduction: passed on 156 UTC weeks across six synthetic outer folds (100,000 bootstrap iterations), exact seed map; runtime 45.1 seconds.
- `python -m unittest tests.test_m4_validation -v`: 10 tests discovered, 8 passed, 2 signed M1/M2 integration tests skipped because local Python lacks declared `cryptography`. CI must execute those two tests without skips.

## Open scientific decision

The frozen protocol specifies the raw centered bootstrap p-value at the zero-edge null, Holm FWER alpha 0.05 across up to 144 hypotheses, and requires simultaneous lower bounds derived by inversion of Holm-adjusted tests. It does not specify the p-value function at arbitrary null values or the joint inversion rule. Qualification remains fail-closed until that rule and its golden fixtures are approved and recorded as a versioned M0 decision.
