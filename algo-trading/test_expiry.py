import pyotp
from SmartApi import SmartConnect
from datetime import datetime, timedelta, timezone
import pandas as pd

API_KEY = 'G2Tjf9cb'
CLIENT_CODE = 'AACF753746'
PASSWORD = '9545'
TOTP_SECRET = 'TJKRQNWUMYSOSNINFPTKZ2YS74'
IST = timezone(timedelta(hours=5, minutes=30))

smartApi = SmartConnect(api_key=API_KEY)
totp = pyotp.TOTP(TOTP_SECRET).now()
data = smartApi.generateSession(CLIENT_CODE, PASSWORD, totp)

if data['status']:
    now = datetime.now(IST)
    historicParam = {
        'exchange': 'BSE',
        'symboltoken': '99919000',
        'interval': 'ONE_DAY',
        'fromdate': (now - timedelta(days=60)).strftime('%Y-%m-%d 09:15'),
        'todate': now.strftime('%Y-%m-%d 15:30')
    }
    res = smartApi.getCandleData(historicParam)
    if res.get('data'):
        df = pd.DataFrame(res['data'], columns=['time','open','high','low','close','vol'])
        df['time'] = pd.to_datetime(df['time']).dt.date
        df.set_index('time', inplace=True)

        print("All daily data (last 15 days):")
        print(df.tail(15)[['open','high','low','close']])

        # SENSEX expires every Friday
        # Previous contract week = Last Friday to the Thursday before that
        today = now.date()
        weekday = today.weekday()  # 0=Mon, 4=Fri, 6=Sun
        
        # Find last completed Friday
        # If today is Friday (4) or after, last expiry was last Friday
        days_to_last_friday = (weekday - 4) % 7
        last_friday = today - timedelta(days=days_to_last_friday)
        prev_thursday = last_friday - timedelta(days=1)  # Thursday before last Friday
        prev_friday = prev_thursday - timedelta(days=6)  # Friday a week before

        print(f"\nToday: {today} (weekday={weekday})")
        print(f"Current contract week: {last_friday} (Fri) to next Thu")
        print(f"Prev contract week: {prev_friday} (Fri) to {prev_thursday} (Thu)")

        mask = (df.index >= prev_friday) & (df.index <= prev_thursday)
        prev_week = df[mask]
        print("\nPrev contract week data:")
        print(prev_week[['high','low']])
        
        prev_high = prev_week['high'].max()
        prev_low = prev_week['low'].min()
        diff = prev_high - prev_low
        print(f"\n=== CORRECT FIB ANCHOR POINTS ===")
        print(f"HIGH: {prev_high:.2f}")
        print(f"LOW:  {prev_low:.2f}")
        print(f"DIFF: {diff:.2f}")
        print(f"\n=== ALL FIB LEVELS ===")
        print(f"1.414 (purple):  {prev_high + diff * 0.414:.2f}")
        print(f"1.272 (blue):    {prev_high + diff * 0.272:.2f}")
        print(f"1.000 (blue):    {prev_high:.2f}")
        print(f"0.786 (purple):  {prev_low + diff * 0.786:.2f}")
        print(f"0.618 (blue):    {prev_low + diff * 0.618:.2f}")
        print(f"0.500 (blue):    {prev_low + diff * 0.500:.2f}")
        print(f"0.382 (blue):    {prev_low + diff * 0.382:.2f}")
        print(f"0.236 (purple):  {prev_low + diff * 0.236:.2f}")
        print(f"0.000 (blue):    {prev_low:.2f}")
        print(f"-0.272 (blue):   {prev_low - diff * 0.272:.2f}")
        print(f"-0.414 (purple): {prev_low - diff * 0.414:.2f}")
