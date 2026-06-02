"""
Lambda Cost Anomaly Detector — Claude Code ROI System
======================================================
Scheduled Lambda (every 6 hours via CloudWatch Events) that auto-discovers
all developers from the ClaudeCode/DevProductivity and ClaudeCode/ToolUsage
CloudWatch namespaces, then applies statistical anomaly detection across
three signal categories:

  1. Cost Spike      — z-score > 2.0 on daily SessionCost
  2. Acceptance Drop — BuildPassRate week-over-week decline > 15%
  3. Usage Anomaly   — SessionCount change > 3× (spike or drop)

Outputs:
  • SNS notification  → "claude-code-roi-anomalies" topic
  • CloudWatch metric → "AnomalyDetected" in ClaudeCode/DevProductivity
  • Structured log    → /claude-code/anomaly-detection CW Logs

All developer names are discovered dynamically via list_metrics —
ZERO hardcoding.
"""

from __future__ import annotations

import json
import math
import os
import time
import boto3
from datetime import datetime, timedelta, timezone
from typing import Any

# ── AWS Clients (reused across warm invocations) ──────────────────────────
REGION = os.environ.get("AWS_REGION", "us-west-2")
cw = boto3.client("cloudwatch", region_name=REGION)
sns = boto3.client("sns", region_name=REGION)
logs = boto3.client("logs", region_name=REGION)

# ── Constants ─────────────────────────────────────────────────────────────
NS_PRODUCTIVITY = "ClaudeCode/DevProductivity"
NS_TOOL_USAGE = "ClaudeCode/ToolUsage"
ANOMALY_LOG_GROUP = "/claude-code/anomaly-detection"
ANOMALY_LOG_STREAM_PREFIX = "anomaly-events"

# SNS topic ARN — resolved at cold start from env or constructed
ACCOUNT_ID = os.environ.get("ACCOUNT_ID", "YOUR_ACCOUNT_ID")
SNS_TOPIC_ARN = os.environ.get(
    "SNS_TOPIC_ARN",
    f"arn:aws:sns:{REGION}:{ACCOUNT_ID}:claude-code-roi-anomalies",
)

# ── Thresholds ────────────────────────────────────────────────────────────
COST_ZSCORE_WARNING = 2.0       # z-score ≥ 2.0 → warning
COST_ZSCORE_CRITICAL = 3.0      # z-score ≥ 3.0 → critical
ACCEPTANCE_DROP_THRESHOLD = 0.15 # >15% week-over-week decline
SESSION_SPIKE_FACTOR = 3.0       # >3× change in session count
ROLLING_WINDOW_DAYS = 7          # baseline window for rolling stats
MIN_DATAPOINTS = 3               # minimum days of data for valid baseline


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Developer Discovery
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def discover_developers() -> set:
    """
    Auto-discover all unique Developer dimension values across both
    ClaudeCode namespaces using list_metrics pagination.

    Returns a set of developer name strings.
    """
    developers = set()

    for namespace in [NS_PRODUCTIVITY, NS_TOOL_USAGE]:
        paginator = cw.get_paginator("list_metrics")
        page_iterator = paginator.paginate(
            Namespace=namespace,
            Dimensions=[{"Name": "Developer"}],
        )
        for page in page_iterator:
            for metric in page.get("Metrics", []):
                for dim in metric.get("Dimensions", []):
                    if dim["Name"] == "Developer":
                        developers.add(dim["Value"])

    print(f"[discovery] Found {len(developers)} developers: {sorted(developers)}")
    return developers


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  CloudWatch Metric Queries
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _build_metric_query(
    query_id: str,
    namespace: str,
    metric_name: str,
    developer: str,
    stat: str = "Sum",
    period: int = 86400,
) -> dict:
    """Build a single MetricDataQuery using MetricStat (not expression)."""
    # Sanitize query_id: must match ^[a-z][a-zA-Z0-9_]*$
    safe_id = query_id.replace(".", "_").replace("-", "_").lower()
    return {
        "Id": safe_id,
        "MetricStat": {
            "Metric": {
                "Namespace": namespace,
                "MetricName": metric_name,
                "Dimensions": [
                    {"Name": "Developer", "Value": developer},
                ],
            },
            "Period": period,
            "Stat": stat,
        },
        "ReturnData": True,
    }


def get_daily_metric_series(
    developer: str,
    namespace: str,
    metric_name: str,
    stat: str = "Sum",
    days: int = 8,
) -> list[tuple[datetime, float]]:
    """
    Fetch a daily time series for a specific metric + developer.
    Returns list of (timestamp, value) tuples sorted chronologically.

    Uses get_metric_data with MetricStat for proper server-side aggregation.
    Fetches `days` days ending now (includes today's partial data).
    """
    now = datetime.now(timezone.utc)
    start_time = now - timedelta(days=days)

    query_id = f"q_{metric_name}_{developer}".replace(".", "_").replace("-", "_").lower()
    # Ensure starts with letter and within 255 chars
    if not query_id[0].isalpha():
        query_id = "m_" + query_id
    query_id = query_id[:255]

    try:
        response = cw.get_metric_data(
            MetricDataQueries=[
                _build_metric_query(
                    query_id=query_id,
                    namespace=namespace,
                    metric_name=metric_name,
                    developer=developer,
                    stat=stat,
                    period=86400,  # 1 day
                ),
            ],
            StartTime=start_time,
            EndTime=now,
            ScanBy="TimestampAscending",
        )
    except Exception as e:
        print(f"[metric_query] Error fetching {metric_name} for {developer}: {e}")
        return []

    results = response.get("MetricDataResults", [])
    if not results:
        return []

    timestamps = results[0].get("Timestamps", [])
    values = results[0].get("Values", [])

    # Pair and sort chronologically
    series = sorted(zip(timestamps, values), key=lambda x: x[0])
    return series


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Statistical Helpers
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def compute_rolling_stats(values: list[float]) -> dict:
    """
    Compute mean and standard deviation over a list of values.
    Returns dict with mean, stddev, count.
    """
    n = len(values)
    if n == 0:
        return {"mean": 0.0, "stddev": 0.0, "count": 0}

    mean = sum(values) / n
    if n < 2:
        return {"mean": mean, "stddev": 0.0, "count": n}

    variance = sum((x - mean) ** 2 for x in values) / (n - 1)  # sample variance
    stddev = math.sqrt(variance)
    return {"mean": round(mean, 4), "stddev": round(stddev, 4), "count": n}


def compute_zscore(current: float, mean: float, stddev: float) -> float:
    """Compute z-score. Returns 0.0 if stddev is zero (no variation)."""
    if stddev == 0:
        if current == mean:
            return 0.0
        # No variance but value differs — flag as significant
        return 10.0 if current > mean else -10.0
    return round((current - mean) / stddev, 4)


def week_over_week_change(
    series: list[tuple[datetime, float]],
) -> dict | None:
    """
    Split the last 14 days of data into two 7-day windows.
    Compute average for each window and return the percentage change.

    Returns dict with prev_week_avg, curr_week_avg, pct_change
    or None if insufficient data.
    """
    if len(series) < 4:
        return None

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=7)

    prev_week = [v for ts, v in series if ts < cutoff]
    curr_week = [v for ts, v in series if ts >= cutoff]

    if not prev_week or not curr_week:
        return None

    prev_avg = sum(prev_week) / len(prev_week)
    curr_avg = sum(curr_week) / len(curr_week)

    if prev_avg == 0:
        pct_change = 0.0 if curr_avg == 0 else 1.0
    else:
        pct_change = (curr_avg - prev_avg) / prev_avg

    return {
        "prev_week_avg": round(prev_avg, 4),
        "curr_week_avg": round(curr_avg, 4),
        "pct_change": round(pct_change, 4),
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Anomaly Detection Logic
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _classify_severity(z_score: float) -> str:
    """Map z-score magnitude to severity level."""
    abs_z = abs(z_score)
    if abs_z >= COST_ZSCORE_CRITICAL:
        return "critical"
    return "warning"


def _recommended_action(anomaly_type: str, severity: str, developer: str) -> str:
    """Generate human-readable recommended action for each anomaly type."""
    actions = {
        "cost_spike": {
            "warning": (
                f"Review {developer}'s recent Claude Code sessions for unusually "
                f"long conversations or large file processing. Consider setting "
                f"per-session token limits."
            ),
            "critical": (
                f"URGENT: {developer} has a severe cost spike. Check for runaway "
                f"sessions or recursive tool loops immediately. Consider pausing "
                f"the developer's access until root cause is identified."
            ),
        },
        "acceptance_drop": {
            "warning": (
                f"{developer}'s code acceptance/build pass rate is declining. "
                f"Schedule a 1:1 to review their Claude Code workflow and "
                f"prompt patterns. Consider pairing with a power user."
            ),
            "critical": (
                f"URGENT: {developer}'s acceptance rate has collapsed. This may "
                f"indicate a fundamental workflow issue or model mismatch. "
                f"Review recent PRs and Claude Code session logs immediately."
            ),
        },
        "usage_spike": {
            "warning": (
                f"{developer}'s session count spiked significantly. Verify this "
                f"is intentional (e.g., sprint push). Monitor cost impact."
            ),
            "critical": (
                f"URGENT: {developer} has an extreme session spike. Check for "
                f"automated/scripted usage or credential sharing. Investigate "
                f"session origins."
            ),
        },
        "usage_drop": {
            "warning": (
                f"{developer} appears to have stopped using Claude Code. "
                f"Check if they're blocked, have tooling issues, or have "
                f"voluntarily disengaged. Proactive outreach recommended."
            ),
            "critical": (
                f"{developer} has near-zero activity after sustained usage. "
                f"This may indicate access issues, frustration, or team change. "
                f"Investigate and re-engage."
            ),
        },
    }
    return actions.get(anomaly_type, {}).get(severity, "Review developer activity.")


def detect_anomalies_for_developer(developer: str) -> list[dict]:
    """
    Run all anomaly detection checks for a single developer.
    Returns a list of anomaly records (may be empty).
    """
    anomalies = []
    now_iso = datetime.now(timezone.utc).isoformat()

    # ── 1. Cost Spike Detection (z-score on SessionCost) ──────────────
    cost_series = get_daily_metric_series(
        developer=developer,
        namespace=NS_TOOL_USAGE,
        metric_name="SessionCost",
        stat="Sum",
        days=ROLLING_WINDOW_DAYS + 1,  # +1 to separate today from baseline
    )

    if len(cost_series) >= MIN_DATAPOINTS:
        # Separate today's value from the rolling baseline
        # Today = last data point if it's within the last 24 hours
        today_cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        baseline_values = [v for ts, v in cost_series if ts < today_cutoff]
        today_values = [v for ts, v in cost_series if ts >= today_cutoff]
        today_cost = sum(today_values) if today_values else 0.0

        if baseline_values and len(baseline_values) >= MIN_DATAPOINTS:
            stats = compute_rolling_stats(baseline_values)
            z_score = compute_zscore(today_cost, stats["mean"], stats["stddev"])

            print(
                f"[cost] {developer}: today=${today_cost:.2f}, "
                f"mean=${stats['mean']:.2f}, stddev=${stats['stddev']:.2f}, "
                f"z={z_score:.2f}"
            )

            if z_score >= COST_ZSCORE_WARNING:
                severity = _classify_severity(z_score)
                anomalies.append({
                    "developer": developer,
                    "anomaly_type": "cost_spike",
                    "current_value": round(today_cost, 4),
                    "baseline_value": stats["mean"],
                    "baseline_stddev": stats["stddev"],
                    "z_score": z_score,
                    "severity": severity,
                    "recommended_action": _recommended_action("cost_spike", severity, developer),
                    "detected_at": now_iso,
                    "window_days": ROLLING_WINDOW_DAYS,
                    "datapoints": stats["count"],
                })
    else:
        print(f"[cost] {developer}: insufficient data ({len(cost_series)} points)")

    # ── 2. Acceptance Rate Drop (BuildPassRate week-over-week) ────────
    acceptance_series = get_daily_metric_series(
        developer=developer,
        namespace=NS_PRODUCTIVITY,
        metric_name="BuildPassRate",
        stat="Average",
        days=14,  # need 2 weeks for WoW comparison
    )

    wow = week_over_week_change(acceptance_series)
    if wow is not None:
        pct_drop = -wow["pct_change"]  # negative change = drop, so negate
        print(
            f"[acceptance] {developer}: prev={wow['prev_week_avg']:.2%}, "
            f"curr={wow['curr_week_avg']:.2%}, change={wow['pct_change']:+.2%}"
        )

        if pct_drop > ACCEPTANCE_DROP_THRESHOLD:
            z_score_equiv = pct_drop / ACCEPTANCE_DROP_THRESHOLD  # normalized score
            severity = "critical" if pct_drop > 0.30 else "warning"
            anomalies.append({
                "developer": developer,
                "anomaly_type": "acceptance_drop",
                "current_value": wow["curr_week_avg"],
                "baseline_value": wow["prev_week_avg"],
                "baseline_stddev": 0.0,
                "z_score": round(z_score_equiv, 4),
                "severity": severity,
                "recommended_action": _recommended_action("acceptance_drop", severity, developer),
                "detected_at": now_iso,
                "window_days": 14,
                "pct_change": wow["pct_change"],
                "datapoints": len(acceptance_series),
            })
    else:
        print(f"[acceptance] {developer}: insufficient data for WoW comparison")

    # ── 3. Session Count Anomaly (PRsMerged as proxy for activity) ────
    # We use PRsMerged as the session activity signal.
    # A sudden spike may indicate runaway automation; a drop may mean
    # the developer stopped using the tool.
    session_series = get_daily_metric_series(
        developer=developer,
        namespace=NS_PRODUCTIVITY,
        metric_name="PRsMerged",
        stat="Sum",
        days=ROLLING_WINDOW_DAYS + 1,
    )

    if len(session_series) >= MIN_DATAPOINTS:
        today_cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        baseline_sessions = [v for ts, v in session_series if ts < today_cutoff]
        today_sessions_list = [v for ts, v in session_series if ts >= today_cutoff]
        today_sessions = sum(today_sessions_list) if today_sessions_list else 0.0

        if baseline_sessions and len(baseline_sessions) >= MIN_DATAPOINTS:
            stats = compute_rolling_stats(baseline_sessions)
            baseline_avg = stats["mean"]

            # Check for spike (>3× baseline)
            if baseline_avg > 0 and today_sessions > baseline_avg * SESSION_SPIKE_FACTOR:
                ratio = today_sessions / baseline_avg
                z_score = compute_zscore(today_sessions, stats["mean"], stats["stddev"])
                severity = "critical" if ratio > 5.0 else "warning"

                print(
                    f"[session_spike] {developer}: today={today_sessions}, "
                    f"baseline_avg={baseline_avg:.1f}, ratio={ratio:.1f}x"
                )

                anomalies.append({
                    "developer": developer,
                    "anomaly_type": "usage_spike",
                    "current_value": today_sessions,
                    "baseline_value": baseline_avg,
                    "baseline_stddev": stats["stddev"],
                    "z_score": z_score,
                    "severity": severity,
                    "recommended_action": _recommended_action("usage_spike", severity, developer),
                    "detected_at": now_iso,
                    "window_days": ROLLING_WINDOW_DAYS,
                    "spike_ratio": round(ratio, 2),
                    "datapoints": stats["count"],
                })

            # Check for drop (< 1/3× baseline, only if baseline is meaningful)
            elif baseline_avg >= 1.0 and today_sessions < baseline_avg / SESSION_SPIKE_FACTOR:
                ratio = today_sessions / baseline_avg if baseline_avg > 0 else 0
                z_score = compute_zscore(today_sessions, stats["mean"], stats["stddev"])
                severity = "critical" if today_sessions == 0 else "warning"

                print(
                    f"[session_drop] {developer}: today={today_sessions}, "
                    f"baseline_avg={baseline_avg:.1f}, ratio={ratio:.2f}x"
                )

                anomalies.append({
                    "developer": developer,
                    "anomaly_type": "usage_drop",
                    "current_value": today_sessions,
                    "baseline_value": baseline_avg,
                    "baseline_stddev": stats["stddev"],
                    "z_score": z_score,
                    "severity": severity,
                    "recommended_action": _recommended_action("usage_drop", severity, developer),
                    "detected_at": now_iso,
                    "window_days": ROLLING_WINDOW_DAYS,
                    "drop_ratio": round(ratio, 4),
                    "datapoints": stats["count"],
                })
            else:
                print(
                    f"[session] {developer}: today={today_sessions}, "
                    f"baseline_avg={baseline_avg:.1f} — normal"
                )
    else:
        print(f"[session] {developer}: insufficient data ({len(session_series)} points)")

    return anomalies


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Output: SNS Notification
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def publish_sns_notification(anomalies: list[dict]) -> None:
    """
    Publish a summary notification to the SNS anomaly topic.
    Groups anomalies by severity for clear email formatting.
    """
    if not anomalies:
        return

    critical = [a for a in anomalies if a["severity"] == "critical"]
    warnings = [a for a in anomalies if a["severity"] == "warning"]

    subject = f"🚨 Claude Code ROI: {len(anomalies)} Anomal{'y' if len(anomalies) == 1 else 'ies'} Detected"

    lines = [
        "=" * 60,
        "  Claude Code ROI — Anomaly Detection Report",
        f"  Detected at: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        "=" * 60,
        "",
    ]

    if critical:
        lines.append(f"🔴 CRITICAL ({len(critical)}):")
        lines.append("-" * 40)
        for a in critical:
            lines.append(f"  Developer:  {a['developer']}")
            lines.append(f"  Type:       {a['anomaly_type']}")
            lines.append(f"  Current:    {a['current_value']}")
            lines.append(f"  Baseline:   {a['baseline_value']}")
            lines.append(f"  Z-Score:    {a['z_score']}")
            lines.append(f"  Action:     {a['recommended_action']}")
            lines.append("")

    if warnings:
        lines.append(f"🟡 WARNING ({len(warnings)}):")
        lines.append("-" * 40)
        for a in warnings:
            lines.append(f"  Developer:  {a['developer']}")
            lines.append(f"  Type:       {a['anomaly_type']}")
            lines.append(f"  Current:    {a['current_value']}")
            lines.append(f"  Baseline:   {a['baseline_value']}")
            lines.append(f"  Z-Score:    {a['z_score']}")
            lines.append(f"  Action:     {a['recommended_action']}")
            lines.append("")

    lines.extend([
        "=" * 60,
        "  Dashboard: CloudWatch > Dashboards > ClaudeCodeROI",
        "  Log Group: /claude-code/anomaly-detection",
        "=" * 60,
    ])

    message = "\n".join(lines)

    try:
        sns.publish(
            TopicArn=SNS_TOPIC_ARN,
            Subject=subject[:100],  # SNS subject max 100 chars
            Message=message,
        )
        print(f"[sns] Published notification with {len(anomalies)} anomalies")
    except Exception as e:
        print(f"[sns] Error publishing notification: {e}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Output: CloudWatch Custom Metric
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def publish_anomaly_metrics(anomalies: list[dict]) -> None:
    """
    Publish AnomalyDetected metric to CloudWatch for each anomaly.
    This enables dashboard widgets and CloudWatch Alarms on anomaly counts.
    """
    if not anomalies:
        # Publish a zero-value metric so the time series stays continuous
        try:
            cw.put_metric_data(
                Namespace=NS_PRODUCTIVITY,
                MetricData=[{
                    "MetricName": "AnomalyDetected",
                    "Value": 0,
                    "Unit": "Count",
                    "Dimensions": [
                        {"Name": "AnomalyType", "Value": "none"},
                        {"Name": "Severity", "Value": "none"},
                    ],
                }],
            )
        except Exception as e:
            print(f"[metric] Error publishing zero-count metric: {e}")
        return

    # Batch metrics (max 20 per PutMetricData call)
    metric_data = []
    for anomaly in anomalies:
        metric_data.append({
            "MetricName": "AnomalyDetected",
            "Value": 1,
            "Unit": "Count",
            "Dimensions": [
                {"Name": "Developer", "Value": anomaly["developer"]},
                {"Name": "AnomalyType", "Value": anomaly["anomaly_type"]},
                {"Name": "Severity", "Value": anomaly["severity"]},
            ],
        })

    # Push in batches of 20 (CloudWatch limit)
    for i in range(0, len(metric_data), 20):
        batch = metric_data[i : i + 20]
        try:
            cw.put_metric_data(Namespace=NS_PRODUCTIVITY, MetricData=batch)
        except Exception as e:
            print(f"[metric] Error publishing anomaly batch {i}: {e}")

    print(f"[metric] Published {len(metric_data)} AnomalyDetected metrics")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Output: Structured CloudWatch Logs
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _ensure_log_group_and_stream(stream_name: str) -> None:
    """Create the log group + stream if they don't already exist."""
    try:
        logs.create_log_group(logGroupName=ANOMALY_LOG_GROUP)
    except logs.exceptions.ResourceAlreadyExistsException:
        pass
    except Exception as e:
        print(f"[logs] Error creating log group: {e}")

    try:
        logs.create_log_stream(
            logGroupName=ANOMALY_LOG_GROUP, logStreamName=stream_name
        )
    except logs.exceptions.ResourceAlreadyExistsException:
        pass
    except Exception as e:
        print(f"[logs] Error creating log stream: {e}")


def _put_structured_log(event_records: list[dict]) -> None:
    """
    Write structured JSON events to CloudWatch Logs.
    Stream name: anomaly-events/YYYY/MM/DD (daily rotation).
    """
    if not event_records:
        return

    now = datetime.now(timezone.utc)
    stream_name = f"{ANOMALY_LOG_STREAM_PREFIX}/{now.strftime('%Y/%m/%d')}"
    _ensure_log_group_and_stream(stream_name)

    timestamp_ms = int(now.timestamp() * 1000)

    # Build log events — each anomaly is a separate log event for Logs Insights
    log_events = []
    for i, record in enumerate(event_records):
        log_events.append({
            "timestamp": timestamp_ms + i,  # unique timestamp per event
            "message": json.dumps(record, default=str),
        })

    # Get sequence token
    try:
        resp = logs.describe_log_streams(
            logGroupName=ANOMALY_LOG_GROUP,
            logStreamNamePrefix=stream_name,
            limit=1,
        )
        streams = resp.get("logStreams", [])
        seq_token = streams[0].get("uploadSequenceToken") if streams else None
    except Exception:
        seq_token = None

    put_kwargs = {
        "logGroupName": ANOMALY_LOG_GROUP,
        "logStreamName": stream_name,
        "logEvents": log_events,
    }
    if seq_token:
        put_kwargs["sequenceToken"] = seq_token

    try:
        logs.put_log_events(**put_kwargs)
        print(f"[logs] Wrote {len(log_events)} events to {ANOMALY_LOG_GROUP}/{stream_name}")
    except logs.exceptions.InvalidSequenceTokenException as e:
        # Race condition — retry with correct token
        correct_token = str(e).split("sequenceToken is: ")[-1].strip()
        if correct_token and correct_token != "null":
            put_kwargs["sequenceToken"] = correct_token
        else:
            put_kwargs.pop("sequenceToken", None)
        try:
            logs.put_log_events(**put_kwargs)
            print(f"[logs] Wrote {len(log_events)} events (retry)")
        except Exception as e2:
            print(f"[logs] Error writing events on retry: {e2}")
    except Exception as e:
        print(f"[logs] Error writing events: {e}")


def publish_anomaly_logs(anomalies: list[dict], run_summary: dict) -> None:
    """
    Write anomaly records + run summary to structured CW Logs.
    Each record is a separate JSON line for Logs Insights queries.
    """
    records = []

    # Add run summary as the first log event
    records.append({
        "event_type": "anomaly_detection_run",
        **run_summary,
    })

    # Add each anomaly as a separate event
    for anomaly in anomalies:
        records.append({
            "event_type": "anomaly_detected",
            **anomaly,
        })

    _put_structured_log(records)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Lambda Handler
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def lambda_handler(event, context):
    """
    Cost Anomaly Detection — Scheduled every 6 hours via CloudWatch Events.

    1. Discovers all developers from CloudWatch metrics (zero hardcoding)
    2. For each developer, checks three anomaly signals
    3. Publishes results to SNS, CloudWatch Metrics, and CW Logs
    """
    start_time = time.time()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")

    print(f"[handler] Anomaly detection run started: {run_id}")
    print(f"[handler] Region: {REGION}, SNS Topic: {SNS_TOPIC_ARN}")

    try:
        # ── Step 1: Discover developers ──────────────────────────────
        developers = discover_developers()
        if not developers:
            print("[handler] No developers found in CloudWatch. Exiting.")
            return {
                "statusCode": 200,
                "body": json.dumps({
                    "message": "No developers found — nothing to analyze",
                    "run_id": run_id,
                }),
            }

        # ── Step 2: Detect anomalies for each developer ──────────────
        all_anomalies = []
        developers_analyzed = 0
        errors = []

        for developer in sorted(developers):
            try:
                print(f"\n[handler] Analyzing: {developer}")
                dev_anomalies = detect_anomalies_for_developer(developer)
                all_anomalies.extend(dev_anomalies)
                developers_analyzed += 1

                if dev_anomalies:
                    print(
                        f"[handler] {developer}: "
                        f"{len(dev_anomalies)} anomal{'y' if len(dev_anomalies) == 1 else 'ies'} detected"
                    )
                else:
                    print(f"[handler] {developer}: no anomalies")
            except Exception as e:
                error_msg = f"Error analyzing {developer}: {e}"
                print(f"[handler] {error_msg}")
                errors.append(error_msg)

        # ── Step 3: Build run summary ────────────────────────────────
        elapsed = round(time.time() - start_time, 2)
        run_summary = {
            "run_id": run_id,
            "developers_discovered": len(developers),
            "developers_analyzed": developers_analyzed,
            "total_anomalies": len(all_anomalies),
            "critical_count": len([a for a in all_anomalies if a["severity"] == "critical"]),
            "warning_count": len([a for a in all_anomalies if a["severity"] == "warning"]),
            "anomaly_types": list(set(a["anomaly_type"] for a in all_anomalies)),
            "affected_developers": list(set(a["developer"] for a in all_anomalies)),
            "errors": errors,
            "elapsed_seconds": elapsed,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        print(f"\n{'=' * 60}")
        print(f"  Run Summary: {run_id}")
        print(f"  Developers: {developers_analyzed}/{len(developers)}")
        print(f"  Anomalies:  {run_summary['total_anomalies']} "
              f"({run_summary['critical_count']} critical, "
              f"{run_summary['warning_count']} warning)")
        print(f"  Elapsed:    {elapsed}s")
        print(f"{'=' * 60}")

        # ── Step 4: Publish outputs ──────────────────────────────────

        # 4a. SNS notification (only if anomalies found)
        if all_anomalies:
            publish_sns_notification(all_anomalies)

        # 4b. CloudWatch AnomalyDetected metric
        publish_anomaly_metrics(all_anomalies)

        # 4c. Structured logs
        publish_anomaly_logs(all_anomalies, run_summary)

        # ── Return response ──────────────────────────────────────────
        return {
            "statusCode": 200,
            "body": json.dumps({
                "message": "Anomaly detection complete",
                **run_summary,
            }, default=str),
        }

    except Exception as exc:
        error_msg = f"[handler] Fatal error: {exc}"
        print(error_msg)

        # Attempt to log the error
        try:
            _put_structured_log([{
                "event_type": "anomaly_detection_error",
                "error": str(exc),
                "run_id": run_id,
            }])
        except Exception:
            pass

        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(exc), "run_id": run_id}),
        }
