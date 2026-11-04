# Strategy evaluation — current 2× leverage / 0.5% risk

Run input: `tests\tests\combined.csv` (232,608 candles, 2019-12-31T00:00:00+00:00 through 2026-08-19T23:45:00+00:00).
Configuration: initial equity $10,000.00; risk 0.50%; leverage 2×; fee 5 bps/side; slippage 5 bps/side; funding 1 bps/8h; max notional 100% of equity; max margin 80% of equity; sizing cost reserve 20 bps round trip; max participation 5% of prior-candle volume; force-close at dataset end; kill switch **enabled in safety mode** (10% max drawdown threshold).
Input timestamps are candle-open labels; signal decisions occur at the 15m close. Trades are scored from the completed ledger, and no strategy settings were changed.

## Observed overall results

| Metric | Observed |
|---|---|
| Completed trades | 92 |
| Raw gross P&L (before modeled slippage, fees, funding) | $144.73 |
| Gross P&L at slipped execution prices (before fees/funding) | $-389.57 |
| Net P&L | $-927.38 |
| Fees paid | $534.30 |
| Slippage cost | $534.30 |
| Funding paid (negative means net credit) | $3.51 |
| Win rate | 39.13% |
| Average winning trade (net) | $42.31 |
| Average losing trade (net) | $-43.76 |
| Profit factor (net wins / absolute net losses) | 0.62 |
| Expectancy per completed trade (net) | $-10.08 |
| Final equity | $9,072.62 |
| Return on initial equity | -9.27% |
| Maximum marked-to-market drawdown | 10.09% |
| Longest consecutive losing trades | 5 |
| Mean / median holding time (candle-label difference) | 2.86h / 0.88h |

**Ledger reconciliation:** raw gross $144.73 − slippage $534.30 − fees $534.30 − funding paid $3.51 = net $-927.38. Maximum absolute per-trade reconciliation delta: 1.42e-14. Ledger net P&L equals final equity minus initial equity within 1e-7.

## Performance by selected strategy component

Attribution is exclusive to the single signal selected by the ensemble for each accepted trade (`Trade.strategy`). If multiple same-direction strategies signaled, the ensemble uses their confidence sum to choose a side but records the highest-confidence same-side candidate as the selected strategy; P&L is not split. Opposing candidates can cancel the decision. Signals below include selected ensemble signals, not every internal candidate from every component.

| Component | Selected signals | Trades | Wins/Losses | Net P&L | Win rate | PF | Expectancy |
|---|---|---|---|---|---|---|---|
| breakout | 2215 | 6 | 3/3 | $26.81 | 50.0% | 1.19 | $4.47 |
| mean_reversion | 20589 | 59 | 23/36 | $-617.30 | 39.0% | 0.59 | $-10.46 |
| momentum | 4179 | 10 | 3/7 | $-173.58 | 30.0% | 0.47 | $-17.36 |
| trend_following | 1718 | 17 | 7/10 | $-163.31 | 41.2% | 0.65 | $-9.61 |

## Long vs. short and market regime

| Side | Trades | Wins/Losses | Net P&L | Win rate | PF | Expectancy | Mean hold |
|---|---|---|---|---|---|---|---|
| long | 57 | 23/34 | $-579.04 | 40.4% | 0.62 | $-10.16 | 2.63h |
| short | 35 | 13/22 | $-348.34 | 37.1% | 0.63 | $-9.95 | 3.24h |

Regime is the primary 4H regime value present in the decision snapshot for the trade's signal ID. The engine intentionally uses its current developing higher-timeframe semantics; these are not completed-bar-only regime labels.

| Decision regime | Trades | Wins/Losses | Net P&L | Win rate | PF | Expectancy |
|---|---|---|---|---|---|---|
| bull_trend | 33 | 13/20 | $-310.08 | 39.4% | 0.67 | $-9.40 |
| range | 59 | 23/36 | $-617.30 | 39.0% | 0.59 | $-10.46 |

## Equity, drawdown, and safety switch

The safety switch activated while processing candle-open label `2020-01-28T01:00:00+00:00` (max_drawdown_limit); the equity snapshot uses that candle's close and therefore the decision/observation time is `2020-01-28T01:15:00+00:00`. Equity was $9,072.62, from a peak of $10,090.67 at `2020-01-09T20:45:00+00:00`; measured drawdown 10.0891%. 92 trades entered by that point; 0 entered afterward. Equity after activation ranged $9,072.62–$9,072.62 through the dataset end.

The switch remains enabled in this primary evaluation, so the reported loss and flat post-trigger equity are safety-censored results—not the performance of signals that were blocked after activation. The monthly table below contains month-end equity, return from the previous month-end (initial equity for the first partial month), and the largest running peak-to-date drawdown observed in that month. The full per-candle curve is in `equity_drawdown.csv`.

| Month | Month-end equity | Return | Peak-to-date DD |
|---|---|---|---|
| 2019-12 | $10,000.00 | 0.00% | 0.00% |
| 2020-01 | $9,072.62 | -9.27% | 10.09% |
| 2020-02 | $9,072.62 | 0.00% | 10.09% |
| 2020-03 | $9,072.62 | 0.00% | 10.09% |
| 2020-04 | $9,072.62 | 0.00% | 10.09% |
| 2020-05 | $9,072.62 | 0.00% | 10.09% |
| 2020-06 | $9,072.62 | 0.00% | 10.09% |
| 2020-07 | $9,072.62 | 0.00% | 10.09% |
| 2020-08 | $9,072.62 | 0.00% | 10.09% |
| 2020-09 | $9,072.62 | 0.00% | 10.09% |
| 2020-10 | $9,072.62 | 0.00% | 10.09% |
| 2020-11 | $9,072.62 | 0.00% | 10.09% |
| 2020-12 | $9,072.62 | 0.00% | 10.09% |
| 2021-01 | $9,072.62 | 0.00% | 10.09% |
| 2021-02 | $9,072.62 | 0.00% | 10.09% |
| 2021-03 | $9,072.62 | 0.00% | 10.09% |
| 2021-04 | $9,072.62 | 0.00% | 10.09% |
| 2021-05 | $9,072.62 | 0.00% | 10.09% |
| 2021-06 | $9,072.62 | 0.00% | 10.09% |
| 2021-07 | $9,072.62 | 0.00% | 10.09% |
| 2021-08 | $9,072.62 | 0.00% | 10.09% |
| 2021-09 | $9,072.62 | 0.00% | 10.09% |
| 2021-10 | $9,072.62 | 0.00% | 10.09% |
| 2021-11 | $9,072.62 | 0.00% | 10.09% |
| 2021-12 | $9,072.62 | 0.00% | 10.09% |
| 2022-01 | $9,072.62 | 0.00% | 10.09% |
| 2022-02 | $9,072.62 | 0.00% | 10.09% |
| 2022-03 | $9,072.62 | 0.00% | 10.09% |
| 2022-04 | $9,072.62 | 0.00% | 10.09% |
| 2022-05 | $9,072.62 | 0.00% | 10.09% |
| 2022-06 | $9,072.62 | 0.00% | 10.09% |
| 2022-07 | $9,072.62 | 0.00% | 10.09% |
| 2022-08 | $9,072.62 | 0.00% | 10.09% |
| 2022-09 | $9,072.62 | 0.00% | 10.09% |
| 2022-10 | $9,072.62 | 0.00% | 10.09% |
| 2022-11 | $9,072.62 | 0.00% | 10.09% |
| 2022-12 | $9,072.62 | 0.00% | 10.09% |
| 2023-01 | $9,072.62 | 0.00% | 10.09% |
| 2023-02 | $9,072.62 | 0.00% | 10.09% |
| 2023-03 | $9,072.62 | 0.00% | 10.09% |
| 2023-04 | $9,072.62 | 0.00% | 10.09% |
| 2023-05 | $9,072.62 | 0.00% | 10.09% |
| 2023-06 | $9,072.62 | 0.00% | 10.09% |
| 2023-07 | $9,072.62 | 0.00% | 10.09% |
| 2023-08 | $9,072.62 | 0.00% | 10.09% |
| 2023-09 | $9,072.62 | 0.00% | 10.09% |
| 2023-10 | $9,072.62 | 0.00% | 10.09% |
| 2023-11 | $9,072.62 | 0.00% | 10.09% |
| 2023-12 | $9,072.62 | 0.00% | 10.09% |
| 2024-01 | $9,072.62 | 0.00% | 10.09% |
| 2024-02 | $9,072.62 | 0.00% | 10.09% |
| 2024-03 | $9,072.62 | 0.00% | 10.09% |
| 2024-04 | $9,072.62 | 0.00% | 10.09% |
| 2024-05 | $9,072.62 | 0.00% | 10.09% |
| 2024-06 | $9,072.62 | 0.00% | 10.09% |
| 2024-07 | $9,072.62 | 0.00% | 10.09% |
| 2024-08 | $9,072.62 | 0.00% | 10.09% |
| 2024-09 | $9,072.62 | 0.00% | 10.09% |
| 2024-10 | $9,072.62 | 0.00% | 10.09% |
| 2024-11 | $9,072.62 | 0.00% | 10.09% |
| 2024-12 | $9,072.62 | 0.00% | 10.09% |
| 2025-01 | $9,072.62 | 0.00% | 10.09% |
| 2025-02 | $9,072.62 | 0.00% | 10.09% |
| 2025-03 | $9,072.62 | 0.00% | 10.09% |
| 2025-04 | $9,072.62 | 0.00% | 10.09% |
| 2025-05 | $9,072.62 | 0.00% | 10.09% |
| 2025-06 | $9,072.62 | 0.00% | 10.09% |
| 2025-07 | $9,072.62 | 0.00% | 10.09% |
| 2025-08 | $9,072.62 | 0.00% | 10.09% |
| 2025-09 | $9,072.62 | 0.00% | 10.09% |
| 2025-10 | $9,072.62 | 0.00% | 10.09% |
| 2025-11 | $9,072.62 | 0.00% | 10.09% |
| 2025-12 | $9,072.62 | 0.00% | 10.09% |
| 2026-01 | $9,072.62 | 0.00% | 10.09% |
| 2026-02 | $9,072.62 | 0.00% | 10.09% |
| 2026-03 | $9,072.62 | 0.00% | 10.09% |
| 2026-04 | $9,072.62 | 0.00% | 10.09% |
| 2026-05 | $9,072.62 | 0.00% | 10.09% |
| 2026-06 | $9,072.62 | 0.00% | 10.09% |
| 2026-07 | $9,072.62 | 0.00% | 10.09% |
| 2026-08 | $9,072.62 | 0.00% | 10.09% |

### Yearly

| Year | Year-end equity | Return | Peak-to-date DD |
|---|---|---|---|
| 2019 | $10,000.00 | 0.00% | 0.00% |
| 2020 | $9,072.62 | -9.27% | 10.09% |
| 2021 | $9,072.62 | 0.00% | 10.09% |
| 2022 | $9,072.62 | 0.00% | 10.09% |
| 2023 | $9,072.62 | 0.00% | 10.09% |
| 2024 | $9,072.62 | 0.00% | 10.09% |
| 2025 | $9,072.62 | 0.00% | 10.09% |
| 2026 | $9,072.62 | 0.00% | 10.09% |

## Interpretation: observed vs. hypothesis

Observed: the run closed with a net loss, and the kill switch prevented further entries after the January 2020 drawdown trigger. Exclusive attribution assigns the largest negative P&L to MeanReversion in this realized, safety-censored sample; the other three components also have negative net P&L except Breakout, whose small positive result is based on very few trades.

Hypothesis, not established causation: MeanReversion may be the largest contributor to the early drawdown, but this sample contains only 92 trades before the switch stopped entries. Component comparisons are not controlled experiments; components see different regimes and opportunity sets, and the chosen-component attribution does not allocate shared consensus credit. Do not infer a durable edge or rank from these figures alone.

## Proposed time-based evaluation protocol (not run)

1. Reserve the earliest data only for indicator warm-up (for example 2019-12-31 through the first completed warm-up window); exclude it from scored returns.
2. Use 2020–2022 as development data and 2023 as a single validation period. Any strategy/parameter choices belong only to development/validation; record them before opening the holdout.
3. Lock the strategy and all execution assumptions, then evaluate 2024–2025 once as the untouched final historical holdout. Do not tune on this result and continue calling it out-of-sample.
4. Treat 2026 data as forward/prequential evaluation only: freeze each test block before it begins; if later blocks use earlier blocks for training, report each block separately and never use the aggregate to justify repeated retuning. A truly untouched final test after any further change requires new future data.
5. If walk-forward tuning is needed before the final holdout, use rolling/expanding train windows followed by a separate validation block and then a strictly later test block. Fit thresholds/scalers/feature selection only on each training window; preserve a change log and evaluate the final locked configuration once on the sealed holdout.

## Artifacts

`trades.csv` contains each completed trade and reconstructed cost fields; `signals.csv` contains selected ensemble signals with contemporaneous regime; `strategy_performance.csv`, `side_performance.csv`, `regime_performance.csv`, `monthly_returns.csv`, `yearly_returns.csv`, and `equity_drawdown.csv` provide the report tables and detailed time series.
