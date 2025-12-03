import requests
import time
from datetime import datetime, timedelta

# Calculate timestamps
current_timestamp = int(time.time())
two_months_ago = datetime.now() - timedelta(days=60)
two_months_ago_timestamp = int(two_months_ago.timestamp())

url = f"https://mainnet.zklighter.elliot.ai/api/v1/candlesticks?market_id=2&resolution=5m&start_timestamp={two_months_ago_timestamp}&end_timestamp={current_timestamp}&count_back=2000&set_timestamp_to_end=true"

print(f"URL: {url}")

headers = {"accept": "application/json"}

try:
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    data = response.json()
    
    if isinstance(data, list):
        print(f"Received {len(data)} records")
        if len(data) > 0:
            print("First record:", data[0])
            print("Last record:", data[-1])
    elif isinstance(data, dict):
         print("Keys:", data.keys())
         # Try to find the list
         for k, v in data.items():
             if isinstance(v, list):
                 print(f"Key '{k}' contains list of {len(v)} items")
                 if len(v) > 0:
                     print("First item:", v[0])
    else:
        print("Unknown response type")
        print(data)

except Exception as e:
    print(f"Error: {e}")
