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

def monitor_running_trades(user_id, candles_dict, db_adapter=None, execute_adapter=None):
    """
    Monitors all active RUNNING trades. Evaluates latest candle closes
    against reference box lower boundaries to trigger automatic Stop Loss exits.
    """
    db_local = db_adapter or db
    if not STOP_LOSS_CONFIG['enabled']:
        return

    running_trades = db_local.get_running_trades(user_id)
    if not running_trades:
        return

    for trade in running_trades:
        trade_id = trade['id']
        
        # 1. Fetch active Reference Box for this trade
        box = db_local.get_reference_box_for_trade(user_id, trade_id)
        if not box:
            logger.warning(f"Stop Loss monitor: Could not resolve Reference Box for Trade ID {trade_id}")
            continue

        # 2. Ensure a Stop Loss Event record exists in MONITORING state
        sl_event = db_local.get_stop_loss_by_trade_id(user_id, trade_id)
        calculated_sl = box['lower_boundary'] - STOP_LOSS_CONFIG['points_offset']
        
        exec_record = db_local.get_execution_for_trade(user_id, trade_id)
        exec_id = exec_record['id'] if exec_record else None

        if not sl_event:
            event_id = db_local.save_stop_loss_event(
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
            sl_event = db_local.get_stop_loss_by_trade_id(user_id, trade_id)

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
            db_local.save_stop_loss_event(
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
            execute_stop_loss_exit(user_id, trade, sl_event['id'], calculated_sl, latest_candle, db_adapter=db_local, execute_adapter=execute_adapter)

def execute_stop_loss_exit(user_id, trade, sl_id, stop_loss_price, trigger_candle, db_adapter=None, execute_adapter=None):
    """Places exit MARKET order using perform_manual_trade_close helper."""
    db_local = db_adapter or db
    try:
        from strategy.execution_engine import perform_manual_trade_close
        pnl = perform_manual_trade_close(
            user_id=user_id,
            trade_id=trade['id'],
            exit_price=stop_loss_price,
            exit_reason='Stop Loss Triggered',
            db_adapter=db_local,
            execute_adapter=execute_adapter
        )
        
        exec_record = db_local.get_execution_for_trade(user_id, trade['id'])
        broker_order_id = exec_record['broker_order_id'] if exec_record else f"SIM_SL_{trade['id']}"
        
        db_local.update_stop_loss_status(
            user_id=user_id,
            sl_id=sl_id,
            status='COMPLETE',
            exit_price=stop_loss_price,
            broker_exit_order_id=broker_order_id
        )
    except Exception as e:
        logger.error(f"Stop Loss Exit execution failed: {e}")
        db_local.update_stop_loss_status(
            user_id=user_id,
            sl_id=sl_id,
            status='FAILED',
            exit_reason=str(e)
        )

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
    if broker_order_id and str(broker_order_id).startswith("SIM_"):
        return True, requested_price, None

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

def update_trade_after_sl(user_id, trade_id, sl_id, exit_price, broker_order_id, db_adapter=None):
    """Updates trade table entry and logs exit audit events."""
    db_local = db_adapter or db
    # Fetch trade details
    trade = db_local.get_trade_by_id(user_id, trade_id)
    if not trade:
        return

    # Calculate final P&L: (exit_price - entry_price) * quantity
    pnl = (exit_price - trade['entry_price']) * trade['quantity']

    # Update trade in database
    conn = db_local.get_db_connection()
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
    db_local.update_stop_loss_status(
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

def monitor_reference_box(user_id, trade_id, db_adapter=None):
    """Wraps db.get_reference_box_for_trade."""
    db_local = db_adapter or db
    return db_local.get_reference_box_for_trade(user_id, trade_id)

def calculate_stop_loss(lower_boundary):
    """Calculates stop loss offset level."""
    return lower_boundary - STOP_LOSS_CONFIG['points_offset']

def validate_stop_loss(user_id, trade_id, db_adapter=None):
    """Verifies that the trade exists and is running."""
    db_local = db_adapter or db
    trade = db_local.get_trade_by_id(user_id, trade_id)
    return trade is not None and trade['status'] == 'RUNNING'

def execute_stop_loss(user_id, trade_id, db_adapter=None, execute_adapter=None):
    """Manually forces a stop-loss exit trigger for the trade."""
    db_local = db_adapter or db
    trade = db_local.get_trade_by_id(user_id, trade_id)
    if not trade or trade['status'] != 'RUNNING':
        return False, "Trade is not running"

    sl_event = db_local.get_stop_loss_by_trade_id(user_id, trade_id)
    if not sl_event:
        return False, "Stop Loss monitoring event not initialized"

    logger.info(f"Manual Stop Loss Triggered: Trade ID {trade_id}")
    latest_candle = {'close': 0.0, 'open': 0.0, 'high': 0.0, 'low': 0.0}
    execute_stop_loss_exit(user_id, trade, sl_event['id'], sl_event['calculated_stop_loss'], latest_candle, db_adapter=db_local, execute_adapter=execute_adapter)
    return True, "Manual Stop Loss Exit Complete"

def retry_exit_order(user_id, sl_event_id, db_adapter=None, execute_adapter=None):
    """Retries a failed exit order."""
    db_local = db_adapter or db
    conn = db_local.get_db_connection()
    try:
        row = conn.execute("SELECT * FROM stop_loss_events WHERE id = ? AND user_id = ?", (sl_event_id, user_id)).fetchone()
        if not row:
            return False, "Stop Loss Event not found"
        event = dict(row)
    finally:
        conn.close()

    if event['exit_status'] not in ['FAILED', 'REJECTED']:
        return False, "Exit event is not in a retryable status"

    trade = db_local.get_trade_by_id(user_id, event['trade_id'])
    if not trade or trade['status'] != 'RUNNING':
        return False, "Associated trade is not running"

    logger.info(f"Manual Exit Retry Initiated: Event ID {sl_event_id}")
    latest_candle = {'close': 0.0, 'open': 0.0, 'high': 0.0, 'low': 0.0}
    execute_stop_loss_exit(user_id, trade, sl_event_id, event['calculated_stop_loss'], latest_candle, db_adapter=db_local, execute_adapter=execute_adapter)
    return True, "Exit retry initiated"

def expire_monitoring(user_id, trade_id, db_adapter=None):
    """Cancels active Stop Loss monitoring for a trade."""
    db_local = db_adapter or db
    sl_event = db_local.get_stop_loss_by_trade_id(user_id, trade_id)
    if not sl_event:
        return False, "Monitoring event not found"

    db_local.update_stop_loss_status(user_id, sl_event['id'], 'CANCELLED', exit_reason='Monitoring manually cancelled')
    logger.info(f"Stop Loss Monitoring Cancelled: Trade ID {trade_id}")
    return True, "Monitoring cancelled"
