# Chronological safety-on window diagnostics

Dataset SHA-256: `5a9a149c5e35cd543280ca432daacf05fe3842ff063358df3d386e8140e1cf3d`. Each row below is an independent account run; values are not compounded, stitched, or combined.

The 2025–2026-08-19 window is a previously examined historical diagnostic period, not an untouched holdout. Warm-up candles are strictly earlier than each scored start and only seed indicators. Every window starts with fresh $10,000 equity and a fresh safety kill switch.

| Window | Trades | Raw gross | Net P&L | Fees | Slippage | Funding | Expectancy | PF | Return | Max DD | Exposure | Turnover / initial | Underwater | Kill trips | Skipped signals |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 2020_2022 | 92 | $144.73 | $-927.38 | $534.30 | $534.30 | $3.51 | $-10.08 | 0.622 | -9.27% | 10.09% | 1.09% (286.0h) | 106.86× | 26110.5h (99.26%; max spell 26082.2h) | 1 | 13066 |
| 2023_2024 | 44 | $-202.12 | $-1,028.70 | $414.29 | $414.29 | $-2.01 | $-23.38 | 0.061 | -10.29% | 10.29% | 0.56% (99.0h) | 82.86× | 17527.5h (99.91%; max spell 17527.5h) | 2 | 8536 |
| 2025_2026-08-19_previously_examined | 78 | $161.96 | $-933.19 | $547.60 | $547.60 | $-0.05 | $-11.96 | 0.508 | -9.33% | 10.06% | 1.20% (170.8h) | 109.52× | 14278.5h (99.99%; max spell 14278.5h) | 1 | 6793 |

## Window interpretation

- **2020_2022**: 92 trades, 1 kill-switch activation(s), 13066 skipped signals. The switch activated and censored subsequent in-window opportunities; performance after activation is not assessed as taken trades.
- **2023_2024**: 44 trades, 2 kill-switch activation(s), 8536 skipped signals. The switch activated and censored subsequent in-window opportunities; performance after activation is not assessed as taken trades.
- **2025_2026-08-19_previously_examined**: 78 trades, 1 kill-switch activation(s), 6793 skipped signals. The switch activated and censored subsequent in-window opportunities; performance after activation is not assessed as taken trades.

Each window directory contains its own `summary.json`, `trades.csv` (including post-close equity and sizing comparison), `risk_ledger.csv`, `safety_checks.csv` (every scored candle), and `equity_drawdown.csv`. Safety checks are replayed candle-by-candle and asserted equal to the engine's activation events. Trade net P&L reconciles to final equity, gross/cost/funding identity reconciles, and the equity-curve endpoint and maximum drawdown match. No synthetic full-period result is generated.
