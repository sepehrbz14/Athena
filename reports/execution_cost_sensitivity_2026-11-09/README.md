# Reproduction

The four scenarios were run from the clean evaluator snapshot commit `1bb70314e62f73b227f1e0a0652ef8acc0727250`, based on reviewed commit `bfa6eedbecefb5ebedebf5b9ca7b37b75ea39aa7`. Each scenario's `run_metadata.json` records the exact code commit, file hashes, clean-worktree flag, source signal SHA-256, configuration, seed, and evaluation time.

From the isolated checkout at `reports/fixed_reference_clean_repro_2026-11-09/clean_checkout`, check out the snapshot commit and run:

```powershell
git checkout --detach 1bb70314e62f73b227f1e0a0652ef8acc0727250
python tools\run_execution_cost_sensitivity.py `
  --signals C:\Users\admin\OneDrive\Documents\Athena\reports\independent_signal_research_gap_parity_2026-11-05\signals.csv `
  --candles tests\tests\combined.csv `
  --output C:\Users\admin\OneDrive\Documents\Athena\reports\execution_cost_sensitivity_2026-11-09 `
  --funding-bps-8h 1 `
  --seed 20261105 `
  --bootstrap-iterations 2000
```

The frozen signal CSV was read only. Fee/slippage changes affect risk-aware quantity under the same sizing rule, so gross P&L also varies across cost scenarios. Funding stays at 1 bp/8h and is reported separately. No scenario is selected or recommended.
