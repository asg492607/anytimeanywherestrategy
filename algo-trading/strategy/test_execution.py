import os
import sys
import unittest
import tempfile
import uuid
import time
from unittest.mock import MagicMock, patch

# Add parent directory to sys.path so we can import modules
ALGO_TRADING_DIR = r"c:\Users\Atharva\OneDrive\Desktop\algo_anytimeanywhere\algo-trading"
if ALGO_TRADING_DIR not in sys.path:
    sys.path.insert(0, ALGO_TRADING_DIR)

import db  # type: ignore
from strategy.execution_engine import (  # type: ignore
    monitor_confirmations,
    validate_confirmation,
    calculate_order_quantity,
    retry_execution,
    cancel_execution,
    EXECUTION_CONFIG
)

class TestExecutionEngine(unittest.TestCase):

    def setUp(self):
        # Setup clean SQLite testing DB
        self.db_path = os.path.join(tempfile.gettempdir(), f"test_exec_{uuid.uuid4().hex}.db")
        db.DB_PATH = self.db_path
        db.init_db()

        # Mock active user
        self.user_id = db.create_user("User Zeta", "zeta@example.com", "password")
        
        # Reset default configuration
        EXECUTION_CONFIG['enabled'] = True
        EXECUTION_CONFIG['sizing_type'] = 'FIXED'
        EXECUTION_CONFIG['fixed_qty'] = 10
        EXECUTION_CONFIG['capital_allocated'] = 50000
        EXECUTION_CONFIG['max_retries'] = 3
        EXECUTION_CONFIG['retry_delay_seconds'] = 0.01  # Fast retries during tests

        # Setup mock reference boxes and buy signals
        c1 = {'time': int(time.time()) - 100, 'open': 100.0, 'high': 120.0, 'low': 90.0, 'close': 110.0}
        self.box_call = db.save_reference_box(self.user_id, 'CALL', 'CE_SYM', '3m', 'LOW_TO_HIGH', '0.786', c1['time'], 100, 120, 90, 110, 120, 90)
        self.box_spot = db.save_reference_box(self.user_id, 'SPOT', 'SPOT_SYM', '3m', 'LOW_TO_HIGH', '0.786', c1['time'], 100, 120, 90, 110, 120, 90)

        # Create confirmed session
        self.conf_id = db.create_confirmation_session(
            user_id=self.user_id,
            strategy_name='institutional',
            window_seconds=30,
            start_time=c1['time']
        )
        
        # Save buy signals
        sig_id1 = db.save_buy_signal(self.user_id, self.box_call, 'CALL', 'CE_SYM', 'CONFIRMED', c1['time'], 100, 122, 95, 121, 121, 120)
        sig_id2 = db.save_buy_signal(self.user_id, self.box_spot, 'SPOT', 'SPOT_SYM', 'CONFIRMED', c1['time'] + 2, 100, 122, 95, 121, 121, 120)

        # Link signals to confirmation
        db.add_signal_to_confirmation(self.conf_id, sig_id1, 'CALL', 'CE_SYM', 121, c1['time'], 1)
        db.add_signal_to_confirmation(self.conf_id, sig_id2, 'SPOT', 'SPOT_SYM', 121, c1['time'] + 2, 2)
        
        # Update confirmation status to CONFIRMED
        db.update_confirmation_status(self.user_id, self.conf_id, 'CONFIRMED')
        self.session = db.get_confirmation_by_id(self.user_id, self.conf_id)

    def tearDown(self):
        if os.path.exists(self.db_path):
            try:
                os.remove(self.db_path)
            except Exception as e:
                print(f"Cleanup warning: {e}")

    def test_confirmation_validation(self):
        # 1. Valid confirmation
        is_valid, reason = validate_confirmation(self.user_id, self.session)
        self.assertTrue(is_valid)

        # 2. Invalid status (e.g. WAITING)
        db.update_confirmation_status(self.user_id, self.conf_id, 'WAITING')
        session_waiting = db.get_confirmation_by_id(self.user_id, self.conf_id)
        is_valid, reason = validate_confirmation(self.user_id, session_waiting)
        self.assertFalse(is_valid)
        self.assertEqual(reason, "Session not confirmed")

    def test_quantity_calculations(self):
        # 1. FIXED Quantity
        EXECUTION_CONFIG['sizing_type'] = 'FIXED'
        EXECUTION_CONFIG['fixed_qty'] = 20
        self.assertEqual(calculate_order_quantity(150.0), 20)

        # 2. CAPITAL Quantity
        EXECUTION_CONFIG['sizing_type'] = 'CAPITAL'
        EXECUTION_CONFIG['capital_allocated'] = 50000
        # 50000 / 121 = 413.22. Rounded down to nearest lot of 10: 410
        self.assertEqual(calculate_order_quantity(121.0), 410)

    @patch('data_engine.smartApi')
    def test_successful_execution_and_trade_creation(self, mock_smartapi):
        # Mock SmartAPI order placement response
        mock_smartapi.sessionToken = "active_session_token"
        mock_smartapi.placeOrder.return_value = {
            'status': True,
            'message': 'SUCCESS',
            'data': {'orderid': 'BROKER_12345'}
        }
        mock_smartapi.orderBook.return_value = {
            'status': True,
            'data': [{'orderid': 'BROKER_12345', 'status': 'COMPLETE', 'averageprice': 122.5}]
        }

        # Run confirmation monitor
        monitor_confirmations(self.user_id)

        # 1. Verify execution entry exists in DB
        executions = db.get_all_executions(self.user_id)
        self.assertEqual(len(executions), 1)
        self.assertEqual(executions[0]['execution_status'], 'COMPLETE')
        self.assertEqual(executions[0]['executed_price'], 122.5)

        # 2. Verify trade record was created automatically
        trades, total = db.get_all_trades(self.user_id)
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]['status'], 'RUNNING')
        self.assertEqual(trades[0]['entry_price'], 122.5)
        self.assertEqual(trades[0]['quantity'], 10)
        self.assertEqual(trades[0]['call_symbol'], 'CE_SYM')

    @patch('data_engine.smartApi')
    def test_duplicate_execution_prevention(self, mock_smartapi):
        mock_smartapi.sessionToken = "active_session_token"
        mock_smartapi.placeOrder.return_value = {
            'status': True,
            'data': {'orderid': 'BROKER_12345'}
        }
        mock_smartapi.orderBook.return_value = {
            'status': True,
            'data': [{'orderid': 'BROKER_12345', 'status': 'COMPLETE', 'averageprice': 121.0}]
        }

        # Run monitor first time
        monitor_confirmations(self.user_id)
        # Run monitor second time for the same confirmation
        monitor_confirmations(self.user_id)

        # Database should still contain exactly 1 execution and 1 trade
        executions = db.get_all_executions(self.user_id)
        self.assertEqual(len(executions), 1)
        trades, total = db.get_all_trades(self.user_id)
        self.assertEqual(len(trades), 1)

    @patch('data_engine.smartApi')
    def test_failed_retry_logic(self, mock_smartapi):
        mock_smartapi.sessionToken = "active_session_token"
        # Simulate network timeout / rate-limit failures
        mock_smartapi.placeOrder.return_value = {
            'status': False,
            'message': 'Rate limit exceeded'
        }

        # Run monitor
        monitor_confirmations(self.user_id)

        # Verify status became FAILED after max retries
        executions = db.get_all_executions(self.user_id)
        self.assertEqual(len(executions), 1)
        self.assertEqual(executions[0]['execution_status'], 'FAILED')
        self.assertIn("Submission failed after 3 attempts", executions[0]['rejection_reason'])

        # No trades should have been created
        trades, total = db.get_all_trades(self.user_id)
        self.assertEqual(len(trades), 0)

if __name__ == "__main__":
    unittest.main()
