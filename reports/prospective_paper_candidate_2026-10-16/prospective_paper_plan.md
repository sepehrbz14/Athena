# Prospective paper-runner plan and prepared harness

## Prepared implementation

The paper-only harness is implemented in `crypto_bot/paper.py`, with the single-candle CLI in `tools/run_paper.py`. It has no exchange client, credentials, or order-submission path. It uses the frozen strategy ensemble, 0.5% risk, 2x leverage, safety-on controls, 5 bp/side fees and slippage, 1 bp/8h prorated funding, 5% prior-candle-volume participation cap, 0.20% sizing cost reserve, and next-open bar semantics. It does not force-close a position at the end of a replay; an open position remains marked in the equity curve.

The runner treats historical candles as indicator warm-up only. The prospective account starts at $10,000 with a fresh position state and fresh safety-switch state. It requires a new candle to follow the latest warm-up/accepted candle exactly 15 minutes later; each call accepts one UTC, quarter-hour-aligned candle only after its close and within a two-minute ingestion-lateness window. An identical retry is ignored idempotently; a conflicting duplicate, missing interval, partial candle, or late candle is rejected. A single-writer lock prevents concurrent updates. The canonical candle journal and each derived output are replaced atomically. On restart, the outputs are deterministically regenerated from the journal, so a crash between journal and output writes can be recovered without duplicate trades/events.

Each successful replay writes:

- `prospective_candles.csv`: immutable input/reception journal;
- `paper_events.jsonl`: timestamped decisions, entry requests/acceptances/rejections, fills/partial fills and their reference price, fee and slippage, per-candle funding, safety events, position lifecycle, and marked-equity events; stable event IDs make repeated replay idempotent;
- `paper_trades.csv`: closed-trade accounting ledger reconciled by the existing cost identity;
- `paper_equity.csv`: candle-open and decision-at-close timestamps, marked equity and drawdown;
- `paper_summary.json` and `paper_manifest.json`: output counts, account summary, exact configuration, dataset identity, and paper-only assertions.

Example invocation (not run as part of this preparation):

```powershell
& 'C:\Program Files\Blender Foundation\Blender 4.5\4.5\python\bin\python.exe' tools\run_paper.py `
  --history <frozen-warmup.csv> `
  --new-candle <one-completed-15m-candle.csv> `
  --state-dir reports\prospective_paper_candidate_2026-10-16\paper_run
```

## Safe start and recovery protocol

1. The repository's current history ends at the close of the 2026-08-19 23:45 UTC candle. Because the current date is 2026-10-16 and the runner correctly rejects stale data, do not try to score August–October candles as if they arrived live. Obtain source-verified completed bars from 2026-08-20 through the intended trial start and use them only as indicator warm-up. Preserve the original CSV and its hash unchanged; record the new warm-up bundle hash/source separately.
2. Validate the added warm-up for UTC, strict order, duplicates, and every 15-minute interval. The known 2025-08-14 historical gap remains disclosed; do not interpolate it.
3. Pick a future start after current feed health and UTC clock are independently verified. The first scored candle must be the next quarter-hour bar after warm-up. Reset paper capital/positions/kill-switch at that boundary; no previous safety-on or hypothetical equity is stitched into the prospective account.
4. Supply exactly one newly closed candle per invocation. The 2-minute lateness guard is intentional. For process downtime or late collector delivery, stop scoring. Do not backfill the gap as simulated fills at historical opens. Investigate feed logs, archive the incident, then resume only from a newly approved contiguous warm-up/reset boundary.
5. Ignore the still-forming candle. The CLI accepts only one row and requires `observed_at >= candle open + 15m`; incomplete bars are rejected. Conflicting duplicates and gaps halt input. An identical duplicate retry is acknowledged without appending or replaying a second trade.
6. Use one runner process per state directory. If a process stops unexpectedly, restart from the same dataset hash and state directory; startup replays the canonical candle journal and repairs derived files. If the exclusive lock remains after a crash, first verify the process is gone, then have the operator inspect/remove the stale lock. Never run two collectors against the same state.
7. On permanent drawdown/volatility lock or daily-loss lock, keep the safety behavior. Do not bypass a lock or assign P&L to blocked signals. Report the tested interval through the lock and label subsequent strategy-generated observations as unexecuted if collected separately.
8. Archive all outputs and the software/configuration hashes after each review. The runner currently replays the complete scored candle journal on every accepted bar; this is deterministic and simple, but its full-history latency must be measured before unattended operation.

## Remaining implementation boundary

This is a local completed-candle CSV adapter, not an autonomous feed service. It does not authenticate to an exchange, verify signed market data, monitor host clock drift, keep a process alive, send broker orders, or capture tick-level executable quotes. `ORDER_FILLED` means a simulated backtest fill at the later candle's recorded open, reconstructed when that candle is received; it is not a timely exchange paper order. If prospective execution quality is a study objective, add an independent quote/trade recorder and compare the model fill to contemporaneous bid/ask after the core candidate has been frozen.
