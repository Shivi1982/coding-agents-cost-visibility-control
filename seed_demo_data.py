#!/usr/bin/env python3
"""
seed_demo_data.py — Multi-Developer Demo Data Seeder for Claude Code ROI Dashboard
==================================================================================

Pushes 14 days of realistic CloudWatch metrics for 8 developer profiles,
simulating enterprise-scale Claude Code usage across multiple repositories.

Namespaces:
  - ClaudeCode/DevProductivity   (PRs, cycle time, lines, builds, reviews)
  - ClaudeCode/ToolUsage         (tool invocations, session cost, tokens)

Usage:
  python3 seed_demo_data.py                 # Seed all 8 developers, 14 days
  python3 seed_demo_data.py --days 7        # Last 7 days only
  python3 seed_demo_data.py --dry-run       # Preview without pushing to CW
  python3 seed_demo_data.py --dev alice.chen # Seed one developer only

Requires: boto3, valid AWS credentials with cloudwatch:PutMetricData permission.
Region: us-west-2
"""

import sys
import random
import math
import argparse
from datetime import datetime, timedelta, timezone
from collections import defaultdict

# ---------------------------------------------------------------------------
# Auto-install boto3 if missing
# ---------------------------------------------------------------------------
try:
    import boto3
except ImportError:
    import subprocess
    print("📦 boto3 not found — installing...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "boto3", "-q"])
    import boto3

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
REGION = "us-west-2"
NS_PRODUCTIVITY = "ClaudeCode/DevProductivity"
NS_TOOL_USAGE = "ClaudeCode/ToolUsage"

REPOSITORIES = [
    "platform/backend",
    "platform/frontend",
    "infra/terraform",
    "data/pipeline",
]

ALL_TOOLS = [
    "code_edit", "bash", "read_file", "web_search",
    "semantic_search", "code_review", "cicd_trigger", "calculator",
]

# CloudWatch PutMetricData limit: 1000 metric data points per call
CW_BATCH_LIMIT = 1000

# ---------------------------------------------------------------------------
# Developer Profiles
# ---------------------------------------------------------------------------
# Each profile defines the *weekly averages* and tool-usage weights.
# The seeder adds day-level and per-PR-level variance around these centres.
#
# Fields:
#   prs_per_week       — mean PRs merged per week
#   cycle_hours        — mean PR cycle time in hours
#   lines_added        — mean lines added per PR
#   remove_ratio       — fraction of lines_added that are removals
#   files_per_pr       — mean files changed per PR
#   build_pass_rate    — probability each PR build passes (0-1)
#   approvals_per_pr   — mean approvals per PR
#   commits_per_pr     — mean commits per PR
#   weekly_cost        — mean $ cost of Claude Code per week
#   tokens_input_wk    — mean input tokens per week
#   tokens_output_wk   — mean output tokens per week
#   tools              — dict of tool_name → relative daily invocation weight
#   repos              — which repos this dev works in (weighted)
#   score_label        — display label (for logging only)

DEVELOPERS = {
    "alice.chen": {
        "prs_per_week": 18,
        "cycle_hours": 4.2,
        "lines_added": 842,
        "remove_ratio": 0.30,
        "files_per_pr": 12,
        "build_pass_rate": 1.00,
        "approvals_per_pr": 2.1,
        "commits_per_pr": 3.4,
        "weekly_cost": 47.00,
        "tokens_input_wk": 2_800_000,
        "tokens_output_wk": 920_000,
        "tools": {
            "code_edit": 85, "bash": 42, "read_file": 55,
            "web_search": 18, "semantic_search": 22, "cicd_trigger": 8,
        },
        "repos": {"platform/backend": 0.50, "platform/frontend": 0.30, "data/pipeline": 0.20},
        "score_label": "⭐⭐⭐⭐⭐",
    },
    "priya.k": {
        "prs_per_week": 22,
        "cycle_hours": 2.1,
        "lines_added": 1840,
        "remove_ratio": 0.25,
        "files_per_pr": 18,
        "build_pass_rate": 0.98,
        "approvals_per_pr": 2.5,
        "commits_per_pr": 4.8,
        "weekly_cost": 32.00,
        "tokens_input_wk": 3_400_000,
        "tokens_output_wk": 1_250_000,
        "tools": {
            "code_edit": 110, "bash": 65, "read_file": 72,
            "web_search": 34, "semantic_search": 40, "code_review": 28,
            "cicd_trigger": 15, "calculator": 12,
        },
        "repos": {
            "platform/backend": 0.35, "platform/frontend": 0.25,
            "infra/terraform": 0.15, "data/pipeline": 0.25,
        },
        "score_label": "⭐⭐⭐⭐⭐⭐",
    },
    "bob.m": {
        "prs_per_week": 8,
        "cycle_hours": 12.1,
        "lines_added": 120,
        "remove_ratio": 0.15,
        "files_per_pr": 3,
        "build_pass_rate": 0.75,
        "approvals_per_pr": 1.2,
        "commits_per_pr": 1.8,
        "weekly_cost": 12.00,
        "tokens_input_wk": 480_000,
        "tokens_output_wk": 140_000,
        "tools": {
            "code_edit": 30,
        },
        "repos": {"platform/backend": 0.80, "platform/frontend": 0.20},
        "score_label": "⭐⭐",
    },
    "carlos.r": {
        "prs_per_week": 1,
        "cycle_hours": 26.4,
        "lines_added": 14,
        "remove_ratio": 0.10,
        "files_per_pr": 1,
        "build_pass_rate": 0.0,   # builds always fail
        "approvals_per_pr": 0.3,
        "commits_per_pr": 1.0,
        "weekly_cost": 18.50,
        "tokens_input_wk": 620_000,
        "tokens_output_wk": 85_000,
        "tools": {
            "code_edit": 14,
        },
        "repos": {"platform/backend": 1.0},
        "score_label": "❌ (struggling)",
    },
    "sarah.j": {
        "prs_per_week": 12,
        "cycle_hours": 6.0,
        "lines_added": 450,
        "remove_ratio": 0.22,
        "files_per_pr": 8,
        "build_pass_rate": 0.92,
        "approvals_per_pr": 1.8,
        "commits_per_pr": 2.6,
        "weekly_cost": 28.00,
        "tokens_input_wk": 1_800_000,
        "tokens_output_wk": 580_000,
        "tools": {
            "code_edit": 58, "bash": 25, "code_review": 18,
        },
        "repos": {"platform/backend": 0.40, "platform/frontend": 0.35, "data/pipeline": 0.25},
        "score_label": "⭐⭐⭐⭐",
    },
    "mike.t": {
        "prs_per_week": 5,
        "cycle_hours": 18.0,
        "lines_added": 200,
        "remove_ratio": 0.12,
        "files_per_pr": 4,
        "build_pass_rate": 0.80,
        "approvals_per_pr": 1.0,
        "commits_per_pr": 1.5,
        "weekly_cost": 8.00,
        "tokens_input_wk": 320_000,
        "tokens_output_wk": 95_000,
        "tools": {
            "code_edit": 20, "read_file": 10,
        },
        "repos": {"platform/frontend": 0.70, "platform/backend": 0.30},
        "score_label": "⭐⭐ (new to AI tools)",
    },
    "elena.v": {
        "prs_per_week": 15,
        "cycle_hours": 3.5,
        "lines_added": 680,
        "remove_ratio": 0.28,
        "files_per_pr": 10,
        "build_pass_rate": 0.95,
        "approvals_per_pr": 2.3,
        "commits_per_pr": 3.1,
        "weekly_cost": 38.00,
        "tokens_input_wk": 2_400_000,
        "tokens_output_wk": 780_000,
        "tools": {
            "code_edit": 72, "code_review": 45, "semantic_search": 30,
        },
        "repos": {
            "platform/backend": 0.40, "platform/frontend": 0.30,
            "infra/terraform": 0.20, "data/pipeline": 0.10,
        },
        "score_label": "⭐⭐⭐⭐⭐ (review heavy)",
    },
    "raj.p": {
        "prs_per_week": 10,
        "cycle_hours": 5.0,
        "lines_added": 520,
        "remove_ratio": 0.20,
        "files_per_pr": 7,
        "build_pass_rate": 0.97,
        "approvals_per_pr": 1.9,
        "commits_per_pr": 2.8,
        "weekly_cost": 25.00,
        "tokens_input_wk": 1_500_000,
        "tokens_output_wk": 480_000,
        "tools": {
            "code_edit": 50, "bash": 35, "cicd_trigger": 22, "calculator": 8,
        },
        "repos": {
            "infra/terraform": 0.45, "platform/backend": 0.30, "data/pipeline": 0.25,
        },
        "score_label": "⭐⭐⭐⭐ (CI/CD focused)",
    },
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def noisy(mean: float, cv: float = 0.25, floor: float = 0) -> float:
    """Return a value drawn from a log-normal distribution around *mean*.
    cv = coefficient of variation (0.25 → ±25 % typical spread).
    Clamps result to *floor* at minimum."""
    if mean <= 0:
        return max(floor, 0)
    sigma = math.sqrt(math.log(1 + cv ** 2))
    mu = math.log(mean) - sigma ** 2 / 2
    return max(floor, random.lognormvariate(mu, sigma))


def noisy_int(mean: float, cv: float = 0.25, floor: int = 0) -> int:
    return max(floor, int(round(noisy(mean, cv, floor))))


def weighted_choice(weights: dict) -> str:
    """Weighted random pick from {key: weight} dict."""
    keys = list(weights.keys())
    vals = [weights[k] for k in keys]
    return random.choices(keys, weights=vals, k=1)[0]


def day_activity_factor(weekday: int) -> float:
    """Simulate lower weekend activity. weekday: 0=Mon..6=Sun."""
    if weekday >= 5:          # Sat / Sun
        return random.uniform(0.05, 0.25)
    if weekday == 4:          # Friday
        return random.uniform(0.70, 0.95)
    return random.uniform(0.85, 1.15)  # Mon-Thu


def build_timestamp(base_date: datetime, hour_offset: float) -> datetime:
    """Return a timestamp at base_date + random hour within the work day."""
    # Spread PRs between 08:00 and 20:00 with some jitter
    hour = random.uniform(8, 20) + hour_offset * 0.01
    return base_date.replace(
        hour=int(min(hour, 23)),
        minute=random.randint(0, 59),
        second=random.randint(0, 59),
    )


# ---------------------------------------------------------------------------
# Metric assembly
# ---------------------------------------------------------------------------

def generate_pr_metrics(dev_name: str, profile: dict, num_days: int) -> list:
    """Generate per-PR CloudWatch metric data points for a developer."""
    metrics = []
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    total_prs = 0

    # --- Generate PRs per-week then scatter across weekdays ---
    # This avoids rounding-to-zero for low-volume devs (e.g. carlos.r: 1 PR/week).
    num_weeks = max(1, num_days // 7)
    remaining_days = num_days % 7

    # Collect all days and their activity weights
    all_days = []
    for day_offset in range(num_days, 0, -1):
        day = today - timedelta(days=day_offset)
        factor = day_activity_factor(day.weekday())
        all_days.append((day, factor))

    # Assign PRs: draw weekly count via Poisson, scatter across that week's days
    for week_idx in range(num_weeks + (1 if remaining_days else 0)):
        week_start = week_idx * 7
        week_end = min(week_start + 7, num_days)
        week_days = all_days[week_start:week_end]
        if not week_days:
            continue

        prs_this_week = max(0, int(random.gauss(profile["prs_per_week"], profile["prs_per_week"] * 0.25)))
        # Scatter PRs across the week weighted by activity factor
        for pr_seq in range(prs_this_week):
            day, factor = random.choices(week_days, weights=[w for _, w in week_days], k=1)[0]
            ts = build_timestamp(day, pr_seq)
            repo = weighted_choice(profile["repos"])

            lines_added = noisy_int(profile["lines_added"], cv=0.40, floor=1)
            lines_removed = noisy_int(lines_added * profile["remove_ratio"], cv=0.50, floor=0)
            files_changed = noisy_int(profile["files_per_pr"], cv=0.35, floor=1)
            cycle_hours = noisy(profile["cycle_hours"], cv=0.35, floor=0.15)
            approvals = noisy_int(profile["approvals_per_pr"], cv=0.30, floor=0)
            commits = noisy_int(profile["commits_per_pr"], cv=0.30, floor=1)
            build_passed = random.random() < profile["build_pass_rate"]

            dims = [
                {"Name": "Developer", "Value": dev_name},
                {"Name": "Repository", "Value": repo},
            ]

            pr_metrics = [
                {"MetricName": "PRsMerged",        "Value": 1,              "Unit": "Count",  "Timestamp": ts, "Dimensions": dims},
                {"MetricName": "PRCycleTimeHours",  "Value": round(cycle_hours, 2), "Unit": "None", "Timestamp": ts, "Dimensions": dims},
                {"MetricName": "LinesAddedPerPR",   "Value": lines_added,    "Unit": "Count",  "Timestamp": ts, "Dimensions": dims},
                {"MetricName": "LinesRemovedPerPR", "Value": lines_removed,  "Unit": "Count",  "Timestamp": ts, "Dimensions": dims},
                {"MetricName": "FilesChangedPerPR", "Value": files_changed,  "Unit": "Count",  "Timestamp": ts, "Dimensions": dims},
                {"MetricName": "ApprovalsPerPR",    "Value": approvals,      "Unit": "Count",  "Timestamp": ts, "Dimensions": dims},
                {"MetricName": "CommitsPerPR",      "Value": commits,        "Unit": "Count",  "Timestamp": ts, "Dimensions": dims},
                {
                    "MetricName": "BuildPassRate",
                    "Value": 1.0 if build_passed else 0.0,
                    "Unit": "None",
                    "Timestamp": ts,
                    "Dimensions": dims + [{"Name": "Status", "Value": "passed" if build_passed else "failed"}],
                },
            ]
            metrics.extend(pr_metrics)
            total_prs += 1

    return metrics, total_prs


def generate_tool_metrics(dev_name: str, profile: dict, num_days: int) -> list:
    """Generate daily tool-usage, cost, and token metrics."""
    metrics = []
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    for day_offset in range(num_days, 0, -1):
        day = today - timedelta(days=day_offset)
        weekday = day.weekday()
        factor = day_activity_factor(weekday)

        # Tool invocations
        ts = day.replace(hour=18, minute=0, second=0)  # end-of-day rollup
        for tool_name, daily_mean in profile["tools"].items():
            invocations = noisy_int(daily_mean / 7.0 * factor, cv=0.40, floor=0)  # daily_mean is weekly
            if invocations > 0:
                metrics.append({
                    "MetricName": "ToolInvocations",
                    "Value": invocations,
                    "Unit": "Count",
                    "Timestamp": ts,
                    "Dimensions": [
                        {"Name": "Developer", "Value": dev_name},
                        {"Name": "Tool", "Value": tool_name},
                    ],
                })

        # Session cost (daily)
        daily_cost = noisy(profile["weekly_cost"] / 7.0 * factor, cv=0.30, floor=0)
        metrics.append({
            "MetricName": "SessionCost",
            "Value": round(daily_cost, 4),
            "Unit": "None",
            "Timestamp": ts,
            "Dimensions": [{"Name": "Developer", "Value": dev_name}],
        })

        # Tokens used (daily — input + output separately)
        tokens_in = noisy_int(profile["tokens_input_wk"] / 7.0 * factor, cv=0.30, floor=0)
        tokens_out = noisy_int(profile["tokens_output_wk"] / 7.0 * factor, cv=0.30, floor=0)
        metrics.append({
            "MetricName": "TokensUsed",
            "Value": tokens_in,
            "Unit": "Count",
            "Timestamp": ts,
            "Dimensions": [
                {"Name": "Developer", "Value": dev_name},
                {"Name": "TokenType", "Value": "input"},
            ],
        })
        metrics.append({
            "MetricName": "TokensUsed",
            "Value": tokens_out,
            "Unit": "Count",
            "Timestamp": ts,
            "Dimensions": [
                {"Name": "Developer", "Value": dev_name},
                {"Name": "TokenType", "Value": "output"},
            ],
        })

    return metrics


# ---------------------------------------------------------------------------
# CloudWatch push
# ---------------------------------------------------------------------------

def push_metrics(client, namespace: str, metric_data: list, dry_run: bool = False):
    """Push metric data in CW-compliant batches (max 1000 per call).
    CloudWatch PutMetricData also has a 20-metric limit per request,
    but with single-valued metrics we can batch up to 1000.
    Actually, AWS enforces max 1000 *items* per PutMetricData call."""
    # AWS docs: max 1000 MetricData items per PutMetricData call.
    # However, in practice the limit is **20** MetricDatum items per call
    # for the standard API. We use 20 to be safe.
    BATCH = 20
    total_calls = 0
    for i in range(0, len(metric_data), BATCH):
        batch = metric_data[i : i + BATCH]
        if dry_run:
            total_calls += 1
            continue
        client.put_metric_data(Namespace=namespace, MetricData=batch)
        total_calls += 1
    return total_calls


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Seed CloudWatch with realistic Claude Code developer metrics."
    )
    parser.add_argument("--days", type=int, default=13, help="Number of days of history (default: 13, max safe for CloudWatch 2-week window)")
    parser.add_argument("--dry-run", action="store_true", help="Generate data without pushing to CloudWatch")
    parser.add_argument("--dev", type=str, default=None, help="Seed a single developer (e.g. alice.chen)")
    parser.add_argument("--region", type=str, default=REGION, help=f"AWS region (default: {REGION})")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for reproducibility (default: 42)")
    args = parser.parse_args()

    random.seed(args.seed)

    print("=" * 72)
    print("  Claude Code ROI Dashboard — Demo Data Seeder")
    print("=" * 72)
    print(f"  Region:     {args.region}")
    print(f"  Days:       {args.days}")
    print(f"  Dry run:    {args.dry_run}")
    print(f"  Seed:       {args.seed}")
    print(f"  Namespaces: {NS_PRODUCTIVITY}")
    print(f"              {NS_TOOL_USAGE}")
    print("=" * 72)

    if args.dry_run:
        cw = None
        print("  ⚠️  DRY RUN — no data will be pushed to CloudWatch\n")
    else:
        cw = boto3.client("cloudwatch", region_name=args.region)
        print()

    # Filter developers if --dev flag is set
    if args.dev:
        if args.dev not in DEVELOPERS:
            print(f"❌ Unknown developer '{args.dev}'. Available: {', '.join(DEVELOPERS.keys())}")
            sys.exit(1)
        devs = {args.dev: DEVELOPERS[args.dev]}
    else:
        devs = DEVELOPERS

    grand_total_metrics = 0
    grand_total_calls = 0
    summary = []

    for dev_name, profile in devs.items():
        print(f"🔄 Seeding {dev_name} ({profile['score_label']})...")

        # --- Productivity metrics (per-PR) ---
        pr_metrics, total_prs = generate_pr_metrics(dev_name, profile, args.days)
        calls_prod = push_metrics(cw, NS_PRODUCTIVITY, pr_metrics, dry_run=args.dry_run)

        # --- Tool / cost / token metrics (daily rollup) ---
        tool_metrics = generate_tool_metrics(dev_name, profile, args.days)
        calls_tool = push_metrics(cw, NS_TOOL_USAGE, tool_metrics, dry_run=args.dry_run)

        total_metrics = len(pr_metrics) + len(tool_metrics)
        total_calls = calls_prod + calls_tool
        grand_total_metrics += total_metrics
        grand_total_calls += total_calls

        tools_str = ", ".join(profile["tools"].keys())
        repos_str = ", ".join(profile["repos"].keys())
        print(f"   ✅ {total_prs} PRs across {args.days} days | {total_metrics} data points | {total_calls} API calls")
        print(f"      Tools: {tools_str}")
        print(f"      Repos: {repos_str}")
        print()

        summary.append({
            "developer": dev_name,
            "prs": total_prs,
            "metrics": total_metrics,
            "api_calls": total_calls,
        })

    # --- Summary ---
    print("=" * 72)
    print("  SEEDING COMPLETE")
    print("=" * 72)
    print(f"  {'Developer':<16} {'PRs':>6} {'Metrics':>10} {'API Calls':>10}")
    print(f"  {'-' * 16} {'-' * 6} {'-' * 10} {'-' * 10}")
    for s in summary:
        print(f"  {s['developer']:<16} {s['prs']:>6} {s['metrics']:>10,} {s['api_calls']:>10,}")
    print(f"  {'-' * 16} {'-' * 6} {'-' * 10} {'-' * 10}")
    total_prs_all = sum(s["prs"] for s in summary)
    print(f"  {'TOTAL':<16} {total_prs_all:>6} {grand_total_metrics:>10,} {grand_total_calls:>10,}")
    print()

    if args.dry_run:
        print("  ⚠️  No data was pushed (dry run). Rerun without --dry-run to push to CloudWatch.")
    else:
        print(f"  ✅ All data pushed to CloudWatch in {args.region}")
        print(f"     → Open CloudWatch console → Metrics → Custom namespaces")
        print(f"     → {NS_PRODUCTIVITY}")
        print(f"     → {NS_TOOL_USAGE}")
    print()


if __name__ == "__main__":
    main()
