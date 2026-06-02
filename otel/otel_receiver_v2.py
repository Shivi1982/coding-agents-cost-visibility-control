#!/usr/bin/env python3
"""
Claude Code OTEL Receiver v2 — Enterprise ROI Demo
===================================================
Per-session aggregation • Developer name resolution • JSONL persistence
Tool usage tracking • Git context integration • Multi-developer support

v1 just printed to terminal — data disappeared on close.
v2 accumulates per-session, writes session summaries to otel_sessions.json,
and resolves hashed user IDs to developer names.

Usage:
    python3 otel_receiver_v2.py                    # default port 4318
    python3 otel_receiver_v2.py --port 4320        # custom port
    python3 otel_receiver_v2.py --devmap devs.json # custom developer map

Then in Terminal 2:
    export CLAUDE_CODE_ENABLE_TELEMETRY=1
    export OTEL_METRICS_EXPORTER=otlp
    export OTEL_LOGS_EXPORTER=otlp
    export OTEL_EXPORTER_OTLP_PROTOCOL=http/json
    export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
    claude
"""

import subprocess, sys, os, argparse

# ── Auto-install Flask (same pattern as v1) ──────────────────────────
try:
    import flask
except ImportError:
    print("📦 Installing Flask (one-time)...")
    subprocess.run([sys.executable, "-m", "pip", "install", "flask", "-q"], check=True)
    import flask

try:
    import boto3
except ImportError:
    print("📦 Installing boto3 (one-time)...")
    subprocess.run([sys.executable, "-m", "pip", "install", "boto3", "-q"], check=True)
    import boto3

from botocore.exceptions import ClientError

from flask import Flask, request
import json, datetime, threading, time, copy
from collections import defaultdict

# ── Flask app setup ──────────────────────────────────────────────────
app = Flask(__name__)
app.logger.disabled = True
import logging
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

# ── Config ───────────────────────────────────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SESSIONS_LOG = os.path.join(SCRIPT_DIR, "otel_sessions.json")
DEFAULT_DEV_MAP = os.path.join(SCRIPT_DIR, "developers.json")

# ── CloudWatch push config ────────────────────────────────────────
# Set PUSH_TO_CLOUDWATCH=1 to enable (off by default for local-only demo)
PUSH_TO_CLOUDWATCH = os.environ.get("PUSH_TO_CLOUDWATCH", "0") == "1"
CW_REGION = os.environ.get("CW_REGION", "us-west-2")
CW_NS_PRODUCTIVITY = "ClaudeCode/DevProductivity"
CW_NS_TOOL_USAGE = "ClaudeCode/ToolUsage"
CW_LOG_GROUP = "/claude-code/otel-sessions"
CW_LOG_STREAM_PREFIX = "session-summaries"
_cw_client = None
_cw_logs_client = None

# ── Developer name resolution ────────────────────────────────────
# developers.json format: {"hashed_user_id": "Alice Chen", ...}
# If the file doesn't exist, we show first 8 chars of the hash.
_dev_map = {}

def load_developer_map(path=None):
    """Load hash→name mapping. Returns empty dict if file missing (optional)."""
    global _dev_map
    p = path or DEFAULT_DEV_MAP
    if os.path.exists(p):
        try:
            with open(p, 'r') as f:
                _dev_map = json.load(f)
            print(f"  ✅ Loaded {len(_dev_map)} developer mappings from {os.path.basename(p)}")
        except Exception as e:
            print(f"  ⚠️  Could not parse {p}: {e}")
    else:
        print(f"  ℹ️  No developers.json found — will show hashed IDs")
        print(f"     Create {p} to map hashes to names")

def resolve_developer(user_id):
    """Map a hashed user.id to a readable name, or show first 8 chars."""
    if not user_id:
        return "unknown"
    # Try exact match first
    if user_id in _dev_map:
        return _dev_map[user_id]
    # Try prefix match (in case map has full or partial hashes)
    for hash_key, name in _dev_map.items():
        if user_id.startswith(hash_key) or hash_key.startswith(user_id):
            return name
    # Fallback: first 8 chars of hash
    return user_id[:8] if len(user_id) > 8 else user_id


# ══════════════════════════════════════════════════════════════════════
#  CLOUDWATCH PUSH — session-level metrics + structured logs
# ══════════════════════════════════════════════════════════════════════

def _get_cw_client():
    """Lazy-init CloudWatch client (reused across sessions)."""
    global _cw_client
    if _cw_client is None:
        _cw_client = boto3.client("cloudwatch", region_name=CW_REGION)
    return _cw_client

def _get_cw_logs_client():
    """Lazy-init CloudWatch Logs client."""
    global _cw_logs_client
    if _cw_logs_client is None:
        _cw_logs_client = boto3.client("logs", region_name=CW_REGION)
    return _cw_logs_client

def _push_session_metrics(summary: dict) -> None:
    """
    Push session-end metrics to CloudWatch.
    Namespace: ClaudeCode/DevProductivity + ClaudeCode/ToolUsage
    Dimensions: Developer (auto-discovered by dashboard SEARCH expressions)
    """
    if not PUSH_TO_CLOUDWATCH:
        return
    try:
        cw = _get_cw_client()
        dev = summary.get("developer", "unknown")
        ts = datetime.datetime.now(datetime.timezone.utc)

        # ── DevProductivity namespace ──────────────────────────────
        productivity_metrics = [
            {"MetricName": "SessionCost", "Value": summary.get("total_cost_usd", 0),
             "Unit": "None", "Timestamp": ts,
             "Dimensions": [{"Name": "Developer", "Value": dev}]},
            {"MetricName": "TokensInput", "Value": summary.get("tokens", {}).get("input", 0),
             "Unit": "Count", "Timestamp": ts,
             "Dimensions": [{"Name": "Developer", "Value": dev}]},
            {"MetricName": "TokensOutput", "Value": summary.get("tokens", {}).get("output", 0),
             "Unit": "Count", "Timestamp": ts,
             "Dimensions": [{"Name": "Developer", "Value": dev}]},
            {"MetricName": "LinesAdded", "Value": summary.get("lines_added", 0),
             "Unit": "Count", "Timestamp": ts,
             "Dimensions": [{"Name": "Developer", "Value": dev}]},
            {"MetricName": "LinesRemoved", "Value": summary.get("lines_removed", 0),
             "Unit": "Count", "Timestamp": ts,
             "Dimensions": [{"Name": "Developer", "Value": dev}]},
            {"MetricName": "SessionDurationSec", "Value": summary.get("duration_sec", 0),
             "Unit": "Seconds", "Timestamp": ts,
             "Dimensions": [{"Name": "Developer", "Value": dev}]},
            {"MetricName": "Turns", "Value": summary.get("turns", 0),
             "Unit": "Count", "Timestamp": ts,
             "Dimensions": [{"Name": "Developer", "Value": dev}]},
            {"MetricName": "CommitsInSession", "Value": summary.get("git", {}).get("commits_in_session", 0),
             "Unit": "Count", "Timestamp": ts,
             "Dimensions": [{"Name": "Developer", "Value": dev}]},
        ]

        # Code acceptance rate as a metric (0-100)
        roi = summary.get("roi", {})
        acceptance = roi.get("code_acceptance_rate")
        if acceptance is not None:
            productivity_metrics.append({
                "MetricName": "CodeAcceptanceRate", "Value": acceptance,
                "Unit": "Percent", "Timestamp": ts,
                "Dimensions": [{"Name": "Developer", "Value": dev}]})

        cw.put_metric_data(Namespace=CW_NS_PRODUCTIVITY, MetricData=productivity_metrics)
        print(f"  ☁️  Pushed {len(productivity_metrics)} metrics → {CW_NS_PRODUCTIVITY}")

        # ── ToolUsage namespace ────────────────────────────────────
        tool_metrics = []
        for tool_name, info in summary.get("tools", {}).items():
            tool_metrics.append({
                "MetricName": "ToolInvocations",
                "Value": info.get("count", 0),
                "Unit": "Count", "Timestamp": ts,
                "Dimensions": [
                    {"Name": "Developer", "Value": dev},
                    {"Name": "ToolName", "Value": tool_name},
                ]})

        if tool_metrics:
            cw.put_metric_data(Namespace=CW_NS_TOOL_USAGE, MetricData=tool_metrics)
            print(f"  ☁️  Pushed {len(tool_metrics)} tool metrics → {CW_NS_TOOL_USAGE}")

    except Exception as e:
        print(f"  ⚠️  CloudWatch metrics push failed: {e}")


def _push_session_log(summary: dict) -> None:
    """Push full session summary as structured JSON to CloudWatch Logs."""
    if not PUSH_TO_CLOUDWATCH:
        return
    try:
        logs_client = _get_cw_logs_client()
        now = datetime.datetime.now(datetime.timezone.utc)
        stream_name = f"{CW_LOG_STREAM_PREFIX}/{now.strftime('%Y/%m/%d')}"

        # Ensure log group + stream exist
        try:
            logs_client.create_log_group(logGroupName=CW_LOG_GROUP)
        except logs_client.exceptions.ResourceAlreadyExistsException:
            pass
        try:
            logs_client.create_log_stream(logGroupName=CW_LOG_GROUP, logStreamName=stream_name)
        except logs_client.exceptions.ResourceAlreadyExistsException:
            pass

        logs_client.put_log_events(
            logGroupName=CW_LOG_GROUP,
            logStreamName=stream_name,
            logEvents=[{
                "timestamp": int(now.timestamp() * 1000),
                "message": json.dumps(summary, default=str),
            }])
        print(f"  ☁️  Session log → {CW_LOG_GROUP}/{stream_name}")
    except Exception as e:
        print(f"  ⚠️  CloudWatch Logs push failed: {e}")


# ══════════════════════════════════════════════════════════════════════
#  SESSION STORE — per-session accumulation
# ══════════════════════════════════════════════════════════════════════
_lock = threading.Lock()

def _new_session():
    """Template for a fresh session accumulator."""
    return {
        "session_id": None,
        "developer_hash": None,
        "developer": "unknown",
        "started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "ended_at": None,

        # Cost & tokens (from metrics: claude_code.cost.usage, claude_code.tokens.*)
        "total_cost_usd": 0.0,
        "tokens": {
            "input": 0,
            "output": 0,
            "cache_read": 0,
            "cache_creation": 0,
        },

        # Code impact (from metrics: claude_code.lines_added/removed)
        "lines_added": 0,
        "lines_removed": 0,

        # Activity (from metrics: claude_code.active_time.total)
        "active_time_sec": 0.0,

        # Session turns (from metrics: claude_code.turns)
        "turns": 0,

        # API calls — model breakdown
        # { "claude-sonnet-4-20250514": 5, "claude-haiku-4-5-20251001": 2 }
        "models": defaultdict(int),

        # Tool usage — from tool_use log events
        # { "code_edit": {"count": 5, "total_ms": 1200, "pass": 4, "fail": 1},
        #   "bash": {"count": 3, ...} }
        "tools": defaultdict(lambda: {"count": 0, "total_ms": 0, "pass": 0, "fail": 0}),

        # Code edit events — accept/reject (from code_edit log events)
        "code_edits": {"accepted": 0, "rejected": 0},

        # Git context (merged from stop hook's claude_code.session_end event)
        "git": {
            "repo": None,
            "branch": None,
            "commits_in_session": 0,
            "commit_shas": [],
        },

        # Raw counters for debugging
        "_metric_points": 0,
        "_log_events": 0,
    }

# Active sessions: session_id → session dict
_sessions = {}

def _get_session(session_id):
    """Get or create a session accumulator. Thread-safe."""
    if not session_id:
        return None
    with _lock:
        if session_id not in _sessions:
            s = _new_session()
            s["session_id"] = session_id
            _sessions[session_id] = s
        return _sessions[session_id]

def _extract_session_id_from_resource(resource):
    """Extract session.id from resource attributes (OTEL resource-level)."""
    for attr in resource.get("attributes", []):
        if attr.get("key") == "session.id":
            return _val(attr.get("value", {}))
    return None

def _extract_user_id_from_resource(resource):
    """Extract user.id from resource attributes."""
    for attr in resource.get("attributes", []):
        if attr.get("key") == "user.id":
            return _val(attr.get("value", {}))
    return None


# ══════════════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════════════

def ts():
    return datetime.datetime.now().strftime('%H:%M:%S')

def _val(v):
    """Extract scalar value from an OTEL attribute value object."""
    return (v.get('stringValue') or v.get('intValue') or
            v.get('doubleValue') or v.get('boolValue') or '')

def parse_attrs(attrs):
    """Parse OTEL attributes array → flat dict."""
    out = {}
    for a in attrs:
        v = a.get('value', {})
        out[a['key']] = _val(v)
    return out

def try_parse(req):
    """Try JSON first, then explain binary as fallback."""
    data = req.get_json(force=True, silent=True)
    if data:
        return data, None
    raw = req.data
    ct = req.content_type
    return None, f"binary/protobuf ({len(raw)} bytes, content-type: {ct}) — add OTEL_EXPORTER_OTLP_PROTOCOL=http/json"

def _safe_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0

def _safe_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


# ══════════════════════════════════════════════════════════════════════
#  METRICS ENDPOINT — /v1/metrics
# ══════════════════════════════════════════════════════════════════════

@app.route('/v1/metrics', methods=['POST'])
def metrics():
    data, raw_msg = try_parse(request)
    if not data:
        print(f"\n[{ts()}] 📊 METRICS received ({raw_msg})")
        return '', 200

    found = []
    for rm in data.get('resourceMetrics', []):
        resource = rm.get('resource', {})
        session_id = _extract_session_id_from_resource(resource)
        user_id = _extract_user_id_from_resource(resource)

        for sm in rm.get('scopeMetrics', []):
            for m in sm.get('metrics', []):
                name = m.get('name', '?')

                # Collect data points from sum or gauge
                data_points = (
                    m.get('sum', {}).get('dataPoints', []) +
                    m.get('gauge', {}).get('dataPoints', [])
                )

                for dp in data_points:
                    val = dp.get('asDouble', dp.get('asInt', 0))
                    attrs = parse_attrs(dp.get('attributes', []))

                    # Session ID can be in datapoint attributes too
                    sid = attrs.get('session.id') or session_id
                    uid = attrs.get('user.id') or user_id

                    found.append((name, val, attrs, sid, uid))

                    # ── Accumulate into session ──────────────────
                    sess = _get_session(sid)
                    if not sess:
                        continue

                    sess["_metric_points"] += 1

                    # Resolve developer on first sight
                    if uid and sess["developer"] == "unknown":
                        sess["developer_hash"] = uid
                        sess["developer"] = resolve_developer(uid)

                    # Cost
                    if 'cost' in name and val:
                        sess["total_cost_usd"] += _safe_float(val)

                    # Tokens
                    elif 'tokens' in name:
                        if 'input' in name or 'input' in str(attrs):
                            sess["tokens"]["input"] += _safe_int(val)
                        elif 'output' in name or 'output' in str(attrs):
                            sess["tokens"]["output"] += _safe_int(val)
                        elif 'cache_read' in name or 'cache_read' in str(attrs):
                            sess["tokens"]["cache_read"] += _safe_int(val)
                        elif 'cache_creation' in name or 'cache_creation' in str(attrs):
                            sess["tokens"]["cache_creation"] += _safe_int(val)

                    # Lines added/removed
                    elif 'lines_added' in name:
                        sess["lines_added"] += _safe_int(val)
                    elif 'lines_removed' in name:
                        sess["lines_removed"] += _safe_int(val)

                    # Active time
                    elif 'active_time' in name:
                        sess["active_time_sec"] += _safe_float(val)

                    # Turns
                    elif 'turns' in name:
                        sess["turns"] = max(sess["turns"], _safe_int(val))

                    # Model breakdown (from api_request metric attributes)
                    model = attrs.get('model') or attrs.get('model_id', '')
                    if model and ('cost' in name or 'tokens' in name):
                        sess["models"][model] += 1

    # ── Terminal pretty-print (like v1) ──────────────────────────────
    if found:
        print(f"\n{'━'*70}")
        print(f"[{ts()}] 📊  METRICS  ({len(found)} data points)")
        for name, val, attrs, sid, uid in found:
            dev = resolve_developer(uid) if uid else ''
            sid_short = sid[:8] if sid else ''
            prefix = f"[{dev}|{sid_short}]" if dev or sid_short else ''
            attr_str = '  '.join(f'{k}={v}' for k, v in attrs.items()
                                 if v and k not in ('session.id', 'user.id'))
            print(f"  {prefix:20s} {name:40s} = {val}")
            if attr_str:
                print(f"  {'':20s} {'':40s}   ↳ {attr_str}")
    return '', 200


# ══════════════════════════════════════════════════════════════════════
#  LOGS ENDPOINT — /v1/logs
# ══════════════════════════════════════════════════════════════════════

@app.route('/v1/logs', methods=['POST'])
def logs():
    data, raw_msg = try_parse(request)
    if not data:
        print(f"\n[{ts()}] 📝 LOGS received ({raw_msg})")
        return '', 200

    records = []
    for rl in data.get('resourceLogs', []):
        resource = rl.get('resource', {})
        session_id = _extract_session_id_from_resource(resource)
        user_id = _extract_user_id_from_resource(resource)

        for sl in rl.get('scopeLogs', []):
            for r in sl.get('logRecords', []):
                body = r.get('body', {}).get('stringValue', '')
                attrs = parse_attrs(r.get('attributes', []))

                sid = attrs.get('session.id') or session_id
                uid = attrs.get('user.id') or user_id

                records.append((body, attrs, sid, uid))

                # ── Accumulate into session ──────────────────────
                sess = _get_session(sid)
                if sess:
                    sess["_log_events"] += 1

                    # Resolve developer
                    if uid and sess["developer"] == "unknown":
                        sess["developer_hash"] = uid
                        sess["developer"] = resolve_developer(uid)

                    # ── api_request events → model + cost ────────
                    if body == 'api_request':
                        model = attrs.get('model') or attrs.get('model_id', '')
                        if model:
                            sess["models"][model] += 1
                        cost = _safe_float(attrs.get('cost_usd', 0))
                        if cost:
                            sess["total_cost_usd"] += cost
                        # Token counts from log events
                        sess["tokens"]["input"] += _safe_int(attrs.get('input_tokens', 0))
                        sess["tokens"]["output"] += _safe_int(attrs.get('output_tokens', 0))
                        sess["tokens"]["cache_read"] += _safe_int(attrs.get('cache_read_tokens',
                                                        attrs.get('cache_read_input_tokens', 0)))
                        sess["tokens"]["cache_creation"] += _safe_int(attrs.get('cache_creation_tokens',
                                                            attrs.get('cache_creation_input_tokens', 0)))

                    # ── tool_use events → tool tracking ──────────
                    elif body == 'tool_use':
                        tool_name = attrs.get('tool_name', attrs.get('name', 'unknown'))
                        duration = _safe_int(attrs.get('duration_ms', 0))
                        success = str(attrs.get('success', attrs.get('status', 'true'))).lower()
                        is_pass = success in ('true', '1', 'pass', 'success', 'ok')

                        t = sess["tools"][tool_name]
                        t["count"] += 1
                        t["total_ms"] += duration
                        if is_pass:
                            t["pass"] += 1
                        else:
                            t["fail"] += 1

                    # ── code_edit events → acceptance tracking ───
                    elif body == 'code_edit':
                        status = str(attrs.get('status', attrs.get('accepted', ''))).lower()
                        if status in ('accepted', 'true', '1'):
                            sess["code_edits"]["accepted"] += 1
                        elif status in ('rejected', 'false', '0'):
                            sess["code_edits"]["rejected"] += 1
                        else:
                            # If no explicit status, count as accepted
                            sess["code_edits"]["accepted"] += 1

                    # ── session_end event (from stop hook) ───────
                    elif body == 'claude_code.session_end':
                        _handle_session_end(sid, attrs)

    # ── Terminal pretty-print (like v1) ──────────────────────────────
    if records:
        print(f"\n{'━'*70}")
        print(f"[{ts()}] 📝  LOG EVENTS  ({len(records)} records)")
        for body, attrs, sid, uid in records:
            dev = resolve_developer(uid) if uid else ''
            sid_short = sid[:8] if sid else ''
            prefix = f"[{dev}|{sid_short}]" if dev or sid_short else ''

            # Color-code event types
            icon = {'api_request': '🔗', 'tool_use': '🔧', 'code_edit': '✏️',
                    'user_prompt': '💬', 'claude_code.session_end': '🏁'}.get(body, '📄')

            print(f"  {prefix:20s} {icon} {body}")
            for k, v in attrs.items():
                if v and k not in ('session.id', 'user.id'):
                    print(f"  {'':20s}   {k}: {str(v)[:100]}")
    return '', 200


# ══════════════════════════════════════════════════════════════════════
#  TRACES ENDPOINT — /v1/traces
# ══════════════════════════════════════════════════════════════════════

@app.route('/v1/traces', methods=['POST'])
def traces():
    data, raw_msg = try_parse(request)
    if not data:
        print(f"\n[{ts()}] 🔍 TRACES received ({raw_msg})")
        return '', 200
    spans = []
    for rt in data.get('resourceSpans', []):
        for st in rt.get('scopeSpans', []):
            for s in st.get('spans', []):
                spans.append(s.get('name', '?'))
    if spans:
        print(f"\n{'━'*70}")
        print(f"[{ts()}] 🔍  TRACES  spans: {', '.join(spans[:10])}")
    return '', 200


# ══════════════════════════════════════════════════════════════════════
#  SESSION END — aggregate, summarize, persist
# ══════════════════════════════════════════════════════════════════════

def _handle_session_end(session_id, attrs):
    """
    Called when we receive a claude_code.session_end log event
    (fired by the stop hook). Merges git context, emits summary,
    writes to JSONL.
    """
    sess = _get_session(session_id)
    if not sess:
        return

    # Merge git context from stop hook attributes
    sess["git"]["repo"] = attrs.get('git.repo', sess["git"]["repo"])
    sess["git"]["branch"] = attrs.get('git.branch', sess["git"]["branch"])
    sess["git"]["commits_in_session"] = _safe_int(
        attrs.get('commits.in_session', sess["git"]["commits_in_session"]))

    # Store commit SHAs for session↔MR linkage
    shas_str = attrs.get('git.commit_shas', '')
    if shas_str:
        sha_list = [s.strip() for s in shas_str.split(',') if s.strip()]
        sess["git"]["commit_shas"] = sha_list

    # Mark end time
    sess["ended_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()

    # Calculate session duration
    try:
        start = datetime.datetime.fromisoformat(sess["started_at"])
        end = datetime.datetime.fromisoformat(sess["ended_at"])
        sess["duration_sec"] = round((end - start).total_seconds(), 1)
        sess["duration_human"] = _format_duration(sess["duration_sec"])
    except Exception:
        sess["duration_sec"] = 0
        sess["duration_human"] = "unknown"

    # Calculate ROI metrics
    commits = sess["git"]["commits_in_session"]
    cost = sess["total_cost_usd"]
    sess["roi"] = {
        "cost_per_commit": round(cost / commits, 4) if commits > 0 else None,
        "lines_per_dollar": round((sess["lines_added"] + sess["lines_removed"]) / cost, 1) if cost > 0 else None,
        "code_acceptance_rate": _acceptance_rate(sess["code_edits"]),
    }

    # Build clean summary (convert defaultdicts → regular dicts for JSON)
    summary = _build_summary(sess)

    # Pretty-print to terminal
    _print_session_summary(summary)

    # Append to JSONL file
    _write_session_jsonl(summary)

    # Push to CloudWatch (if enabled via PUSH_TO_CLOUDWATCH=1)
    _push_session_metrics(summary)
    _push_session_log(summary)

    # Remove from active sessions
    with _lock:
        _sessions.pop(session_id, None)


def _build_summary(sess):
    """Convert session accumulator to a clean JSON-serializable dict."""
    s = {
        "session_id": sess["session_id"],
        "developer": sess["developer"],
        "developer_hash": sess["developer_hash"],
        "started_at": sess["started_at"],
        "ended_at": sess["ended_at"],
        "duration_sec": sess.get("duration_sec", 0),
        "duration_human": sess.get("duration_human", ""),
        "total_cost_usd": round(sess["total_cost_usd"], 6),
        "tokens": dict(sess["tokens"]),
        "tokens_total": sum(sess["tokens"].values()),
        "lines_added": sess["lines_added"],
        "lines_removed": sess["lines_removed"],
        "active_time_sec": round(sess["active_time_sec"], 1),
        "turns": sess["turns"],
        "models": dict(sess["models"]),
        "tools": {k: dict(v) for k, v in sess["tools"].items()},
        "tools_total_calls": sum(v["count"] for v in sess["tools"].values()),
        "code_edits": dict(sess["code_edits"]),
        "git": dict(sess["git"]),
        "roi": sess.get("roi", {}),
        "raw_counts": {
            "metric_data_points": sess["_metric_points"],
            "log_events": sess["_log_events"],
        },
    }
    return s


def _write_session_jsonl(summary):
    """Append one JSON line to otel_sessions.json (JSONL format)."""
    try:
        with open(SESSIONS_LOG, 'a') as f:
            f.write(json.dumps(summary, default=str) + '\n')
        print(f"\n  💾 Saved to {SESSIONS_LOG}")
    except Exception as e:
        print(f"\n  ❌ Failed to write session log: {e}")


def _print_session_summary(s):
    """Pretty-print a complete session summary to terminal."""
    w = 70
    print(f"\n{'█'*w}")
    print(f"{'█'*w}")
    print(f"  🏁  SESSION COMPLETE — {s['developer']}")
    print(f"{'█'*w}")

    print(f"\n  {'Session ID:':<22} {s['session_id']}")
    print(f"  {'Developer:':<22} {s['developer']}")
    if s.get('developer_hash'):
        print(f"  {'Hash:':<22} {s['developer_hash'][:16]}...")
    print(f"  {'Duration:':<22} {s.get('duration_human', 'unknown')}")
    print(f"  {'Active time:':<22} {_format_duration(s['active_time_sec'])}")
    print(f"  {'Turns:':<22} {s['turns']}")

    # ── Cost ─────────────────────────────────────────────────────────
    print(f"\n  {'─'*50}")
    print(f"  💰 COST")
    print(f"  {'Total:':<22} ${s['total_cost_usd']:.4f}")

    # ── Tokens ───────────────────────────────────────────────────────
    print(f"\n  {'─'*50}")
    print(f"  🔢 TOKENS")
    tk = s['tokens']
    print(f"  {'Input:':<22} {tk['input']:,}")
    print(f"  {'Output:':<22} {tk['output']:,}")
    print(f"  {'Cache read:':<22} {tk['cache_read']:,}")
    print(f"  {'Cache creation:':<22} {tk['cache_creation']:,}")
    print(f"  {'Total:':<22} {s['tokens_total']:,}")

    # ── Code impact ──────────────────────────────────────────────────
    print(f"\n  {'─'*50}")
    print(f"  📝 CODE IMPACT")
    print(f"  {'Lines added:':<22} +{s['lines_added']}")
    print(f"  {'Lines removed:':<22} -{s['lines_removed']}")
    edits = s['code_edits']
    total_edits = edits['accepted'] + edits['rejected']
    if total_edits > 0:
        rate = edits['accepted'] / total_edits * 100
        print(f"  {'Code edits:':<22} {total_edits} ({edits['accepted']}✓ {edits['rejected']}✗ — {rate:.0f}% accepted)")

    # ── Models ───────────────────────────────────────────────────────
    if s['models']:
        print(f"\n  {'─'*50}")
        print(f"  🤖 MODELS")
        for model, count in sorted(s['models'].items(), key=lambda x: -x[1]):
            # Shorten long model IDs for readability
            short = model.split('.')[-1] if '.' in model else model
            print(f"  {short:<35} × {count}")

    # ── Tool usage ───────────────────────────────────────────────────
    if s['tools']:
        print(f"\n  {'─'*50}")
        print(f"  🔧 TOOLS ({s['tools_total_calls']} total calls)")
        for tool, info in sorted(s['tools'].items(), key=lambda x: -x[1]['count']):
            avg_ms = info['total_ms'] / info['count'] if info['count'] else 0
            status = f"{info['pass']}✓ {info['fail']}✗" if info['fail'] else f"{info['pass']}✓"
            print(f"  {tool:<25} × {info['count']:<4} avg {avg_ms:>6.0f}ms  {status}")

    # ── Git context ──────────────────────────────────────────────────
    git = s['git']
    if git.get('repo'):
        print(f"\n  {'─'*50}")
        print(f"  🔀 GIT CONTEXT")
        repo_short = git['repo'].split('/')[-1].replace('.git', '') if git['repo'] else '—'
        print(f"  {'Repo:':<22} {repo_short}")
        print(f"  {'Branch:':<22} {git.get('branch', '—')}")
        print(f"  {'Commits (2h):':<22} {git.get('commits_in_session', 0)}")

    # ── ROI ───────────────────────────────────────────────────────────
    roi = s.get('roi', {})
    if any(v is not None for v in roi.values()):
        print(f"\n  {'─'*50}")
        print(f"  📈 ROI METRICS")
        if roi.get('cost_per_commit') is not None:
            print(f"  {'Cost per commit:':<22} ${roi['cost_per_commit']:.4f}")
        if roi.get('lines_per_dollar') is not None:
            print(f"  {'Lines per dollar:':<22} {roi['lines_per_dollar']:.0f}")
        if roi.get('code_acceptance_rate') is not None:
            print(f"  {'Acceptance rate:':<22} {roi['code_acceptance_rate']:.0f}%")

    print(f"\n  {'─'*50}")
    print(f"  📊 Raw: {s['raw_counts']['metric_data_points']} metric points, "
          f"{s['raw_counts']['log_events']} log events")
    print(f"{'█'*w}\n")


# ══════════════════════════════════════════════════════════════════════
#  STATUS ENDPOINT — quick check of active sessions
# ══════════════════════════════════════════════════════════════════════

@app.route('/status', methods=['GET'])
def status():
    """Health check + active session summary for debugging."""
    with _lock:
        active = []
        for sid, sess in _sessions.items():
            active.append({
                "session_id": sid,
                "developer": sess["developer"],
                "cost_so_far": round(sess["total_cost_usd"], 4),
                "metrics_received": sess["_metric_points"],
                "logs_received": sess["_log_events"],
                "tools_used": sum(v["count"] for v in sess["tools"].values()),
            })
    # Count completed sessions in JSONL
    completed = 0
    if os.path.exists(SESSIONS_LOG):
        with open(SESSIONS_LOG, 'r') as f:
            completed = sum(1 for _ in f)
    return json.dumps({
        "status": "running",
        "active_sessions": len(active),
        "completed_sessions": completed,
        "sessions": active,
    }, indent=2), 200, {'Content-Type': 'application/json'}


# ══════════════════════════════════════════════════════════════════════
#  MANUAL FLUSH — force-end a session without stop hook
# ══════════════════════════════════════════════════════════════════════

@app.route('/flush/<session_id>', methods=['POST'])
def flush_session(session_id):
    """
    Manually trigger session end (for sessions that don't get a stop hook).
    POST /flush/<session_id>
    """
    with _lock:
        if session_id not in _sessions:
            return json.dumps({"error": f"No active session {session_id}"}), 404
    _handle_session_end(session_id, {})
    return json.dumps({"ok": True, "session_id": session_id}), 200


@app.route('/flush', methods=['POST'])
def flush_all():
    """Flush all active sessions. Useful when Claude Code exits without hook."""
    with _lock:
        sids = list(_sessions.keys())
    for sid in sids:
        _handle_session_end(sid, {})
    return json.dumps({"ok": True, "flushed": len(sids)}), 200


# ══════════════════════════════════════════════════════════════════════
#  UTILITY FUNCTIONS
# ══════════════════════════════════════════════════════════════════════

def _format_duration(seconds):
    """Convert seconds to human-readable duration."""
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    elif s < 3600:
        return f"{s // 60}m {s % 60}s"
    else:
        return f"{s // 3600}h {(s % 3600) // 60}m"

def _acceptance_rate(code_edits):
    """Calculate code acceptance rate as a percentage."""
    total = code_edits['accepted'] + code_edits['rejected']
    if total == 0:
        return None
    return round(code_edits['accepted'] / total * 100, 1)


# ══════════════════════════════════════════════════════════════════════
#  STARTUP
# ══════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description='Claude Code OTEL Receiver v2')
    parser.add_argument('--port', type=int, default=4318, help='Port (default: 4318)')
    parser.add_argument('--devmap', type=str, default=None,
                        help='Path to developers.json mapping file')
    args = parser.parse_args()

    load_developer_map(args.devmap)

    print()
    print("█" * 70)
    print("  Claude Code OTEL Receiver v2 — Enterprise ROI Demo")
    print(f"  Listening on http://localhost:{args.port}")
    print("█" * 70)
    print()
    print("  ✨ What's new in v2:")
    print("  • Per-session aggregation (cost, tokens, tools, models)")
    print("  • Developer name resolution (developers.json)")
    print(f"  • Persistent JSONL log → {os.path.basename(SESSIONS_LOG)}")
    print("  • Tool usage tracking (name, duration, pass/fail)")
    print("  • Git context integration (repo, branch, commits)")
    print("  • Multi-developer concurrent session support")
    print("  • Manual flush: POST /flush or /flush/<session_id>")
    print("  • Status check: GET /status")
    print()
    if PUSH_TO_CLOUDWATCH:
        print(f"  ☁️  CloudWatch PUSH ENABLED")
        print(f"     Region:          {CW_REGION}")
        print(f"     Metrics NS:      {CW_NS_PRODUCTIVITY}, {CW_NS_TOOL_USAGE}")
        print(f"     Logs Group:      {CW_LOG_GROUP}")
    else:
        print("  📁 Local-only mode (set PUSH_TO_CLOUDWATCH=1 to enable CloudWatch)")
    print()
    print("  ── Terminal 2: run these BEFORE starting claude ──")
    print("  export CLAUDE_CODE_ENABLE_TELEMETRY=1")
    print("  export OTEL_METRICS_EXPORTER=otlp")
    print("  export OTEL_LOGS_EXPORTER=otlp")
    print("  export OTEL_EXPORTER_OTLP_PROTOCOL=http/json   ← REQUIRED")
    print(f"  export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:{args.port}")
    print("  claude")
    print()
    print("  ── Install stop hook for git context (one-time) ──")
    print("  cp otel/stop.sh ~/.claude/hooks/stop.sh")
    print("  chmod +x ~/.claude/hooks/stop.sh")
    print()
    print("  ── Type /exit in Claude Code to flush + get session summary ──")
    print()

    # Check if JSONL log exists and report count
    if os.path.exists(SESSIONS_LOG):
        try:
            with open(SESSIONS_LOG, 'r') as f:
                count = sum(1 for _ in f)
            print(f"  📂 Found {count} previous session(s) in {os.path.basename(SESSIONS_LOG)}")
        except Exception:
            pass
    print()
    print("  Waiting for data...\n")

    app.run(host='0.0.0.0', port=args.port, debug=False, use_reloader=False)


if __name__ == '__main__':
    main()
