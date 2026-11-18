# Phase 5 Final Review Addendum — Funding Timing and Reproducibility

**Date:** 2026-11-18
**Implementation commit:** `d256a182b578e1daba09e3dbc3c6bce6e82d0479`
**Scope:** Corrected hypothetical delayed-entry funding timing and independently reproduced Phase 5 from a clean checkout. The prior authoritative Phase 5 run and its outputs remain unchanged.

## Entry-timing funding correction

`_entry_timing()` now uses the corrected Phase 4 funding convention. For each delayed hypothetical position, funding accrues over the actual elapsed time from its last funding timestamp. When the stop is crossed at an observed candle open, the position exits under the canonical opening-gap stop rule and funding accrues only through that open, marked at the open. Positions that survive the opening, and positions stopped intrabar, retain close-mark accrual through the candle close. The existing legacy funding sign is preserved: a long charge is positive and is subtracted from trade P&L; a short funding receipt is negative and therefore adds to trade P&L.

Regression coverage exercises an opening-gap stop after a data gap, a position surviving the opening, and a normal intrabar stop, each for long and short directions. Phase 4 simulator source and historical research evaluator behavior were not modified.

The corrected funding changes **9 of 12** delayed-entry scenario net totals. Trend following, breakout, and mean reversion change at each of the three fixed delays; momentum does not change. Trade counts and scenario definitions are unchanged. These remain hypothetical, fixed-quantity diagnostics. The corrected table is preserved separately in [corrected_entry_timing_sensitivity.csv](corrected_entry_timing_sensitivity.csv); the previous authoritative output remains intact.

## Clean-checkout reproduction

The Phase 5 implementation at `d256a18` was checked out with a clean Git worktree. The full evaluation completed from that source, using the preserved, byte-identical inputs. The [verification record](clean_checkout_verification_d256a18.json) contains all 19 input hashes, six source-file hashes, and all 12 CSV output hashes. The generated run manifest reports `source_worktree_dirty: false` and records Python 3.13.0.

The Phase 4 replay from the clean checkout produced 91 completed positions, ending equity of `$9,051.510973339474987446164828`, net loss of `$948.489026660525012553835172`, and zero insolvency deficit. The event stream hash matches the preserved replay exactly. The raw result JSON hash differs because it embeds the absolute checkout paths in `source_manifest`; after normalizing only those machine-specific paths, its hash matches the preserved result. The decoded source manifests also match after the same path normalization. The account cash, equity, realized P&L, fee, funding, completed-position, and lifecycle reconciliation deltas are zero.

Eleven of the 12 CSV hashes match the previous authoritative run. The only changed CSV is `entry_timing_sensitivity.csv`, as expected from the funding correction. No frozen input or previous output was overwritten.

## Tests and interpretation

The complete local unittest suite passes **157 tests**, preserving the prior 156 and adding the entry-timing funding regression. Ubuntu and Windows Python 3.13 CI are part of final acceptance and are reported with the final review commit.

Chronological partitions through **2026-08-19** are diagnostics on previously observed historical data. Boundary purging and chronological ordering reduce leakage within those retrospective slices, but do not make them a genuine prospective walk-forward evaluation or an untouched holdout. No prospective walk-forward validation has been performed; the future holdout remains pending until unseen data become available. No strategy parameters or thresholds were changed or optimized.
