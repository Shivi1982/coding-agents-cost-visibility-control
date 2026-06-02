# 🚀 Claude Code on Bedrock — Cost Visibility, Cost Control & Developer Productivity

**Enterprise-grade observability, cost enforcement, and ROI tracking for Claude Code running on Amazon Bedrock.**



![AWS](https://img.shields.io/badge/AWS-Bedrock-orange?logo=amazon-aws)

![Python](https://img.shields.io/badge/Python-3.9+-blue?logo=python)

![OpenTelemetry](https://img.shields.io/badge/OTEL-Supported-blueviolet?logo=opentelemetry)

![License](https://img.shields.io/badge/License-MIT-green)

---

## 📋 Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Features](#features)
- [Quick Start](#quick-start)
- [Project Structure](#project-structure)
- [Pipelines](#pipelines)
- [Deployment](#deployment)
- [Configuration](#configuration)
- [MCP Tools](#mcp-tools)
- [Cost Control Methods](#cost-control-methods)
- [Contributing](#contributing)
- [License](#license)

---

## Overview

This project provides a **complete solution** for enterprises adopting Claude Code on Amazon Bedrock who need to:

1. **See** who is spending what (Cost Visibility)
2. **Stop** runaway spend in near real-time (Cost Control)
3. **Measure** the Development productivity using metrics (Developer Productivity)

It uses **3 data pipelines** to capture cost, code output, and delivery quality — then unifies them in CloudWatch dashboards for per-developer ROI tracking.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        DEVELOPER WORKSTATION                            │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  Claude Code ──► OTEL Telemetry ──► OTEL Receiver (local or CW Agent)  │
│       │                                      │                          │
│       │         ┌────────────────┐           │                          │
│       └────────►│ Git Hook       │           │   Pipeline 1: Cost +     │
│    /exit fires  │ (stop.sh)      │           │   Token + Code Metrics   │
│                 └───────┬────────┘           │                          │
│                         │                    ▼                          │
│                         │           ┌─────────────────┐                │
│                         └──────────►│ CloudWatch      │                │
│                                     │ Metrics + Logs  │                │
│                  Pipeline 2:        └────────┬────────┘                │
│                  Git Attribution              │                          │
└──────────────────────────────────────────────┼──────────────────────────┘
                                               │
┌──────────────────────────────────────────────┼──────────────────────────┐
│                          AWS CLOUD           │                           │
├──────────────────────────────────────────────┼──────────────────────────┤
│                                              │                           │
│  GitLab/GitHub ──► Webhook ──► API GW ──► Lambda ──► CloudWatch         │
│  (PR Merged)                                                            │
│                    Pipeline 3: PR + Build Quality                        │
│                                                                         │
│  ┌─────────────────┐    ┌──────────────────┐    ┌───────────────────┐  │
│  │ Cost Anomaly    │    │ ROI Dashboard    │    │ SNS Alerts        │  │
│  │ Lambda (6h)     │───►│ (CW Dashboard)   │    │ (Anomalies)       │  │
│  └─────────────────┘    └──────────────────┘    └───────────────────┘  │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘

```

---

## Features

### 🔍 Cost Visibility

- **Per-developer cost tracking** via IAM Principal Attribution + CUR 2.0
- **Real-time token & cost metrics** via native Claude Code OTEL telemetry
- **4 tracking options**: IAM + CUR, Invocation Logs + Athena, Inference Profiles, CW Logs Insights

### 🛡️ Cost Control

- **Near real-time enforcement** — CW Metric Filter → Alarm → Lambda → IAM Deny
- **Per-user budget limits** with automatic nightly reset
- **Anomaly detection** — z-score based cost spike alerts (warning + critical)
- **Session tags** for shared-role environments (STS AssumeRole with userId)

### 📊 Developer Productivity

- **3-pipeline data fusion**: OTEL (cost/tokens) + Git hooks (commits) + Webhooks (PRs/builds)
- **9 CloudWatch metrics per PR**: cycle time, lines changed, approvals, build pass rate
- **Code acceptance rate tracking** — accepted vs. rejected Claude suggestions
- **Per-developer ROI calculation**: value delivered ÷ AI cost spent

### 🔧 MCP Tools (Bonus)

- Cost calculator MCP server
- CI/CD trigger MCP server
- Code review MCP server
- Semantic search MCP server

---

## Quick Start

### Prerequisites

- Python 3.9+
- AWS CLI configured with appropriate permissions
- Claude Code installed and configured for Amazon Bedrock
- AWS Account with Bedrock access enabled

### 1. Clone the Repository

```bash
git clone git@ssh.gitlab.aws.dev:shivibha/coding-agents-cost-visibility-control.git
cd coding-agents-cost-visibility-control

```

### 2. Install Dependencies

```bash
pip install -r requirements.txt

```

### 3. Start the OTEL Receiver (Terminal 1)

```bash
python otel/otel_receiver.py

```

### 4. Configure Claude Code (Terminal 2)

```bash
export CLAUDE_CODE_ENABLE_TELEMETRY=1
export OTEL_METRICS_EXPORTER=otlp
export OTEL_LOGS_EXPORTER=otlp
export OTEL_EXPORTER_OTLP_PROTOCOL=http/json
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
claude

```

### 5. Use Claude Code, then type `/exit` to flush metrics

That's it — you'll see cost, token, and code metrics in Terminal 1.

---

## Project Structure

```
.
├── README.md                   # This file
├── requirements.txt            # Python dependencies
├── .gitignore                  # Git ignore rules
│
├── otel/                       # OpenTelemetry receivers
│   ├── otel_receiver.py        # v1 — Simple terminal output (demos)
│   ├── otel_receiver_v2.py     # v2 — Enterprise: per-session, CloudWatch push, JSONL
│   └── developers.json         # Hash → developer name mapping
│
├── lambda/                     # AWS Lambda functions
│   ├── gitlab_webhook_v2.py    # GitLab MR webhook → 9 CW metrics + structured logs
│   ├── github_webhook.py       # GitHub PR webhook → CW metrics
│   ├── cost_anomaly.py         # Scheduled anomaly detection (z-score, WoW)
│   └── test_anomaly.py         # Unit tests for anomaly detection
│
├── dashboard/                  # CloudWatch dashboard configs
│   ├── create_dashboard.sh     # Deploy the ROI dashboard
│   ├── delete_dashboard.sh     # Teardown
│   ├── seed_demo_data.py       # Seed fake data for demos
│   └── roi_dashboard.html      # Standalone HTML dashboard
│
├── mcp-tools/                  # MCP server implementations
│   ├── calculator_mcp.py       # Cost calculation tool
│   ├── cicd_trigger_mcp.py     # CI/CD pipeline trigger tool
│   ├── code_review_mcp.py      # Automated code review tool
│   ├── semantic_search_mcp.py  # Semantic code search tool
│   └── mcp.json                # MCP configuration
│
├── scripts/                    # Deployment & setup automation
│   ├── deploy_all.sh           # One-command full deployment
│   ├── deploy_lambda.sh        # Lambda-only deployment
│   ├── create_api_gateway.sh   # API Gateway setup
│   └── add_webhook.sh          # GitLab/GitHub webhook config
│
└── docs/                       # Documentation & guides
    ├── cost_explainer.md        # CUR, IAM, roles — concepts explained
    ├── enterprise_roi_guide.md  # Enterprise ROI measurement guide
    ├── gitlab_setup.md          # GitLab integration setup
    ├── github_webhook_setup.md  # GitHub integration setup
    └── aws_profiles_guide.md    # AWS credential profiles reference

```

---

## Pipelines

### Pipeline 1 — Native OTEL Telemetry (Zero Code Changes)

| Metric | What it captures |
| --- | --- |
| `claude_code.cost.usage` | Dollars spent per API call |
| `claude_code.tokens.input/output` | Token consumption |
| `claude_code.lines_added/removed` | Code volume (best ROI signal) |
| `claude_code.session.count` | Sessions opened |
| `claude_code.active_time.total` | Active typing time |

**Data destinations:**

- **Local** (demo): `otel/otel_receiver.py` → terminal
- **Enterprise**: CloudWatch Agent (OTLP) → CloudWatch Metrics/Logs

### Pipeline 2 — Git Attribution (Commits per Session)

Links Claude Code sessions to git commits via a stop hook:

```
Claude Code /exit → stop.sh hook → reads git state → OTEL log event

```

Result: **cost per commit** = session cost ÷ commits in session

### Pipeline 3 — PR + Build Quality (Webhook)

```
PR Merged on GitLab/GitHub → Webhook → API Gateway → Lambda → CloudWatch

```

**9 metrics captured per merged PR:**

1. PRsMerged
2. PRCycleTimeHours
3. LinesAddedPerPR
4. LinesRemovedPerPR
5. FilesChangedPerPR
6. CommitsPerPR
7. ApprovalsPerPR
8. ReviewCommentsPerPR
9. BuildPassRate

---

## Deployment

### Full Stack (Recommended)

```bash
cd scripts/
chmod +x deploy_all.sh
./deploy_all.sh

```

This deploys: Lambda functions, API Gateway, CloudWatch Dashboard, SNS topic.

### Lambda Only

```bash
cd scripts/
./deploy_lambda.sh

```

### Add Webhook to GitLab/GitHub

```bash
cd scripts/
./add_webhook.sh <YOUR_API_GATEWAY_URL>

```

---

## Configuration

### Environment Variables

| Variable | Default | Description |
| --- | --- | --- |
| `CLAUDE_CODE_ENABLE_TELEMETRY` | `0` | Enable OTEL export from Claude Code |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | — | OTLP receiver URL (localhost:4318 or CW Agent) |
| `OTEL_EXPORTER_OTLP_PROTOCOL` | — | Must be `http/json` for human-readable output |
| `PUSH_TO_CLOUDWATCH` | `0` | Enable CW push from otel_receiver_v2 |
| `CW_REGION` | `us-west-2` | AWS region for CloudWatch |
| `SNS_TOPIC_ARN` | Auto-constructed | SNS topic for anomaly alerts |

### Anomaly Detection Thresholds

| Threshold | Default | Description |
| --- | --- | --- |
| `COST_ZSCORE_WARNING` | 2.0 | z-score triggering warning |
| `COST_ZSCORE_CRITICAL` | 3.0 | z-score triggering critical alert |
| `ACCEPTANCE_DROP_THRESHOLD` | 0.15 | 15% week-over-week build pass rate decline |
| `SESSION_SPIKE_FACTOR` | 3.0 | 3× change in session count |

> 💡 The anomaly detection schedule (default: every 6 hours) is fully customizable via CloudWatch Events cron expression — adjust to 1h, 12h, or daily based on your team's needs.

---

## Cost Control Methods

| Method | Granularity | Latency | Best For |
| --- | --- | --- | --- |
| **IAM Principal + CUR 2.0** | Per-developer (daily) | 24h | Visibility — always enable first |
| **Invocation Logs + Athena** | Per-request, per-token | ~minutes | Deep analytics + enforcement |
| **Inference Profiles + Cost Explorer** | Per-team | ~8-12h | Finance dashboards |
| **CW Logs Insights** | Per-request | Near real-time | Real-time alarms → enforcement |

### Enforcement (Near Real-Time)

```
CW Metric Filter → CW Alarm → Lambda → IAM Deny Policy

```

- Blocks developer when daily spend threshold is hit
- Automatic nightly reset via EventBridge
- Admin manual override: `aws lambda invoke --function-name bedrock-reset --payload '{"user":"alice"}' response.json`

---

## MCP Tools

The `mcp-tools/` directory contains Model Context Protocol server implementations that extend Claude Code capabilities:

| Tool | Purpose |
| --- | --- |
| `calculator_mcp.py` | Real-time cost calculation for Bedrock API calls |
| `cicd_trigger_mcp.py` | Trigger CI/CD pipelines from within Claude Code |
| `code_review_mcp.py` | Automated code review with configurable rules |
| `semantic_search_mcp.py` | Semantic code search across repositories |

**Configuration:** See `mcp-tools/mcp.json` for Claude Code MCP settings.

---

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/your-feature`)
3. Commit your changes (`git commit -m 'Add some feature'`)
4. Push to the branch (`git push origin feature/your-feature`)
5. Open a Merge Request

---

## License

This project is licensed under the MIT License — see [LICENSE](LICENSE) for details.

---

## Author

**Shivi Bhatia** — Sr. Specialist SA, GenAI/ML @ AWSBuilt for enterprise customers adopting Claude Code & Other developer coding tools on Amazon Bedrock.

**Harsha Tadiparthi** - Pr. Gen AI/ML SA, Enterprises . Assisting Enterprises & Semiconductors building on Bedrock on cost visibility and controls. 

---

> 💡 **Tip:** Start with Pipeline 1 (5 env vars, zero code changes) to see immediate cost visibility. Then layer on Pipelines 2 and 3 for full ROI measurement.

