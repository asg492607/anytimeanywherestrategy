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
from strategy.stop_loss_engine import (  # type: ignore
    monitor_running_trades,
    calculate_stop_loss,
    validate_stop_loss,
    execute_stop_loss,
    retry_exit_order,
    expire_monitoring,
    STOP_LOSS_CONFIG
)

class TestStopLossEngine(unittest.TestCase):

    def setUp(self):
        # Setup clean SQLite testing DB
        self.db_path = os.path.join(tempfile.gettempdir(), f"test_sl_{uuid.uuid4().hex}.db")
        db.DB_PATH = self.db_path
        db.init_db()

        # Mock active user
        self.user_id = db.create_user("User Eta", "eta@example.com", "password")
        
        # Reset defaults
        STOP_LOSS_CONFIG['enabled'] = True
        STOP_LOSS_CONFIG['max_retries'] = 3
        STOP_LOSS_CONFIG['retry_delay_seconds'] = 0.01  # fast retries

        # Setup mock reference boxes and buy signals
        c1 = {'time': int(time.time()) - 100, 'open': 100.0, 'high': 120.0, 'low': 90.0, 'close': 110.0}
        self.box_call = db.save_reference_box(self.user_id, 'CALL', 'CE_SYM', '3m', 'LOW_TO_HIGH', '0.786', c1['time'], 100, 120, 90, 110, 120, 90)

        # Create confirmed session and executions to create running trades
        self.conf_id = db.create_confirmation_session(self.user_id, 'institutional', 30, c1['time'])
        sig_id = db.save_buy_signal(self.user_id, self.box_call, 'CALL', 'CE_SYM', 'CONFIRMED', c1['time'], 100, 122, 95, 121, 121, 120)
        db.add_signal_to_confirmation(self.conf_id, sig_id, 'CALL', 'CE_SYM', 121, c1['time'], 1)
        db.update_confirmation_status(self.user_id, self.conf_id, 'CONFIRMED')
        
        # Create a RUNNING trade
        self.trade_id = db.create_trade(
            user_id=self.user_id,
            broker='angelone',
            underlying='SENSEX',
            expiry=None,
            call_symbol='CE_SYM',
            put_symbol=None,
            entry_price=121.0,
            quantity=10,
            stop_loss=111.0,
            target=141.0,
            strategy_name='institutional',
            direction='BUY'
        )
        
        # Create execution mapping
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
            requested_price=121.0,
            executed_price=121.0,
            order_id='INT_123456',
            broker_order_id='BROKER_12345',
            execution_status='COMPLETE',
            rejection_reason=None,
            execution_time=datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        )

    def tearDown(self):
        if os.path.exists(self.db_path):
            try:
                os.remove(self.db_path)
            except Exception as e:
                print(f"Cleanup warning: {e}")

    def test_stop_loss_calculation(self):
        # Box lower boundary is 90.0. Calc SL = 90.0 - 5 = 85.0
        sl_price = calculate_stop_loss(90.0)
        self.assertEqual(sl_price, 85.0)

    def test_candle_close_validation_rules(self):
        # 1. Start monitoring
        candles_dict = {
            'CALL': [{'time': int(time.time()), 'open': 100.0, 'high': 110.0, 'low': 92.0, 'close': 95.0}]
        }
        
        # Run monitor to initialize SL Event
        monitor_running_trades(self.user_id, candles_dict)
        
        # Verify monitoring event exists in DB
        sl_event = db.get_stop_loss_by_trade_id(self.user_id, self.trade_id)
        self.assertIsNotNone(sl_event)
        self.assertEqual(sl_event['exit_status'], 'MONITORING')
        self.assertEqual(sl_event['calculated_stop_loss'], 85.0)

        # 2. Wick-only penetration (low goes below 90, but close is above 90)
        candles_wick = {
            'CALL': [{'time': int(time.time()) + 1, 'open': 100.0, 'high': 110.0, 'low': 84.0, 'close': 92.0}]
        }
        monitor_running_trades(self.user_id, candles_wick)
        
        sl_event = db.get_stop_loss_by_trade_id(self.user_id, self.trade_id)
        self.assertEqual(sl_event['exit_status'], 'MONITORING') # stays active

        # 3. Body close below box lower boundary (close < 90)
        candles_trigger = {
            'CALL': [{'time': int(time.time()) + 2, 'open': 95.0, 'high': 98.0, 'low': 82.0, 'close': 84.0}]
        }
        
        # Mock SmartAPI exit execution
        with patch('strategy.stop_loss_engine.execute_order_api') as mock_exit:
            mock_exit.return_value = {'status': True, 'broker_order_id': 'SIM_EXIT_ORD'}
            monitor_running_trades(self.user_id, candles_trigger)

        # Verify trade status is upgraded to STOP_LOSS_HIT, exit details recorded
        trade = db.get_trade_by_id(self.user_id, self.trade_id)
        self.assertEqual(trade['status'], 'STOP_LOSS_HIT')
        self.assertEqual(trade['exit_price'], 85.0) # calculated sl price used as exit price

        # Verify stop loss event status becomes ORDER_COMPLETE
        sl_event_final = db.get_stop_loss_by_trade_id(self.user_id, self.trade_id)
        self.assertEqual(sl_event_final['exit_status'], 'ORDER_COMPLETE')
        self.assertEqual(sl_event_final['pnl'], (85.0 - 121.0) * 10) # (exit - entry) * qty

    @patch('strategy.stop_loss_engine.execute_order_api')
    def test_failed_exit_retry_count(self, mock_exit):
        mock_exit.return_value = {'status': False, 'message': 'API error'}
        
        # Trigger SL event monitoring initialization
        candles_dict = {
            'CALL': [{'time': int(time.time()), 'open': 100.0, 'high': 110.0, 'low': 92.0, 'close': 95.0}]
        }
        monitor_running_trades(self.user_id, candles_dict)

        # Trigger body close SL trigger
        candles_trigger = {
            'CALL': [{'time': int(time.time()) + 2, 'open': 95.0, 'high': 98.0, 'low': 82.0, 'close': 84.0}]
        }
        monitor_running_trades(self.user_id, candles_trigger)

        # Exit status should become FAILED after 3 attempts
        sl_event = db.get_stop_loss_by_trade_id(self.user_id, self.trade_id)
        self.assertEqual(sl_event['exit_status'], 'FAILED')
        self.assertIn("Exit failed after 3 attempts", sl_event['exit_reason'])

        # Trade should still remain RUNNING since exit failed
        trade = db.get_trade_by_id(self.user_id, self.trade_id)
        self.assertEqual(trade['status'], 'RUNNING')

    def test_duplicate_exit_prevention(self):
        # If exit_status is ORDER_COMPLETE, monitor_running_trades should immediately return without executing order
        sl_id = db.save_stop_loss_event(
            user_id=self.user_id,
            trade_id=self.trade_id,
            execution_id=self.exec_id,
            reference_box_id=self.box_call,
            chart_type='CALL',
            instrument_symbol='CE_SYM',
            reference_box_upper=120, reference_box_lower=90,
            calculated_stop_loss=85.0,
            trigger_candle_timestamp=int(time.time()),
            trigger_open=95, trigger_high=98, trigger_low=82, trigger_close=84,
            exit_price=85.0, pnl=-360.0,
            broker_exit_order_id='SIM_EXIT_ORD',
            exit_status='ORDER_COMPLETE',
            exit_reason='Filled'
        )

        candles_trigger = {
            'CALL': [{'time': int(time.time()) + 2, 'open': 95.0, 'high': 98.0, 'low': 82.0, 'close': 84.0}]
        }

        with patch('strategy.stop_loss_engine.execute_order_api') as mock_exit:
            monitor_running_trades(self.user_id, candles_trigger)
            # Should NOT place order as exit is complete
            mock_exit.assert_not_called()

if __name__ == "__main__":
    unittest.main()
