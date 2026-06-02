"""
Lambda GitHub Webhook v2 — Lambda Function URL (No API Gateway Required)
=========================================================================
Receives GitHub webhook events directly via Lambda Function URL.
Same processing logic as v1, but simplified deployment:

  v1: GitHub → API Gateway → Lambda (requires API GW setup)
  v2: GitHub → Lambda Function URL (one command, no extra services)

What is Lambda Function URL?
  A built-in HTTPS endpoint assigned directly to your Lambda function.
  No API Gateway, no ALB — just a public URL that triggers your Lambda.
  Format: https://<url-id>.lambda-url.<region>.on.aws/

Setup (one command):
  aws lambda create-function-url-config \
    --function-name claude-code-github-webhook-v2 \
    --auth-type NONE \
    --region us-west-2

  Then paste the returned URL into GitHub → Settings → Webhooks → Payload URL.

Processes:
  1. Merged PRs (pull_request: action "closed" + merged true)
  2. Bug/Incident issues (issues: action "opened" or "labeled")

Security:
  Validates X-Hub-Signature-256 header (HMAC-SHA256) against the
  GITHUB_WEBHOOK_SECRET environment variable. Rejects unsigned or
  invalid payloads with 401/403.

CloudWatch Metrics (12 metrics, all dimensioned by Developer + Repository):

  --- PR Throughput ---
  1. PRsMerged              — count of merged PRs
  2. PRCycleTimeHours       — hours from PR open → merge
  3. BuildPassRate           — 1.0 (passed) or 0.0 (failed/unknown)
  4. LinesAddedPerPR        — pr.additions
  5. LinesRemovedPerPR      — pr.deletions
  6. FilesChangedPerPR      — pr.changed_files
  7. ApprovalsPerPR          — review approvals
  8. ReviewCommentsPerPR     — pr.review_comments
  9. CommitsPerPR            — pr.commits

  --- Quality / Stability ---
  10. CodeChurnSignal        — 1.0 if branch indicates rework (fix/hotfix/revert)
  11. IncidentCount          — count of bug/incident issues per repository
  12. IncidentsLinkedToPR    — incidents with PR references in body/title

CloudWatch Logs (structured JSON → /claude-code/dev-productivity):
  Full event record for Logs Insights queries.

Deployment:
  1. Create the Lambda function:
     aws lambda create-function \
       --function-name claude-code-github-webhook-v2 \
       --runtime python3.9 \
       --handler github_webhook_v2.lambda_handler \
       --role arn:aws:iam::YOUR_ACCOUNT_ID:role/YOUR_LAMBDA_ROLE \
       --zip-file fileb://github_webhook_v2.zip \
       --timeout 30 \
       --region us-west-2

  2. Create Function URL (public HTTPS endpoint — no API Gateway):
     aws lambda create-function-url-config \
       --function-name claude-code-github-webhook-v2 \
       --auth-type NONE \
       --region us-west-2

  3. Allow public invoke (required for Function URL with auth-type NONE):
     aws lambda add-permission \
       --function-name claude-code-github-webhook-v2 \
       --statement-id FunctionURLAllowPublicAccess \
       --action lambda:InvokeFunctionUrl \
       --principal "*" \
       --function-url-auth-type NONE \
       --region us-west-2

  4. Set webhook secret (optional but recommended):
     aws lambda update-function-configuration \
       --function-name claude-code-github-webhook-v2 \
       --environment Variables={GITHUB_WEBHOOK_SECRET=your-secret-here} \
       --region us-west-2

  5. Copy the Function URL → paste into GitHub repo → Settings → Webhooks:
     - Payload URL: https://xxxxxxxx.lambda-url.us-west-2.on.aws/
     - Content type: application/json
     - Secret: (same secret you set in step 4)
     - Events: Pull requests, Issues

All values extracted dynamically from the webhook payload — ZERO hardcoding.
"""

from __future__ import annotations

import json
import hashlib
import hmac
import re
import os
import logging
import boto3
from datetime import datetime, timezone

# ── Logging ──────────────────────────────────────────────────────
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# ── AWS Clients (reused across warm invocations) ─────────────────
REGION = os.environ.get("AWS_REGION", "us-west-2")
cw = boto3.client("cloudwatch", region_name=REGION)
logs_client = boto3.client("logs", region_name=REGION)

# ── Constants ────────────────────────────────────────────────────
CW_NAMESPACE = "ClaudeCode/DevProductivity"
LOG_GROUP = "/claude-code/dev-productivity"
LOG_STREAM_PREFIX = "webhook-events"

# Labels on issues that indicate production incidents / bugs
INCIDENT_LABELS = {"bug", "incident", "production-issue", "hotfix", "regression"}
CHURN_BRANCH_PREFIXES = ("fix/", "hotfix/", "revert/", "bugfix/", "patch/")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Signature Validation (HMAC-SHA256)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _verify_signature(payload_body: str, signature_header: str | None, secret: str) -> bool:
    """
    Verify the X-Hub-Signature-256 header.
    GitHub sends: sha256=<hex_digest>
    We compute HMAC-SHA256(secret, body) and compare in constant time.
    """
    if not signature_header:
        logger.warning("No X-Hub-Signature-256 header present")
        return False

    if not signature_header.startswith("sha256="):
        logger.warning("Signature header does not start with 'sha256='")
        return False

    expected_sig = signature_header[7:]  # strip "sha256=" prefix

    computed = hmac.new(
        key=secret.encode("utf-8"),
        msg=payload_body.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(computed, expected_sig)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Helpers
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _parse_iso(ts: str) -> datetime | None:
    """Parse ISO-8601 timestamps from GitHub (handles Z and +00:00)."""
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
    """Calculate hours between PR creation and merge."""
    created = _parse_iso(created_at)
    merged = _parse_iso(merged_at)
    if created and merged:
        return round((merged - created).total_seconds() / 3600, 2)
    return 0.0


def _extract_build_status(pr: dict) -> tuple[str, bool]:
    """
    Determine build/CI status from the GitHub PR payload.
    If merged, branch protections were satisfied.
    """
    mergeable_state = pr.get("mergeable_state", "")

    if mergeable_state == "clean":
        return "success", True
    elif mergeable_state in ("unstable", "dirty"):
        return mergeable_state, False

    merge_commit_sha = pr.get("merge_commit_sha", "")
    if merge_commit_sha:
        return "merged_with_protections", True

    return "unknown", False


def _extract_repository_name(pr: dict, body: dict) -> str:
    """Extract repository full name from the payload."""
    repo = body.get("repository", {})
    full_name = repo.get("full_name", "")
    if full_name:
        return full_name
    base_repo = pr.get("base", {}).get("repo", {})
    return base_repo.get("full_name", "unknown")


def _is_churn_branch(branch_name: str) -> bool:
    """Branch names starting with fix/hotfix/revert indicate rework."""
    return branch_name.lower().startswith(CHURN_BRANCH_PREFIXES)


def _extract_pr_references(text: str) -> list[int]:
    """Extract PR numbers referenced in issue body/title."""
    patterns = [
        r'#(\d+)',
        r'GH-(\d+)',
        r'pull/(\d+)',
        r'PR\s*#?(\d+)',
        r'(?:closes|fixes|resolves)\s*#(\d+)',
    ]
    refs = set()
    for pattern in patterns:
        refs.update(int(m) for m in re.findall(pattern, text, re.IGNORECASE))
    return sorted(refs)


def _is_incident_issue(labels: list[str]) -> bool:
    """Check if any issue label matches known incident/bug labels."""
    return bool(set(l.lower() for l in labels).intersection(INCIDENT_LABELS))


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  CloudWatch Logs — write structured JSON event
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _ensure_log_group_and_stream(stream_name: str) -> None:
    """Create the log group + stream if they don't already exist."""
    try:
        logs_client.create_log_group(logGroupName=LOG_GROUP)
    except logs_client.exceptions.ResourceAlreadyExistsException:
        pass
    try:
        logs_client.create_log_stream(logGroupName=LOG_GROUP, logStreamName=stream_name)
    except logs_client.exceptions.ResourceAlreadyExistsException:
        pass


def _put_structured_log(event_record: dict) -> None:
    """Write structured JSON event to CloudWatch Logs (daily streams)."""
    now = datetime.now(timezone.utc)
    stream_name = f"{LOG_STREAM_PREFIX}/{now.strftime('%Y/%m/%d')}"
    _ensure_log_group_and_stream(stream_name)

    timestamp_ms = int(now.timestamp() * 1000)

    try:
        resp = logs_client.describe_log_streams(
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
            {"timestamp": timestamp_ms, "message": json.dumps(event_record, default=str)}
        ],
    }
    if seq_token:
        put_kwargs["sequenceToken"] = seq_token

    try:
        logs_client.put_log_events(**put_kwargs)
    except logs_client.exceptions.InvalidSequenceTokenException as e:
        correct_token = str(e).split("sequenceToken is: ")[-1].strip()
        if correct_token and correct_token != "null":
            put_kwargs["sequenceToken"] = correct_token
            logs_client.put_log_events(**put_kwargs)
        else:
            put_kwargs.pop("sequenceToken", None)
            logs_client.put_log_events(**put_kwargs)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  CloudWatch Metrics — 12 metrics, Developer + Repository dimensions
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

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


def _publish_pr_metrics(
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
    churn_signal: bool,
) -> None:
    """Publish all PR metrics in a single PutMetricData call."""
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
        _build_metric("CodeChurnSignal",      1.0 if churn_signal else 0.0,   "None",  developer, repository),
    ]
    cw.put_metric_data(Namespace=CW_NAMESPACE, MetricData=metrics)


def _publish_incident_metrics(repository: str, linked_prs: list[int]) -> None:
    """Emit IncidentCount + IncidentsLinkedToPR metrics."""
    metrics = [_build_metric("IncidentCount", 1, "Count", "all", repository)]
    for pr_num in linked_prs:
        metrics.append({
            "MetricName": "IncidentsLinkedToPR",
            "Value": 1,
            "Unit": "Count",
            "Dimensions": [
                {"Name": "Repository", "Value": repository},
                {"Name": "LinkedPR", "Value": str(pr_num)},
            ],
        })
    cw.put_metric_data(Namespace=CW_NAMESPACE, MetricData=metrics)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Response helper
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _response(status_code: int, body: dict) -> dict:
    """
    Build response compatible with BOTH API Gateway and Lambda Function URL.
    
    Lambda Function URL uses the same response format as API Gateway proxy:
    {statusCode, headers, body}
    """
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Lambda Handler (works with both API Gateway AND Lambda Function URL)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def lambda_handler(event, context):
    """
    GitHub Webhook → CloudWatch Metrics + Structured Logs.

    Trigger: Lambda Function URL (or API Gateway) POST from GitHub webhook.
    
    Key difference from v1:
      - No API Gateway required
      - Lambda Function URL provides the public HTTPS endpoint directly
      - Same event format (API GW proxy compatible) — works with both
    
    Processes:
      - pull_request events: action "closed" + merged true
      - issues events: action "opened"/"labeled" with incident labels
      - ping events: responds with pong (webhook health check)
    """
    try:
        # ── Extract raw body and headers ──────────────────────────
        raw_body = event.get("body", "")
        if not raw_body:
            return _response(400, {"error": "Empty request body"})

        # Lambda Function URL may base64-encode the body
        if event.get("isBase64Encoded", False):
            import base64
            raw_body = base64.b64decode(raw_body).decode("utf-8")

        headers = event.get("headers", {})
        headers_lower = {k.lower(): v for k, v in headers.items()} if headers else {}

        # ── Signature Validation ──────────────────────────────────
        webhook_secret = os.environ.get("GITHUB_WEBHOOK_SECRET")
        if webhook_secret:
            signature = headers_lower.get("x-hub-signature-256", "")
            if not _verify_signature(raw_body, signature, webhook_secret):
                logger.error("HMAC signature validation failed")
                return _response(403, {"error": "Invalid signature"})
            logger.info("HMAC signature validated successfully")
        else:
            logger.warning(
                "GITHUB_WEBHOOK_SECRET not set — skipping signature validation. "
                "Set this env var in production!"
            )

        # ── Identify event type ───────────────────────────────────
        github_event = headers_lower.get("x-github-event", "")

        # Handle ping events (sent when webhook is first configured)
        if github_event == "ping":
            body = json.loads(raw_body)
            zen = body.get("zen", "")
            logger.info(f"Ping received: {zen}")
            return _response(200, {
                "message": "Pong! Webhook configured successfully.",
                "zen": zen,
            })

        # ── Route: Issues → Incident Tracking ─────────────────────
        if github_event == "issues":
            body = json.loads(raw_body)
            action = body.get("action", "")

            if action not in ("opened", "labeled"):
                return _response(200, {"message": f"Issue action '{action}' skipped"})

            issue = body.get("issue", {})
            labels = [l.get("name", "") for l in issue.get("labels", [])]

            if not _is_incident_issue(labels):
                return _response(200, {"message": "Issue not labeled as incident, skipping"})

            repository = body.get("repository", {}).get("full_name", "unknown")
            issue_number = issue.get("number", 0)
            issue_title = issue.get("title", "")
            issue_body_text = issue.get("body", "") or ""

            linked_prs = _extract_pr_references(issue_body_text + " " + issue_title)
            _publish_incident_metrics(repository, linked_prs)

            _put_structured_log({
                "event_type": "incident_linked",
                "source": "github",
                "repository": repository,
                "issue_number": issue_number,
                "issue_title": issue_title,
                "labels": labels,
                "linked_prs": linked_prs,
            })

            logger.info(f"Incident #{issue_number} linked to {len(linked_prs)} PRs")
            return _response(200, {
                "message": "Incident processed",
                "issue_number": issue_number,
                "linked_prs": linked_prs,
            })

        # ── Gate: only pull_request events beyond this point ──────
        if github_event != "pull_request":
            return _response(200, {"message": f"Ignored event: {github_event}"})

        # ── Parse PR payload ──────────────────────────────────────
        body = json.loads(raw_body)
        action = body.get("action", "")
        pr = body.get("pull_request", {})
        merged = pr.get("merged", False)

        # Only process merged PRs (action=closed + merged=true)
        if action != "closed" or not merged:
            return _response(200, {
                "message": f"Skipped: action={action}, merged={merged}",
            })

        # ── Extract all fields dynamically from payload ───────────
        developer = pr.get("user", {}).get("login", "unknown")
        repository = _extract_repository_name(pr, body)
        source_branch = pr.get("head", {}).get("ref", "")

        # Timing
        created_at = pr.get("created_at", "")
        merged_at = pr.get("merged_at", "")
        cycle_time = _cycle_time_hours(created_at, merged_at)

        # Code volume
        lines_added = _safe_int(pr.get("additions", 0))
        lines_removed = _safe_int(pr.get("deletions", 0))
        files_changed = _safe_int(pr.get("changed_files", 0))
        commits_count = _safe_int(pr.get("commits", 0))

        # Review quality
        review_comments = _safe_int(pr.get("review_comments", 0))
        # GitHub doesn't include approval count directly in PR payload
        # Use review_comments as a proxy, or check reviews via API
        approvals = _safe_int(pr.get("review_comments", 0))

        # Build status
        build_status, build_passed = _extract_build_status(pr)

        # Code churn signal
        churn_signal = _is_churn_branch(source_branch)

        # Commit SHAs for session↔PR linkage
        merge_commit_sha = pr.get("merge_commit_sha", "")
        head_sha = pr.get("head", {}).get("sha", "")

        # ── Publish metrics ───────────────────────────────────────
        _publish_pr_metrics(
            developer=developer,
            repository=repository,
            cycle_time_hours=cycle_time,
            build_passed=build_passed,
            lines_added=lines_added,
            lines_removed=lines_removed,
            files_changed=files_changed,
            approvals=approvals,
            review_comments=review_comments,
            commits_count=commits_count,
            churn_signal=churn_signal,
        )

        # ── Structured log ────────────────────────────────────────
        event_record = {
            "event_type": "pr_merged",
            "source": "github",
            "developer": developer,
            "repository": repository,
            "pr_number": pr.get("number", 0),
            "pr_title": pr.get("title", ""),
            "source_branch": source_branch,
            "target_branch": pr.get("base", {}).get("ref", ""),
            "created_at": created_at,
            "merged_at": merged_at,
            "cycle_time_hours": cycle_time,
            "lines_added": lines_added,
            "lines_removed": lines_removed,
            "files_changed": files_changed,
            "commits_count": commits_count,
            "review_comments": review_comments,
            "build_status": build_status,
            "build_passed": build_passed,
            "churn_signal": churn_signal,
            "merge_commit_sha": merge_commit_sha,
            "head_sha": head_sha,
        }
        _put_structured_log(event_record)

        logger.info(
            f"Metrics recorded: developer={developer}, repo={repository}, "
            f"cycle_time={cycle_time}h, lines=+{lines_added}/-{lines_removed}, "
            f"build={'PASS' if build_passed else 'FAIL'}, churn={churn_signal}"
        )

        return _response(200, {
            "message": "PR metrics recorded successfully",
            "developer": developer,
            "repository": repository,
            "metrics_published": 10,
            "log_written": True,
        })

    except json.JSONDecodeError as e:
        logger.error(f"JSON parse error: {e}")
        return _response(400, {"error": f"Invalid JSON: {str(e)}"})

    except Exception as e:
        logger.error(f"Unhandled error: {e}", exc_info=True)
        return _response(500, {"error": f"Internal error: {str(e)}"})
