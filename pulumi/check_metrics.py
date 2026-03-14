import requests
import json
import os

url = "http://localhost:30090/api/v1/query"
user = "prometheus-admin"
password = os.environ.get('PROMETHEUS_PASSWORD')
if not password:
    raise SystemExit("Error: Set PROMETHEUS_PASSWORD environment variable before running this script")
params = {'query': 'up'}

try:
    response = requests.get(url, auth=(user, password), params=params)
    if response.status_code == 200:
        data = response.json()
        results = data.get('data', {}).get('result', [])
        print(f"Query 'up' Results: {len(results)}")
        for r in results:
             print(f"- {r['metric'].get('job')}: {r['value']}")
    else:
        print(f"Failed: {response.status_code} - {response.text}")
except Exception as e:
    print(f"Error: {e}")
