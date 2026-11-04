# Signal-quality and cost-sensitivity research

Dataset SHA-256: `5a9a149c5e35cd543280ca432daacf05fe3842ff063358df3d386e8140e1cf3d`. Source signals are recorded causal signal events. The frozen production configuration and all prior reports remain unchanged.

## Causal forward-return diagnostics

These are forward market outcomes measured from the signal candle close. Future prices are outcome labels only; they are not used in signal features or decisions, and the results are not executable portfolio P&L. MFE/MAE uses the future OHLC path through the stated horizon. Signal observations overlap; uncertainty uses a 200-replicate block bootstrap that resamples occupied 64-candle chronological blocks intact.

Forward summaries are in `forward_summary.csv`; per-signal outcomes and the recorded decision features are in `forward_outcomes.csv`. Group dimensions include each of strategy, side, decision-time regime, and confidence band, plus strategy-by-side/regime/confidence slices. The CSV reports mean/median, standard deviation, IQR, p10/p90, block-bootstrap intervals, sample count, MFE, and MAE for 1/4/16/64-candle horizons.

Signal counts used:

- 2020_2022: 13,158 recorded signals
- 2023_2024: 8,580 recorded signals
- 2025_2026-08-19_previously_examined: 6,871 recorded signals

Only signals with a fully observed forward horizon before that window's end are included for that horizon. Signals generated after safety locks remain descriptive signal observations, not trades or P&L. Bootstrap blocks retain local time clustering, but are approximate for irregularly spaced signals and cannot remove all selection or regime dependence.

## Cost sensitivity

The grid was declared before evaluating scenarios. Baseline is actual; all other rows are hypothetical. Only fees and slippage vary. Funding remains 0.0001 per 8 hours, the baseline 0.20% sizing cost reserve is frozen, and causal prior-volume fills, participation, risk sizing mechanics, leverage, safety thresholds, next-open timing, and force-close behavior remain fixed. Safety remains enabled in every run.

| Window | Scenario | Fee / slip bps per side | Actual / hypothetical | Trades | Raw gross | Exec gross | Fees | Slippage | Net funding paid | Net P&L | Return | Max DD | Turnover | Kill activations | Skipped signals |
|---|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 2020_2022 | baseline_actual | 5 / 5 | Actual baseline | 92 | $144.73 | $-389.57 | $534.30 | $534.30 | $3.51 | $-927.38 | -9.27% | 10.09% | $1,068,601 | 1 | 13066 |
| 2020_2022 | lower_cost_2p5_2p5 | 2.5 / 2.5 | Hypothetical | 127 | $-87.76 | $-478.76 | $390.99 | $390.99 | $2.61 | $-872.36 | -8.72% | 10.05% | $1,563,971 | 1 | 12975 |
| 2020_2022 | lower_cost_1_2 | 1 / 2 | Hypothetical | 139 | $-286.85 | $-637.44 | $175.30 | $350.59 | $2.96 | $-815.70 | -8.16% | 10.13% | $1,752,954 | 1 | 12956 |
| 2020_2022 | lower_cost_0p5_1 | 0.5 / 1 | Hypothetical | 156 | $-464.01 | $-663.97 | $99.98 | $199.97 | $4.32 | $-768.27 | -7.68% | 10.16% | $1,999,662 | 1 | 12915 |
| 2023_2024 | baseline_actual | 5 / 5 | Actual baseline | 44 | $-202.12 | $-616.42 | $414.29 | $414.29 | $-2.01 | $-1,028.70 | -10.29% | 10.29% | $828,590 | 2 | 8536 |
| 2023_2024 | lower_cost_2p5_2p5 | 2.5 / 2.5 | Hypothetical | 202 | $419.55 | $-286.74 | $706.28 | $706.28 | $7.96 | $-1,000.98 | -10.01% | 10.05% | $2,825,126 | 1 | 8149 |
| 2023_2024 | lower_cost_1_2 | 1 / 2 | Hypothetical | 229 | $-5.08 | $-671.90 | $333.41 | $666.82 | $9.56 | $-1,014.88 | -10.15% | 10.32% | $3,334,096 | 2 | 8099 |
| 2023_2024 | lower_cost_0p5_1 | 0.5 / 1 | Hypothetical | 232 | $-116.24 | $-464.93 | $174.34 | $348.69 | $9.77 | $-649.05 | -6.49% | 10.05% | $3,486,900 | 2 | 8094 |
| 2025_2026-08-19_previously_examined | baseline_actual | 5 / 5 | Actual baseline | 78 | $161.96 | $-385.64 | $547.60 | $547.60 | $-0.05 | $-933.19 | -9.33% | 10.06% | $1,095,196 | 1 | 6793 |
| 2025_2026-08-19_previously_examined | lower_cost_2p5_2p5 | 2.5 / 2.5 | Hypothetical | 110 | $-194.87 | $-578.93 | $384.05 | $384.05 | $-1.41 | $-961.57 | -9.62% | 10.16% | $1,536,213 | 1 | 6726 |
| 2025_2026-08-19_previously_examined | lower_cost_1_2 | 1 / 2 | Hypothetical | 198 | $-179.66 | $-708.03 | $264.19 | $528.37 | $-1.61 | $-970.61 | -9.71% | 10.37% | $2,641,873 | 1 | 6553 |
| 2025_2026-08-19_previously_examined | lower_cost_0p5_1 | 0.5 / 1 | Hypothetical | 306 | $-248.47 | $-676.66 | $214.10 | $428.19 | $1.18 | $-891.94 | -8.92% | 10.25% | $4,281,928 | 2 | 6318 |

The baseline row in each window was asserted against the preserved safety/risk trade ledger, equity curve, final equity, drawdown, and kill events. Lower-cost rows are independent hypothetical replays, not actual results and not combined with the safety-on baseline. Lower modeled costs can change equity, risk sizing in dollars, lock timing, and which signals become actual entries under the unchanged rules; the table reports those scenario outcomes as such.

## Findings and limitations

A descriptive forward-return distribution is not evidence of a tradable edge: it omits portfolio overlap, sizing, fees/funding, signal competition, market impact, and safety-state execution. Use confidence intervals and sample counts; small component/regime slices are especially uncertain. Cost sensitivity is assumption analysis, not a claim that lower costs are achievable.

The 2025–August 2026 window was previously examined and is not an untouched holdout. Before strategy selection, reserve a genuinely future chronological period not inspected during prior reports or tuning; freeze the complete system first, then evaluate once with realistic costs and safety controls. If a future period is unavailable, use an untouched chronological segment only after verifying it has not been examined; do not reuse the 2025–2026 diagnostic window as holdout.

No thresholds were tuned, no component was removed or promoted, and no skipped-signal P&L was assigned. The cost grid retains safety-on behavior and does not enable live trading.

Reproduce with `python tools/run_signal_quality_cost_sensitivity.py tests/tests/combined.csv --output reports/signal_quality_cost_sensitivity_2026-10-15`.