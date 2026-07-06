import logging
import json
import db  # type: ignore

logger = logging.getLogger('notification_engine')

def trigger_notification(user_id, event_type, title, description, severity='INFO', trade_id=None, metadata=None):
    """Saves notification event to strategy_events table and prints log summary."""
    # 1. Resolve active session and db adapter
    import sys
    db_local = db
    if 'simulate_db' in sys.modules:
        import simulate_db
        db_local = simulate_db

    session = db_local.get_active_strategy_session(user_id, 'institutional')
    session_id = session['id'] if session else None
    
    metadata_json = json.dumps(metadata) if metadata else None
    
    # 2. Persist notification as structured strategy event
    event_id = db_local.log_strategy_event(
        user_id=user_id,
        session_id=session_id,
        trade_id=trade_id,
        event_type=event_type,
        event_title=title,
        event_description=description,
        event_source='NotificationEngine',
        severity=severity,
        metadata_json=metadata_json
    )
    
    logger.info(f"[{severity}] Notification Triggered ({event_type}): {title} - {description}")
    return event_id

def notify_box_created(user_id, chart_type, level, price):
    trigger_notification(
        user_id=user_id,
        event_type='BOX_CREATED',
        title=f"Reference Box Created ({chart_type})",
        description=f"New box formed interacting with Fibonacci Level {level} at ₹{price:.2f}",
        severity='INFO'
    )

def notify_buy_signal(user_id, chart_type, boundary, price):
    trigger_notification(
        user_id=user_id,
        event_type='BUY_SIGNAL',
        title=f"Buy Signal Generated ({chart_type})",
        description=f"Breakout candle close above boundary {boundary} at price ₹{price:.2f}",
        severity='INFO'
    )

def notify_confirmation_complete(user_id, session_id, matched_charts):
    charts_str = ", ".join(matched_charts)
    trigger_notification(
        user_id=user_id,
        event_type='CONFIRMATION_COMPLETE',
        title="2-out-of-3 Confirmation Complete",
        description=f"Multi-chart confirmation conditions met on: {charts_str}",
        severity='INFO',
        metadata={'session_id': session_id}
    )

def notify_trade_executed(user_id, trade_id, symbol, qty, price):
    trigger_notification(
        user_id=user_id,
        event_type='TRADE_EXECUTED',
        title=f"Order Executed: Trade #{trade_id}",
        description=f"Bought {qty} contracts of {symbol} at entry price ₹{price:.2f}",
        severity='INFO',
        trade_id=trade_id
    )

def notify_stop_loss_hit(user_id, trade_id, symbol, exit_price, loss_val):
    trigger_notification(
        user_id=user_id,
        event_type='STOP_LOSS_HIT',
        title=f"Stop Loss Hit: Trade #{trade_id}",
        description=f"Exited {symbol} below reference box at ₹{exit_price:.2f}. Realised Loss: -₹{loss_val:.2f}",
        severity='WARNING',
        trade_id=trade_id
    )

def notify_target_hit(user_id, trade_id, symbol, exit_price, profit_val):
    trigger_notification(
        user_id=user_id,
        event_type='TARGET_HIT',
        title=f"Target Hit: Trade #{trade_id}",
        description=f"Exited {symbol} touching Fibonacci target price at ₹{exit_price:.2f}. Realised Profit: +₹{profit_val:.2f}",
        severity='INFO',
        trade_id=trade_id
    )

def notify_broker_disconnected(user_id, error_msg):
    trigger_notification(
        user_id=user_id,
        event_type='BROKER_DISCONNECTED',
        title="Broker Disconnected",
        description=f"Connection lost to Angel One SmartAPI: {error_msg}",
        severity='CRITICAL'
    )

def notify_websocket_reconnected(user_id):
    trigger_notification(
        user_id=user_id,
        event_type='WEBSOCKET_RECONNECTED',
        title="WebSocket Reconnected",
        description="Market tick feed connection successfully re-established.",
        severity='INFO'
    )
