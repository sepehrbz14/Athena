# Frozen-signal execution-cost sensitivity

All scenarios use the same frozen signals and fixed-reference-risk sizing rules. Funding is held at the configured rate and reported separately. These are independent overlapping trades, not executable portfolios. No scenario is selected as preferred.

| Scenario | Strategy | Trades | Gross P&L | Fees | Slippage | Funding | Net P&L | Expectancy | PF |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 0 / 0 bps | mean_reversion | 27969 | $-24,906.73 | $0.00 | $0.00 | $-176.12 | $-24,730.61 | $-0.88 | 0.97 |
| 0 / 0 bps | breakout | 16288 | $-485.58 | $0.00 | $0.00 | $263.11 | $-748.69 | $-0.05 | 1.00 |
| 0 / 0 bps | trend_following | 2226 | $-2,733.39 | $0.00 | $0.00 | $258.33 | $-2,991.73 | $-1.34 | 0.95 |
| 0 / 0 bps | momentum | 4809 | $7,463.41 | $0.00 | $0.00 | $611.24 | $6,852.17 | $1.42 | 1.05 |
| 2 / 2 bps | mean_reversion | 27969 | $-23,188.97 | $85,859.39 | $85,859.39 | $-175.18 | $-194,732.56 | $-6.96 | 0.75 |
| 2 / 2 bps | breakout | 16288 | $-983.59 | $45,784.40 | $45,784.40 | $248.64 | $-92,801.03 | $-5.70 | 0.80 |
| 2 / 2 bps | trend_following | 2226 | $-2,572.20 | $5,372.28 | $5,372.28 | $238.87 | $-13,555.63 | $-6.09 | 0.80 |
| 2 / 2 bps | momentum | 4809 | $7,189.86 | $8,187.89 | $8,187.89 | $570.79 | $-9,756.70 | $-2.03 | 0.93 |
| 5 / 5 bps | mean_reversion | 27969 | $-20,889.36 | $192,663.60 | $192,663.60 | $-154.21 | $-406,062.35 | $-14.52 | 0.51 |
| 5 / 5 bps | breakout | 16288 | $-1,355.24 | $102,072.59 | $102,072.59 | $218.41 | $-205,718.83 | $-12.63 | 0.57 |
| 5 / 5 bps | trend_following | 2226 | $-2,215.76 | $11,850.69 | $11,850.69 | $210.72 | $-26,127.85 | $-11.74 | 0.62 |
| 5 / 5 bps | momentum | 4809 | $6,803.35 | $18,423.58 | $18,423.58 | $519.80 | $-30,563.61 | $-6.36 | 0.78 |
| 10 / 10 bps | mean_reversion | 27969 | $-16,706.07 | $305,982.92 | $305,982.91 | $-96.87 | $-628,575.03 | $-22.47 | 0.28 |
| 10 / 10 bps | breakout | 16288 | $-1,001.44 | $163,265.40 | $163,265.40 | $172.70 | $-327,704.95 | $-20.12 | 0.34 |
| 10 / 10 bps | trend_following | 2226 | $-1,733.75 | $19,245.88 | $19,245.88 | $172.83 | $-40,398.34 | $-18.15 | 0.41 |
| 10 / 10 bps | momentum | 4809 | $6,228.83 | $31,664.86 | $31,664.86 | $453.69 | $-57,554.58 | $-11.97 | 0.59 |

Funding is kept constant across the grid and its signed value is shown separately (`funding_paid`; negative values are net credits). Cost levels are analytical scenarios, not recommendations. See each scenario subdirectory for trade ledger, predictive results, monthly/regime summaries and reproducibility metadata.