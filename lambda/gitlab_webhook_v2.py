"""
Lambda GitLab Webhook v2 — Full MR Metadata Extraction
========================================================
Receives GitLab Merge Request webhook events via API Gateway.

CloudWatch Metrics (9 metrics, all dimensioned by Developer + Repository):
  1. PRsMerged              — count of merged MRs
  2. PRCycleTimeHours       — hours from MR open → merge
  3. BuildPassRate           — 1.0 (passed) or 0.0 (failed)
  4. LinesAddedPerPR        — additions from the diff
  5. LinesRemovedPerPR      — deletions from the diff
  6. FilesChangedPerPR      — number of files touched
  7. ApprovalsPerPR          — number of approvals on the MR
  8. ReviewCommentsPerPR     — user_notes_count from the MR
  9. CommitsPerPR            — commits_count from the MR

CloudWatch Logs (structured JSON → /claude-code/dev-productivity):
  Full event record for Logs Insights queries.

All values extracted dynamically from the webhook payload — ZERO hardcoding.
"""

from __future__ import annotations

import json
import time
import boto3
from datetime import datetime, timezone

# ── AWS Clients (reused across warm invocations) ──────────────────────
cw = boto3.client("cloudwatch", region_name="us-west-2")
logs = boto3.client("logs", region_name="us-west-2")

# ── Constants ─────────────────────────────────────────────────────────
CW_NAMESPACE = "ClaudeCode/DevProductivity"
LOG_GROUP = "/claude-code/dev-productivity"
LOG_STREAM_PREFIX = "webhook-events"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Helpers
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _parse_iso(ts: str) -> datetime | None:
    """Parse ISO-8601 timestamps from GitLab (handles Z and +00:00)."""
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _safe_int(value, default: int = 0) -> int:
    """Coerce a value to int safely."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _cycle_time_hours(created_at: str, merged_at: str) -> float:
    """Calculate hours between MR creation and merge."""
    created = _parse_iso(created_at)
    merged = _parse_iso(merged_at)
    if created and merged:
        return round((merged - created).total_seconds() / 3600, 2)
    return 0.0


def _extract_build_status(mr: dict, body: dict) -> tuple[str, bool]:
    """
    Determine build/pipeline status from the payload.
    GitLab sends pipeline info in multiple locations — check all.
    Returns (status_string, passed_bool).
    """
    # Priority 1: last_pipeline in the MR attributes (most reliable)
    last_pipeline = mr.get("last_pipeline") or mr.get("head_pipeline") or {}
    pipeline_status = last_pipeline.get("status", "")

    # Priority 2: top-level pipeline object (sent in pipeline events)
    if not pipeline_status:
        pipeline_status = body.get("pipeline", {}).get("status", "")

    # Priority 3: merge_status field (fallback)
    if not pipeline_status:
        merge_status = mr.get("merge_status", "")
        if merge_status in ("can_be_merged", "can_be_merged_auto_merge_enabled"):
            pipeline_status = "success"
        elif merge_status == "cannot_be_merged":
            pipeline_status = "failed"
        else:
            pipeline_status = "unknown"

    passed = pipeline_status == "success"
    return pipeline_status, passed


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  CloudWatch Logs — write structured JSON event
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _ensure_log_group_and_stream(stream_name: str) -> None:
    """Create the log group + stream if they don't already exist."""
    try:
        logs.create_log_group(logGroupName=LOG_GROUP)
    except logs.exceptions.ResourceAlreadyExistsException:
        pass

    try:
        logs.create_log_stream(logGroupName=LOG_GROUP, logStreamName=stream_name)
    except logs.exceptions.ResourceAlreadyExistsException:
        pass


def _put_structured_log(event_record: dict) -> None:
    """
    Write a single structured JSON event to CloudWatch Logs.
    Stream name: webhook-events/YYYY/MM/DD  (daily rotation for easy browsing).
    """
    now = datetime.now(timezone.utc)
    stream_name = f"{LOG_STREAM_PREFIX}/{now.strftime('%Y/%m/%d')}"
    _ensure_log_group_and_stream(stream_name)

    timestamp_ms = int(now.timestamp() * 1000)

    # Get the upload sequence token (needed for existing streams)
    try:
        resp = logs.describe_log_streams(
            logGroupName=LOG_GROUP,
            logStreamNamePrefix=stream_name,
            limit=1,
        )
        streams = resp.get("logStreams", [])
        seq_token = streams[0].get("uploadSequenceToken") if streams else None
    except Exception:
        seq_token = None

    put_kwargs = {
        "logGroupName": LOG_GROUP,
        "logStreamName": stream_name,
        "logEvents": [
            {
                "timestamp": timestamp_ms,
                "message": json.dumps(event_record, default=str),
            }
        ],
    }
    if seq_token:
        put_kwargs["sequenceToken"] = seq_token

    try:
        logs.put_log_events(**put_kwargs)
    except logs.exceptions.InvalidSequenceTokenException as e:
        # Race condition — retry with the correct token from the exception
        correct_token = str(e).split("sequenceToken is: ")[-1].strip()
        if correct_token and correct_token != "null":
            put_kwargs["sequenceToken"] = correct_token
            logs.put_log_events(**put_kwargs)
        else:
            # Token is null → remove it and retry
            put_kwargs.pop("sequenceToken", None)
            logs.put_log_events(**put_kwargs)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  CloudWatch Metrics — 9 metrics, Developer + Repository dimensions
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _build_metric(name: str, value: float, unit: str, developer: str, repository: str) -> dict:
    """Build a single MetricDatum with standard dimensions."""
    return {
        "MetricName": name,
        "Value": value,
        "Unit": unit,
        "Dimensions": [
            {"Name": "Developer", "Value": developer},
            {"Name": "Repository", "Value": repository},
        ],
    }


def _publish_metrics(
    developer: str,
    repository: str,
    cycle_time_hours: float,
    build_passed: bool,
    lines_added: int,
    lines_removed: int,
    files_changed: int,
    approvals: int,
    review_comments: int,
    commits_count: int,
) -> None:
    """Publish all 9 metrics in a single PutMetricData call."""
    metrics = [
        _build_metric("PRsMerged",           1,                              "Count", developer, repository),
        _build_metric("PRCycleTimeHours",     cycle_time_hours,               "None",  developer, repository),
        _build_metric("BuildPassRate",        1.0 if build_passed else 0.0,   "None",  developer, repository),
        _build_metric("LinesAddedPerPR",      lines_added,                    "Count", developer, repository),
        _build_metric("LinesRemovedPerPR",    lines_removed,                  "Count", developer, repository),
        _build_metric("FilesChangedPerPR",    files_changed,                  "Count", developer, repository),
        _build_metric("ApprovalsPerPR",       approvals,                      "Count", developer, repository),
        _build_metric("ReviewCommentsPerPR",  review_comments,                "Count", developer, repository),
        _build_metric("CommitsPerPR",         commits_count,                  "Count", developer, repository),
    ]

    # CloudWatch allows max 1000 metrics per call — 9 is well under
    cw.put_metric_data(Namespace=CW_NAMESPACE, MetricData=metrics)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Lambda Handler
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def lambda_handler(event, context):
    """
    GitLab MR Webhook → CloudWatch Metrics + Structured Logs.

    Trigger: API Gateway POST from GitLab webhook.
    Processes only 'merge_request' events where state == 'merged'.
    """

    try:
        # ── Parse incoming payload ────────────────────────────────
        body = json.loads(event.get("body", "{}"))

        # Gate: only merge_request events
        object_kind = body.get("object_kind", "")
        if object_kind != "merge_request":
            return {
                "statusCode": 200,
                "body": json.dumps({
                    "message": f"Ignored event: {object_kind}",
                    "action": "skipped",
                }),
            }

        mr = body.get("object_attributes", {})

        # Gate: only merged MRs
        state = mr.get("state", "")
        action = mr.get("action", "")
        if state != "merged":
            return {
                "statusCode": 200,
                "body": json.dumps({
                    "message": f"MR state is '{state}' (action: {action}), skipping",
                    "action": "skipped",
                }),
            }

        # ── Extract ALL fields from payload ───────────────────────

        # Developer — from top-level user object (who triggered the event)
        developer = body.get("user", {}).get("username", "unknown")

        # Repository — full namespace path (e.g., "team/project-name")
        project = body.get("project", {})
        repository = project.get("path_with_namespace", "unknown")

        # MR metadata
        mr_id = mr.get("iid", 0)
        mr_title = mr.get("title", "")
        source_branch = mr.get("source_branch", "")
        target_branch = mr.get("target_branch", "")
        created_at = mr.get("created_at", "")
        merged_at = mr.get("merged_at", "")
        labels_raw = body.get("labels", [])
        labels = [lbl.get("title", str(lbl)) if isinstance(lbl, dict) else str(lbl) for lbl in labels_raw]

        # Cycle time
        cycle_time_hours = _cycle_time_hours(created_at, merged_at)

        # Code volume — GitLab sends these in object_attributes for merge events
        # Also check changes object for detailed diff stats
        changes = body.get("changes", {})
        lines_added = _safe_int(mr.get("additions", 0))
        lines_removed = _safe_int(mr.get("deletions", 0))
        files_changed = _safe_int(mr.get("changed_files", 0))

        # If MR attributes didn't have diff stats, try diff_stats
        diff_stats = mr.get("diff_stats", {})
        if lines_added == 0 and diff_stats:
            lines_added = _safe_int(diff_stats.get("additions", 0))
        if lines_removed == 0 and diff_stats:
            lines_removed = _safe_int(diff_stats.get("deletions", 0))
        if files_changed == 0 and diff_stats:
            files_changed = _safe_int(diff_stats.get("changed_files", 0))

        # Commits count
        commits_count = _safe_int(mr.get("commits_count", 0))

        # Review metadata — approvals
        # GitLab webhook sends approvals in various formats depending on version
        approvals_list = body.get("approvals", [])
        if isinstance(approvals_list, list):
            approvals = len(approvals_list)
        elif isinstance(approvals_list, dict):
            # Some GitLab versions send { "approved": true, "approvers": [...] }
            approvals = len(approvals_list.get("approvers", []))
        else:
            approvals = 0

        # Review comments — user_notes_count on the MR
        review_comments = _safe_int(mr.get("user_notes_count", 0))

        # Build / pipeline status
        build_status, build_passed = _extract_build_status(mr, body)

        # Commit SHAs — for session↔MR linkage
        # GitLab sends last_commit in MR attributes, and commit list varies by event
        commit_shas = []
        last_commit = mr.get("last_commit", {})
        if last_commit and last_commit.get("id"):
            commit_shas.append(last_commit["id"])
        merge_commit_sha = mr.get("merge_commit_sha", "")
        if merge_commit_sha:
            commit_shas.append(merge_commit_sha)

        # ── Publish CloudWatch Metrics (9 metrics) ────────────────
        _publish_metrics(
            developer=developer,
            repository=repository,
            cycle_time_hours=cycle_time_hours,
            build_passed=build_passed,
            lines_added=lines_added,
            lines_removed=lines_removed,
            files_changed=files_changed,
            approvals=approvals,
            review_comments=review_comments,
            commits_count=commits_count,
        )

        # ── Write Structured Log Event ────────────────────────────
        log_event = {
            "event_type": "mr_merged",
            "developer": developer,
            "repository": repository,
            "mr_id": mr_id,
            "mr_title": mr_title,
            "source_branch": source_branch,
            "target_branch": target_branch,
            "created_at": created_at,
            "merged_at": merged_at,
            "cycle_time_hours": cycle_time_hours,
            "lines_added": lines_added,
            "lines_removed": lines_removed,
            "files_changed": files_changed,
            "commits_count": commits_count,
            "approvals": approvals,
            "review_comments": review_comments,
            "build_status": build_status,
            "labels": labels,
            "build_passed": build_passed,
            "commit_shas": commit_shas,
        }

        _put_structured_log(log_event)

        # ── Return success ────────────────────────────────────────
        return {
            "statusCode": 200,
            "body": json.dumps({
                "message": "v2 — Metrics + Logs recorded",
                "metrics_published": 9,
                "log_group": LOG_GROUP,
                **log_event,
            }, default=str),
        }

    except Exception as exc:
        # Log the full error for debugging
        error_msg = f"[lambda_gitlab_webhook_v2] Error: {exc}"
        print(error_msg)

        # Attempt to log the error to CloudWatch Logs as well
        try:
            _put_structured_log({
                "event_type": "error",
                "error": str(exc),
                "raw_body_preview": str(event.get("body", ""))[:500],
            })
        except Exception:
            pass  # Don't mask the original error

        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(exc)}),
        }
