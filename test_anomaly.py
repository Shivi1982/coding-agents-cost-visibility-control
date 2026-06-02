#!/usr/bin/env python3
"""
test_anomaly.py — Test Script for Cost Anomaly Detection Lambda
================================================================

Seeds three specific anomaly scenarios into CloudWatch, then invokes
the anomaly detection Lambda locally (importing the handler directly)
to verify all three anomaly types are correctly detected.

Scenarios:
  1. alice.chen  — 5× cost spike today (SessionCost)
  2. carlos.r    — acceptance rate drop from ~80% to ~40% (BuildPassRate)
  3. bob.m       — session count spike from ~2/day to 15/day (PRsMerged)

Usage:
  python3 test_anomaly.py                    # Seed + invoke locally
  python3 test_anomaly.py --seed-only        # Seed data without invoking
  python3 test_anomaly.py --invoke-only      # Invoke without seeding
  python3 test_anomaly.py --dry-run          # Preview without pushing to CW
  python3 test_anomaly.py --invoke-lambda    # Invoke deployed Lambda via AWS

Requires: boto3, valid AWS credentials with cloudwatch:PutMetricData permission.
Region: us-west-2
"""

import sys
import json
import argparse
from datetime import datetime, timedelta, timezone

try:
    import boto3
except ImportError:
    import subprocess
    print("📦 boto3 not found — installing...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "boto3", "-q"])
    import boto3

# ── Constants ─────────────────────────────────────────────────────────────
REGION = "us-west-2"
NS_PRODUCTIVITY = "ClaudeCode/DevProductivity"
NS_TOOL_USAGE = "ClaudeCode/ToolUsage"
FUNCTION_NAME = "claude-code-roi-anomaly-detector"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Scenario 1: alice.chen — 5× Cost Spike
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def seed_alice_cost_spike(cw_client, dry_run: bool = False) -> None:
    """
    alice.chen normally spends ~$6-8/day on Claude Code.
    Today she has a $40 spike (5×).

    Seeds:
      - 7 days of baseline SessionCost: $6-8/day
      - Today: $40 (the anomaly)
    """
    developer = "alice.chen"
    today = datetime.now(timezone.utc).replace(hour=18, minute=0, second=0, microsecond=0)
    metrics = []

    print(f"\n  📊 Scenario 1: {developer} — 5× cost spike")
    print(f"     Baseline: ~$7/day for 7 days")
    print(f"     Today:    $40.00 (5× spike)")

    # Baseline: 7 days of normal cost ($6-8/day)
    baseline_costs = [6.50, 7.20, 6.80, 7.50, 7.00, 6.90, 7.80]
    for i, cost in enumerate(baseline_costs):
        ts = today - timedelta(days=7 - i)
        metrics.append({
            "MetricName": "SessionCost",
            "Value": cost,
            "Unit": "None",
            "Timestamp": ts,
            "Dimensions": [{"Name": "Developer", "Value": developer}],
        })
        print(f"     Day -{7 - i}: ${cost:.2f}")

    # Today: $40 cost spike
    metrics.append({
        "MetricName": "SessionCost",
        "Value": 40.00,
        "Unit": "None",
        "Timestamp": today,
        "Dimensions": [{"Name": "Developer", "Value": developer}],
    })
    print(f"     Day  0: $40.00 ← SPIKE")

    # Also seed some PRsMerged for discovery (so list_metrics finds alice)
    for i in range(7):
        ts = today - timedelta(days=7 - i)
        metrics.append({
            "MetricName": "PRsMerged",
            "Value": 3,
            "Unit": "Count",
            "Timestamp": ts,
            "Dimensions": [
                {"Name": "Developer", "Value": developer},
                {"Name": "Repository", "Value": "platform/backend"},
            ],
        })

    _push_metrics(cw_client, metrics, developer, dry_run)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Scenario 2: carlos.r — Acceptance Rate Drop (80% → 40%)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def seed_carlos_acceptance_drop(cw_client, dry_run: bool = False) -> None:
    """
    carlos.r had a ~80% build pass rate last week but it dropped to ~40%
    this week (>15% week-over-week decline triggers anomaly).

    Seeds:
      - Days -13 to -7: BuildPassRate ~0.80 (previous week)
      - Days -7 to -1:  BuildPassRate ~0.40 (current week)
    """
    developer = "carlos.r"
    today = datetime.now(timezone.utc).replace(hour=18, minute=0, second=0, microsecond=0)
    metrics = []

    print(f"\n  📊 Scenario 2: {developer} — acceptance rate drop")
    print(f"     Previous week: ~80% build pass rate")
    print(f"     This week:     ~40% build pass rate")

    # Previous week: ~80% acceptance rate (6 days, starting at -13)
    prev_week_rates = [0.80, 0.85, 0.75, 0.82, 0.78, 0.83]
    for i, rate in enumerate(prev_week_rates):
        ts = today - timedelta(days=13 - i)
        metrics.append({
            "MetricName": "BuildPassRate",
            "Value": rate,
            "Unit": "None",
            "Timestamp": ts,
            "Dimensions": [
                {"Name": "Developer", "Value": developer},
                {"Name": "Repository", "Value": "platform/backend"},
            ],
        })
        print(f"     Day -{13 - i}: {rate:.0%}")

    # Current week: ~40% acceptance rate (the drop)
    curr_week_rates = [0.42, 0.38, 0.45, 0.35, 0.40, 0.42, 0.38]
    for i, rate in enumerate(curr_week_rates):
        ts = today - timedelta(days=7 - i)
        metrics.append({
            "MetricName": "BuildPassRate",
            "Value": rate,
            "Unit": "None",
            "Timestamp": ts,
            "Dimensions": [
                {"Name": "Developer", "Value": developer},
                {"Name": "Repository", "Value": "platform/backend"},
            ],
        })
        print(f"     Day  -{7 - i}: {rate:.0%} ← DROP")

    # Also seed some SessionCost and PRsMerged for discovery
    for i in range(13):
        ts = today - timedelta(days=13 - i)
        metrics.append({
            "MetricName": "SessionCost",
            "Value": 2.50,
            "Unit": "None",
            "Timestamp": ts,
            "Dimensions": [{"Name": "Developer", "Value": developer}],
        })
        metrics.append({
            "MetricName": "PRsMerged",
            "Value": 1,
            "Unit": "Count",
            "Timestamp": ts, 
            "Dimensions": [
                {"Name": "Developer", "Value": developer},
                {"Name": "Repository", "Value": "platform/backend"},
            ],
        })

    _push_metrics(cw_client, metrics, developer, dry_run)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Scenario 3: bob.m — Session Count Spike (2/day → 15/day)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def seed_bob_session_spike(cw_client, dry_run: bool = False) -> None:
    """
    bob.m normally merges ~2 PRs/day. Today he suddenly has 15
    (>3× baseline triggers usage_spike anomaly).

    Seeds:
      - 7 days baseline: ~2 PRsMerged/day
      - Today: 15 PRsMerged (7.5× spike)
    """
    developer = "bob.m"
    today = datetime.now(timezone.utc).replace(hour=18, minute=0, second=0, microsecond=0)
    metrics = []

    print(f"\n  📊 Scenario 3: {developer} — session count spike")
    print(f"     Baseline: ~2 PRs/day for 7 days")
    print(f"     Today:    15 PRs (7.5× spike)")

    # Baseline: 7 days of normal activity (~2 PRs/day)
    baseline_sessions = [2, 1, 3, 2, 2, 1, 2]
    for i, count in enumerate(baseline_sessions):
        ts = today - timedelta(days=7 - i)
        metrics.append({
            "MetricName": "PRsMerged",
            "Value": count,
            "Unit": "Count",
            "Timestamp": ts,
            "Dimensions": [
                {"Name": "Developer", "Value": developer},
                {"Name": "Repository", "Value": "platform/backend"},
            ],
        })
        print(f"     Day -{7 - i}: {count} PRs")

    # Today: 15 PRs (the spike)
    metrics.append({
        "MetricName": "PRsMerged",
        "Value": 15,
        "Unit": "Count",
        "Timestamp": today,
        "Dimensions": [
            {"Name": "Developer", "Value": developer},
            {"Name": "Repository", "Value": "platform/backend"},
        ],
    })
    print(f"     Day  0: 15 PRs ← SPIKE")

    # Also seed SessionCost for discovery + cost check (normal)
    for i in range(8):
        ts = today - timedelta(days=7 - i)
        metrics.append({
            "MetricName": "SessionCost",
            "Value": 1.80,
            "Unit": "None",
            "Timestamp": ts,
            "Dimensions": [{"Name": "Developer", "Value": developer}],
        })

    _push_metrics(cw_client, metrics, developer, dry_run)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Helpers
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _push_metrics(cw_client, metrics: list, developer: str, dry_run: bool) -> None:
    """Push metrics to CloudWatch in batches, split by namespace."""
    if dry_run:
        print(f"     [DRY RUN] Would push {len(metrics)} data points for {developer}")
        return

    # Separate metrics by namespace based on MetricName
    tool_usage_metrics = ["SessionCost", "TokensUsed", "ToolInvocations"]

    ns_prod = []
    ns_tool = []
    for m in metrics:
        if m["MetricName"] in tool_usage_metrics:
            ns_tool.append(m)
        else:
            ns_prod.append(m)

    calls = 0
    for namespace, batch_metrics in [
        (NS_PRODUCTIVITY, ns_prod),
        (NS_TOOL_USAGE, ns_tool),
    ]:
        for i in range(0, len(batch_metrics), 20):
            batch = batch_metrics[i : i + 20]
            cw_client.put_metric_data(Namespace=namespace, MetricData=batch)
            calls += 1

    print(f"     ✅ Pushed {len(metrics)} data points ({calls} API calls)")


def invoke_lambda_locally() -> dict:
    """
    Import the anomaly Lambda handler and invoke it directly.
    This tests the full detection logic without deploying.
    """
    print("\n" + "=" * 60)
    print("  Invoking Lambda Handler Locally")
    print("=" * 60)

    # Import the handler from the same directory
    import importlib.util
    import os

    script_dir = os.path.dirname(os.path.abspath(__file__))
    module_path = os.path.join(script_dir, "lambda_cost_anomaly.py")

    spec = importlib.util.spec_from_file_location("lambda_cost_anomaly", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    # Simulate a CloudWatch Events scheduled event
    test_event = {
        "source": "aws.events",
        "detail-type": "Scheduled Event",
        "detail": {},
    }

    # Create a minimal context mock
    class MockContext:
        function_name = "test-anomaly-detector"
        memory_limit_in_mb = 256
        invoked_function_arn = "arn:aws:lambda:us-west-2:YOUR_ACCOUNT_ID:function:test"
        aws_request_id = "test-request-id"

        def get_remaining_time_in_millis(self):
            return 120000

    result = module.lambda_handler(test_event, MockContext())
    return result


def invoke_lambda_remote(lambda_client) -> dict:
    """Invoke the deployed Lambda function via AWS API."""
    print("\n" + "=" * 60)
    print("  Invoking Deployed Lambda Remotely")
    print("=" * 60)

    test_event = {
        "source": "aws.events",
        "detail-type": "Scheduled Event",
        "detail": {},
    }

    response = lambda_client.invoke(
        FunctionName=FUNCTION_NAME,
        InvocationType="RequestResponse",
        Payload=json.dumps(test_event),
    )

    payload = json.loads(response["Payload"].read())
    return payload


def validate_results(result: dict) -> None:
    """Validate that all three anomaly scenarios were detected."""
    print("\n" + "=" * 60)
    print("  Validation Results")
    print("=" * 60)

    body = result.get("body", "{}")
    if isinstance(body, str):
        body = json.loads(body)

    total = body.get("total_anomalies", 0)
    affected = body.get("affected_developers", [])
    types = body.get("anomaly_types", [])

    print(f"\n  Total anomalies detected: {total}")
    print(f"  Affected developers:     {affected}")
    print(f"  Anomaly types:           {types}")

    # Check expected outcomes
    checks = [
        ("alice.chen in affected", "alice.chen" in affected),
        ("carlos.r in affected", "carlos.r" in affected),
        ("bob.m in affected", "bob.m" in affected),
        ("cost_spike detected", "cost_spike" in types),
        ("acceptance_drop detected", "acceptance_drop" in types),
        ("usage_spike detected", "usage_spike" in types),
        ("at least 3 anomalies", total >= 3),
    ]

    print("")
    all_passed = True
    for label, passed in checks:
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"  {status}: {label}")
        if not passed:
            all_passed = False

    print("")
    if all_passed:
        print("  🎉 All validation checks passed!")
    else:
        print("  ⚠️  Some checks failed — review the Lambda output above")
        print("     Note: CW metrics may need a few minutes to become queryable")
        print("     Tip: Wait 2-3 minutes after seeding, then re-run with --invoke-only")

    print("")
    print("  Full response:")
    print(f"  {json.dumps(body, indent=2, default=str)}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Main
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def main():
    parser = argparse.ArgumentParser(
        description="Test the Claude Code ROI anomaly detection Lambda."
    )
    parser.add_argument(
        "--seed-only", action="store_true",
        help="Seed anomaly data without invoking the Lambda",
    )
    parser.add_argument(
        "--invoke-only", action="store_true",
        help="Invoke Lambda without seeding (use if data already seeded)",
    )
    parser.add_argument(
        "--invoke-lambda", action="store_true",
        help="Invoke the deployed Lambda via AWS API instead of locally",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Preview metrics without pushing to CloudWatch",
    )
    parser.add_argument(
        "--region", type=str, default=REGION,
        help=f"AWS region (default: {REGION})",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("  Claude Code ROI — Anomaly Detection Test Suite")
    print("=" * 60)
    print(f"  Region:       {args.region}")
    print(f"  Dry run:      {args.dry_run}")
    print(f"  Seed only:    {args.seed_only}")
    print(f"  Invoke only:  {args.invoke_only}")
    print(f"  Invoke mode:  {'remote (deployed Lambda)' if args.invoke_lambda else 'local (import handler)'}")
    print("=" * 60)

    cw_client = boto3.client("cloudwatch", region_name=args.region)

    # ── Seed Phase ────────────────────────────────────────────────
    if not args.invoke_only:
        print("\n📊 Seeding anomaly scenarios into CloudWatch...")
        seed_alice_cost_spike(cw_client, dry_run=args.dry_run)
        seed_carlos_acceptance_drop(cw_client, dry_run=args.dry_run)
        seed_bob_session_spike(cw_client, dry_run=args.dry_run)

        if args.dry_run:
            print("\n  ⚠️  DRY RUN complete — no data was pushed to CloudWatch")
            return

        print("\n  ✅ All test scenarios seeded successfully.")
        print("  ⏳ Waiting 5 seconds for CloudWatch to index metrics...")
        import time
        time.sleep(5)

    if args.seed_only:
        print("\n  Seed-only mode — skipping Lambda invocation.")
        print("  Run again with --invoke-only to test detection.")
        return

    # ── Invoke Phase ──────────────────────────────────────────────
    if args.invoke_lambda:
        lambda_client = boto3.client("lambda", region_name=args.region)
        result = invoke_lambda_remote(lambda_client)
    else:
        result = invoke_lambda_locally()

    # ── Validation Phase ──────────────────────────────────────────
    validate_results(result)


if __name__ == "__main__":
    main()
