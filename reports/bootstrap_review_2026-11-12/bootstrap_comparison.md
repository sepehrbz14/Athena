# Bootstrap methodology comparison

The moving-block intervals below are primary. They were recomputed from the frozen completed-trade ledgers only; no signal generation, sizing, execution simulation, or backtest was rerun. The legacy values are the original independent-week cluster bootstrap.

- Scenario evaluation code snapshot: `1bb70314e62f73b227f1e0a0652ef8acc0727250`
- Bootstrap implementation commit: `f327a824534d1d9ee7764989ec6f8b48d6cf2811`
- Bootstrap implementation SHA-256: `4ee4e4fc5d4eba512796c4a1c283e432496401a279aa3b7aef4a8e24062dac51`
- Bootstrap checkout worktree clean: `True`
- Frozen signal dataset SHA-256: `0135f4f2329303662ffcb8d63a4d8aa954cde77cfb65accc51c815de5d9fc6f7`
- Signal count per scenario: 51,292
- Moving-block bootstrap: 2,000 iterations; 4 consecutive calendar weeks per non-circular sampled block; seed 20261105.

Four scenario metadata files were audited: all have a clean worktree, the same code snapshot, signal hash and signal count, and their fee/slippage/funding settings match their scenario labels. Full audit details are in `bootstrap_comparison.json`.

| Scenario | Strategy | Trades | Legacy expectancy CI | Moving-block expectancy CI | Legacy PF CI | Moving-block PF CI |
|---|---|---:|---:|---:|---:|---:|
| fee0_slip0 | mean_reversion | 27969 | [-1.66, -0.08] | [-1.61, -0.11] | [0.94, 1.00] | [0.94, 1.00] |
| fee0_slip0 | breakout | 16288 | [-0.95, 0.75] | [-0.81, 0.73] | [0.96, 1.03] | [0.97, 1.03] |
| fee0_slip0 | trend_following | 2226 | [-4.43, 1.62] | [-4.60, 1.85] | [0.85, 1.06] | [0.85, 1.07] |
| fee0_slip0 | momentum | 4809 | [-2.13, 5.08] | [-2.14, 4.90] | [0.93, 1.18] | [0.93, 1.17] |
| fee2_slip2 | mean_reversion | 27969 | [-7.68, -6.21] | [-7.65, -6.24] | [0.72, 0.77] | [0.72, 0.77] |
| fee2_slip2 | breakout | 16288 | [-6.56, -4.91] | [-6.52, -4.86] | [0.77, 0.82] | [0.77, 0.83] |
| fee2_slip2 | trend_following | 2226 | [-9.04, -3.29] | [-9.19, -3.03] | [0.71, 0.89] | [0.71, 0.89] |
| fee2_slip2 | momentum | 4809 | [-5.44, 1.48] | [-5.41, 1.25] | [0.82, 1.05] | [0.82, 1.04] |
| fee5_slip5 | mean_reversion | 27969 | [-15.19, -13.81] | [-15.31, -13.69] | [0.49, 0.53] | [0.48, 0.53] |
| fee5_slip5 | breakout | 16288 | [-13.48, -11.81] | [-13.58, -11.63] | [0.55, 0.60] | [0.54, 0.60] |
| fee5_slip5 | trend_following | 2226 | [-14.47, -9.17] | [-14.64, -8.90] | [0.55, 0.69] | [0.54, 0.70] |
| fee5_slip5 | momentum | 4809 | [-9.58, -3.10] | [-9.50, -3.32] | [0.69, 0.89] | [0.69, 0.88] |
| fee10_slip10 | mean_reversion | 27969 | [-23.16, -21.77] | [-23.45, -21.45] | [0.26, 0.29] | [0.25, 0.30] |
| fee10_slip10 | breakout | 16288 | [-20.97, -19.29] | [-21.22, -18.95] | [0.32, 0.36] | [0.31, 0.37] |
| fee10_slip10 | trend_following | 2226 | [-20.61, -15.80] | [-20.80, -15.47] | [0.35, 0.47] | [0.35, 0.48] |
| fee10_slip10 | momentum | 4809 | [-14.92, -9.05] | [-14.77, -9.19] | [0.52, 0.68] | [0.52, 0.67] |

These confidence intervals describe the variability under their resampling schemes; neither establishes independent observations or predictive validity.