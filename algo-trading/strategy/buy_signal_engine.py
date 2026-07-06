import logging
import time
import db  # type: ignore

logger = logging.getLogger('buy_signal_engine')

SIGNAL_CONFIG = {
    'enabled': True,
    'expiry_seconds': 3600  # Default: 1 hour expiration for signals
}

# DEAD CODE REMOVED
# The legacy breakout and confirmation states are no longer used.
# The new stateless Rule Engine automatically evaluates and triggers executions instantly.

def detect_breakout(candle, box):
    pass

def detect_rejection(candle, box):
    pass

def create_buy_signal(user_id, box, candle):
    pass

def reject_breakout(user_id, box, candle):
    pass

def check_and_expire_signals(user_id):
    pass

from strategy.feature_extractor import extract_facts
from strategy.rule_engine import get_engine
from strategy.execution_engine import execute_rule_signal

def monitor_reference_boxes(user_id, candles_dict, db_adapter=None, execute_adapter=None):
    """
    Processes the latest candles for SENSEX, PE, and CE.
    Converts them into facts and feeds them to the stateless Rule Engine.
    """
    db_local = db_adapter or db
    if not SIGNAL_CONFIG['enabled']:
        return

    # Fetch active boxes (this tells us the Fib lines and Reference Highs/Lows)
    active_boxes = db_local.get_active_boxes(user_id)
    
    # Map boxes by chart type for quick access
    boxes_by_chart = {box['chart_type']: box for box in active_boxes}
    
    # We will evaluate the rule engine on the absolute latest candle
    # In a live system, this runs every tick/second.
    facts_by_chart = {}
    
    for chart_type in ['SENSEX', 'PE', 'CE']:
        candles = candles_dict.get(chart_type, [])
        if not candles:
            continue
            
        latest_candle = sorted(candles, key=lambda x: x['time'])[-1]
        box = boxes_by_chart.get(chart_type)
        
        # Reconstruct the reference candle from DB fields for High/Low break logic
        ref_candle = None
        if box:
            ref_candle = {
                'high': box['candle_high'],
                'low': box['candle_low']
            }
            
        # Extract facts (e.g. fib_cross, high_break)
        facts = extract_facts(latest_candle, box, ref_candle)
        facts_by_chart[chart_type] = facts

    # If we have no boxes at all, there's nothing to evaluate
    if not boxes_by_chart:
        return

    # Feed all facts into the rule engine
    engine = get_engine()
    signals = engine.evaluate_all(facts_by_chart)
    
    # Pass raw signals through the Decision Engine
    from strategy.decision_engine import get_decision_engine
    decision_engine = get_decision_engine()
    approved_signals = decision_engine.evaluate_decisions(user_id, signals)
    
    # Execute any approved signals
    for signal in approved_signals:
        rule_id = signal['rule_id']
        action = signal['action']
        
        # Determine which symbol to buy and the current price
        target_chart = 'CE' if action == 'BUY_CE' else 'PE'
        
        # We need the symbol string (e.g. SENSEX24JUL80000CE) to execute
        target_box = boxes_by_chart.get(target_chart)
        if not target_box:
            logger.error(f"Rule {rule_id} triggered {action}, but no active {target_chart} box exists to find the symbol.")
            continue
            
        symbol = target_box['instrument_symbol']
        
        # Get the latest price for the target option
        price = candles_dict.get(target_chart, [])[-1]['close'] if candles_dict.get(target_chart) else 0.0
        
        # Fire to Execution Engine (Bypasses old DB waiting states!)
        execute_rule_signal(user_id, rule_id, action, symbol, price, db_adapter=db_adapter, execute_adapter=execute_adapter)

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
