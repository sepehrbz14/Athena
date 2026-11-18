# Athena Evolution Engine — Milestone 1 Completion Report

**Scope:** Immutable Experiment Registry only. Candidate generation, strategy evaluation, optimization, prospective holdout access, paper trading, live trading, and Milestone 2 are not included.
**Frozen protocol:** [Milestone 0 frozen protocol](../evolution_engine_milestone0_2026-11-18/frozen_protocol.md).
**Repository baseline reviewed:** `deef43c6c33e5c00711095a2fee63aa49c9f09c9`.

## D0 consistency review

Before implementation, all eleven D0 decisions were compared across the frozen protocol, schemas, and acceptance checklist. The review found no conflicting numerical constants or acceptance rules. The registry-focused test suite validates the schema constants relevant to registry admission and persistent accounting.

| D0 | Frozen protocol/schema/checklist cross-check |
|---|---|
| 1 — grammar and limits | Three finite families; 12 attempts per family and 36 cumulative attempts across linked protocols, commits, and reruns; weighted allocation deferred. |
| 2 — chronology | 24-month training; 6-month inner validation, outer test, and step; 6 eligible outer folds; nonoverlapping outer tests. |
| 3 — horizons | 96-bar label horizon, 17,664-bar maximum six-month position horizon, 96-bar embargo and entry blackout, 816-bar causal warm-up. |
| 4 — samples | Predictive 200 signals/30 weeks; counterfactual 100 trades/30 weeks; executable 100 positions/30 weeks/6 folds; regime 30 observations/12 weeks. |
| 5 — estimands | Absolute and paired predictive and executable estimands; absolute after-cost and paired improvement are both required; separate evidence lanes and ledgers. |
| 6 — costs and inference | 5/5/1 bps baseline fee/slippage/funding; 10/10/2 stress; four-week moving blocks, 100,000 iterations, 95% intervals, Holm FWER 0.05, four hypotheses per attempt, p=1 placeholders, maximum 144 tests. |
| 7 — identity and seeds | RFC 8785 content IDs; fixed SHA-256 seed derivation with master seed 20261118 and unsigned big-endian first eight bytes. Persisted seed is a decimal string solely to preserve all 64 derived bits during canonical JSON serialization. |
| 8 — registry | One-based monotonic event sequence, hash chain, sealed pre-validation attempt manifest, append-only records, and valid `NO_QUALIFYING_CANDIDATE` terminal outcome. |
| 9 — resources | Offline, one worker; 1,800 seconds/attempt, 86,400 seconds/family, 8,192 MiB, 512 MiB/attempt, 10 GiB/family. |
| 10 — holdout | Data through 2026-08-19 remain previously observed; minimum 52 future weeks plus 96-bar tail; holdout operations unavailable to this milestone. |
| 11 — authority | Candidate outcome requires both adjusted absolute executable profitability and paired improvement; human status remains research-only. |

The only clarification to frozen wording is the seed's storage representation. The seed derivation, size, and interpretation are unchanged; the exact unsigned 64-bit result is serialized as decimal text because a JSON number cannot safely represent all such values under the protocol's canonicalization model.

## Architecture and invariants

`crypto_bot/experiment_registry.py` implements a local SQLite event store with `synchronous=FULL`, exclusive create semantics, and update/delete-denying triggers for events and raw candidate specifications. Protocol registration, raw attempt bytes, attempt registration events, and the manifest seal are committed in one SQLite transaction. An injected interruption at any event boundary rolls back the whole registration batch.

Protocol, candidate, search-family, attempt, and event identifiers use deterministic canonical content hashing. The protocol hash excludes only the derived attempt-manifest digest to avoid a protocol/attempt/manifest hash cycle; the full manifest digest is included in the sealed hash-chain event and final protocol record. Attempt ordinals are cumulative within the persistent search family and never reset across linked protocol revisions. Duplicate candidate content can be submitted again as a new counted attempt with a distinct ordinal and attempt ID.

Every attempt is recorded before parsing or validation. Invalid submitted bytes, rejected candidates, insufficient evidence, technical failures, skipped/unevaluated attempts, and completed attempts remain in the sealed family. Four multiplicity rows are reconstructed for every attempt; invalid, failed, insufficient, skipped, unevaluated, and missing p-values receive p=1. Resource-limit violations become technical failures. `NO_QUALIFYING_CANDIDATE` is accepted only after every attempt has a compatible terminal disposition and is recorded as a successful outcome.

Evaluation-start records preserve source commit and dirty state, dataset and source/configuration hashes, deterministic fold/metric seeds, and protocol identity. Valid candidate records preserve parent lineage. The registry has no strategy runner, candidate generator, portfolio ledger adapter, or holdout reader. Candidate/incumbent ledger isolation remains an evaluator-stage contract; this milestone cannot create or modify either ledger.

## Changed files

- `crypto_bot/experiment_registry.py` — append-only registry, deterministic IDs/seeds, event transitions, multiplicity accounting, verifier, and CLI.
- `tests/test_experiment_registry.py` — 16 registry-specific contract and failure-path tests.
- `reports/evolution_engine_milestone0_2026-11-18/frozen_protocol.md` — precise decimal-string representation for the already-frozen unsigned 64-bit seed.
- `reports/evolution_engine_milestone1_2026-11-18/README.md` — usage and trust boundary.
- `reports/evolution_engine_milestone1_2026-11-18/completion_report.md` — this acceptance record.

Existing Phase 3, Phase 4, Phase 5 datasets, reports, experiment outputs, ledgers, and approved configuration were not edited. Preexisting unrelated untracked report artifacts were excluded from the change set.

## Acceptance evidence

- Registry-specific tests: **16 passed**.
- Full local regression suite: **173 passed** on Windows Python 3.13 when `TEMP`/`TMP` point to a directory on the workspace volume. The desktop synthetic temporary mount caused four existing atomic-rename tests to fail with WinError 650; the repository workflow already sets a workspace-local temporary directory for both CI platforms.
- Clean-checkout reproduction: pending final commit and report.
- GitHub Actions Ubuntu and Windows Python 3.13: pending final push and workflow completion.

## Integrity model and limitations

SQLite transaction atomicity, immutable triggers, content hashes, schema checks, and the event chain detect partial writes and ordinary tampering. A party able to replace the entire database and recompute every hash can rewrite an unanchored history; retain a trusted tip hash externally or sign exported checkpoints for stronger authenticity. Milestone 1 records evaluation identity and lifecycle but does not validate strategy metrics, perform chronological evaluation, enforce portfolio-ledger isolation at runtime, or open a prospective holdout.

## Review boundary

Milestone 1 is complete for review when clean-checkout reproduction and both configured CI jobs pass and their links/hashes are recorded here. No Milestone 2 work begins before explicit user review and approval.
