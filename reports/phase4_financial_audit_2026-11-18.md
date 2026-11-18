# Athena Phase 4 Financial Audit — Funding Timing Addendum

**Published:** 2026-11-18  
**Corrected simulator source commit:** `414cd2705e069936f2dbe6ed6d054f8b5a62e918`  
**Prior full-replay source commit:** `c97b39f9919e85227aac1266c622fc3bc4468a3a`  
**Scope:** Opening-gap stop funding timing and historical impact. The prior [2026-11-14 financial audit](phase4_financial_audit_2026-11-14/PHASE4_FINANCIAL_AUDIT.md) and all its replay outputs remain unchanged.

## Finding and correction

Previously, the simulator posted funding through the current candle close before deciding whether a position had exited at the observed opening price through a gap stop. That assigned the position funding after its modeled exit.

The event processing now determines an opening-gap stop before accrual:

- A position stopped at the observed open accrues carried funding only through that opening timestamp, using the opening price as the mark. It exits under the canonical `BarExecutionModel` gap-stop price rule.
- A position that survives the opening accrues through the candle close using the close mark.
- Normal intrabar stops and targets retain the documented candle-close prorated funding approximation.
- A newly opened position still accrues only from the open through the close. Funding cash sign conversion is unchanged: the legacy model's positive long charge is negated once for the ledger's signed cash movement.
- Insolvency handling follows the modeled opening-gap stop when one is triggered; the position is not kept open through later prices in that candle after the modeled open exit.

Two focused regression tests cover a carried position stopped at the first observed open after a multi-interval gap and a position surviving that opening. Existing funding-sign and reconciliation tests remain in place.

## Historical impact

I inspected the complete prior full-dataset event stream. It contains **one data gap and zero `POSITION_CLOSED` events marked `gap_at_open`**. Therefore this timing change has no affected historical trade and changes no historical funding or cash entry. The full historical result remains the one reproduced at `c97b39f`; a second full evaluation was not needed because the only changed branch is absent from that replay.

The corrected source commit is `414cd2705e069936f2dbe6ed6d054f8b5a62e918`. Its runtime change is limited to the opening-gap branch. The previous full replay is preserved under `reports/phase4_financial_audit_2026-11-14/final_c97b39f/`; it is not overwritten.

## Frozen run inputs

| Input | Path | SHA-256 |
|---|---|---|
| Frozen signal rows | `reports/independent_signal_research_gap_parity_2026-11-05/signals.csv` | `0135f4f2329303662ffcb8d63a4d8aa954cde77cfb65accc51c815de5d9fc6f7` |
| Signal metadata | `reports/independent_signal_research_gap_parity_2026-11-05/run_metadata.json` | `49beadbdb4cca781c20f37c5d8e651d4eebb3e5da429be3b1d7e2e6ef7a1a6cc` |
| Candles | `tests/tests/combined.csv` | `5a9a149c5e35cd543280ca432daacf05fe3842ff063358df3d386e8140e1cf3d` |
| Baseline config | `configs/athena2_portfolio_baseline_v1.json` | `bb7e286d265fd51a25d55ae428d0527c794a94008b2e8bf0e03548376aa8e859` (canonical JSON hash) |

The config file's byte hash is `bf5eeff4de2b58487f917cefecd9b8b7dc94ec7a72bf125729f34ab65c861627`. The canonical hash above is the simulator's `config_sha256`. No strategy or risk setting changed.

## Full executable-portfolio result

This is the Phase 4 executable simulation, not the legacy research-mode diagnostic.

| Measure | Result |
|---|---:|
| Starting cash | $10,000.00 |
| External deposits / withdrawals | $0 / $0 |
| Realized gross P&L | -$379.0488241035019187139351740 |
| Final unrealized gross P&L | $0 |
| Entry fees (cash movement) | -$282.8661540657855445 |
| Exit fees (cash movement) | -$282.7980888137845014 |
| Funding paid | $11.097633907245617065 |
| Funding received | $7.3216742297925691251 |
| Signed net funding movement | -$3.7759596774530479399 |
| Final cash and equity | **$9,051.510973339474987446164828** |
| Net loss from starting equity | $948.489026660525012553835172 |
| Insolvency deficit | $0 |
| Maximum drawdown | 10.321809993105611687% |

The 1,416-entry Phase 3 cash journal's ordered running balance equals final cash exactly. The ledger reports zero deltas for cash, equity, realized P&L, both fee categories, funding, completed-position P&L, and lifecycle quantity. Independently regrouping Decimal totals can leave a `2e-24 USDT` addition-order residual; the audit tolerance remains `1e-20 USDT`.

Slippage is already embedded in fill prices and gross realized P&L, so it is not booked again as a separate cash debit. The prior full run measured `$282.8364553154864913` entry slippage and `$282.8278342178949533107300428` exit slippage where raw and fill prices were present.

### Signal dispositions

| Disposition | Count |
|---|---:|
| Raw source rows | 51,292 |
| Not ensemble-selected; filtered | 22,535 |
| Selected rows | 28,757 |
| Accepted orders / completed positions | 91 |
| Selected rows rejected | 28,666 |
| Rejected: kill switch active | 28,523 |
| Rejected: duplicate exposure | 126 |
| Rejected: opposing signal while exposed | 17 |

Every row has a disposition. The complete JSONL and rejection breakdown are in the preserved replay outputs listed below.

### Drawdown and safety diagnostics

The configured 10% drawdown control blocks future entries but does not liquidate an open position. The historical maximum therefore reached 10.3218% while an existing trade was still exposed. The `$1` minimum-equity floor likewise locks entries; it does not cap existing-position losses. The historical run ended with 91 closed positions, zero open positions, and no insolvency event. Synthetic gap tests continue to cover floor crossing and negative-equity deficits.

The prior audit separately identified the `$0.104373` final equity as legacy research mode, where kill-switch thresholds were observational and not enforced. It is not the executable portfolio result.

## Replay output references and checksums

The large replay outputs are preserved locally at `reports/phase4_financial_audit_2026-11-14/final_c97b39f/` and were not added to Git. These checksums identify the exact artifacts; the corrected gap branch was not encountered in this historical event stream.

| Artifact | SHA-256 |
|---|---|
| Complete portfolio result, including schema-validated journal and positions | `f0111014caa1baf99aa9d3e4145eeba582ed99820d0e8f3502e8b66d543b539e` |
| Complete event stream | `0dfd36a24813dc090fb579d102eef67762755dc779987daf6a666cf093f717e1` |
| Exact-decimal cash journal | `ab0e9fa41f6abf1ce3f74c6a36679006c58e89dca470a496528bb55b192a5025` |
| Completed-position ledger | `59a2cc1f7b4993da1b9c4943b5acaccd749059e78bb387c24fe47a2c473e8d82` |
| Signal-disposition ledger | `bf2fb8f742302ff0cbee3f229ae61fcde484386c1c7a9c113213606bf5b517e1` |
| Order/fill event ledger | `82785e0747141e7f047d292d276501a675ee5ad52b18df6d14dbd0e178b7f01e` |
| Equity/exposure/drawdown curve | `83980de09fb5504c74ed12fb08990da7ee8a2f261901602ee9693761d560795e` |
| Post-allocation exposure and reservation curve | `505b7744b04894a6372ec5224125b5097fde81f5e74ab4f2fb4bb508d42e1ae6` |

The original Phase 3 fixture remains unchanged. `crypto_bot/portfolio_ledger.py`, `crypto_bot/backtest.py`, the research evaluator, historical reports, frozen datasets, and schemas have no changes in this correction.

## Reproduction

From a clean checkout of source commit `414cd2705e069936f2dbe6ed6d054f8b5a62e918`:

```powershell
python -m pip install -e ".[test]"
python -m unittest discover -s tests
```

To reproduce the full dataset run, call `crypto_bot.portfolio.run_from_files()` with the four paths and exact SHA-256 arguments in the table above, writing to a new output directory. The prior audit contains the complete run manifest and outputs; the event stream confirms there are no opening-gap exits to alter.

## Verification and changed files

- Local complete suite: **145 tests passed**; the original 143 remain, plus two gap-funding tests.
- Phase 3 ledger and historical backtester were unchanged.
- No historical results changed; the previous audit outputs were preserved.
- No schema or Phase 3 replay fixture changed.
- Source/test changes: `crypto_bot/portfolio.py`, `tests/test_portfolio_simulator.py`.
- This addendum is published as a new report rather than replacing the previous audit.
- GitHub Actions run [44279188490](https://github.com/sepehrbz14/Athena/actions/runs/44279188490) passed on source commit `414cd27`: [Ubuntu / Python 3.13](https://github.com/sepehrbz14/Athena/actions/runs/44279188490/job/134715050880) and [Windows / Python 3.13](https://github.com/sepehrbz14/Athena/actions/runs/44279188490/job/134715050276) both succeeded.

The publication commit adds this report only; it does not change simulator or test code.

## Deferred work

No strategy optimization, Phase 5 work, Evolution Engine, AI/ML, live execution, or paper-trading integration was started. Exchange settlement timing, exchange-accurate liquidation, and sub-candle execution remain outside this milestone.
