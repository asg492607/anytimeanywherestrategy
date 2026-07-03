import os
import json
import time
from datetime import datetime, timedelta
import pytz
import urllib.request
from collections import defaultdict

url = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
print("Downloading scrip master...")
req = urllib.request.urlopen(url)
scrip_master = json.loads(req.read())

print("Loaded", len(scrip_master), "tokens")

today = datetime.now().strftime('%d%b%Y').upper()
print("Today:", today)

sensex_options = []
for item in scrip_master:
    if item['name'] == 'SENSEX' and item['instrumenttype'] == 'OPTIDX':
        sensex_options.append(item)

# Group by expiry
expiries = set(item['expiry'] for item in sensex_options)
sorted_expiries = sorted(list(expiries), key=lambda x: datetime.strptime(x, "%d%b%Y"))
print("Available expiries for SENSEX:", sorted_expiries[:5])

if sorted_expiries:
    nearest_expiry = sorted_expiries[0]
    print("Nearest expiry:", nearest_expiry)
    
    # Find ATM strike
    # Let's just find the first CE and PE for a middle strike
    opts = [o for o in sensex_options if o['expiry'] == nearest_expiry]
    if opts:
        ce = [o for o in opts if o['symbol'].endswith('CE')][0]
        pe = [o for o in opts if o['symbol'].endswith('PE')][0]
        print("Example CE:", ce['symbol'], ce['token'])
        print("Example PE:", pe['symbol'], pe['token'])

for item in scrip_master:
    if item['name'] == 'SENSEX' and item['exch_seg'] == 'BSE':
        print("SENSEX Index token:", item['symbol'], item['token'])
