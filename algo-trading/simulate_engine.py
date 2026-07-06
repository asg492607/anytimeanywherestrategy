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
from strategy.fibonacci_engine import get_fibonacci_danger_zone

import simulate_db
import simulate_execution

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

def generate_deterministic_candles(date_str, ce_symbol, pe_symbol):
    """
    Generates 125 3-minute candles (09:15 to 15:30 IST) for a specific date.
    Specifically pre-programmed to form a box, break out, trigger BUY_CE,
    and then hit target or stop loss, to ensure strategy tests succeed.
    """
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        dt = datetime.now()

    base_time = datetime(dt.year, dt.month, dt.day, 9, 15, 0, tzinfo=IST)
    
    candles = {
        'SPOT': [],
        'CALL': [],
        'PUT': []
    }
    
    # Pre-programmed patterns:
    # 0 to 5: range-bound (forms reference box crossing 1.14 level)
    # Monitored levels:
    # SENSEX Weekly anchor: Low=77000, High=79000. Diff=2000.
    # Level 1.14 = 77000 + 2000 * 1.14 = 79280.
    # We will make candle 5 cross 79280.
    
    for i in range(125):
        candle_time = int((base_time + timedelta(minutes=3*i)).timestamp())
        
        # 1. SPOT (SENSEX)
        if i < 5:
            # Range bound below 79280
            c_open = 79250 + (i * 2)
            c_close = 79260 - (i * 2)
            c_high = max(c_open, c_close) + 5
            c_low = min(c_open, c_close) - 5
        elif i == 5:
            # Crosses 79280! Open 79270, Close 79295 (Crosses 79280)
            c_open = 79270
            c_close = 79295
            c_high = 79310 # Upper boundary of reference box
            c_low = 79260  # Lower boundary of reference box
        elif i < 10:
            # Stays inside box boundaries (79260 to 79310)
            c_open = 79280
            c_close = 79285
            c_high = 79290
            c_low = 79270
        elif i == 10:
            # Breakout upwards! High 79350, Close 79340 (Breaks 79310)
            c_open = 79285
            c_close = 79340
            c_high = 79350
            c_low = 79280
        elif i < 25:
            # Continues upward trend to hit Target
            c_open = 79340 + (i - 10) * 10
            c_close = 79340 + (i - 10) * 10 + 8
            c_high = c_close + 5
            c_low = c_open - 5
        else:
            # Sideways after trade closes
            c_open = 79490
            c_close = 79485
            c_high = 79500
            c_low = 79480

        candles['SPOT'].append({
            'time': candle_time, 'open': c_open, 'high': c_high, 'low': c_low, 'close': c_close, 'volume': 1000
        })

        # 2. CALL (CE) - Monitored Weekly anchor: Low=350, High=450. Diff=100.
        # Level 1.14 = 350 + 100 * 1.14 = 464.
        # We make CE follow SENSEX.
        if i < 5:
            ce_open = 460 + i
            ce_close = 462 - i
            ce_high = max(ce_open, ce_close) + 1
            ce_low = min(ce_open, ce_close) - 1
        elif i == 5:
            # Crosses 464!
            ce_open = 462
            ce_close = 466
            ce_high = 470 # Box Upper
            ce_low = 460  # Box Lower
        elif i < 10:
            ce_open = 465
            ce_close = 467
            ce_high = 468
            ce_low = 464
        elif i == 10:
            # Breakout CE! High 475, Close 474
            ce_open = 467
            ce_close = 474
            ce_high = 475
            ce_low = 466
        elif i < 25:
            # CE price rises to 520 (Target is at 350 + 100 * 1.39 = 489, so this will trigger target hit!)
            ce_open = 474 + (i - 10) * 4
            ce_close = 474 + (i - 10) * 4 + 3
            ce_high = ce_close + 2
            ce_low = ce_open - 2
        else:
            ce_open = 530
            ce_close = 528
            ce_high = 532
            ce_low = 526

        candles['CALL'].append({
            'time': candle_time, 'open': ce_open, 'high': ce_high, 'low': ce_low, 'close': ce_close, 'volume': 500
        })

        # 3. PUT (PE) - Inverse of SENSEX/CE
        if i < 5:
            pe_open = 460 - i
            pe_close = 458 + i
            pe_high = max(pe_open, pe_close) + 1
            pe_low = min(pe_open, pe_close) - 1
        elif i == 5:
            # Crosses 464 downwards
            pe_open = 462
            pe_close = 458
            pe_high = 465
            pe_low = 455
        elif i < 10:
            pe_open = 457
            pe_close = 456
            pe_high = 459
            pe_low = 454
        elif i == 10:
            # Drops below low boundary (rejection)
            pe_open = 456
            pe_close = 448
            pe_high = 457
            pe_low = 446
        elif i < 25:
            pe_open = 448 - (i - 10) * 3
            pe_close = 448 - (i - 10) * 3 - 2
            pe_high = pe_open + 2
            pe_low = pe_close - 2
        else:
            pe_open = 400
            pe_close = 402
            pe_high = 405
            pe_low = 398

        candles['PUT'].append({
            'time': candle_time, 'open': pe_open, 'high': pe_high, 'low': pe_low, 'close': pe_close, 'volume': 500
        })

    return candles

def start_simulation(user_id, date_str, ce_symbol=None, pe_symbol=None):
    """
    Starts a new simulation session for the user.
    Loads/generates all 125 candles for the selected day.
    """
    if not ce_symbol:
        ce_symbol = "SENSEX2670679000CE"
    if not pe_symbol:
        pe_symbol = "SENSEX2670679000PE"
        
    reset_simulation_db(user_id)
    
    # 1. Generate SENSEX, CALL, PUT candles
    candles = generate_deterministic_candles(date_str, ce_symbol, pe_symbol)
    
    # 2. Setup mock weekly dashboard data to resolve Fib targets
    weekly_fibs_dict = {
        'sensex': {
            'symbol': 'SENSEX',
            'weekly': [{'time': 1720000000, 'open': 78000, 'high': 79000, 'low': 77000, 'close': 78500}]
        },
        'call': {
            'symbol': ce_symbol,
            'weekly': [{'time': 1720000000, 'open': 380, 'high': 450, 'low': 350, 'close': 400}]
        },
        'put': {
            'symbol': pe_symbol,
            'weekly': [{'time': 1720000000, 'open': 380, 'high': 450, 'low': 350, 'close': 400}]
        }
    }
    
    # 3. Calculate weekly fib levels
    from strategy.fibonacci_engine import get_fibonacci_levels
    sx_res = get_fibonacci_levels(weekly_fibs_dict['sensex']['weekly'])
    ce_res = get_fibonacci_levels(weekly_fibs_dict['call']['weekly'])
    pe_res = get_fibonacci_levels(weekly_fibs_dict['put']['weekly'])
    
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
        'total_candles': 125,
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
        'total_candles': 125
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
        'SPOT': session['candles']['SPOT'][:current_idx + 1],
        'CALL': session['candles']['CALL'][:current_idx + 1],
        'PUT': session['candles']['PUT'][:current_idx + 1]
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
    sx_fibs = get_fibonacci_danger_zone(session['weekly_fibs_dict']['sensex']['weekly'], 'SENSEX')
    ce_fibs = get_fibonacci_danger_zone(session['weekly_fibs_dict']['call']['weekly'], session['ce_symbol'])
    pe_fibs = get_fibonacci_danger_zone(session['weekly_fibs_dict']['put']['weekly'], session['pe_symbol'])
    
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
