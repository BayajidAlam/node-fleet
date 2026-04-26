#!/usr/bin/env python3
"""
Traffic simulator for demo-app metrics generation.
Sends random 1-50 req/s for configurable duration.

Usage:
    python traffic-sim.py <APP_URL> [--duration 120] [--min-rps 1] [--max-rps 50]

Example (from master node):
    APP_IP=$(kubectl get svc demo-app -n default -o jsonpath='{.spec.clusterIP}')
    python traffic-sim.py http://$APP_IP

    # Or via NodePort:
    MASTER_IP=$(curl -s http://169.254.169.254/latest/meta-data/public-ipv4)
    python traffic-sim.py http://$MASTER_IP:30080
"""

import sys
import time
import random
import threading
import argparse
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

try:
    import requests
    requests.packages.urllib3.disable_warnings()
except ImportError:
    print("ERROR: requests not installed. Run: pip install requests")
    sys.exit(1)

# Stats
stats = {"ok": 0, "err": 0, "total": 0}
stats_lock = threading.Lock()


def hit(base_url, session):
    endpoint, method, body = random.choice([
        ("/api/data",        "GET",  None),
        ("/api/data",        "GET",  None),
        ("/api/data",        "GET",  None),
        ("/health",          "GET",  None),
        ("/api/heavy",       "GET",  None),
        ("/api/process",     "POST", {"complexity": random.choice(["simple", "normal", "complex"])}),
        ("/api/process",     "POST", {"complexity": "simple"}),
        ("/api/queue/add",   "POST", {"count": random.randint(1, 3)}),
    ])

    try:
        if method == "GET":
            r = session.get(f"{base_url}{endpoint}", timeout=5)
        else:
            r = session.post(f"{base_url}{endpoint}", json=body, timeout=5)

        with stats_lock:
            stats["total"] += 1
            if r.status_code < 500:
                stats["ok"] += 1
            else:
                stats["err"] += 1
    except Exception:
        with stats_lock:
            stats["total"] += 1
            stats["err"] += 1


def clear_queue(base_url, session):
    try:
        r = session.post(f"{base_url}/api/queue/clear", timeout=5)
        data = r.json()
        cleared = data.get("cleared_count", "?")
        print(f"Queue cleared ({cleared} items removed)")
    except Exception as e:
        print(f"Queue clear failed: {e}")


def run(base_url, duration, min_rps, max_rps):
    session = requests.Session()
    end = time.time() + duration
    interval_report = time.time() + 10

    print(f"\n{'='*50}")
    print(f"Target : {base_url}")
    print(f"Duration: {duration}s | RPS range: {min_rps}-{max_rps}")
    print(f"Started : {datetime.now().strftime('%H:%M:%S')}")
    print(f"{'='*50}")
    clear_queue(base_url, session)
    print()

    with ThreadPoolExecutor(max_workers=60) as pool:
        while time.time() < end:
            rps = random.randint(min_rps, max_rps)
            sleep = 1.0 / rps

            pool.submit(hit, base_url, session)
            time.sleep(sleep)

            if time.time() >= interval_report:
                elapsed = duration - (end - time.time())
                with stats_lock:
                    t = stats["total"]
                    ok = stats["ok"]
                    err = stats["err"]
                print(
                    f"[{elapsed:5.0f}s] total={t:5d}  ok={ok:5d}  err={err:4d}"
                    f"  err%={err/t*100:.1f}%  current_rps={rps}"
                )
                interval_report = time.time() + 10

    with stats_lock:
        t = stats["total"]
        ok = stats["ok"]
        err = stats["err"]

    print(f"\n{'='*50}")
    print(f"Done | total={t}  ok={ok}  err={err}  err%={err/t*100:.1f}%")
    print(f"{'='*50}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Demo-app traffic simulator")
    parser.add_argument("url", help="App base URL, e.g. http://10.0.1.100:30080")
    parser.add_argument("--duration", type=int, default=120, help="Seconds to run (default 120)")
    parser.add_argument("--min-rps", type=int, default=1,  help="Min requests/sec (default 1)")
    parser.add_argument("--max-rps", type=int, default=50, help="Max requests/sec (default 50)")
    args = parser.parse_args()

    run(args.url.rstrip("/"), args.duration, args.min_rps, args.max_rps)
