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
from strategy.buy_signal_engine import create_buy_signal  # type: ignore
from strategy.confirmation_engine import (  # type: ignore
    monitor_buy_signals,
    expire_old_sessions,
    CONFIRMATION_WINDOW_SECONDS
)

class TestConfirmationEngine(unittest.TestCase):

    def setUp(self):
        # Setup clean SQLite testing DB
        self.db_path = os.path.join(tempfile.gettempdir(), f"test_conf_{uuid.uuid4().hex}.db")
        db.DB_PATH = self.db_path
        db.init_db()

        # Mock active users
        self.user_a = db.create_user("User Alpha", "alpha@example.com", "password")
        self.user_b = db.create_user("User Beta", "beta@example.com", "password")
        
        # Setup mock reference boxes and buy signals for User A
        c1 = {'time': int(time.time()) - 100, 'open': 10.0, 'high': 15.0, 'low': 5.0, 'close': 12.0}
        
        self.box_call = db.save_reference_box(self.user_a, 'CALL', 'CE_SYM', '3m', 'LOW_TO_HIGH', '0.786', c1['time'], 10, 15, 5, 12, 15, 5)
        self.box_spot = db.save_reference_box(self.user_a, 'SPOT', 'SPOT_SYM', '3m', 'LOW_TO_HIGH', '0.786', c1['time'], 10, 15, 5, 12, 15, 5)
        self.box_put  = db.save_reference_box(self.user_a, 'PUT', 'PE_SYM', '3m', 'LOW_TO_HIGH', '0.786', c1['time'], 10, 15, 5, 12, 15, 5)

        # Setup mock reference boxes and buy signals for User B
        self.box_user_b = db.save_reference_box(self.user_b, 'CALL', 'CE_SYM_B', '3m', 'LOW_TO_HIGH', '0.786', c1['time'], 10, 15, 5, 12, 15, 5)

    def tearDown(self):
        if os.path.exists(self.db_path):
            try:
                os.remove(self.db_path)
            except Exception as e:
                print(f"Cleanup warning: {e}")

    def test_confirmation_session_creation_on_first_signal(self):
        # 1. Create a CALL buy signal
        candle = {'time': int(time.time()), 'open': 12, 'high': 18, 'low': 11, 'close': 16}
        sig_id = db.save_buy_signal(
            user_id=self.user_a,
            reference_box_id=self.box_call,
            chart_type='CALL',
            instrument_symbol='CE_SYM',
            signal_status='WAITING',
            trigger_candle_timestamp=candle['time'],
            trigger_open=12, trigger_high=18, trigger_low=11, trigger_close=16,
            breakout_price=16, breakout_boundary=15
        )
        
        # 2. Run monitor engine
        monitor_buy_signals(self.user_a)

        # 3. Verify a WAITING confirmation session is created and the signal is linked
        active_sessions = db.get_active_confirmations(self.user_a)
        self.assertEqual(len(active_sessions), 1)
        self.assertEqual(active_sessions[0]['confirmation_status'], 'WAITING')
        self.assertEqual(active_sessions[0]['received_confirmations'], 1)
        self.assertEqual(len(active_sessions[0]['signals']), 1)
        self.assertEqual(active_sessions[0]['signals'][0]['buy_signal_id'], sig_id)

    def test_two_out_of_three_confirmation_combinations(self):
        now_ts = int(time.time())
        # Combinations test: CALL + SPOT should confirm
        sig_id1 = db.save_buy_signal(self.user_a, self.box_call, 'CALL', 'CE_SYM', 'WAITING', now_ts, 12, 18, 11, 16, 16, 15)
        sig_id2 = db.save_buy_signal(self.user_a, self.box_spot, 'SPOT', 'SPOT_SYM', 'WAITING', now_ts + 2, 12, 18, 11, 16, 16, 15)

        # Process signals
        monitor_buy_signals(self.user_a)

        # Session should transition to CONFIRMED
        history = db.get_confirmation_history(self.user_a)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]['confirmation_status'], 'CONFIRMED')
        self.assertEqual(history[0]['received_confirmations'], 2)

        # Signals status should be upgraded to CONFIRMED
        s1 = db.get_buy_signal_by_id(self.user_a, sig_id1)
        s2 = db.get_buy_signal_by_id(self.user_a, sig_id2)
        self.assertEqual(s1['signal_status'], 'CONFIRMED')
        self.assertEqual(s2['signal_status'], 'CONFIRMED')

    def test_duplicate_and_same_chart_prevention(self):
        now_ts = int(time.time())
        
        # 1. Create CALL Buy Signal
        sig_id1 = db.save_buy_signal(self.user_a, self.box_call, 'CALL', 'CE_SYM', 'WAITING', now_ts, 12, 18, 11, 16, 16, 15)
        
        # 2. Create another CALL Buy Signal (should not happen on active box, but testing bounds)
        # Using a dummy box ID to simulate multiple CALL signals
        box_call_dummy = db.save_reference_box(self.user_a, 'CALL', 'CE_SYM_DUMMY', '3m', 'LOW_TO_HIGH', '0.786', now_ts - 50, 10, 15, 5, 12, 15, 5)
        sig_id2 = db.save_buy_signal(self.user_a, box_call_dummy, 'CALL', 'CE_SYM_DUMMY', 'WAITING', now_ts + 2, 12, 18, 11, 16, 16, 15)

        # Process
        monitor_buy_signals(self.user_a)

        # Should create two independent active confirmation sessions
        active_sessions = db.get_active_confirmations(self.user_a)
        self.assertEqual(len(active_sessions), 2)
        
        # Each session should contain exactly 1 signal (they did not combine)
        self.assertEqual(len(active_sessions[0]['signals']), 1)
        self.assertEqual(len(active_sessions[1]['signals']), 1)

    def test_session_timeout_expiration(self):
        now_ts = int(time.time())
        # First signal starts a session but end time is set based on its trigger time
        # We can back-date the start time of the session by back-dating the trigger timestamp
        old_ts = now_ts - CONFIRMATION_WINDOW_SECONDS - 5
        
        sig_id = db.save_buy_signal(self.user_a, self.box_call, 'CALL', 'CE_SYM', 'WAITING', old_ts, 12, 18, 11, 16, 16, 15)
        
        # Process to start session
        monitor_buy_signals(self.user_a)

        # Run expiry checker
        expire_old_sessions(self.user_a)

        # Verify status is EXPIRED
        history = db.get_confirmation_history(self.user_a)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]['confirmation_status'], 'EXPIRED')

        # Underlying signal should also be marked EXPIRED
        s = db.get_buy_signal_by_id(self.user_a, sig_id)
        self.assertEqual(s['signal_status'], 'EXPIRED')

    def test_multi_user_isolation(self):
        now_ts = int(time.time())
        # User A has a CALL signal
        db.save_buy_signal(self.user_a, self.box_call, 'CALL', 'CE_SYM', 'WAITING', now_ts, 12, 18, 11, 16, 16, 15)
        # User B has a CALL signal
        db.save_buy_signal(self.user_b, self.box_user_b, 'CALL', 'CE_SYM_B', 'WAITING', now_ts, 12, 18, 11, 16, 16, 15)

        # Run process for User A only
        monitor_buy_signals(self.user_a)

        # User A has 1 session
        self.assertEqual(len(db.get_all_confirmations(self.user_a)), 1)
        # User B has 0 sessions
        self.assertEqual(len(db.get_all_confirmations(self.user_b)), 0)

if __name__ == "__main__":
    unittest.main()
