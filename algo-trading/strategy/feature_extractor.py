import logging

logger = logging.getLogger('feature_extractor')

def extract_signals(candle, fib_level=None, ref_high=None, ref_low=None, tolerance=1.0):
    """
    Extracts atomic signals (S1-S8) for a single candle.
    fib_level: the exact price of the fib line being monitored.
    ref_high/ref_low: the high/low of the active reference box, if any.
    """
    signals = {
        'S1_FIB_CROSS': False,
        'S2_FIB_TOUCH': False,
        'S3_HIGH_BREAK': False,
        'S4_LOW_BREAK': False,
        'S5_CLOSE_ABOVE': False,
        'S6_CLOSE_BELOW': False,
        'S7_GREEN_SUSTAIN': False,
        'S8_RED_REJECTION': False,
        'bullish_state': False,
        'bearish_state': False
    }
    
    op = float(candle['open'])
    hi = float(candle['high'])
    lo = float(candle['low'])
    cl = float(candle['close'])
    
    is_green = cl > op
    is_red = cl < op

    if fib_level is not None:
        fib = float(fib_level)
        # S1: Fib Cross
        # Candle crosses the Fib line with its body or gap (Open < Fib and Close > Fib) OR (Open > Fib and Close < Fib)
        # We also count a gap cross, but strictly body cross is:
        if (op < fib and cl > fib) or (op > fib and cl < fib):
            signals['S1_FIB_CROSS'] = True
        
        # S2: Fib Touch
        # Wick touches but body doesn't necessarily cross
        # Alternatively, if low <= fib <= high
        if lo <= fib <= hi:
            if not signals['S1_FIB_CROSS']:
                signals['S2_FIB_TOUCH'] = True
            
        # S7: Green Sustain Candle
        # After touching Fib, candle is Green and sustains above the Fib level
        if signals['S2_FIB_TOUCH'] and is_green and cl > fib:
            signals['S7_GREEN_SUSTAIN'] = True
            
        # S8: Red Rejection Candle
        # After touching Fib, candle becomes Red and rejects the Fib level
        if signals['S2_FIB_TOUCH'] and is_red and cl < fib:
            signals['S8_RED_REJECTION'] = True

    if ref_high is not None:
        # S3: High Break
        if hi > ref_high:
            signals['S3_HIGH_BREAK'] = True
        # S5: Close Above
        if cl > ref_high:
            signals['S5_CLOSE_ABOVE'] = True
            signals['bullish_state'] = True

    if ref_low is not None:
        # S4: Low Break
        if lo < ref_low:
            signals['S4_LOW_BREAK'] = True
        # S6: Close Below
        if cl < ref_low:
            signals['S6_CLOSE_BELOW'] = True
            signals['bearish_state'] = True

    return signals

def extract_facts(candle, box, ref_candle):
    """
    Adapter function that bridges the engine's box/ref_candle format
    to the extract_signals function.
    """
    fib_level = box['fib_level'] if box else None
    ref_high = ref_candle['high'] if ref_candle else None
    ref_low = ref_candle['low'] if ref_candle else None
    
    return extract_signals(candle, fib_level, ref_high, ref_low)
