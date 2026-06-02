#!/bin/bash
# ════════════════════════════════════════════════════════════════════
#  deploy_all.sh — Full deployment of Claude Code ROI system
# ════════════════════════════════════════════════════════════════════
#  Runs ALL deployment steps in order:
#    1. Update IAM (add CW Logs permissions)
#    2. Deploy Lambda v2 (9 metrics + structured logs)
#    3. Seed demo data (8 devs × 14 days)
#    4. Create CloudWatch Dashboard
#    5. Install stop hook (git attribution + commit SHAs)
#    6. Merge MCP tools config
#
#  Account: YOUR_ACCOUNT_ID | Region: us-west-2
#  Usage:  chmod +x deploy_all.sh && ./deploy_all.sh
# ════════════════════════════════════════════════════════════════════

set -euo pipefail

# ── Auto-detect Account ID ────────────────────────────────────────
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text 2>/dev/null)
if [ -z "$ACCOUNT_ID" ] || [ "$ACCOUNT_ID" = "None" ]; then
  echo "❌ ERROR: Could not detect AWS Account ID."
  echo "   Make sure your AWS CLI is configured (aws configure) or set AWS_PROFILE."
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo ""
echo "══════════════════════════════════════════════════════════"
echo "  Claude Code ROI — Full Deployment"
echo "══════════════════════════════════════════════════════════"
echo ""
echo "  Account: $ACCOUNT_ID"
echo "  Region:  us-west-2"
echo ""

# ── Step 1: Update IAM ────────────────────────────────────────────
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Step 1/6: IAM — Add CloudWatch Logs permissions"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
cd "${SCRIPT_DIR}/lambda"
bash update_iam.sh
echo ""

# ── Step 2: Deploy Lambda v2 ──────────────────────────────────────
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Step 2/6: Lambda — Deploy v2 (9 metrics + CW Logs)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
bash deploy_v2.sh
echo ""

# ── Step 3: Seed demo data ────────────────────────────────────────
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Step 3/6: Seed — 8 developers × 14 days demo data"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
cd "${SCRIPT_DIR}/dashboard"
python3 seed_demo_data.py
echo ""

# ── Step 4: Create CloudWatch Dashboard ───────────────────────────
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Step 4/6: Dashboard — CloudWatch ROI Dashboard"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
bash create_dashboard.sh
echo ""

# ── Step 5: Install stop hook ─────────────────────────────────────
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Step 5/6: Hook — Install stop.sh (git attribution)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
HOOK_DIR="$HOME/.claude/hooks"
mkdir -p "$HOOK_DIR"
cp "${SCRIPT_DIR}/stop.sh" "${HOOK_DIR}/stop.sh"
chmod +x "${HOOK_DIR}/stop.sh"
echo "  ✅ stop.sh → ${HOOK_DIR}/stop.sh"
echo "     Captures: session_id, repo, branch, commits, commit SHAs, git user"
echo ""

# ── Step 6: Merge MCP tools config ───────────────────────────────
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Step 6/6: MCP — Copy tool servers + merge config"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
MCP_DIR="$HOME/mcp-tools"
mkdir -p "$MCP_DIR"
cp "${SCRIPT_DIR}/mcp-tools/calculator_mcp.py" "$MCP_DIR/"
cp "${SCRIPT_DIR}/mcp-tools/semantic_search_mcp.py" "$MCP_DIR/"
cp "${SCRIPT_DIR}/mcp-tools/code_review_mcp.py" "$MCP_DIR/"
cp "${SCRIPT_DIR}/mcp-tools/cicd_trigger_mcp.py" "$MCP_DIR/"
echo "  ✅ 4 MCP tool servers → ${MCP_DIR}/"
echo ""
echo "  ⚠️  Manual step: merge mcp-tools/mcp.json into ~/.claude/mcp.json"
echo "     (run: cat ${SCRIPT_DIR}/mcp-tools/mcp.json)"
echo ""

# ── Summary ───────────────────────────────────────────────────────
echo ""
echo "══════════════════════════════════════════════════════════"
echo "  ✅ ALL 6 STEPS COMPLETE"
echo "══════════════════════════════════════════════════════════"
echo ""
echo "  What's deployed:"
echo "    ✅ Lambda v2 (9 MR metrics + CloudWatch Logs)"
echo "    ✅ IAM (CloudWatch Logs permissions)"
echo "    ✅ Dashboard (ClaudeCode-ROI-Dashboard)"
echo "    ✅ Demo data (8 devs × 14 days)"
echo "    ✅ Stop hook (git attribution + commit SHAs)"
echo "    ✅ MCP tools (calculator, search, review, cicd)"
echo ""
echo "  To run OTEL receiver v2 with CloudWatch push:"
echo "    cd ${SCRIPT_DIR}/otel"
echo "    PUSH_TO_CLOUDWATCH=1 python3 otel_receiver_v2.py"
echo ""
echo "  To run in local-only mode:"
echo "    python3 ${SCRIPT_DIR}/otel/otel_receiver_v2.py"
echo ""
echo "  CloudWatch Dashboard:"
echo "    https://us-west-2.console.aws.amazon.com/cloudwatch/home?region=us-west-2#dashboards:name=ClaudeCode-ROI-Dashboard"
echo ""
echo "══════════════════════════════════════════════════════════"
