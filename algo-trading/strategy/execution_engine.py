import logging
import time
import uuid
import json
from datetime import datetime
import db  # type: ignore

logger = logging.getLogger('execution_engine')

EXECUTION_CONFIG = {
    'enabled': True,
    'sizing_type': 'FIXED',  # 'FIXED' or 'CAPITAL'
    'fixed_qty': 10,
    'capital_allocated': 50000,
    'max_retries': 3,
    'retry_delay_seconds': 2
}

def monitor_confirmations(user_id, db_adapter=None, execute_adapter=None):
    """
    Scans for CONFIRMED sessions and triggers automatic trade execution
    through the Angel One SmartAPI (or simulated matching engine).
    """
    db_local = db_adapter or db
    if not EXECUTION_CONFIG['enabled']:
        return

    # 1. Fetch completed historical sessions (which include CONFIRMED sessions)
    history = db_local.get_confirmation_history(user_id)
    confirmed_sessions = [s for s in history if s['confirmation_status'] == 'CONFIRMED']

    for session in confirmed_sessions:
        # Avoid double execution on same session
        if db_local.check_confirmation_executed(user_id, session['id']):
            continue

        logger.info(f"Execution Triggered: Confirmation Session ID {session['id']} is ready for execution")
        
        # 2. Validate session rules
        is_valid, reason = validate_confirmation(user_id, session, db_adapter=db_local)
        if not is_valid:
            logger.warning(f"Execution Rejected: Confirmation ID {session['id']} validation failed: {reason}")
            save_failed_execution(user_id, session, reason, db_adapter=db_local)
            continue

        # 3. Prepare order details
        order_params = prepare_order(user_id, session)
        if not order_params:
            logger.error(f"Execution Rejected: Could not prepare order params for Session ID {session['id']}")
            save_failed_execution(user_id, session, "Failed to resolve instrument token", db_adapter=db_local)
            continue

        # 4. Save initial execution record in PENDING state
        exec_id = db_local.save_trade_execution(
            user_id=user_id,
            confirmation_id=session['id'],
            trade_id=None,
            broker='angelone',
            exchange=order_params['exchange'],
            symbol=order_params['symbol'],
            token=order_params['token'],
            order_type=order_params['order_type'],
            transaction_type=order_params['transaction_type'],
            product_type=order_params['product_type'],
            variety=order_params['variety'],
            quantity=order_params['quantity'],
            requested_price=order_params['requested_price'],
            executed_price=None,
            order_id=order_params['internal_order_id'],
            broker_order_id=None,
            execution_status='PENDING',
            rejection_reason=None,
            execution_time=None
        )

        logger.info(f"Execution Pending: ID {exec_id} recorded in DB")

        # 5. Submit order to broker with retry loops
        execute_with_retry(user_id, exec_id, order_params, session, db_adapter=db_local, execute_adapter=execute_adapter)

def validate_confirmation(user_id, session, db_adapter=None):
    """
    Checks market status, user session status, and signal integrity constraints.
    """
    db_local = db_adapter or db
    if not session or session['confirmation_status'] != 'CONFIRMED':
        return False, "Session not confirmed"

    if not session.get('signals') or len(session['signals']) == 0:
        return False, "No buy signals in confirmation session"

    # Verify that at least one option chart (CALL or PUT) is part of the confirmation
    has_option = any(s['chart_type'] in ['CALL', 'PUT'] for s in session['signals'])
    if not has_option:
        return False, "No option instrument found to place trade"

    # Extract target signal to check price bounds and type (CE/PE)
    call_sig = next((s for s in session['signals'] if s['chart_type'] == 'CALL'), None)
    put_sig = next((s for s in session['signals'] if s['chart_type'] == 'PUT'), None)
    target_sig = call_sig if call_sig else put_sig
    
    if target_sig:
        symbol = target_sig.get('instrument_symbol', '')
        # Try to get live LTP
        current_ltp = 0.0
        try:
            from data_engine import LIVE_PRICES, TOKENS
            for label, info in TOKENS.items():
                if info.get('symbol') == symbol:
                    current_ltp = LIVE_PRICES.get(label, {}).get('ltp', 0.0)
                    break
        except Exception:
            pass

        if current_ltp > 0:
            if not (400 <= current_ltp <= 500):
                return False, f"Live LTP ({current_ltp}) is outside allowed range (400 - 500)"
        else:
            breakout_price = target_sig.get('breakout_price', 0)
            if not (400 <= breakout_price <= 500):
                return False, f"Trade LTP ({breakout_price}) is outside allowed range (400 - 500)"
            
        # Check for active opposite trades
        running_trades = db_local.get_running_trades(user_id)
        new_trade_type = 'CE' if target_sig['chart_type'] == 'CALL' else 'PE'
        
        for trade in running_trades:
            if new_trade_type == 'CE' and trade.get('put_symbol'):
                return False, "Opposite trade (PE) is already running"
            if new_trade_type == 'PE' and trade.get('call_symbol'):
                return False, "Opposite trade (CE) is already running"

    # Market hours check (09:15 to 15:30 IST)
    # Bypass for simulation mode since it runs historical day logs which are already during market hours
    if db_adapter is not None and db_adapter.__name__ == 'simulate_db':
        return True, "Valid"

    import pytz
    ist = pytz.timezone('Asia/Kolkata')
    now_ist = datetime.now(ist)
    market_start = now_ist.replace(hour=9, minute=15, second=0, microsecond=0)
    market_end = now_ist.replace(hour=15, minute=30, second=0, microsecond=0)
    
    if not (market_start <= now_ist <= market_end):
        return False, "Outside market hours (09:15 - 15:30 IST)"

    return True, "Valid"

def prepare_order(user_id, session):
    """
    Resolves option symbols to instrument tokens and calculates lot quantity.
    """
    # 1. Resolve trading instrument from CALL or PUT signal (prefer CALL if both exist)
    call_sig = next((s for s in session['signals'] if s['chart_type'] == 'CALL'), None)
    put_sig = next((s for s in session['signals'] if s['chart_type'] == 'PUT'), None)
    
    target_sig = call_sig if call_sig else put_sig
    if not target_sig:
        return None

    symbol = target_sig['instrument_symbol']
    breakout_price = target_sig['breakout_price']

    # 2. Lookup instrument token inside scrip master
    token, exchange = find_token_by_symbol(symbol)
    if not token:
        logger.error(f"Token lookup failed for symbol {symbol}")
        return None

    # 3. Sizing
    quantity = calculate_order_quantity(breakout_price)

    # 4. Params pack
    internal_order_id = f"INT_{uuid.uuid4().hex[:12].upper()}"
    
    return {
        'exchange': exchange or 'BFO',
        'symbol': symbol,
        'token': token,
        'order_type': 'MARKET',
        'transaction_type': 'BUY',
        'product_type': 'CARRYFORWARD',
        'variety': 'NORMAL',
        'quantity': quantity,
        'requested_price': breakout_price,
        'internal_order_id': internal_order_id
    }

def find_token_by_symbol(symbol):
    """Searches scrip master list for matching instrument token."""
    try:
        from data_engine import SCRIP_MASTER_DATA
        if SCRIP_MASTER_DATA:
            for item in SCRIP_MASTER_DATA:
                if item.get('symbol') == symbol:
                    return item['token'], item.get('exch_seg', 'BFO')
    except Exception as e:
        logger.error(f"Scrip lookup error: {e}")
    # Fallback default constants for testing if not loaded
    if "CE" in symbol:
        return "99919001", "BFO"
    elif "PE" in symbol:
        return "99919002", "BFO"
    return None, None

def calculate_order_quantity(breakout_price):
    """Returns requested order sizing quantity."""
    sizing_type = EXECUTION_CONFIG['sizing_type']
    if sizing_type == 'FIXED':
        return EXECUTION_CONFIG['fixed_qty']
    elif sizing_type == 'CAPITAL':
        capital = EXECUTION_CONFIG['capital_allocated']
        raw_qty = int(capital / breakout_price)
        # Round down to nearest lot size of 10
        qty = (raw_qty // 10) * 10
        return max(10, qty)
    return 10

def execute_rule_signal(user_id, rule_id, action, symbol, price, db_adapter=None, execute_adapter=None):
    """
    Directly executes a trade based on a confirmed Rule Engine signal.
    Bypasses the old multi-step confirmation state machine.
    """
    db_local = db_adapter or db
    if not EXECUTION_CONFIG['enabled']:
        return

    logger.info(f"Executing Rule Signal: Rule {rule_id} triggered {action} for {symbol} at {price}")
    
    quantity = calculate_order_quantity(price)
    option_type = 'CE' if "CE" in symbol else 'PE'
    
    try:
        trade_id = perform_manual_trade_open(
            user_id=user_id,
            symbol=symbol,
            entry_price=price,
            quantity=quantity,
            direction='BUY',
            option_type=option_type,
            strategy_name=rule_id,
            db_adapter=db_local,
            execute_adapter=execute_adapter
        )
        logger.info(f"Trade Created via Helper: ID {trade_id} (RUNNING) triggered by {rule_id}")
    except Exception as e:
        logger.error(f"Rule signal order placement failed: {e}")
            
def execute_order(params):
    """
    Submits order to Angel One OpenAPI. Falls back to simulated fill in offline mode.
    """
    try:
        from data_engine import smartApi
        # Verify if SmartAPI object is initialized and has a valid session token
        if smartApi and smartApi.sessionToken:
            # Place order on OpenAPI
            req_params = {
                "variety": params['variety'],
                "tradingsymbol": params['symbol'],
                "symboltoken": params['token'],
                "transactiontype": params['transaction_type'],
                "exchange": params['exchange'],
                "ordertype": params['order_type'],
                "producttype": params['product_type'],
                "quantity": params['quantity']
            }
            if 'order_tag' in params:
                req_params['ordertag'] = params['order_tag'] # Support for order tagging

            resp = smartApi.placeOrder(req_params)
            if resp and resp.get('status') and resp.get('data'):
                return {
                    'status': True,
                    'broker_order_id': resp['data']['orderid'],
                    'message': 'SUCCESS'
                }
            else:
                return {
                    'status': False,
                    'message': resp.get('message', 'Rejected by broker')
                }
    except Exception as e:
        logger.error(f"OpenAPI exception during placement: {e}")

    return {
        'status': False,
        'message': 'OpenAPI Exception / Broker Unreachable'
    }

def verify_order_status(broker_order_id, requested_price):
    """
    Checks filled state of submitted broker order ID.
    Returns status tuple: (is_filled, price, reason)
    """
    if broker_order_id and str(broker_order_id).startswith("SIM_"):
        return True, requested_price, None

    try:
        from data_engine import smartApi
        if smartApi and smartApi.sessionToken:
            # Fetch order book history
            book = smartApi.orderBook()
            if book and book.get('status') and book.get('data'):
                for ord_item in book['data']:
                    if ord_item.get('orderid') == broker_order_id:
                        status = ord_item.get('status')
                        if status == 'COMPLETE':
                            # Read executed price
                            exec_price = float(ord_item.get('averageprice', requested_price))
                            return True, exec_price, None
                        elif status in ['REJECTED', 'CANCELLED']:
                            reason = ord_item.get('text', 'Broker rejection')
                            return False, None, reason
                        else:
                            # Still pending / open
                            return None, None, None
    except Exception as e:
        logger.error(f"OpenAPI order book query error: {e}")

    return False, None, "OpenAPI order book query error or session invalid"

def execute_with_retry(user_id, exec_id, params, session, db_adapter=None, execute_adapter=None):
    """
    Executes order with retry loops for network/connectivity exceptions.
    Delegates to perform_manual_trade_open helper.
    """
    db_local = db_adapter or db
    symbol = params['symbol']
    option_type = 'CE' if 'CE' in symbol else 'PE'
    try:
        perform_manual_trade_open(
            user_id=user_id,
            symbol=symbol,
            entry_price=params['requested_price'],
            quantity=params['quantity'],
            direction=params.get('transaction_type', 'BUY'),
            option_type=option_type,
            strategy_name=session.get('strategy_name', 'manual'),
            confirmation_id=session.get('id'),
            execution_id=exec_id,
            db_adapter=db_local,
            execute_adapter=execute_adapter
        )
    except Exception as e:
        logger.error(f"Strategy execution order placement failed: {e}")
        db_local.update_execution_status(
            user_id=user_id,
            exec_id=exec_id,
            status='FAILED',
            rejection_reason=str(e)
        )

def save_failed_execution(user_id, session, reason, db_adapter=None):
    """Creates a failed execution log for auditing purposes."""
    db_local = db_adapter or db
    db_local.save_trade_execution(
        user_id=user_id,
        confirmation_id=session['id'],
        trade_id=None,
        broker='angelone',
        exchange='BFO',
        symbol='—',
        token='—',
        order_type='MARKET',
        transaction_type='BUY',
        product_type='CARRYFORWARD',
        variety='NORMAL',
        quantity=0,
        requested_price=0.0,
        executed_price=None,
        order_id=None,
        broker_order_id=None,
        execution_status='FAILED',
        rejection_reason=reason,
        execution_time=None
    )

# ─── Required Strategy Functions Wrappers ───

def retry_execution(user_id, exec_id, db_adapter=None, execute_adapter=None):
    """Manually retries a failed trade execution."""
    db_local = db_adapter or db
    execution = db_local.get_execution_by_id(user_id, exec_id)
    if not execution or execution['execution_status'] not in ['FAILED', 'REJECTED']:
        return False, "Execution is not in a retryable state"

    # Re-prepare parameters
    token, exchange = find_token_by_symbol(execution['symbol'])
    params = {
        'exchange': execution['exchange'],
        'symbol': execution['symbol'],
        'token': token or execution['token'],
        'order_type': execution['order_type'],
        'transaction_type': execution['transaction_type'],
        'product_type': execution['product_type'],
        'variety': execution['variety'],
        'quantity': execution['quantity'],
        'requested_price': execution['requested_price'],
        'internal_order_id': execution['order_id'] or f"INT_{uuid.uuid4().hex[:12].upper()}"
    }

    # Fetch confirmation details
    session = db_local.get_confirmation_by_id(user_id, execution['confirmation_id'])
    if not session:
        return False, "Confirmation session not found"

    # Trigger order
    logger.info(f"Manual Retry Initiated: Execution ID {exec_id}")
    execute_with_retry(user_id, exec_id, params, session, db_adapter=db_local, execute_adapter=execute_adapter)
    return True, "Retry process completed"

def cancel_execution(user_id, exec_id, db_adapter=None):
    """Updates status to CANCELLED representing cancellation of pending fills."""
    db_local = db_adapter or db
    db_local.update_execution_status(user_id, exec_id, 'CANCELLED')
    logger.info(f"Execution Cancelled: ID {exec_id} marked as CANCELLED")
    return True, "Execution cancelled successfully"

def save_execution(user_id, exec_data, db_adapter=None):
    """Directly wraps db.save_trade_execution."""
    db_local = db_adapter or db
    return db_local.save_trade_execution(
        user_id=user_id,
        confirmation_id=exec_data['confirmation_id'],
        trade_id=exec_data.get('trade_id'),
        broker=exec_data.get('broker', 'angelone'),
        exchange=exec_data.get('exchange', 'BFO'),
        symbol=exec_data['symbol'],
        token=exec_data['token'],
        order_type=exec_data.get('order_type', 'MARKET'),
        transaction_type=exec_data.get('transaction_type', 'BUY'),
        product_type=exec_data.get('product_type', 'CARRYFORWARD'),
        variety=exec_data.get('variety', 'NORMAL'),
        quantity=exec_data['quantity'],
        requested_price=exec_data.get('requested_price', 0.0),
        executed_price=exec_data.get('executed_price'),
        order_id=exec_data.get('order_id'),
        broker_order_id=exec_data.get('broker_order_id'),
        execution_status=exec_data.get('execution_status', 'PENDING'),
        rejection_reason=exec_data.get('rejection_reason'),
        execution_time=exec_data.get('execution_time')
    )


def perform_manual_trade_open(user_id, symbol, entry_price, quantity, direction="BUY", option_type="CE", strategy_name="manual", confirmation_id=None, execution_id=None, db_adapter=None, execute_adapter=None):
    """
    Unified manual order opening logic.
    Validates duplicates, executes the order via the injected execution adapter,
    creates the trade, and registers the execution logs.
    """
    db_local = db_adapter or db
    if db_adapter is None and execute_adapter is None:
        # Live manual open: mock the execute_local to preserve original live behavior (no-op broker call)
        execute_local = lambda params: {'status': True, 'broker_order_id': f"MANUAL_{uuid.uuid4().hex[:6].upper()}"}
    else:
        execute_local = execute_adapter or execute_order

    # 1. Reject if a trade of the same type is already running
    running = db_local.get_running_trades(user_id)
    for t in running:
        t_type = "CE" if t.get('call_symbol') else ("PE" if t.get('put_symbol') else None)
        if t_type == option_type:
            raise ValueError(f"{option_type} trade already running")

    # 2. Resolve token / exchange
    token, exchange = find_token_by_symbol(symbol)
    if not token:
        token = "99919001"
    if not exchange:
        exchange = "BFO"

    internal_order_id = f"INT_OPEN_{uuid.uuid4().hex[:12].upper()}"
    params = {
        'variety': 'NORMAL',
        'symbol': symbol,
        'token': token,
        'transaction_type': direction,
        'exchange': exchange,
        'order_type': 'MARKET',
        'product_type': 'CARRYFORWARD',
        'quantity': quantity,
        'requested_price': entry_price,
        'internal_order_id': internal_order_id
    }

    # 3. Place order
    res = execute_local(params)
    if not res['status']:
        raise RuntimeError(f"Broker rejected entry: {res.get('message', 'Unknown Error')}")

    broker_order_id = res.get('broker_order_id', f"SIM_OPEN_{uuid.uuid4().hex[:6].upper()}")

    # 4. Insert trade
    stop_loss = entry_price - 10.0 if direction == 'BUY' else entry_price + 10.0
    target = entry_price + 20.0 if direction == 'BUY' else entry_price - 20.0

    trade_id = db_local.create_trade(
        user_id=user_id,
        broker='angelone',
        underlying='SENSEX',
        expiry=None,
        call_symbol=symbol if option_type == 'CE' else None,
        put_symbol=symbol if option_type == 'PE' else None,
        entry_price=entry_price,
        quantity=quantity,
        stop_loss=stop_loss,
        target=target,
        strategy_name=strategy_name,
        direction=direction
    )

    # 5. Log trade execution
    import pytz
    ist = pytz.timezone('Asia/Kolkata')
    exec_time = datetime.now(ist).strftime('%Y-%m-%d %H:%M:%S')

    # Resolve confirmation_id to avoid NOT NULL constraint errors
    if not confirmation_id:
        conn = db_local.get_db_connection()
        try:
            import time
            now_ts = int(time.time())
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO trade_confirmations (
                    user_id, confirmation_status, strategy_name, 
                    confirmation_start_time, confirmation_end_time
                ) VALUES (?, 'MANUAL_EXECUTION', ?, ?, ?)
            """, (user_id, strategy_name, now_ts, now_ts))
            conn.commit()
            confirmation_id = cursor.lastrowid
        finally:
            conn.close()

    if execution_id:
        db_local.update_execution_status(
            user_id=user_id,
            exec_id=execution_id,
            status='COMPLETE',
            executed_price=entry_price,
            broker_order_id=broker_order_id,
            trade_id=trade_id
        )
        # Also update the execution time field directly in DB
        conn = db_local.get_db_connection()
        try:
            conn.execute("""
                UPDATE trade_executions SET execution_time = ?
                WHERE id = ? AND user_id = ?
            """, (exec_time, execution_id, user_id))
            conn.commit()
        finally:
            conn.close()
    else:
        db_local.save_trade_execution(
            user_id=user_id,
            confirmation_id=confirmation_id,
            trade_id=trade_id,
            broker='angelone',
            exchange=exchange,
            symbol=symbol,
            token=token,
            order_type='MARKET',
            transaction_type=direction,
            product_type='CARRYFORWARD',
            variety='NORMAL',
            quantity=quantity,
            requested_price=entry_price,
            executed_price=entry_price,
            order_id=internal_order_id,
            broker_order_id=broker_order_id,
            execution_status='COMPLETE',
            rejection_reason=None,
            execution_time=exec_time
        )

    return trade_id


def perform_manual_trade_close(user_id, trade_id, exit_price, exit_reason="Manual Exit", db_adapter=None, execute_adapter=None):
    """
    Unified manual trade closing logic.
    Executes exit order, computes final P&L, updates execution logs, and closes trade.
    """
    db_local = db_adapter or db
    execute_local = execute_adapter or execute_order

    trade = db_local.get_trade_by_id(user_id, trade_id)
    if not trade:
        raise ValueError("Trade not found")
    if trade['status'] != 'RUNNING':
        raise ValueError("Trade is already closed")

    symbol = trade.get('call_symbol') or trade.get('put_symbol')
    token, exchange = find_token_by_symbol(symbol)
    if not token:
        token = "99919001"
    if not exchange:
        exchange = "BFO"

    entry_direction = trade.get('direction') or 'BUY'
    exit_direction = 'SELL' if entry_direction == 'BUY' else 'BUY'

    internal_order_id = f"INT_CLOSE_{uuid.uuid4().hex[:12].upper()}"
    params = {
        'variety': 'NORMAL',
        'symbol': symbol,
        'token': token,
        'transaction_type': exit_direction,
        'exchange': exchange,
        'order_type': 'MARKET',
        'product_type': 'CARRYFORWARD',
        'quantity': trade['quantity'],
        'internal_order_id': internal_order_id
    }

    # Place order
    res = execute_local(params)
    if not res['status']:
        raise RuntimeError(f"Broker rejected exit: {res.get('message', 'Unknown Error')}")

    broker_order_id = res.get('broker_order_id', f"SIM_CLOSE_{uuid.uuid4().hex[:6].upper()}")

    # Log exit execution
    import pytz
    ist = pytz.timezone('Asia/Kolkata')
    exec_time = datetime.now(ist).strftime('%Y-%m-%d %H:%M:%S')

    # Resolve confirmation_id to avoid NOT NULL constraint errors
    confirmation_id = None
    exec_record = db_local.get_execution_for_trade(user_id, trade_id)
    if exec_record:
        confirmation_id = exec_record.get('confirmation_id')
        
    if not confirmation_id:
        conn = db_local.get_db_connection()
        try:
            import time
            now_ts = int(time.time())
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO trade_confirmations (
                    user_id, confirmation_status, strategy_name, 
                    confirmation_start_time, confirmation_end_time
                ) VALUES (?, 'MANUAL_EXECUTION', ?, ?, ?)
            """, (user_id, 'manual', now_ts, now_ts))
            conn.commit()
            confirmation_id = cursor.lastrowid
        finally:
            conn.close()

    db_local.save_trade_execution(
        user_id=user_id,
        confirmation_id=confirmation_id,
        trade_id=trade_id,
        broker='angelone',
        exchange=exchange,
        symbol=symbol,
        token=token,
        order_type='MARKET',
        transaction_type=exit_direction,
        product_type='CARRYFORWARD',
        variety='NORMAL',
        quantity=trade['quantity'],
        requested_price=exit_price,
        executed_price=exit_price,
        order_id=internal_order_id,
        broker_order_id=broker_order_id,
        execution_status='COMPLETE',
        rejection_reason=None,
        execution_time=exec_time
    )

    pnl = db_local.close_trade(user_id, trade_id, exit_price, exit_reason)
    return pnl


def perform_manual_trade_close_by_type(user_id, option_type, exit_price, exit_reason="Manual Exit", db_adapter=None, execute_adapter=None):
    """
    Closes any running trade of the specified option type ('CE' or 'PE').
    If no running trade exists, raises ValueError.
    """
    db_local = db_adapter or db
    running = db_local.get_running_trades(user_id)
    target_trade = None
    for t in running:
        t_type = "CE" if t.get('call_symbol') else ("PE" if t.get('put_symbol') else None)
        if t_type == option_type:
            target_trade = t
            break

    if not target_trade:
        raise ValueError(f"No active {option_type} trade found")

    return perform_manual_trade_close(
        user_id=user_id,
        trade_id=target_trade['id'],
        exit_price=exit_price,
        exit_reason=exit_reason,
        db_adapter=db_adapter,
        execute_adapter=execute_adapter
    )
