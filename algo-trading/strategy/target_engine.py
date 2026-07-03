import logging
import time
import uuid
from datetime import datetime
import db  # type: ignore
from strategy.fibonacci_engine import get_fibonacci_levels  # type: ignore

logger = logging.getLogger('target_engine')

TARGET_CONFIG = {
    'enabled': True,
    'default_target_level': '1.39',     # Default Target Level
    'max_retries': 3,
    'retry_delay_seconds': 2,
    'candle_close_confirmation': False  # False means touch (high >= target / low <= target)
}

TARGET_LEVEL_KEYS = {
    'LOW_TO_HIGH': {
        '1.14': 'f1_1_140',
        '1.39': 'f1_1_390',
        '1.618': 'f1_1_618',
        '2.0': 'f1_2_000'
    },
    'HIGH_TO_LOW': {
        '1.14': 'f2_1_140',
        '1.39': 'f2_1_390',
        '1.618': 'f2_1_618',
        '2.0': 'f2_2_000'
    }
}

def monitor_running_trades(user_id, candles_dict, db_data=None):
    """Monitors all RUNNING trades for target hit events on CALL, SPOT, or PUT charts."""
    if not TARGET_CONFIG['enabled']:
        return

    running_trades = db.get_running_trades(user_id)
    if not running_trades:
        return

    for trade in running_trades:
        trade_id = trade['id']
        
        # 1. Fetch Reference Box for trade
        box = db.get_reference_box_for_trade(user_id, trade_id)
        if not box:
            logger.warning(f"Target Exit Monitor: Reference Box not found for Trade ID {trade_id}")
            continue

        exec_record = db.get_execution_for_trade(user_id, trade_id)
        exec_id = exec_record['id'] if exec_record else None
        confirmation_id = exec_record['confirmation_id'] if exec_record else None

        # 2. Get active target level event
        target_level = TARGET_CONFIG['default_target_level']
        target_event = db.get_target_exit_by_trade_id(user_id, trade_id)

        # Resolve levels for the trade's setup chart to define target price
        levels_box_chart = get_levels_for_chart(box['chart_type'], db_data)
        if not levels_box_chart:
            logger.warning(f"Target Exit Monitor: Could not resolve levels for box chart {box['chart_type']}")
            continue

        tgt_key = TARGET_LEVEL_KEYS[box['fib_direction']][target_level]
        target_price = levels_box_chart.get(tgt_key)
        if not target_price:
            logger.warning(f"Target Exit Monitor: Level key {tgt_key} not resolved in levels")
            continue

        if not target_event:
            # Create a target event in MONITORING state
            event_id = db.save_target_exit_event(
                user_id=user_id,
                trade_id=trade_id,
                execution_id=exec_id,
                confirmation_id=confirmation_id,
                reference_box_id=box['id'],
                chart_type=box['chart_type'],
                instrument_symbol=box['instrument_symbol'],
                fib_direction=box['fib_direction'],
                target_level=target_level,
                target_price=target_price,
                trigger_candle_timestamp=None,
                trigger_open=None,
                trigger_high=None,
                trigger_low=None,
                trigger_close=None,
                exit_price=None,
                pnl=None,
                broker_exit_order_id=None,
                exit_status='MONITORING',
                exit_reason='Active Target Monitoring'
            )
            logger.info(f"Target Monitoring Started: Created Event ID {event_id} for Trade ID {trade_id}")
            target_event = db.get_target_exit_by_trade_id(user_id, trade_id)

        # Skip if target is already complete or canceled
        if target_event['exit_status'] not in ['MONITORING', 'FAILED', 'REJECTED']:
            continue

        # 3. Check target crosses on ALL THREE charts (CALL, SPOT, PUT)
        for check_chart in ['CALL', 'SPOT', 'PUT']:
            candles = candles_dict.get(check_chart, [])
            if not candles:
                continue

            latest_candle = candles[-1]
            entry_ts = parse_local_time(trade['entry_time'])
            if latest_candle['time'] <= entry_ts:
                continue

            # Resolve levels for this chart
            chart_levels = get_levels_for_chart(check_chart, db_data)
            if not chart_levels:
                continue

            # Calculate target price for this specific chart
            chart_tgt_price = chart_levels.get(tgt_key)
            if not chart_tgt_price:
                continue

            # Evaluate breakout touch
            is_hit = detect_target_hit(latest_candle, chart_tgt_price, box['fib_direction'])
            if is_hit:
                logger.info(f"Target Hit Triggered on chart {check_chart} (price={latest_candle['close']} vs target={chart_tgt_price})")
                
                db.save_target_exit_event(
                    user_id=user_id,
                    trade_id=trade_id,
                    execution_id=exec_id,
                    confirmation_id=confirmation_id,
                    reference_box_id=box['id'],
                    chart_type=check_chart,
                    instrument_symbol=trade['call_symbol'] or trade['put_symbol'],
                    fib_direction=box['fib_direction'],
                    target_level=target_level,
                    target_price=target_price,  # Save target price of original option setup chart
                    trigger_candle_timestamp=latest_candle['time'],
                    trigger_open=latest_candle['open'],
                    trigger_high=latest_candle['high'],
                    trigger_low=latest_candle['low'],
                    trigger_close=latest_candle['close'],
                    exit_price=None,
                    pnl=None,
                    broker_exit_order_id=None,
                    exit_status='TARGET_DETECTED',
                    exit_reason=f"Target level {target_level} crossed on {check_chart} chart"
                )
                
                execute_target_exit(user_id, trade, target_event['id'], target_price, latest_candle, check_chart)
                break  # Exit loop after first chart hits

def detect_target_hit(candle, target_price, fib_direction):
    """Checks if candle high/low boundary touched or crossed the target price."""
    if TARGET_CONFIG['candle_close_confirmation']:
        if fib_direction == 'LOW_TO_HIGH':
            return candle['close'] >= target_price
        else:
            return candle['close'] <= target_price
    else:
        if fib_direction == 'LOW_TO_HIGH':
            return candle['high'] >= target_price
        else:
            return candle['low'] <= target_price

def get_levels_for_chart(chart_type, db_data=None):
    """Helper to resolve weekly levels for a chart type from database or runtime feed."""
    key_map = {'CALL': 'call', 'SPOT': 'sensex', 'PUT': 'put'}
    symbol_map = {'CALL': 'CE', 'SPOT': 'SENSEX', 'PUT': 'PE'}
    
    if not db_data:
        try:
            from data_engine import get_dashboard_data
            db_data = get_dashboard_data()
        except Exception:
            return None
            
    feed_key = key_map.get(chart_type)
    if not feed_key or not db_data or feed_key not in db_data:
        return None
        
    symbol_feed = db_data[feed_key]
    weekly_df = symbol_feed.get('weekly', [])
    symbol = symbol_feed.get('symbol', symbol_map[chart_type])
    
    from data_engine import MANUAL_FIBS
    result = get_fibonacci_levels(weekly_df, symbol, MANUAL_FIBS)
    if not result:
        return None
    levels, high, low, start, end = result
    return levels

def execute_target_exit(user_id, trade, target_id, target_price, trigger_candle, chart_type):
    """Places MARKET exit SELL order with retry handlers."""
    db.update_target_exit_status(user_id, target_id, 'ORDER_SUBMITTED')

    symbol = trade['call_symbol'] or trade['put_symbol']
    # Resolve token
    token = None
    try:
        from data_engine import SCRIP_MASTER_DATA
        if SCRIP_MASTER_DATA:
            for item in SCRIP_MASTER_DATA:
                if item.get('symbol') == symbol:
                    token = item['token']
                    break
    except Exception as e:
        logger.error(f"Error fetching exit token: {e}")

    if not token:
        token = "99919001"

    entry_direction = trade.get('direction', 'BUY')
    exit_direction = 'SELL' if entry_direction == 'BUY' else 'BUY'

    params = {
        'variety': 'NORMAL',
        'symbol': symbol,
        'token': token,
        'transaction_type': exit_direction,
        'exchange': 'BFO',
        'order_type': 'MARKET',
        'product_type': 'CARRYFORWARD',
        'quantity': trade['quantity']
    }

    max_attempts = TARGET_CONFIG['max_retries']
    delay = TARGET_CONFIG['retry_delay_seconds']

    for attempt in range(1, max_attempts + 1):
        logger.info(f"Submitting Target Exit (Attempt {attempt}/{max_attempts}) for Trade ID {trade['id']}")
        
        res = execute_order_api(params)
        
        if res['status']:
            broker_order_id = res['broker_order_id']
            
            # Verify status
            is_filled, fill_price, reason = verify_exit_order(broker_order_id, target_price)
            
            if is_filled is True:
                update_trade_after_target(
                    user_id=user_id,
                    trade_id=trade['id'],
                    target_id=target_id,
                    exit_price=fill_price,
                    broker_order_id=broker_order_id,
                    target_level=TARGET_CONFIG['default_target_level'],
                    target_price=target_price
                )
                return
            elif is_filled is False:
                # Rejected
                db.update_target_exit_status(
                    user_id=user_id,
                    target_id=target_id,
                    status='REJECTED',
                    exit_reason=reason
                )
                logger.warning(f"Target Exit Rejected: Event ID {target_id} rejected by broker: {reason}")
                return
            else:
                # Pending
                db.update_target_exit_status(
                    user_id=user_id,
                    target_id=target_id,
                    status='ORDER_SUBMITTED',
                    broker_exit_order_id=broker_order_id
                )
                return
        else:
            logger.warning(f"Target Exit failed on attempt {attempt}: {res['message']}")
            if attempt < max_attempts:
                time.sleep(delay)
            else:
                db.update_target_exit_status(
                    user_id=user_id,
                    target_id=target_id,
                    status='FAILED',
                    exit_reason=f"Target exit failed after {max_attempts} attempts: {res['message']}"
                )
                logger.error(f"Target Exit Failed: Event ID {target_id} failed retry loops")

def execute_order_api(params):
    """API connector to place orders. Mocked in offline modes."""
    try:
        from data_engine import smartApi
        if smartApi and smartApi.sessionToken:
            resp = smartApi.placeOrder({
                "variety": params['variety'],
                "tradingsymbol": params['symbol'],
                "symboltoken": params['token'],
                "transactiontype": params['transaction_type'],
                "exchange": params['exchange'],
                "ordertype": params['order_type'],
                "producttype": params['product_type'],
                "quantity": params['quantity']
            })
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
        logger.error(f"OpenAPI exception during exit: {e}")

    return {
        'status': False,
        'message': 'OpenAPI Exception / Broker Unreachable'
    }

def verify_exit_order(broker_order_id, requested_price):
    """Verifies completion fill status of exit order."""

    try:
        from data_engine import smartApi
        if smartApi and smartApi.sessionToken:
            book = smartApi.orderBook()
            if book and book.get('status') and book.get('data'):
                for ord_item in book['data']:
                    if ord_item.get('orderid') == broker_order_id:
                        status = ord_item.get('status')
                        if status == 'COMPLETE':
                            exec_price = float(ord_item.get('averageprice', requested_price))
                            return True, exec_price, None
                        elif status in ['REJECTED', 'CANCELLED']:
                            reason = ord_item.get('text', 'Broker rejection')
                            return False, None, reason
                        else:
                            return None, None, None
    except Exception as e:
        logger.error(f"OpenAPI target verification check failed: {e}")

    return False, None, "OpenAPI order book query error or session invalid"

def update_trade_after_target(user_id, trade_id, target_id, exit_price, broker_order_id, 
                              target_level, target_price):
    """Updates active trades to TARGET_HIT status and logs profit exit events."""
    trade = db.get_trade_by_id(user_id, trade_id)
    if not trade:
        return

    pnl = (exit_price - trade['entry_price']) * trade['quantity']

    conn = db.get_db_connection()
    try:
        cursor = conn.cursor()
        exit_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        # 1. Update trades table
        cursor.execute("""
            UPDATE trades
            SET exit_price = ?, exit_time = ?, pnl = ?, status = 'TARGET_HIT', exit_reason = 'Target Level Hit'
            WHERE id = ? AND user_id = ?
        """, (exit_price, exit_time, pnl, trade_id, user_id))
        
        # 2. Add log entry
        desc = f"Fibonacci Target Hit: Position exited at ₹{exit_price:.2f} (Target level: {target_level}, P&L: ₹{pnl:.2f})"
        cursor.execute("""
            INSERT INTO trade_logs (trade_id, event_type, description, price)
            VALUES (?, 'Target Hit', ?, ?)
        """, (trade_id, desc, exit_price))
        
        conn.commit()
    finally:
        conn.close()

    # 3. Update target_exit_events table to ORDER_COMPLETE
    db.update_target_exit_status(
        user_id=user_id,
        target_id=target_id,
        status='ORDER_COMPLETE',
        exit_price=exit_price,
        pnl=pnl,
        broker_exit_order_id=broker_order_id,
        exit_reason=f"Target level {target_level} hit at price ₹{target_price:.2f}"
    )
    
    logger.info(f"Monitoring Stopped: Trade ID {trade_id} closed at target (P&L: ₹{pnl:.2f})")

def parse_local_time(str_val):
    if not str_val:
        return int(time.time())
    try:
        dt = datetime.strptime(str_val, '%Y-%m-%d %H:%M:%S')
        return int(dt.timestamp())
    except Exception:
        return int(time.time())

# ─── Required Strategy Functions Wrappers ───

def monitor_targets(user_id, trade_id):
    """Retrieves the target event details."""
    return db.get_target_exit_by_trade_id(user_id, trade_id)

def validate_target(user_id, trade_id):
    """Verifies that the trade is running."""
    trade = db.get_trade_by_id(user_id, trade_id)
    return trade is not None and trade['status'] == 'RUNNING'

def retry_target_exit(user_id, target_event_id):
    """Retries a failed exit order."""
    conn = db.get_db_connection()
    try:
        row = conn.execute("SELECT * FROM target_exit_events WHERE id = ? AND user_id = ?", (target_event_id, user_id)).fetchone()
        if not row:
            return False, "Target Exit Event not found"
        event = dict(row)
    finally:
        conn.close()

    if event['exit_status'] not in ['FAILED', 'REJECTED']:
        return False, "Exit event is not in a retryable status"

    trade = db.get_trade_by_id(user_id, event['trade_id'])
    if not trade or trade['status'] != 'RUNNING':
        return False, "Associated trade is not running"

    logger.info(f"Manual Target Exit Retry Initiated: Event ID {target_event_id}")
    latest_candle = {'close': 0.0, 'open': 0.0, 'high': 0.0, 'low': 0.0}
    execute_target_exit(user_id, trade, target_event_id, event['target_price'], latest_candle, event['chart_type'])
    return True, "Exit retry initiated"

def stop_monitoring(user_id, trade_id):
    """Cancels target monitoring for a trade."""
    target_event = db.get_target_exit_by_trade_id(user_id, trade_id)
    if not target_event:
        return False, "Monitoring event not found"

    db.update_target_exit_status(user_id, target_event['id'], 'CANCELLED', exit_reason='Monitoring manually cancelled')
    logger.info(f"Target Monitoring Cancelled: Trade ID {trade_id}")
    return True, "Monitoring cancelled"
