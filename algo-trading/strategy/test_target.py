import os
import sys
import unittest
import tempfile
import uuid
import time
from datetime import datetime
from unittest.mock import MagicMock, patch

# Add parent directory to sys.path so we can import modules
ALGO_TRADING_DIR = r"c:\Users\Atharva\OneDrive\Desktop\algo_anytimeanywhere\algo-trading"
if ALGO_TRADING_DIR not in sys.path:
    sys.path.insert(0, ALGO_TRADING_DIR)

import db  # type: ignore
import data_engine  # type: ignore
from strategy.target_engine import (  # type: ignore
    monitor_running_trades,
    detect_target_hit,
    validate_target,
    retry_target_exit,
    stop_monitoring,
    TARGET_CONFIG
)

class TestTargetEngine(unittest.TestCase):

    def setUp(self):
        # Setup clean SQLite testing DB
        self.db_path = os.path.join(tempfile.gettempdir(), f"test_tgt_{uuid.uuid4().hex}.db")
        db.DB_PATH = self.db_path
        db.init_db()

        # Mock manual fibs to bypass datetime range restrictions during tests
        data_engine.MANUAL_FIBS = {
            'CE_SYM': {'high': 100.0, 'low': 0.0},
            'SENSEX': {'high': 100.0, 'low': 0.0},
            'PE_SYM': {'high': 100.0, 'low': 0.0}
        }

        # Mock active user
        self.user_id = db.create_user("User Theta", "theta@example.com", "password")
        
        # Reset defaults
        TARGET_CONFIG['enabled'] = True
        TARGET_CONFIG['max_retries'] = 3
        TARGET_CONFIG['retry_delay_seconds'] = 0.01  # fast retries
        TARGET_CONFIG['candle_close_confirmation'] = False

        # Setup mock reference boxes
        c1 = {'time': int(time.time()) - 200, 'open': 100.0, 'high': 120.0, 'low': 90.0, 'close': 110.0}
        self.box_call = db.save_reference_box(self.user_id, 'CALL', 'CE_SYM', '3m', 'LOW_TO_HIGH', '0.786', c1['time'], 100, 120, 90, 110, 120, 90)

        # Create confirmed session
        self.conf_id = db.create_confirmation_session(self.user_id, 'institutional', 30, c1['time'])
        sig_id = db.save_buy_signal(self.user_id, self.box_call, 'CALL', 'CE_SYM', 'CONFIRMED', c1['time'], 100, 122, 95, 121, 121, 120)
        db.add_signal_to_confirmation(self.conf_id, sig_id, 'CALL', 'CE_SYM', 121, c1['time'], 1)
        db.update_confirmation_status(self.user_id, self.conf_id, 'CONFIRMED')
        
        # Create a RUNNING trade (entry time is current time, so we should mock candle timestamp to be in the future)
        self.trade_id = db.create_trade(
            user_id=self.user_id,
            broker='angelone',
            underlying='SENSEX',
            expiry=None,
            call_symbol='CE_SYM',
            put_symbol=None,
            entry_price=100.0,
            quantity=10,
            stop_loss=90.0,
            target=139.0,
            strategy_name='institutional',
            direction='BUY'
        )
        
        self.exec_id = db.save_trade_execution(
            user_id=self.user_id,
            confirmation_id=self.conf_id,
            trade_id=self.trade_id,
            broker='angelone',
            exchange='BFO',
            symbol='CE_SYM',
            token='99919001',
            order_type='MARKET',
            transaction_type='BUY',
            product_type='CARRYFORWARD',
            variety='NORMAL',
            quantity=10,
            requested_price=100.0,
            executed_price=100.0,
            order_id='INT_123456',
            broker_order_id='BROKER_12345',
            execution_status='COMPLETE',
            rejection_reason=None,
            execution_time=datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        )

        # Mock weekly data for active levels calculation in test (high 100, low 0, target level 1.39 resolves to 139)
        self.mock_db_data = {
            'call': {
                'symbol': 'CE_SYM',
                'weekly': [{'time': c1['time'] - 86400, 'open': 10, 'high': 100, 'low': 0, 'close': 50}]
            },
            'sensex': {
                'symbol': 'SENSEX',
                'weekly': [{'time': c1['time'] - 86400, 'open': 10, 'high': 100, 'low': 0, 'close': 50}]
            },
            'put': {
                'symbol': 'PE_SYM',
                'weekly': [{'time': c1['time'] - 86400, 'open': 10, 'high': 100, 'low': 0, 'close': 50}]
            }
        }

    def tearDown(self):
        if os.path.exists(self.db_path):
            try:
                os.remove(self.db_path)
            except Exception as e:
                print(f"Cleanup warning: {e}")

    def test_target_detection(self):
        # Target price level is 1.39 -> 139.0
        # 1. High touches target, close remains below. Touch detection should trigger.
        candle_touch = {
            'time': int(time.time()) + 100, 'open': 120.0, 'high': 140.0, 'low': 110.0, 'close': 130.0
        }
        self.assertTrue(detect_target_hit(candle_touch, 139.0, 'LOW_TO_HIGH'))

        # 2. Close target checks (touch fails if close confirmation is turned on)
        TARGET_CONFIG['candle_close_confirmation'] = True
        self.assertFalse(detect_target_hit(candle_touch, 139.0, 'LOW_TO_HIGH'))
        TARGET_CONFIG['candle_close_confirmation'] = False

    def test_single_chart_exit_rule(self):
        # If CALL is normal, but SPOT chart high crosses its target (which is also 139.0 for simplicity)
        # Using a future timestamp to pass the trade's entry_time boundary
        future_time = int(time.time()) + 100
        candles_dict = {
            'CALL': [{'time': future_time, 'open': 100.0, 'high': 120.0, 'low': 90.0, 'close': 110.0}],
            'SPOT': [{'time': future_time, 'open': 100.0, 'high': 145.0, 'low': 90.0, 'close': 110.0}],
            'PUT': [{'time': future_time, 'open': 100.0, 'high': 120.0, 'low': 90.0, 'close': 110.0}]
        }

        # Mock execution and verify exit places
        with patch('strategy.target_engine.execute_order_api') as mock_exit:
            mock_exit.return_value = {'status': True, 'broker_order_id': 'SIM_EXIT_TGT'}
            monitor_running_trades(self.user_id, candles_dict, self.mock_db_data)

        # Verify trade status upgraded to TARGET_HIT
        trade = db.get_trade_by_id(self.user_id, self.trade_id)
        self.assertEqual(trade['status'], 'TARGET_HIT')
        self.assertEqual(trade['exit_price'], 139.0)

        # Verify target exit events record completed
        tgt_event = db.get_target_exit_by_trade_id(self.user_id, self.trade_id)
        self.assertEqual(tgt_event['exit_status'], 'ORDER_COMPLETE')
        self.assertEqual(tgt_event['chart_type'], 'SPOT') # Triggered on SPOT chart touch

    @patch('strategy.target_engine.execute_order_api')
    def test_failed_exit_retry_count(self, mock_exit):
        mock_exit.return_value = {'status': False, 'message': 'Network timeout'}
        
        future_time = int(time.time()) + 100
        candles_dict = {
            'CALL': [{'time': future_time, 'open': 100.0, 'high': 140.0, 'low': 90.0, 'close': 110.0}]
        }

        monitor_running_trades(self.user_id, candles_dict, self.mock_db_data)

        # Event exit_status should become FAILED after 3 retry drops
        tgt_event = db.get_target_exit_by_trade_id(self.user_id, self.trade_id)
        self.assertEqual(tgt_event['exit_status'], 'FAILED')
        self.assertIn("Target exit failed after 3 attempts", tgt_event['exit_reason'])

        # Trade stays RUNNING
        trade = db.get_trade_by_id(self.user_id, self.trade_id)
        self.assertEqual(trade['status'], 'RUNNING')

    def test_duplicate_exit_prevention(self):
        db.save_target_exit_event(
            user_id=self.user_id,
            trade_id=self.trade_id,
            execution_id=self.exec_id,
            confirmation_id=self.conf_id,
            reference_box_id=self.box_call,
            chart_type='CALL',
            instrument_symbol='CE_SYM',
            fib_direction='LOW_TO_HIGH',
            target_level='1.39',
            target_price=139.0,
            trigger_candle_timestamp=int(time.time()),
            trigger_open=100.0, trigger_high=140.0, trigger_low=90.0, trigger_close=110.0,
            exit_price=139.0, pnl=390.0,
            broker_exit_order_id='SIM_EXIT_TGT',
            exit_status='ORDER_COMPLETE',
            exit_reason='Filled'
        )

        future_time = int(time.time()) + 100
        candles_trigger = {
            'CALL': [{'time': future_time, 'open': 100.0, 'high': 145.0, 'low': 90.0, 'close': 110.0}]
        }

        with patch('strategy.target_engine.execute_order_api') as mock_exit:
            monitor_running_trades(self.user_id, candles_trigger, self.mock_db_data)
            # Should NOT place order as exit is complete
            mock_exit.assert_not_called()

if __name__ == "__main__":
    unittest.main()
