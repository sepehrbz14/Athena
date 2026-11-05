# Independent signal capture

This is a signal and forward-outcome dataset, not a trade simulation or portfolio result. Each raw strategy is evaluated once on the same per-candle market snapshot. The ensemble's selected decision is stored separately in `ensemble_selected`.

## Run

```powershell
@'
import json
from collections import Counter
from pathlib import Path
from time import perf_counter
from crypto_bot.io import load_ohlcv_csv
from crypto_bot.research import capture_signals, export_signal_dataset
candles = load_ohlcv_csv('tests/tests/combined.csv')
started = perf_counter()
rows = capture_signals(candles)
elapsed = perf_counter() - started
out = Path('reports/independent_signal_research_2026-11-05')
export_signal_dataset(rows, out/'signals.csv')
print(len(rows), dict(Counter(r['strategy'] for r in rows)), elapsed)
'@ | python -
```

For this full run: 232,608 candles, 51,292 raw strategy signals, 16.30 seconds capture time (17.55 seconds including CSV load). Per-strategy totals: mean reversion 27,969; breakout 16,288; momentum 4,809; trend following 2,226.

## Timing and gaps

`timestamp` is the signal candle's opening label. `available_at` is its close. Forward outcomes start at that availability time, using only subsequent bars. Returns and high/low-based MFE and MAE are direction adjusted. Horizons are 1, 4, 16, and 96 observed contiguous bars. A gap censors outcomes at the first discontinuity; the outcome reports `censored_gap` and the observed bar count. No synthetic bars or prices are introduced.

At every missing 15-minute interval, 15-minute history, aggregated 1-hour/4-hour bars, EMA state, and feature warm-up reset. Post-gap aggregates contain only observed bars; their current higher-timeframe bucket remains partial until later observed bars arrive, consistent with Athena's existing mutable-bucket snapshot semantics. Indicators warm up again from post-gap observations. This capture-layer policy does not alter the existing safety backtest path.

Funding remains a continuous prorated approximation in the backtester. It accrues by elapsed time and therefore crosses data gaps; it is not exchange settlement replay. Actual settlement accounting requires historical funding settlement records, which are not part of this candle dataset.

Files: `signals.csv` is the full flat export, `sample.json` contains the first ten rows, and `run_metadata.json` records source, row counts, runtime, timestamp convention, and gap policy.
