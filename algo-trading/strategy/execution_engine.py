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

def monitor_confirmations(user_id):
    """
    Scans for CONFIRMED sessions and triggers automatic trade execution
    through the Angel One SmartAPI (or simulated matching engine).
    """
    if not EXECUTION_CONFIG['enabled']:
        return

    # 1. Fetch completed historical sessions (which include CONFIRMED sessions)
    history = db.get_confirmation_history(user_id)
    confirmed_sessions = [s for s in history if s['confirmation_status'] == 'CONFIRMED']

    for session in confirmed_sessions:
        # Avoid double execution on same session
        if db.check_confirmation_executed(user_id, session['id']):
            continue

        logger.info(f"Execution Triggered: Confirmation Session ID {session['id']} is ready for execution")
        
        # 2. Validate session rules
        is_valid, reason = validate_confirmation(user_id, session)
        if not is_valid:
            logger.warning(f"Execution Rejected: Confirmation ID {session['id']} validation failed: {reason}")
            save_failed_execution(user_id, session, reason)
            continue

        # 3. Prepare order details
        order_params = prepare_order(user_id, session)
        if not order_params:
            logger.error(f"Execution Rejected: Could not prepare order params for Session ID {session['id']}")
            save_failed_execution(user_id, session, "Failed to resolve instrument token")
            continue

        # 4. Save initial execution record in PENDING state
        exec_id = db.save_trade_execution(
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
        execute_with_retry(user_id, exec_id, order_params, session)

def validate_confirmation(user_id, session):
    """
    Checks market status, user session status, and signal integrity constraints.
    """
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
        running_trades = db.get_running_trades(user_id)
        new_trade_type = 'CE' if target_sig['chart_type'] == 'CALL' else 'PE'
        
        for trade in running_trades:
            if new_trade_type == 'CE' and trade.get('put_symbol'):
                return False, "Opposite trade (PE) is already running"
            if new_trade_type == 'PE' and trade.get('call_symbol'):
                return False, "Opposite trade (CE) is already running"

    # Market hours check (09:15 to 15:30 IST)
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

def execute_rule_signal(user_id, rule_id, action, symbol, price):
    """
    Directly executes a trade based on a confirmed Rule Engine signal.
    Bypasses the old multi-step confirmation state machine.
    """
    if not EXECUTION_CONFIG['enabled']:
        return

    logger.info(f"Executing Rule Signal: Rule {rule_id} triggered {action} for {symbol} at {price}")
    
    # 1. Lookup instrument token
    token, exchange = find_token_by_symbol(symbol)
    if not token:
        logger.error(f"Token lookup failed for symbol {symbol}")
        return

    # 2. Sizing
    quantity = calculate_order_quantity(price)
    internal_order_id = f"INT_{uuid.uuid4().hex[:12].upper()}"
    
    params = {
        'exchange': exchange or 'BFO',
        'symbol': symbol,
        'token': token,
        'order_type': 'MARKET',
        'transaction_type': 'BUY',
        'product_type': 'CARRYFORWARD',
        'variety': 'NORMAL',
        'quantity': quantity,
        'requested_price': price,
        'internal_order_id': internal_order_id,
        'order_tag': rule_id  # Attach the Rule ID for broker traceability!
    }

    # 3. Double check for active opposite trades
    running_trades = db.get_running_trades(user_id)
    new_trade_type = 'CE' if "CE" in symbol else 'PE'
    for trade in running_trades:
        if new_trade_type == 'CE' and trade.get('put_symbol'):
            logger.warning(f"Execution Rejected: Opposite trade (PE) already running")
            return
        if new_trade_type == 'PE' and trade.get('call_symbol'):
            logger.warning(f"Execution Rejected: Opposite trade (CE) already running")
            return

    # 4. Submit Order
    logger.info(f"Submitting Order for Rule {rule_id}: {quantity} lots of {symbol}")
    res = execute_order(params)
    
    if res['status']:
        broker_order_id = res['broker_order_id']
        is_filled, fill_price, reason = verify_order_status(broker_order_id, params['requested_price'])
        
        if is_filled is True:
            # Create trade directly
            stop_loss = fill_price - 10.0
            target = fill_price + 20.0
            trade_id = db.create_trade(
                user_id=user_id,
                broker='angelone',
                underlying='SENSEX',
                expiry=None,
                call_symbol=symbol if "CE" in symbol else None,
                put_symbol=symbol if "PE" in symbol else None,
                entry_price=fill_price,
                quantity=quantity,
                stop_loss=stop_loss,
                target=target,
                strategy_name=rule_id, # Display rule ID in DB!
                direction='BUY'
            )
            logger.info(f"Trade Created: ID {trade_id} (RUNNING) triggered by {rule_id}")
            
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

def execute_with_retry(user_id, exec_id, params, session):
    """
    Executes order with retry loops for network/connectivity exceptions.
    """
    # Double check for active opposite trades to prevent race conditions
    running_trades = db.get_running_trades(user_id)
    symbol = params['symbol']
    new_trade_type = 'CE' if "CE" in symbol else 'PE'
    
    for trade in running_trades:
        if new_trade_type == 'CE' and trade.get('put_symbol'):
            db.update_execution_status(user_id, exec_id, 'REJECTED', rejection_reason="Opposite trade (PE) already running")
            logger.warning(f"Execution Rejected (Race Condition Prev): Opposite trade (PE) already running for Execution ID {exec_id}")
            return
        if new_trade_type == 'PE' and trade.get('call_symbol'):
            db.update_execution_status(user_id, exec_id, 'REJECTED', rejection_reason="Opposite trade (CE) already running")
            logger.warning(f"Execution Rejected (Race Condition Prev): Opposite trade (CE) already running for Execution ID {exec_id}")
            return

    db.update_execution_status(user_id, exec_id, 'SUBMITTED')
    
    max_attempts = EXECUTION_CONFIG['max_retries']
    delay = EXECUTION_CONFIG['retry_delay_seconds']

    for attempt in range(1, max_attempts + 1):
        logger.info(f"Submitting Order (Attempt {attempt}/{max_attempts}) for Execution ID {exec_id}")
        
        # Place order
        res = execute_order(params)
        
        if res['status']:
            broker_order_id = res['broker_order_id']
            
            # Immediately verify order filled state
            is_filled, fill_price, reason = verify_order_status(broker_order_id, params['requested_price'])
            
            if is_filled is True:
                # Trade Executed Successfully! Create running trade record.
                create_running_trade(user_id, exec_id, broker_order_id, fill_price, params, session)
                return
            elif is_filled is False:
                # Order explicitly rejected by broker - do NOT retry
                db.update_execution_status(
                    user_id=user_id,
                    exec_id=exec_id,
                    status='REJECTED',
                    broker_order_id=broker_order_id,
                    rejection_reason=reason
                )
                logger.warning(f"Order Rejected: Execution ID {exec_id} rejected by broker: {reason}")
                return
            else:
                # Order is still pending in broker book, mark as SUBMITTED and log
                db.update_execution_status(
                    user_id=user_id,
                    exec_id=exec_id,
                    status='SUBMITTED',
                    broker_order_id=broker_order_id
                )
                logger.info(f"Order Pending: Execution ID {exec_id} is currently open in broker book")
                return
        else:
            # Placement failed (e.g. timeout, API rate-limit rejection)
            logger.warning(f"Order Submission failed on attempt {attempt}: {res['message']}")
            if attempt < max_attempts:
                time.sleep(delay)
            else:
                # All retry attempts failed
                db.update_execution_status(
                    user_id=user_id,
                    exec_id=exec_id,
                    status='FAILED',
                    rejection_reason=f"Submission failed after {max_attempts} attempts: {res['message']}"
                )
                logger.error(f"Execution Failed: ID {exec_id} failed submission retry loops")

def create_running_trade(user_id, exec_id, broker_order_id, executed_price, params, session):
    """
    On successful order fill, initializes a running trade in the trades table
    and links it to the execution session.
    """
    # 1. Prepare trade parameters
    symbol = params['symbol']
    direction = params['transaction_type'] # BUY
    qty = params['quantity']
    
    call_symbol = symbol if "CE" in symbol else None
    put_symbol = symbol if "PE" in symbol else None
    
    # Simple default bounds for Stop Loss and Target
    stop_loss = executed_price - 10.0
    target = executed_price + 20.0

    # 2. Insert trade into trades table (creates audit log automatically)
    trade_id = db.create_trade(
        user_id=user_id,
        broker='angelone',
        underlying='SENSEX',
        expiry=None,
        call_symbol=call_symbol,
        put_symbol=put_symbol,
        entry_price=executed_price,
        quantity=qty,
        stop_loss=stop_loss,
        target=target,
        strategy_name=session['strategy_name'],
        direction=direction
    )

    # 3. Update trade link inside executions table
    import pytz
    ist = pytz.timezone('Asia/Kolkata')
    exec_time = datetime.now(ist).strftime('%Y-%m-%d %H:%M:%S')
    db.update_execution_status(
        user_id=user_id,
        exec_id=exec_id,
        status='COMPLETE',
        executed_price=executed_price,
        broker_order_id=broker_order_id,
        trade_id=trade_id
    )
    
    # 4. Also update the execution time field directly in DB
    conn = db.get_db_connection()
    try:
        conn.execute("""
            UPDATE trade_executions SET execution_time = ?
            WHERE id = ? AND user_id = ?
        """, (exec_time, exec_id, user_id))
        conn.commit()
    finally:
        conn.close()

    logger.info(f"Trade Created: ID {trade_id} (RUNNING) linked to Execution ID {exec_id} (COMPLETE)")

def save_failed_execution(user_id, session, reason):
    """Creates a failed execution log for auditing purposes."""
    db.save_trade_execution(
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

def retry_execution(user_id, exec_id):
    """Manually retries a failed trade execution."""
    execution = db.get_execution_by_id(user_id, exec_id)
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
    session = db.get_confirmation_by_id(user_id, execution['confirmation_id'])
    if not session:
        return False, "Confirmation session not found"

    # Trigger order
    logger.info(f"Manual Retry Initiated: Execution ID {exec_id}")
    execute_with_retry(user_id, exec_id, params, session)
    return True, "Retry process completed"

def cancel_execution(user_id, exec_id):
    """Updates status to CANCELLED representing cancellation of pending fills."""
    db.update_execution_status(user_id, exec_id, 'CANCELLED')
    logger.info(f"Execution Cancelled: ID {exec_id} marked as CANCELLED")
    return True, "Execution cancelled successfully"

def save_execution(user_id, exec_data):
    """Directly wraps db.save_trade_execution."""
    return db.save_trade_execution(
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
