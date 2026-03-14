import requests
import json
import os

url = "http://localhost:30090/api/v1/targets"
user = "prometheus-admin"
password = os.environ.get('PROMETHEUS_PASSWORD')
if not password:
    raise SystemExit("Error: Set PROMETHEUS_PASSWORD environment variable before running this script")

try:
    response = requests.get(url, auth=(user, password))
    if response.status_code == 200:
        data = response.json()
        active_targets = data.get('data', {}).get('activeTargets', [])
        print(f"Active Targets Count: {len(active_targets)}")
        for t in active_targets:
             print(f"- {t['labels'].get('job')}: {t['health']} ({t['labels'].get('instance')})")
    else:
        print(f"Failed: {response.status_code} - {response.text}")
except Exception as e:
    print(f"Error: {e}")
