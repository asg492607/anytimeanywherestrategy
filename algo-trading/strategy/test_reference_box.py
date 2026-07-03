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
from strategy.reference_box_engine import (  # type: ignore
    detect_fibonacci_cross,
    create_reference_box,
    check_and_expire_boxes,
    get_active_boxes,
    update_reference_box,
    load_reference_boxes,
    BOX_CONFIG
)

class TestReferenceBoxEngine(unittest.TestCase):

    def setUp(self):
        # Setup clean SQLite testing DB
        self.db_path = os.path.join(tempfile.gettempdir(), f"test_ref_box_{uuid.uuid4().hex}.db")
        db.DB_PATH = self.db_path
        db.init_db()

        # Mock active user
        self.user_id = db.create_user("User Delta", "delta@example.com", "password")
        
        # Reset config to defaults
        BOX_CONFIG['enabled'] = True
        BOX_CONFIG['max_active_boxes'] = 5
        BOX_CONFIG['auto_expiry_seconds'] = 3600

    def tearDown(self):
        if os.path.exists(self.db_path):
            try:
                os.remove(self.db_path)
            except Exception as e:
                print(f"Cleanup warning: {e}")

    def test_fibonacci_cross_detection(self):
        # 1. Mock levels
        levels = {
            'f1_0_236': 100.0,
            'f1_0_786': 200.0,
            'f1_1_140': 300.0,
            'f1_1_390': 400.0,
            'f2_0_236': 500.0,
            'f2_0_786': 600.0,
            'f2_1_140': 700.0,
            'f2_1_390': 800.0
        }

        # 2. Candle crosses 200.0 (f1_0_786) - Bullish
        candle_cross = {'open': 190.0, 'high': 220.0, 'low': 180.0, 'close': 210.0}
        crosses = detect_fibonacci_cross(candle_cross, levels)
        self.assertEqual(len(crosses), 1)
        self.assertEqual(crosses[0]['level_name'], '0.786')
        self.assertEqual(crosses[0]['fib_direction'], 'LOW_TO_HIGH')
        self.assertEqual(crosses[0]['crossed_direction'], 'UPWARD')

        # 3. Candle does not cross any monitored level
        candle_no_cross = {'open': 210.0, 'high': 250.0, 'low': 205.0, 'close': 220.0}
        crosses_none = detect_fibonacci_cross(candle_no_cross, levels)
        self.assertEqual(len(crosses_none), 0)

    def test_reference_box_creation_and_boundaries(self):
        candle = {'time': int(time.time()), 'open': 190.0, 'high': 220.0, 'low': 180.0, 'close': 210.0}
        
        # Create box
        box_id = create_reference_box(
            user_id=self.user_id,
            chart_type="CALL",
            instrument_symbol="SENSEX26700CE",
            timeframe="3m",
            fib_direction="LOW_TO_HIGH",
            fib_level="0.786",
            candle=candle,
            crossed_direction="UPWARD"
        )
        self.assertIsNotNone(box_id)

        # Retrieve and check boundaries
        boxes = get_active_boxes(self.user_id)
        self.assertEqual(len(boxes), 1)
        self.assertEqual(boxes[0]['id'], box_id)
        self.assertEqual(boxes[0]['upper_boundary'], 220.0) # High of candle
        self.assertEqual(boxes[0]['lower_boundary'], 180.0) # Low of candle
        self.assertEqual(boxes[0]['box_status'], 'ACTIVE')

    def test_active_box_replacement(self):
        now_ts = int(time.time())
        candle1 = {'time': now_ts - 10, 'open': 190.0, 'high': 220.0, 'low': 180.0, 'close': 210.0}
        candle2 = {'time': now_ts, 'open': 195.0, 'high': 225.0, 'low': 185.0, 'close': 215.0}

        # 1. Create first box on level 0.786
        box_id1 = create_reference_box(self.user_id, "CALL", "SENSEX26700CE", "3m", "LOW_TO_HIGH", "0.786", candle1, "UPWARD")
        
        # 2. Create second box on SAME level 0.786
        box_id2 = create_reference_box(self.user_id, "CALL", "SENSEX26700CE", "3m", "LOW_TO_HIGH", "0.786", candle2, "UPWARD")

        # 3. Verify box 1 is REPLACED, and box 2 is ACTIVE
        boxes_active = get_active_boxes(self.user_id)
        self.assertEqual(len(boxes_active), 1)
        self.assertEqual(boxes_active[0]['id'], box_id2)

        all_boxes = load_reference_boxes(self.user_id)
        self.assertEqual(len(all_boxes), 2)
        box1 = [b for b in all_boxes if b['id'] == box_id1][0]
        self.assertEqual(box1['box_status'], 'REPLACED')

    def test_multi_chart_independence(self):
        candle = {'time': int(time.time()), 'open': 190.0, 'high': 220.0, 'low': 180.0, 'close': 210.0}

        # Create box on CALL chart
        call_id = create_reference_box(self.user_id, "CALL", "SENSEX26700CE", "3m", "LOW_TO_HIGH", "0.786", candle, "UPWARD")
        # Create box on SPOT chart for SAME level
        spot_id = create_reference_box(self.user_id, "SPOT", "SENSEX", "3m", "LOW_TO_HIGH", "0.786", candle, "UPWARD")

        # Both should be ACTIVE independently
        boxes_active = get_active_boxes(self.user_id)
        self.assertEqual(len(boxes_active), 2)
        self.assertIn(call_id, [b['id'] for b in boxes_active])
        self.assertIn(spot_id, [b['id'] for b in boxes_active])

    def test_box_auto_expiration(self):
        # Configure timeout to 1 second
        BOX_CONFIG['auto_expiry_seconds'] = 1
        
        # 1. Create a box with an older timestamp (2 seconds ago)
        old_time = int(time.time()) - 2
        candle = {'time': old_time, 'open': 190.0, 'high': 220.0, 'low': 180.0, 'close': 210.0}
        
        box_id = create_reference_box(self.user_id, "CALL", "SENSEX26700CE", "3m", "LOW_TO_HIGH", "0.786", candle, "UPWARD")
        
        # 2. Run expiration checker
        check_and_expire_boxes(self.user_id)

        # 3. Check status is now EXPIRED
        # Using db directly because get_active_boxes runs the expire check
        boxes_active = db.get_active_boxes(self.user_id)
        self.assertEqual(len(boxes_active), 0)

        all_boxes = load_reference_boxes(self.user_id)
        self.assertEqual(all_boxes[0]['box_status'], 'EXPIRED')

    def test_duplicate_prevention(self):
        candle = {'time': int(time.time()), 'open': 190.0, 'high': 220.0, 'low': 180.0, 'close': 210.0}
        
        # 1. Save box
        id1 = create_reference_box(self.user_id, "CALL", "SENSEX26700CE", "3m", "LOW_TO_HIGH", "0.786", candle, "UPWARD")
        
        # 2. Try saving identical box again (same user, chart, symbol, level, timestamp)
        id2 = create_reference_box(self.user_id, "CALL", "SENSEX26700CE", "3m", "LOW_TO_HIGH", "0.786", candle, "UPWARD")
        
        # 3. Both IDs should point to the SAME database record
        self.assertEqual(id1, id2)
        all_boxes = load_reference_boxes(self.user_id)
        self.assertEqual(len(all_boxes), 1)

    def test_maximum_active_boxes_limit(self):
        # Set limit to 2
        BOX_CONFIG['max_active_boxes'] = 2
        
        # Create 3 boxes on different levels (so they don't replace each other)
        now_ts = int(time.time())
        c1 = {'time': now_ts - 20, 'open': 10.0, 'high': 15.0, 'low': 5.0, 'close': 12.0}
        c2 = {'time': now_ts - 10, 'open': 10.0, 'high': 15.0, 'low': 5.0, 'close': 12.0}
        c3 = {'time': now_ts, 'open': 10.0, 'high': 15.0, 'low': 5.0, 'close': 12.0}
        
        id1 = create_reference_box(self.user_id, "CALL", "SENSECE", "3m", "LOW_TO_HIGH", "0.236", c1, "UPWARD")
        id2 = create_reference_box(self.user_id, "CALL", "SENSECE", "3m", "LOW_TO_HIGH", "0.786", c2, "UPWARD")
        id3 = create_reference_box(self.user_id, "CALL", "SENSECE", "3m", "LOW_TO_HIGH", "1.14", c3, "UPWARD")
        
        # Active boxes should be limited to 2. Oldest one (id1) should be EXPIRED.
        active = db.get_active_boxes(self.user_id, "CALL")
        self.assertEqual(len(active), 2)
        self.assertNotIn(id1, [b['id'] for b in active])
        self.assertIn(id2, [b['id'] for b in active])
        self.assertIn(id3, [b['id'] for b in active])
        
        all_boxes = load_reference_boxes(self.user_id)
        box1 = [b for b in all_boxes if b['id'] == id1][0]
        self.assertEqual(box1['box_status'], 'EXPIRED')

if __name__ == "__main__":
    unittest.main()
