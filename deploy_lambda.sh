#!/bin/bash

# Step 2: Deploy Lambda Function for Pipeline 3 (PR + Build Webhook)
# This script packages the Lambda code and deploys it to AWS

FUNCTION_NAME="claude-code-roi-webhook"
ROLE_NAME="claude-code-roi-lambda-role"
REGION="us-west-2"
ZIP_FILE="lambda_function.zip"
SOURCE_FILE="../lambda/gitlab_webhook_v2.py"
HANDLER="gitlab_webhook_v2.lambda_handler"

# ── Auto-detect Account ID ────────────────────────────────────────
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text 2>/dev/null)
if [ -z "$ACCOUNT_ID" ] || [ "$ACCOUNT_ID" = "None" ]; then
  echo "❌ ERROR: Could not detect AWS Account ID."
  echo "   Make sure your AWS CLI is configured (aws configure) or set AWS_PROFILE."
  exit 1
fi

echo "======================================"
echo "Deploying Lambda Function"
echo "======================================"
echo ""
echo "  Account:  $ACCOUNT_ID"
echo "  Region:   $REGION"
echo "  Function: $FUNCTION_NAME"
echo "  Handler:  $HANDLER"
echo ""
echo "Creating ZIP file..."

# Create ZIP with the correct source file
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cp "${SCRIPT_DIR}/${SOURCE_FILE}" /tmp/gitlab_webhook_v2.py
cd /tmp && zip -q $ZIP_FILE gitlab_webhook_v2.py

echo "✅ ZIP created: $ZIP_FILE"
echo ""
echo "Deploying to AWS Lambda..."

# Wait for IAM role propagation (if role was just created)
echo "  Waiting 10s for IAM role propagation..."
sleep 10

# Deploy the function
aws lambda create-function \
  --function-name $FUNCTION_NAME \
  --runtime python3.12 \
  --role arn:aws:iam::${ACCOUNT_ID}:role/$ROLE_NAME \
  --handler $HANDLER \
  --zip-file fileb:///tmp/$ZIP_FILE \
  --region $REGION \
  --timeout 30 \
  --memory-size 256

echo ""
echo "✅ Lambda function deployed!"
echo ""
echo "Function Name: $FUNCTION_NAME"
echo "Region: $REGION"
echo "Runtime: Python 3.12"
echo "Handler: $HANDLER"
echo ""
echo "Next: Run Step 3 to create the API Gateway endpoint."
