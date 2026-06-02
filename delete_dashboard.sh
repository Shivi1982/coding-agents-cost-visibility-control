#!/bin/bash

###############################################################################
# delete_dashboard.sh — Delete the Claude Code Enterprise ROI Dashboard
#
# Removes: "ClaudeCode-ROI-Dashboard" from us-west-2
# Account: YOUR_ACCOUNT_ID
#
# This only deletes the CloudWatch dashboard — it does NOT affect:
#   - Custom metrics in ClaudeCode/DevProductivity
#   - Custom metrics in ClaudeCode/ToolUsage
#   - Lambda functions
#   - API Gateway
#   - CloudWatch Logs
#
# Usage:
#   chmod +x delete_dashboard.sh
#   ./delete_dashboard.sh
###############################################################################

set -euo pipefail

DASHBOARD_NAME="ClaudeCode-ROI-Dashboard"
REGION="us-west-2"

echo "============================================================"
echo "  Deleting CloudWatch Dashboard"
echo "============================================================"
echo ""
echo "  Dashboard : $DASHBOARD_NAME"
echo "  Region    : $REGION"
echo ""

# Confirm before deleting
read -p "  ⚠️  Are you sure? This will delete the dashboard. (y/N): " confirm
if [[ "$confirm" != "y" && "$confirm" != "Y" ]]; then
    echo ""
    echo "  ❌ Cancelled. Dashboard was NOT deleted."
    exit 0
fi

echo ""
echo "  🗑️  Deleting dashboard..."
echo ""

aws cloudwatch delete-dashboards \
    --dashboard-names "$DASHBOARD_NAME" \
    --region "$REGION"

echo "  ✅ Dashboard '$DASHBOARD_NAME' deleted successfully."
echo ""
echo "  To recreate: ./create_dashboard.sh"
echo ""
echo "  Note: Custom metrics and logs are NOT affected."
echo "  Metrics in ClaudeCode/DevProductivity and ClaudeCode/ToolUsage"
echo "  will continue to collect data from your Lambda webhook."
echo ""
