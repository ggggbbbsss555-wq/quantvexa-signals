#!/usr/bin/env python3
"""
QuantVexa Pattern Analyzer
==========================
Analyzes candle data for 13 currencies, grouped by day of week,
and finds sequences of consecutive same-direction candles that
repeat 100% across all instances of each weekday.

For each duration D (1..20 minutes):
  1. Aggregate 1-min base candles into D-min candles
  2. Group by weekday (Mon..Sun)
  3. For each weekday, for each time slot, find the longest run of
     consecutive UP (or DOWN) candles that appears at that same
     clock-time in 100% of the weeks
  4. Write results to {D}minute.txt

All timestamps are UTC (candle timezone). No browser timezone logic.

To use with REAL candle data: replace generate_candles() with a loader
that reads your candle CSV/JSON and returns the same dict structure:
  { symbol: [ {t, o, h, l, c, dir}, ... ] }
where t = Unix seconds (UTC), dir = 'UP' or 'DOWN'.
"""

import os
from datetime import datetime, timedelta, timezone

# ============================================================================
# CONFIG
# ============================================================================
SYMBOLS = [
    'EURUSD-OTC', 'GBPUSD-OTC', 'USDJPY-OTC', 'AUDUSD-OTC', 'USDCAD-OTC',
    'EURGBP-OTC', 'EURJPY-OTC', 'GBPJPY-OTC',
    'BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'XRP/USDT', 'AVAX/USDT',
]
WEEKDAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
WEEKS_OF_DATA = 8          # how many weeks to generate / analyze
MIN_RUN_LEN = 2            # minimum consecutive candles to count as a "pattern"
MAX_RUN_LEN = 60           # cap search depth
INJECTED_PATTERNS = [
    # (symbol, weekday_index, start_minute_of_day, length_minutes, direction)
    # These are GUARANTEED 100% patterns baked into the synthetic data.
    # Each spans 60 minutes starting at 00:00 UTC so it aligns with every duration 1..20.
    ('EURUSD-OTC', 4, 0, 60, 'UP'),       # Friday 00:00-01:00, 60 UP
    ('GBPUSD-OTC', 0, 0, 60, 'DOWN'),     # Monday 00:00-01:00, 60 DOWN
    ('BTC/USDT',   6, 0, 60, 'UP'),        # Sunday 00:00-01:00, 60 UP
    ('ETH/USDT',   2, 0, 40, 'UP'),        # Wednesday 00:00-00:40, 40 UP
    ('SOL/USDT',   3, 0, 30, 'DOWN'),      # Thursday 00:00-00:30, 30 DOWN
]

# ============================================================================
# DATA GENERATION (synthetic — replace with real candle loader)
# ============================================================================
def generate_candles():
    """Generate 1-minute candle data for 13 symbols across WEEKS_OF_DATA weeks.
    Injects guaranteed 100% patterns so the demo produces non-empty output.
    """
    import random
    random.seed(42)

    base = {}  # symbol -> list of {t, o, h, l, c, dir}
    start_date = datetime(2026, 8, 3, 0, 0, 0, tzinfo=timezone.utc)  # a Monday

    total_days = WEEKS_OF_DATA * 7
    for day_offset in range(total_days):
        day_dt = start_date + timedelta(days=day_offset)
        weekday = day_dt.weekday()  # 0=Mon .. 6=Sun

        for symbol in SYMBOLS:
            candles = base.setdefault(symbol, [])
            # Starting price for the day
            if 'OTC' in symbol:
                price = 1.0 + (hash(symbol) % 100) * 0.01
            else:
                price = 100.0 + (hash(symbol) % 1000)

            for minute in range(1440):  # 24h
                t = int((day_dt + timedelta(minutes=minute)).timestamp())
                op = price
                # Default: random walk
                change = random.gauss(0, 0.0008)
                cl = op * (1 + change)

                # Check if this minute is inside an injected pattern
                forced_dir = None
                for (sym, wd, start_min, length, direction) in INJECTED_PATTERNS:
                    if sym == symbol and wd == weekday and start_min <= minute < start_min + length:
                        forced_dir = direction
                        break

                if forced_dir == 'UP':
                    cl = op * (1 + abs(random.gauss(0.0002, 0.0001)))
                elif forced_dir == 'DOWN':
                    cl = op * (1 - abs(random.gauss(0.0002, 0.0001)))

                hi = max(op, cl) * (1 + abs(random.gauss(0, 0.0003)))
                lo = min(op, cl) * (1 - abs(random.gauss(0, 0.0003)))
                direction = 'UP' if cl >= op else 'DOWN'

                candles.append({'t': t, 'o': op, 'h': hi, 'l': lo, 'c': cl, 'dir': direction})
                price = cl

    return base

# ============================================================================
# AGGREGATION: N 1-min candles -> 1 D-min candle
# ============================================================================
def aggregate(base_candles, duration_min):
    """Aggregate 1-min candles into duration_min candles.
    Each D-min candle: open=first.open, close=last.close, dir=UP if close>=open.
    Time slots are aligned to midnight UTC (t % (D*60) == 0).
    """
    aggregated = {}
    for symbol, candles in base_candles.items():
        agg = []
        slot_size = duration_min * 60  # seconds
        i = 0
        while i + duration_min <= len(candles):
            group = candles[i:i + duration_min]
            # Ensure this group is within the same day (no crossing midnight)
            first_t = group[0]['t']
            if first_t % slot_size != 0:
                i += 1
                continue
            op = group[0]['o']
            cl = group[-1]['c']
            hi = max(c['h'] for c in group)
            lo = min(c['l'] for c in group)
            direction = 'UP' if cl >= op else 'DOWN'
            agg.append({'t': first_t, 'o': op, 'h': hi, 'l': lo, 'c': cl, 'dir': direction})
            i += duration_min
        aggregated[symbol] = agg
    return aggregated

# ============================================================================
# PATTERN FINDER: 100% repeating sequences per weekday
# ============================================================================
def find_patterns(candles_by_symbol, duration_min):
    """For each symbol, group D-min candles by weekday.
    For each weekday, find time-slots where a run of >= MIN_RUN_LEN consecutive
    same-direction candles appears in 100% of the weeks.
    Returns a list of pattern dicts.
    """
    results = []
    slot_sec = duration_min * 60

    for symbol, candles in candles_by_symbol.items():
        # Group candles by weekday.
        # Slot key = minute-of-day (0..1439) so the same clock-time across
        # different weeks maps to the same key.
        # by_weekday[wd] = { week_key: { minute_of_day: direction } }
        by_weekday = {i: {} for i in range(7)}
        for c in candles:
            dt = datetime.fromtimestamp(c['t'], tz=timezone.utc)
            wd = dt.weekday()
            week_key = dt.isocalendar()[1]
            minute_of_day = dt.hour * 60 + dt.minute  # 0..1439
            by_weekday[wd].setdefault(week_key, {})[minute_of_day] = c['dir']

        for wd, weeks in by_weekday.items():
            week_list = list(weeks.values())
            if len(week_list) < 2:
                continue  # need at least 2 weeks

            # All minute-of-day slots present in week 0 (reference), sorted
            ref_week = week_list[0]
            ref_slots = sorted(ref_week.keys())

            for start_idx in range(len(ref_slots)):
                start_slot = ref_slots[start_idx]
                base_dir = ref_week[start_slot]

                # Extend the run as far as 100% match holds
                run_len = 0
                for offset in range(MAX_RUN_LEN):
                    idx = start_idx + offset
                    if idx >= len(ref_slots):
                        break
                    slot = ref_slots[idx]
                    # Must be same direction AND present in ALL weeks
                    all_match = all(
                        w.get(slot) == base_dir
                        for w in week_list
                    )
                    if all_match:
                        run_len += 1
                    else:
                        break

                if run_len >= MIN_RUN_LEN:
                    # Convert minute-of-day back to HH:MM
                    start_hour = start_slot // 60
                    start_min = start_slot % 60
                    end_slot_total = start_slot + run_len * duration_min
                    end_hour = (end_slot_total // 60) % 24
                    end_min = end_slot_total % 60
                    results.append({
                        'symbol': symbol,
                        'weekday': WEEKDAYS[wd],
                        'start': f'{start_hour:02d}:{start_min:02d}',
                        'end': f'{end_hour:02d}:{end_min:02d}',
                        'direction': base_dir,
                        'length': run_len,
                        'duration_min': duration_min,
                        'total_span_min': run_len * duration_min,
                        'weeks': len(week_list),
                    })

    # Deduplicate: remove sub-runs fully contained within a longer run.
    # A run P is "contained" in Q if same (symbol, weekday, direction),
    # Q starts <= P.start and Q ends >= P.end, and Q is strictly longer.
    # Convert HH:MM to total minutes for comparison.
    def to_min(hhmm):
        h, m = hhmm.split(':')
        return int(h) * 60 + int(m)

    final = list(results)
    filtered = []
    for p in final:
        p_start = to_min(p['start'])
        p_end = p_start + p['total_span_min']
        contained = False
        for q in final:
            if q is p:
                continue
            if not (q['symbol'] == p['symbol'] and q['weekday'] == p['weekday']
                    and q['direction'] == p['direction']):
                continue
            q_start = to_min(q['start'])
            q_end = q_start + q['total_span_min']
            # Q contains P if Q starts at or before P and ends at or after P,
            # and Q is strictly longer
            if q_start <= p_start and q_end >= p_end and q['total_span_min'] > p['total_span_min']:
                contained = True
                break
        if not contained:
            filtered.append(p)
    filtered.sort(key=lambda p: (p['symbol'], ['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'].index(p['weekday']), p['start']))
    return filtered

# ============================================================================
# MAIN
# ============================================================================
def main():
    out_dir = os.path.dirname(os.path.abspath(__file__))

    print('Generating candle data...')
    base = generate_candles()
    total = sum(len(v) for v in base.values())
    print(f'  {total:,} one-minute candles across {len(base)} symbols ({WEEKS_OF_DATA} weeks)')

    for duration in range(1, 21):
        print(f'Analyzing {duration:2d}-minute candles...', end=' ')
        agg = aggregate(base, duration)
        patterns = find_patterns(agg, duration)

        out_path = os.path.join(out_dir, f'{duration}minute.txt')
        with open(out_path, 'w', encoding='utf-8') as f:
            f.write(f'# QuantVexa — 100% Repeating Candle Patterns\n')
            f.write(f'# Duration: {duration} minute(s) per candle\n')
            f.write(f'# Symbols: {len(SYMBOLS)} currencies\n')
            f.write(f'# Data: {WEEKS_OF_DATA} weeks\n')
            f.write(f'# All times: UTC (candle timezone)\n')
            f.write(f'# Method: group candles by weekday, find consecutive UP/DOWN runs\n')
            f.write(f'#         that appear at the same clock-time in 100% of weeks\n')
            f.write(f'# Patterns found: {len(patterns)}\n')
            f.write(f'# {"=" * 90}\n')
            f.write(f'# {"SYMBOL":14s} | {"WEEKDAY":9s} | {"START-END":14s} | {"DIR":4s} | '
                    f'{"CANDLES":7s} | {"SPAN":8s} | {"MATCH"}\n')
            f.write(f'# {"-" * 90}\n\n')

            if not patterns:
                f.write('  (no 100% repeating patterns found at this duration)\n')
            else:
                for p in patterns:
                    f.write(f'  {p["symbol"]:14s} | {p["weekday"]:9s} | '
                            f'{p["start"]}-{p["end"]:5s} UTC | {p["direction"]:4s} | '
                            f'{p["length"]:2d} candles | {p["total_span_min"]:3d} min  | '
                            f'100% ({p["weeks"]}/{p["weeks"]} weeks)\n')

        print(f'{len(patterns)} patterns -> {duration}minute.txt')

    print(f'\nDone! 20 files written to {out_dir}')

if __name__ == '__main__':
    main()
