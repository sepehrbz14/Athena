# Stop-gap execution and data-integrity review

Date: 2026-11-04 (Asia/Tehran). The original dataset, baseline, safety controls, and historical reports were not changed.

## Stop-loss gap execution

The current `_intrabar_exit()` already uses `min(stop_price, candle.open)` for a long stop and `max(stop_price, candle.open)` for a short stop. Thus a gap through the stop executes at the opening price (the worse market reference); a normal intrabar stop executes at the stop. Stop remains first when both stop and target are inside a candle range. No execution-code change was needed. Added five regression cases cover long/short gap-through stops, ordinary long/short stop hits, and long/short stop-target collisions. All pass.

Because the implementation was already gap-aware, the before/after portfolio results are exactly equal. Three independent safety-on windows were rerun with the frozen configuration and asserted against their preserved reports:

| Window | Trades baseline → rerun | Net P&L baseline → rerun | Final equity baseline → rerun | Max drawdown baseline → rerun | Kill activations | Safety-skipped signals |
|---|---:|---:|---:|---:|---:|---:|
| 2020–2022 | 92 → 92 | -$927.38 → -$927.38 | $9,072.62 → $9,072.62 | 10.0891% → 10.0891% | 1 | 13,066 |
| 2023–2024 | 44 → 44 | -$1,028.70 → -$1,028.70 | $8,971.30 → $8,971.30 | 10.2870% → 10.2870% | 2 | 8,536 |
| 2025–2026-08-19 (previously examined) | 78 → 78 | -$933.19 → -$933.19 | $9,066.81 → $9,066.81 | 10.0635% → 10.0635% | 1 | 6,793 |

These are safety-censored portfolio outcomes: entries were blocked after safety activations. Skipped signals have no assigned P&L. Full-period signal diagnostics are descriptive forward-price outcomes, not an executable portfolio result. The 2025–August 2026 window remains previously examined, not an untouched holdout.

## Full dataset integrity

Dataset: `tests/tests/combined.csv`; SHA-256 `5a9a149c5e35cd543280ca432daacf05fe3842ff063358df3d386e8140e1cf3d`. It has 232,608 candles, from 2019-12-31 00:00 UTC through 2026-08-19 23:45 UTC. Timestamp values are Unix milliseconds and are interpreted as UTC candle-open times.

The merged dataset and all 2,423 available daily files were checked. There are no duplicate/non-increasing timestamps, invalid or non-finite OHLCV values, unexpected OHLC bounds, negative volumes, or OHLCV mismatches between available daily rows and the merged CSV. Exactly one date/file is absent: **2025-08-14**. Both the merged CSV and daily-file collection have one cadence gap, from 2025-08-13 23:45 UTC to 2025-08-15 00:00 UTC: 24h15m between candle opens, meaning **96 missing 15-minute candles**. The daily source set contains 232,608 rows, matching the merged file; none of those rows are missing from or extra to the merged file. No bars were synthesized or filled.

The frozen 2025–2026 trade ledger has no executed position spanning that gap. The absence still creates a discontinuity for bar-count-based indicators/regime context; historical reports remain unchanged. If corrected data are obtained later, use a separately hashed dataset version and do not overwrite this baseline.

Detailed evidence: [integrity JSON](integrity_report.json), [all cadence gaps](all_gaps.csv), [frozen-run comparison](safety_on_replay/diagnostic_report.md), [rerun manifest](safety_on_replay/manifest.json).

## Compounding-aware Monte Carlo design (not run)

The existing Monte Carlo shuffles fixed-dollar closed-trade P&Ls. A separate research simulator should instead:

1. Use only actual closed trades and their recorded entry/stop, side, fees, slippage, funding, and expected loss at stop. Normalize each observed outcome as `net_pnl / expected_loss_at_stop` (an observed net R-multiple).
2. Resample chronological blocks of trade outcomes, not iid dollar P&Ls, to retain some clustering. Map each sampled R-multiple onto a chronological slot carrying that slot's timestamp, entry price, stop, side, and available ATR context.
3. At each slot, call the unchanged `PositionSizer` with that simulation's current equity and the slot's actual entry/stop geometry. Its 0.5% risk budget, 2x leverage collateral, 100% notional cap, 80% margin cap, and 0.20% cost reserve recalculate size as equity changes. Convert the sampled R-multiple to net simulated P&L using the resized expected stop loss. Costs are embedded in the sampled net R; do not lower or remove them.
4. Apply the existing safety rules after each simulated close: reset the 3% daily-loss baseline on the slot's UTC date; permanently stop entries at the 10% peak drawdown or 8% ATR limit. Use recorded ATR at the slot time. Report the point where a simulated path is censored and the remaining observed sample not replayed. Because this resampling design has closed-trade observations rather than a counterfactual intratrade equity path, it would only approximate when a safety control trips; it must not be represented as an exact safety-equivalent portfolio simulation.
5. Report final equity, maximum drawdown, probability of hitting each safety control, and censored-trade count across seeded runs. Record the exact dataset hash, source/config hashes, seed, block length, and replicate count.

This simulator would be conditional on the selected trades that actually survived safety controls. It cannot recover unexecuted opportunities or estimate an uncensored full-period strategy portfolio. It is a sizing/sequence-risk diagnostic, not a replacement for the actual safety-on equity curve. Given the small executed samples and existing early locks, its intervals would be highly uncertain. No Monte Carlo implementation or run was added in this patch.

## Reproducibility

Exact rerun command:

```powershell
& 'C:\Program Files\Blender Foundation\Blender 4.5\4.5\python\bin\python.exe' tools\run_chronological_windows.py tests\tests\combined.csv --output reports\stop_gap_integrity_mc_2026-11-04\safety_on_replay
```

The frozen config is in `safety_on_replay/manifest.json` and matches the original three-window baseline: $10,000 starting equity per window; 0.5% risk; 2x leverage; 100% notional cap; 80% margin cap; 5 bp/side fees and slippage; 1 bp per 8h prorated funding; 5% prior-candle-volume participation; safety switch enabled with 3% daily loss, 10% max drawdown, and 8% ATR thresholds. Git metadata is unavailable in this workspace, so code identity is recorded by hashes in `experiment_manifest.json`, not by commit ID.

Full suite command used the bundled Python 3.11.11 runtime. Result: **47 tests passed**. During this run, the sandbox's default Windows temp directory denied writes; rerunning with `TEMP`/`TMP` redirected to a project-writable temporary folder passed.
