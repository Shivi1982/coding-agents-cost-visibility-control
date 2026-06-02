#!/bin/bash

###############################################################################
# create_dashboard.sh — CloudWatch Dashboard for Claude Code Enterprise ROI
#
# Creates: "ClaudeCode-ROI-Dashboard" in us-west-2
# Account: YOUR_ACCOUNT_ID
#
# This dashboard provides ENTERPRISE-LEVEL visibility across ALL developers.
# No developer names are hardcoded — uses CloudWatch SEARCH expressions to
# auto-discover every developer who has ever published metrics.
#
# Metric Namespaces:
#   ClaudeCode/DevProductivity  — PRs, cycle time, build rate, lines, cost
#   ClaudeCode/ToolUsage        — tool invocations, diversity
#
# Dimensions:
#   Developer  — auto-discovered via SEARCH
#   Repository — auto-discovered via SEARCH
#
# Prerequisites:
#   - AWS CLI configured with access to account YOUR_ACCOUNT_ID
#   - Lambda webhook (lambda_gitlab_webhook_v2.py) deployed and emitting metrics
#   - Region: us-west-2
#
# Usage:
#   chmod +x create_dashboard.sh
#   ./create_dashboard.sh
#
# Cleanup:
#   ./delete_dashboard.sh
###############################################################################

set -euo pipefail

DASHBOARD_NAME="ClaudeCode-ROI-Dashboard"
REGION="us-west-2"

echo "============================================================"
echo "  CloudWatch Dashboard: Claude Code Enterprise ROI"
echo "============================================================"
echo ""
echo "  Dashboard : $DASHBOARD_NAME"
echo "  Region    : $REGION"
echo "  Account   : YOUR_ACCOUNT_ID"
echo ""
echo "  Creating dashboard with 6 sections:"
echo "    Row 1 — Overview KPIs (4 single-value widgets)"
echo "    Row 2 — Per-Developer Productivity (2 charts)"
echo "    Row 3 — Code Volume & Quality (2 charts)"
echo "    Row 4 — Tool Usage (2 charts)"
echo "    Row 5 — Cost Efficiency (2 charts)"
echo "    Row 6 — Logs Insights Query Reference"
echo ""
echo "------------------------------------------------------------"

# ---------------------------------------------------------------------------
# Build the dashboard JSON body
#
# DESIGN PRINCIPLES:
#   1. SEARCH expressions auto-discover ALL developers (no hardcoding)
#   2. MetricName:{...} syntax lets CloudWatch find every dimension value
#   3. 7-day default period for trend visibility
#   4. Consistent color scheme for professional enterprise look
# ---------------------------------------------------------------------------

read -r -d '' DASHBOARD_BODY << 'DASHBOARD_EOF' || true
{
    "widgets": [

        ___ROW_1_HEADER___
        {
            "type": "text",
            "x": 0,
            "y": 0,
            "width": 24,
            "height": 1,
            "properties": {
                "markdown": "# 📊 Claude Code Enterprise ROI Dashboard\n**All developers · Auto-discovered · Last 7 days** | Namespace: `ClaudeCode/DevProductivity` · `ClaudeCode/ToolUsage`"
            }
        },

        ___KPI_1__TOTAL_PRS_MERGED___
        {
            "type": "metric",
            "x": 0,
            "y": 1,
            "width": 6,
            "height": 4,
            "properties": {
                "metrics": [
                    [ { "expression": "SEARCH('{ClaudeCode/DevProductivity,Developer} MetricName=\"PRsMerged\"', 'Sum', 604800)", "id": "prs", "label": "" } ]
                ],
                "view": "singleValue",
                "stacked": false,
                "region": "us-west-2",
                "stat": "Sum",
                "period": 604800,
                "title": "🎯 Total PRs Merged (All Devs, 7d)",
                "setPeriodToTimeRange": true,
                "sparkline": true,
                "liveData": true
            }
        },

        ___KPI_2__AVG_CYCLE_TIME___
        {
            "type": "metric",
            "x": 6,
            "y": 1,
            "width": 6,
            "height": 4,
            "properties": {
                "metrics": [
                    [ { "expression": "SEARCH('{ClaudeCode/DevProductivity,Developer} MetricName=\"PRCycleTimeHours\"', 'Average', 604800)", "id": "ct", "label": "" } ]
                ],
                "view": "singleValue",
                "stacked": false,
                "region": "us-west-2",
                "stat": "Average",
                "period": 604800,
                "title": "⏱️ Avg Cycle Time — Hours (All Devs, 7d)",
                "setPeriodToTimeRange": true,
                "sparkline": true,
                "liveData": true
            }
        },

        ___KPI_3__BUILD_PASS_RATE___
        {
            "type": "metric",
            "x": 12,
            "y": 1,
            "width": 6,
            "height": 4,
            "properties": {
                "metrics": [
                    [ { "expression": "SEARCH('{ClaudeCode/DevProductivity,Developer,Repository} MetricName=\"BuildPassRate\"', 'Average', 604800)", "id": "bpr", "label": "" } ]
                ],
                "view": "singleValue",
                "stacked": false,
                "region": "us-west-2",
                "stat": "Average",
                "period": 604800,
                "title": "✅ Build Pass Rate (All Devs, 7d)",
                "setPeriodToTimeRange": true,
                "sparkline": true,
                "liveData": true
            }
        },

        ___KPI_4__TOTAL_COST___
        {
            "type": "metric",
            "x": 18,
            "y": 1,
            "width": 6,
            "height": 4,
            "properties": {
                "metrics": [
                    [ { "expression": "SEARCH('{ClaudeCode/DevProductivity,Developer} MetricName=\"SessionCost\"', 'Sum', 604800)", "id": "cost", "label": "" } ]
                ],
                "view": "singleValue",
                "stacked": false,
                "region": "us-west-2",
                "stat": "Sum",
                "period": 604800,
                "title": "💰 Total Cost — USD (All Devs, 7d)",
                "setPeriodToTimeRange": true,
                "sparkline": true,
                "liveData": true
            }
        },

        ___ROW_2_HEADER___
        {
            "type": "text",
            "x": 0,
            "y": 5,
            "width": 24,
            "height": 1,
            "properties": {
                "markdown": "## 👥 Per-Developer Productivity"
            }
        },

        ___ROW_2_LEFT__PRS_PER_DEVELOPER___
        {
            "type": "metric",
            "x": 0,
            "y": 6,
            "width": 12,
            "height": 6,
            "properties": {
                "metrics": [
                    [ { "expression": "SEARCH('{ClaudeCode/DevProductivity,Developer} MetricName=\"PRsMerged\"', 'Sum', 86400)", "id": "e1" } ]
                ],
                "view": "bar",
                "stacked": true,
                "region": "us-west-2",
                "stat": "Sum",
                "period": 86400,
                "title": "PRs Merged per Developer (stacked daily, 7d)",
                "setPeriodToTimeRange": true,
                "liveData": true,
                "yAxis": {
                    "left": { "label": "PRs Merged", "showUnits": false, "min": 0 }
                }
            }
        },

        ___ROW_2_RIGHT__BUILD_RATE_PER_DEVELOPER___
        {
            "type": "metric",
            "x": 12,
            "y": 6,
            "width": 12,
            "height": 6,
            "properties": {
                "metrics": [
                    [ { "expression": "SEARCH('{ClaudeCode/DevProductivity,Developer,Repository} MetricName=\"BuildPassRate\"', 'Average', 86400)", "id": "e2" } ]
                ],
                "view": "timeSeries",
                "stacked": false,
                "region": "us-west-2",
                "stat": "Average",
                "period": 86400,
                "title": "Build Pass Rate per Developer (line, 7d)",
                "setPeriodToTimeRange": true,
                "liveData": true,
                "yAxis": {
                    "left": { "label": "Pass Rate (0–1)", "showUnits": false, "min": 0, "max": 1 }
                },
                "annotations": {
                    "horizontal": [
                        { "label": "Target: 80%", "value": 0.8, "color": "#2ca02c", "fill": "none" }
                    ]
                }
            }
        },

        ___ROW_3_HEADER___
        {
            "type": "text",
            "x": 0,
            "y": 12,
            "width": 24,
            "height": 1,
            "properties": {
                "markdown": "## 📝 Code Volume & Quality"
            }
        },

        ___ROW_3_LEFT__LINES_ADDED_PER_DEV___
        {
            "type": "metric",
            "x": 0,
            "y": 13,
            "width": 12,
            "height": 6,
            "properties": {
                "metrics": [
                    [ { "expression": "SEARCH('{ClaudeCode/DevProductivity,Developer} MetricName=\"LinesAddedPerPR\"', 'Sum', 86400)", "id": "e3" } ]
                ],
                "view": "bar",
                "stacked": false,
                "region": "us-west-2",
                "stat": "Sum",
                "period": 86400,
                "title": "Lines Added per Developer (daily, 7d)",
                "setPeriodToTimeRange": true,
                "liveData": true,
                "yAxis": {
                    "left": { "label": "Lines Added", "showUnits": false, "min": 0 }
                }
            }
        },

        ___ROW_3_RIGHT__CYCLE_TIME_PER_DEV___
        {
            "type": "metric",
            "x": 12,
            "y": 13,
            "width": 12,
            "height": 6,
            "properties": {
                "metrics": [
                    [ { "expression": "SEARCH('{ClaudeCode/DevProductivity,Developer} MetricName=\"PRCycleTimeHours\"', 'Average', 86400)", "id": "e4" } ]
                ],
                "view": "timeSeries",
                "stacked": false,
                "region": "us-west-2",
                "stat": "Average",
                "period": 86400,
                "title": "Cycle Time per Developer — Hours (lower is better, 7d)",
                "setPeriodToTimeRange": true,
                "liveData": true,
                "yAxis": {
                    "left": { "label": "Hours", "showUnits": false, "min": 0 }
                },
                "annotations": {
                    "horizontal": [
                        { "label": "Target: 4h", "value": 4, "color": "#2ca02c", "fill": "none" },
                        { "label": "Warning: 24h", "value": 24, "color": "#d62728", "fill": "none" }
                    ]
                }
            }
        },

        ___ROW_4_HEADER___
        {
            "type": "text",
            "x": 0,
            "y": 19,
            "width": 24,
            "height": 1,
            "properties": {
                "markdown": "## 🔧 Tool Usage"
            }
        },

        ___ROW_4_LEFT__TOOL_INVOCATIONS_BY_TYPE___
        {
            "type": "metric",
            "x": 0,
            "y": 20,
            "width": 12,
            "height": 6,
            "properties": {
                "metrics": [
                    [ { "expression": "SEARCH('{ClaudeCode/ToolUsage,ToolName} MetricName=\"ToolInvocations\"', 'Sum', 604800)", "id": "e5" } ]
                ],
                "view": "bar",
                "stacked": true,
                "region": "us-west-2",
                "stat": "Sum",
                "period": 604800,
                "title": "Tool Invocations by Tool Type (all devs, 7d)",
                "setPeriodToTimeRange": true,
                "liveData": true,
                "yAxis": {
                    "left": { "label": "Invocations", "showUnits": false, "min": 0 }
                }
            }
        },

        ___ROW_4_RIGHT__TOOL_DIVERSITY_PER_DEV___
        {
            "type": "metric",
            "x": 12,
            "y": 20,
            "width": 12,
            "height": 6,
            "properties": {
                "metrics": [
                    [ { "expression": "SEARCH('{ClaudeCode/ToolUsage,Developer,ToolName} MetricName=\"ToolInvocations\"', 'Sum', 604800)", "id": "e6" } ]
                ],
                "view": "bar",
                "stacked": true,
                "region": "us-west-2",
                "stat": "Sum",
                "period": 604800,
                "title": "Tool Diversity per Developer (stacked by tool, 7d)",
                "setPeriodToTimeRange": true,
                "liveData": true,
                "yAxis": {
                    "left": { "label": "Invocations", "showUnits": false, "min": 0 }
                }
            }
        },

        ___ROW_5_HEADER___
        {
            "type": "text",
            "x": 0,
            "y": 26,
            "width": 24,
            "height": 1,
            "properties": {
                "markdown": "## 💵 Cost Efficiency"
            }
        },

        ___ROW_5_LEFT__SESSION_COST_PER_DEV___
        {
            "type": "metric",
            "x": 0,
            "y": 27,
            "width": 12,
            "height": 6,
            "properties": {
                "metrics": [
                    [ { "expression": "SEARCH('{ClaudeCode/DevProductivity,Developer} MetricName=\"SessionCost\"', 'Sum', 86400)", "id": "e7" } ]
                ],
                "view": "bar",
                "stacked": false,
                "region": "us-west-2",
                "stat": "Sum",
                "period": 86400,
                "title": "Session Cost per Developer — USD (daily, 7d)",
                "setPeriodToTimeRange": true,
                "liveData": true,
                "yAxis": {
                    "left": { "label": "USD", "showUnits": false, "min": 0 }
                }
            }
        },

        ___ROW_5_RIGHT__COST_PER_PR___
        {
            "type": "metric",
            "x": 12,
            "y": 27,
            "width": 12,
            "height": 6,
            "properties": {
                "metrics": [
                    [ { "expression": "SEARCH('{ClaudeCode/DevProductivity,Developer} MetricName=\"SessionCost\"', 'Sum', 604800)", "id": "cost_per_dev", "visible": false } ],
                    [ { "expression": "SEARCH('{ClaudeCode/DevProductivity,Developer} MetricName=\"PRsMerged\"', 'Sum', 604800)", "id": "prs_per_dev", "visible": false } ],
                    [ { "expression": "METRICS(\"cost_per_dev\") / METRICS(\"prs_per_dev\")", "id": "cost_per_pr", "label": "" } ]
                ],
                "view": "bar",
                "stacked": false,
                "region": "us-west-2",
                "stat": "Sum",
                "period": 604800,
                "title": "Cost per PR per Developer — USD (7d)",
                "setPeriodToTimeRange": true,
                "liveData": true,
                "yAxis": {
                    "left": { "label": "USD / PR", "showUnits": false, "min": 0 }
                }
            }
        },

        ___ROW_6_HEADER___
        {
            "type": "text",
            "x": 0,
            "y": 33,
            "width": 24,
            "height": 1,
            "properties": {
                "markdown": "## 🔍 CloudWatch Logs Insights — Ready-to-Use Queries"
            }
        },

        ___ROW_6__LOGS_INSIGHTS_QUERIES___
        {
            "type": "text",
            "x": 0,
            "y": 34,
            "width": 24,
            "height": 8,
            "properties": {
                "markdown": "### Copy these queries into [CloudWatch Logs Insights](https://us-west-2.console.aws.amazon.com/cloudwatch/home?region=us-west-2#logsV2:logs-insights) \nLog Group: `/aws/lambda/claude-code-roi-webhook`\n\n---\n\n#### 🏆 Top 5 Developers by PRs Merged This Week\n```\nfields @timestamp, @message\n| filter @message like /Metrics recorded/\n| parse @message '\"developer\": \"*\"' as developer\n| stats count(*) as prs_merged by developer\n| sort prs_merged desc\n| limit 5\n```\n\n---\n\n#### 🚨 Developers with Build Failure Rate > 20%\n```\nfields @timestamp, @message\n| filter @message like /Metrics recorded/\n| parse @message '\"developer\": \"*\"' as developer\n| parse @message '\"build_passed\": *' as build_passed\n| stats sum(case when build_passed = 'false' then 1 else 0 end) as failures,\n        count(*) as total\n        by developer\n| filter (failures / total) > 0.20\n| display developer, failures, total, concat(toString(round((failures / total) * 100, 1)), '%') as failure_rate\n| sort failure_rate desc\n```\n\n---\n\n#### 📦 Average Cycle Time by Repository\n```\nfields @timestamp, @message\n| filter @message like /Metrics recorded/\n| parse @message '\"repository\": \"*\"' as repository\n| parse @message '\"cycle_time_hours\": *,' as cycle_time\n| stats avg(cycle_time) as avg_cycle_hours,\n        min(cycle_time) as min_hours,\n        max(cycle_time) as max_hours,\n        count(*) as pr_count\n        by repository\n| sort avg_cycle_hours asc\n```\n\n---\n\n#### 🔗 Tool Usage Correlation with Build Pass Rate\n```\nfields @timestamp, @message\n| filter @message like /Metrics recorded/\n| parse @message '\"developer\": \"*\"' as developer\n| parse @message '\"build_passed\": *' as build_passed\n| parse @message '\"files_changed\": *,' as files_changed\n| parse @message '\"lines_added\": *,' as lines_added\n| stats avg(case when build_passed = 'true' then 1 else 0 end) as pass_rate,\n        avg(files_changed) as avg_files,\n        avg(lines_added) as avg_lines,\n        count(*) as total_prs\n        by developer\n| sort pass_rate desc\n```\n\n---\n\n💡 **Tip**: Adjust the time range in Logs Insights (top-right) to match your analysis window. Use `1w` for weekly, `1d` for daily."
            }
        }
    ]
}
DASHBOARD_EOF

# ---------------------------------------------------------------------------
# Strip the comment markers (___ROW_*___) — they're only for readability
# ---------------------------------------------------------------------------
CLEAN_BODY=$(echo "$DASHBOARD_BODY" | grep -v '___.*___')

# ---------------------------------------------------------------------------
# Deploy the dashboard
# ---------------------------------------------------------------------------
echo ""
echo "📤 Deploying dashboard to CloudWatch..."
echo ""

aws cloudwatch put-dashboard \
    --dashboard-name "$DASHBOARD_NAME" \
    --dashboard-body "$CLEAN_BODY" \
    --region "$REGION"

echo ""
echo "============================================================"
echo "  ✅ Dashboard deployed successfully!"
echo "============================================================"
echo ""
echo "  🔗 Open in console:"
echo "  https://us-west-2.console.aws.amazon.com/cloudwatch/home?region=us-west-2#dashboards/dashboard/$DASHBOARD_NAME"
echo ""
echo "============================================================"
echo "  Dashboard Layout Summary"
echo "============================================================"
echo ""
echo "  Row 1 — Overview KPIs"
echo "    ├── 🎯 Total PRs Merged (SUM, all devs)"
echo "    ├── ⏱️ Avg Cycle Time (AVG, all devs)"
echo "    ├── ✅ Build Pass Rate (AVG, all devs)"
echo "    └── 💰 Total Cost (SUM, all devs)"
echo ""
echo "  Row 2 — Per-Developer Productivity"
echo "    ├── 📊 PRs Merged per Developer (stacked bar)"
echo "    └── 📈 Build Pass Rate per Developer (line)"
echo ""
echo "  Row 3 — Code Volume & Quality"
echo "    ├── 📊 Lines Added per Developer (bar)"
echo "    └── 📈 Cycle Time per Developer (line)"
echo ""
echo "  Row 4 — Tool Usage"
echo "    ├── 📊 Tool Invocations by Type (stacked bar)"
echo "    └── 📊 Tool Diversity per Developer (stacked bar)"
echo ""
echo "  Row 5 — Cost Efficiency"
echo "    ├── 📊 Session Cost per Developer (bar)"
echo "    └── 📊 Cost per PR per Developer (calculated)"
echo ""
echo "  Row 6 — Logs Insights Queries (copy-paste ready)"
echo ""
echo "============================================================"
echo "  Key Design Decisions"
echo "============================================================"
echo ""
echo "  • SEARCH expressions auto-discover ALL developers"
echo "    → No hardcoded names; new devs appear automatically"
echo "  • Metric Math (METRICS()) used for Cost-per-PR calculation"
echo "  • Annotation lines mark targets (80% build rate, 4h cycle)"
echo "  • 86400s (daily) periods for trend charts"
echo "  • 604800s (weekly) periods for KPI rollups"
echo "  • Namespaces: ClaudeCode/DevProductivity, ClaudeCode/ToolUsage"
echo ""
echo "  To delete: ./delete_dashboard.sh"
echo ""
