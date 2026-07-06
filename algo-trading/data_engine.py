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
    try:
        tempApi = SmartConnect(api_key=API_KEY)
        tempApi.disable_ssl = True
        totp_val = pyotp.TOTP(TOTP_SECRET).now()
        resp = tempApi.generateSession(CLIENT_CODE, PASSWORD, totp_val)
        if resp['status']:
            print("Login Successful!")
            smartApi = tempApi
            feedToken = smartApi.getfeedToken()
            # Wait 5 seconds after login before any API calls — Angel One rate limits fresh sessions hard
            time.sleep(5)
            fetch_tokens()
        else:
            print("Login Failed:", resp)
            smartApi = None
    except Exception as e:
        print("Exception during Angel One Login:", e)
        smartApi = None
    finally:
        # ALWAYS start the cache refresh thread so the UI doesn't hang in "loading" state forever
        threading.Thread(target=_refresh_cache, daemon=True).start()


# ─── Scrip Master ──────────────────────────────────────────────────────────────
def fetch_tokens():
    global TOKENS, SCRIP_MASTER_DATA, DEFAULT_CE, DEFAULT_PE
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
    
    # Auto-resolve SENSEX active defaults
    now_ts = time.time()
    sensex_opts = []
    for item in SCRIP_MASTER_DATA:
        if item.get('name') == 'SENSEX' and item.get('instrumenttype') == 'OPTIDX':
            expiry_str = item.get('expiry', '')
            if expiry_str:
                try:
                    exp_dt = datetime.strptime(expiry_str, "%d%b%Y")
                    if exp_dt.timestamp() + 86400 > now_ts:
                        sensex_opts.append(item)
                except Exception:
                    pass
    if sensex_opts:
        expiries = sorted(list(set(x['expiry'] for x in sensex_opts)), key=lambda d: datetime.strptime(d, "%d%b%Y"))
        if expiries:
            nearest_exp = expiries[0]
            nearest_opts = [x for x in sensex_opts if x['expiry'] == nearest_exp]
            ce_opts = sorted([x for x in nearest_opts if x['symbol'].endswith('CE')], key=lambda x: float(x.get('strike', 0)) / 100.0)
            pe_opts = sorted([x for x in nearest_opts if x['symbol'].endswith('PE')], key=lambda x: float(x.get('strike', 0)) / 100.0)
            if ce_opts and pe_opts:
                spot = 79000
                try:
                    if smartApi:
                        res = smartApi.ltpData("BSE", "SENSEX", "99919000")
                        if res and res.get('status') and res.get('data'):
                            spot = res['data']['ltp']
                except Exception as e:
                    print("Error fetching spot for default tokens:", e)
                
                # Find the closest strike to spot
                def get_strike(opt):
                    return float(opt.get('strike', 0)) / 100.0
                
                ce_opts.sort(key=lambda x: abs(get_strike(x) - spot))
                pe_opts.sort(key=lambda x: abs(get_strike(x) - spot))
                
                # Fetch LTP for the closest 10 strikes to find one > 430
                def find_target_strike(options_list):
                    best_opt = options_list[0]
                    best_diff = float('inf')
                    
                    if smartApi:
                        for opt in options_list[:10]:
                            try:
                                res = smartApi.ltpData(opt['exch_seg'], opt['symbol'], opt['token'])
                                if res and res.get('status') and res.get('data'):
                                    ltp = res['data']['ltp']
                                    if ltp > 430:
                                        diff = ltp - 430
                                        if diff < best_diff:
                                            best_diff = diff
                                            best_opt = opt
                            except Exception:
                                pass
                    return best_opt['symbol']
                
                DEFAULT_CE = find_target_strike(ce_opts)
                DEFAULT_PE = find_target_strike(pe_opts)
                print(f"Auto-resolved default tokens based on spot {spot} and LTP > 430: {DEFAULT_CE}, {DEFAULT_PE}")


def search_contracts(query):
    now_ts = time.time()
    results = []
    count   = 0
    for item in SCRIP_MASTER_DATA:
        if query in item.get('symbol', '') and item.get('exch_seg') == 'BFO' and 'SENSEX' in item.get('symbol', ''):
            expiry_str = item.get('expiry', '')
            if expiry_str:
                try:
                    exp_dt = datetime.strptime(expiry_str, "%d%b%Y")
                    # If it expired yesterday or earlier, skip it
                    if exp_dt.timestamp() + 86400 <= now_ts:
                        continue
                except Exception:
                    pass

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
    print(f"[Tokens] Attempting to swap to CE: {ce_symbol}, PE: {pe_symbol}")
    for item in SCRIP_MASTER_DATA:
        if item.get('symbol') == ce_symbol: ce_token = item['token']
        if item.get('symbol') == pe_symbol: pe_token = item['token']
    if not ce_token or not pe_token:
        print(f"[Tokens] FAILED! CE token found: {ce_token}, PE token found: {pe_token}")
        return False
    print(f"[Tokens] SUCCESS! CE token: {ce_token}, PE token: {pe_token}")
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
    
    with CACHE_LOCK:
        DASHBOARD_CACHE['data'] = None
        DASHBOARD_CACHE['ts'] = 0
    threading.Thread(target=_refresh_cache, daemon=True).start()
    
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
def get_historical_data(exchange, symboltoken, interval, days=None, fromdate=None, todate=None):
    global LAST_API_CALL_TIME
    if fromdate and todate:
        cache_key = f"{symboltoken}_{interval}_{fromdate}_{todate}"
    else:
        cache_key = f"{symboltoken}_{interval}_{days}"
    if cache_key in HISTORICAL_CACHE:
        ts, cached = HISTORICAL_CACHE[cache_key]
        if time.time() - ts < 60:
            return cached
            
    for attempt in range(3):
        try:
            if fromdate and todate:
                f_date = fromdate
                t_date = todate
            else:
                now = datetime.now(IST)
                f_date = (now - timedelta(days=days)).strftime("%Y-%m-%d %H:%M")
                t_date = now.strftime("%Y-%m-%d %H:%M")
            params = {
                "exchange"   : exchange,
                "symboltoken": symboltoken,
                "interval"   : interval,
                "fromdate"   : f_date,
                "todate"     : t_date
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


from strategy.fibonacci_engine import group_into_weekly, get_fibonacci_danger_zone


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
DEFAULT_CE = None
DEFAULT_PE = None


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

    dynamic_fibs = dict(MANUAL_FIBS)
    
    def add_true_anchors(sym, opt_1w, opt_1d, s_1w, s_1d, is_put):
        if sym in dynamic_fibs: return
        if not opt_1w or not opt_1d or not s_1w or not s_1d: return
        
        from strategy.fibonacci_engine import _auto_weekly_high_low
        # Get the EXACT week window that the Fibonacci engine is using for SENSEX
        s_w_high, s_w_low, week_start, week_end = _auto_weekly_high_low(s_1w)
        
        if week_start is None or week_end is None:
            return
            
        # Compare by calendar dates instead of timestamps to bypass all UTC/IST offset issues
        s_days = [d for d in s_1d if week_start <= datetime.fromtimestamp(d['time'], tz=IST).date() <= week_end]
        o_days = [d for d in opt_1d if week_start <= datetime.fromtimestamp(d['time'], tz=IST).date() <= week_end]
        
        if not s_days or not o_days: return
        
        s_high_day = max(s_days, key=lambda d: d['high'])
        s_low_day = min(s_days, key=lambda d: d['low'])
        
        s_high_date = datetime.fromtimestamp(s_high_day['time'], tz=IST).date()
        s_low_date = datetime.fromtimestamp(s_low_day['time'], tz=IST).date()
        
        o_day_for_s_high = next((d for d in o_days if datetime.fromtimestamp(d['time'], tz=IST).date() == s_high_date), None)
        o_day_for_s_low = next((d for d in o_days if datetime.fromtimestamp(d['time'], tz=IST).date() == s_low_date), None)
        
        if o_day_for_s_high and o_day_for_s_low:
            # Use the CLOSING price on the SENSEX pivot day as the anchor.
            # Close is the cleanest price — free from 9:15 AM illiquidity spikes
            # and matches exactly what TradingView manual Fibonacci anchors show.
            if is_put:
                # PE High = PE HIGH WICK on day SENSEX was at its weekly LOW (PE most expensive)
                # PE Low  = PE LOW WICK on day SENSEX was at its weekly HIGH (PE cheapest)
                dynamic_fibs[sym] = {
                    'high': o_day_for_s_low['high'],
                    'low':  o_day_for_s_high['low']
                }
            else:
                # CE High = CE HIGH WICK on day SENSEX was at its weekly HIGH
                # CE Low  = CE LOW WICK on day SENSEX was at its weekly LOW
                dynamic_fibs[sym] = {
                    'high': o_day_for_s_high['high'],
                    'low':  o_day_for_s_low['low']
                }

    # add_true_anchors(ce_sym, ce_1w, ce_1d, sx_1w, sx_1d, False)
    # add_true_anchors(pe_sym, pe_1w, pe_1d, sx_1w, sx_1d, True)

    sx_fibs = get_fibonacci_danger_zone(sx_1w, sx_sym, dynamic_fibs)
    ce_fibs = get_fibonacci_danger_zone(ce_1w, ce_sym, dynamic_fibs)
    pe_fibs = get_fibonacci_danger_zone(pe_1w, pe_sym, dynamic_fibs)

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
