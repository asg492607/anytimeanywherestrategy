import logging

logger = logging.getLogger('feature_extractor')

def detect_fib_cross(candle, box):
    """Candle body or wick crosses the Fib line."""
    line = box['upper_boundary'] # Assuming upper_boundary is the fib line
    return candle['low'] <= line <= candle['high']

def detect_fib_touch(candle, box, tolerance=1.0):
    """Candle touches the Fib line within a small tolerance."""
    line = box['upper_boundary']
    return abs(candle['low'] - line) <= tolerance or abs(candle['high'] - line) <= tolerance

def detect_rejection_red(candle, box, tolerance=1.0):
    """Touches Fib line and forms a red candle (close < open)."""
    if not detect_fib_touch(candle, box, tolerance):
        return False
    return candle['close'] < candle['open']

def detect_sustain_green(candle, box, tolerance=1.0):
    """Touches Fib line and forms a green candle (close > open)."""
    if not detect_fib_touch(candle, box, tolerance):
        return False
    return candle['close'] > candle['open']

def detect_high_break_and_close_above(candle, ref_high):
    return candle['high'] > ref_high and candle['close'] > ref_high

def detect_low_break_and_close_below(candle, ref_low):
    return candle['low'] < ref_low and candle['close'] < ref_low

def extract_facts(candle, box, ref_candle=None):
    """
    Converts a candle into a dictionary of atomic boolean facts.
    """
    facts = {
        'fib_cross': False,
        'fib_touch': False,
        'rejection_red': False,
        'sustain_green': False,
        'high_break_and_close_above': False,
        'low_break_and_close_below': False,
        'fib_cross_or_touch_rejection': False,
        'fib_cross_or_touch_sustain': False
    }
    
    if not box:
        return facts
        
    facts['fib_cross'] = detect_fib_cross(candle, box)
    facts['fib_touch'] = detect_fib_touch(candle, box)
    facts['rejection_red'] = detect_rejection_red(candle, box)
    facts['sustain_green'] = detect_sustain_green(candle, box)
    
    # Composite facts used in JSON rules
    facts['fib_cross_or_touch_rejection'] = facts['fib_cross'] or facts['rejection_red']
    facts['fib_cross_or_touch_sustain'] = facts['fib_cross'] or facts['sustain_green']
    
    if ref_candle:
        facts['high_break_and_close_above'] = detect_high_break_and_close_above(candle, ref_candle['high'])
        facts['low_break_and_close_below'] = detect_low_break_and_close_below(candle, ref_candle['low'])
        
    return facts
