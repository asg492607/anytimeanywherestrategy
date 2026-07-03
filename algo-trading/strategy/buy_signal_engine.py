import logging
import time
import db  # type: ignore

logger = logging.getLogger('buy_signal_engine')

SIGNAL_CONFIG = {
    'enabled': True,
    'expiry_seconds': 3600  # Default: 1 hour expiration for signals
}

def detect_breakout(candle, box):
    """
    Checks if a candle is a valid body breakout above the reference box upper boundary.
    Wick-only breakouts (high above, but close below/equal) are not buy signals.
    """
    upper_boundary = box['upper_boundary']
    
    # Body breakout: close > upper_boundary, and body must cross (open <= upper_boundary)
    is_breakout = (candle['close'] > upper_boundary) and (candle['open'] <= upper_boundary)
    return is_breakout

def detect_rejection(candle, box):
    """
    Checks if a candle touches either the upper or lower boundary,
    but gets rejected and reverses without closing beyond the boundary.
    """
    upper_boundary = box['upper_boundary']
    lower_boundary = box['lower_boundary']
    
    # Upper boundary rejection
    is_upper_touch = (candle['high'] >= upper_boundary)
    is_upper_close_inside = (candle['close'] <= upper_boundary)
    upper_rejection = is_upper_touch and is_upper_close_inside
    
    # Lower boundary rejection
    is_lower_touch = (candle['low'] <= lower_boundary)
    is_lower_close_inside = (candle['close'] >= lower_boundary)
    lower_rejection = is_lower_touch and is_lower_close_inside
    
    return upper_rejection or lower_rejection

def create_buy_signal(user_id, box, candle):
    """
    Creates or updates a Buy Signal with WAITING status (awaiting multi-chart confirmation).
    """
    # Check if a signal already exists for this box
    existing = db.get_buy_signal_by_box(user_id, box['id'])
    
    rejection_cnt = existing['rejection_count'] if existing else 0
    
    # Check if we already have an active/waiting signal to prevent duplicates
    if existing and existing['signal_status'] in ['WAITING', 'CONFIRMED']:
        logger.info(f"Duplicate Buy Signal Prevented: Box {box['id']} already has active signal ID {existing['id']}")
        return existing['id']

    # Insert or update to WAITING status
    sig_id = db.save_buy_signal(
        user_id=user_id,
        reference_box_id=box['id'],
        chart_type=box['chart_type'],
        instrument_symbol=box['instrument_symbol'],
        signal_status='WAITING',
        trigger_candle_timestamp=int(candle['time']),
        trigger_open=float(candle['open']),
        trigger_high=float(candle['high']),
        trigger_low=float(candle['low']),
        trigger_close=float(candle['close']),
        breakout_price=float(candle['close']),
        breakout_boundary=float(box['upper_boundary']),
        rejection_count=rejection_cnt
    )
    
    logger.info(f"Buy Signal Created: ID {sig_id} (WAITING) on {box['chart_type']} level {box['fib_level']} @ breakout price {candle['close']}")
    return sig_id

def reject_breakout(user_id, box, candle):
    """
    Handles a breakout rejection. Increments rejection count, keeps box active.
    """
    existing = db.get_buy_signal_by_box(user_id, box['id'])
    
    # Ignore if already confirmed or waiting breakout
    if existing and existing['signal_status'] in ['WAITING', 'CONFIRMED']:
        return existing['id']
        
    new_rejections = (existing['rejection_count'] + 1) if existing else 1
    
    sig_id = db.save_buy_signal(
        user_id=user_id,
        reference_box_id=box['id'],
        chart_type=box['chart_type'],
        instrument_symbol=box['instrument_symbol'],
        signal_status='REJECTED',
        trigger_candle_timestamp=int(candle['time']),
        trigger_open=float(candle['open']),
        trigger_high=float(candle['high']),
        trigger_low=float(candle['low']),
        trigger_close=float(candle['close']),
        rejection_count=new_rejections
    )
    
    logger.info(f"Breakout Rejected: Box {box['id']} on {box['chart_type']} touched boundary {box['upper_boundary']} (High: {candle['high']}) but closed inside {candle['close']}. Total Rejections: {new_rejections}")
    return sig_id

def check_and_expire_signals(user_id):
    """Marks WAITING signals older than expiry_seconds as EXPIRED."""
    active = db.get_active_signals(user_id)
    now_ts = int(time.time())
    expiry_limit = SIGNAL_CONFIG['expiry_seconds']
    
    for sig in active:
        # Check signal age based on trigger_candle_timestamp or created_at
        # We will check based on trigger_candle_timestamp
        if sig['trigger_candle_timestamp']:
            age = now_ts - sig['trigger_candle_timestamp']
            if age > expiry_limit and sig['signal_status'] == 'WAITING':
                db.update_signal_status(user_id, sig['id'], 'EXPIRED')
                logger.info(f"Signal Expired: ID {sig['id']} timed out without confirmation")

def monitor_reference_boxes(user_id, candles_dict):
    """
    Processes all ACTIVE reference boxes and matches them against latest candles
    to detect breakouts or boundary rejections.
    """
    if not SIGNAL_CONFIG['enabled']:
        return

    # Fetch active boxes
    active_boxes = db.get_active_boxes(user_id)
    
    for box in active_boxes:
        chart_key = box['chart_type'] # SPOT, CALL, PUT
        candles = candles_dict.get(chart_key, [])
        if not candles:
            continue
            
        # Filter candles newer than the reference box creation candle
        newer_candles = [c for c in candles if int(c['time']) > box['candle_timestamp']]
        
        # Sort chronologically
        newer_candles_sorted = sorted(newer_candles, key=lambda x: x['time'])
        
        for candle in newer_candles_sorted:
            # First check for body breakout
            if detect_breakout(candle, box):
                create_buy_signal(user_id, box, candle)
                break  # Stop processing newer candles for this box since breakout triggered
                
            # Then check for boundary rejection
            elif detect_rejection(candle, box):
                reject_breakout(user_id, box, candle)

# ─── Strategy Required Wrappers ───

def validate_breakout(user_id, signal_id):
    """Exposes get_buy_signal_by_id."""
    return db.get_buy_signal_by_id(user_id, signal_id)

def expire_signal(user_id, signal_id):
    """Manually expires a signal."""
    db.update_signal_status(user_id, signal_id, 'EXPIRED')
    logger.info(f"Signal Expired: ID {signal_id} marked as EXPIRED")

def get_active_signals(user_id, chart_type=None):
    """Returns active (WAITING or CONFIRMED) signals."""
    check_and_expire_signals(user_id)
    return db.get_active_signals(user_id, chart_type)

def save_buy_signal(user_id, sig_data):
    """Wraps save_buy_signal for dict-based parameters."""
    return db.save_buy_signal(
        user_id=user_id,
        reference_box_id=sig_data['reference_box_id'],
        chart_type=sig_data['chart_type'],
        instrument_symbol=sig_data['instrument_symbol'],
        signal_status=sig_data.get('signal_status', 'WAITING'),
        trigger_candle_timestamp=sig_data.get('trigger_candle_timestamp'),
        trigger_open=sig_data.get('trigger_open'),
        trigger_high=sig_data.get('trigger_high'),
        trigger_low=sig_data.get('trigger_low'),
        trigger_close=sig_data.get('trigger_close'),
        breakout_price=sig_data.get('breakout_price'),
        breakout_boundary=sig_data.get('breakout_boundary'),
        rejection_count=sig_data.get('rejection_count', 0)
    )
