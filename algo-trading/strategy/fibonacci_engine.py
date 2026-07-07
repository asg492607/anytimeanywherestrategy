from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))

FIB_RATIOS = {
    # ── Fib 1 (High to Low: 0% = High, 100% = Low) ──
    # Above-High extensions (Low-to-High BUY direction)
    'f1_4_618' : ('above', 4.618, '4.618', '#ff9800'),
    'f1_4_414' : ('above', 4.414, '4.414', '#e91e63'),
    'f1_4_272' : ('above', 4.272, '4.272', '#9c27b0'),
    'f1_4_000' : ('above', 4.000, '4.000', '#0d47a1'),
    'f1_3_618' : ('above', 3.618, '3.618', '#9c27b0'),
    'f1_3_414' : ('above', 3.414, '3.414', '#2196f3'),
    'f1_3_272' : ('above', 3.272, '3.272', '#9e9e9e'),
    'f1_3_000' : ('above', 3.000, '3.000', '#0d47a1'),
    'f1_2_618' : ('above', 2.618, '2.618', '#f44336'),
    'f1_2_414' : ('above', 2.414, '2.414', '#4caf50'),
    'f1_2_272' : ('above', 2.272, '2.272', '#ff9800'),
    'f1_2_000' : ('above', 2.000, '2.000', '#0d47a1'),
    'f1_1_618' : ('above', 1.618, '1.618', '#2196f3'),
    'f1_1_414' : ('above', 1.414, '1.41', '#f44336'),
    'f1_1_390' : ('above', 1.390, '1.39', '#f44336'),
    'f1_1_272' : ('above', 1.272, '1.272', '#ff9800'),
    'f1_1_140' : ('above', 1.140, '1.140', '#0d47a1'),
    'f1_1_000' : ('above', 1.000, '1.00',  '#0d47a1'),
    'f1_0_786' : ('above', 0.786, '0.786', '#131722'),
    'f1_0_236' : ('above', 0.236, '0.236', '#131722'),
    'f1_0_000' : ('above', 0.000, '0.00',  '#0d47a1'),

    # Below-Low extensions (High-to-Low SELL direction)
    'f2_0_236' : ('mirror', 0.236, '0.236', '#131722'),
    'f2_0_786' : ('mirror', 0.786, '0.786', '#131722'),
    'f2_1_272' : ('mirror', 1.272, '1.272', '#ff9800'),
    'f2_1_140' : ('mirror', 1.140, '1.140', '#0d47a1'),
    'f2_1_390' : ('mirror', 1.390, '1.39', '#f44336'),
    'f2_1_414' : ('mirror', 1.414, '1.41', '#f44336'),
    'f2_1_618' : ('mirror', 1.618, '1.618', '#2196f3'),
    'f2_2_000' : ('mirror', 2.000, '2.000', '#0d47a1'),
    'f2_2_272' : ('mirror', 2.272, '2.272', '#ff9800'),
    'f2_2_414' : ('mirror', 2.414, '2.414', '#4caf50'),
    'f2_2_618' : ('mirror', 2.618, '2.618', '#f44336'),
    'f2_3_000' : ('mirror', 3.000, '3.000', '#0d47a1'),
    'f2_3_272' : ('mirror', 3.272, '3.272', '#9e9e9e'),
    'f2_3_414' : ('mirror', 3.414, '3.414', '#2196f3'),
    'f2_3_618' : ('mirror', 3.618, '3.618', '#9c27b0'),
    'f2_4_000' : ('mirror', 4.000, '4.000', '#0d47a1'),
    'f2_4_272' : ('mirror', 4.272, '4.272', '#9c27b0'),
    'f2_4_414' : ('mirror', 4.414, '4.414', '#e91e63'),
    'f2_4_618' : ('mirror', 4.618, '4.618', '#ff9800'),

    # ── Standard Mid Lines (Perfect Confluence / Visual Reference) ──
    'level_0_618': ('above', 0.618, '0.618', '#0d47a1'),
    'level_0_500': ('above', 0.500, '0.50',  '#0d47a1'),
    'level_0_382': ('above', 0.382, '0.382', '#0d47a1'),
}

def _filter_spike(candle):
    """Returns a version of a weekly candle with illiquid spike wicks removed.
    If the High wick is >20% above the open/close body, cap it to the body max.
    If the Low  wick is >20% below the open/close body, cap it to the body min.
    This removes the fake 9:15 AM illiquidity prints that Angel One data includes."""
    o, c = candle['open'], candle['close']
    body_max = max(o, c)
    body_min = min(o, c)
    # Only catch truly catastrophic data errors (spike >100% above body).
    # Normal option price swings of 30-50% are REAL — do not filter them.
    # Proper anchor calculation is handled by add_true_anchors() in data_engine.py.
    true_high = candle['high'] if candle['high'] <= body_max * 2.00 else body_max
    true_low  = candle['low']   # Never filter the low — options fall legitimately
    return {**candle, 'high': true_high, 'low': true_low}


def group_into_weekly(daily_df):
    """Groups daily candles into SENSEX weekly expiry cycles (Mon open -> Fri close)."""
    if not daily_df:
        return []
    weekly_data, current_week, current_candle = [], None, None
    for d in daily_df:
        dt = datetime.fromtimestamp(d['time'], tz=IST)
        iso_year, iso_week, _ = dt.isocalendar()
        week_id = f"{iso_year}-W{iso_week:02d}"
        if current_week != week_id:
            if current_candle:
                weekly_data.append(current_candle)
            current_week = week_id
            # Anchor the weekly candle's time to Monday 09:15 IST of this ISO week.
            # Using the first trade timestamp is wrong for illiquid options — they can
            # start trading Tuesday or later, which shifts the Fibonacci anchor window
            # and causes add_true_anchors() to map pivots to the wrong week.
            monday = dt.date() - timedelta(days=dt.weekday())
            monday_open_ist = datetime(monday.year, monday.month, monday.day, 9, 15, 0, tzinfo=IST)
            current_candle = {
                'time': int(monday_open_ist.timestamp()),
                'open': d['open'], 'high': d['high'],
                'low': d['low'], 'close': d['close'], 'volume': d.get('volume', 0)
            }
        else:
            current_candle['high'] = max(current_candle['high'], d['high'])
            current_candle['low'] = min(current_candle['low'], d['low'])
            current_candle['close'] = d['close']
            current_candle['volume'] += d.get('volume', 0)
    if current_candle:
        weekly_data.append(current_candle)
    return weekly_data

def _calc_level(key, high, low):
    """Calculates price of a Fibonacci level based on high and low bounds."""
    direction, ratio, _, _ = FIB_RATIOS[key]
        
    diff = high - low
    if direction == 'above':
        return low + diff * ratio
    elif direction == 'mirror':
        return high - diff * ratio
    return low - diff * ratio

def _auto_weekly_high_low(weekly_df, symbol='SENSEX'):
    """Derives previous completed week's anchor boundaries."""
    if not weekly_df:
        return None, None, None, None

    now = datetime.now(IST)
    current_iso_year, current_iso_week, _ = now.isocalendar()

    completed_week = None
    for w in reversed(weekly_df):
        dt = datetime.fromtimestamp(w['time'], tz=IST)
        iso_year, iso_week, _ = dt.isocalendar()
        # We want the most recent week that is strictly before the current week
        if (iso_year < current_iso_year) or (iso_year == current_iso_year and iso_week < current_iso_week):
            completed_week = w
            break

    if not completed_week:
        if len(weekly_df) >= 2:
            completed_week = weekly_df[-2]
        elif len(weekly_df) == 1:
            completed_week = weekly_df[0]

    if completed_week:
        dt = datetime.fromtimestamp(completed_week['time'], tz=IST).date()
        # Approximate start as Monday and end as Friday of that week
        start = dt - timedelta(days=dt.weekday())
        end = start + timedelta(days=4)
        
        if symbol != 'SENSEX':
            # Apply spike filtering for options too — consistent with get_fibonacci_danger_zone
            # which calls _filter_spike() on every historical zone.
            # The old logic skipped this "because options move 200-500%" but that's wrong:
            # a single illiquid 9:15 AM trade can print a fake high (e.g. 1705 when the
            # option genuinely traded at 1320 all week), corrupting the Fibonacci anchor.
            filtered = _filter_spike(completed_week)
            return filtered['high'], filtered['low'], start, end
        else:
            # Apply spike filtering to SENSEX to remove illiquid 9:15 AM prints
            filtered = _filter_spike(completed_week)
            return filtered['high'], filtered['low'], start, end

    return None, None, None, None

def get_fibonacci_levels(weekly_df, symbol='SENSEX', manual_fibs=None):
    """Returns calculated levels dict, high boundary, and low boundary."""
    abs_high, abs_low = None, None
    if manual_fibs and symbol in manual_fibs:
        high = manual_fibs[symbol]['high']
        low = manual_fibs[symbol]['low']
        abs_high = manual_fibs[symbol].get('abs_high', high)
        abs_low = manual_fibs[symbol].get('abs_low', low)
        # For manual, we approximate the week start/end to current week
        now = datetime.now(IST)
        start = now - timedelta(days=now.weekday())
        end = start + timedelta(days=4)
    else:
        high, low, start, end = _auto_weekly_high_low(weekly_df, symbol)
        abs_high, abs_low = high, low
        if high is None:
            return None

    levels = {k: _calc_level(k, high, low) for k in FIB_RATIOS}
    return levels, high, low, start, end

def get_fibonacci_danger_zone(weekly_df, symbol='SENSEX', manual_fibs=None):
    """Returns a list of danger zones for all historical weeks in weekly_df."""
    if not weekly_df or len(weekly_df) < 2:
        return []

    zones = []
    for i in range(1, len(weekly_df)):
        prev = weekly_df[i - 1]
        curr = weekly_df[i]

        filtered_prev = _filter_spike(prev)
        h, l = filtered_prev['high'], filtered_prev['low']
        fibs = {k: _calc_level(k, h, l) for k in FIB_RATIOS}
        zones.append({'start_time': curr['time'], 'fibs': fibs, 'anchor_high': h, 'anchor_low': l})

    result = get_fibonacci_levels(weekly_df, symbol, manual_fibs)
    if result and result[0]:
        live_fibs, live_high, live_low, _, _ = result
        if zones:
            zones[-1]['fibs'] = live_fibs
            zones[-1]['anchor_high'] = live_high
            zones[-1]['anchor_low'] = live_low
        else:
            zones.append({
                'start_time': weekly_df[-1]['time'], 'fibs': live_fibs,
                'anchor_high': live_high, 'anchor_low': live_low
            })

    return zones

def get_confluence_levels(high, low):
    """Returns structured list of confluence and key reference levels."""
    diff = high - low
    
    # Upper Confluence (0.786 of f1 and 0.236 of f2)
    f1_0_786 = low + diff * 0.786
    f2_0_236 = high - diff * 0.236
    upper_confl = (f1_0_786 + f2_0_236) / 2.0

    # Lower Confluence (0.236 of f1 and 0.786 of f2)
    f1_0_236 = low + diff * 0.236
    f2_0_786 = high - diff * 0.786
    lower_confl = (f1_0_236 + f2_0_786) / 2.0

    return [
        {
            "level": "0.786 / 0.236",
            "price": float(upper_confl),
            "type": "Upper Confluence",
            "strength": "High"
        },
        {
            "level": "0.236 / 0.786",
            "price": float(lower_confl),
            "type": "Lower Confluence",
            "strength": "High"
        },
        {
            "level": "0.500",
            "price": float(low + diff * 0.500),
            "type": "Confluence Midpoint",
            "strength": "Medium"
        },
        {
            "level": "0.618",
            "price": float(low + diff * 0.618),
            "type": "Fib Golden Ratio",
            "strength": "Medium"
        },
        {
            "level": "0.382",
            "price": float(low + diff * 0.382),
            "type": "Fib Silver Ratio",
            "strength": "Medium"
        }
    ]

def get_reversal_zones(high, low):
    """Returns structured list of reversal boundaries for option trading strategies."""
    diff = high - low
    
    # Upper Extension (1.39 to 1.414 of f1 above High)
    f1_1_390 = low + diff * 1.390
    f1_1_414 = low + diff * 1.414

    # Upper Confluence (f1 0.786 and f2 0.236 overlap)
    f1_0_786 = low + diff * 0.786
    f2_0_236 = high - diff * 0.236

    # Lower Confluence (f1 0.236 and f2 0.786 overlap)
    f1_0_236 = low + diff * 0.236
    f2_0_786 = high - diff * 0.786

    # Lower Extension (1.39 to 1.414 of f2 below Low)
    f2_1_390 = high - diff * 1.390
    f2_1_414 = high - diff * 1.414

    return [
        {
            "zone": "Upper Extension",
            "from": float(min(f1_1_390, f1_1_414)),
            "to": float(max(f1_1_390, f1_1_414)),
            "type": "1.390-1.414"
        },
        {
            "zone": "Upper Confluence",
            "from": float(min(f1_0_786, f2_0_236)),
            "to": float(max(f1_0_786, f2_0_236)),
            "type": "0.236-0.786"
        },
        {
            "zone": "Lower Confluence",
            "from": float(min(f1_0_236, f2_0_786)),
            "to": float(max(f1_0_236, f2_0_786)),
            "type": "0.236-0.786"
        },
        {
            "zone": "Lower Extension",
            "from": float(min(f2_1_390, f2_1_414)),
            "to": float(max(f2_1_390, f2_1_414)),
            "type": "1.390-1.414"
        }
    ]
