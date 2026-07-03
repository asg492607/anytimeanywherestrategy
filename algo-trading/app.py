from flask import Flask, render_template, jsonify, send_from_directory, request, redirect, url_for, make_response, g
from data_engine import get_dashboard_data, search_contracts, set_active_tokens
from db import (
    init_db, create_user, get_user_by_email, get_user_by_id, update_last_login, verify_password,
    create_trade, update_trade, close_trade, get_trade_by_id, get_running_trades,
    get_closed_trades, get_all_trades, get_trade_logs,
    save_fib_levels, get_fib_levels, get_reference_box_by_id, get_buy_signal_by_id,
    get_confirmation_by_id, get_active_confirmations, get_confirmation_history, get_all_confirmations,
    get_execution_by_id, get_running_executions, get_completed_executions, get_all_executions,
    get_stop_loss_by_trade_id, get_active_stop_loss_events, get_completed_stop_loss_events,
    get_target_exit_by_trade_id, get_active_target_events, get_completed_target_events,
    get_system_config, save_system_config, create_strategy_session, get_active_strategy_session,
    update_strategy_session_stats, log_strategy_event, save_system_health, get_system_health, get_strategy_logs
)
from strategy.fibonacci_engine import get_fibonacci_levels, get_confluence_levels, get_reversal_zones
from strategy.reference_box_engine import process_latest_candles, get_active_boxes as get_active_boxes_logic
from strategy.strategy_monitor import monitor_strategy, calculate_live_statistics
from strategy.buy_signal_engine import monitor_reference_boxes, get_active_signals as get_active_signals_logic, check_and_expire_signals
from strategy.confirmation_engine import monitor_buy_signals, expire_old_sessions
from strategy.execution_engine import monitor_confirmations, retry_execution as retry_execution_logic, cancel_execution as cancel_execution_logic
from strategy.stop_loss_engine import (
    monitor_running_trades, execute_stop_loss, retry_exit_order as retry_exit_order_logic, expire_monitoring as expire_monitoring_logic
)
from strategy.target_engine import (
    monitor_running_trades as monitor_targets_logic, retry_target_exit as retry_target_exit_logic, stop_monitoring as stop_monitoring_logic
)
from auth import login_required, generate_token, decode_token
import os
import datetime

app = Flask(__name__)

# Initialize the SQLite database schema
init_db()

@app.route('/favicon.ico')
def favicon():
    return '', 204

@app.route('/')
@login_required
def index():
    return redirect('/dashboard?broker=angelone&underlying=SENSEX&strategy=institutional')

@app.route('/dashboard')
@login_required
def dashboard():
    ce_symbol = request.args.get('ce_symbol')
    pe_symbol = request.args.get('pe_symbol')
    if ce_symbol and pe_symbol:
        set_active_tokens(ce_symbol, pe_symbol)
    return render_template('dashboard.html', user=g.user)

@app.route('/register', methods=['GET', 'POST'])
def register():
    token = request.cookies.get("auth_token")
    if token:
        user_id = decode_token(token)
        if user_id and get_user_by_id(user_id):
            return redirect(url_for('index'))
        
    error = None
    if request.method == 'POST':
        full_name = request.form.get('full_name', '').strip()
        email = request.form.get('email', '').strip()
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')
        
        if not full_name or not email or not password:
            error = "All fields are required."
        elif password != confirm_password:
            error = "Passwords do not match."
        elif len(password) < 6:
            error = "Password must be at least 6 characters long."
        else:
            try:
                create_user(full_name, email, password)
                return redirect(url_for('login', registered='true'))
            except ValueError as e:
                error = str(e)
                
    return render_template('register.html', error=error)

@app.route('/login', methods=['GET', 'POST'])
def login():
    token = request.cookies.get("auth_token")
    if token:
        user_id = decode_token(token)
        if user_id and get_user_by_id(user_id):
            return redirect(url_for('index'))
        
    error = None
    registered = request.args.get('registered') == 'true'
    
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        password = request.form.get('password')
        
        if not email or not password:
            error = "Please enter both email and password."
        else:
            user = get_user_by_email(email)
            if user and verify_password(user['password_hash'], password):
                update_last_login(user['id'])
                token = generate_token(user['id'])
                response = make_response(redirect(url_for('index')))
                response.set_cookie(
                    "auth_token",
                    token,
                    max_age=24 * 60 * 60,
                    httponly=True,
                    samesite="Lax"
                )
                return response
            else:
                error = "Invalid email or password."
                
    return render_template('login.html', error=error, registered=registered)

@app.route('/logout')
def logout():
    response = make_response(redirect(url_for('login')))
    response.delete_cookie("auth_token")
    return response

@app.route('/profile')
@login_required
def profile():
    return render_template('profile.html', user=g.user)

# ─── Trade View Routes ──────────────────────────────────────────────────────────

@app.route('/trades/history')
@login_required
def trade_history():
    return render_template('trade_history.html', user=g.user)

@app.route('/trades/<int:trade_id>')
@login_required
def trade_details(trade_id):
    try:
        trade = get_trade_by_id(g.user['id'], trade_id)
        if not trade:
            return "Trade not found", 404
        return render_template('trade_details.html', user=g.user, trade_id=trade_id)
    except PermissionError:
        return "Access Denied", 403

# ─── REST APIs for Trades ──────────────────────────────────────────────────────

@app.route('/api/trades', methods=['GET'])
@login_required
def get_trades_api():
    status = request.args.get('status')
    search = request.args.get('search')
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    sort_by = request.args.get('sort_by')
    
    try:
        page = int(request.args.get('page', 1))
        per_page = int(request.args.get('per_page', 10))
    except ValueError:
        return jsonify({'status': 'error', 'message': 'Invalid pagination parameters'}), 400
        
    trades, total = get_all_trades(
        g.user['id'], status=status, search=search,
        start_date=start_date, end_date=end_date,
        sort_by=sort_by, page=page, per_page=per_page
    )
    return jsonify({'status': 'success', 'trades': trades, 'total': total, 'page': page, 'per_page': per_page})

@app.route('/api/trades/running', methods=['GET'])
@login_required
def get_running_trades_api():
    trades = get_running_trades(g.user['id'])
    return jsonify({'status': 'success', 'trades': trades})

@app.route('/api/trades/closed', methods=['GET'])
@login_required
def get_closed_trades_api():
    trades = get_closed_trades(g.user['id'])
    return jsonify({'status': 'success', 'trades': trades})

@app.route('/api/trades/<int:trade_id>', methods=['GET'])
@login_required
def get_trade_details_api(trade_id):
    try:
        trade = get_trade_by_id(g.user['id'], trade_id)
        if not trade:
            return jsonify({'status': 'error', 'message': 'Trade not found'}), 404
        logs = get_trade_logs(g.user['id'], trade_id)
        return jsonify({'status': 'success', 'trade': trade, 'logs': logs})
    except PermissionError:
        return jsonify({'status': 'error', 'message': 'Access denied'}), 403

@app.route('/api/trades', methods=['POST'])
@login_required
def create_trade_api():
    payload = request.json or {}
    broker = payload.get('broker', 'angelone')
    underlying = payload.get('underlying', 'SENSEX')
    expiry = payload.get('expiry')
    call_symbol = payload.get('call_symbol')
    put_symbol = payload.get('put_symbol')
    entry_price = payload.get('entry_price')
    quantity = payload.get('quantity')
    stop_loss = payload.get('stop_loss')
    target = payload.get('target')
    strategy_name = payload.get('strategy_name')
    direction = payload.get('direction', 'BUY')
    
    if not entry_price or not quantity:
        return jsonify({'status': 'error', 'message': 'Entry price and quantity are required'}), 400
        
    try:
        trade_id = create_trade(
            g.user['id'], broker, underlying, expiry, call_symbol, put_symbol,
            float(entry_price), int(quantity),
            float(stop_loss) if stop_loss is not None else None,
            float(target) if target is not None else None,
            strategy_name, direction
        )
        return jsonify({'status': 'success', 'trade_id': trade_id})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/trades/<int:trade_id>', methods=['PUT'])
@login_required
def update_trade_api(trade_id):
    payload = request.json or {}
    stop_loss = payload.get('stop_loss')
    target = payload.get('target')
    
    try:
        update_trade(g.user['id'], trade_id, stop_loss=stop_loss, target=target)
        return jsonify({'status': 'success'})
    except PermissionError:
        return jsonify({'status': 'error', 'message': 'Access denied'}), 403
    except ValueError as e:
        return jsonify({'status': 'error', 'message': str(e)}), 400

@app.route('/api/trades/<int:trade_id>/close', methods=['POST'])
@login_required
def close_trade_api(trade_id):
    payload = request.json or {}
    exit_price = payload.get('exit_price')
    exit_reason = payload.get('exit_reason', 'Manual Exit')
    
    if exit_price is None:
        return jsonify({'status': 'error', 'message': 'Exit price is required'}), 400
        
    try:
        trade = get_trade_by_id(g.user['id'], trade_id)
        if not trade:
            return jsonify({'status': 'error', 'message': 'Trade not found'}), 404
        if trade['status'] != 'RUNNING':
            return jsonify({'status': 'error', 'message': 'Trade is already closed'}), 400

        symbol = trade.get('call_symbol') or trade.get('put_symbol')
        token = "99919001"
        exchange = "BFO"
        
        try:
            from data_engine import SCRIP_MASTER_DATA
            if SCRIP_MASTER_DATA:
                for item in SCRIP_MASTER_DATA:
                    if item.get('symbol') == symbol:
                        token = item['token']
                        exchange = item.get('exch_seg', 'BFO')
                        break
        except Exception:
            pass

        entry_direction = trade.get('direction') or 'BUY'
        exit_direction = 'SELL' if entry_direction == 'BUY' else 'BUY'

        params = {
            'variety': 'NORMAL',
            'symbol': symbol,
            'token': token,
            'transaction_type': exit_direction,
            'exchange': exchange,
            'order_type': 'MARKET',
            'product_type': 'CARRYFORWARD',
            'quantity': trade['quantity']
        }
        
        from strategy.execution_engine import execute_order
        res = execute_order(params)
        
        if not res['status']:
            return jsonify({'status': 'error', 'message': f"Broker rejected exit: {res.get('message', 'Unknown Error')}"}), 400

        pnl = close_trade(g.user['id'], trade_id, float(exit_price), exit_reason)
        return jsonify({'status': 'success', 'pnl': pnl})
    except PermissionError:
        return jsonify({'status': 'error', 'message': 'Access denied'}), 403
    except ValueError as e:
        return jsonify({'status': 'error', 'message': str(e)}), 400

def get_live_price_by_symbol(symbol):
    """Helper to fetch live price of option or spot symbol from stream state."""
    if not symbol:
        return 0.0
    from data_engine import TOKENS, LIVE_PRICES
    for label, info in TOKENS.items():
        if info.get('symbol') == symbol:
            return LIVE_PRICES.get(label, {}).get('ltp', 0.0)
    return 0.0

@app.route('/api/trades/pnl', methods=['GET'])
@login_required
def get_live_pnl_api():
    """Calculates active running P&L and aggregates overall trading stats."""
    # 1. Fetch running and closed trades
    running_trades = get_running_trades(g.user['id'])
    closed_trades = get_closed_trades(g.user['id'])
    
    # 2. Iterate running trades and calculate live P&L
    running_pnl = 0.0
    today_str = datetime.date.today().strftime("%Y-%m-%d")
    
    for t in running_trades:
        # Determine live price
        if t['call_symbol'] and t['put_symbol']:
            c_price = get_live_price_by_symbol(t['call_symbol'])
            p_price = get_live_price_by_symbol(t['put_symbol'])
            current_price = c_price + p_price
        elif t['call_symbol']:
            current_price = get_live_price_by_symbol(t['call_symbol'])
        elif t['put_symbol']:
            current_price = get_live_price_by_symbol(t['put_symbol'])
        else:
            current_price = 0.0
            
        # If live price is not streaming yet, fallback to entry_price
        if current_price == 0.0:
            current_price = t['entry_price']
            
        t['current_price'] = current_price
        
        # P&L logic
        direction = t['direction'] or 'BUY'
        if direction == 'BUY':
            t_pnl = (current_price - t['entry_price']) * t['quantity']
        else:
            t_pnl = (t['entry_price'] - current_price) * t['quantity']
            
        t['pnl'] = t_pnl
        running_pnl += t_pnl

    closed_pnl = sum(t['pnl'] for t in closed_trades)
    
    # Stats aggregation
    total_trades = len(running_trades) + len(closed_trades)
    winning_trades = sum(1 for t in closed_trades if t['pnl'] > 0)
    losing_trades = sum(1 for t in closed_trades if t['pnl'] < 0)
    win_rate = (winning_trades / len(closed_trades) * 100.0) if len(closed_trades) > 0 else 0.0
    
    # Today's P&L
    today_running_pnl = sum(t['pnl'] for t in running_trades if t['entry_time'].startswith(today_str))
    today_closed_pnl = sum(t['pnl'] for t in closed_trades if t['entry_time'].startswith(today_str))
    today_pnl = today_running_pnl + today_closed_pnl
    
    stats = {
        'running_pnl': running_pnl,
        'closed_pnl': closed_pnl,
        'today_pnl': today_pnl,
        'total_pnl': running_pnl + closed_pnl,
        'total_trades': total_trades,
        'winning_trades': winning_trades,
        'losing_trades': losing_trades,
        'win_rate': win_rate
    }
    
    return jsonify({
        'status': 'success',
        'stats': stats,
        'running_trades': running_trades
    })

@app.route('/api/data')
@login_required
def get_data():
    """Returns the JSON data for the dashboard."""
    data = get_dashboard_data()
    
    if not data.get('loading'):
        try:
            # SPOT
            sx_fibs = data['sensex']['fibonacci'][-1]['fibs'] if data['sensex']['fibonacci'] else None
            if sx_fibs:
                process_latest_candles(g.user['id'], 'SPOT', data['sensex']['symbol'], '3m', data['sensex']['intraday'], sx_fibs)
            # CALL
            ce_fibs = data['call']['fibonacci'][-1]['fibs'] if data['call']['fibonacci'] else None
            if ce_fibs:
                process_latest_candles(g.user['id'], 'CALL', data['call']['symbol'], '3m', data['call']['intraday'], ce_fibs)
            # PUT
            pe_fibs = data['put']['fibonacci'][-1]['fibs'] if data['put']['fibonacci'] else None
            if pe_fibs:
                process_latest_candles(g.user['id'], 'PUT', data['put']['symbol'], '3m', data['put']['intraday'], pe_fibs)
                
            # Run Buy Signal Engine on the updated active reference boxes
            candles_dict = {
                'SPOT': data['sensex'].get('intraday', []),
                'CALL': data['call'].get('intraday', []),
                'PUT': data['put'].get('intraday', [])
            }
            monitor_reference_boxes(g.user['id'], candles_dict)
            
            # Run Confirmation Engine on unassigned active Buy Signals
            monitor_buy_signals(g.user['id'])
            
            # Run Trade Execution Engine on CONFIRMED sessions
            monitor_confirmations(g.user['id'])
            
            # Run Stop Loss Engine on active RUNNING trades
            monitor_running_trades(g.user['id'], candles_dict)
            
            # Run Target Exit Engine on active RUNNING trades
            monitor_targets_logic(g.user['id'], candles_dict, data)
            
            # Execute monitoring sweep for active strategy session
            monitor_strategy(g.user['id'])
        except Exception as e:
            app.logger.error(f"Error processing reference boxes/buy signals/confirmations/executions/stop_loss/targets/strategy_monitor during data update: {e}")
            
    return jsonify(data)

@app.route('/api/search')
@login_required
def search():
    query = request.args.get('q', '').upper()
    if not query:
        return jsonify([])
    results = search_contracts(query)
    return jsonify(results)

@app.route('/api/update_tokens', methods=['POST'])
@login_required
def update_tokens():
    payload = request.json
    ce_symbol = payload.get('ce_symbol')
    pe_symbol = payload.get('pe_symbol')
    if ce_symbol and pe_symbol:
        if set_active_tokens(ce_symbol, pe_symbol) is False:
            return jsonify({'status': 'error', 'message': 'Tokens not found in scrip master data'})
        return jsonify({'status': 'success'})
    return jsonify({'status': 'error', 'message': 'Invalid symbols'})

@app.route('/api/set_fib', methods=['POST'])
@login_required
def set_fib():
    payload = request.json
    symbol = payload.get('symbol')
    high = payload.get('high')
    low = payload.get('low')
    if symbol and high and low:
        from data_engine import MANUAL_FIBS
        MANUAL_FIBS[symbol] = {'high': float(high), 'low': float(low)}
        return jsonify({'status': 'success'})
    return jsonify({'status': 'error'})

@app.route('/api/live')
@login_required
def get_live():
    from data_engine import LIVE_PRICES
    return jsonify(LIVE_PRICES)

@app.route('/api/fibonacci')
@login_required
def get_fibonacci_api():
    """Returns structured Fibonacci levels, reversal zones, and confluence points."""
    symbol = request.args.get('symbol', 'SENSEX').upper()
    db_data = get_dashboard_data()
    
    if db_data.get('loading'):
        return jsonify({'status': 'loading', 'message': 'Data engine warming up...'}), 202
        
    chart_type = 'SENSEX'
    symbol_data = db_data.get('sensex')
    
    if symbol == db_data.get('call', {}).get('symbol'):
        chart_type = 'CALL'
        symbol_data = db_data['call']
    elif symbol == db_data.get('put', {}).get('symbol'):
        chart_type = 'PUT'
        symbol_data = db_data['put']
    elif symbol != 'SENSEX':
        if symbol.endswith('CE'):
            chart_type = 'CALL'
            symbol_data = db_data['call']
        elif symbol.endswith('PE'):
            chart_type = 'PUT'
            symbol_data = db_data['put']
            
    if not symbol_data:
        return jsonify({'status': 'error', 'message': f'Symbol {symbol} not active or found'}), 400
        
    weekly_df = symbol_data.get('weekly', [])
    from data_engine import MANUAL_FIBS
    result = get_fibonacci_levels(weekly_df, symbol, MANUAL_FIBS)
    
    if not result:
        return jsonify({'status': 'error', 'message': 'Not enough weekly historical candles to calculate'}), 400
        
    levels, high, low, start, end = result
    week_start_str = start.strftime("%Y-%m-%d")
    week_end_str = end.strftime("%Y-%m-%d")
    
    h2l_cached = get_fib_levels(week_start_str, week_end_str, chart_type, 'HIGH_TO_LOW')
    l2h_cached = get_fib_levels(week_start_str, week_end_str, chart_type, 'LOW_TO_HIGH')
    
    if h2l_cached and l2h_cached:
        high_to_low_levels = h2l_cached
        low_to_high_levels = l2h_cached
    else:
        high_to_low_levels = {k: float(v) for k, v in levels.items() if k.startswith('f2_')}
        low_to_high_levels = {k: float(v) for k, v in levels.items() if k.startswith('f1_') or k.startswith('level_')}
        
        save_fib_levels(week_start_str, week_end_str, chart_type, 'HIGH_TO_LOW', high_to_low_levels)
        save_fib_levels(week_start_str, week_end_str, chart_type, 'LOW_TO_HIGH', low_to_high_levels)
        
    confluence_levels = get_confluence_levels(high, low)
    reversal_zones = get_reversal_zones(high, low)
    
    return jsonify({
        'status': 'success',
        'symbol': symbol,
        'chart_type': chart_type,
        'week_start': week_start_str,
        'week_end': week_end_str,
        'previous_week_high': float(high),
        'previous_week_low': float(low),
        'high_to_low_levels': high_to_low_levels,
        'low_to_high_levels': low_to_high_levels,
        'confluence_levels': confluence_levels,
        'reversal_zones': reversal_zones
    })

# ─── Reference Box APIs ────────────────────────────────────────────────────────

@app.route('/api/reference-boxes', methods=['GET'])
@login_required
def get_reference_boxes_api():
    """Returns all active reference boxes."""
    try:
        boxes = get_active_boxes_logic(g.user['id'])
        return jsonify({'status': 'success', 'reference_boxes': boxes})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/reference-boxes/<int:box_id>', methods=['GET'])
@login_required
def get_reference_box_details_api(box_id):
    """Returns details for a single reference box by ID."""
    try:
        box = get_reference_box_by_id(g.user['id'], box_id)
        if not box:
            return jsonify({'status': 'error', 'message': 'Reference box not found'}), 404
        return jsonify({'status': 'success', 'reference_box': box})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/reference-boxes/chart/<chart_type>', methods=['GET'])
@login_required
def get_reference_boxes_by_chart_api(chart_type):
    """Returns active reference boxes for a specific chart (CALL, SPOT, PUT)."""
    chart_type = chart_type.upper()
    if chart_type not in ['CALL', 'SPOT', 'PUT']:
        return jsonify({'status': 'error', 'message': 'Invalid chart type'}), 400
    try:
        boxes = get_active_boxes_logic(g.user['id'], chart_type)
        return jsonify({'status': 'success', 'reference_boxes': boxes})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/reference-boxes/refresh', methods=['POST'])
@login_required
def refresh_reference_boxes_api():
    """Forces recalculation of Reference Boxes using the latest candles."""
    try:
        db_data = get_dashboard_data()
        if db_data.get('loading'):
            return jsonify({'status': 'loading', 'message': 'Cache warmup in progress'}), 202
            
        # SENSEX SPOT
        sx_df = db_data['sensex'].get('intraday', [])
        sx_sym = db_data['sensex'].get('symbol', 'SENSEX')
        from data_engine import MANUAL_FIBS
        sx_res = get_fibonacci_levels(db_data['sensex'].get('weekly', []), sx_sym, MANUAL_FIBS)
        if sx_res:
            process_latest_candles(g.user['id'], 'SPOT', sx_sym, '3m', sx_df, sx_res[0])
            
        # CALL
        ce_df = db_data['call'].get('intraday', [])
        ce_sym = db_data['call'].get('symbol', '')
        ce_res = get_fibonacci_levels(db_data['call'].get('weekly', []), ce_sym, MANUAL_FIBS)
        if ce_res:
            process_latest_candles(g.user['id'], 'CALL', ce_sym, '3m', ce_df, ce_res[0])
            
        # PUT
        pe_df = db_data['put'].get('intraday', [])
        pe_sym = db_data['put'].get('symbol', '')
        pe_res = get_fibonacci_levels(db_data['put'].get('weekly', []), pe_sym, MANUAL_FIBS)
        if pe_res:
            process_latest_candles(g.user['id'], 'PUT', pe_sym, '3m', pe_df, pe_res[0])
            
        # Return new active boxes list
        boxes = get_active_boxes_logic(g.user['id'])
        return jsonify({
            'status': 'success',
            'message': 'Reference boxes recalculation complete',
            'reference_boxes': boxes
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

# ─── Buy Signal APIs ───────────────────────────────────────────────────────────

@app.route('/api/buy-signals', methods=['GET'])
@login_required
def get_buy_signals_api():
    """Returns all buy signals."""
    try:
        from db import load_all_signals
        signals = load_all_signals(g.user['id'])
        return jsonify({'status': 'success', 'buy_signals': signals})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/buy-signals/<int:signal_id>', methods=['GET'])
@login_required
def get_buy_signal_details_api(signal_id):
    """Returns details for a single buy signal by ID."""
    try:
        sig = get_buy_signal_by_id(g.user['id'], signal_id)
        if not sig:
            return jsonify({'status': 'error', 'message': 'Buy signal not found'}), 404
        return jsonify({'status': 'success', 'buy_signal': sig})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/buy-signals/chart/<chart_type>', methods=['GET'])
@login_required
def get_buy_signals_by_chart_api(chart_type):
    """Returns active buy signals for a specific chart (CALL, SPOT, PUT)."""
    chart_type = chart_type.upper()
    if chart_type not in ['CALL', 'SPOT', 'PUT']:
        return jsonify({'status': 'error', 'message': 'Invalid chart type'}), 400
    try:
        signals = get_active_signals_logic(g.user['id'], chart_type)
        return jsonify({'status': 'success', 'buy_signals': signals})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/buy-signals/refresh', methods=['POST'])
@login_required
def refresh_buy_signals_api():
    """Forces recalculation of Buy Signals using latest dashboard candles."""
    try:
        db_data = get_dashboard_data()
        if db_data.get('loading'):
            return jsonify({'status': 'loading', 'message': 'Cache warmup in progress'}), 202
            
        candles_dict = {
            'SPOT': db_data['sensex'].get('intraday', []),
            'CALL': db_data['call'].get('intraday', []),
            'PUT': db_data['put'].get('intraday', [])
        }
        
        # Run monitor check
        monitor_reference_boxes(g.user['id'], candles_dict)
        
        # Return new active signals
        signals = get_active_signals_logic(g.user['id'])
        return jsonify({
            'status': 'success',
            'message': 'Buy signals recalculation complete',
            'buy_signals': signals
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/buy-signals/expire', methods=['POST'])
@login_required
def expire_buy_signals_api():
    """Manually triggers expiration checks on all active signals."""
    try:
        check_and_expire_signals(g.user['id'])
        signals = get_active_signals_logic(g.user['id'])
        return jsonify({
            'status': 'success',
            'message': 'Signal expiration processing complete',
            'buy_signals': signals
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

# ─── Trade Confirmation APIs ───────────────────────────────────────────────────

@app.route('/api/confirmations', methods=['GET'])
@login_required
def get_confirmations_api():
    """Returns all confirmation sessions."""
    try:
        sessions = get_all_confirmations(g.user['id'])
        return jsonify({'status': 'success', 'confirmations': sessions})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/confirmations/<int:conf_id>', methods=['GET'])
@login_required
def get_confirmation_details_api(conf_id):
    """Returns details for a single confirmation session by ID."""
    try:
        sess = get_confirmation_by_id(g.user['id'], conf_id)
        if not sess:
            return jsonify({'status': 'error', 'message': 'Confirmation session not found'}), 404
        return jsonify({'status': 'success', 'confirmation': sess})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/confirmations/active', methods=['GET'])
@login_required
def get_active_confirmations_api():
    """Returns active (WAITING) confirmation sessions."""
    try:
        sessions = get_active_confirmations(g.user['id'])
        return jsonify({'status': 'success', 'confirmations': sessions})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/confirmations/history', methods=['GET'])
@login_required
def get_confirmation_history_api():
    """Returns completed confirmation sessions (CONFIRMED, FAILED, EXPIRED)."""
    try:
        sessions = get_confirmation_history(g.user['id'])
        return jsonify({'status': 'success', 'confirmations': sessions})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/confirmations/refresh', methods=['POST'])
@login_required
def refresh_confirmations_api():
    """Forces recalculation of confirmations."""
    try:
        monitor_buy_signals(g.user['id'])
        sessions = get_all_confirmations(g.user['id'])
        return jsonify({
            'status': 'success',
            'message': 'Confirmations recalculation complete',
            'confirmations': sessions
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/confirmations/expire', methods=['POST'])
@login_required
def expire_confirmations_api():
    """Manually checks and expires timed-out sessions."""
    try:
        expire_old_sessions(g.user['id'])
        sessions = get_all_confirmations(g.user['id'])
        return jsonify({
            'status': 'success',
            'message': 'Confirmations expiration complete',
            'confirmations': sessions
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

# ─── Trade Execution APIs ──────────────────────────────────────────────────────

@app.route('/api/executions', methods=['GET'])
@login_required
def get_executions_api():
    """Returns all execution sessions."""
    try:
        execs = get_all_executions(g.user['id'])
        return jsonify({'status': 'success', 'executions': execs})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/executions/<int:exec_id>', methods=['GET'])
@login_required
def get_execution_details_api(exec_id):
    """Returns details for a single execution by ID."""
    try:
        ex = get_execution_by_id(g.user['id'], exec_id)
        if not ex:
            return jsonify({'status': 'error', 'message': 'Execution not found'}), 404
        return jsonify({'status': 'success', 'execution': ex})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/executions/running', methods=['GET'])
@login_required
def get_running_executions_api():
    """Returns active running executions."""
    try:
        execs = get_running_executions(g.user['id'])
        return jsonify({'status': 'success', 'executions': execs})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/executions/history', methods=['GET'])
@login_required
def get_completed_executions_api():
    """Returns completed executions."""
    try:
        execs = get_completed_executions(g.user['id'])
        return jsonify({'status': 'success', 'executions': execs})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/executions/execute', methods=['POST'])
@login_required
def trigger_execution_api():
    """Manually triggers execution session monitoring check."""
    try:
        monitor_confirmations(g.user['id'])
        execs = get_all_executions(g.user['id'])
        return jsonify({
            'status': 'success',
            'message': 'Execution engine check run complete',
            'executions': execs
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/executions/retry', methods=['POST'])
@login_required
def retry_execution_api():
    """Retries a failed order execution."""
    try:
        req_data = request.get_json() or {}
        exec_id = req_data.get('execution_id')
        if not exec_id:
            return jsonify({'status': 'error', 'message': 'execution_id parameter is required'}), 400
            
        success, msg = retry_execution_logic(g.user['id'], exec_id)
        if not success:
            return jsonify({'status': 'error', 'message': msg}), 400
            
        return jsonify({'status': 'success', 'message': msg})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/executions/cancel', methods=['POST'])
@login_required
def cancel_execution_api():
    """Cancels a pending execution."""
    try:
        req_data = request.get_json() or {}
        exec_id = req_data.get('execution_id')
        if not exec_id:
            return jsonify({'status': 'error', 'message': 'execution_id parameter is required'}), 400
            
        success, msg = cancel_execution_logic(g.user['id'], exec_id)
        if not success:
            return jsonify({'status': 'error', 'message': msg}), 400
            
        return jsonify({'status': 'success', 'message': msg})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

# ─── Stop Loss APIs ────────────────────────────────────────────────────────────

@app.route('/api/stop-loss', methods=['GET'])
@login_required
def get_active_stop_loss_api():
    """Returns active stop loss monitoring sessions."""
    try:
        events = get_active_stop_loss_events(g.user['id'])
        return jsonify({'status': 'success', 'stop_loss_events': events})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/stop-loss/<int:trade_id>', methods=['GET'])
@login_required
def get_stop_loss_details_api(trade_id):
    """Returns stop-loss details for a specific trade."""
    try:
        ev = get_stop_loss_by_trade_id(g.user['id'], trade_id)
        if not ev:
            return jsonify({'status': 'error', 'message': 'Stop Loss event not found'}), 404
        return jsonify({'status': 'success', 'stop_loss_event': ev})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/stop-loss/history', methods=['GET'])
@login_required
def get_completed_stop_loss_api():
    """Returns all completed stop loss events."""
    try:
        events = get_completed_stop_loss_events(g.user['id'])
        return jsonify({'status': 'success', 'stop_loss_events': events})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/stop-loss/check', methods=['POST'])
@login_required
def force_stop_loss_check_api():
    """Forces stop-loss evaluation scanning check."""
    try:
        data = get_dashboard_data()
        candles_dict = {
            'SPOT': data['sensex'].get('intraday', []),
            'CALL': data['call'].get('intraday', []),
            'PUT': data['put'].get('intraday', [])
        }
        monitor_running_trades(g.user['id'], candles_dict)
        events = get_active_stop_loss_events(g.user['id'])
        return jsonify({
            'status': 'success',
            'message': 'Stop-loss check run complete',
            'stop_loss_events': events
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/stop-loss/retry', methods=['POST'])
@login_required
def retry_stop_loss_exit_api():
    """Retries a failed exit order."""
    try:
        req_data = request.get_json() or {}
        sl_event_id = req_data.get('stop_loss_event_id')
        if not sl_event_id:
            return jsonify({'status': 'error', 'message': 'stop_loss_event_id parameter is required'}), 400
            
        success, msg = retry_exit_order_logic(g.user['id'], sl_event_id)
        if not success:
            return jsonify({'status': 'error', 'message': msg}), 400
            
        return jsonify({'status': 'success', 'message': msg})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/stop-loss/cancel', methods=['POST'])
@login_required
def cancel_stop_loss_monitoring_api():
    """Cancels stop-loss monitoring for a trade."""
    try:
        req_data = request.get_json() or {}
        trade_id = req_data.get('trade_id')
        if not trade_id:
            return jsonify({'status': 'error', 'message': 'trade_id parameter is required'}), 400
            
        success, msg = expire_monitoring_logic(g.user['id'], trade_id)
        if not success:
            return jsonify({'status': 'error', 'message': msg}), 400
            
        return jsonify({'status': 'success', 'message': msg})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

# ─── Target Exit APIs ──────────────────────────────────────────────────────────

@app.route('/api/targets', methods=['GET'])
@login_required
def get_active_targets_api():
    """Returns active target monitoring sessions."""
    try:
        events = get_active_target_events(g.user['id'])
        return jsonify({'status': 'success', 'target_events': events})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/targets/<int:trade_id>', methods=['GET'])
@login_required
def get_target_details_api(trade_id):
    """Returns target exit details for a specific trade."""
    try:
        ev = get_target_exit_by_trade_id(g.user['id'], trade_id)
        if not ev:
            return jsonify({'status': 'error', 'message': 'Target event not found'}), 404
        return jsonify({'status': 'success', 'target_event': ev})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/targets/history', methods=['GET'])
@login_required
def get_completed_targets_api():
    """Returns all completed target exits."""
    try:
        events = get_completed_target_events(g.user['id'])
        return jsonify({'status': 'success', 'target_events': events})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/targets/check', methods=['POST'])
@login_required
def force_targets_check_api():
    """Forces target evaluation scanning check."""
    try:
        data = get_dashboard_data()
        candles_dict = {
            'SPOT': data['sensex'].get('intraday', []),
            'CALL': data['call'].get('intraday', []),
            'PUT': data['put'].get('intraday', [])
        }
        monitor_targets_logic(g.user['id'], candles_dict, data)
        events = get_active_target_events(g.user['id'])
        return jsonify({
            'status': 'success',
            'message': 'Target check run complete',
            'target_events': events
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/targets/retry', methods=['POST'])
@login_required
def retry_target_exit_api():
    """Retries a failed target exit."""
    try:
        req_data = request.get_json() or {}
        target_event_id = req_data.get('target_event_id')
        if not target_event_id:
            return jsonify({'status': 'error', 'message': 'target_event_id parameter is required'}), 400
            
        success, msg = retry_target_exit_logic(g.user['id'], target_event_id)
        if not success:
            return jsonify({'status': 'error', 'message': msg}), 400
            
        return jsonify({'status': 'success', 'message': msg})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/targets/cancel', methods=['POST'])
@login_required
def cancel_target_monitoring_api():
    """Cancels target monitoring for a trade."""
    try:
        req_data = request.get_json() or {}
        trade_id = req_data.get('trade_id')
        if not trade_id:
            return jsonify({'status': 'error', 'message': 'trade_id parameter is required'}), 400
            
        success, msg = stop_monitoring_logic(g.user['id'], trade_id)
        if not success:
            return jsonify({'status': 'error', 'message': msg}), 400
            
        return jsonify({'status': 'success', 'message': msg})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

# ─── Strategy Monitoring APIs ──────────────────────────────────────────────────

@app.route('/api/strategy/status', methods=['GET'])
@login_required
def get_strategy_status_api():
    """Returns active strategy session and market state summaries."""
    try:
        session_id = monitor_strategy(g.user['id'])
        sess = get_active_strategy_session(g.user['id'], 'institutional')
        
        from data_engine import get_dashboard_data, MANUAL_FIBS
        dash = get_dashboard_data()
        
        from strategy.fibonacci_engine import get_fibonacci_levels
        call_weekly = dash['call'].get('weekly', [])
        fibs = get_fibonacci_levels(call_weekly, dash['call'].get('symbol', 'CE'), MANUAL_FIBS)
        
        direction = 'LOW_TO_HIGH'
        if fibs:
            direction = 'LOW_TO_HIGH' if fibs.get('f0_236', 0) > fibs.get('f0_786', 0) else 'HIGH_TO_LOW'

        return jsonify({
            'status': 'success',
            'session': sess,
            'direction': direction,
            'current_sensex': dash['sensex'].get('ltp', 0.0),
            'weekly_high': dash['sensex'].get('weekly', [{}])[0].get('high', 0.0) if dash['sensex'].get('weekly') else 0.0,
            'weekly_low': dash['sensex'].get('weekly', [{}])[0].get('low', 0.0) if dash['sensex'].get('weekly') else 0.0
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/strategy/analytics', methods=['GET'])
@login_required
def get_strategy_analytics_api():
    """Returns dynamic strategy performance, wins/losses, profit factors, drawdowns."""
    try:
        stats = calculate_live_statistics(g.user['id'])
        return jsonify({'status': 'success', 'analytics': stats})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/strategy/session', methods=['GET'])
@login_required
def get_strategy_session_api():
    """Returns full strategy session details."""
    try:
        sess = get_active_strategy_session(g.user['id'], 'institutional')
        return jsonify({'status': 'success', 'session': sess})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/system/health', methods=['GET'])
@login_required
def get_system_health_api():
    """Returns database, cache, workers, and broker health parameters."""
    try:
        health = get_system_health()
        return jsonify({'status': 'success', 'health': health})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/system/logs', methods=['GET'])
@login_required
def get_system_logs_api():
    """Returns database-saved log statements."""
    try:
        search = request.args.get('search')
        severity = request.args.get('severity')
        event_type = request.args.get('event_type')
        limit = int(request.args.get('limit', 100))
        
        logs = get_strategy_logs(g.user['id'], search, severity, event_type, limit)
        return jsonify({'status': 'success', 'logs': logs})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/config', methods=['GET', 'PUT'])
@login_required
def manage_config_api():
    """Retrieves or updates system config params."""
    try:
        if request.method == 'GET':
            cfg = get_system_config(g.user['id'])
            return jsonify({'status': 'success', 'config': cfg})
        else:
            req_data = request.get_json() or {}
            save_system_config(g.user['id'], req_data)
            return jsonify({'status': 'success', 'message': 'Configuration updated successfully'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/reports/daily', methods=['GET'])
@login_required
def get_daily_report_api():
    """Generates daily report dashboard details or CSV attachment exports."""
    try:
        stats = calculate_live_statistics(g.user['id'])
        export_fmt = request.args.get('export')
        
        if export_fmt in ['csv', 'excel']:
            import csv
            import io
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(['Metric', 'Value'])
            writer.writerow(["Today's Net Profit", f"INR {stats['net_profit']:.2f}"])
            writer.writerow(['Win Rate', f"{stats['win_rate']:.1f}%"])
            writer.writerow(['Target Hits', stats['winning_trades']])
            writer.writerow(['Stop Loss Hits', stats['losing_trades']])
            
            response = make_response(output.getvalue())
            response.headers["Content-Disposition"] = "attachment; filename=daily_report.csv"
            response.headers["Content-type"] = "text/csv"
            return response
            
        return jsonify({'status': 'success', 'report': stats})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/reports/weekly', methods=['GET'])
@login_required
def get_weekly_report_api():
    """Generates weekly growth stats and risk summaries."""
    try:
        stats = calculate_live_statistics(g.user['id'])
        export_fmt = request.args.get('export')
        
        if export_fmt in ['csv', 'excel']:
            import csv
            import io
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(['Weekly Metric', 'Value'])
            writer.writerow(['Net Profit', f"INR {stats['net_profit']:.2f}"])
            writer.writerow(['Profit Factor', f"{stats['profit_factor']:.2f}"])
            writer.writerow(['Risk Reward Ratio', f"{stats['risk_reward']:.2f}"])
            writer.writerow(['Max Drawdown', f"INR {stats['max_drawdown']:.2f}"])
            writer.writerow(['Consecutive Wins', stats['consecutive_wins']])
            writer.writerow(['Consecutive Losses', stats['consecutive_losses']])
            
            response = make_response(output.getvalue())
            response.headers["Content-Disposition"] = "attachment; filename=weekly_report.csv"
            response.headers["Content-type"] = "text/csv"
            return response
            
        return jsonify({'status': 'success', 'report': stats})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/reports/monthly', methods=['GET'])
@login_required
def get_monthly_report_api():
    """Generates monthly trade distributions and growth figures."""
    try:
        stats = calculate_live_statistics(g.user['id'])
        export_fmt = request.args.get('export')
        
        if export_fmt in ['csv', 'excel']:
            import csv
            import io
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(['Monthly Metric', 'Value'])
            writer.writerow(['Gross Profit', f"INR {stats['gross_profit']:.2f}"])
            writer.writerow(['Gross Loss', f"INR {stats['gross_loss']:.2f}"])
            writer.writerow(['Net Profit', f"INR {stats['net_profit']:.2f}"])
            writer.writerow(['Win Rate', f"{stats['win_rate']:.1f}%"])
            writer.writerow(['Average Holding Time', f"{stats['avg_holding_minutes']:.1f} mins"])
            writer.writerow(['Boxes Formed', stats['boxes_created']])
            writer.writerow(['Signals Triggered', stats['signals_generated']])
            
            response = make_response(output.getvalue())
            response.headers["Content-Disposition"] = "attachment; filename=monthly_report.csv"
            response.headers["Content-type"] = "text/csv"
            return response
            
        return jsonify({'status': 'success', 'report': stats})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)

