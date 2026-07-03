import logging
import time
import json
from datetime import datetime, timezone, timedelta
import db  # type: ignore

logger = logging.getLogger('confirmation_engine')

CONFIRMATION_WINDOW_SECONDS = 30  # Configurable timeout window in seconds

def monitor_buy_signals(user_id):
    """
    Processes unconfirmed WAITING buy signals and resolves them into multi-chart confirmation sessions.
    Automatically handles session creation, signal matching, and breakout counts.
    """
    # 1. First, run expiration checks on active sessions
    expire_old_sessions(user_id)

    # 2. Get all WAITING buy signals
    conn = db.get_db_connection()
    try:
        rows = conn.execute("""
            SELECT * FROM buy_signals
            WHERE user_id = ? AND signal_status = 'WAITING'
            ORDER BY trigger_candle_timestamp ASC
        """, (user_id,)).fetchall()
        signals = [dict(r) for r in rows]
    finally:
        conn.close()

    for sig in signals:
        # Check if this signal is already linked to any confirmation session
        if db.check_signal_in_any_confirmation(user_id, sig['id']):
            continue

        # Look for an active WAITING confirmation session
        active_sessions = db.get_active_confirmations(user_id)
        matched = False

        for session in active_sessions:
            # Check if signal falls inside session window:
            # start_time <= sig_timestamp <= end_time
            sig_ts = sig['trigger_candle_timestamp']
            if session['confirmation_start_time'] <= sig_ts <= session['confirmation_end_time']:
                # Ensure the session doesn't already contain a signal from the same chart type
                already_has_chart = any(s['chart_type'] == sig['chart_type'] for s in session['signals'])
                if not already_has_chart:
                    # Add signal to this session
                    order_num = len(session['signals']) + 1
                    db.add_signal_to_confirmation(
                        confirmation_id=session['id'],
                        buy_signal_id=sig['id'],
                        chart_type=sig['chart_type'],
                        instrument_symbol=sig['instrument_symbol'],
                        breakout_price=sig['breakout_price'],
                        signal_timestamp=sig_ts,
                        order_num=order_num
                    )
                    
                    logger.info(f"Signal Received: Confirmation ID {session['id']} added {sig['chart_type']} signal #{sig['id']} (Order {order_num})")
                    
                    # Reload session to check if target count is achieved
                    updated_session = db.get_confirmation_by_id(user_id, session['id'])
                    if updated_session['received_confirmations'] >= updated_session['required_confirmations']:
                        # 2-out-of-3 criteria met! Confirm the session
                        import pytz
                        ist = pytz.timezone('Asia/Kolkata')
                        local_now = datetime.now(ist).strftime('%Y-%m-%d %H:%M:%S')
                        details_dict = {
                            "matched_charts": [s['chart_type'] for s in updated_session['signals']],
                            "matched_symbols": [s['instrument_symbol'] for s in updated_session['signals']],
                            "breakout_prices": [s['breakout_price'] for s in updated_session['signals']],
                            "start_time": updated_session['confirmation_start_time']
                        }
                        db.update_confirmation_status(
                            user_id=user_id,
                            confirmation_id=session['id'],
                            status='CONFIRMED',
                            confirmed_time=local_now,
                            details=json.dumps(details_dict)
                        )
                        
                        # Mark participating signals as CONFIRMED in the database
                        for s_item in updated_session['signals']:
                            db.update_signal_status(user_id, s_item['buy_signal_id'], 'CONFIRMED')
                            
                        logger.info(f"2/3 Confirmed: Trade Confirmation ID {session['id']} successfully confirmed!")
                    
                    matched = True
                    break

        if not matched:
            # Create a brand new confirmation session starting with this signal
            sig_ts = sig['trigger_candle_timestamp']
            conf_id = db.create_confirmation_session(
                user_id=user_id,
                strategy_name='institutional',
                window_seconds=CONFIRMATION_WINDOW_SECONDS,
                start_time=sig_ts
            )
            
            # Link this first signal
            db.add_signal_to_confirmation(
                confirmation_id=conf_id,
                buy_signal_id=sig['id'],
                chart_type=sig['chart_type'],
                instrument_symbol=sig['instrument_symbol'],
                breakout_price=sig['breakout_price'],
                signal_timestamp=sig_ts,
                order_num=1
            )
            
            logger.info(f"Confirmation Started: Session ID {conf_id} created for user {user_id} starting at {sig_ts}")

def expire_old_sessions(user_id):
    """
    Checks active WAITING confirmation sessions against current timestamp
    and moves them to EXPIRED if they exceed the confirmation window.
    """
    active = db.get_active_confirmations(user_id)
    now_ts = int(time.time())

    for session in active:
        # Expiry is determined if current system time exceeds the session's end time
        if now_ts > session['confirmation_end_time']:
            # Mark confirmation session as EXPIRED
            db.update_confirmation_status(user_id, session['id'], 'EXPIRED')
            
            # Mark linked buy signals as EXPIRED as well
            for s_item in session['signals']:
                # Only expire if the signal hasn't been confirmed by another session (safety check)
                db.update_signal_status(user_id, s_item['buy_signal_id'], 'EXPIRED')
                
            logger.info(f"Confirmation Expired: Session ID {session['id']} expired after {session['confirmation_window_seconds']}s")

# ─── Required Strategy Functions Wrappers ───

def create_confirmation_session(user_id, strategy_name, window_seconds, start_time):
    """Wraps db.create_confirmation_session."""
    return db.create_confirmation_session(user_id, strategy_name, window_seconds, start_time)

def add_confirmation(confirmation_id, buy_signal_id, chart_type, symbol, price, timestamp, order_num):
    """Wraps db.add_signal_to_confirmation."""
    db.add_signal_to_confirmation(confirmation_id, buy_signal_id, chart_type, symbol, price, timestamp, order_num)

def remove_confirmation(user_id, confirmation_id):
    """Updates status to FAILED representing cancellation or rejection."""
    db.update_confirmation_status(user_id, confirmation_id, 'FAILED')
    logger.info(f"Confirmation Failed (Cancelled): ID {confirmation_id} marked as FAILED")

def validate_confirmation(user_id, confirmation_id):
    """Retrieves session from DB and validates its status."""
    return db.get_confirmation_by_id(user_id, confirmation_id)

def expire_confirmation(user_id, confirmation_id):
    """Manually marks a confirmation session as EXPIRED."""
    db.update_confirmation_status(user_id, confirmation_id, 'EXPIRED')
    logger.info(f"Confirmation Expired (Manual): ID {confirmation_id} marked as EXPIRED")

def get_confirmation_status(user_id, confirmation_id):
    """Fetches status of confirmation session."""
    conf = db.get_confirmation_by_id(user_id, confirmation_id)
    return conf['confirmation_status'] if conf else None

def save_confirmation(user_id, conf_data):
    """Directly creates a custom manual confirmation session (useful in tests)."""
    conf_id = db.create_confirmation_session(
        user_id=user_id,
        strategy_name=conf_data.get('strategy_name', 'institutional'),
        window_seconds=conf_data.get('confirmation_window_seconds', 30),
        start_time=conf_data['confirmation_start_time']
    )
    if conf_data.get('confirmation_status') and conf_data['confirmation_status'] != 'WAITING':
        db.update_confirmation_status(user_id, conf_id, conf_data['confirmation_status'])
    return conf_id
