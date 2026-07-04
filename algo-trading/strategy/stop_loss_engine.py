import logging
import time
import uuid
from datetime import datetime
import db  # type: ignore

logger = logging.getLogger('stop_loss_engine')

STOP_LOSS_CONFIG = {
    'enabled': True,
    'points_offset': 3.0,     # Exit 3 points below the Reference Box
    'max_retries': 3,
    'retry_delay_seconds': 2
}

def monitor_running_trades(user_id, candles_dict):
    """
    Monitors all active RUNNING trades. Evaluates latest candle closes
    against reference box lower boundaries to trigger automatic Stop Loss exits.
    """
    if not STOP_LOSS_CONFIG['enabled']:
        return

    running_trades = db.get_running_trades(user_id)
    if not running_trades:
        return

    for trade in running_trades:
        trade_id = trade['id']
        
        # 1. Fetch active Reference Box for this trade
        box = db.get_reference_box_for_trade(user_id, trade_id)
        if not box:
            logger.warning(f"Stop Loss monitor: Could not resolve Reference Box for Trade ID {trade_id}")
            continue

        # 2. Ensure a Stop Loss Event record exists in MONITORING state
        sl_event = db.get_stop_loss_by_trade_id(user_id, trade_id)
        calculated_sl = box['lower_boundary'] - STOP_LOSS_CONFIG['points_offset']
        
        exec_record = db.get_execution_for_trade(user_id, trade_id)
        exec_id = exec_record['id'] if exec_record else None

        if not sl_event:
            event_id = db.save_stop_loss_event(
                user_id=user_id,
                trade_id=trade_id,
                execution_id=exec_id,
                reference_box_id=box['id'],
                chart_type=box['chart_type'],
                instrument_symbol=box['instrument_symbol'],
                reference_box_upper=box['upper_boundary'],
                reference_box_lower=box['lower_boundary'],
                calculated_stop_loss=calculated_sl,
                trigger_candle_timestamp=None,
                trigger_open=None,
                trigger_high=None,
                trigger_low=None,
                trigger_close=None,
                exit_price=None,
                pnl=None,
                broker_exit_order_id=None,
                exit_status='MONITORING',
                exit_reason='Active Stop Loss Monitoring'
            )
            logger.info(f"Stop Loss Monitoring Started: Created Event ID {event_id} for Trade ID {trade_id}")
            sl_event = db.get_stop_loss_by_trade_id(user_id, trade_id)

        # Skip if stop loss monitoring is already complete, triggered, or inactive
        if sl_event['exit_status'] not in ['MONITORING', 'FAILED', 'REJECTED']:
            continue

        # 3. Fetch latest closed candle for the corresponding chart
        chart_type = box['chart_type']
        candles = candles_dict.get(chart_type, [])
        if not candles:
            continue

        # Use the latest candle (or the second-to-last if the last is incomplete, but in our sandbox data we use the last element)
        latest_candle = candles[-1]
        
        # Check body close confirmation: close below box lower boundary
        # Ensure we only check candles that occurred after trade entry time
        # entry_time is local text format YYYY-MM-DD HH:MM:SS
        entry_ts = parse_local_time(trade['entry_time'])
        if latest_candle['time'] <= entry_ts:
            continue

        if latest_candle['low'] <= calculated_sl:
            # STOP LOSS TRIGGERED!
            logger.info(f"Stop Loss Triggered: Candle low {latest_candle['low']} hit {calculated_sl} (3 points below box {box['lower_boundary']}) for Trade ID {trade_id}")
            
            # Save trigger candle parameters
            db.save_stop_loss_event(
                user_id=user_id,
                trade_id=trade_id,
                execution_id=exec_id,
                reference_box_id=box['id'],
                chart_type=box['chart_type'],
                instrument_symbol=box['instrument_symbol'],
                reference_box_upper=box['upper_boundary'],
                reference_box_lower=box['lower_boundary'],
                calculated_stop_loss=calculated_sl,
                trigger_candle_timestamp=latest_candle['time'],
                trigger_open=latest_candle['open'],
                trigger_high=latest_candle['high'],
                trigger_low=latest_candle['low'],
                trigger_close=latest_candle['close'],
                exit_price=None,
                pnl=None,
                broker_exit_order_id=None,
                exit_status='TRIGGERED',
                exit_reason='Candle touched 3 points below lower boundary'
            )
            
            # Execute Exit order
            execute_stop_loss_exit(user_id, trade, sl_event['id'], calculated_sl, latest_candle)

def execute_stop_loss_exit(user_id, trade, sl_id, stop_loss_price, trigger_candle):
    """Places exit MARKET order with retries."""
    db.update_stop_loss_status(user_id, sl_id, 'ORDER_SUBMITTED')
    
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
        logger.error(f"Error fetching token for exit symbol: {e}")

    # Fallback default token if not resolved
    if not token:
        token = "99919001"

    params = {
        'variety': 'NORMAL',
        'symbol': symbol,
        'token': token,
        'transaction_type': 'SELL',
        'exchange': 'BFO',
        'order_type': 'MARKET',
        'product_type': 'CARRYFORWARD',
        'quantity': trade['quantity']
    }

    max_attempts = STOP_LOSS_CONFIG['max_retries']
    delay = STOP_LOSS_CONFIG['retry_delay_seconds']

    for attempt in range(1, max_attempts + 1):
        logger.info(f"Submitting Stop Loss Exit (Attempt {attempt}/{max_attempts}) for Trade ID {trade['id']}")
        
        # Place exit order
        res = execute_order_api(params)
        
        if res['status']:
            broker_order_id = res['broker_order_id']
            
            # Verify fill status
            is_filled, fill_price, reason = verify_exit_order(broker_order_id, stop_loss_price)
            
            if is_filled is True:
                # Execution Completed Successfully! Update trade and event.
                update_trade_after_sl(
                    user_id=user_id,
                    trade_id=trade['id'],
                    sl_id=sl_id,
                    exit_price=fill_price,
                    broker_order_id=broker_order_id
                )
                return
            elif is_filled is False:
                # Explicit broker rejection - do NOT retry
                db.update_stop_loss_status(
                    user_id=user_id,
                    sl_id=sl_id,
                    status='REJECTED',
                    exit_reason=reason
                )
                logger.warning(f"Stop Loss Exit Rejected: Event ID {sl_id} rejected by broker: {reason}")
                return
            else:
                # Still open/pending
                db.update_stop_loss_status(
                    user_id=user_id,
                    sl_id=sl_id,
                    status='ORDER_SUBMITTED',
                    broker_exit_order_id=broker_order_id
                )
                return
        else:
            logger.warning(f"Stop Loss Exit failed on attempt {attempt}: {res['message']}")
            if attempt < max_attempts:
                time.sleep(delay)
            else:
                db.update_stop_loss_status(
                    user_id=user_id,
                    sl_id=sl_id,
                    status='FAILED',
                    exit_reason=f"Exit failed after {max_attempts} attempts: {res['message']}"
                )
                logger.error(f"Stop Loss Exit Failed: Event ID {sl_id} failed retry loops")

def execute_order_api(params):
    """Submits order to Angel One OpenAPI. Mocked in offline mode."""
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
    """Checks filled status of the exit order."""


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
        logger.error(f"OpenAPI exit status check error: {e}")

    return False, None, "OpenAPI order book query error or session invalid"

def update_trade_after_sl(user_id, trade_id, sl_id, exit_price, broker_order_id):
    """Updates trade table entry and logs exit audit events."""
    # Fetch trade details
    trade = db.get_trade_by_id(user_id, trade_id)
    if not trade:
        return

    # Calculate final P&L: (exit_price - entry_price) * quantity
    pnl = (exit_price - trade['entry_price']) * trade['quantity']

    # Update trade in database
    conn = db.get_db_connection()
    try:
        cursor = conn.cursor()
        exit_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        # 1. Update trades table
        cursor.execute("""
            UPDATE trades
            SET exit_price = ?, exit_time = ?, pnl = ?, status = 'STOP_LOSS_HIT', exit_reason = 'Stop Loss Hit'
            WHERE id = ? AND user_id = ?
        """, (exit_price, exit_time, pnl, trade_id, user_id))
        
        # 2. Add log entry to trade_logs
        desc = f"Stop Loss Triggered: Position closed at ₹{exit_price:.2f} (P&L: ₹{pnl:.2f})"
        cursor.execute("""
            INSERT INTO trade_logs (trade_id, event_type, description, price)
            VALUES (?, 'Stop Loss Hit', ?, ?)
        """, (trade_id, desc, exit_price))
        
        conn.commit()
    finally:
        conn.close()

    # 3. Update Stop Loss Event status to ORDER_COMPLETE
    db.update_stop_loss_status(
        user_id=user_id,
        sl_id=sl_id,
        status='ORDER_COMPLETE',
        exit_price=exit_price,
        pnl=pnl,
        broker_exit_order_id=broker_order_id,
        exit_reason='Stop Loss Filled'
    )
    
    logger.info(f"Monitoring Stopped: Trade ID {trade_id} closed by Stop Loss (P&L: ₹{pnl:.2f})")

def parse_local_time(str_val):
    if not str_val:
        return int(time.time())
    try:
        dt = datetime.strptime(str_val, '%Y-%m-%d %H:%M:%S')
        return int(dt.timestamp())
    except Exception:
        return int(time.time())

# ─── Required Strategy Functions Wrappers ───

def monitor_reference_box(user_id, trade_id):
    """Wraps db.get_reference_box_for_trade."""
    return db.get_reference_box_for_trade(user_id, trade_id)

def calculate_stop_loss(lower_boundary):
    """Calculates stop loss offset level."""
    return lower_boundary - STOP_LOSS_CONFIG['points_offset']

def validate_stop_loss(user_id, trade_id):
    """Verifies that the trade exists and is running."""
    trade = db.get_trade_by_id(user_id, trade_id)
    return trade is not None and trade['status'] == 'RUNNING'

def execute_stop_loss(user_id, trade_id):
    """Manually forces a stop-loss exit trigger for the trade."""
    trade = db.get_trade_by_id(user_id, trade_id)
    if not trade or trade['status'] != 'RUNNING':
        return False, "Trade is not running"

    sl_event = db.get_stop_loss_by_trade_id(user_id, trade_id)
    if not sl_event:
        return False, "Stop Loss monitoring event not initialized"

    logger.info(f"Manual Stop Loss Triggered: Trade ID {trade_id}")
    latest_candle = {'close': 0.0, 'open': 0.0, 'high': 0.0, 'low': 0.0}
    execute_stop_loss_exit(user_id, trade, sl_event['id'], sl_event['calculated_stop_loss'], latest_candle)
    return True, "Manual Stop Loss Exit Complete"

def retry_exit_order(user_id, sl_event_id):
    """Retries a failed exit order."""
    conn = db.get_db_connection()
    try:
        row = conn.execute("SELECT * FROM stop_loss_events WHERE id = ? AND user_id = ?", (sl_event_id, user_id)).fetchone()
        if not row:
            return False, "Stop Loss Event not found"
        event = dict(row)
    finally:
        conn.close()

    if event['exit_status'] not in ['FAILED', 'REJECTED']:
        return False, "Exit event is not in a retryable status"

    trade = db.get_trade_by_id(user_id, event['trade_id'])
    if not trade or trade['status'] != 'RUNNING':
        return False, "Associated trade is not running"

    logger.info(f"Manual Exit Retry Initiated: Event ID {sl_event_id}")
    latest_candle = {'close': 0.0, 'open': 0.0, 'high': 0.0, 'low': 0.0}
    execute_stop_loss_exit(user_id, trade, sl_event_id, event['calculated_stop_loss'], latest_candle)
    return True, "Exit retry initiated"

def expire_monitoring(user_id, trade_id):
    """Cancels active Stop Loss monitoring for a trade."""
    sl_event = db.get_stop_loss_by_trade_id(user_id, trade_id)
    if not sl_event:
        return False, "Monitoring event not found"

    db.update_stop_loss_status(user_id, sl_event['id'], 'CANCELLED', exit_reason='Monitoring manually cancelled')
    logger.info(f"Stop Loss Monitoring Cancelled: Trade ID {trade_id}")
    return True, "Monitoring cancelled"
