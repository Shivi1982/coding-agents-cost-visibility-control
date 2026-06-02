# Enterprise ROI Measurement for Claude Code on Amazon Bedrock

## Complete Technical Guide — From Concept to Production Dashboard

---

## 1. Executive Summary

This system measures the return on investment of Claude Code (running on Amazon Bedrock) across your engineering organization. It combines three data pipelines — native OpenTelemetry from Claude Code, git commit attribution via lifecycle hooks, and PR/build webhooks from GitLab or GitHub — to produce a unified CloudWatch dashboard that answers one question: **for every dollar spent on AI-assisted development, what measurable output did the organization get?** The system tracks cost per developer, tokens consumed, lines of code written, code acceptance rate, commits per session, PRs merged, PR cycle time, build pass/fail rate, tool and MCP usage patterns, and session engagement. It requires no changes to developer workflows. Once deployed, it runs silently — every Claude Code session, every git commit, every merged PR feeds data into CloudWatch, where you can build per-developer scorecards, team-level dashboards, and executive ROI summaries.

---

## 2. Architecture Overview

The system consists of three independent data pipelines that converge in Amazon CloudWatch. Each pipeline captures a different layer of the development lifecycle.

### The Three Pipelines

| Pipeline | Source | What It Captures | How It Gets Data |
|---|---|---|---|
| **Pipeline 1: OTEL** | Claude Code (native) | Cost, tokens, lines added/removed, session duration, tool calls, code edits (accept/reject) | Claude Code exports OTLP metrics and log events to a local receiver or CloudWatch Agent |
| **Pipeline 2: Git Attribution** | `~/.claude/hooks/stop.sh` | Repo name, branch, commits made during session, session_id linkage | Shell hook fires on `/exit`, reads git state, sends OTLP log event |
| **Pipeline 3: PR + Build Webhook** | GitLab/GitHub webhook | PRs merged, cycle time, build pass/fail, lines added/removed per PR, files changed, approvals | Git platform fires webhook on MR merge → API Gateway → Lambda → CloudWatch custom metrics |

### Full Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        DEVELOPER WORKSTATION                                │
│                                                                             │
│  ┌───────────────┐     ┌──────────────────────┐     ┌────────────────────┐ │
│  │  Claude Code   │────▶│  OTEL Metrics/Logs   │────▶│ OTLP Receiver     │ │
│  │  (on Bedrock)  │     │  (Stream 1: Metrics) │     │ localhost:4318    │ │
│  │                │     │  (Stream 2: Logs)    │     │  (dev) or         │ │
│  └───────┬───────┘     └──────────────────────┘     │ localhost:4316    │ │
│          │                                           │  (CW Agent prod) │ │
│          │ /exit                                     └────────┬─────────┘ │
│          ▼                                                    │           │
│  ┌───────────────┐                                            │           │
│  │ stop.sh hook  │──── OTLP log event ────────────────────────┘           │
│  │ (Pipeline 2)  │     session_id + repo + branch + commits              │
│  └───────────────┘                                                        │
└─────────────────────────────────────────────────────────────────────────────┘
          │                                              │
          │ (Pipeline 1 + 2 via OTLP)                    │
          ▼                                              ▼
┌─────────────────────────┐                   ┌────────────────────────┐
│   CloudWatch Logs       │                   │   CloudWatch Metrics   │
│   /claude-code/otel     │                   │   (OTEL auto-mapped)   │
│                         │                   │                        │
│   • Session events      │                   │   • claude_code.cost   │
│   • code_edit accept/   │                   │   • claude_code.tokens │
│     reject events       │                   │   • claude_code.lines  │
│   • Git attribution     │                   │   • claude_code.session│
│     (from stop.sh)      │                   │                        │
└────────────┬────────────┘                   └───────────┬────────────┘
             │                                            │
             │              ┌─────────────────────┐       │
             └──────────────▶  CloudWatch Dashboard ◀──────┘
                            │  (Unified ROI View)  │
             ┌──────────────▶                      │
             │              └─────────────────────┘
             │
┌────────────┴────────────────────────────────────────────────────────────────┐
│                         PIPELINE 3: PR + BUILD WEBHOOK                      │
│                                                                             │
│  ┌─────────────────┐    ┌──────────────────┐    ┌───────────────────────┐  │
│  │  GitLab/GitHub   │───▶│  API Gateway     │───▶│  Lambda Function      │  │
│  │  MR/PR merged    │    │  (HTTPS POST)    │    │  • Parse MR payload   │  │
│  │  webhook fires   │    │  /webhook        │    │  • Extract developer  │  │
│  └─────────────────┘    └──────────────────┘    │  • Calculate cycle    │  │
│                                                  │    time (hrs)         │  │
│                                                  │  • Build pass/fail    │  │
│                                                  │  • Lines, files,      │  │
│                                                  │    approvals          │  │
│                                                  │  ▼                    │  │
│                                                  │  CloudWatch           │  │
│                                                  │  put_metric_data()    │  │
│                                                  │  Namespace:           │  │
│                                                  │  ClaudeCode/          │  │
│                                                  │   DevProductivity     │  │
│                                                  └───────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Data Flow Summary

1. **Developer uses Claude Code** → OTEL metrics (cost, tokens, lines) stream to CloudWatch every 60 seconds
2. **Developer types `/exit`** → stop.sh hook fires, links session_id to git repo/branch/commits
3. **Developer merges a PR** → GitLab/GitHub webhook fires → Lambda writes PR metrics to CloudWatch
4. **CloudWatch Dashboard** → Joins all three data streams by developer name for unified ROI view

---

## 3. What Gets Measured (Complete Signal Map)

Every signal in the system, where it comes from, and what decision it enables.

### Full Signal Table

| Signal | Pipeline | Metric Name / Field | Decision It Enables |
|---|---|---|---|
| **Cost per API call** | P1 (OTEL Metrics) | `claude_code.cost.usage` | Budget allocation, per-developer spend caps |
| **Input tokens** | P1 (OTEL Metrics) | `claude_code.tokens.input` | Prompt optimization, cache tuning |
| **Output tokens** | P1 (OTEL Metrics) | `claude_code.tokens.output` | Cost forecasting (output tokens cost 5x input) |
| **Cache read/write tokens** | P1 (OTEL Metrics) | `claude_code.tokens.cacheRead` / `claude_code.tokens.cacheWrite` | Measure prompt caching effectiveness — cache reads cost 90% less |
| **Lines added** | P1 (OTEL Metrics) | `claude_code.lines_added` | Code volume, productivity signal |
| **Lines removed** | P1 (OTEL Metrics) | `claude_code.lines_removed` | Refactoring activity signal |
| **Session count** | P1 (OTEL Metrics) | `claude_code.session.count` | Adoption tracking |
| **Active session time** | P1 (OTEL Metrics) | `claude_code.active_time.total` | Engagement depth |
| **Turns per session** | P1 (OTEL Metrics) | `claude_code.turns` | Conversation complexity |
| **Code edit events** | P1 (OTEL Logs) | `code_edit` event with `accepted`/`rejected` attribute | Code acceptance rate — the highest-fidelity ROI signal |
| **Tool calls** | P1 (OTEL Logs) | `tool_use` events with tool name, duration | Which tools are used, how often, MCP adoption |
| **Git repository** | P2 (Git Hook) | `git.repo` attribute | Attribution — which repo got the AI investment |
| **Git branch** | P2 (Git Hook) | `git.branch` attribute | Feature vs. main branch work distribution |
| **Commits in session** | P2 (Git Hook) | `commits.in_session` attribute | Direct output — did the session produce commits? |
| **Session ID linkage** | P2 (Git Hook) | `session.id` attribute | Links OTEL cost data to git output |
| **PRs merged** | P3 (Webhook) | `PRsMerged` (CloudWatch metric) | Deployment frequency, output volume |
| **PR cycle time** | P3 (Webhook) | `PRCycleTimeHours` (CloudWatch metric) | How fast code moves from branch to main |
| **Build pass/fail** | P3 (Webhook) | `BuildPassRate` (CloudWatch metric) | AI code quality — does it break builds? |
| **Lines added per PR** | P3 (Webhook) | `LinesAddedPerPR` (CloudWatch metric) | PR size, review burden |
| **Lines removed per PR** | P3 (Webhook) | `LinesRemovedPerPR` (CloudWatch metric) | Cleanup and refactoring |
| **Files changed per PR** | P3 (Webhook) | `FilesChangedPerPR` (CloudWatch metric) | Change scope |
| **Approvals per PR** | P3 (Webhook) | `ApprovalsPerPR` (CloudWatch metric) | Review throughput |

### What is NOT Captured (and Why)

| Signal | Why It's Missing | Workaround |
|---|---|---|
| Which specific lines Claude wrote vs. developer wrote | Claude Code doesn't tag generated lines in the diff | Use `code_edit` accept/reject events as proxy — accepted edits = Claude-authored code |
| Jira/Linear ticket linkage | No native integration with project management tools | Parse PR title/description for ticket IDs in the Lambda webhook |
| Time saved vs. manual coding | No baseline comparison available | Survey developers, compare PR velocity before/after Claude Code rollout |
| Individual prompt quality | OTEL logs event types, not prompt content | Enable Bedrock Model Invocation Logging to S3 for full prompt/response capture |

---

## 4. ROI Formulas

Concrete formulas you can calculate from the signals above. Each includes the data source and a worked example.

### 4.1 Cost per Commit

```
Cost per Commit = Session Cost ÷ Commits in Session
```

**Data source:** Pipeline 1 (`claude_code.cost.usage` summed for session) ÷ Pipeline 2 (`commits.in_session`)

**Example:**
- Session cost: $0.42 (summed from OTEL metrics)
- Commits in session: 3 (from stop.sh hook)
- **Cost per commit: $0.14**

**Interpretation:** If your average developer costs $75/hr and takes 20 minutes per commit manually, each manual commit costs ~$25 in developer time. At $0.14 per AI-assisted commit, that's a **178x cost reduction per commit** (with the caveat that commit complexity varies).

### 4.2 Cost per PR

```
Cost per PR = Weekly Developer AI Cost ÷ PRs Merged That Week
```

**Data source:** Pipeline 1 (weekly cost sum per developer) ÷ Pipeline 3 (`PRsMerged` count per developer per week)

**Example:**
- Developer weekly AI cost: $18.50
- PRs merged this week: 7
- **Cost per PR: $2.64**

### 4.3 Developer Productivity Score

```
Productivity Score = (W1 × Cost per PR score) + (W2 × Build Pass Rate) + (W3 × Code Acceptance Rate)

Where:
  Cost per PR score = 1 - (actual_cost_per_PR / cost_ceiling)   [clamped 0–1]
  Build Pass Rate = builds passed / total builds                 [0–1]
  Code Acceptance Rate = accepted edits / total edits            [0–1]
  W1 + W2 + W3 = 1.0 (default: 0.4, 0.3, 0.3)
```

**Data source:** Pipeline 1 (cost, acceptance rate from `code_edit` events) + Pipeline 3 (PRs merged, build pass rate)

**Example:**
- Cost per PR: $2.64 (with a ceiling of $10.00 → score = 1 - 2.64/10 = 0.736)
- Build pass rate: 0.86
- Code acceptance rate: 0.72
- **Productivity Score: (0.4 × 0.736) + (0.3 × 0.86) + (0.3 × 0.72) = 0.294 + 0.258 + 0.216 = 0.77**

**Interpretation:** Score ranges 0–1. Each component is independently meaningful:
- Cost per PR score: "How efficiently is AI spend converting to shipped PRs?" (higher = cheaper per PR)
- Build pass rate: "Is the shipped code passing CI?" (standalone quality signal)
- Code acceptance rate: "Is the developer keeping Claude's suggestions?" (relevance signal)

**Why weighted average, not multiplication:** Multiplying a count (PRs) by percentages produces a meaningless number. A weighted average keeps each input in its natural 0–1 range, makes the final score interpretable (0 = worst, 1 = best), and lets you tune weights to match organizational priorities.

**How to use:** Compare across developers/teams. A developer at 0.85 is performing well across all three dimensions. A developer at 0.45 has a weak spot — look at which component is dragging the score down and address it specifically (cost optimization? code quality? prompt engineering?).

### 4.4 Tool Maturity Index

```
Tool Maturity Index = Unique Tools Used ÷ Total Available Tools
```

**Data source:** Pipeline 1 (OTEL log events with `tool_use` type, count distinct tool names) ÷ count of configured MCP + built-in tools

**Example:**
- Unique tools used: 6 (Bash, Read, Write, Edit, semantic_search, code_review)
- Total available tools: 10 (4 built-in + 4 MCP + 2 custom)
- **Tool Maturity Index: 0.60 (60%)**

**Interpretation:** Low index (< 30%) suggests the developer isn't leveraging available tooling. High index (> 70%) suggests mature adoption. Use this to identify training opportunities.

### 4.5 AI Code Quality Score

```
AI Code Quality = Build Pass Rate (for developers using Claude Code)
```

**Data source:** Pipeline 3 (`BuildPassed` / `TotalBuilds` per developer, filtered to developers with active OTEL sessions)

**Example:**
- Developer's merged PRs this month: 12
- PRs where CI build passed: 11
- **AI Code Quality: 91.7%**

**Interpretation:** Track this as a standalone percentage per developer. If a developer's build pass rate drops after adopting Claude Code (compare to their own historical baseline), it signals they may be accepting AI suggestions without adequate review.

**Note:** The previous formula (AI build rate ÷ org-wide build rate) is misleading because it doesn't control for task complexity, developer experience, or which repos are being worked on. A standalone rate tracked over time per developer is more honest — compare each developer to their own pre-Claude-Code baseline, not to unrelated developers.

### 4.6 Session Efficiency

```
Session Efficiency = (Lines Added + Lines Removed) ÷ Total Tokens Consumed
```

**Data source:** Pipeline 1 (all OTEL metrics from single session)

**Example:**
- Lines added: 142, Lines removed: 38
- Total tokens: 45,000 (input + output)
- **Session Efficiency: 180 ÷ 45,000 = 0.004 lines per token**

**Interpretation:** Higher is better. Developers who write clear prompts and let Claude work in larger batches will have higher efficiency. Use this to identify developers who might benefit from prompt engineering training.

### Formula Reference Card

| Formula | Inputs | Good Range | Action if Low |
|---|---|---|---|
| Cost per Commit | P1 + P2 | < $1.00 | Check if sessions are producing commits |
| Cost per PR | P1 + P3 | < $5.00 | Review session-to-PR conversion rate |
| Productivity Score | P1 + P3 | > 0.70 (weighted avg, 0–1 scale) | Identify which component is low: cost efficiency, build quality, or acceptance rate |
| Tool Maturity Index | P1 | > 0.50 | Educate on available tools |
| AI Code Quality | P3 | > 85% build pass rate | Compare to developer's own pre-Claude baseline |
| Session Efficiency | P1 | > 0.003 | Prompt engineering training |

---

## 5. Pipeline 1: Claude Code OTEL Setup

### What This Pipeline Does

Claude Code has native OpenTelemetry support. When enabled, it exports two data streams:

- **Stream 1 (Metrics)**: Aggregated numbers flushed every 60 seconds — cost, tokens, lines, session duration
- **Stream 2 (Log Events)**: Per-action detail events — every prompt, every API call, every file edit with accept/reject status

No custom code required. You set 5 environment variables, point them at a receiver, and data flows.

### Environment Variables

Set these before launching `claude`:

```bash
export CLAUDE_CODE_ENABLE_TELEMETRY=1
export OTEL_METRICS_EXPORTER=otlp
export OTEL_LOGS_EXPORTER=otlp
export OTEL_EXPORTER_OTLP_PROTOCOL=http/json
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
```

| Variable | Value | Purpose |
|---|---|---|
| `CLAUDE_CODE_ENABLE_TELEMETRY` | `1` | Master switch — enables OTEL export |
| `OTEL_METRICS_EXPORTER` | `otlp` | Use OTLP protocol for metrics (not console, not prometheus) |
| `OTEL_LOGS_EXPORTER` | `otlp` | Use OTLP protocol for log events |
| `OTEL_EXPORTER_OTLP_PROTOCOL` | `http/json` | Use HTTP+JSON format (human-readable). Without this, data ships as protobuf binary — still works but unreadable for debugging |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://localhost:4318` | Where to send data. Port 4318 = standard OTLP HTTP port. Change to `4316` for CloudWatch Agent |

### Stream 1: Metrics (What You Get)

Metrics are aggregated counters and gauges flushed every ~60 seconds.

| Metric Name | Type | Unit | Description |
|---|---|---|---|
| `claude_code.cost.usage` | Counter | USD | Cumulative cost per API call — the primary spend signal |
| `claude_code.tokens.input` | Counter | Count | Input tokens consumed (prompt + context) |
| `claude_code.tokens.output` | Counter | Count | Output tokens generated (response) |
| `claude_code.tokens.cacheRead` | Counter | Count | Tokens served from prompt cache (90% cheaper) |
| `claude_code.tokens.cacheWrite` | Counter | Count | Tokens written to prompt cache |
| `claude_code.lines_added` | Counter | Count | Lines of code added during the session |
| `claude_code.lines_removed` | Counter | Count | Lines of code removed during the session |
| `claude_code.session.count` | Counter | Count | Number of sessions opened |
| `claude_code.turns` | Counter | Count | Conversational turns (user prompt → model response) |
| `claude_code.active_time.total` | Gauge | Seconds | Active typing/interaction time |

**Attributes attached to every metric data point:**

| Attribute | Example Value | Description |
|---|---|---|
| `session.id` | `a1b2c3d4-e5f6-...` | UUID for the session — the join key for Pipeline 2 |
| `model` | `claude-sonnet-4-6` | Which model was used |
| `provider` | `bedrock` | Confirms Bedrock backend |
| `account_id` | `YOUR_ACCOUNT_ID` | AWS account ID |

### Stream 2: Log Events (What You Get)

Log events are individual action records with full detail.

| Event Type | Key Attributes | Why It Matters |
|---|---|---|
| `code_edit` | `file_path`, `lines_added`, `lines_removed`, `accepted` (true/false) | **Code Acceptance Rate** — the best single ROI signal. Tells you what % of Claude's suggestions the developer actually kept |
| `tool_use` | `tool_name`, `duration_ms` | Which tools Claude called (Bash, Read, Write, Edit, Grep, MCP tools) and how long each took |
| `api_call` | `model`, `input_tokens`, `output_tokens`, `cost` | Per-call detail for cost attribution |
| `session_start` | `session.id`, `cwd`, `model` | Session begin — working directory tells you which project |
| `session_end` | `session.id`, `total_cost`, `total_turns` | Session summary on `/exit` |

### Stream 1 vs. Stream 2: When to Use Which

| Question | Use Stream | Why |
|---|---|---|
| "How much did alice.chen spend this week?" | Stream 1 (Metrics) | Aggregated cost counter, fast to query |
| "What's our org-wide code acceptance rate?" | Stream 2 (Logs) | Need `code_edit` events with accept/reject detail |
| "How many tokens per day across all developers?" | Stream 1 (Metrics) | Aggregated token counters |
| "Which specific files did Claude edit in session X?" | Stream 2 (Logs) | Need per-event file path detail |
| "What tools are being used most?" | Stream 2 (Logs) | Need `tool_use` events with tool name |
| "What's our daily cost trend?" | Stream 1 (Metrics) | Time-series aggregation on cost counter |

### Local Receiver for Development/Testing

For local development and demos, run the included Python receiver. It pretty-prints OTEL data to your terminal.

**Terminal 1 — Start the receiver:**

```bash
python3 otel_receiver.py
```

This starts a Flask server on port 4318 that accepts OTLP HTTP data and prints it formatted. The receiver handles `/v1/metrics`, `/v1/logs`, and `/v1/traces` endpoints.

**Terminal 2 — Set env vars and start Claude Code:**

```bash
export CLAUDE_CODE_ENABLE_TELEMETRY=1
export OTEL_METRICS_EXPORTER=otlp
export OTEL_LOGS_EXPORTER=otlp
export OTEL_EXPORTER_OTLP_PROTOCOL=http/json
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
claude
```

Use Claude normally. When you type `/exit`, metrics flush to Terminal 1. You'll see output like:

```
────────────────────────────────────────────────────────
[14:23:15] 📊  METRICS  (8 data points)
  claude_code.cost.usage                          = 0.0342
                                                    ↳ session.id=a1b2c3d4  model=claude-sonnet-4-6
  claude_code.tokens.input                        = 12450
  claude_code.tokens.output                       = 3280
  claude_code.tokens.cacheRead                    = 8200
  claude_code.lines_added                         = 47
  claude_code.lines_removed                       = 12
  claude_code.session.count                       = 1
  claude_code.turns                               = 4

────────────────────────────────────────────────────────
[14:23:15] 📝  LOG EVENTS  (6 records)
  event: code_edit
    file_path: src/auth.py
    lines_added: 23
    accepted: true
  event: tool_use
    tool_name: Bash
    duration_ms: 1240
  event: tool_use
    tool_name: Read
    duration_ms: 45
```

### CloudWatch Agent for Production

For production deployment, replace the local receiver with the CloudWatch Agent configured as an OTLP receiver.

**Step 1: Install CloudWatch Agent** (if not already present):

```bash
# macOS
brew install amazon-cloudwatch-agent

# Amazon Linux / AL2023
sudo yum install amazon-cloudwatch-agent

# Ubuntu/Debian
sudo apt-get install amazon-cloudwatch-agent
```

**Step 2: Configure OTLP receiver** — create `/opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json`:

```json
{
  "logs": {
    "metrics_collected": {
      "otlp": {
        "grpc_endpoint": "0.0.0.0:4317",
        "http_endpoint": "0.0.0.0:4316"
      }
    }
  },
  "metrics": {
    "metrics_collected": {
      "otlp": {
        "grpc_endpoint": "0.0.0.0:4317",
        "http_endpoint": "0.0.0.0:4316"
      }
    }
  }
}
```

**Step 3: Start the agent:**

```bash
sudo /opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl \
  -a fetch-config \
  -m ec2 \
  -s \
  -c file:/opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json
```

**Step 4: Update the OTEL endpoint** to point at the CloudWatch Agent:

```bash
# Change from local receiver to CloudWatch Agent
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4316
```

All other env vars remain identical. Metrics and logs now flow directly into CloudWatch Logs and CloudWatch Metrics.

---

## 6. Pipeline 2: Git Attribution Setup

### What This Pipeline Does

Pipeline 1 tells you Claude spent $0.42 in a session. Pipeline 2 answers: **did that session actually produce any code that was committed?** It links the OTEL session ID to git state at the time the session ends.

### What Are Hooks?

Claude Code has a lifecycle hook system. Hooks are shell scripts placed in `~/.claude/hooks/` that fire automatically at specific moments:

| Hook | File | When It Fires |
|---|---|---|
| **start** | `~/.claude/hooks/start.sh` | When a Claude Code session begins |
| **stop** | `~/.claude/hooks/stop.sh` | When you type `/exit` |
| **pre_tool_use** | `~/.claude/hooks/pre_tool_use.sh` | Before Claude calls any tool (Bash, Read, Write, etc.) |
| **post_tool_use** | `~/.claude/hooks/post_tool_use.sh` | After a tool call completes |

Claude Code passes a JSON payload to each hook via stdin. The payload includes `session_id`, `cwd` (current working directory), and context about the event.

### The stop.sh Script (Line-by-Line)

```bash
#!/bin/bash

# Claude Code STOP HOOK — Pipeline 2: Git Attribution
# This script fires automatically when you type /exit in Claude Code
# It reads git context and sends it to the OTEL receiver

# Step 1: Read the session ID from stdin
# Claude Code passes JSON to the hook via stdin. We extract the session_id.
# This is the same session_id that appears in Pipeline 1 OTEL metrics.
SESSION_ID=$(cat /dev/stdin | jq -r '.session_id')

# Step 2: Get git context from the current working directory
# git remote get-url origin → the repo URL (e.g., git@github.com:org/repo.git)
REPO=$(git remote get-url origin 2>/dev/null || echo "unknown")

# git branch --show-current → the branch name (e.g., feature/add-auth)
BRANCH=$(git branch --show-current 2>/dev/null || echo "unknown")

# git log --since="2 hours ago" → count commits made in the last 2 hours
# This is a heuristic — it captures commits made during a typical Claude Code session
COMMITS=$(git log --since="2 hours ago" --oneline | wc -l | tr -d ' ')

# Step 3: Send git context + session ID as an OTLP log event
# This goes to the same receiver as Pipeline 1 data (localhost:4318 or CloudWatch Agent)
# The key insight: session.id here matches the session.id in Pipeline 1 metrics
# This is what enables the join: cost from Pipeline 1 ↔ commits from Pipeline 2
curl -s -X POST http://localhost:4318/v1/logs \
  -H "Content-Type: application/json" \
  -d '{
    "resourceLogs": [{
      "scopeLogs": [{
        "logRecords": [{
          "body": {"stringValue": "claude_code.session_end"},
          "attributes": [
            {"key":"session.id","value":{"stringValue":"'$SESSION_ID'"}},
            {"key":"git.repo","value":{"stringValue":"'$REPO'"}},
            {"key":"git.branch","value":{"stringValue":"'$BRANCH'"}},
            {"key":"commits.in_session","value":{"intValue":'$COMMITS'}}
          ]
        }]
      }]
    }]
  }' &>/dev/null &
# The trailing & runs the curl in the background so /exit doesn't hang waiting for the HTTP call
```

### Installation

**One-time setup (per developer machine):**

```bash
# Create hooks directory
mkdir -p ~/.claude/hooks

# Copy the stop.sh script
cp stop.sh ~/.claude/hooks/stop.sh

# Make it executable
chmod +x ~/.claude/hooks/stop.sh

# Verify
ls -la ~/.claude/hooks/stop.sh
# Should show: -rwxr-xr-x ... stop.sh
```

Or use the provided installer:

```bash
bash install_pipeline2.sh
```

### How session_id Links OTEL to Git

This is the critical connection that makes ROI calculation possible.

```
Pipeline 1 (OTEL):
  session.id = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
  claude_code.cost.usage = 0.42
  claude_code.tokens.input = 15200
  claude_code.lines_added = 68

Pipeline 2 (stop.sh):
  session.id = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"   ← same UUID
  git.repo = "git@github.com:acme/backend.git"
  git.branch = "feature/add-auth"
  commits.in_session = 3

JOIN on session.id:
  → Session a1b2c3d4 cost $0.42 and produced 3 commits on acme/backend
  → Cost per commit: $0.42 ÷ 3 = $0.14
```

### What the Output Looks Like

When you type `/exit` with Pipeline 2 active, the OTEL receiver shows:

```
────────────────────────────────────────────────────────
[14:25:03] 📝  LOG EVENTS  (1 records)
  event: claude_code.session_end
    session.id: a1b2c3d4-e5f6-7890-abcd-ef1234567890
    git.repo: git@github.com:acme/backend.git
    git.branch: feature/add-auth
    commits.in_session: 3
```

### Testing Pipeline 2

```bash
# Step 1: Start the OTEL receiver
python3 otel_receiver.py

# Step 2: In another terminal, navigate to a git repo and start Claude
cd ~/projects/my-repo
export CLAUDE_CODE_ENABLE_TELEMETRY=1
export OTEL_METRICS_EXPORTER=otlp
export OTEL_LOGS_EXPORTER=otlp
export OTEL_EXPORTER_OTLP_PROTOCOL=http/json
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
claude

# Step 3: Use Claude — ask a question, make some edits

# Step 4: Type /exit

# Step 5: Check Terminal 1 — you should see:
#   📊 METRICS (cost, tokens, lines)
#   📝 LOG EVENTS (session_end with git.repo, git.branch, commits.in_session)
```

**Troubleshooting:**
- If `git.repo` shows `unknown`: you're not in a git repo. `cd` into one before launching claude.
- If `commits.in_session` shows `0`: you didn't make any commits in the last 2 hours. Make a test commit first.
- If nothing appears: check that `~/.claude/hooks/stop.sh` exists and has execute permission (`chmod +x`).

---

## 7. Pipeline 3: PR + Build Webhook Setup

### What This Pipeline Does

Pipeline 2 tells you commits were made. Pipeline 3 answers: **did those commits get merged into a PR, pass code review, and survive the build?**

Think of it like a doorbell. When someone merges a PR on GitLab or GitHub, it's like pressing the doorbell — the platform sends an HTTP request (the "ring") to a URL you control. Your Lambda function opens the door, reads the delivery (the webhook payload), and records what it finds in CloudWatch.

### Architecture

```
Developer merges PR on GitLab/GitHub
         ↓
Git platform fires webhook (HTTP POST with JSON payload)
         ↓
API Gateway (public HTTPS endpoint — this is what GitLab/GitHub calls)
         ↓
Lambda function (parses payload, extracts metrics)
         ↓
CloudWatch put_metric_data() — writes to ClaudeCode/DevProductivity namespace
         ↓
Dashboard queries these metrics alongside Pipeline 1 + 2 data
```

**Why do you need API Gateway?** GitLab and GitHub servers can't reach `localhost` on your machine. You need a real, publicly accessible HTTPS endpoint. API Gateway provides that endpoint and routes requests to your Lambda function.

### Step 1: Create the IAM Role

The Lambda function needs permission to write CloudWatch metrics.

```bash
# Create the trust policy (allows Lambda to assume this role)
cat > trust-policy.json << 'EOF'
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {"Service": "lambda.amazonaws.com"},
    "Action": "sts:AssumeRole"
  }]
}
EOF

# Create the role
aws iam create-role \
  --role-name claude-code-roi-lambda-role \
  --assume-role-policy-document file://trust-policy.json

# Attach CloudWatch permissions
cat > lambda-policy.json << 'EOF'
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "cloudwatch:PutMetricData"
      ],
      "Resource": "*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "logs:CreateLogGroup",
        "logs:CreateLogStream",
        "logs:PutLogEvents"
      ],
      "Resource": "arn:aws:logs:*:*:*"
    }
  ]
}
EOF

aws iam put-role-policy \
  --role-name claude-code-roi-lambda-role \
  --policy-name cloudwatch-write \
  --policy-document file://lambda-policy.json

# Wait for IAM propagation (takes ~10 seconds)
sleep 10
```

### Step 2: Lambda Function

This is the full Lambda function. It handles both GitLab and GitHub webhook payloads.

```python
import json
import boto3
from datetime import datetime

cw = boto3.client('cloudwatch')

def lambda_handler(event, context):
    """
    Parse GitLab MR or GitHub PR webhook payload.
    Extract: developer name, cycle time, build pass/fail, lines changed.
    Write custom metrics to CloudWatch — parameterized per developer.
    """
    
    try:
        body = json.loads(event.get('body', '{}'))
        
        # Detect source: GitLab or GitHub
        if 'object_kind' in body:
            return handle_gitlab(body)
        elif 'pull_request' in body:
            return handle_github(body)
        else:
            return {'statusCode': 400, 'body': 'Unrecognized webhook format'}
    
    except Exception as e:
        print(f'Error: {str(e)}')
        return {'statusCode': 500, 'body': json.dumps({'error': str(e)})}


def handle_gitlab(body):
    """Process GitLab Merge Request webhook."""
    
    if body.get('object_kind') != 'merge_request':
        return {'statusCode': 400, 'body': 'Not a merge request event'}
    
    mr = body.get('object_attributes', {})
    if mr.get('state') != 'merged':
        return {'statusCode': 200, 'body': 'MR not merged, skipping'}
    
    developer_name = body.get('user', {}).get('username', 'unknown')
    repository = body.get('project', {}).get('path_with_namespace', 'unknown')
    mr_id = mr.get('iid', 'unknown')
    mr_title = mr.get('title', 'Untitled')
    
    # Cycle time
    created_at = mr.get('created_at', '')
    merged_at = mr.get('merged_at', '')
    cycle_time_hours = 0
    if created_at and merged_at:
        created = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
        merged = datetime.fromisoformat(merged_at.replace('Z', '+00:00'))
        cycle_time_hours = (merged - created).total_seconds() / 3600
    
    # Code volume (GitLab sends these on the MR attributes)
    lines_added = mr.get('additions', 0)
    lines_removed = mr.get('deletions', 0)
    files_changed = mr.get('changed_files', 0)
    
    # Build status
    pipeline_status = mr.get('merge_status', 'unknown')
    build_passed = pipeline_status in ['can_be_merged', 'can_be_merged_auto_merge_enabled']
    
    # Approvals
    approvals = len(body.get('approvals', []))
    
    # Commit hashes (for session ↔ MR linkage)
    commits = [c.get('id', '') for c in body.get('commits', [])]
    
    write_metrics(developer_name, repository, cycle_time_hours, 
                  build_passed, lines_added, lines_removed, 
                  files_changed, approvals)
    
    # Also log commit hashes for session linkage
    if commits:
        print(json.dumps({
            'event': 'mr_merged',
            'developer': developer_name,
            'repository': repository,
            'mr_id': mr_id,
            'commits': commits
        }))
    
    return {
        'statusCode': 200,
        'body': json.dumps({
            'message': 'Metrics recorded',
            'developer': developer_name,
            'repository': repository,
            'mr_id': mr_id
        })
    }


def handle_github(body):
    """Process GitHub Pull Request webhook."""
    
    action = body.get('action', '')
    if action != 'closed':
        return {'statusCode': 200, 'body': 'PR not closed, skipping'}
    
    pr = body.get('pull_request', {})
    if not pr.get('merged'):
        return {'statusCode': 200, 'body': 'PR closed without merge, skipping'}
    
    developer_name = pr.get('user', {}).get('login', 'unknown')
    repository = body.get('repository', {}).get('full_name', 'unknown')
    
    # Cycle time
    created_at = pr.get('created_at', '')
    merged_at = pr.get('merged_at', '')
    cycle_time_hours = 0
    if created_at and merged_at:
        created = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
        merged = datetime.fromisoformat(merged_at.replace('Z', '+00:00'))
        cycle_time_hours = (merged - created).total_seconds() / 3600
    
    # Code volume (GitHub includes these directly on the PR object)
    lines_added = pr.get('additions', 0)
    lines_removed = pr.get('deletions', 0)
    files_changed = pr.get('changed_files', 0)
    
    # Build status — GitHub uses merge_commit_sha + status checks
    # Simplified: if the PR was mergeable, the build passed
    build_passed = pr.get('mergeable_state', '') == 'clean'
    
    # Review comments as proxy for approvals
    approvals = pr.get('review_comments', 0)
    
    write_metrics(developer_name, repository, cycle_time_hours,
                  build_passed, lines_added, lines_removed,
                  files_changed, approvals)
    
    return {
        'statusCode': 200,
        'body': json.dumps({
            'message': 'Metrics recorded',
            'developer': developer_name,
            'repository': repository,
            'pr_number': pr.get('number')
        })
    }


def write_metrics(developer, repository, cycle_time_hours, 
                  build_passed, lines_added, lines_removed,
                  files_changed, approvals):
    """Write standardized metrics to CloudWatch."""
    
    metrics_data = [
        {
            'MetricName': 'PRsMerged',
            'Value': 1,
            'Unit': 'Count',
            'Dimensions': [
                {'Name': 'Developer', 'Value': developer},
                {'Name': 'Repository', 'Value': repository}
            ]
        },
        {
            'MetricName': 'PRCycleTimeHours',
            'Value': cycle_time_hours,
            'Unit': 'None',
            'Dimensions': [
                {'Name': 'Developer', 'Value': developer},
                {'Name': 'Repository', 'Value': repository}
            ]
        },
        {
            'MetricName': 'BuildPassRate',
            'Value': 1.0 if build_passed else 0.0,
            'Unit': 'None',
            'Dimensions': [
                {'Name': 'Developer', 'Value': developer},
                {'Name': 'Status', 'Value': 'passed' if build_passed else 'failed'},
                {'Name': 'Repository', 'Value': repository}
            ]
        },
        {
            'MetricName': 'LinesAddedPerPR',
            'Value': lines_added,
            'Unit': 'Count',
            'Dimensions': [
                {'Name': 'Developer', 'Value': developer},
                {'Name': 'Repository', 'Value': repository}
            ]
        },
        {
            'MetricName': 'LinesRemovedPerPR',
            'Value': lines_removed,
            'Unit': 'Count',
            'Dimensions': [
                {'Name': 'Developer', 'Value': developer},
                {'Name': 'Repository', 'Value': repository}
            ]
        },
        {
            'MetricName': 'FilesChangedPerPR',
            'Value': files_changed,
            'Unit': 'Count',
            'Dimensions': [
                {'Name': 'Developer', 'Value': developer},
                {'Name': 'Repository', 'Value': repository}
            ]
        },
        {
            'MetricName': 'ApprovalsPerPR',
            'Value': approvals,
            'Unit': 'Count',
            'Dimensions': [
                {'Name': 'Developer', 'Value': developer},
                {'Name': 'Repository', 'Value': repository}
            ]
        }
    ]
    
    cw.put_metric_data(
        Namespace='ClaudeCode/DevProductivity',
        MetricData=metrics_data
    )
```

### Step 3: Deploy the Lambda

```bash
FUNCTION_NAME="claude-code-roi-webhook"
REGION="us-west-2"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

# Package the function
zip lambda_function.zip lambda_webhook.py

# Create the function
aws lambda create-function \
  --function-name $FUNCTION_NAME \
  --runtime python3.12 \
  --role arn:aws:iam::${ACCOUNT_ID}:role/claude-code-roi-lambda-role \
  --handler lambda_webhook.lambda_handler \
  --zip-file fileb://lambda_function.zip \
  --region $REGION \
  --timeout 30 \
  --memory-size 256

# Verify
aws lambda get-function --function-name $FUNCTION_NAME --region $REGION \
  --query 'Configuration.FunctionArn' --output text
```

### Step 4: Create API Gateway

```bash
API_NAME="claude-code-roi-api"
REGION="us-west-2"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

# Create REST API
API_ID=$(aws apigateway create-rest-api \
  --name $API_NAME \
  --description "Webhook endpoint for GitLab/GitHub MR/PR events" \
  --region $REGION \
  --query 'id' --output text)

echo "API ID: $API_ID"

# Get root resource ID
ROOT_ID=$(aws apigateway get-resources \
  --rest-api-id $API_ID \
  --region $REGION \
  --query 'items[0].id' --output text)

# Create /webhook resource
RESOURCE_ID=$(aws apigateway create-resource \
  --rest-api-id $API_ID \
  --parent-id $ROOT_ID \
  --path-part webhook \
  --region $REGION \
  --query 'id' --output text)

# Create POST method (no auth — GitLab/GitHub need to call this without AWS creds)
aws apigateway put-method \
  --rest-api-id $API_ID \
  --resource-id $RESOURCE_ID \
  --http-method POST \
  --authorization-type NONE \
  --region $REGION

# Integrate with Lambda (AWS_PROXY passes the full request to Lambda)
aws apigateway put-integration \
  --rest-api-id $API_ID \
  --resource-id $RESOURCE_ID \
  --http-method POST \
  --type AWS_PROXY \
  --integration-http-method POST \
  --uri "arn:aws:apigateway:${REGION}:lambda:path/2015-03-31/functions/arn:aws:lambda:${REGION}:${ACCOUNT_ID}:function:${FUNCTION_NAME}/invocations" \
  --region $REGION

# Grant API Gateway permission to invoke Lambda
aws lambda add-permission \
  --function-name $FUNCTION_NAME \
  --statement-id AllowAPIGatewayInvoke \
  --action lambda:InvokeFunction \
  --principal apigateway.amazonaws.com \
  --source-arn "arn:aws:execute-api:${REGION}:${ACCOUNT_ID}:${API_ID}/*/*" \
  --region $REGION

# Deploy to 'prod' stage
aws apigateway create-deployment \
  --rest-api-id $API_ID \
  --stage-name prod \
  --region $REGION

# Print the webhook URL
WEBHOOK_URL="https://${API_ID}.execute-api.${REGION}.amazonaws.com/prod/webhook"
echo ""
echo "Your webhook URL:"
echo "  $WEBHOOK_URL"
echo ""
echo "Add this URL to your GitLab/GitHub repo webhook settings."
```

### Step 5: Configure GitLab Webhook

1. Go to your GitLab repository → **Settings** → **Webhooks**
2. Click **Add webhook**
3. Fill in:
   - **URL:** `https://<API_ID>.execute-api.us-west-2.amazonaws.com/prod/webhook`
   - **Trigger events:** Check only **Merge request events**
4. Click **Add webhook**
5. Test: merge a PR and check CloudWatch for the `PRsMerged` metric

### Step 6: Configure GitHub Webhook

1. Go to your GitHub repository → **Settings** → **Webhooks**
2. Click **Add webhook**
3. Fill in:
   - **Payload URL:** `https://<API_ID>.execute-api.us-west-2.amazonaws.com/prod/webhook`
   - **Content type:** `application/json`
   - **Secret:** (optional — add a shared secret for payload verification)
   - **Which events?** Select **Let me select individual events** → check only **Pull requests**
4. Click **Add webhook**
5. Test: merge a PR and check CloudWatch for the `PRsMerged` metric

### What Metrics Land in CloudWatch

After a PR is merged, these custom metrics appear in CloudWatch under the `ClaudeCode/DevProductivity` namespace:

| Metric | Dimensions | Example Value |
|---|---|---|
| `PRsMerged` | Developer, Repository | 1 (count) |
| `PRCycleTimeHours` | Developer, Repository | 4.5 (hours from PR open to merge) |
| `BuildPassRate` | Developer, Status, Repository | 1.0 (passed) or 0.0 (failed) |
| `LinesAddedPerPR` | Developer, Repository | 142 |
| `LinesRemovedPerPR` | Developer, Repository | 38 |
| `FilesChangedPerPR` | Developer, Repository | 7 |
| `ApprovalsPerPR` | Developer, Repository | 2 |

### Verify Metrics

```bash
aws cloudwatch get-metric-statistics \
  --namespace ClaudeCode/DevProductivity \
  --metric-name PRsMerged \
  --dimensions Name=Developer,Value=<USERNAME> \
  --start-time $(date -u -v-1H +%Y-%m-%dT%H:%M:%S)Z \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S)Z \
  --period 300 \
  --statistics Sum \
  --region us-west-2
```

Expected output:
```json
{
  "Label": "PRsMerged",
  "Datapoints": [
    {
      "Sum": 1.0,
      "Unit": "Count",
      "Timestamp": "2026-04-28T19:02:00+00:00"
    }
  ]
}
```

---

## 8. MCP Tools for Enterprise

### What MCP Tools Add to the ROI System

MCP (Model Context Protocol) tools extend Claude Code's capabilities beyond the built-in tools (Bash, Read, Write, Edit, Grep). For enterprise deployments, custom MCP tools are a key driver of developer productivity — and OTEL captures every MCP tool invocation.

### Recommended Enterprise MCP Tools

#### 1. Semantic Code Search

Lets Claude search your codebase by meaning, not just text patterns. Critical for large monorepos.

```json
{
  "mcpServers": {
    "semantic_search": {
      "command": "node",
      "args": ["/opt/mcp-tools/semantic-search-server.js"],
      "env": {
        "CODEBASE_INDEX_PATH": "/var/indexes/repo-embeddings",
        "EMBEDDING_MODEL": "amazon.titan-embed-text-v2:0"
      }
    }
  }
}
```

**Tool name in OTEL logs:** `semantic_search`
**Use case:** "Find all functions that handle authentication" — returns semantically relevant results even when code doesn't contain the word "authentication."

#### 2. Automated Code Review

Runs static analysis and security checks on Claude's output before the developer commits.

```json
{
  "mcpServers": {
    "code_review": {
      "command": "python3",
      "args": ["/opt/mcp-tools/code-review-server.py"],
      "env": {
        "RULES_PATH": "/etc/code-review/rules.yaml",
        "SEVERITY_THRESHOLD": "warning"
      }
    }
  }
}
```

**Tool name in OTEL logs:** `code_review`
**Use case:** Every file Claude edits gets automatically checked for security vulnerabilities, style violations, and anti-patterns.

#### 3. CI/CD Pipeline Trigger

Lets Claude trigger builds and tests without the developer switching to the CI/CD UI.

```json
{
  "mcpServers": {
    "cicd_trigger": {
      "command": "node",
      "args": ["/opt/mcp-tools/cicd-trigger-server.js"],
      "env": {
        "CI_API_URL": "https://gitlab.example.com/api/v4",
        "CI_TOKEN_SECRET_ARN": "arn:aws:secretsmanager:us-west-2:ACCT:secret:ci-token"
      }
    }
  }
}
```

**Tool name in OTEL logs:** `cicd_trigger`
**Use case:** "Run the test suite for the auth module" — Claude triggers the pipeline and waits for results.

#### 4. ROI Calculator

An MCP tool that queries CloudWatch metrics and calculates ROI on demand, accessible from within Claude Code sessions.

```json
{
  "mcpServers": {
    "roi_calculator": {
      "command": "python3",
      "args": ["/opt/mcp-tools/roi-calculator-server.py"],
      "env": {
        "CW_NAMESPACE": "ClaudeCode/DevProductivity",
        "AWS_REGION": "us-west-2"
      }
    }
  }
}
```

**Tool name in OTEL logs:** `roi_calculator`
**Use case:** Developer asks Claude "What's my cost per PR this week?" — Claude calls the tool, queries CloudWatch, returns the answer.

### How to Install MCP Tools

Add tool configurations to `~/.claude/mcp.json`:

```json
{
  "mcpServers": {
    "semantic_search": {
      "command": "node",
      "args": ["/opt/mcp-tools/semantic-search-server.js"],
      "env": {
        "CODEBASE_INDEX_PATH": "/var/indexes/repo-embeddings"
      }
    },
    "code_review": {
      "command": "python3",
      "args": ["/opt/mcp-tools/code-review-server.py"]
    },
    "cicd_trigger": {
      "command": "node",
      "args": ["/opt/mcp-tools/cicd-trigger-server.js"]
    },
    "roi_calculator": {
      "command": "python3",
      "args": ["/opt/mcp-tools/roi-calculator-server.py"]
    }
  }
}
```

### What OTEL Captures When Tools Are Used

When Claude calls any tool (built-in or MCP), Pipeline 1 OTEL logs capture:

```
event: tool_use
  tool_name: semantic_search          ← which tool
  duration_ms: 342                    ← how long it took
  session.id: a1b2c3d4-...           ← which session

event: tool_use
  tool_name: code_review
  duration_ms: 1205
  session.id: a1b2c3d4-...
```

In Bedrock invocation logs, MCP tools appear alongside built-in tools in the `tools` array:

```json
"tools": [
  {"name": "Bash", "description": "Execute shell commands"},
  {"name": "Read", "description": "Read file contents"},
  {"name": "semantic_search", "description": "Search codebase semantically"},
  {"name": "code_review", "description": "Run code review checks"}
]
```

And actual MCP tool invocations appear as `tool_use` content blocks in the messages:

```json
{
  "type": "tool_use",
  "name": "semantic_search",
  "input": {"query": "authentication middleware"}
}
```

### Tool Usage as a Maturity Signal

Track the Tool Maturity Index (Section 4.4) across your organization:

```
CloudWatch Logs Insights Query:

fields @timestamp
| filter @message like /tool_use/
| parse @message '"tool_name":"*"' as toolName
| stats count() as calls by toolName
| sort calls desc
```

What to look for:
- **High Bash, low everything else:** Developers are using Claude as a fancy terminal. Train on Read/Write/Edit.
- **High built-in, zero MCP:** Developers haven't configured MCP tools. Roll out the `~/.claude/mcp.json` config.
- **High semantic_search + code_review:** Mature adoption. These developers likely have the best cost-per-PR ratios.

---

## 9. CloudWatch Dashboard

### Dashboard Layout

The recommended dashboard has 4 sections, each containing specific widgets:

```
┌─────────────────────────────────────────────────────────────────┐
│  SECTION 1: COST OVERVIEW                                       │
│  ┌──────────────────────┐  ┌──────────────────────┐            │
│  │ Daily AI Spend       │  │ Cost per Developer   │            │
│  │ (Line chart, 30d)    │  │ (Bar chart, 7d)      │            │
│  └──────────────────────┘  └──────────────────────┘            │
│  ┌──────────────────────┐  ┌──────────────────────┐            │
│  │ Token Breakdown      │  │ Cache Hit Rate       │            │
│  │ (Stacked area)       │  │ (Single value)       │            │
│  └──────────────────────┘  └──────────────────────┘            │
├─────────────────────────────────────────────────────────────────┤
│  SECTION 2: DEVELOPER OUTPUT                                    │
│  ┌──────────────────────┐  ┌──────────────────────┐            │
│  │ PRs Merged / Week    │  │ PR Cycle Time        │            │
│  │ (Bar, per developer) │  │ (Line, avg hours)    │            │
│  └──────────────────────┘  └──────────────────────┘            │
│  ┌──────────────────────┐  ┌──────────────────────┐            │
│  │ Build Pass Rate      │  │ Lines Added/Removed  │            │
│  │ (Gauge, %)           │  │ (Stacked bar)        │            │
│  └──────────────────────┘  └──────────────────────┘            │
├─────────────────────────────────────────────────────────────────┤
│  SECTION 3: ROI METRICS                                         │
│  ┌──────────────────────┐  ┌──────────────────────┐            │
│  │ Cost per PR          │  │ Productivity Score   │            │
│  │ (Single value, $)    │  │ (Leaderboard table)  │            │
│  └──────────────────────┘  └──────────────────────┘            │
│  ┌──────────────────────┐  ┌──────────────────────┐            │
│  │ Code Acceptance Rate │  │ Session Efficiency   │            │
│  │ (Gauge, %)           │  │ (Line chart, trend)  │            │
│  └──────────────────────┘  └──────────────────────┘            │
├─────────────────────────────────────────────────────────────────┤
│  SECTION 4: ADOPTION & TOOLS                                    │
│  ┌──────────────────────┐  ┌──────────────────────┐            │
│  │ Active Sessions/Day  │  │ Tool Usage Breakdown │            │
│  │ (Line chart)         │  │ (Pie chart)          │            │
│  └──────────────────────┘  └──────────────────────┘            │
│  ┌──────────────────────┐  ┌──────────────────────┐            │
│  │ Tool Maturity Index  │  │ Commits per Session  │            │
│  │ (Gauge, per team)    │  │ (Bar chart)          │            │
│  └──────────────────────┘  └──────────────────────┘            │
└─────────────────────────────────────────────────────────────────┘
```

### Creating the Dashboard

Use the AWS CLI to create the dashboard programmatically:

```bash
aws cloudwatch put-dashboard \
  --dashboard-name "Claude-Code-ROI" \
  --dashboard-body file://dashboard.json \
  --region us-west-2
```

Or create it in the AWS Console:
1. **CloudWatch** → **Dashboards** → **Create dashboard**
2. Name: `Claude-Code-ROI`
3. Add widgets per the layout above

### Sample CloudWatch Logs Insights Queries

These queries work against the OTEL log data in CloudWatch Logs.

**Code Acceptance Rate (last 7 days):**

```sql
fields @timestamp, @message
| filter @message like /code_edit/
| parse @message '"accepted":*' as accepted
| stats 
    count(*) as total_edits,
    sum(case when accepted = 'true' then 1 else 0 end) as accepted_edits
| display total_edits, accepted_edits, 
    (accepted_edits * 100.0 / total_edits) as acceptance_rate_pct
```

**Cost per Developer per Day:**

```sql
fields @timestamp, @message
| filter @message like /claude_code.cost/
| parse @message '"session.id":"*"' as sessionId
| parse @message '"asDouble":*' as cost
| stats sum(cost) as daily_cost by bin(1d) as day
| sort day desc
```

**Tool Usage Breakdown:**

```sql
fields @timestamp
| filter @message like /tool_use/
| parse @message '"tool_name":"*"' as toolName
| stats count() as calls by toolName
| sort calls desc
```

**Sessions with Zero Commits (waste identification):**

```sql
fields @timestamp, @message
| filter @message like /claude_code.session_end/
| parse @message '"commits.in_session":*' as commits
| filter commits = 0
| parse @message '"session.id":"*"' as sessionId
| parse @message '"git.repo":"*"' as repo
| display @timestamp, sessionId, repo, commits
| sort @timestamp desc
| limit 20
```

**Average Commits per Session (productivity trend):**

```sql
fields @timestamp, @message
| filter @message like /claude_code.session_end/
| parse @message '"commits.in_session":*' as commits
| stats avg(commits) as avg_commits, count(*) as sessions by bin(1d) as day
| sort day desc
```

### Customizing for Your Organization

**By team:** Add a `team` dimension to Pipeline 3 metrics. Modify the Lambda to parse the team from the repository path (e.g., `org/team-name/repo`).

**By project:** The `Repository` dimension already segments by project. Create per-repository dashboard widgets.

**By time zone:** CloudWatch stores timestamps in UTC. Use the dashboard time range selector to view in local time.

**Custom alerts:**

```bash
# Alert when daily spend exceeds $100
aws cloudwatch put-metric-alarm \
  --alarm-name "Claude-Code-Daily-Spend-High" \
  --namespace "ClaudeCode/OTEL" \
  --metric-name "claude_code.cost.usage" \
  --statistic Sum \
  --period 86400 \
  --evaluation-periods 1 \
  --threshold 100 \
  --comparison-operator GreaterThanThreshold \
  --alarm-actions arn:aws:sns:us-west-2:ACCT:ops-alerts \
  --region us-west-2
```

---

## 10. Session ↔ MR Linkage

### The Problem

Pipeline 1 knows the cost of session `a1b2c3d4`. Pipeline 3 knows that MR #42 was merged. But how do you connect the session to the MR?

### The Solution: Commit Hash as Join Key

When a developer uses Claude Code to write code, they make commits. Those commits have unique hashes. The same commits appear in the MR when it's merged.

```
Pipeline 2 (stop.sh):
  session_id: a1b2c3d4
  git commits in last 2 hours:
    - abc1234 "add auth middleware"
    - def5678 "fix token validation"

Pipeline 3 (webhook):
  MR #42 merged
  commits in MR:
    - abc1234 "add auth middleware"
    - def5678 "fix token validation"
    - ghi9012 "update tests"

JOIN: commits abc1234 and def5678 appear in both
→ Session a1b2c3d4 contributed to MR #42
→ Session cost ($0.42) can be attributed to MR #42
```

### How It Works End-to-End

**1. stop.sh captures recent commit hashes:**

To capture actual commit hashes (not just count), enhance stop.sh:

```bash
#!/bin/bash
SESSION_ID=$(cat /dev/stdin | jq -r '.session_id')
REPO=$(git remote get-url origin 2>/dev/null || echo "unknown")
BRANCH=$(git branch --show-current 2>/dev/null || echo "unknown")
COMMITS=$(git log --since="2 hours ago" --oneline | wc -l | tr -d ' ')
COMMIT_HASHES=$(git log --since="2 hours ago" --format="%H" | tr '\n' ',' | sed 's/,$//')

curl -s -X POST http://localhost:4318/v1/logs \
  -H "Content-Type: application/json" \
  -d '{
    "resourceLogs": [{"scopeLogs": [{"logRecords": [{
      "body": {"stringValue": "claude_code.session_end"},
      "attributes": [
        {"key":"session.id","value":{"stringValue":"'$SESSION_ID'"}},
        {"key":"git.repo","value":{"stringValue":"'$REPO'"}},
        {"key":"git.branch","value":{"stringValue":"'$BRANCH'"}},
        {"key":"commits.in_session","value":{"intValue":'$COMMITS'}},
        {"key":"commit.hashes","value":{"stringValue":"'$COMMIT_HASHES'"}}
      ]
    }]}]}]
  }' &>/dev/null &
```

**2. Lambda logs commit hashes from the MR webhook:**

The webhook payload from GitLab includes a `commits` array. The Lambda logs these to CloudWatch:

```python
# In the Lambda function (already included in the handler above)
commits = [c.get('id', '') for c in body.get('commits', [])]
print(json.dumps({
    'event': 'mr_merged',
    'developer': developer_name,
    'repository': repository,
    'mr_id': mr_id,
    'commits': commits
}))
```

**3. CloudWatch Logs Insights join query:**

```sql
-- Find sessions that contributed to a specific MR
-- Step 1: Get commit hashes from the MR
fields @timestamp, @message
| filter @message like /mr_merged/
| filter @message like /mr_id.*42/
| parse @message '"commits":*' as commit_list

-- Step 2: In a separate query, match commit hashes to sessions
fields @timestamp, @message
| filter @message like /claude_code.session_end/
| parse @message '"commit.hashes":"*"' as hashes
| parse @message '"session.id":"*"' as sessionId
| filter hashes like /abc1234/
| display @timestamp, sessionId, hashes
```

**4. Full attribution chain:**

```
Session a1b2c3d4 → cost $0.42, 2 commits (abc1234, def5678)
  ↓ (commit hashes match)
MR #42 → merged, cycle time 4.5 hrs, build passed
  ↓ (attributed)
ROI: $0.42 spent → 1 merged PR with green build in 4.5 hours
Cost per PR for this session: $0.42
```

---

## 11. Enterprise Deployment Guide

### Deployment Architecture

```
┌───────────────────────────────────────────────────┐
│               CENTRAL INFRASTRUCTURE              │
│               (One-time admin setup)               │
│                                                   │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────┐│
│  │ API Gateway  │  │ Lambda       │  │ CloudWatch││
│  │ /webhook     │  │ (webhook     │  │ Dashboard ││
│  │              │  │  handler)    │  │           ││
│  └──────┬──────┘  └──────┬───────┘  └──────┬────┘│
│         │                │                  │     │
└─────────┼────────────────┼──────────────────┼─────┘
          │                │                  │
          │                ▼                  │
          │     CloudWatch Metrics            │
          │     ClaudeCode/DevProductivity    │
          │                │                  │
          │                └──────────────────┘
          │
          ▼
┌─────────────────────┐
│  GitLab/GitHub      │
│  Org-wide webhook   │
│  (one config)       │
└─────────────────────┘

┌───────────────────────────────────────────────────┐
│           PER-DEVELOPER SETUP                      │
│           (Each developer machine)                 │
│                                                   │
│  ┌─────────────────────────────────────────────┐  │
│  │ 1. OTEL env vars (5 exports in .bashrc)     │  │
│  │ 2. stop.sh hook (copy to ~/.claude/hooks/)  │  │
│  │ 3. MCP tools (copy ~/.claude/mcp.json)      │  │
│  │ 4. CloudWatch Agent (optional, for prod)    │  │
│  └─────────────────────────────────────────────┘  │
└───────────────────────────────────────────────────┘
```

### Phase 1: Central Infrastructure (Admin, ~30 minutes)

Run these steps once. They create the shared backend that all developers' data flows into.

```bash
# 1. Create IAM Role for Lambda
# (See Section 7, Step 1)

# 2. Deploy Lambda Function
# (See Section 7, Step 2-3)

# 3. Create API Gateway
# (See Section 7, Step 4)

# 4. Configure org-wide webhook on GitLab/GitHub
# (See Section 7, Step 5-6)
# TIP: For GitLab, set the webhook at the GROUP level, not per-repo.
# This means every repo in the group automatically sends MR events.
# GitLab: Group → Settings → Webhooks
# GitHub: Organization → Settings → Webhooks

# 5. Create CloudWatch Dashboard
# (See Section 9)
```

### Phase 2: Per-Developer Setup (~5 minutes per developer)

Distribute this as a setup script or include in your developer onboarding.

**Option A: Manual setup**

```bash
# Step 1: Add OTEL env vars to shell profile
cat >> ~/.bashrc << 'EOF'
# Claude Code OTEL — enterprise ROI tracking
export CLAUDE_CODE_ENABLE_TELEMETRY=1
export OTEL_METRICS_EXPORTER=otlp
export OTEL_LOGS_EXPORTER=otlp
export OTEL_EXPORTER_OTLP_PROTOCOL=http/json
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
EOF

# For zsh users:
# cat >> ~/.zshrc << 'EOF' ... same content ... EOF

# Step 2: Install stop.sh hook
mkdir -p ~/.claude/hooks
curl -sL https://your-internal-url/stop.sh -o ~/.claude/hooks/stop.sh
chmod +x ~/.claude/hooks/stop.sh

# Step 3: Install MCP tools config
curl -sL https://your-internal-url/mcp.json -o ~/.claude/mcp.json

# Step 4: Reload shell
source ~/.bashrc
```

**Option B: Automated setup script**

```bash
#!/bin/bash
# enterprise-claude-setup.sh — run once per developer machine

set -e

echo "Setting up Claude Code Enterprise ROI Tracking..."

# OTEL env vars
if ! grep -q "CLAUDE_CODE_ENABLE_TELEMETRY" ~/.bashrc 2>/dev/null; then
  cat >> ~/.bashrc << 'ENVVARS'
# Claude Code OTEL — enterprise ROI tracking
export CLAUDE_CODE_ENABLE_TELEMETRY=1
export OTEL_METRICS_EXPORTER=otlp
export OTEL_LOGS_EXPORTER=otlp
export OTEL_EXPORTER_OTLP_PROTOCOL=http/json
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
ENVVARS
  echo "✅ OTEL env vars added to ~/.bashrc"
else
  echo "⏭️  OTEL env vars already present"
fi

# stop.sh hook
mkdir -p ~/.claude/hooks
cat > ~/.claude/hooks/stop.sh << 'HOOK'
#!/bin/bash
SESSION_ID=$(cat /dev/stdin | jq -r '.session_id')
REPO=$(git remote get-url origin 2>/dev/null || echo "unknown")
BRANCH=$(git branch --show-current 2>/dev/null || echo "unknown")
COMMITS=$(git log --since="2 hours ago" --oneline | wc -l | tr -d ' ')
COMMIT_HASHES=$(git log --since="2 hours ago" --format="%H" | tr '\n' ',' | sed 's/,$//')
curl -s -X POST ${OTEL_EXPORTER_OTLP_ENDPOINT:-http://localhost:4318}/v1/logs \
  -H "Content-Type: application/json" \
  -d '{"resourceLogs":[{"scopeLogs":[{"logRecords":[{"body":{"stringValue":"claude_code.session_end"},"attributes":[{"key":"session.id","value":{"stringValue":"'$SESSION_ID'"}},{"key":"git.repo","value":{"stringValue":"'$REPO'"}},{"key":"git.branch","value":{"stringValue":"'$BRANCH'"}},{"key":"commits.in_session","value":{"intValue":'$COMMITS'}},{"key":"commit.hashes","value":{"stringValue":"'$COMMIT_HASHES'"}}]}]}]}]}' &>/dev/null &
HOOK
chmod +x ~/.claude/hooks/stop.sh
echo "✅ stop.sh hook installed"

# MCP tools (customize this URL for your org)
if [ ! -f ~/.claude/mcp.json ]; then
  cat > ~/.claude/mcp.json << 'MCP'
{
  "mcpServers": {}
}
MCP
  echo "✅ MCP config created (add your tools)"
else
  echo "⏭️  MCP config already exists"
fi

echo ""
echo "Done! Run 'source ~/.bashrc' then start 'claude'."
echo "Metrics will flow to your OTEL receiver."
```

### Phase 3: CloudWatch Agent (Optional — Production OTEL)

For production deployments where you want OTEL data to go directly to CloudWatch (not the local Python receiver), install the CloudWatch Agent on each developer machine.

See Section 5 ("CloudWatch Agent for Production") for full instructions.

**When to use the local receiver vs. CloudWatch Agent:**

| Scenario | Use This |
|---|---|
| Development, testing, demos | Local Python receiver (`otel_receiver.py`) |
| Small team (< 10 developers), low-fidelity tracking | Local receiver writing to JSON file |
| Production (10+ developers), dashboards, alerts | CloudWatch Agent |
| Enterprise (100+ developers), org-wide reporting | CloudWatch Agent |

### Scaling Considerations

**For 10-50 developers:**
- Single Lambda function handles all webhooks
- CloudWatch Logs Insights queries run in seconds
- No additional infrastructure needed

**For 50-200 developers:**
- Consider Lambda concurrency limits (default 1000, no issue)
- CloudWatch Logs Insights may take 5-10 seconds for week-long queries
- Add CloudWatch Log retention policy (90 days recommended)
- Consider using CloudWatch Metric Math for cross-developer aggregations

**For 200-1000+ developers:**
- API Gateway throttling: increase rate limit if needed (default 10,000 req/s)
- Lambda: no changes needed — it's stateless and scales automatically
- CloudWatch custom metrics: watch for dimension cardinality (Developer × Repository combinations). If you have 500 developers × 100 repos, that's 50,000 unique dimension combinations. CloudWatch handles this fine but costs increase. Consider aggregating to team-level dimensions.
- OTEL data volume: each developer produces ~50-100 KB/day of OTEL data. At 1000 developers, that's ~100 MB/day into CloudWatch Logs. At $0.50/GB ingestion, that's ~$1.50/month. Negligible.
- Consider creating separate dashboards per team/org and a single executive rollup dashboard

**CloudWatch costs at scale:**

| Component | Unit Cost | At 100 devs | At 1000 devs |
|---|---|---|---|
| Custom metrics (Pipeline 3) | $0.30/metric/month | ~$21/month (7 metrics × 100 dimensions) | ~$210/month |
| Log ingestion (Pipeline 1+2) | $0.50/GB | ~$3/month | ~$30/month |
| Log storage (3 months) | $0.03/GB | ~$1/month | ~$10/month |
| Logs Insights queries | $0.005/GB scanned | ~$5/month | ~$50/month |
| Dashboard | Free (first 3) | $0 | $3/month (per additional) |
| **Total** | | **~$30/month** | **~$303/month** |

The cost of the ROI system itself is negligible compared to the Claude Code spend it measures. At 100 developers spending $20/day each on AI, your monthly AI spend is ~$40,000. The measurement system costs $30/month — that's 0.075%.

---

## 12. Sample Output

### Scenario

A platform engineering team of 8 developers has been using Claude Code on Bedrock for 1 week. All three pipelines are active. Here is what the dashboard shows.

### Per-Developer Scorecard

| Developer | PRs Merged | Avg Cycle Time (hrs) | Lines Added | Lines Removed | Build Pass Rate | Tools Used | Code Accept Rate | Weekly AI Cost | Cost/PR | Score (0–100) |
|---|---|---|---|---|---|---|---|---|---|---|
| alice.chen | 9 | 3.2 | 1,247 | 389 | 89% | 8/10 | 78% | $24.50 | $2.72 | 89 |
| bob.kumar | 7 | 4.8 | 892 | 156 | 100% | 6/10 | 71% | $19.20 | $2.74 | 90 |
| carol.zhang | 12 | 2.1 | 2,104 | 612 | 83% | 9/10 | 82% | $31.80 | $2.65 | 88 |
| dave.smith | 4 | 8.5 | 445 | 78 | 75% | 3/10 | 54% | $22.10 | $5.53 | 62 |
| eva.jones | 8 | 3.7 | 1,056 | 298 | 88% | 7/10 | 75% | $21.40 | $2.68 | 87 |
| frank.lee | 6 | 5.2 | 734 | 201 | 83% | 5/10 | 68% | $17.80 | $2.97 | 84 |
| grace.patel | 10 | 2.8 | 1,543 | 445 | 90% | 8/10 | 80% | $28.90 | $2.89 | 89 |
| henry.wu | 5 | 6.1 | 623 | 134 | 80% | 4/10 | 62% | $16.30 | $3.26 | 74 |

### Team-Level Aggregates

| Metric | Value |
|---|---|
| **Total PRs Merged** | 61 |
| **Average PR Cycle Time** | 4.1 hours |
| **Total Lines Added** | 8,644 |
| **Total Lines Removed** | 2,313 |
| **Team Build Pass Rate** | 86% |
| **Average Code Acceptance Rate** | 71% |
| **Total Weekly AI Cost** | $182.00 |
| **Average Cost per PR** | $2.98 |
| **Team Productivity Score (avg)** | 83 / 100 |
| **Average Tool Maturity Index** | 62.5% |
| **Total Sessions** | 312 |
| **Sessions with Zero Commits** | 47 (15%) |

### Insights the Data Reveals

**1. Tool usage correlates with build quality.**
Developers who used 7+ tools (alice, carol, grace, eva) had an average build pass rate of 88%. Developers who used fewer than 5 tools (dave, henry) averaged 78%. Specifically, developers who used the `code_review` MCP tool had a 23% higher build pass rate than those who didn't.

**2. Cost per PR is remarkably consistent across productive developers.**
Alice ($2.72), Bob ($2.74), Carol ($2.65), Eva ($2.68), Grace ($2.89) — all within a $0.24 range. This suggests a natural "floor" for cost per PR with Claude Code, regardless of individual coding style.

**3. Dave is spending but not shipping.**
Dave's weekly cost ($22.10) is in the mid-range, but his PR output (4) is half the team average. His code acceptance rate (54%) is the lowest — he's rejecting nearly half of Claude's suggestions. This signals either a prompt quality issue, a mismatch between tasks and AI capabilities, or insufficient tool adoption (3/10 tools). **Action:** Pair Dave with Carol (highest output) for a prompt engineering session.

**4. 15% of sessions produce zero commits.**
47 out of 312 sessions ended without any git commits. These sessions represent ~$27 in AI spend that produced no tangible output. Some zero-commit sessions are valid (research, code review, reading), but if the rate exceeds 20%, investigate. **Action:** Track zero-commit rate by developer and flag when it exceeds 25%.

**5. Prompt caching is saving 30-40% on token costs.**
Across the team, cache read tokens represent 35% of total input tokens. At the standard 90% discount for cache reads, this saves approximately $22/week in token costs. Developers with longer sessions (more conversational turns) benefit most from caching.

**6. Carol is the highest producer but also the highest spender.**
Carol merged 12 PRs at $31.80 in AI costs — the highest on both metrics. Her cost per PR ($2.65) is actually the lowest, meaning she's the most efficient converter of AI spend into shipped code. She should be studied, not cost-capped.

### Executive Summary from This Data

> In one week, a team of 8 developers spent $182 on Claude Code and merged 61 pull requests — an average cost of $2.98 per merged PR. At an average developer cost of $75/hour and 2 hours per manual PR, the estimated manual cost would have been $9,150. The AI-assisted cost was $182 + developer review time (~30 min/PR × $75/hr ÷ 2 = $37.50 × 61 = $2,288). Total AI-assisted cost: $2,470. **Estimated savings: $6,680/week (73% reduction in PR delivery cost).** The build pass rate for AI-assisted code (86%) is within 2 points of the team's historical manual rate (84%), indicating no quality degradation.

---

## Appendix A: Quick Reference

### All Environment Variables

```bash
export CLAUDE_CODE_ENABLE_TELEMETRY=1
export OTEL_METRICS_EXPORTER=otlp
export OTEL_LOGS_EXPORTER=otlp
export OTEL_EXPORTER_OTLP_PROTOCOL=http/json
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318   # dev: 4318, prod CW Agent: 4316
```

### All Files to Deploy

| File | Location | Purpose |
|---|---|---|
| `stop.sh` | `~/.claude/hooks/stop.sh` | Pipeline 2 — git attribution |
| `mcp.json` | `~/.claude/mcp.json` | MCP tool configuration |
| `otel_receiver.py` | Any directory (dev only) | Local OTEL receiver for testing |
| `lambda_webhook.py` | AWS Lambda | Pipeline 3 — PR webhook handler |

### CloudWatch Namespace Reference

| Namespace | Source | Metrics |
|---|---|---|
| Auto-generated (OTEL) | Pipeline 1 via CloudWatch Agent | `claude_code.cost.usage`, `claude_code.tokens.*`, `claude_code.lines_*`, `claude_code.session.count` |
| `ClaudeCode/DevProductivity` | Pipeline 3 via Lambda | `PRsMerged`, `PRCycleTimeHours`, `BuildPassRate`, `LinesAddedPerPR`, `LinesRemovedPerPR`, `FilesChangedPerPR`, `ApprovalsPerPR` |

### Dimension Reference (Pipeline 3)

| Dimension | Used On | Values |
|---|---|---|
| `Developer` | All metrics | Git username (e.g., `alice.chen`) |
| `Repository` | All metrics | Full repo path (e.g., `acme/backend`) |
| `Status` | `BuildPassRate` | `passed` or `failed` |

---

## Appendix B: Troubleshooting

### Pipeline 1 Issues

| Problem | Likely Cause | Fix |
|---|---|---|
| No metrics appear | `CLAUDE_CODE_ENABLE_TELEMETRY` not set | Check `env | grep CLAUDE` |
| Binary/protobuf data (unreadable) | `OTEL_EXPORTER_OTLP_PROTOCOL` not set to `http/json` | Add the env var |
| Metrics appear in receiver but not CloudWatch | Not using CloudWatch Agent | Switch endpoint to `localhost:4316` and start CW Agent |
| `connection refused` on 4318 | Receiver not running | Start `otel_receiver.py` or CloudWatch Agent |

### Pipeline 2 Issues

| Problem | Likely Cause | Fix |
|---|---|---|
| No git data in OTEL logs | Hook doesn't exist or isn't executable | `ls -la ~/.claude/hooks/stop.sh` — check permissions |
| `git.repo` shows `unknown` | Not in a git repo | `cd` into a git repo before launching `claude` |
| `commits.in_session` always 0 | No commits in last 2 hours | Make a commit, or increase the time window in stop.sh |
| Hook doesn't fire | Claude Code version issue | Verify hooks are supported in your version: `claude --version` |

### Pipeline 3 Issues

| Problem | Likely Cause | Fix |
|---|---|---|
| No metrics after MR merge | Webhook not configured | Check GitLab/GitHub webhook settings |
| Lambda returns 400 | MR not in `merged` state | The Lambda only processes merged MRs — check the webhook trigger events |
| Lambda returns 500 | IAM permission issue | Check Lambda execution role has `cloudwatch:PutMetricData` |
| Webhook delivery failed | API Gateway URL wrong | Test with `curl -X POST <url> -H "Content-Type: application/json" -d '{}'` |

---

*Document version: 1.0*
*Last updated: April 2026*
*System components: Claude Code OTEL, Git Hooks, Lambda Webhook, CloudWatch*
