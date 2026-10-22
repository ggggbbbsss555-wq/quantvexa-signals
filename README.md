# QuantVexa Pattern Analyzer

Finds **100% repeating candle patterns** for 13 currencies, grouped by day of week, across 20 candle durations (1–20 minutes).

## Files
- `analyzer.py` — analysis script (run it to regenerate all output files)
- `1minute.txt` … `20minute.txt` — one file per candle duration, listing every 100% repeating pattern found at that duration

## What it does
1. Generates (or loads) 1-minute candle data for **13 currencies** across **8 weeks**
2. For each duration **D** (1 to 20 minutes):
   - Aggregates 1-min candles into D-min candles
   - Groups them by **weekday** (Monday → Sunday, separately)
   - Scans for sequences of consecutive **UP** or **DOWN** candles that appear at the **same clock-time** in **100% of weeks**
   - Writes the results to `Dminute.txt`
3. Sub-runs contained within a longer run are removed — only the **longest** pattern per time range is kept

## Output format (each .txt file)
```
SYMBOL         | WEEKDAY   | START-END UTC  | DIR  | CANDLES | SPAN    | MATCH
EURUSD-OTC     | Friday    | 00:00-01:00    | UP   | 60 candles | 60 min | 100% (8/8 weeks)
```

## Timezone
**All times are UTC** (candle timezone). No browser timezone logic anywhere.

## Run
```bash
python3 analyzer.py
```
Produces 20 files (`1minute.txt` through `20minute.txt`) in the current directory.

## Using real candle data
Replace the `generate_candles()` function with a loader that reads your CSV/JSON and returns:
```python
{ symbol: [ {t, o, h, l, c, dir}, ... ] }
```
where `t` = Unix seconds (UTC), `dir` = `'UP'` or `'DOWN'`. Everything else stays the same.
