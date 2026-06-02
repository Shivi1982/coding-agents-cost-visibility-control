#!/usr/bin/env python3
"""
CI/CD Pipeline Trigger MCP Server — GitLab Pipeline Management
==============================================================

MCP tool server for Claude Code that triggers and monitors GitLab CI/CD
pipelines. Developers ask Claude "run the tests" or "check if CI passed"
and this tool interacts with GitLab's API.

OTEL Stream 2 Telemetry (auto-captured on every invocation):
─────────────────────────────────────────────────────────────
  • tool_name: "trigger_pipeline" | "get_pipeline_status" | "get_latest_pipelines" | "get_job_logs"
  • duration_ms: API call latency (network + GitLab processing)
  • status: pass/fail
  • Pipeline trigger events directly measure developer workflow acceleration:
    - "Triggered pipeline via Claude Code" vs manual GitLab navigation
    - Time saved per CI interaction feeds ROI calculations
    - These events flow → CloudWatch → QuickSight ROI Dashboard

Configuration (environment variables):
  GITLAB_BASE_URL  — GitLab instance URL (default: https://gitlab.com)
  GITLAB_TOKEN     — Personal access token with api scope
  GITLAB_PROJECT_ID — Default project ID (can override per-call)

Add to ~/.claude/mcp.json to enable in Claude Code.
"""

# ---------------------------------------------------------------------------
# Auto-install dependencies
# ---------------------------------------------------------------------------
import subprocess, sys

def _ensure(pkg, import_name=None):
    try:
        __import__(import_name or pkg)
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", pkg])

_ensure("mcp", "mcp")
_ensure("requests")

# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------
import json
import os
import time
from typing import Optional
from urllib.parse import quote

import requests
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
GITLAB_BASE_URL = os.environ.get("GITLAB_BASE_URL", "https://gitlab.com")
GITLAB_TOKEN = os.environ.get("GITLAB_TOKEN", "")
GITLAB_PROJECT_ID = os.environ.get("GITLAB_PROJECT_ID", "")

# ---------------------------------------------------------------------------
# GitLab API Client
# ---------------------------------------------------------------------------

class GitLabClient:
    """Thin wrapper around GitLab REST API v4."""

    def __init__(self, base_url: str, token: str):
        self.base_url = base_url.rstrip("/")
        self.api = f"{self.base_url}/api/v4"
        self.session = requests.Session()
        self.session.headers.update({
            "PRIVATE-TOKEN": token,
            "Content-Type": "application/json",
        })
        self.session.timeout = 30

    def _project_url(self, project_id: str) -> str:
        # Support both numeric IDs and namespace/project paths
        return f"{self.api}/projects/{quote(str(project_id), safe='')}"

    def trigger_pipeline(self, project_id: str, ref: str = "main",
                         variables: Optional[dict] = None) -> dict:
        """Trigger a new pipeline run."""
        url = f"{self._project_url(project_id)}/pipeline"
        payload = {"ref": ref}
        if variables:
            payload["variables"] = [
                {"key": k, "value": v} for k, v in variables.items()
            ]
        resp = self.session.post(url, json=payload)
        resp.raise_for_status()
        return resp.json()

    def get_pipeline(self, project_id: str, pipeline_id: int) -> dict:
        """Get details of a specific pipeline."""
        url = f"{self._project_url(project_id)}/pipelines/{pipeline_id}"
        resp = self.session.get(url)
        resp.raise_for_status()
        return resp.json()

    def list_pipelines(self, project_id: str, ref: Optional[str] = None,
                       status: Optional[str] = None, count: int = 5) -> list:
        """List recent pipelines with optional filters."""
        url = f"{self._project_url(project_id)}/pipelines"
        params = {"per_page": count, "order_by": "id", "sort": "desc"}
        if ref:
            params["ref"] = ref
        if status:
            params["status"] = status
        resp = self.session.get(url, params=params)
        resp.raise_for_status()
        return resp.json()

    def get_pipeline_jobs(self, project_id: str, pipeline_id: int) -> list:
        """Get all jobs in a pipeline."""
        url = f"{self._project_url(project_id)}/pipelines/{pipeline_id}/jobs"
        resp = self.session.get(url, params={"per_page": 100})
        resp.raise_for_status()
        return resp.json()

    def get_job_log(self, project_id: str, job_id: int, tail_lines: int = 50) -> str:
        """Get job log output (last N lines)."""
        url = f"{self._project_url(project_id)}/jobs/{job_id}/trace"
        resp = self.session.get(url)
        resp.raise_for_status()
        lines = resp.text.strip().split('\n')
        return '\n'.join(lines[-tail_lines:])

    def retry_pipeline(self, project_id: str, pipeline_id: int) -> dict:
        """Retry a failed pipeline."""
        url = f"{self._project_url(project_id)}/pipelines/{pipeline_id}/retry"
        resp = self.session.post(url)
        resp.raise_for_status()
        return resp.json()

    def cancel_pipeline(self, project_id: str, pipeline_id: int) -> dict:
        """Cancel a running pipeline."""
        url = f"{self._project_url(project_id)}/pipelines/{pipeline_id}/cancel"
        resp = self.session.post(url)
        resp.raise_for_status()
        return resp.json()


# ---------------------------------------------------------------------------
# Demo/Simulation Mode (when no GitLab token configured)
# ---------------------------------------------------------------------------

def _demo_pipeline_status(pipeline_id: int) -> dict:
    """Return simulated pipeline data for demo purposes."""
    import random
    statuses = ["success", "success", "success", "failed", "running"]
    stages = [
        {"name": "lint", "status": "success", "duration": 12},
        {"name": "unit-test", "status": "success", "duration": 45},
        {"name": "build", "status": "success", "duration": 78},
        {"name": "integration-test", "status": random.choice(["success", "failed"]), "duration": 120},
        {"name": "security-scan", "status": "success", "duration": 34},
        {"name": "deploy-staging", "status": "success", "duration": 56},
    ]
    status = random.choice(statuses)
    return {
        "id": pipeline_id,
        "iid": pipeline_id,
        "status": status,
        "ref": "main",
        "sha": "a1b2c3d4e5f6789012345678abcdef0123456789",
        "web_url": f"{GITLAB_BASE_URL}/my-org/my-project/-/pipelines/{pipeline_id}",
        "created_at": "2026-04-28T10:30:00Z",
        "updated_at": "2026-04-28T10:35:22Z",
        "duration": sum(s["duration"] for s in stages),
        "stages": stages,
        "coverage": "87.3%",
        "_mode": "demo (set GITLAB_TOKEN for live API)",
    }

def _demo_trigger() -> dict:
    import random
    pid = random.randint(100000, 999999)
    return {
        "id": pid,
        "iid": pid,
        "status": "pending",
        "ref": "main",
        "sha": "a1b2c3d4e5f6789012345678abcdef0123456789",
        "web_url": f"{GITLAB_BASE_URL}/my-org/my-project/-/pipelines/{pid}",
        "created_at": "2026-04-28T10:30:00Z",
        "_mode": "demo (set GITLAB_TOKEN for live API)",
        "_message": "Pipeline triggered successfully (demo mode)",
    }

def _demo_list_pipelines(count: int) -> list:
    import random
    pipelines = []
    for i in range(count):
        pid = 100000 - i
        pipelines.append({
            "id": pid,
            "status": random.choice(["success", "success", "failed", "success", "running"]),
            "ref": random.choice(["main", "main", "develop", "feature/auth-refactor"]),
            "created_at": f"2026-04-{28 - i}T{10 + i}:00:00Z",
            "duration": random.randint(120, 480),
            "web_url": f"{GITLAB_BASE_URL}/my-org/my-project/-/pipelines/{pid}",
        })
    return pipelines


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------
server = Server("cicd-trigger-mcp")

def _get_client() -> Optional[GitLabClient]:
    if GITLAB_TOKEN:
        return GitLabClient(GITLAB_BASE_URL, GITLAB_TOKEN)
    return None

def _get_project_id(arguments: dict) -> str:
    return str(arguments.get("project_id") or GITLAB_PROJECT_ID or "my-org/my-project")


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="trigger_pipeline",
            description=(
                "Trigger a CI/CD pipeline on GitLab. Starts a new pipeline run on the "
                "specified branch (default: main). Optionally pass CI/CD variables. "
                "Returns pipeline ID and URL for tracking. Use when a developer says "
                "'run the tests', 'trigger CI', 'deploy to staging', 'run the pipeline'."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "ref": {
                        "type": "string",
                        "description": "Branch or tag to run pipeline on (default: main)",
                        "default": "main",
                    },
                    "project_id": {
                        "type": "string",
                        "description": "GitLab project ID or path (e.g., 'my-org/my-project'). Uses GITLAB_PROJECT_ID env var if not specified.",
                    },
                    "variables": {
                        "type": "object",
                        "description": "CI/CD variables to pass (e.g., {'DEPLOY_ENV': 'staging'})",
                        "additionalProperties": {"type": "string"},
                    },
                },
            },
        ),
        Tool(
            name="get_pipeline_status",
            description=(
                "Check the status of a specific CI/CD pipeline. Returns status (running, "
                "success, failed, canceled), duration, stage details, and coverage. "
                "Use when developer asks 'did CI pass?', 'what's the build status?', "
                "'is the pipeline done?'."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "pipeline_id": {
                        "type": "integer",
                        "description": "Pipeline ID to check"
                    },
                    "project_id": {
                        "type": "string",
                        "description": "GitLab project ID or path",
                    },
                    "include_jobs": {
                        "type": "boolean",
                        "description": "Include individual job details (default: true)",
                        "default": True,
                    },
                },
                "required": ["pipeline_id"],
            },
        ),
        Tool(
            name="get_latest_pipelines",
            description=(
                "Get the most recent CI/CD pipelines for a project. Can filter by branch "
                "and status. Returns list with status, duration, branch, and URL. "
                "Use when developer asks 'show recent builds', 'any failed pipelines?', "
                "'what's the latest CI status?'."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "count": {
                        "type": "integer",
                        "description": "Number of pipelines to return (default: 5, max: 20)",
                        "default": 5,
                    },
                    "ref": {
                        "type": "string",
                        "description": "Filter by branch name (e.g., 'main', 'develop')",
                    },
                    "status": {
                        "type": "string",
                        "description": "Filter by status: running, success, failed, canceled, pending",
                        "enum": ["running", "success", "failed", "canceled", "pending"],
                    },
                    "project_id": {
                        "type": "string",
                        "description": "GitLab project ID or path",
                    },
                },
            },
        ),
        Tool(
            name="get_job_logs",
            description=(
                "Get the log output from a specific CI/CD job. Returns the last N lines "
                "of output. Use when a pipeline failed and developer wants to see why: "
                "'show me the test output', 'why did the build fail?', 'get CI logs'."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "job_id": {
                        "type": "integer",
                        "description": "Job ID to get logs for"
                    },
                    "project_id": {
                        "type": "string",
                        "description": "GitLab project ID or path",
                    },
                    "tail_lines": {
                        "type": "integer",
                        "description": "Number of lines from the end to return (default: 50)",
                        "default": 50,
                    },
                },
                "required": ["job_id"],
            },
        ),
        Tool(
            name="retry_pipeline",
            description=(
                "Retry a failed CI/CD pipeline. Reruns all failed jobs. "
                "Use when developer says 'retry the build', 'rerun CI', 'try again'."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "pipeline_id": {
                        "type": "integer",
                        "description": "Pipeline ID to retry"
                    },
                    "project_id": {
                        "type": "string",
                        "description": "GitLab project ID or path",
                    },
                },
                "required": ["pipeline_id"],
            },
        ),
        Tool(
            name="cancel_pipeline",
            description=(
                "Cancel a running CI/CD pipeline. "
                "Use when developer says 'stop the build', 'cancel CI', 'abort pipeline'."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "pipeline_id": {
                        "type": "integer",
                        "description": "Pipeline ID to cancel"
                    },
                    "project_id": {
                        "type": "string",
                        "description": "GitLab project ID or path",
                    },
                },
                "required": ["pipeline_id"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    start = time.time()
    client = _get_client()
    project_id = _get_project_id(arguments)
    is_demo = client is None

    try:
        if name == "trigger_pipeline":
            ref = arguments.get("ref", "main")
            variables = arguments.get("variables")

            if client:
                result = client.trigger_pipeline(project_id, ref, variables)
            else:
                result = _demo_trigger()
                result["ref"] = ref

            elapsed = round((time.time() - start) * 1000, 1)
            result["_api_duration_ms"] = elapsed
            return [TextContent(type="text", text=json.dumps(result, indent=2))]

        elif name == "get_pipeline_status":
            pipeline_id = arguments["pipeline_id"]
            include_jobs = arguments.get("include_jobs", True)

            if client:
                result = client.get_pipeline(project_id, pipeline_id)
                if include_jobs:
                    jobs = client.get_pipeline_jobs(project_id, pipeline_id)
                    result["jobs"] = [
                        {
                            "id": j["id"],
                            "name": j["name"],
                            "stage": j["stage"],
                            "status": j["status"],
                            "duration": j.get("duration"),
                            "web_url": j.get("web_url"),
                        }
                        for j in jobs
                    ]
            else:
                result = _demo_pipeline_status(pipeline_id)

            elapsed = round((time.time() - start) * 1000, 1)
            result["_api_duration_ms"] = elapsed
            return [TextContent(type="text", text=json.dumps(result, indent=2))]

        elif name == "get_latest_pipelines":
            count = min(arguments.get("count", 5), 20)
            ref = arguments.get("ref")
            status = arguments.get("status")

            if client:
                pipelines = client.list_pipelines(project_id, ref, status, count)
            else:
                pipelines = _demo_list_pipelines(count)
                if ref:
                    pipelines = [p for p in pipelines if p["ref"] == ref]
                if status:
                    pipelines = [p for p in pipelines if p["status"] == status]

            elapsed = round((time.time() - start) * 1000, 1)
            result = {
                "project": project_id,
                "pipelines": pipelines[:count],
                "total_returned": len(pipelines[:count]),
                "filters": {"ref": ref, "status": status},
                "_api_duration_ms": elapsed,
            }
            if is_demo:
                result["_mode"] = "demo (set GITLAB_TOKEN for live API)"
            return [TextContent(type="text", text=json.dumps(result, indent=2))]

        elif name == "get_job_logs":
            job_id = arguments["job_id"]
            tail = arguments.get("tail_lines", 50)

            if client:
                log_text = client.get_job_log(project_id, job_id, tail)
            else:
                log_text = (
                    "$ pytest tests/ -v --tb=short\n"
                    "========================= test session starts =========================\n"
                    "collected 47 items\n\n"
                    "tests/test_auth.py::test_login_success PASSED\n"
                    "tests/test_auth.py::test_login_invalid_password PASSED\n"
                    "tests/test_auth.py::test_token_refresh PASSED\n"
                    "tests/test_auth.py::test_mfa_validation PASSED\n"
                    "tests/test_api.py::test_create_resource PASSED\n"
                    "tests/test_api.py::test_unauthorized_access PASSED\n"
                    "tests/test_api.py::test_rate_limiting FAILED\n"
                    "\n"
                    "FAILED tests/test_api.py::test_rate_limiting - AssertionError:\n"
                    "  Expected status 429, got 200. Rate limiter not enforcing.\n"
                    "\n"
                    "========================= 46 passed, 1 failed =========================\n"
                    f"\n[demo mode — set GITLAB_TOKEN for real logs from job {job_id}]"
                )

            elapsed = round((time.time() - start) * 1000, 1)
            result = {
                "job_id": job_id,
                "project": project_id,
                "log_lines": tail,
                "log_output": log_text,
                "_api_duration_ms": elapsed,
            }
            if is_demo:
                result["_mode"] = "demo"
            return [TextContent(type="text", text=json.dumps(result, indent=2))]

        elif name == "retry_pipeline":
            pipeline_id = arguments["pipeline_id"]
            if client:
                result = client.retry_pipeline(project_id, pipeline_id)
            else:
                result = _demo_pipeline_status(pipeline_id)
                result["status"] = "pending"
                result["_message"] = "Pipeline retry triggered (demo mode)"
            elapsed = round((time.time() - start) * 1000, 1)
            result["_api_duration_ms"] = elapsed
            return [TextContent(type="text", text=json.dumps(result, indent=2))]

        elif name == "cancel_pipeline":
            pipeline_id = arguments["pipeline_id"]
            if client:
                result = client.cancel_pipeline(project_id, pipeline_id)
            else:
                result = _demo_pipeline_status(pipeline_id)
                result["status"] = "canceled"
                result["_message"] = "Pipeline canceled (demo mode)"
            elapsed = round((time.time() - start) * 1000, 1)
            result["_api_duration_ms"] = elapsed
            return [TextContent(type="text", text=json.dumps(result, indent=2))]

        else:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]

    except requests.HTTPError as e:
        return [TextContent(type="text", text=json.dumps({
            "error": f"GitLab API error: {e.response.status_code} {e.response.reason}",
            "detail": e.response.text[:500] if e.response else "",
            "tool": name,
            "status": "failed",
            "duration_ms": round((time.time() - start) * 1000, 1),
        }, indent=2))]
    except Exception as e:
        return [TextContent(type="text", text=json.dumps({
            "error": str(e),
            "tool": name,
            "status": "failed",
            "duration_ms": round((time.time() - start) * 1000, 1),
        }, indent=2))]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
