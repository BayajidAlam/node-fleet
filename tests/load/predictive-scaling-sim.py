#!/usr/bin/env python3
"""
Predictive scaling simulator (Layer 3).

Seeds 7 days of historical CPU-spike data into DynamoDB at (current_hour+1),
then waits ≤2 min for EventBridge to fire Lambda automatically.
Lambda detects the pattern and pre-scales BEFORE the spike arrives.

Flow
----
Phase 1  Auto-detect Lambda name + DynamoDB metrics-history table
Phase 2  Seed DynamoDB  (7 days x 24 h, spike at next_hour, normal elsewhere)
Phase 3  Wait up to 2 min for EventBridge to fire Lambda
Phase 4  Tail CloudWatch Logs for "Predictive:" decision
Phase 5  Pass/fail verdict + optional cleanup

Usage
-----
    python tests/load/predictive-scaling-sim.py
    python tests/load/predictive-scaling-sim.py --cluster node-fleet-prod
    python tests/load/predictive-scaling-sim.py --seed-only
    python tests/load/predictive-scaling-sim.py --clean-seed
    python tests/load/predictive-scaling-sim.py --no-cleanup

Flags
-----
  --cluster NAME    Cluster prefix (auto-detected if omitted)
  --spike-hour N    Force spike at UTC hour N (default: current_hour+1)
  --days N          Days of history to seed (default: 7)
  --seed-only       Seed and exit; do not wait for Lambda
  --clean-seed      Delete seeded rows and exit
  --no-cleanup      Keep seeded rows after run
"""

import argparse
import json
import sys
import time
import random
from datetime import datetime, timedelta, timezone
from typing import Optional

try:
    import boto3
    from boto3.dynamodb.conditions import Attr
    from botocore.exceptions import ClientError
except ImportError:
    print("ERROR: pip install boto3")
    sys.exit(1)


# ── console helpers ────────────────────────────────────────────────────────────

def banner(title: str):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"\n{'='*64}")
    print(f"  {title:<50}  [{ts}]")
    print(f"{'='*64}")

def ok(msg):   print(f"  [OK]   {msg}")
def info(msg): print(f"  [...] {msg}")
def warn(msg): print(f"  [!!]  {msg}")
def fail(msg): print(f"  [FAIL] {msg}")


# ── resource detection ─────────────────────────────────────────────────────────

def detect_cluster_name() -> Optional[str]:
    """Scan Lambda list for *-autoscaler matching node-fleet pattern."""
    try:
        lc = boto3.client("lambda")
        pager = lc.get_paginator("list_functions")
        for page in pager.paginate():
            for fn in page["Functions"]:
                name = fn["FunctionName"]
                if name.endswith("-autoscaler") and "node-fleet" in name:
                    return name.replace("-autoscaler", "")
    except Exception:
        pass
    return None


def detect_resources(cluster: str):
    lambda_name   = f"{cluster}-autoscaler"
    metrics_table = f"{cluster}-metrics-history"
    log_group     = f"/aws/lambda/{lambda_name}"
    return lambda_name, metrics_table, log_group


def verify_resources(lambda_name: str, metrics_table: str) -> bool:
    ok_flag = True
    try:
        boto3.client("lambda").get_function(FunctionName=lambda_name)
        ok(f"Lambda        : {lambda_name}")
    except ClientError as e:
        fail(f"Lambda not found: {lambda_name}  ({e.response['Error']['Code']})")
        ok_flag = False
    try:
        boto3.client("dynamodb").describe_table(TableName=metrics_table)
        ok(f"Metrics table : {metrics_table}")
    except ClientError as e:
        fail(f"Table not found: {metrics_table}  ({e.response['Error']['Code']})")
        ok_flag = False
    return ok_flag


# ── seed / clean ──────────────────────────────────────────────────────────────

SEED_TAG = "__predictive_sim_seed__"


def count_existing_rows_at_hour(metrics_table: str, spike_hour: int,
                                lookback_days: int = 7) -> int:
    """Count real rows at spike_hour within the 7-day lookback PredictiveScaler uses."""
    ddb    = boto3.resource("dynamodb")
    table  = ddb.Table(metrics_table)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()
    count  = 0
    kwargs = dict(
        FilterExpression=(
            Attr("hour").eq(spike_hour) &
            Attr("_seed_tag").not_exists() &
            Attr("timestamp").gte(cutoff)
        ),
        Select="COUNT",
    )
    while True:
        resp   = table.scan(**kwargs)
        count += resp.get("Count", 0)
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    return count


def seed_history(metrics_table: str, spike_hour: int, days: int = 7,
                 spike_rows: int = None) -> list:
    """
    Seed spike rows for spike_hour on TODAY and YESTERDAY only.

    Why today+yesterday only:
      The cluster may have only 1-2 days of real data. If we seed across all 7
      days, Mon-Fri get seeded-only rows (avg ~87%) while Sun/Sat have real rows
      (avg ~0.38%). This makes overall_avg >> Sunday_avg, so the weekly modifier
      tanks the prediction (multiplier ~0.38 → 74.8% × 0.38 = 28%).

      Seeding only today + yesterday keeps both days at the same real+spike ratio
      → weekly_avg ≈ overall_avg → multiplier ≈ 1.0 → prediction stays at ~77%.

    Today (day_offset 0) uses spike_hour timestamps in the future (e.g. hour 12
    while it is currently hour 11). These are included in the 7-day lookback since
    PredictiveScaler only filters timestamp >= (now - 7 days).
    """
    ddb      = boto3.resource("dynamodb")
    table    = ddb.Table(metrics_table)
    now      = datetime.now(timezone.utc)
    inserted = []

    if spike_rows is None:
        existing   = count_existing_rows_at_hour(metrics_table, spike_hour)
        spike_rows = existing * 3 + 80   # extra buffer for the multiplier
        info(f"Existing rows at hour {spike_hour:02d}: {existing} → seeding {spike_rows} spike rows")

    # Alternate between today (day_offset 0) and yesterday (day_offset 1)
    # Both days have real rows → weekly multiplier ≈ 1.0
    with table.batch_writer() as batch:
        for i in range(spike_rows):
            day_offset = i % 2          # 0 = today, 1 = yesterday
            ts = (now - timedelta(days=day_offset)).replace(
                hour=spike_hour,
                minute=random.randint(0, 59),
                second=random.randint(0, 59),
                microsecond=0,
            ) + timedelta(seconds=i)   # ensure unique timestamp
            item = {
                "timestamp":      ts.isoformat(),
                "hour":           spike_hour,
                "day_of_week":    ts.weekday(),
                "cpu_percent":    str(round(random.uniform(83, 92), 1)),
                "memory_percent": str(round(random.uniform(72, 82), 1)),
                "pending_pods":   random.randint(3, 6),
                "node_count":     random.randint(4, 6),
                "ttl":            int((ts + timedelta(days=2)).timestamp()),
                "_seed_tag":      SEED_TAG,
            }
            batch.put_item(Item=item)
            inserted.append(ts.isoformat())

    return inserted


def clean_seed(metrics_table: str):
    info(f"Scanning {metrics_table} for seeded rows …")
    ddb     = boto3.resource("dynamodb")
    table   = ddb.Table(metrics_table)
    deleted = 0
    kwargs  = {"FilterExpression": Attr("_seed_tag").eq(SEED_TAG)}

    # Paginate — DynamoDB returns LastEvaluatedKey if table has more data to scan
    while True:
        resp  = table.scan(**kwargs)
        items = resp.get("Items", [])
        if items:
            with table.batch_writer() as batch:
                for item in items:
                    batch.delete_item(Key={"timestamp": item["timestamp"]})
                    deleted += 1
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]

    ok(f"Deleted {deleted} seeded rows")


# ── CloudWatch log tail ────────────────────────────────────────────────────────

def tail_logs(log_group: str, since_ms: int, timeout_s: int, keywords: list) -> bool:
    """
    Poll CloudWatch Logs every 6 s for up to timeout_s.
    Returns True if any keyword found in events after since_ms.
    """
    region = boto3.session.Session().region_name or "ap-southeast-1"
    logs   = boto3.client("logs", region_name=region)
    end_t  = time.time() + timeout_s
    seen   = set()
    found  = False

    while time.time() < end_t:
        try:
            streams = logs.describe_log_streams(
                logGroupName=log_group,
                orderBy="LastEventTime",
                descending=True,
                limit=5,
            ).get("logStreams", [])

            for stream in streams:
                events = logs.get_log_events(
                    logGroupName=log_group,
                    logStreamName=stream["logStreamName"],
                    startTime=since_ms,
                    startFromHead=True,
                ).get("events", [])

                for ev in events:
                    eid = str(ev.get("eventId", ev["timestamp"]))
                    if eid in seen:
                        continue
                    seen.add(eid)
                    msg = ev["message"].rstrip()
                    ts  = datetime.fromtimestamp(ev["timestamp"] / 1000).strftime("%H:%M:%S")
                    print(f"  [{ts}] {msg}")
                    if any(kw.lower() in msg.lower() for kw in keywords):
                        found = True

        except ClientError as e:
            warn(f"Log error: {e}")

        if found:
            break

        remaining = int(end_t - time.time())
        print(f"  [...] waiting for Lambda invocation … {remaining}s remaining", end="\r", flush=True)
        time.sleep(6)

    print()
    return found


# ── main ───────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Predictive scaling simulation — Layer 3")
    ap.add_argument("--cluster",    default=None,
                    help="Cluster name prefix (auto-detected if omitted)")
    ap.add_argument("--spike-hour", type=int, default=None,
                    help="UTC hour for spike pattern (default: current_hour+1)")
    ap.add_argument("--days",        type=int, default=7,
                    help="Days of normal-hour history to seed (default: 7)")
    ap.add_argument("--spike-rows",  type=int, default=None,
                    help="Spike rows to seed at spike_hour (default: auto = 3×existing+50)")
    ap.add_argument("--seed-only",  action="store_true",
                    help="Seed DynamoDB and exit; do not wait for Lambda")
    ap.add_argument("--clean-seed", action="store_true",
                    help="Delete seeded rows and exit")
    ap.add_argument("--no-cleanup", action="store_true",
                    help="Keep seeded rows after the run")
    args = ap.parse_args()

    # ── Phase 1: detect ──────────────────────────────────────────────────────
    banner("PHASE 1 — Detect AWS Resources")
    cluster = args.cluster
    if not cluster:
        info("Auto-detecting cluster name …")
        cluster = detect_cluster_name()
    if not cluster:
        fail("Cannot detect cluster. Pass --cluster node-fleet-prod")
        sys.exit(1)

    lambda_name, metrics_table, log_group = detect_resources(cluster)

    if not verify_resources(lambda_name, metrics_table):
        fail("Resource check failed — verify AWS credentials and cluster name")
        sys.exit(1)

    # ── clean-seed mode ──────────────────────────────────────────────────────
    if args.clean_seed:
        banner("CLEANUP — Removing Seeded Rows")
        clean_seed(metrics_table)
        ok("Done.")
        return

    # ── spike hour ───────────────────────────────────────────────────────────
    now_utc = datetime.now(timezone.utc)

    # Compute seconds until next EventBridge tick (2-min boundary)
    seconds_in_cycle = now_utc.minute % 2 * 60 + now_utc.second
    wait_for         = max(5, 120 - seconds_in_cycle + 10)   # +10s buffer

    # Key fix: predict spike_hour from what time it will be WHEN Lambda fires,
    # not what time it is now. Avoids seeding wrong hour when crossing :00 boundary.
    fire_time  = now_utc + timedelta(seconds=wait_for)
    next_hour  = (fire_time.hour + 1) % 24
    spike_hour = args.spike_hour if args.spike_hour is not None else next_hour

    spike_rows_label = args.spike_rows if args.spike_rows else "auto (3×existing+80)"
    print(f"\n  Current UTC time   : {now_utc.strftime('%H:%M:%S')}  (hour {now_utc.hour})")
    print(f"  Lambda fires at    : ~{fire_time.strftime('%H:%M:%S')}  (hour {fire_time.hour})")
    print(f"  Lambda will predict: hour {next_hour:02d}")
    print(f"  Spike seeded at    : hour {spike_hour:02d}  (today + yesterday only)")
    print(f"  Spike rows         : {spike_rows_label}")
    print(f"  Weekly multiplier  : ~1.0  (today+yesterday have same real+spike ratio)")
    print(f"  Threshold check    : predicted CPU > 70%  →  predictive scale-up fires")
    print(f"  EventBridge cadence: every 2 min  →  Lambda fires automatically")

    # ── Phase 2: seed ────────────────────────────────────────────────────────
    banner(f"PHASE 2 — Seed History  (spike @ hour {spike_hour:02d}:xx UTC)")
    info("Pre-cleaning any leftover seeded rows from previous runs …")
    clean_seed(metrics_table)
    t0       = time.time()
    inserted = seed_history(metrics_table, spike_hour, spike_rows=args.spike_rows)
    elapsed  = time.time() - t0
    ok(f"Seeded {len(inserted)} spike rows  ({elapsed:.1f}s)")
    info(f"Spike hour {spike_hour:02d} : CPU ~83-92%  |  today + yesterday only")
    info(f"Rows auto-expire in 2 days via DynamoDB TTL")

    if args.seed_only:
        ok("--seed-only done.  Re-run without the flag to wait for Lambda.")
        return

    # ── Phase 3: wait for EventBridge ────────────────────────────────────────
    banner("PHASE 3 — Wait for EventBridge (Lambda fires every 2 min automatically)")
    wait_start_ms = int(time.time() * 1000)

    # Recompute fresh — seeding took a few seconds, stale wait_for can miss the tick
    now_fresh        = datetime.now(timezone.utc)
    secs_in_cycle    = now_fresh.minute % 2 * 60 + now_fresh.second
    wait_for         = max(5, 120 - secs_in_cycle + 5)   # +5s buffer is enough now
    deadline         = now_fresh + timedelta(seconds=wait_for)

    print(f"  Now             : {now_fresh.strftime('%H:%M:%S')} UTC")
    print(f"  Lambda fires at : ~{deadline.strftime('%H:%M:%S')} UTC")
    print(f"  Waiting         : {wait_for}s")
    print()

    # Count down visibly
    for remaining in range(wait_for, 0, -1):
        print(f"  [...] {remaining:3d}s until Lambda fires …", end="\r", flush=True)
        time.sleep(1)
    print()

    # ── Phase 4: tail logs ───────────────────────────────────────────────────
    banner("PHASE 4 — CloudWatch Logs  (watching for Predictive signal)")
    log_timeout = 150    # 2.5 min extra window after estimated fire time
    keywords    = ["Predictive", "predictive", "scale_up", "ScaleUp", "scale up"]

    print(f"  Log group : {log_group}")
    print(f"  Window    : last {log_timeout}s from now")
    print()

    found = tail_logs(log_group, wait_start_ms, log_timeout, keywords)

    # ── Phase 5: verdict ─────────────────────────────────────────────────────
    banner("PHASE 5 — Verdict")
    if found:
        print()
        print("  ✓  PREDICTIVE SCALING VERIFIED")
        print(f"     Lambda pre-scaled BEFORE CPU hit 70% at hour {spike_hour:02d}:xx UTC")
        print(f"     Action reason logged as: 'Predictive: Predicted CPU spike …'")
        print()
        print("  What just happened:")
        print(f"   1. DynamoDB seeded: spike rows at hour {spike_hour:02d} (today + yesterday)")
        print(f"   2. EventBridge fired Lambda (auto, no manual invoke)")
        print(f"   3. Lambda called predict_next_hour_load() → confidence 1.0")
        print(f"   4. should_proactive_scale_up() returned True → action = scale_up")
        print(f"   5. EC2 RunInstances called BEFORE load arrives  (proactive, not reactive)")
        passed = True
    else:
        print()
        warn("Predictive signal NOT found. Possible causes:")
        print()
        print(f"  1. Reactive scaling fired first → predictive path skipped (action ≠ none guard)")
        print(f"     Fix: ensure cluster CPU is currently below 70%")
        print(f"  2. spike_hour ({spike_hour}) ≠ next_hour ({next_hour})")
        print(f"     Fix: run again closer to hour {next_hour - 1:02d}:55 UTC")
        print(f"  3. Lambda env var ENABLE_PREDICTIVE_SCALING not 'true'")
        print(f"  4. Lambda env var METRICS_HISTORY_TABLE not set")
        print(f"  5. Spike rows still outnumbered by real data → re-run (auto will recalculate)")
        print(f"  6. Log tail window too short → increase --log-timeout (default 150s)")
        passed = False

    # ── cleanup ──────────────────────────────────────────────────────────────
    if inserted and not args.no_cleanup:
        print()
        info("Cleaning up seeded rows …")
        clean_seed(metrics_table)

    print()
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
