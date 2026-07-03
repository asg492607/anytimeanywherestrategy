import os
import sys
import unittest
import tempfile
import uuid
from datetime import datetime, timedelta, timezone

# Add parent directory to sys.path so we can import modules
ALGO_TRADING_DIR = r"c:\Users\Atharva\OneDrive\Desktop\algo_anytimeanywhere\algo-trading"
if ALGO_TRADING_DIR not in sys.path:
    sys.path.insert(0, ALGO_TRADING_DIR)

import db  # type: ignore
from strategy.fibonacci_engine import (  # type: ignore
    group_into_weekly,
    _calc_level,
    _auto_weekly_high_low,
    get_fibonacci_levels,
    get_confluence_levels,
    get_reversal_zones
)

IST = timezone(timedelta(hours=5, minutes=30))

class TestFibonacciEngine(unittest.TestCase):

    def setUp(self):
        # Setup clean SQLite testing DB
        self.db_path = os.path.join(tempfile.gettempdir(), f"test_fib_{uuid.uuid4().hex}.db")
        db.DB_PATH = self.db_path
        db.init_db()

        # Construct dummy daily/weekly candle data for tests
        # Let's mock historical weekly candles that represent previous weekly ranges
        now = datetime.now(IST)
        
        # Last completed Friday anchor weekly candles
        # Week starts Monday, expires Friday
        self.weekly_data = []
        for i in range(5, 0, -1):
            time_dt = now - timedelta(weeks=i)
            # Make sure it's a Monday timestamp
            mon_dt = time_dt - timedelta(days=time_dt.weekday())
            self.weekly_data.append({
                'time': int(datetime(mon_dt.year, mon_dt.month, mon_dt.day, 9, 15).replace(tzinfo=IST).timestamp()),
                'open': 80000.0 - i * 100.0,
                'high': 82000.0 - i * 100.0,
                'low': 79000.0 - i * 100.0,
                'close': 81000.0 - i * 100.0,
                'volume': 500000
            })

    def tearDown(self):
        if os.path.exists(self.db_path):
            try:
                os.remove(self.db_path)
            except Exception as e:
                print(f"Cleanup warning: {e}")

    def test_group_into_weekly(self):
        # Construct daily candles
        now = datetime.now(IST)
        mon = now - timedelta(days=now.weekday()) # Monday of current week
        daily = []
        for d in range(5): # Monday to Friday
            day_dt = mon + timedelta(days=d)
            daily.append({
                'time': int(datetime(day_dt.year, day_dt.month, day_dt.day, 9, 15).replace(tzinfo=IST).timestamp()),
                'open': 80000.0 + d * 10.0,
                'high': 80500.0 + d * 10.0,
                'low': 79500.0 - d * 10.0,
                'close': 80100.0 + d * 10.0,
                'volume': 10000
            })
        
        weekly = group_into_weekly(daily)
        self.assertEqual(len(weekly), 1)
        self.assertEqual(weekly[0]['high'], 80540.0) # Monday High = 80500, Friday High = 80540
        self.assertEqual(weekly[0]['low'], 79460.0)  # Monday Low = 79500, Friday Low = 79460
        self.assertEqual(weekly[0]['volume'], 50000)

    def test_fib_math_formulas(self):
        # Test basic manual values
        high = 82000.0
        low = 78000.0
        diff = high - low # 4000.0
        
        # BUY (Low-to-High) level 0.618: low + diff * 0.618
        # 78000 + 4000 * 0.618 = 78000 + 2472 = 80472
        l_0618 = _calc_level('level_0_618', high, low)
        self.assertAlmostEqual(l_0618, 80472.0)
        
        # SELL (High-to-Low) level 1.39: high - diff * 1.39
        # 82000 - 4000 * 1.39 = 82000 - 5560 = 76440
        l_139 = _calc_level('f2_1_390', high, low)
        self.assertAlmostEqual(l_139, 76440.0)

    def test_auto_weekly_high_low_extraction(self):
        # Evaluate autodetection from test candles
        high, low, start, end = _auto_weekly_high_low(self.weekly_data)
        self.assertIsNotNone(high)
        self.assertIsNotNone(low)
        self.assertTrue(high > low)
        self.assertIsNotNone(start)
        self.assertIsNotNone(end)

    def test_confluence_structures(self):
        high = 82000.0
        low = 78000.0
        confluences = get_confluence_levels(high, low)
        
        self.assertEqual(len(confluences), 5)
        for conf in confluences:
            self.assertIn("level", conf)
            self.assertIn("price", conf)
            self.assertIn("type", conf)
            self.assertIn("strength", conf)
            
        # Upper Confluence price should lie between 78000 and 82000
        upper = [c for c in confluences if c["type"] == "Upper Confluence"][0]
        self.assertTrue(low < upper["price"] < high)

    def test_reversal_zones_structures(self):
        high = 82000.0
        low = 78000.0
        zones = get_reversal_zones(high, low)
        
        self.assertEqual(len(zones), 4)
        for zone in zones:
            self.assertIn("zone", zone)
            self.assertIn("from", zone)
            self.assertIn("to", zone)
            self.assertIn("type", zone)
            self.assertTrue(zone["from"] <= zone["to"])

    def test_weekly_refresh_database_cache_logic(self):
        # 1. Calculate dummy parameters
        week_start = "2026-06-22"
        week_end = "2026-06-26"
        chart_type = "SENSEX"
        direction = "LOW_TO_HIGH"
        
        levels = {
            "f1_0_000": 78000.0,
            "f1_0_618": 80472.0,
            "f1_1_000": 82000.0
        }
        
        # 2. Assert no levels exist in cache initially
        cached = db.get_fib_levels(week_start, week_end, chart_type, direction)
        self.assertIsNone(cached)
        
        # 3. Save levels to database cache
        db.save_fib_levels(week_start, week_end, chart_type, direction, levels)
        
        # 4. Fetch again -> should load from DB cache instead of returning None
        cached = db.get_fib_levels(week_start, week_end, chart_type, direction)
        self.assertIsNotNone(cached)
        self.assertEqual(len(cached), 3)
        self.assertAlmostEqual(cached["f1_0_618"], 80472.0)

if __name__ == "__main__":
    unittest.main()
