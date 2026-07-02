import os
import json
import time
import threading
from datetime import datetime, timedelta, timezone
import pandas as pd
import numpy as np
import pyotp
import requests
from SmartApi import SmartConnect
from SmartApi.smartWebSocketV2 import SmartWebSocketV2

IST = timezone(timedelta(hours=5, minutes=30))

API_KEY    = os.environ.get("ANGEL_API_KEY",    "G2Tjf9cb")
CLIENT_CODE= os.environ.get("ANGEL_CLIENT_CODE","AACF753746")
PASSWORD   = os.environ.get("ANGEL_PASSWORD",   "9545")
TOTP_SECRET= os.environ.get("ANGEL_TOTP_SECRET","TJKRQNWUMYSOSNINFPTKZ2YS74")

# ─── Global State ──────────────────────────────────────────────────────────────
TOKENS           = {}
LIVE_PRICES      = {}
smartApi         = None
feedToken        = None
SCRIP_MASTER_DATA= []
API_LOCK         = threading.Lock()
LAST_API_CALL_TIME = 0
GLOBAL_SWS       = None
MANUAL_FIBS      = {}          # {symbol: {high, low}} — overrides auto calc
HISTORICAL_CACHE  = {}
DASHBOARD_CACHE   = {'data': None, 'ts': 0}   # pre-warmed result
CACHE_LOCK        = threading.Lock()
REFRESH_IN_PROGRESS = False


# ─── Authentication ────────────────────────────────────────────────────────────
def initialize_angel_one():
    global smartApi, feedToken
    print("Authenticating with Angel One…")
    smartApi = SmartConnect(api_key=API_KEY)
    totp_val = pyotp.TOTP(TOTP_SECRET).now()
    resp = smartApi.generateSession(CLIENT_CODE, PASSWORD, totp_val)
    if resp['status']:
        print("Login Successful!")
        feedToken = smartApi.getfeedToken()
        # Wait 5 seconds after login before any API calls — Angel One rate limits fresh sessions hard
        time.sleep(5)
        fetch_tokens()
        # Pre-warm cache in background so first request is instant
        threading.Thread(target=_refresh_cache, daemon=True).start()
    else:
        print("Login Failed:", resp)


# ─── Scrip Master ──────────────────────────────────────────────────────────────
def fetch_tokens():
    global TOKENS, SCRIP_MASTER_DATA
    token_file = "tokens_cache.json"
    if not os.path.exists(token_file) or time.time() - os.path.getmtime(token_file) > 86400:
        print("Downloading Angel One Scrip Master…")
        url = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
        data = requests.get(url, timeout=30).json()
        with open(token_file, "w") as f:
            json.dump(data, f)
    else:
        with open(token_file, "r") as f:
            data = json.load(f)
    SCRIP_MASTER_DATA = data
    # SENSEX index
    TOKENS['SENSEX'] = {'token': '99919000', 'symbol': 'SENSEX', 'exch_seg': 'BSE'}
    print("Scrip master loaded:", len(SCRIP_MASTER_DATA), "instruments")


def search_contracts(query):
    results = []
    count   = 0
    for item in SCRIP_MASTER_DATA:
        if query in item.get('symbol', '') and item.get('exch_seg') == 'BFO' and 'SENSEX' in item.get('symbol', ''):
            results.append({
                'symbol'        : item['symbol'],
                'token'         : item['token'],
                'expiry'        : item.get('expiry', ''),
                'strike'        : item.get('strike', ''),
                'instrumenttype': item.get('instrumenttype', '')
            })
            count += 1
            if count >= 20:
                break
    return results


def set_active_tokens(ce_symbol, pe_symbol):
    global TOKENS, GLOBAL_SWS
    ce_token = pe_token = None
    for item in SCRIP_MASTER_DATA:
        if item.get('symbol') == ce_symbol: ce_token = item['token']
        if item.get('symbol') == pe_symbol: pe_token = item['token']
    if not ce_token or not pe_token:
        return
    old_tokens = [TOKENS.get('CALL', {}).get('token', ''), TOKENS.get('PUT', {}).get('token', '')]
    TOKENS['CALL'] = {'token': ce_token, 'symbol': ce_symbol, 'exch_seg': 'BFO'}
    TOKENS['PUT']  = {'token': pe_token, 'symbol': pe_symbol, 'exch_seg': 'BFO'}
    if GLOBAL_SWS and hasattr(GLOBAL_SWS, 'subscribe'):
        if old_tokens[0]:
            try: GLOBAL_SWS.unsubscribe('sws', 1, [{"exchangeType": 4, "tokens": old_tokens}])
            except: pass
        try: GLOBAL_SWS.subscribe('sws', 1, [{"exchangeType": 4, "tokens": [ce_token, pe_token]}])
        except: pass
    for t in ['SENSEX', 'CALL', 'PUT']:
        if t in TOKENS: LIVE_PRICES[t] = {}
    time.sleep(0.6)
    threading.Thread(target=start_websocket, daemon=True).start()


# ─── WebSocket (Live Prices) ───────────────────────────────────────────────────
def start_websocket():
    global GLOBAL_SWS
    while True:
        try:
            auth_token = getattr(smartApi, 'access_token', None) or smartApi.getfeedToken()
            sws = SmartWebSocketV2(auth_token, API_KEY, CLIENT_CODE, feedToken)
            GLOBAL_SWS = sws

            def on_data(wsapp, message):
                token = message.get('token')
                ltp   = message.get('last_traded_price')
                if ltp:
                    for key, val in TOKENS.items():
                        if val.get('token') == token:
                            LIVE_PRICES[key] = {'ltp': ltp / 100.0, 'time': int(datetime.now(IST).timestamp())}

            def on_open(wsapp):
                print("WebSocket Connected!")
                token_list = [{"exchangeType": 3, "tokens": [TOKENS['SENSEX']['token']]}]
                if 'CALL' in TOKENS and 'PUT' in TOKENS:
                    token_list.append({"exchangeType": 4, "tokens": [TOKENS['CALL']['token'], TOKENS['PUT']['token']]})
                sws.subscribe('sws', 1, token_list)

            def on_error(wsapp, error): 
                print("WebSocket Error:", error)

            sws.on_open  = on_open
            sws.on_data  = on_data
            sws.on_error = on_error
            
            sws.connect()
        except Exception as e:
            print("WebSocket crashed:", e)
        
        print("WebSocket disconnected, reconnecting in 5 seconds...")
        time.sleep(5)


# ─── Historical Data ───────────────────────────────────────────────────────────
def get_historical_data(exchange, symboltoken, interval, days):
    global LAST_API_CALL_TIME
    cache_key = f"{symboltoken}_{interval}_{days}"
    if cache_key in HISTORICAL_CACHE:
        ts, cached = HISTORICAL_CACHE[cache_key]
        if time.time() - ts < 60:
            return cached
            
    for attempt in range(3):
        try:
            now = datetime.now(IST)
            params = {
                "exchange"   : exchange,
                "symboltoken": symboltoken,
                "interval"   : interval,
                "fromdate"   : (now - timedelta(days=days)).strftime("%Y-%m-%d %H:%M"),
                "todate"     : now.strftime("%Y-%m-%d %H:%M")
            }
            
            with API_LOCK:
                elapsed = time.time() - LAST_API_CALL_TIME
                if elapsed < 2.0:
                    time.sleep(2.0 - elapsed)
                res = smartApi.getCandleData(params)
                LAST_API_CALL_TIME = time.time()
                
            if res and isinstance(res, dict) and res.get('status') and res.get('data'):
                data = []
                for row in res['data']:
                    dt = datetime.strptime(row[0], "%Y-%m-%dT%H:%M:%S+05:30").replace(tzinfo=IST)
                    if row[1] == 0 or row[4] == 0:
                        continue
                    data.append({
                        'time': int(dt.timestamp()),
                        'open': row[1], 'high': row[2], 'low': row[3], 'close': row[4], 'volume': row[5] or 0
                    })
                HISTORICAL_CACHE[cache_key] = (time.time(), data)
                return data
            else:
                print(f"Attempt {attempt+1}: Angel One API returned no data or error: {res}")
        except Exception as e:
            print(f"Attempt {attempt+1}: Historical Data Error:", e)
        time.sleep(3.0)
        
    return None


# ─── Weekly Grouping (SENSEX Monday-Friday expiry cycle) ────────────────────
def group_into_weekly(daily_df):
    """
    Groups daily candles into SENSEX weekly expiry cycles.
    SENSEX expires every Friday, so each 'week' = Monday open → Friday close.
    Algorithm: use standard ISO week numbering (Monday is day 1).
    """
    if not daily_df:
        return []
    weekly_data, current_week, current_candle = [], None, None
    for d in daily_df:
        dt         = datetime.fromtimestamp(d['time'])
        iso_year, iso_week, _ = dt.isocalendar()
        week_id    = f"{iso_year}-W{iso_week:02d}"
        if current_week != week_id:
            if current_candle:
                weekly_data.append(current_candle)
            current_week   = week_id
            current_candle = {'time': d['time'], 'open': d['open'], 'high': d['high'], 'low': d['low'], 'close': d['close'], 'volume': d.get('volume', 0)}
        else:
            current_candle['high']   = max(current_candle['high'], d['high'])
            current_candle['low']    = min(current_candle['low'],  d['low'])
            current_candle['close']  = d['close']
            current_candle['volume'] += d.get('volume', 0)
    if current_candle:
        weekly_data.append(current_candle)
    return weekly_data


# ─── Fibonacci Levels ──────────────────────────────────────────────────────────
FIB_RATIOS = {
    # ── Fib 1 (High to Low: 0% = High, 100% = Low) ──────────────────────────────
    # Above-High extensions
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
    'f1_1_000' : ('above', 1.000, '1.00',  '#0d47a1'),
    'f1_0_786' : ('above', 0.786, '0.786', '#131722'),
    'f1_0_236' : ('above', 0.236, '0.236', '#131722'),
    'f1_0_000' : ('above', 0.000, '0.00',  '#0d47a1'),

    # Below-Low extensions
    'f2_0_236' : ('mirror', 0.236, '0.236', '#131722'),
    'f2_0_786' : ('mirror', 0.786, '0.786', '#131722'),
    'f2_1_272' : ('mirror', 1.272, '1.272', '#ff9800'),
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

    # ── Standard Mid Lines (Perfect Confluence / Visual Reference) ────────────────
    'level_0_618': ('above', 0.618, '0.618', '#0d47a1'),
    'level_0_500': ('above', 0.500, '0.50',  '#0d47a1'),
    'level_0_382': ('above', 0.382, '0.382', '#0d47a1'),
}

def _calc_level(key, high, low):
    direction, ratio, _, _ = FIB_RATIOS[key]
    diff = high - low
    if direction == 'above':
        # Fib 1: measured UP from Low. 0.0=Low, 1.0=High, >1.0 = above High
        return low + diff * ratio
    elif direction == 'mirror':
        # Fib 2: measured DOWN from High. 0.0=High, 1.0=Low, >1.0 = below Low
        return high - diff * ratio
    else:
        return low - diff * ratio

def _auto_weekly_high_low(weekly_df):
    """
    Automatically derive the correct anchor high/low for the CURRENT week
    from the PREVIOUS completed expiry cycle.
    Uses the last completed Friday-Thursday cycle.
    """
    now          = datetime.now(IST)
    today        = now.date()
    weekday      = today.weekday()      # 0=Mon … 4=Fri … 6=Sun

    # Find the last completed Friday (=last expiry date)
    days_to_last_fri = (weekday - 4) % 7
    last_fri         = today - timedelta(days=days_to_last_fri)

    # Previous contract cycle = (last_fri - 7) to (last_fri - 1)
    prev_cycle_end   = last_fri - timedelta(days=1)        # Thursday
    prev_cycle_start = prev_cycle_end - timedelta(days=6)  # Friday

    # Filter daily_df candles that fall within the previous cycle
    highs, lows = [], []
    for w in weekly_df:
        dt = datetime.fromtimestamp(w['time']).date()
        if prev_cycle_start <= dt <= prev_cycle_end:
            highs.append(w['high'])
            lows.append(w['low'])

    if highs:
        return max(highs), min(lows)
    # Fallback: use the second-to-last weekly candle
    if len(weekly_df) >= 2:
        return weekly_df[-2]['high'], weekly_df[-2]['low']
    return None, None


def get_fibonacci_levels(weekly_df, symbol='SENSEX'):
    """
    Returns the flat dict of {key: price} for the CURRENT week's Fib levels.
    Priority: MANUAL_FIBS override → automatic expiry-cycle detection.
    """
    if symbol in MANUAL_FIBS:
        high = MANUAL_FIBS[symbol]['high']
        low  = MANUAL_FIBS[symbol]['low']
    else:
        high, low = _auto_weekly_high_low(weekly_df)
        if high is None:
            return None

    return {k: _calc_level(k, high, low) for k in FIB_RATIOS}, high, low


def get_fibonacci_danger_zone(weekly_df, symbol='SENSEX'):
    """
    Returns a list of zone dicts, one per historical week + the current live week.
    Each zone: {start_time, fibs: {key: price}, anchor_high, anchor_low}
    """
    if not weekly_df or len(weekly_df) < 2:
        return []

    zones = []
    for i in range(1, len(weekly_df)):
        prev  = weekly_df[i - 1]
        curr  = weekly_df[i]

        # Use actual previous-week high/low for historical accuracy
        h, l  = prev['high'], prev['low']
        diff  = h - l or 0.01
        fibs  = {k: _calc_level(k, h, l) for k in FIB_RATIOS}
        zones.append({'start_time': curr['time'], 'fibs': fibs, 'anchor_high': h, 'anchor_low': l})

    # ── Override the LAST (current live) zone with the correct previous-expiry anchor ──
    result = get_fibonacci_levels(weekly_df, symbol)
    if result and result[0]:
        live_fibs, live_high, live_low = result
        if zones:
            zones[-1]['fibs']        = live_fibs
            zones[-1]['anchor_high'] = live_high
            zones[-1]['anchor_low']  = live_low
        else:
            # Edge case: only one week of data
            zones.append({'start_time': weekly_df[-1]['time'], 'fibs': live_fibs, 'anchor_high': live_high, 'anchor_low': live_low})

    return zones


# ─── Background Cache Refresh ─────────────────────────────────────────────────
def _refresh_cache():
    """Runs in a background thread. Fetches all data and stores in DASHBOARD_CACHE.
    Also re-runs every 3 minutes to keep the cache fresh during market hours."""
    global REFRESH_IN_PROGRESS
    while True:
        if REFRESH_IN_PROGRESS:
            time.sleep(5)
            continue
        REFRESH_IN_PROGRESS = True
        success = False
        try:
            print("[Cache] Refreshing dashboard data in background...")
            result = _fetch_all_data()
            with CACHE_LOCK:
                DASHBOARD_CACHE['data'] = result
                DASHBOARD_CACHE['ts']   = time.time()
            print("[Cache] Dashboard data refreshed successfully.")
            success = True
        except Exception as e:
            print("[Cache] Refresh error:", e)
        finally:
            REFRESH_IN_PROGRESS = False
        # Retry in 30s if failed, else refresh every 3 minutes
        time.sleep(180 if success else 30)


# ─── Default Options Tokens ───────────────────────────────────────────────────
DEFAULT_CE = 'SENSEX2670276900CE'
DEFAULT_PE = 'SENSEX2670276900PE'


# ─── Main Dashboard Data (returns from cache instantly — NEVER blocks) ────────
def get_dashboard_data():
    if not smartApi:
        # Start login + cache warmup in background, return loading signal
        threading.Thread(target=initialize_angel_one, daemon=True).start()

    # Return cached data immediately if available
    with CACHE_LOCK:
        cached = DASHBOARD_CACHE.get('data')
        cache_age = time.time() - DASHBOARD_CACHE.get('ts', 0)

    if cached is not None:
        # Trigger background refresh if cache is older than 3 minutes
        if cache_age > 180 and not REFRESH_IN_PROGRESS:
            threading.Thread(target=_refresh_cache, daemon=True).start()
        return cached

    # Cache not ready yet — return loading signal immediately (no blocking!)
    return {'loading': True, 'sensex': {}, 'call': {}, 'put': {}}


def _fetch_all_data():
    """Internal: actually fetches all data. Called only from background thread."""
    if not smartApi:
        initialize_angel_one()

    # Ensure we have CE/PE tokens
    if 'CALL' not in TOKENS or 'PUT' not in TOKENS:
        for item in SCRIP_MASTER_DATA:
            sym = item.get('symbol', '')
            if sym == DEFAULT_CE: TOKENS['CALL'] = item
            if sym == DEFAULT_PE: TOKENS['PUT']  = item
        if not GLOBAL_SWS:
            threading.Thread(target=start_websocket, daemon=True).start()

    def fetch_pair(tok, label):
        exch   = tok.get('exch_seg', 'BFO')
        token  = tok.get('token',    '0')
        data3m = get_historical_data(exch, token, 'THREE_MINUTE', 7) or []
        data1d = get_historical_data(exch, token, 'ONE_DAY',      60) or []
        
        data1w = group_into_weekly(data1d) if data1d else []
        if data3m:
            LIVE_PRICES.setdefault(label, {})['ltp'] = data3m[-1]['close']
        return data3m, data1d, data1w

    # ── SENSEX ──
    sx_tok           = TOKENS.get('SENSEX', {'token': '99919000', 'exch_seg': 'BSE', 'symbol': 'SENSEX'})
    sx_3m, sx_1d, sx_1w = fetch_pair(sx_tok, 'SENSEX')

    # ── CALL ──
    ce_tok           = TOKENS.get('CALL', {'token': '0', 'exch_seg': 'BFO', 'symbol': DEFAULT_CE})
    ce_3m, ce_1d, ce_1w = fetch_pair(ce_tok, 'CALL')

    # ── PUT ──
    pe_tok           = TOKENS.get('PUT',  {'token': '0', 'exch_seg': 'BFO', 'symbol': DEFAULT_PE})
    pe_3m, pe_1d, pe_1w = fetch_pair(pe_tok, 'PUT')

    sx_sym  = sx_tok.get('symbol', 'SENSEX')
    ce_sym  = ce_tok.get('symbol', DEFAULT_CE)
    pe_sym  = pe_tok.get('symbol', DEFAULT_PE)

    sx_fibs = get_fibonacci_danger_zone(sx_1w, sx_sym)
    ce_fibs = get_fibonacci_danger_zone(ce_1w, ce_sym)
    pe_fibs = get_fibonacci_danger_zone(pe_1w, pe_sym)

    # Include current anchor info in the response so the frontend can display it
    sx_anchor = (sx_fibs[-1]['anchor_high'], sx_fibs[-1]['anchor_low']) if sx_fibs else (None, None)
    ce_anchor = (ce_fibs[-1]['anchor_high'], ce_fibs[-1]['anchor_low']) if ce_fibs else (None, None)
    pe_anchor = (pe_fibs[-1]['anchor_high'], pe_fibs[-1]['anchor_low']) if pe_fibs else (None, None)

    return {
        'sensex': {
            'symbol'       : sx_sym,
            'intraday'     : sx_3m,
            'daily'        : sx_1d,
            'weekly'       : sx_1w,
            'fibonacci'    : sx_fibs,
            'anchor_high'  : sx_anchor[0],
            'anchor_low'   : sx_anchor[1],
        },
        'call': {
            'symbol'       : ce_sym,
            'intraday'     : ce_3m,
            'daily'        : ce_1d,
            'weekly'       : ce_1w,
            'fibonacci'    : ce_fibs,
            'anchor_high'  : ce_anchor[0],
            'anchor_low'   : ce_anchor[1],
        },
        'put': {
            'symbol'       : pe_sym,
            'intraday'     : pe_3m,
            'daily'        : pe_1d,
            'weekly'       : pe_1w,
            'fibonacci'    : pe_fibs,
            'anchor_high'  : pe_anchor[0],
            'anchor_low'   : pe_anchor[1],
        }
    }
