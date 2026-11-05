# Independent strategy signal capture

This dataset contains every individual strategy output, regardless of portfolio position, pending entry, capital, or kill-switch state. The ensemble decision is retained in a separate `ensemble_selected` column. This is signal research only; it is not a backtest or executable portfolio result.

## Full-dataset run

```powershell
@'
from pathlib import Path
from crypto_bot.io import load_ohlcv_csv
from crypto_bot.research import capture_signals, export_signal_dataset
candles = load_ohlcv_csv('tests/tests/combined.csv')
rows = capture_signals(candles)
export_signal_dataset(rows, Path('reports/independent_signal_research_gap_parity_2026-11-05/signals.csv'))
print(len(rows))
'@ | python -
```

The measured run processed 232,608 candles and captured 51,292 signals in 16.61 seconds (17.81 seconds including load). Counts: mean reversion 27,969; breakout 16,288; momentum 4,809; trend following 2,226.

## Gap policy

On a timestamp discontinuity both research capture and the normal backtest clear market-derived 15m/1h/4h history, streaming EMA state, ATR and feature warm-up state, then continue from observed bars only. Higher-timeframe buckets may be partial while observed bars accumulate; no missing bars or prices are fabricated. Portfolio state is separate: any already pending entry is processed at the next observed open as before, and an open position continues to be marked, funded and exited using observed candles. Gap events record the identifier, previous/next observed timestamps, missing interval count and that portfolio state is preserved. Forward outcomes stop and report `censored_gap` at the first discontinuity.

Each captured row includes gap id and endpoints, missing interval count, post-gap observed-candle warm-up progress, and readiness of 15m ATR, 1h trend EMAs and 4h regime. `timestamp` labels the signal candle open; `available_at` labels its close. Forward horizons (1, 4, 16, 96 contiguous bars) begin at/after availability and include direction-adjusted returns, MFE and MAE.

The backtester’s funding model is continuous elapsed-time proration, an approximation. It is not exchange settlement replay; that requires historical settlement records.
