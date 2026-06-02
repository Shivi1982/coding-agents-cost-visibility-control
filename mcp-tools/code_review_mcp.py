#!/usr/bin/env python3
"""
Code Review MCP Server — Automated Lint, Security Scan & Best Practices
========================================================================

MCP tool server for Claude Code that performs automated code review.
Developers ask Claude "review this code before my PR" and this tool
runs lint checks, security pattern scanning (hardcoded secrets, SQL
injection, XSS), and best practices validation.

OTEL Stream 2 Telemetry (auto-captured on every invocation):
─────────────────────────────────────────────────────────────
  • tool_name: "review_code" | "security_scan" | "lint_check"
  • duration_ms: time to analyze the code
  • status: pass/fail (clean code vs findings detected)
  • Finding counts by severity feed into ROI calculations:
    - Critical findings caught = incidents prevented = $$$$ saved
    - These events flow → CloudWatch → QuickSight ROI Dashboard

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

# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------
import json
import os
import re
import time
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import Optional

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

@dataclass
class Finding:
    rule_id: str
    severity: str          # critical | warning | info
    category: str          # security | lint | best-practice
    title: str
    description: str
    line_number: Optional[int] = None
    line_content: Optional[str] = None
    suggestion: Optional[str] = None


# ---------------------------------------------------------------------------
# Security Scanner — Pattern-based detection
# ---------------------------------------------------------------------------

SECURITY_RULES = [
    # ── Hardcoded Secrets ──
    {
        "id": "SEC-001",
        "severity": "critical",
        "title": "Hardcoded AWS Access Key",
        "pattern": r'(?:AKIA|ASIA)[0-9A-Z]{16}',
        "description": "AWS access key ID found in source code. Keys should be in environment variables or Secrets Manager.",
        "suggestion": "Use os.environ['AWS_ACCESS_KEY_ID'] or boto3 credential chain instead.",
    },
    {
        "id": "SEC-002",
        "severity": "critical",
        "title": "Hardcoded Secret/Password",
        "pattern": r'''(?:password|secret|api_key|apikey|access_token|private_key)\s*[=:]\s*['\"][^'"]{8,}['\"]''',
        "description": "Potential hardcoded secret or password. Secrets must never be in source code.",
        "suggestion": "Use AWS Secrets Manager, SSM Parameter Store, or environment variables.",
    },
    {
        "id": "SEC-003",
        "severity": "critical",
        "title": "Hardcoded Private Key",
        "pattern": r'-----BEGIN\s+(RSA\s+)?PRIVATE\s+KEY-----',
        "description": "Private key material found in source code.",
        "suggestion": "Store private keys in Secrets Manager or KMS. Never commit to version control.",
    },
    {
        "id": "SEC-004",
        "severity": "critical",
        "title": "Generic High-Entropy Secret",
        "pattern": r'''(?:token|secret|key)\s*[=:]\s*['"][A-Za-z0-9+/=]{32,}['"]''',
        "description": "High-entropy string assigned to a secret-like variable name.",
        "suggestion": "Move to Secrets Manager or environment variable.",
    },

    # ── SQL Injection ──
    {
        "id": "SEC-010",
        "severity": "critical",
        "title": "SQL Injection — String Formatting in Query",
        "pattern": r'''(?:execute|cursor\.execute|query)\s*\(\s*(?:f['\"]|['\"].*%s|['\"].*\.format\()''',
        "description": "SQL query uses string formatting/f-strings, vulnerable to SQL injection.",
        "suggestion": "Use parameterized queries: cursor.execute('SELECT * FROM users WHERE id = %s', (user_id,))",
    },
    {
        "id": "SEC-011",
        "severity": "critical",
        "title": "SQL Injection — String Concatenation",
        "pattern": r'''(?:SELECT|INSERT|UPDATE|DELETE|DROP)\s+.*\+\s*(?:request|user_input|params|args|data)''',
        "description": "SQL statement built with string concatenation from user input.",
        "suggestion": "Use an ORM (SQLAlchemy) or parameterized queries.",
    },

    # ── XSS ──
    {
        "id": "SEC-020",
        "severity": "warning",
        "title": "Potential XSS — Unsafe HTML Rendering",
        "pattern": r'''(?:innerHTML|outerHTML|document\.write|dangerouslySetInnerHTML|\|safe|markupsafe\.Markup)\s*[=({]''',
        "description": "Direct HTML insertion without sanitization may enable XSS attacks.",
        "suggestion": "Use a sanitization library (DOMPurify, bleach) or template auto-escaping.",
    },
    {
        "id": "SEC-021",
        "severity": "warning",
        "title": "Potential XSS — Unescaped Template Variable",
        "pattern": r'\{\{\s*\w+\s*\|?\s*\}\}',
        "description": "Template variable may render unescaped user input (check framework auto-escape settings).",
        "suggestion": "Ensure auto-escaping is enabled or use explicit escape filters.",
    },

    # ── Command Injection ──
    {
        "id": "SEC-030",
        "severity": "critical",
        "title": "Command Injection — os.system / subprocess with shell=True",
        "pattern": r'''(?:os\.system|subprocess\.(?:call|run|Popen))\s*\(.*(?:shell\s*=\s*True|f['\"]|\.format\()''',
        "description": "Shell command execution with user-controlled input enables command injection.",
        "suggestion": "Use subprocess.run() with shell=False and a list of arguments.",
    },

    # ── Path Traversal ──
    {
        "id": "SEC-040",
        "severity": "warning",
        "title": "Path Traversal Risk",
        "pattern": r'''(?:open|Path)\s*\(\s*(?:f['\"]|.*\+\s*(?:request|user|filename|path|params))''',
        "description": "File path constructed from user input without validation — path traversal risk.",
        "suggestion": "Use os.path.abspath() + validate against allowed base directory.",
    },

    # ── Insecure Configuration ──
    {
        "id": "SEC-050",
        "severity": "warning",
        "title": "Debug Mode Enabled",
        "pattern": r'''(?:DEBUG|debug)\s*[=:]\s*(?:True|true|1|['\"]true['\"])''',
        "description": "Debug mode appears enabled. Ensure this is not deployed to production.",
        "suggestion": "Use environment-specific configuration: DEBUG = os.environ.get('DEBUG', 'false') == 'true'",
    },
    {
        "id": "SEC-051",
        "severity": "warning",
        "title": "CORS Allow All Origins",
        "pattern": r'''(?:Access-Control-Allow-Origin|allow_origins)\s*[=:]\s*['\"]?\*['\"]?''',
        "description": "CORS configured to allow all origins. Restrict to known domains.",
        "suggestion": "Specify allowed origins explicitly: allow_origins=['https://app.example.com']",
    },
    {
        "id": "SEC-052",
        "severity": "warning",
        "title": "SSL Verification Disabled",
        "pattern": r'''verify\s*=\s*False''',
        "description": "SSL certificate verification is disabled, enabling MITM attacks.",
        "suggestion": "Remove verify=False or use a custom CA bundle.",
    },
]

# ---------------------------------------------------------------------------
# Lint Checker — Style & Structure
# ---------------------------------------------------------------------------

LINT_RULES = [
    {
        "id": "LINT-001",
        "severity": "info",
        "title": "Function Too Long",
        "check": "function_length",
        "threshold": 50,
        "description": "Function exceeds 50 lines. Consider refactoring into smaller functions.",
        "suggestion": "Extract logical sections into helper functions with descriptive names.",
    },
    {
        "id": "LINT-002",
        "severity": "info",
        "title": "Too Many Function Parameters",
        "pattern": r'def\s+\w+\s*\([^)]{100,}\)',
        "description": "Function has many parameters. Consider using a dataclass or config object.",
        "suggestion": "Group related parameters into a dataclass or TypedDict.",
    },
    {
        "id": "LINT-003",
        "severity": "warning",
        "title": "Bare Except Clause",
        "pattern": r'except\s*:',
        "description": "Bare except catches all exceptions including KeyboardInterrupt and SystemExit.",
        "suggestion": "Use 'except Exception:' at minimum, or catch specific exception types.",
    },
    {
        "id": "LINT-004",
        "severity": "info",
        "title": "TODO/FIXME/HACK Comment",
        "pattern": r'#\s*(?:TODO|FIXME|HACK|XXX|TEMP)\b',
        "description": "Code contains TODO/FIXME marker — track and resolve before release.",
        "suggestion": "Create a ticket for tracking and reference it in the comment.",
    },
    {
        "id": "LINT-005",
        "severity": "warning",
        "title": "Mutable Default Argument",
        "pattern": r'def\s+\w+\s*\([^)]*(?:\[\]|\{\}|set\(\))\s*\)',
        "description": "Mutable default argument — shared across all calls. Classic Python gotcha.",
        "suggestion": "Use None as default and create the mutable inside the function body.",
    },
    {
        "id": "LINT-006",
        "severity": "info",
        "title": "Print Statement (Use Logging)",
        "pattern": r'^\s*print\s*\(',
        "description": "print() used instead of structured logging.",
        "suggestion": "Use logging.info()/debug()/warning() with structured log format.",
    },
    {
        "id": "LINT-007",
        "severity": "warning",
        "title": "Star Import",
        "pattern": r'from\s+\S+\s+import\s+\*',
        "description": "Wildcard import pollutes namespace and obscures dependencies.",
        "suggestion": "Import specific names: from module import ClassA, function_b",
    },
    {
        "id": "LINT-008",
        "severity": "info",
        "title": "Missing Type Hints",
        "pattern": r'def\s+\w+\s*\([^)]*\)\s*:',  # no -> return type
        "neg_pattern": r'def\s+\w+\s*\([^)]*\)\s*->',
        "description": "Function missing return type hint. Type hints improve readability and IDE support.",
        "suggestion": "Add return type: def function_name(args) -> ReturnType:",
    },
]

# ---------------------------------------------------------------------------
# Best Practices Checker
# ---------------------------------------------------------------------------

BEST_PRACTICE_RULES = [
    {
        "id": "BP-001",
        "severity": "warning",
        "title": "No Error Handling on External Call",
        "pattern": r'(?:requests\.(?:get|post|put|delete)|boto3|urllib|http\.client)\s*\(',
        "neg_pattern": r'try\s*:',
        "description": "External service call without visible try/except — may cause unhandled failures.",
        "suggestion": "Wrap external calls in try/except with retry logic and proper error handling.",
    },
    {
        "id": "BP-002",
        "severity": "info",
        "title": "Magic Number",
        "pattern": r'(?:if|while|for|range|sleep|timeout|limit|max|min)\s*(?:\(|[<>=!]+)\s*\d{2,}',
        "description": "Magic number in logic — extract to a named constant for clarity.",
        "suggestion": "Define as a constant: MAX_RETRIES = 3, TIMEOUT_SECONDS = 30",
    },
    {
        "id": "BP-003",
        "severity": "warning",
        "title": "No Docstring on Public Function",
        "check": "missing_docstring",
        "description": "Public function/class missing docstring.",
        "suggestion": "Add a docstring describing purpose, parameters, and return value.",
    },
    {
        "id": "BP-004",
        "severity": "info",
        "title": "Large File (>300 lines)",
        "check": "file_length",
        "threshold": 300,
        "description": "File exceeds 300 lines. Consider splitting into modules.",
        "suggestion": "Extract related functions into separate modules with clear responsibilities.",
    },
]


# ---------------------------------------------------------------------------
# Analysis Engine
# ---------------------------------------------------------------------------

def _scan_patterns(code: str, rules: list[dict], category: str) -> list[Finding]:
    """Run regex-based pattern rules against code."""
    findings = []
    lines = code.split('\n')

    for rule in rules:
        pattern = rule.get("pattern")
        if not pattern:
            continue

        neg_pattern = rule.get("neg_pattern")

        for i, line in enumerate(lines, 1):
            if re.search(pattern, line, re.IGNORECASE):
                # Check negative pattern (within surrounding context)
                if neg_pattern:
                    context_start = max(0, i - 4)
                    context = '\n'.join(lines[context_start:i + 2])
                    if re.search(neg_pattern, context):
                        continue

                findings.append(Finding(
                    rule_id=rule["id"],
                    severity=rule["severity"],
                    category=category,
                    title=rule["title"],
                    description=rule["description"],
                    line_number=i,
                    line_content=line.strip()[:120],
                    suggestion=rule.get("suggestion"),
                ))

    return findings


def _check_function_length(code: str) -> list[Finding]:
    """Check for overly long functions."""
    findings = []
    lines = code.split('\n')
    func_start = None
    func_name = None
    indent_level = 0

    for i, line in enumerate(lines):
        match = re.match(r'^(\s*)def\s+(\w+)', line)
        if match:
            if func_start is not None:
                length = i - func_start
                if length > 50:
                    findings.append(Finding(
                        rule_id="LINT-001",
                        severity="info",
                        category="lint",
                        title="Function Too Long",
                        description=f"Function '{func_name}' is {length} lines (threshold: 50).",
                        line_number=func_start + 1,
                        suggestion="Extract logical sections into helper functions.",
                    ))
            func_start = i
            func_name = match.group(2)
            indent_level = len(match.group(1))

    # Check last function
    if func_start is not None:
        length = len(lines) - func_start
        if length > 50:
            findings.append(Finding(
                rule_id="LINT-001",
                severity="info",
                category="lint",
                title="Function Too Long",
                description=f"Function '{func_name}' is {length} lines (threshold: 50).",
                line_number=func_start + 1,
                suggestion="Extract logical sections into helper functions.",
            ))

    return findings


def _check_missing_docstrings(code: str) -> list[Finding]:
    """Check for public functions/classes missing docstrings."""
    findings = []
    lines = code.split('\n')

    for i, line in enumerate(lines):
        match = re.match(r'^(?:class|def)\s+([a-zA-Z]\w*)', line)
        if match and not match.group(1).startswith('_'):
            # Check if next non-empty line is a docstring
            has_docstring = False
            for j in range(i + 1, min(i + 3, len(lines))):
                stripped = lines[j].strip()
                if stripped.startswith(('"""', "'''", 'r"""', "r'''")):
                    has_docstring = True
                    break
                if stripped and not stripped.startswith('#'):
                    break

            if not has_docstring:
                findings.append(Finding(
                    rule_id="BP-003",
                    severity="warning",
                    category="best-practice",
                    title="No Docstring on Public Function/Class",
                    description=f"'{match.group(1)}' is missing a docstring.",
                    line_number=i + 1,
                    line_content=line.strip(),
                    suggestion="Add a docstring describing purpose, parameters, and return value.",
                ))

    return findings


def analyze_code(code: str, filename: str = "unknown") -> dict:
    """Run full code analysis: security + lint + best practices."""
    start = time.time()

    all_findings: list[Finding] = []

    # Security scan
    all_findings.extend(_scan_patterns(code, SECURITY_RULES, "security"))

    # Lint checks
    all_findings.extend(_scan_patterns(code, LINT_RULES, "lint"))
    all_findings.extend(_check_function_length(code))

    # Best practices
    all_findings.extend(_scan_patterns(code, BEST_PRACTICE_RULES, "best-practice"))
    all_findings.extend(_check_missing_docstrings(code))

    # Check file length
    line_count = len(code.split('\n'))
    if line_count > 300:
        all_findings.append(Finding(
            rule_id="BP-004",
            severity="info",
            category="best-practice",
            title="Large File (>300 lines)",
            description=f"File has {line_count} lines (threshold: 300).",
            suggestion="Extract related functions into separate modules.",
        ))

    # Deduplicate by rule_id + line
    seen = set()
    unique_findings = []
    for f in all_findings:
        key = (f.rule_id, f.line_number)
        if key not in seen:
            seen.add(key)
            unique_findings.append(f)

    # Sort: critical → warning → info, then by line number
    severity_order = {"critical": 0, "warning": 1, "info": 2}
    unique_findings.sort(key=lambda f: (severity_order.get(f.severity, 3), f.line_number or 0))

    elapsed = round((time.time() - start) * 1000, 1)

    # Summary
    counts = {"critical": 0, "warning": 0, "info": 0}
    for f in unique_findings:
        counts[f.severity] = counts.get(f.severity, 0) + 1

    verdict = "PASS ✅" if counts["critical"] == 0 and counts["warning"] == 0 else \
              "FAIL ❌" if counts["critical"] > 0 else "WARN ⚠️"

    return {
        "file": filename,
        "verdict": verdict,
        "summary": {
            "total_findings": len(unique_findings),
            "critical": counts["critical"],
            "warnings": counts["warning"],
            "info": counts["info"],
            "lines_analyzed": line_count,
        },
        "findings": [asdict(f) for f in unique_findings],
        "analysis_duration_ms": elapsed,
    }


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------
server = Server("code-review-mcp")


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="review_code",
            description=(
                "Comprehensive code review: security scan (hardcoded secrets, SQL injection, "
                "XSS, command injection), lint checks (function length, bare except, mutable "
                "defaults), and best practices (docstrings, error handling, magic numbers). "
                "Returns findings with severity (critical/warning/info), line numbers, and "
                "fix suggestions. Provide a file path OR a code snippet."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path to the file to review (reads from disk)"
                    },
                    "code": {
                        "type": "string",
                        "description": "Code snippet to review (alternative to file_path)"
                    },
                    "filename": {
                        "type": "string",
                        "description": "Filename hint for the snippet (for context)",
                        "default": "snippet.py",
                    },
                },
            },
        ),
        Tool(
            name="security_scan",
            description=(
                "Focused security-only scan. Checks for: hardcoded AWS keys, passwords, "
                "API tokens, private keys, SQL injection (string formatting in queries), "
                "XSS (innerHTML, dangerouslySetInnerHTML), command injection (os.system, "
                "shell=True), path traversal, insecure configs (debug mode, CORS *, "
                "SSL verify=False). Returns only security findings."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path to the file to scan"
                    },
                    "code": {
                        "type": "string",
                        "description": "Code snippet to scan (alternative to file_path)"
                    },
                },
            },
        ),
        Tool(
            name="lint_check",
            description=(
                "Quick lint and style check. Checks: function length (>50 lines), "
                "too many parameters, bare except, TODO/FIXME/HACK comments, mutable "
                "default arguments, print statements (should use logging), star imports, "
                "missing type hints. Returns findings with fix suggestions."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path to the file to check"
                    },
                    "code": {
                        "type": "string",
                        "description": "Code snippet to check (alternative to file_path)"
                    },
                },
            },
        ),
    ]


def _get_code(arguments: dict) -> tuple[str, str]:
    """Extract code and filename from arguments."""
    if "file_path" in arguments and arguments["file_path"]:
        path = Path(arguments["file_path"]).expanduser()
        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")
        code = path.read_text(encoding="utf-8", errors="replace")
        filename = path.name
    elif "code" in arguments and arguments["code"]:
        code = arguments["code"]
        filename = arguments.get("filename", "snippet.py")
    else:
        raise ValueError("Provide either 'file_path' or 'code'")
    return code, filename


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    try:
        code, filename = _get_code(arguments)

        if name == "review_code":
            result = analyze_code(code, filename)
            return [TextContent(type="text", text=json.dumps(result, indent=2))]

        elif name == "security_scan":
            start = time.time()
            findings = _scan_patterns(code, SECURITY_RULES, "security")
            elapsed = round((time.time() - start) * 1000, 1)

            counts = {"critical": 0, "warning": 0, "info": 0}
            for f in findings:
                counts[f.severity] = counts.get(f.severity, 0) + 1

            result = {
                "file": filename,
                "scan_type": "security-only",
                "verdict": "SECURE ✅" if not findings else (
                    "CRITICAL ❌" if counts["critical"] > 0 else "WARNINGS ⚠️"
                ),
                "summary": counts,
                "findings": [asdict(f) for f in findings],
                "duration_ms": elapsed,
            }
            return [TextContent(type="text", text=json.dumps(result, indent=2))]

        elif name == "lint_check":
            start = time.time()
            findings = _scan_patterns(code, LINT_RULES, "lint")
            findings.extend(_check_function_length(code))
            elapsed = round((time.time() - start) * 1000, 1)

            result = {
                "file": filename,
                "scan_type": "lint-only",
                "total_findings": len(findings),
                "findings": [asdict(f) for f in findings],
                "duration_ms": elapsed,
            }
            return [TextContent(type="text", text=json.dumps(result, indent=2))]

        else:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]

    except Exception as e:
        return [TextContent(type="text", text=json.dumps({
            "error": str(e),
            "tool": name,
            "status": "failed",
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
