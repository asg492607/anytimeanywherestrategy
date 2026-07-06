import time
import logging
from datetime import datetime, timezone, timedelta
import db  # type: ignore

IST = timezone(timedelta(hours=5, minutes=30))
logger = logging.getLogger('reference_box_engine')

BOX_CONFIG = {
    'enabled': True,
    'max_active_boxes': 5,
    'color': 'rgba(41, 98, 255, 0.15)',
    'border_color': 'rgba(41, 98, 255, 0.6)',
    'auto_expiry_seconds': 3600  # Default: 1 hour
}

MONITORED_LEVELS = [
    '0.00', '0.236', '0.786', '1.14', '1.39', '0.382', '0.50', '0.618', 
    '1.00', '1.272', '1.41', '1.618', '2.00', '2.272', '2.414', '2.618', '3.00', '3.272',
    '3.414', '3.618', '4.00', '4.272', '4.414', '4.618'
]

# Maps monitored level names to their keys in strategy.fibonacci_engine.FIB_RATIOS
LEVEL_KEYS = {
    'LOW_TO_HIGH': {
        '0.00': 'f1_0_000',
        '0.236': 'f1_0_236',
        '0.786': 'f1_0_786',
        '1.14': 'f1_1_140',
        '1.39': 'f1_1_390',
        '0.382': 'level_0_382',
        '0.50': 'level_0_500',
        '0.618': 'level_0_618',
        '1.00': 'f1_1_000',
        '1.272': 'f1_1_272',
        '1.41': 'f1_1_414',
        '1.618': 'f1_1_618',
        '2.00': 'f1_2_000',
        '2.272': 'f1_2_272',
        '2.414': 'f1_2_414',
        '2.618': 'f1_2_618',
        '3.00': 'f1_3_000',
        '3.272': 'f1_3_272',
        '3.414': 'f1_3_414',
        '3.618': 'f1_3_618',
        '4.00': 'f1_4_000',
        '4.272': 'f1_4_272',
        '4.414': 'f1_4_414',
        '4.618': 'f1_4_618'
    },
    'HIGH_TO_LOW': {
        '0.00': 'f1_0_000',
        '0.236': 'f2_0_236',
        '0.786': 'f2_0_786',
        '1.14': 'f2_1_140',
        '1.39': 'f2_1_390',
        '0.382': 'level_0_382',
        '0.50': 'level_0_500',
        '0.618': 'level_0_618',
        '1.00': 'f1_1_000',
        '1.272': 'f2_1_272',
        '1.41': 'f2_1_414',
        '1.618': 'f2_1_618',
        '2.00': 'f2_2_000',
        '2.272': 'f2_2_272',
        '2.414': 'f2_2_414',
        '2.618': 'f2_2_618',
        '3.00': 'f2_3_000',
        '3.272': 'f2_3_272',
        '3.414': 'f2_3_414',
        '3.618': 'f2_3_618',
        '4.00': 'f2_4_000',
        '4.272': 'f2_4_272',
        '4.414': 'f2_4_414',
        '4.618': 'f2_4_618'
    }
}

def detect_fibonacci_cross(candle, levels):
    """
    Evaluates a candle against monitored Fibonacci levels.
    A cross occurs when the candle body (open -> close) spans/crosses a level.
    Returns a list of detected crosses.
    """
    crosses = []
    if not levels:
        return crosses

    # Check the entire candle range (wicks included) to detect touch/contact
    body_min = candle['low']
    body_max = candle['high']
    crossed_direction = 'UPWARD' if candle['close'] >= candle['open'] else 'DOWNWARD'

    for direction in ['LOW_TO_HIGH', 'HIGH_TO_LOW']:
        for lvl_name in MONITORED_LEVELS:
            lvl_key = LEVEL_KEYS[direction][lvl_name]
            lvl_price = levels.get(lvl_key)
            if lvl_price is None:
                continue

            # Verify if level price lies inside the candle body
            if body_min <= lvl_price <= body_max:
                crosses.append({
                    'level_name': lvl_name,
                    'price': float(lvl_price),
                    'fib_direction': direction,
                    'crossed_direction': crossed_direction
                })
    return crosses

def create_reference_box(user_id, chart_type, instrument_symbol, timeframe, fib_direction, fib_level, 
                         candle, crossed_direction, db_adapter=None):
    db_local = db_adapter or db
    """
    Creates and saves a new Reference Box.
    Upper Boundary = High of the crossing candle.
    Lower Boundary = Low of the crossing candle.
    """
    if not BOX_CONFIG['enabled']:
        return None

    upper = float(candle['high'])
    lower = float(candle['low'])
    timestamp = int(candle['time'])

    logger.info(f"Fibonacci Cross Detected: User {user_id} on {chart_type} level {fib_level} @ price {candle['close']}")
    
    # Save box to DB (handles duplicate check at DB level)
    box_id = db_local.save_reference_box(
        user_id=user_id,
        chart_type=chart_type,
        instrument_symbol=instrument_symbol,
        timeframe=timeframe,
        fib_direction=fib_direction,
        fib_level=fib_level,
        candle_timestamp=timestamp,
        candle_open=float(candle['open']),
        candle_high=float(candle['high']),
        candle_low=float(candle['low']),
        candle_close=float(candle['close']),
        upper_boundary=upper,
        lower_boundary=lower,
        box_status="ACTIVE",
        crossed_direction=crossed_direction
    )

    if box_id:
        logger.info(f"Reference Box Created: ID {box_id} on {chart_type} for level {fib_level}")
        
        # Replace previous active boxes for same level
        db_local.replace_active_boxes(user_id, chart_type, fib_level, box_id)
        logger.info(f"Reference Box Replaced: Older boxes for user {user_id} on {chart_type} level {fib_level} replaced by ID {box_id}")
        
        # Enforce maximum active boxes limit per chart
        enforce_max_boxes_limit(user_id, chart_type, db_adapter=db_local)

    return box_id

def enforce_max_boxes_limit(user_id, chart_type, db_adapter=None):
    """Auto-expires older active boxes on a chart if count exceeds max_active_boxes limit."""
    db_local = db_adapter or db
    active = db_local.get_active_boxes(user_id, chart_type)
    max_limit = BOX_CONFIG['max_active_boxes']
    if len(active) > max_limit:
        # Sort by timestamp ascending (oldest first)
        active_sorted = sorted(active, key=lambda x: x['candle_timestamp'])
        excess_count = len(active) - max_limit
        for i in range(excess_count):
            old_box = active_sorted[i]
            db_local.update_box_status(user_id, old_box['id'], 'EXPIRED')
            logger.info(f"Reference Box Expired (Limit reached): ID {old_box['id']} on {chart_type} auto-expired")

def check_and_expire_boxes(user_id, db_adapter=None):
    """Cycles active reference boxes and marks those exceeding auto_expiry_seconds as EXPIRED."""
    db_local = db_adapter or db
    active = db_local.get_active_boxes(user_id)
    now_ts = int(time.time())
    expiry_limit = BOX_CONFIG['auto_expiry_seconds']

    for box in active:
        age = now_ts - box['candle_timestamp']
        if age > expiry_limit:
            db_local.update_box_status(user_id, box['id'], 'EXPIRED')
            logger.info(f"Reference Box Expired: ID {box['id']} on {box['chart_type']} level {box['fib_level']} expired by timeout")

def process_latest_candles(user_id, chart_type, symbol, timeframe, candles, levels, db_adapter=None):
    """
    Processes incoming candle stream for Fibonacci crossings.
    Typically called in the background update loop when a candle close is simulated.
    """
    if not candles or not levels or not BOX_CONFIG['enabled']:
        return

    # Check the last 3 candles to capture fresh crossings reliably
    check_candles = candles[-3:]
    for c in check_candles:
        crosses = detect_fibonacci_cross(c, levels)
        for cross in crosses:
            create_reference_box(
                user_id=user_id,
                chart_type=chart_type,
                instrument_symbol=symbol,
                timeframe=timeframe,
                fib_direction=cross['fib_direction'],
                fib_level=cross['level_name'],
                candle=c,
                crossed_direction=cross['crossed_direction'],
                db_adapter=db_adapter
            )

# ─── Strategy Required Functions Wrappers ───

def validate_reference_box(user_id, box_id, db_adapter=None):
    """Loads a box and validates it against current chart values."""
    db_local = db_adapter or db
    return db_local.get_reference_box_by_id(user_id, box_id)

def get_active_boxes(user_id, chart_type=None, db_adapter=None):
    """Exposes db.get_active_boxes."""
    db_local = db_adapter or db
    # Run automatic expiry check first
    check_and_expire_boxes(user_id, db_adapter=db_local)
    return db_local.get_active_boxes(user_id, chart_type)

def invalidate_reference_box(user_id, box_id, db_adapter=None):
    """Updates a box status to INVALIDATED."""
    db_local = db_adapter or db
    db_local.update_box_status(user_id, box_id, 'INVALIDATED')
    logger.info(f"Reference Box Invalidated: ID {box_id} marked as INVALIDATED")

def update_reference_box(user_id, box_id, status, db_adapter=None):
    """Updates a box status (ACTIVE, REPLACED, INVALIDATED, EXPIRED)."""
    db_local = db_adapter or db
    db_local.update_box_status(user_id, box_id, status)
    logger.info(f"Reference Box Updated: ID {box_id} updated to {status}")

def save_reference_box(user_id, box_data, db_adapter=None):
    """Wraps save_reference_box for dict-based configurations."""
    db_local = db_adapter or db
    return db_local.save_reference_box(
        user_id=user_id,
        chart_type=box_data['chart_type'],
        instrument_symbol=box_data['instrument_symbol'],
        timeframe=box_data['timeframe'],
        fib_direction=box_data['fib_direction'],
        fib_level=box_data['fib_level'],
        candle_timestamp=box_data['candle_timestamp'],
        candle_open=box_data['candle_open'],
        candle_high=box_data['candle_high'],
        candle_low=box_data['candle_low'],
        candle_close=box_data['candle_close'],
        upper_boundary=box_data['upper_boundary'],
        lower_boundary=box_data['lower_boundary'],
        box_status=box_data.get('box_status', 'ACTIVE'),
        crossed_direction=box_data.get('crossed_direction', 'UPWARD')
    )

def load_reference_boxes(user_id, db_adapter=None):
    """Exposes all historical boxes for user audits."""
    db_local = db_adapter or db
    return db_local.load_all_boxes(user_id)
