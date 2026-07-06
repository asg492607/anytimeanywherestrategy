import os
import sqlite3
import time
import logging
from datetime import datetime, timezone, timedelta

# Import our strategy modules
from strategy.reference_box_engine import process_latest_candles
from strategy.buy_signal_engine import monitor_reference_boxes
from strategy.confirmation_engine import monitor_buy_signals
from strategy.execution_engine import monitor_confirmations
from strategy.stop_loss_engine import monitor_running_trades
from strategy.target_engine import monitor_running_trades as monitor_targets_logic
from strategy.strategy_monitor import monitor_strategy, calculate_live_statistics
from strategy.fibonacci_engine import get_fibonacci_danger_zone, group_into_weekly

import simulate_db
import simulate_execution
import data_engine
from data_engine import get_historical_data

logger = logging.getLogger('simulate_engine')

IST = timezone(timedelta(hours=5, minutes=30))

# Global in-memory state of active simulation sessions
SIMULATION_SESSIONS = {}

# Tables that must NEVER be cleared during simulation reset.
# 'users' is cloned from users.db and must be preserved.
# 'sqlite_sequence' is an internal SQLite autoincrement counter — clearing it
# would break auto-generated primary keys across sessions.
_TABLES_TO_SKIP = {'users', 'sqlite_sequence'}


def reset_simulation_db(user_id):
    """
    Clears all simulation-generated data for this user in simulation.db.
    Preserves users/authentication information and internal SQLite bookkeeping.

    Dynamically inspects the schema of every table using PRAGMA table_info()
    so this function stays correct even if new tables are added later.

    Reset strategy per table:
      • Has user_id column  → DELETE WHERE user_id = ?
      • No user_id column   → DELETE ALL (pure simulation data, not user-scoped)
      • In _TABLES_TO_SKIP  → skip entirely (reference / internal tables)
    """
    simulate_db.init_db()
    conn = simulate_db.get_db_connection()
    try:
        cursor = conn.cursor()

        # Discover every table in the database dynamically
        table_rows = cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()

        for (table_name,) in table_rows:
            if table_name in _TABLES_TO_SKIP:
                continue

            # Introspect columns for this table
            columns = cursor.execute(
                f"PRAGMA table_info({table_name})"
            ).fetchall()
            col_names = {row[1] for row in columns}   # row[1] = column name

            if 'user_id' in col_names:
                cursor.execute(
                    f"DELETE FROM {table_name} WHERE user_id = ?",
                    (user_id,)
                )
            else:
                cursor.execute(f"DELETE FROM {table_name}")

        conn.commit()
    finally:
        conn.close()


def resolve_token_robust(symbol, option_type, date_str):
    if len(symbol) < 14:
        return None, None
    strike_str = symbol[11:-2]
    try:
        strike_val = float(strike_str)
    except ValueError:
        return None, None
    
    # Parse date_str to match expiry format in scrip master (e.g. 06JUL2026)
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return None, None
        
    expiry_match = dt.strftime("%d%b%Y").upper()
    
    for item in data_engine.SCRIP_MASTER_DATA:
        if item.get('name') == 'SENSEX' and item.get('exch_seg') == 'BFO':
            symbol_str = item.get('symbol', '')
            if not symbol_str.endswith(option_type):
                continue
                
            # Match expiry date
            item_expiry = item.get('expiry', '')
            if item_expiry.upper() != expiry_match:
                continue
                
            # Match strike price (either raw or divided by 100)
            item_strike = float(item.get('strike', 0))
            if item_strike / 100.0 == strike_val or item_strike == strike_val:
                return item['token'], item['symbol']
                
    return None, None


def start_simulation(user_id, date_str, ce_symbol=None, pe_symbol=None):
    """
    Starts a new simulation session for the user.
    Loads real 3-minute and daily historical candles from Angel One.
    """
    reset_simulation_db(user_id)
    
    # Ensure Angel One API is authenticated
    if not data_engine.smartApi:
        data_engine.initialize_angel_one()
        # Wait up to 15 seconds for dynamic login to complete
        for _ in range(30):
            if data_engine.smartApi:
                break
            time.sleep(0.5)
            
    if not data_engine.smartApi:
        return {'error': "Angel One API session is not authenticated. Please check your credentials or network."}
    
    # Ensure Scrip Master is loaded
    if not data_engine.SCRIP_MASTER_DATA:
        data_engine.fetch_tokens()

    # Dynamically resolve default symbols if not provided
    if not ce_symbol or not pe_symbol:
        sim_dt = datetime.strptime(date_str, "%Y-%m-%d")
        all_sensex_opts = []
        for item in data_engine.SCRIP_MASTER_DATA:
            if item.get('name') == 'SENSEX' and item.get('exch_seg') == 'BFO':
                all_sensex_opts.append(item)
                
        future_opts = []
        for opt in all_sensex_opts:
            exp_str = opt.get('expiry', '')
            if exp_str:
                try:
                    exp_dt = datetime.strptime(exp_str, "%d%b%Y")
                    if exp_dt.date() >= sim_dt.date():
                        future_opts.append((exp_dt, opt))
                except Exception:
                    pass
                    
        if future_opts:
            future_opts.sort(key=lambda x: x[0])
            nearest_exp_dt = future_opts[0][0]
            nearest_opts = [x[1] for x in future_opts if x[0] == nearest_exp_dt]
            
            ce_opts = [x for x in nearest_opts if x['symbol'].endswith('CE')]
            pe_opts = [x for x in nearest_opts if x['symbol'].endswith('PE')]
            
            # Sort by strike closest to 79000
            def strike_diff(opt):
                try:
                    strike_val = float(opt.get('strike', 0))
                    if strike_val > 100000:
                        strike_val /= 100.0
                    return abs(strike_val - 79000)
                except Exception:
                    return 99999
                    
            ce_opts.sort(key=strike_diff)
            pe_opts.sort(key=strike_diff)
            
            if ce_opts and not ce_symbol:
                ce_symbol = ce_opts[0]['symbol']
            if pe_opts and not pe_symbol:
                pe_symbol = pe_opts[0]['symbol']

    # Fallback defaults if lookup yielded nothing
    if not ce_symbol:
        ce_symbol = "SENSEX2670979000CE"
    if not pe_symbol:
        pe_symbol = "SENSEX2670979000PE"

    # Debug: Collect unique expiries and symbols for BFO SENSEX in 2026
    import json
    unique_expiries = {}
    for item in data_engine.SCRIP_MASTER_DATA:
        if item.get('name') == 'SENSEX' and item.get('exch_seg') == 'BFO':
            exp = item.get('expiry', '')
            if '2026' in exp:
                if exp not in unique_expiries:
                    unique_expiries[exp] = []
                if len(unique_expiries[exp]) < 5:
                    unique_expiries[exp].append(item.get('symbol'))
                    
    debug_info = {
        "received_ce": ce_symbol,
        "received_pe": pe_symbol,
        "scrip_master_len": len(data_engine.SCRIP_MASTER_DATA),
        "unique_expiries_2026": unique_expiries
    }
    with open("scratch/debug_scrip_master.json", "w") as f:
        json.dump(debug_info, f, indent=2)

    # Resolve Option Tokens
    ce_token, ce_master_symbol = resolve_token_robust(ce_symbol, 'CE', date_str)
    pe_token, pe_master_symbol = resolve_token_robust(pe_symbol, 'PE', date_str)
    
    # Fallback to exact match lookup if robust lookup returned nothing
    if not ce_token or not pe_token:
        for item in data_engine.SCRIP_MASTER_DATA:
            if item.get('symbol') == ce_symbol:
                ce_token = item['token']
                ce_master_symbol = item['symbol']
            if item.get('symbol') == pe_symbol:
                pe_token = item['token']
                pe_master_symbol = item['symbol']

    if not ce_token or not pe_token:
        return {'error': f"Tokens not found in scrip master for CE symbol '{ce_symbol}' or PE symbol '{pe_symbol}'"}
        
    print(f"Selected CE Symbol: {ce_symbol}")
    print(f"Resolved Token: {ce_token}")
    print(f"Exchange: BFO")
    print(f"Matched Scrip Master Symbol: {ce_master_symbol}")
    print(f"Selected PE Symbol: {pe_symbol}")
    print(f"Resolved Token: {pe_token}")
    print(f"Exchange: BFO")
    print(f"Matched Scrip Master Symbol: {pe_master_symbol}")


        
    # 1. Fetch real historical 3-minute candles from Angel One (including ~20 prior trading days context)
    dt_obj = datetime.strptime(date_str, "%Y-%m-%d")
    fromdate_3m_obj = dt_obj - timedelta(days=30)
    fromdate_3m = fromdate_3m_obj.strftime("%Y-%m-%d 09:15")
    todate_3m = f"{date_str} 15:45"
    
    raw_sensex = get_historical_data('BSE', '99919000', 'THREE_MINUTE', fromdate=fromdate_3m, todate=todate_3m)
    raw_ce = get_historical_data('BFO', ce_token, 'THREE_MINUTE', fromdate=fromdate_3m, todate=todate_3m)
    raw_pe = get_historical_data('BFO', pe_token, 'THREE_MINUTE', fromdate=fromdate_3m, todate=todate_3m)
    
    if not raw_sensex or not raw_ce or not raw_pe:
        return {
            'error': f"Could not fetch historical 3-minute candles from Angel One for SENSEX, CE, or PE on {date_str}. "
                     f"Please verify Angel One API is logged in, and that {date_str} is a valid trading day."
        }
        
    # Filter function for market hours (09:15 to 15:30 IST)
    def filter_market_hours(candles):
        filtered = []
        for c in candles:
            dt = datetime.fromtimestamp(c['time'], tz=IST)
            if (dt.hour > 9 or (dt.hour == 9 and dt.minute >= 15)) and (dt.hour < 15 or (dt.hour == 15 and dt.minute <= 30)):
                filtered.append(c)
        return filtered

    filtered_sensex = filter_market_hours(raw_sensex)
    filtered_ce = filter_market_hours(raw_ce)
    filtered_pe = filter_market_hours(raw_pe)
    
    # Align by common timestamps to ensure perfect synchronization
    common_times = set(c['time'] for c in filtered_sensex) & set(c['time'] for c in filtered_ce) & set(c['time'] for c in filtered_pe)
    sorted_common_times = sorted(list(common_times))
    
    if not sorted_common_times:
        return {'error': f"No aligned trading candles found for SENSEX, CE, and PE on {date_str} during market hours."}
        
    sensex_map = {c['time']: c for c in filtered_sensex}
    ce_map = {c['time']: c for c in filtered_ce}
    pe_map = {c['time']: c for c in filtered_pe}
    
    sim_day_start_dt = datetime.strptime(f"{date_str} 00:00", "%Y-%m-%d %H:%M").replace(tzinfo=IST)
    sim_day_start_ts = int(sim_day_start_dt.timestamp())
    
    historical_times = [t for t in sorted_common_times if t < sim_day_start_ts]
    sim_times = [t for t in sorted_common_times if t >= sim_day_start_ts]
    
    historical_candles = {
        'SPOT': [sensex_map[t] for t in historical_times],
        'CALL': [ce_map[t] for t in historical_times],
        'PUT': [pe_map[t] for t in historical_times]
    }
    
    candles = {
        'SPOT': [sensex_map[t] for t in sim_times],
        'CALL': [ce_map[t] for t in sim_times],
        'PUT': [pe_map[t] for t in sim_times]
    }
    
    total_candles = len(sim_times)
    
    # 2. Fetch daily candles to calculate accurate weekly Fibonacci levels
    dt_obj = datetime.strptime(date_str, "%Y-%m-%d")
    fromdate_1d = (dt_obj - timedelta(days=60)).strftime("%Y-%m-%d 09:15")
    todate_1d = dt_obj.strftime("%Y-%m-%d 15:30")
    
    sx_1d = get_historical_data('BSE', '99919000', 'ONE_DAY', fromdate=fromdate_1d, todate=todate_1d) or []
    ce_1d = get_historical_data('BFO', ce_token, 'ONE_DAY', fromdate=fromdate_1d, todate=todate_1d) or []
    pe_1d = get_historical_data('BFO', pe_token, 'ONE_DAY', fromdate=fromdate_1d, todate=todate_1d) or []
    
    sx_1w = group_into_weekly(sx_1d) if sx_1d else []
    ce_1w = group_into_weekly(ce_1d) if ce_1d else []
    pe_1w = group_into_weekly(pe_1d) if pe_1d else []
    
    # Fallback to current intraday if daily/weekly is empty
    weekly_fibs_dict = {
        'sensex': {
            'symbol': 'SENSEX',
            'weekly': sx_1w if sx_1w else [{'time': sorted_common_times[0], 'open': candles['SPOT'][0]['open'], 'high': max(c['high'] for c in candles['SPOT']), 'low': min(c['low'] for c in candles['SPOT']), 'close': candles['SPOT'][-1]['close']}]
        },
        'call': {
            'symbol': ce_symbol,
            'weekly': ce_1w if ce_1w else [{'time': sorted_common_times[0], 'open': candles['CALL'][0]['open'], 'high': max(c['high'] for c in candles['CALL']), 'low': min(c['low'] for c in candles['CALL']), 'close': candles['CALL'][-1]['close']}]
        },
        'put': {
            'symbol': pe_symbol,
            'weekly': pe_1w if pe_1w else [{'time': sorted_common_times[0], 'open': candles['PUT'][0]['open'], 'high': max(c['high'] for c in candles['PUT']), 'low': min(c['low'] for c in candles['PUT']), 'close': candles['PUT'][-1]['close']}]
        }
    }
    
    # 3. Calculate weekly fib levels
    from strategy.fibonacci_engine import get_fibonacci_levels
    from data_engine import MANUAL_FIBS
    dynamic_fibs = dict(MANUAL_FIBS)
    
    sx_res = get_fibonacci_levels(weekly_fibs_dict['sensex']['weekly'], 'SENSEX', dynamic_fibs)
    ce_res = get_fibonacci_levels(weekly_fibs_dict['call']['weekly'], ce_symbol, dynamic_fibs)
    pe_res = get_fibonacci_levels(weekly_fibs_dict['put']['weekly'], pe_symbol, dynamic_fibs)
    
    weekly_fibs = {
        'SENSEX': sx_res[0] if sx_res else {},
        'CALL': ce_res[0] if ce_res else {},
        'PUT': pe_res[0] if pe_res else {}
    }
    
    SIMULATION_SESSIONS[user_id] = {
        'status': 'RUNNING',
        'date': date_str,
        'ce_symbol': ce_symbol,
        'pe_symbol': pe_symbol,
        'current_index': 0,
        'total_candles': total_candles,
        'historical_candles': historical_candles,
        'candles': candles,
        'weekly_fibs_dict': weekly_fibs_dict,
        'weekly_fibs': weekly_fibs
    }
    
    # Initialize the strategy session stats
    session_id = monitor_strategy(user_id, db_adapter=simulate_db)
    logger.info(f"Simulation started for user {user_id} on {date_str}. Strategy session ID: {session_id}")
    
    return {
        'status': 'RUNNING',
        'date': date_str,
        'current_index': 0,
        'total_candles': total_candles
    }

def stop_simulation(user_id):
    """Stops the active simulation session."""
    if user_id in SIMULATION_SESSIONS:
        del SIMULATION_SESSIONS[user_id]
    return {'status': 'IDLE'}


def tick_simulation(user_id):
    """
    Advances the simulation by 1 candle (3 minutes).
    Runs the strategy pipeline against the sliced candles.
    """
    session = SIMULATION_SESSIONS.get(user_id)
    if not session:
        return {'error': 'No active simulation session'}
        
    if session['status'] == 'COMPLETED':
        return {
            'status': 'COMPLETED',
            'current_index': session['current_index'],
            'total_candles': session['total_candles']
        }
        
    current_idx = session['current_index']
    
    # Slice candles up to current_idx
    sliced_candles = {
        'SPOT': session['candles']['SPOT'][:current_idx + 1],
        'CALL': session['candles']['CALL'][:current_idx + 1],
        'PUT': session['candles']['PUT'][:current_idx + 1]
    }
    
    # Pass to strategy execution pipeline using DI
    # SPOT
    process_latest_candles(
        user_id=user_id,
        chart_type='SPOT',
        symbol='SENSEX',
        timeframe='3m',
        candles=sliced_candles['SPOT'],
        levels=session['weekly_fibs']['SENSEX'],
        db_adapter=simulate_db
    )
    # CALL
    process_latest_candles(
        user_id=user_id,
        chart_type='CALL',
        symbol=session['ce_symbol'],
        timeframe='3m',
        candles=sliced_candles['CALL'],
        levels=session['weekly_fibs']['CALL'],
        db_adapter=simulate_db
    )
    # PUT
    process_latest_candles(
        user_id=user_id,
        chart_type='PUT',
        symbol=session['pe_symbol'],
        timeframe='3m',
        candles=sliced_candles['PUT'],
        levels=session['weekly_fibs']['PUT'],
        db_adapter=simulate_db
    )
    
    # Buy Signal monitor
    monitor_reference_boxes(
        user_id=user_id,
        candles_dict=sliced_candles,
        db_adapter=simulate_db,
        execute_adapter=simulate_execution.mock_execute
    )
    
    # Confirmation / Execution
    monitor_confirmations(
        user_id=user_id,
        db_adapter=simulate_db,
        execute_adapter=simulate_execution.mock_execute
    )
    
    # Stop Loss
    monitor_running_trades(
        user_id=user_id,
        candles_dict=sliced_candles,
        db_adapter=simulate_db,
        execute_adapter=simulate_execution.mock_execute
    )
    
    # Target
    monitor_targets_logic(
        user_id=user_id,
        candles_dict=sliced_candles,
        db_data=session['weekly_fibs_dict'],
        db_adapter=simulate_db,
        execute_adapter=simulate_execution.mock_execute
    )
    
    # Strategy session stats monitor
    monitor_strategy(user_id, db_adapter=simulate_db)
    
    # Increment index
    session['current_index'] += 1
    if session['current_index'] >= session['total_candles']:
        session['status'] = 'COMPLETED'
        
    return {
        'status': session['status'],
        'current_index': session['current_index'],
        'total_candles': session['total_candles']
    }

def get_simulation_data(user_id):
    """
    Returns dashboard data for the active simulation session.
    """
    session = SIMULATION_SESSIONS.get(user_id)
    if not session:
        return {'status': 'IDLE'}
        
    current_idx = session['current_index']
    
    # Slice candles
    sliced_candles = {
        'SPOT': session.get('historical_candles', {}).get('SPOT', []) + session['candles']['SPOT'][:current_idx + 1],
        'CALL': session.get('historical_candles', {}).get('CALL', []) + session['candles']['CALL'][:current_idx + 1],
        'PUT': session.get('historical_candles', {}).get('PUT', []) + session['candles']['PUT'][:current_idx + 1]
    }
    
    # Read trades and reference boxes from simulation.db
    conn = simulate_db.get_db_connection()
    try:
        # Get trades
        trades_rows = conn.execute("SELECT * FROM trades WHERE user_id = ? ORDER BY id DESC", (user_id,)).fetchall()
        trades = [dict(r) for r in trades_rows]
        
        # Get active boxes
        boxes_rows = conn.execute("SELECT * FROM reference_boxes WHERE user_id = ? ORDER BY id DESC", (user_id,)).fetchall()
        boxes = [dict(r) for r in boxes_rows]
        
        # Get executions
        execs_rows = conn.execute("SELECT * FROM trade_executions WHERE user_id = ? ORDER BY id DESC", (user_id,)).fetchall()
        executions = [dict(r) for r in execs_rows]
        
        # Get strategy logs
        logs_rows = conn.execute("SELECT * FROM trade_logs ORDER BY id DESC LIMIT 50").fetchall()
        logs = [dict(r) for r in logs_rows]
        
        # Get buy signals
        sig_rows = conn.execute("SELECT * FROM buy_signals WHERE user_id = ? ORDER BY id DESC", (user_id,)).fetchall()
        buy_signals = [dict(r) for r in sig_rows]

        # Get trade confirmations
        conf_rows = conn.execute("SELECT * FROM trade_confirmations WHERE user_id = ? ORDER BY id DESC", (user_id,)).fetchall()
        trade_confirmations = [dict(r) for r in conf_rows]

        # Get active stop loss events (MONITORING/TRIGGERED)
        sl_rows = conn.execute(
            "SELECT * FROM stop_loss_events WHERE user_id = ? AND exit_status IN ('MONITORING','TRIGGERED') ORDER BY id DESC",
            (user_id,)
        ).fetchall()
        stop_loss_events = [dict(r) for r in sl_rows]

        # Get completed stop loss events
        sl_hist_rows = conn.execute(
            "SELECT * FROM stop_loss_events WHERE user_id = ? AND exit_status NOT IN ('MONITORING','TRIGGERED') ORDER BY id DESC",
            (user_id,)
        ).fetchall()
        stop_loss_history = [dict(r) for r in sl_hist_rows]

        # Get active target events (MONITORING/TRIGGERED)
        tgt_rows = conn.execute(
            "SELECT * FROM target_exit_events WHERE user_id = ? AND exit_status IN ('MONITORING','TRIGGERED') ORDER BY id DESC",
            (user_id,)
        ).fetchall()
        target_exit_events = [dict(r) for r in tgt_rows]

        # Get completed target events
        tgt_hist_rows = conn.execute(
            "SELECT * FROM target_exit_events WHERE user_id = ? AND exit_status NOT IN ('MONITORING','TRIGGERED') ORDER BY id DESC",
            (user_id,)
        ).fetchall()
        target_exit_history = [dict(r) for r in tgt_hist_rows]
    finally:
        conn.close()
        
    # Calculate statistics
    stats = calculate_live_statistics(user_id, db_adapter=simulate_db)
    
    # Compile current spot and option LTPS
    ltps = {
        'SENSEX': sliced_candles['SPOT'][-1]['close'] if sliced_candles['SPOT'] else 0.0,
        'CALL': sliced_candles['CALL'][-1]['close'] if sliced_candles['CALL'] else 0.0,
        'PUT': sliced_candles['PUT'][-1]['close'] if sliced_candles['PUT'] else 0.0
    }
    
    # Compute pnl_stats matching /api/trades/pnl format for dashboard compatibility
    running_trades_list = [t for t in trades if t.get('status') == 'RUNNING']
    closed_trades_list  = [t for t in trades if t.get('status') != 'RUNNING']
    
    for t in running_trades_list:
        if t.get('call_symbol'):
            current_price = ltps['CALL']
        elif t.get('put_symbol'):
            current_price = ltps['PUT']
        else:
            current_price = t.get('entry_price', 0.0) or 0.0
        t['current_price'] = float(current_price)
        direction = t.get('direction', 'BUY')
        entry = float(t.get('entry_price', 0) or 0)
        qty = int(t.get('quantity', 0) or 0)
        if direction == 'BUY':
            t['pnl'] = (current_price - entry) * qty
        else:
            t['pnl'] = (entry - current_price) * qty
    
    running_pnl = sum(t.get('pnl', 0) for t in running_trades_list)
    closed_pnl  = sum(float(t.get('pnl', 0) or 0) for t in closed_trades_list)
    total_pnl = running_pnl + closed_pnl
    n_closed = len(closed_trades_list)
    winning_closed = sum(1 for t in closed_trades_list if float(t.get('pnl', 0) or 0) > 0)
    
    pnl_stats = {
        'running_pnl':    running_pnl,
        'closed_pnl':     closed_pnl,
        'today_pnl':      total_pnl,
        'total_pnl':      total_pnl,
        'total_trades':   len(trades),
        'winning_trades': winning_closed,
        'losing_trades':  n_closed - winning_closed,
        'win_rate':       (winning_closed / n_closed * 100.0) if n_closed > 0 else 0.0
    }
    
    # Format weekly fibonacci levels for charts
    from data_engine import MANUAL_FIBS
    dynamic_fibs = dict(MANUAL_FIBS)
    
    sx_fibs = get_fibonacci_danger_zone(session['weekly_fibs_dict']['sensex']['weekly'], 'SENSEX', dynamic_fibs)
    ce_fibs = get_fibonacci_danger_zone(session['weekly_fibs_dict']['call']['weekly'], session['ce_symbol'], dynamic_fibs)
    pe_fibs = get_fibonacci_danger_zone(session['weekly_fibs_dict']['put']['weekly'], session['pe_symbol'], dynamic_fibs)
    
    sx_anchor = (sx_fibs[-1]['anchor_high'], sx_fibs[-1]['anchor_low']) if sx_fibs else (None, None)
    ce_anchor = (ce_fibs[-1]['anchor_high'], ce_fibs[-1]['anchor_low']) if ce_fibs else (None, None)
    pe_anchor = (pe_fibs[-1]['anchor_high'], pe_fibs[-1]['anchor_low']) if pe_fibs else (None, None)

    return {
        'status': session['status'],
        'date': session['date'],
        'current_index': current_idx,
        'total_candles': session['total_candles'],
        'candles': sliced_candles,
        'trades': trades,
        'running_trades': running_trades_list,
        'reference_boxes': boxes,
        'executions': executions,
        'logs': logs,
        'buy_signals': buy_signals,
        'trade_confirmations': trade_confirmations,
        'stop_loss_events': stop_loss_events,
        'stop_loss_history': stop_loss_history,
        'target_exit_events': target_exit_events,
        'target_exit_history': target_exit_history,
        'statistics': stats,
        'pnl_stats': pnl_stats,
        'ltps': ltps,
        'sensex': {
            'symbol': 'SENSEX',
            'intraday': sliced_candles['SPOT'],
            'daily': session['weekly_fibs_dict']['sensex']['weekly'],
            'weekly': session['weekly_fibs_dict']['sensex']['weekly'],
            'fibonacci': sx_fibs,
            'anchor_high': sx_anchor[0],
            'anchor_low': sx_anchor[1],
        },
        'call': {
            'symbol': session['ce_symbol'],
            'intraday': sliced_candles['CALL'],
            'daily': session['weekly_fibs_dict']['call']['weekly'],
            'weekly': session['weekly_fibs_dict']['call']['weekly'],
            'fibonacci': ce_fibs,
            'anchor_high': ce_anchor[0],
            'anchor_low': ce_anchor[1],
        },
        'put': {
            'symbol': session['pe_symbol'],
            'intraday': sliced_candles['PUT'],
            'daily': session['weekly_fibs_dict']['put']['weekly'],
            'weekly': session['weekly_fibs_dict']['put']['weekly'],
            'fibonacci': pe_fibs,
            'anchor_high': pe_anchor[0],
            'anchor_low': pe_anchor[1],
        }
    }
