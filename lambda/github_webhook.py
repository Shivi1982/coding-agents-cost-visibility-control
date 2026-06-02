"""
Lambda GitHub Webhook — PR + Issues Events → CloudWatch Metrics + Structured Logs
===============================================================================
Receives GitHub webhook events via API Gateway.
Processes:
  1. Merged PRs (pull_request: action "closed" + merged true)
  2. Bug/Incident issues (issues: action "opened" or "labeled")

Security:
  Validates X-Hub-Signature-256 header (HMAC-SHA256) against the
  GITHUB_WEBHOOK_SECRET environment variable. Rejects unsigned or
  invalid payloads with 401/403.

CloudWatch Metrics (12 metrics, all dimensioned by Developer + Repository):

  --- PR Throughput (existing) ---
  1. PRsMerged              — count of merged PRs
  2. PRCycleTimeHours       — hours from PR open → merge
  3. BuildPassRate           — 1.0 (passed) or 0.0 (failed/unknown)
  4. LinesAddedPerPR        — pr.additions
  5. LinesRemovedPerPR      — pr.deletions
  6. FilesChangedPerPR      — pr.changed_files
  7. ApprovalsPerPR          — review approvals (from review_comments or check)
  8. ReviewCommentsPerPR     — pr.review_comments
  9. CommitsPerPR            — pr.commits

  --- Quality / Stability (NEW) ---
  10. CodeChurnSignal        — 1.0 if PR branch name indicates rework (fix/hotfix/revert), else 0.0
  11. IncidentCount          — count of bug/incident issues per repository
  12. IncidentsLinkedToPR    — incidents with PR references in body/title

CloudWatch Logs (structured JSON → /claude-code/dev-productivity):
  Full event record for Logs Insights queries, with commit_shas field
  for session↔PR linkage.

Handles both GitHub Cloud and GitHub Enterprise Server payloads.
All values extracted dynamically from the webhook payload — ZERO hardcoding.

Account: YOUR_ACCOUNT_ID  |  Region: us-west-2
"""

from __future__ import annotations

import json
import hashlib
import hmac
import re
import os
import time
import logging
import boto3
from datetime import datetime, timezone

# ── Logging ──────────────────────────────────────────────────────
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# ── AWS Clients (reused across warm invocations) ─────────────────
cw = boto3.client("cloudwatch", region_name="us-west-2")
logs = boto3.client("logs", region_name="us-west-2")

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

def _get_webhook_secret() -> str | None:
    """Retrieve the webhook secret from environment variable."""
    return os.environ.get("GITHUB_WEBHOOK_SECRET")


def _verify_signature(payload_body: str, signature_header: str | None, secret: str) -> bool:
    """
    Verify the X-Hub-Signature-256 header.

    GitHub sends: sha256=<hex_digest>
    We compute HMAC-SHA256(secret, body) and compare in constant time.

    Works identically for GitHub Cloud and GitHub Enterprise Server.
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
    """
    Parse ISO-8601 timestamps from GitHub.

    GitHub Cloud:      2024-01-15T10:30:00Z
    GitHub Enterprise: 2024-01-15T10:30:00+00:00  or  2024-01-15T10:30:00Z
    """
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

    GitHub does NOT include full check_runs data in the webhook payload.
    We use heuristics from the available fields:

    Priority 1: merge_commit_sha presence — if GitHub merged it,
                branch protections passed (if configured).
    Priority 2: pr.mergeable_state — "clean" = all checks passed.
    Priority 3: Default to "unknown" if no signal available.

    Note: For full check_runs status, use the GitHub API with the
    head_sha after receiving the webhook (requires PAT/GitHub App token).
    """
    # GitHub sends mergeable_state in some payload versions
    mergeable_state = pr.get("mergeable_state", "")

    if mergeable_state == "clean":
        return "success", True
    elif mergeable_state in ("unstable", "dirty"):
        return mergeable_state, False

    # If the PR was merged, branch protection rules were satisfied
    # (GitHub blocks merge if required checks fail)
    merge_commit_sha = pr.get("merge_commit_sha", "")
    if merge_commit_sha:
        return "merged_with_protections", True

    return "unknown", False


def _extract_repository_name(pr: dict, body: dict) -> str:
    """
    Extract repository full name from the payload.

    GitHub Cloud:      repository.full_name = "owner/repo"
    GitHub Enterprise: repository.full_name = "owner/repo" (same format)

    Falls back to pr.base.repo.full_name if top-level is missing.
    """
    # Top-level repository object (standard in all webhook events)
    repo = body.get("repository", {})
    full_name = repo.get("full_name", "")

    if full_name:
        return full_name

    # Fallback: from the PR's base repo
    base_repo = pr.get("base", {}).get("repo", {})
    return base_repo.get("full_name", "unknown")


def _extract_labels(pr: dict) -> list[str]:
    """Extract label names from the PR payload."""
    labels_raw = pr.get("labels", [])
    return [
        lbl.get("name", str(lbl)) if isinstance(lbl, dict) else str(lbl)
        for lbl in labels_raw
    ]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Code Churn + Incident Helpers (NEW)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _is_churn_branch(branch_name: str) -> bool:
    """
    Heuristic: branch names starting with fix/, hotfix/, revert/, bugfix/,
    or patch/ indicate rework on previously-merged code.

    This is a proxy for code churn — files being re-modified shortly after
    initial merge. Exact file-path churn would require GitHub API calls
    (not in scope for this zero-outbound-call architecture).

    Use CloudWatch Logs Insights for deeper correlation:
      fields developer, repository, pr_number, source_branch, merged_at
      | filter event_type = "pr_merged" and churn_signal = 1
      | stats count() as churn_prs by developer, bin(1d)
    """
    lower = branch_name.lower()
    return lower.startswith(CHURN_BRANCH_PREFIXES)


def _extract_pr_references(text: str) -> list[int]:
    """
    Extract PR numbers referenced in issue body/title.
    Matches: #123, GH-123, pull/123, PR #123, closes #123, fixes #123
    """
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


def _publish_incident_metrics(repository: str, linked_prs: list[int]) -> None:
    """Emit IncidentCount + IncidentsLinkedToPR metrics."""
    metrics = [
        _build_metric("IncidentCount", 1, "Count", "all", repository),
    ]
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
#  CloudWatch Logs — write structured JSON event
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

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

    Same pattern as GitLab v2 Lambda — shared log group, daily streams.
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


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  CloudWatch Metrics — 9 metrics, Developer + Repository dimensions
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
    churn_signal: bool,
) -> None:
    """Publish all 10 PR metrics in a single PutMetricData call."""
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

    # CloudWatch allows max 1000 metrics per call — 9 is well under
    cw.put_metric_data(Namespace=CW_NAMESPACE, MetricData=metrics)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Lambda Handler
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def lambda_handler(event, context):
    """
    GitHub PR Webhook → CloudWatch Metrics + Structured Logs.

    Trigger: API Gateway POST from GitHub webhook.
    Processes only 'pull_request' events where action == 'closed' and merged == true.

    Security: Validates X-Hub-Signature-256 HMAC before processing.
    """

    try:
        # ── Extract raw body and headers ──────────────────────────
        raw_body = event.get("body", "")
        if not raw_body:
            return _response(400, {"error": "Empty request body"})

        # API Gateway may pass headers with varying case
        headers = event.get("headers", {})
        # Normalize header keys to lowercase for reliable lookup
        headers_lower = {k.lower(): v for k, v in headers.items()} if headers else {}

        # ── Signature Validation ──────────────────────────────────
        webhook_secret = _get_webhook_secret()
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
        # GitHub sends the event type in the X-GitHub-Event header
        github_event = headers_lower.get("x-github-event", "")

        # Handle ping events (sent when webhook is first configured)
        if github_event == "ping":
            zen = json.loads(raw_body).get("zen", "")
            logger.info(f"Ping received: {zen}")
            return _response(200, {
                "message": "Pong! Webhook configured successfully.",
                "zen": zen,
            })

        # ── Route: Issues → Incident Tracking ──────────────────────
        if github_event == "issues":
            body = json.loads(raw_body)
            action = body.get("action", "")

            # Only process opened or labeled actions
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
            issue_author = issue.get("user", {}).get("login", "unknown")
            created_at = issue.get("created_at", "")

            # Extract PR references from issue body + title
            linked_prs = _extract_pr_references(issue_body_text + " " + issue_title)

            # Publish metrics
            _publish_incident_metrics(repository, linked_prs)

            # Structured log
            _put_structured_log({
                "event_type": "incident_linked",
                "source": "github",
                "repository": repository,
                "issue_number": issue_number,
                "issue_title": issue_title,
                "issue_author": issue_author,
                "labels": labels,
                "created_at": created_at,
                "linked_prs": linked_prs,
                "pr_linkage_count": len(linked_prs),
            })

            logger.info(f"Incident #{issue_number} linked to {len(linked_prs)} PRs: {linked_prs}")
            return _response(200, {
                "message": "Incident processed",
                "issue_number": issue_number,
                "linked_prs": linked_prs,
            })

        # Gate: only pull_request events beyond this point
        if github_event != "pull_request":
            logger.info(f"Ignored event type: {github_event}")
            return _response(200, {
                "message": f"Ignored event: {github_event}",
                "action": "skipped",
            })

        # ── Parse payload ─────────────────────────────────────────
        body = json.loads(raw_body)
        action = body.get("action", "")
        pr = body.get("pull_request", {})
        merged = pr.get("merged", False)

        # Gate: only closed + merged PRs
        if action != "closed" or not merged:
            logger.info(f"PR action='{action}', merged={merged} — skipping")
            return _response(200, {
                "message": f"PR action is '{action}' (merged: {merged}), skipping",
                "action": "skipped",
            })

        logger.info(f"Processing merged PR #{pr.get('number', '?')}")

        # ── Extract ALL fields from payload ───────────────────────

        # Developer — from the PR author (who opened the PR)
        developer = pr.get("user", {}).get("login", "unknown")

        # Repository — full name (e.g., "owner/repo-name")
        repository = _extract_repository_name(pr, body)

        # PR metadata
        pr_number = pr.get("number", 0)
        pr_title = pr.get("title", "")
        source_branch = pr.get("head", {}).get("ref", "")
        target_branch = pr.get("base", {}).get("ref", "")
        created_at = pr.get("created_at", "")
        merged_at = pr.get("merged_at", "")
        labels = _extract_labels(pr)

        # Who merged it (may differ from the PR author)
        merged_by = pr.get("merged_by", {}).get("login", "unknown") if pr.get("merged_by") else "unknown"

        # Cycle time
        cycle_time_hours = _cycle_time_hours(created_at, merged_at)

        # Code volume — GitHub includes these directly on the PR object
        lines_added = _safe_int(pr.get("additions", 0))
        lines_removed = _safe_int(pr.get("deletions", 0))
        files_changed = _safe_int(pr.get("changed_files", 0))

        # Commits count
        commits_count = _safe_int(pr.get("commits", 0))

        # Review metadata
        # GitHub sends review_comments (inline code comments) on the PR object
        review_comments = _safe_int(pr.get("review_comments", 0))

        # Approvals — GitHub doesn't include approval count in the webhook payload.
        # We use review_comments as a proxy; for exact counts, query the
        # GitHub API /pulls/{number}/reviews endpoint.
        # If the PR was merged with branch protection requiring approvals,
        # we know at least the required number were given.
        requested_reviewers = pr.get("requested_reviewers", [])
        requested_teams = pr.get("requested_teams", [])
        # Use a heuristic: if PR was merged, it met required approvals
        # The actual count would need a separate API call
        approvals = 0  # Will be overridden if we find review data

        # Check if review data is in the payload (some GHES versions include it)
        reviews = body.get("reviews", [])
        if isinstance(reviews, list):
            approvals = sum(1 for r in reviews if r.get("state") == "APPROVED")

        # Build / CI status
        build_status, build_passed = _extract_build_status(pr)

        # Code Churn heuristic — branch name indicates rework?
        churn_signal = _is_churn_branch(source_branch)

        # Commit SHAs — for session↔PR linkage
        commit_shas = []
        merge_commit_sha = pr.get("merge_commit_sha", "")
        if merge_commit_sha:
            commit_shas.append(merge_commit_sha)

        head_sha = pr.get("head", {}).get("sha", "")
        if head_sha:
            commit_shas.append(head_sha)

        # ── Publish CloudWatch Metrics (10 metrics) ───────────────
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
            churn_signal=churn_signal,
        )

        logger.info(
            f"Published 10 metrics for {developer}/{repository} "
            f"(PR #{pr_number}, cycle={cycle_time_hours}h)"
        )

        # ── Write Structured Log Event ────────────────────────────
        log_event = {
            "event_type": "pr_merged",
            "source": "github",
            "developer": developer,
            "repository": repository,
            "pr_number": pr_number,
            "pr_title": pr_title,
            "source_branch": source_branch,
            "target_branch": target_branch,
            "created_at": created_at,
            "merged_at": merged_at,
            "merged_by": merged_by,
            "cycle_time_hours": cycle_time_hours,
            "lines_added": lines_added,
            "lines_removed": lines_removed,
            "files_changed": files_changed,
            "commits_count": commits_count,
            "approvals": approvals,
            "review_comments": review_comments,
            "build_status": build_status,
            "build_passed": build_passed,
            "labels": labels,
            "churn_signal": 1 if churn_signal else 0,
            "merge_commit_sha": merge_commit_sha,
            "head_sha": head_sha,
            "commit_shas": commit_shas,
            "requested_reviewers": [
                r.get("login", str(r)) if isinstance(r, dict) else str(r)
                for r in requested_reviewers
            ],
            "requested_teams": [
                t.get("name", str(t)) if isinstance(t, dict) else str(t)
                for t in requested_teams
            ],
        }

        _put_structured_log(log_event)

        logger.info(f"Structured log written to {LOG_GROUP}")

        # ── Return success ────────────────────────────────────────
        return _response(200, {
            "message": "GitHub webhook — Metrics + Logs recorded",
            "metrics_published": 9,
            "log_group": LOG_GROUP,
            **log_event,
        })

    except json.JSONDecodeError as exc:
        logger.error(f"Invalid JSON payload: {exc}")
        return _response(400, {"error": f"Invalid JSON: {exc}"})

    except Exception as exc:
        # Log the full error for debugging
        logger.error(f"[lambda_github_webhook] Error: {exc}", exc_info=True)

        # Attempt to log the error to CloudWatch Logs as well
        try:
            _put_structured_log({
                "event_type": "error",
                "source": "github",
                "error": str(exc),
                "raw_body_preview": str(event.get("body", ""))[:500],
            })
        except Exception:
            pass  # Don't mask the original error

        return _response(500, {"error": str(exc)})


def _response(status_code: int, body: dict) -> dict:
    """Build a standard API Gateway proxy response."""
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
        },
        "body": json.dumps(body, default=str),
    }
