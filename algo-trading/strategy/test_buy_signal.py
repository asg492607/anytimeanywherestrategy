import os
import sys
import unittest
import tempfile
import uuid
import time

# Add parent directory to sys.path so we can import modules
ALGO_TRADING_DIR = r"c:\Users\Atharva\OneDrive\Desktop\algo_anytimeanywhere\algo-trading"
if ALGO_TRADING_DIR not in sys.path:
    sys.path.insert(0, ALGO_TRADING_DIR)

import db  # type: ignore
from strategy.reference_box_engine import create_reference_box  # type: ignore
from strategy.buy_signal_engine import (  # type: ignore
    detect_breakout,
    detect_rejection,
    create_buy_signal,
    reject_breakout,
    check_and_expire_signals,
    get_active_signals,
    SIGNAL_CONFIG
)

class TestBuySignalEngine(unittest.TestCase):

    def setUp(self):
        # Setup clean SQLite testing DB
        self.db_path = os.path.join(tempfile.gettempdir(), f"test_buy_sig_{uuid.uuid4().hex}.db")
        db.DB_PATH = self.db_path
        db.init_db()

        # Mock active user
        self.user_id = db.create_user("User Epsilon", "epsilon@example.com", "password")
        
        # Reset config to defaults
        SIGNAL_CONFIG['enabled'] = True
        SIGNAL_CONFIG['expiry_seconds'] = 3600

        # Create a mock active Reference Box to reference in signals
        candle_box = {'time': int(time.time()) - 100, 'open': 100.0, 'high': 120.0, 'low': 90.0, 'close': 110.0}
        self.box_id = create_reference_box(
            user_id=self.user_id,
            chart_type="CALL",
            instrument_symbol="SENSEX26700CE",
            timeframe="3m",
            fib_direction="LOW_TO_HIGH",
            fib_level="0.786",
            candle=candle_box,
            crossed_direction="UPWARD"
        )
        self.assertIsNotNone(self.box_id)
        self.box = db.get_reference_box_by_id(self.user_id, self.box_id)

    def tearDown(self):
        if os.path.exists(self.db_path):
            try:
                os.remove(self.db_path)
            except Exception as e:
                print(f"Cleanup warning: {e}")

    def test_breakout_detection_rules(self):
        # Box upper boundary is 120.0 (high of the crossing candle)
        # 1. Valid body breakout (open <= 120, close > 120)
        candle_valid = {'time': int(time.time()), 'open': 115.0, 'high': 125.0, 'low': 110.0, 'close': 122.0}
        self.assertTrue(detect_breakout(candle_valid, self.box))
        self.assertFalse(detect_rejection(candle_valid, self.box))

        # 2. Wick-only breakout (high > 120, but close <= 120)
        candle_wick_only = {'time': int(time.time()), 'open': 110.0, 'high': 124.0, 'low': 105.0, 'close': 119.0}
        self.assertFalse(detect_breakout(candle_wick_only, self.box))
        self.assertTrue(detect_rejection(candle_wick_only, self.box))

        # 3. Completely inside box (high < 120, close < 120)
        candle_inside = {'time': int(time.time()), 'open': 105.0, 'high': 115.0, 'low': 100.0, 'close': 110.0}
        self.assertFalse(detect_breakout(candle_inside, self.box))
        self.assertFalse(detect_rejection(candle_inside, self.box))

    def test_buy_signal_creation(self):
        candle_trigger = {'time': int(time.time()), 'open': 115.0, 'high': 125.0, 'low': 110.0, 'close': 122.0}
        
        # Create Buy Signal
        sig_id = create_buy_signal(self.user_id, self.box, candle_trigger)
        self.assertIsNotNone(sig_id)

        # Verify database contents
        sig = db.get_buy_signal_by_id(self.user_id, sig_id)
        self.assertEqual(sig['reference_box_id'], self.box_id)
        self.assertEqual(sig['signal_status'], 'WAITING')
        self.assertEqual(sig['breakout_price'], 122.0)
        self.assertEqual(sig['breakout_boundary'], 120.0)

    def test_duplicate_signal_prevention(self):
        candle_trigger = {'time': int(time.time()), 'open': 115.0, 'high': 125.0, 'low': 110.0, 'close': 122.0}
        
        # Create signal
        id1 = create_buy_signal(self.user_id, self.box, candle_trigger)
        # Try creating identical signal again for the same box
        id2 = create_buy_signal(self.user_id, self.box, candle_trigger)

        # Both IDs should be the same and database should contain only 1 record
        self.assertEqual(id1, id2)
        signals = db.load_all_signals(self.user_id)
        self.assertEqual(len(signals), 1)

    def test_rejection_logic_rejection_count(self):
        candle_rejection = {'time': int(time.time()), 'open': 110.0, 'high': 124.0, 'low': 105.0, 'close': 119.0}

        # 1. Trigger rejection
        reject_breakout(self.user_id, self.box, candle_rejection)
        
        # 2. Check record in DB is status REJECTED and rejection_count is 1
        sig = db.get_buy_signal_by_box(self.user_id, self.box_id)
        self.assertIsNotNone(sig)
        self.assertEqual(sig['signal_status'], 'REJECTED')
        self.assertEqual(sig['rejection_count'], 1)

        # 3. Trigger second rejection
        reject_breakout(self.user_id, self.box, candle_rejection)
        
        # 4. Rejection count should now be 2
        sig_updated = db.get_buy_signal_by_box(self.user_id, self.box_id)
        self.assertEqual(sig_updated['rejection_count'], 2)

    def test_signal_auto_expiration(self):
        SIGNAL_CONFIG['expiry_seconds'] = 1
        
        # Create a WAITING signal with older trigger timestamp (2 seconds ago)
        trigger_time = int(time.time()) - 2
        candle_trigger = {'time': trigger_time, 'open': 115.0, 'high': 125.0, 'low': 110.0, 'close': 122.0}
        sig_id = create_buy_signal(self.user_id, self.box, candle_trigger)

        # Run expiration checks
        check_and_expire_signals(self.user_id)

        # Retrieve and verify status is EXPIRED
        sig = db.get_buy_signal_by_id(self.user_id, sig_id)
        self.assertEqual(sig['signal_status'], 'EXPIRED')

if __name__ == "__main__":
    unittest.main()
