# Safety-control and sizing investigation

Dataset: `tests/tests/combined.csv`  
SHA-256: `5a9a149c5e35cd543280ca432daacf05fe3842ff063358df3d386e8140e1cf3d`  
Dataset timestamps are candle-open instants in UTC. Safety observations occur after execution and mark-to-market at that candle's close; the decision time is the open timestamp plus 15 minutes.

## Controls and event reconstruction

Frozen safety limits in all three independent windows: daily loss 3% from the UTC-day equity baseline, maximum drawdown 10% from the running equity peak, ATR14/close 8%. Daily lock resets at the first observed candle of a new UTC date. Maximum drawdown and ATR locks set a permanent reason and do not reset during that run. The event loop handles completed fills/exits, marks equity, then checks daily loss, maximum drawdown, and ATR in that order. A lock gates new entries; it does not liquidate an existing position.

| Window | Activation at candle open (decision close) | Trigger snapshot | Other checks at activation |
|---|---|---|---|
| 2020–2022 | 2020-01-28 01:00Z (01:15Z) | Equity $9,072.6168; peak $10,090.6709; 10.0891% drawdown versus a $9,081.6038 threshold (breached by $8.9871). | UTC-day baseline $9,115.9270; daily threshold $8,842.4492; daily loss 0.4751%, not tripped. ATR14 $49.2429 / close $9,122.45 = 0.5398%, below 8%. The preceding stop-out was trade 92. |
| 2023–2024, daily lock | 2023-01-06 23:45Z (2023-01-07 00:00Z) | Equity $9,149.0906; UTC-day baseline $9,473.2028; daily threshold $9,189.0067; loss 3.4214%, $39.9161 below the threshold. | Peak $10,000; drawdown 8.5091%, below the $9,000 max-DD threshold. ATR14 $34.8357 / close $16,936.90 = 0.2057%, below 8%. |
| 2023–2024, permanent lock | 2023-01-08 23:15Z (23:30Z) | Equity $8,995.1868; peak $10,000; 10.0481% drawdown versus $9,000, breached by $4.8132. | Daily baseline had reset to $9,131.8632 at the UTC date boundary; daily threshold $8,857.9073; current daily loss 1.4967%, not tripped. ATR14 $29.6571 / close $17,042.70 = 0.1740%, below 8%. Trade 44 was open at this close and later stopped out; it was not liquidated by the switch. |
| 2025–Aug 2026, previously examined | 2025-01-13 15:30Z (15:45Z) | Equity $9,025.2714; peak $10,035.1577; 10.0635% drawdown versus $9,031.6419, breached by $6.3705. | Daily baseline $9,232.1117; daily threshold $8,955.1483; daily loss 2.2404%, not tripped. ATR14 $802.00 / close $92,615.70 = 0.8659%, below 8%. Trade 78 (short MeanReversion) was open; it later reached its take-profit and closed +$30.3904. The permanent lock remained active. |

The 2023 daily lock reset correctly at 2023-01-07 00:00Z; the audit counts one reset after a daily lock. All other UTC-day baselines likewise roll at day boundaries. Permanent locks remained set through the remainder of their scored window. No ATR lock activated. Every scored candle's exact equity, baseline, peak, ATR, thresholds, checks, and post-check lock state is in that window's `safety_checks.csv`; the script replays the switch and asserts its event list matches the engine.

## Trade losses, risk sizing, and costs

The per-window `trades.csv` is the chronological actual ledger, including strategy, side, entry/exit, raw and execution gross P&L, fees, slippage, funding, net P&L, and cumulative equity after close. `risk_ledger.csv` adds entry equity, 0.5% risk budget, stop distance, expected stop loss, notional/margin caps, realized stop loss, and the realized/expected ratio for every trade. No blocked signal is assigned a P&L.

The sizing audit re-ran the configured sizer for every fill and reproduced all 214 quantities exactly. No trade's realized stop loss exceeded its calculated stop-risk amount; no margin cap bound. The 0.5% setting is a ceiling, not a guaranteed deployment: the 100%-of-equity notional cap bound 3/92 trades in 2020–2022, 37/44 in 2023–2024, and 13/78 in 2025–August 2026. Mean intended stop risk was 0.4993%, 0.4139%, and 0.4933% of entry equity respectively. Among stop exits, mean realized loss was 94.02%, 87.75%, and 92.51% of the calculated stop-risk amount; none exceeded it. The lower realization reflects conservative round-trip cost reservation in sizing, realized funding, and the actual execution prices; the full per-trade reconciliation is retained in the ledger.

Turnover is the sum of each filled entry notional plus exit notional (`quantity × slipped fill price`); it is not bar-by-bar exposure and does not include funding. Fee and slippage rates each reconcile to 5 bps of their applicable executed/reference notional per side:

| Window | Entry + exit turnover | Fees | Slippage | Each as % turnover | Each as % of $10k initial equity |
|---|---:|---:|---:|---:|---:|
| 2020–2022 | $1,068,601.43 (106.86×) | $534.30 | $534.30 | 0.0500% | 5.3430% |
| 2023–2024 | $828,589.94 (82.86×) | $414.29 | $414.29 | 0.0500% | 4.1430% |
| 2025–Aug 2026 | $1,095,195.71 (109.52×) | $547.60 | $547.60 | 0.0500% | 5.4760% |

Slippage is modeled price impact, not measured market impact. Fee and slippage values are reconciled trade-by-trade against the ledger; funding is separately subtracted (negative funding is a credit).

## Reconciliation and scope

Each account starts independently with $10,000, a fresh position state, sizer, and kill switch. Historical pre-window candles seed indicators and ATR only; they do not change scored equity or account state. No window returns are joined into a synthetic curve. The 2025–August 2026 segment remains a previously examined diagnostic period, not an untouched holdout. Full results and reconciliation deltas are in each `summary.json`.

The warm-up boundary audit found that historical context previously seeded strategy features but not the kill switch's initial ATR calculation. The backtest now uses warm-up candles for ATR values while observing the switch only on scored candles. A targeted test proves the first scored candle can use warm-up ATR and that no warm-up candle generates an event. This boundary correction did not change these runs' trades or activation counts; none of the newly available initial ATR values approached the 8% threshold. No strategy parameters, execution assumptions, limits, or safety behavior were otherwise changed.

## Reproducibility

```powershell
& 'C:\Program Files\Blender Foundation\Blender 4.5\4.5\python\bin\python.exe' tools\run_chronological_windows.py tests\tests\combined.csv --output reports\chronological_windows
& 'C:\Program Files\Blender Foundation\Blender 4.5\4.5\python\bin\python.exe' -m unittest discover -s tests -v
```

