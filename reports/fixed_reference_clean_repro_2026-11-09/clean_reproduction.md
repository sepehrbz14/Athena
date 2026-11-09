# Clean-checkout reproduction

The earlier metadata identified `90efd7208e4e8f30eb91f7153f8fdd88015ed62` with a dirty worktree. That SHA is not present in the current repository history. The original evaluator run occurred before its changes were committed; commit `bfa6eedbecefb5ebedebf5b9ca7b37b75ea39aa7` subsequently captured that evaluator implementation. This explains the stale SHA/dirty metadata.

I cloned the repository, checked out `bfa6eedbecefb5ebedebf5b9ca7b37b75ea39aa7` detached, and ran the complete evaluation from that clean checkout against the frozen signal CSV and candle dataset. The reproduced run records `code_worktree_dirty: false`, commit `bfa6eedbecefb5ebedebf5b9ca7b37b75ea39aa7`, and the same signal CSV SHA-256 as the prior report. All four strategy trade counts, gross P&L and net P&L match the prior Fixed-Reference-Risk report exactly.

Command run from the clean checkout:

```powershell
python tools\run_fixed_reference_research.py `
  --signals C:\Users\admin\OneDrive\Documents\Athena\reports\independent_signal_research_gap_parity_2026-11-05\signals.csv `
  --candles tests\tests\combined.csv `
  --output C:\Users\admin\OneDrive\Documents\Athena\reports\fixed_reference_clean_repro_2026-11-09\result `
  --seed 20261105 `
  --bootstrap-iterations 2000
```

The cost-sensitivity run used a separate clean snapshot commit `1bb70314e62f73b227f1e0a0652ef8acc0727250` (based on `bfa6eed…`) containing only the shared-liquidity fix, test additions, and sensitivity runner. Each scenario's metadata reports that exact commit with a clean worktree.
