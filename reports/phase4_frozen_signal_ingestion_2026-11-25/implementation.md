# Phase 4 — Frozen Signal Ingestion

**Baseline:** `a596f8707670196082d31c3f661edd3ab78ea964`  
**Scope:** synthetic implementation and regression verification only. No historical portfolio evaluation or holdout access.

## Interface

`crypto_bot.frozen_signal_ingestion.ingest_frozen_signals()` accepts explicit signal CSV, capture metadata, candle CSV, and trusted input-manifest paths. The manifest commits SHA-256 for each data input and fixes the signal/candle/metadata schema versions and candle interval. Hashes are checked before the three inputs are parsed. Metadata must satisfy `athena_frozen_signal_capture_metadata_v1.schema.json` and its row counts must match the parsed inputs.

Only rows with strict `ensemble_selected=true` are normalized. Selected rows must have unique signal IDs and `(timestamp,strategy,direction)` identities, exact UTC source-candle joins, close-time `available_at`, reference prices within the configured absolute tolerance, and an immediate next interval candle. Candle gaps are preserved; no bars or signals are synthesized. CSV row order does not affect event order. `forward_outcomes` is not read by the ingestion implementation.

Each `FrozenSignalEvent` retains the validated `execution_at` and adapts to the canonical `crypto_bot.models.Signal`. The ingestion module does not execute, size, or account for a trade. The Phase 0 master baseline has no shared portfolio ledger interface yet; this adapter is ready for the upcoming orchestration layer without duplicating execution or accounting behavior.

Successful calls return a versioned provenance manifest; optional publication uses exclusive creation and cannot overwrite an input or existing report. Schemas: `athena_frozen_signal_input_manifest_v1`, `athena_frozen_signal_ingestion_manifest_v1`, and the existing normalized `athena2_frozen_signal_v1`.

## Verification

- Focused synthetic suite: `python -m unittest tests.test_frozen_signal_ingestion -v` — 13 passing tests.
- Full regression: `python -m unittest discover -s tests -v` — 115 passing tests.
- Additional checks: schema JSON parsing, Python compile, `git diff --check`, and validation of the existing capture metadata against its new schema.
- Cross-platform Python 3.13 CI is defined in `.github/workflows/tests.yml` and run on the implementation branch.
