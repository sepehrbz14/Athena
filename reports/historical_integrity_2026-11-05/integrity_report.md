# Historical dataset integrity report

Read-only audit of `C:\Users\admin\OneDrive\Documents\Athena\tests\tests\combined.csv` (SHA-256 `5a9a149c5e35cd543280ca432daacf05fe3842ff063358df3d386e8140e1cf3d`).

- Candles inspected: **232,608**
- Time span: 2019-12-31T00:00:00+00:00 to 2026-08-19T23:45:00+00:00
- Duplicate timestamps: **0**
- Out-of-order timestamps: **0** (source sequence is chronological)
- Invalid OHLCV/parse records: **0**
- Timestamp intervals: one gap of 96 expected candles, 2025-08-13T23:45:00+00:00 → 2025-08-15T00:00:00+00:00; no other unexpected intervals.
- Timestamp convention: Unix milliseconds match candle opening labels, corroborated by the UTC quarter-hour sequence and Athena's candle-open timestamp convention.

## 2025-08-14

The date is absent from the daily source files and the combined series has no bars for it. The gap covers 2025-08-14 00:00 through 23:45 UTC (**96 missing 15-minute intervals**). The combined series exactly matches available daily files; no bars were synthesized.

## Reproduce

```powershell
python tools\audit_dataset_integrity.py tests\tests\combined.csv tests\tests reports\historical_integrity_2026-11-05
```

The JSON contains the timestamped gap and detailed audit fields. No data was modified or filled.
