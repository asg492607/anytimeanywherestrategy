import os
import json
import time
from datetime import datetime, timedelta
import pytz
from SmartApi import SmartConnect

IST = pytz.timezone('Asia/Kolkata')
API_KEY = "G2Tjf9cb"
CLIENT_CODE = "AACF753746"
PIN = "9545"
TOTP_SECRET = "TJKRQNWUMYSOSNINFPTKZ2YS74"
import pyotp

smartApi = SmartConnect(api_key=API_KEY)
totp = pyotp.TOTP(TOTP_SECRET).now()
res = smartApi.generateSession(CLIENT_CODE, PIN, totp)
if res['status']:
    print("Login successful")
    
    # Test SENSEX
    now = datetime.now(IST)
    params = {
        "exchange"   : "BSE",
        "symboltoken": "99919000",
        "interval"   : "THREE_MINUTE",
        "fromdate"   : (now - timedelta(days=7)).strftime("%Y-%m-%d %H:%M"),
        "todate"     : now.strftime("%Y-%m-%d %H:%M")
    }
    sx_res = smartApi.getCandleData(params)
    print("SENSEX 3m data:", sx_res.get('data', [])[:2] if isinstance(sx_res, dict) else sx_res)

    # Test CE
    params = {
        "exchange"   : "BFO",
        "symboltoken": "944596", # We'll just try finding the actual token for SENSEX2670276900CE if it exists, or just any option
        "interval"   : "THREE_MINUTE",
        "fromdate"   : (now - timedelta(days=7)).strftime("%Y-%m-%d %H:%M"),
        "todate"     : now.strftime("%Y-%m-%d %H:%M")
    }
    ce_res = smartApi.getCandleData(params)
    print("CE data status:", ce_res.get('status') if isinstance(ce_res, dict) else ce_res)
else:
    print("Login failed:", res)
