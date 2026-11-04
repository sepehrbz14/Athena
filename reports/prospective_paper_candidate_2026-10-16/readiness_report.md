# Frozen candidate readiness report

Assessment date: 2026-10-16 (Asia/Tehran). This is a paper-research preparation only. No prospective candles were ingested, no paper evaluation was started, and no real orders can be submitted by the new runner.

## Repository and freeze status

The workspace does not have usable Git metadata. `git status --short --branch` and `git rev-parse --show-toplevel` fail with “not a git repository”; the visible `.git` directory does not contain a usable `HEAD`/object database. Therefore there is no verifiable baseline commit, and clean/dirty working-tree status cannot be established. No files were discarded and no Git repository was initialized. The manifest freezes this candidate by SHA-256 hashes instead. This is an auditable snapshot, but it is not a substitute for a signed/version-controlled release; establish repository history before calling it an official release.

Earlier reports and source data were preserved. Changes for this candidate are limited to diagnostic-only event enrichment, the simulation-only runner, its CLI, its tests, and new research artifacts. Strategy decisions, sizing, fills, controls, and accounting were not changed. The full suite confirms logging/result equivalence.

## Confirmed data and timing facts

- Dataset `tests/tests/combined.csv`: SHA-256 `5a9a149c5e35cd543280ca432daacf05fe3842ff063358df3d386e8140e1cf3d`; 232,608 candles from 2019-12-31 00:00 UTC through 2026-08-19 23:45 UTC.
- Timestamps are Unix milliseconds in the combined CSV and represent UTC candle-open labels. The bundled source archive test confirms the source convention. The loader converts them to aware UTC datetimes. The paper runner rejects naive timestamps.
- No duplicate or non-increasing timestamps were found. There is one cadence gap: 2025-08-13 23:45 UTC to 2025-08-15 00:00 UTC (24h15m between labels, 96 missing 15-minute candles). The historical backtester permits this because it checks only strict ordering, not 15-minute continuity. It is not repaired here. No executed trade was found crossing that interval in the preserved 2025–2026 ledger, but the gap can still affect bar-count-based features/regime context after the gap. Historical results remain as previously reported.
- The final historical candle opens at 2026-08-19 23:45 UTC and closes at 2026-08-20 00:00 UTC. An immediate append would have to start at 2026-08-20 00:00 UTC.
- Decisions use the current 15-minute candle at its close. Signal/order timing is signal-at-close and simulated execution at the next candle open. Entry liquidity is capped using the preceding completed candle's volume and the 5% participation setting. Higher-timeframe 1H/4H bars intentionally include the developing partial bucket made from already-closed 15m candles at that decision close. This is causal, but it is not a completed-higher-timeframe-bar strategy; that behavior is intentionally preserved.
- Warm-up candles seed indicators and 1H/4H structure but do not seed account equity, positions, or kill-switch state for a scored window. At the prospective trial start, paper equity is reset to $10,000 and the safety controls start fresh.
- Existing execution applies 5 bp slippage and 5 bp fees per side. Fees use executed fill notional; slippage is the configured price adjustment from the bar reference. Funding is a constant 1 bp per 8h, prorated across every 15m candle using that candle's close. This is the frozen model—not exchange settlement-time funding. It is now emitted per bar as a diagnostic event, but its financial model is unchanged.

## Confirmed issues and material risks

1. **No commit identity / no repository status.** Hashes establish file identity but do not establish authorship, ancestry, or whether earlier edits were uncommitted. Block official release sign-off until Git metadata is restored and the candidate is committed in a controlled repository.
2. **Historical 2025-08-14 gap.** There are 96 absent candles. Do not silently interpolate or forward-fill them. The baseline remains frozen, but future comparisons must report the gap and avoid interpreting the affected days as continuous 15m observations.
3. **Dataset is stale for a prospective start.** As of 2026-10-16, the checked-in candles end almost two months earlier. The paper runner deliberately rejects late input. Obtain closed candles from 2026-08-20 through the chosen start as warm-up-only, validate their provenance/cadence, hash that warm-up bundle, then begin scoring at the next new candle. Do not replay that backfill as prospective fills or P&L.
4. **No live ingestion service exists in this repository.** The addition accepts a single candle CSV from an operator or upstream collector. Feed authentication, exchange-clock health, outage alerting, and unattended scheduling remain outside scope.
5. **Bar-open simulation is not a real paper fill.** With completed-candle-only input, the next candle's open is known only after that candle closes. The engine can causally reconstruct the model's next-open fill, but that fill is observed retrospectively at bar granularity. It cannot demonstrate a timely order or executable price. Any future evaluation must label fills as simulated and compare against independently captured quotes/trades if execution realism is required.
6. **Funding/slippage are simplified.** Constant prorated funding and fixed symmetric 5 bp slippage do not reproduce exchange funding timestamps, spread, volatility/size-dependent impact, maker/taker status, or queue priority. Keep them frozen for this candidate; assess realism separately before any live use.
7. **OHLC intrabar ambiguity remains.** When stop and target are both touched, the engine chooses the adverse stop. Exact intrabar ordering and execution time cannot be recovered from 15m OHLCV.
8. **Bootstrap uncertainty is fragile.** Existing forward diagnostics use 200 replicates and occupied, non-overlapping 64-candle blocks for each conditional signal group. The observations overlap for longer horizons, and dropping empty blocks/resampling only occupied blocks does not fully preserve irregular signal spacing or regime dependence. Sensitivity outputs compare 32/64/128-candle blocks with 200 replicates. At the 64-candle Momentum horizon in 2020–2022, the 95% mean interval is `[-0.045%, 1.185%]` at 32 bars, `[0.125%, 1.277%]` at 64 bars, and `[-0.144%, 1.320%]` at 128 bars. For 64-candle Breakout in 2023–2024, it is `[-0.552%, -0.010%]`, `[-0.527%, 0.001%]`, and `[-0.564%, -0.015%]`, respectively. That block-length sensitivity means neither apparent significance is robust. Two hundred draws also give coarse tail quantiles; use at least 5,000 in any preregistered prospective uncertainty analysis. Full comparisons are in `bootstrap_sensitivity.csv`.
9. **Prior historical windows are examined.** The 2025–August 2026 window is explicitly not an untouched holdout. All existing historical reports were retained and are descriptive only.

## Feature availability audit

No confirmed future-data input was found in the current event-driven path: `market_data.update(candle)` runs before the close-time decision, and the snapshot consists of candles/features formed from historical bars plus the just-closed 15m bar. 1H/4H values are developing aggregates, but contain only already observed 15m candles. Signals are queued after close; the pending order is evaluated against the next bar's open, and liquidity references only the prior bar's completed volume. Warm-up history is separate from scored account state. Tests cover future-data isolation, partial higher-timeframe bars, next-open timing, and causal prior-volume fills.

For this candidate, detailed signals now include the cached decision-time features, current regime assessment, and latest observed 1H/4H snapshots. Detailed event logs also record per-candle funding charges, fill reference/execution prices, fee/slippage amounts, and marked equity. These are observer-only changes; the complete suite verifies unchanged trading results under different log levels.

## Proposed follow-up fixes (not applied)

- Restore the repository's Git metadata or place this exact hashed snapshot into the intended version-controlled repository, then commit it and verify a clean status before calling the candidate frozen.
- Retrieve the 96 missing 2025-08-14 bars from a source-verified archive into a separate corrected-dataset candidate. Add a dataset-validator test that rejects non-15m scored input and tests that a signal cannot fill across a missing interval. Do not replace or rerun the preserved reports without an explicit baseline-version decision.
- For any claim about exchange-realistic funding, implement a separate settlement-timestamp funding model and tests for long debit, short credit, no charge between settlement times, and partial holding periods. Do not alter the frozen constant-rate model in this candidate.
- For an execution-quality study, capture timestamped bid/ask/trades independently and compare modeled next-open fills with contemporaneous executable quotes. Add a test that a simulated order cannot be marked filled before its next-open observation is available. The current completed-bar replay alone cannot validate real order latency or fill quality.
- For inference, use the prospective preregistered daily-return bootstrap plan (7/14/28-day sensitivity, at least 5,000 replicates) and report effective occupied blocks. This is a research-method improvement, not a strategy change.

## Test status and next gates

Full test command (bundled runtime):

```powershell
& 'C:\Program Files\Blender Foundation\Blender 4.5\4.5\python\bin\python.exe' -m unittest discover -s tests
```

Result: **43 tests passed**. The new paper tests cover completed-bar gating, lateness, missing intervals, duplicate/restart idempotency, and diagnostic audit files. This validates the local replay harness on hand-built data; it does not validate an exchange feed, live timing, or performance over a fresh full-history replay.

Before prospective scoring: restore version control; backfill and hash a contiguous warm-up ending near the actual trial start; independently verify current UTC and feed-health controls; approve the preregistered criteria; and do a non-scored paper-runner smoke test with synthetic candles. If an outage exceeds the 2-minute acceptance window or a cadence gap occurs, stop and investigate—do not backfill it as a scored order sequence.
