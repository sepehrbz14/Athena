# Trade economics and signal-quality audit

Dataset SHA-256: `5a9a149c5e35cd543280ca432daacf05fe3842ff063358df3d386e8140e1cf3d`. Frozen configuration and safety-on primary results are unchanged; diagnostic reruns were asserted equal to their preserved baseline trade ledger, equity curve, and kill events.

This audit assigns realized P&L only to executed trades. Signals skipped by a safety lock remain signals-only records; no counterfactual fill, cost, position, or P&L is constructed. 2025–August 2026 remains a previously examined diagnostic window, not an untouched holdout.

## Economics by strategy component

Gross is shown both before modeled slippage and after slipped execution. Funding is shown as paid / received / net paid; funding received is not hidden inside a negative net cost.

### 2020_2022

| Component | n | Raw gross | Execution gross | Fees | Slippage | Funding paid / received / net paid | Net P&L | Win rate (95% Wilson CI) | Avg win / loss | PF | Expectancy (95% normal CI) | Turnover |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Breakout | 6 | $75.72 | $50.04 | $25.69 | $25.69 | $0.00 / $2.46 / $-2.46 | $26.81 | 50.0% (18.8%–81.2%) | $54.80 / $-45.87 | 1.195 | $4.47 ($-39.80–$48.73) | $51,372.36 |
| MeanReversion | 59 | $147.47 | $-234.18 | $381.65 | $381.65 | $6.01 / $4.54 / $1.47 | $-617.30 | 39.0% (27.6%–51.7%) | $39.10 / $-42.12 | 0.593 | $-10.46 ($-21.41–$0.49) | $763,308.04 |
| Momentum | 10 | $-94.64 | $-133.18 | $38.53 | $38.53 | $1.88 / $0.00 / $1.88 | $-173.58 | 30.0% (10.8%–60.3%) | $52.31 / $-47.21 | 0.475 | $-17.36 ($-47.19–$12.47) | $77,060.56 |
| TrendFollower | 17 | $16.17 | $-72.26 | $88.43 | $88.43 | $2.62 / $0.00 / $2.62 | $-163.31 | 41.2% (21.6%–64.0%) | $43.21 / $-46.58 | 0.649 | $-9.61 ($-31.30–$12.09) | $176,860.47 |

| Component | Fees / turnover | Slippage / turnover | Fees / absolute raw gross | Slippage / absolute raw gross |
|---|---:|---:|---:|---:|
| Breakout | 0.0500% | 0.0500% | 8.87% | 8.87% |
| MeanReversion | 0.0500% | 0.0500% | 16.47% | 16.47% |
| Momentum | 0.0500% | 0.0500% | 8.50% | 8.50% |
| TrendFollower | 0.0500% | 0.0500% | 12.00% | 12.00% |

Signals generated: 13158; executed trades: 92; kill-switch skips: 13066. Skipped signals have no assigned P&L.

### 2023_2024

| Component | n | Raw gross | Execution gross | Fees | Slippage | Funding paid / received / net paid | Net P&L | Win rate (95% Wilson CI) | Avg win / loss | PF | Expectancy (95% normal CI) | Turnover |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| MeanReversion | 44 | $-202.12 | $-616.42 | $414.29 | $414.29 | $4.85 / $6.86 / $-2.01 | $-1,028.70 | 18.2% (9.5%–32.0%) | $8.37 / $-30.43 | 0.061 | $-23.38 ($-29.04–$-17.72) | $828,589.94 |

| Component | Fees / turnover | Slippage / turnover | Fees / absolute raw gross | Slippage / absolute raw gross |
|---|---:|---:|---:|---:|
| MeanReversion | 0.0500% | 0.0500% | 51.89% | 51.89% |

Signals generated: 8580; executed trades: 44; kill-switch skips: 8536. Skipped signals have no assigned P&L.

### 2025_2026-08-19_previously_examined

| Component | n | Raw gross | Execution gross | Fees | Slippage | Funding paid / received / net paid | Net P&L | Win rate (95% Wilson CI) | Avg win / loss | PF | Expectancy (95% normal CI) | Turnover |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Breakout | 2 | $93.37 | $80.62 | $12.75 | $12.75 | $0.00 / $0.33 / $-0.33 | $68.20 | 100.0% (34.2%–100.0%) | $34.10 / — | — | $34.10 ($19.35–$48.85) | $25,495.93 |
| MeanReversion | 75 | $106.12 | $-424.85 | $530.97 | $530.97 | $5.84 / $6.54 / $-0.71 | $-955.11 | 42.7% (32.1%–53.9%) | $27.96 / $-43.02 | 0.484 | $-12.73 ($-21.26–$-4.21) | $1,061,944.55 |
| Momentum | 1 | $-37.53 | $-41.41 | $3.88 | $3.88 | $0.99 / $0.00 / $0.99 | $-46.27 | 0.0% (0.0%–79.3%) | — / $-46.27 | 0.000 | — | $7,755.23 |

| Component | Fees / turnover | Slippage / turnover | Fees / absolute raw gross | Slippage / absolute raw gross |
|---|---:|---:|---:|---:|
| Breakout | 0.0500% | 0.0500% | 13.65% | 13.65% |
| MeanReversion | 0.0500% | 0.0500% | 20.42% | 20.42% |
| Momentum | 0.0500% | 0.0500% | 10.33% | 10.33% |

Signals generated: 6871; executed trades: 78; kill-switch skips: 6793. Skipped signals have no assigned P&L.

## Interpretation and limitations

Fees and slippage are reported both as a share of turnover and of absolute raw gross P&L in `economics_groups.csv`. Comparing costs with absolute gross P&L is descriptive (and can exceed 100%); it is not a return denominator. For each group, the reconciliation is raw gross − slippage − fees − signed funding paid = net P&L.

`trades_enriched.csv` adds only the causal signal-time regime/features, confidence, duration, and original signal rationale to the actual executed ledger. `signals_causal.csv` includes signals emitted while safety-locked and their outcome class, but intentionally no P&L for skipped signals. Regime is the snapshot available at signal close; confidence bands are descriptive buckets, not tuned thresholds.

Win-rate uncertainty is shown with a 95% Wilson interval. Expectancy intervals use the sample standard error and a normal approximation; they are descriptive and can be unreliable for small or skewed samples. Profit factor is especially unstable for tiny groups. Do not interpret small component or condition slices as evidence of an edge.

The diagnostic reruns retained safety mode. They do not continue generating signals while a position is open (the strategy is only queried when flat and without a pending order), and they do not simulate any skipped signal. Thus this is signal quality under the existing event loop, not an uncensored counterfactual strategy test.

Each window directory includes enriched actual trades, causal signals, grouped economics, grouped signal counts, a result summary, and raw observer diagnostics. The existing safety/risk audit directory was not overwritten.

Reproduce with: `python tools/audit_trade_economics.py tests/tests/combined.csv --output reports/trade_economics_signal_audit`. Rebuild only the Markdown from completed artifacts with `--report-only`.