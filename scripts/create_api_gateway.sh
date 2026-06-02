#!/bin/bash

# Step 3: Create API Gateway for Lambda Webhook Endpoint
# This creates a public HTTPS URL that GitLab can call

FUNCTION_NAME="claude-code-roi-webhook"
API_NAME="claude-code-roi-api"
REGION="us-west-2"

# ── Auto-detect Account ID ────────────────────────────────────────
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text 2>/dev/null)
if [ -z "$ACCOUNT_ID" ] || [ "$ACCOUNT_ID" = "None" ]; then
  echo "❌ ERROR: Could not detect AWS Account ID."
  echo "   Make sure your AWS CLI is configured (aws configure) or set AWS_PROFILE."
  exit 1
fi

echo "======================================"
echo "Creating API Gateway"
echo "======================================"
echo ""

# Create REST API
echo "Creating REST API..."
API_ID=$(aws apigateway create-rest-api \
  --name $API_NAME \
  --description "Webhook endpoint for GitLab MR events" \
  --region $REGION \
  --query 'id' \
  --output text)

echo "✅ API created: $API_ID"
echo ""

# Get the root resource ID
ROOT_ID=$(aws apigateway get-resources \
  --rest-api-id $API_ID \
  --region $REGION \
  --query 'items[0].id' \
  --output text)

echo "Root resource ID: $ROOT_ID"
echo ""

# Create /webhook resource
echo "Creating /webhook resource..."
RESOURCE_ID=$(aws apigateway create-resource \
  --rest-api-id $API_ID \
  --parent-id $ROOT_ID \
  --path-part webhook \
  --region $REGION \
  --query 'id' \
  --output text)

echo "✅ Resource created: $RESOURCE_ID"
echo ""

# Create POST method
echo "Creating POST method..."
aws apigateway put-method \
  --rest-api-id $API_ID \
  --resource-id $RESOURCE_ID \
  --http-method POST \
  --authorization-type NONE \
  --region $REGION

echo "✅ POST method created"
echo ""

# Create integration with Lambda
echo "Integrating with Lambda..."
aws apigateway put-integration \
  --rest-api-id $API_ID \
  --resource-id $RESOURCE_ID \
  --http-method POST \
  --type AWS_PROXY \
  --integration-http-method POST \
  --uri arn:aws:apigateway:$REGION:lambda:path/2015-03-31/functions/arn:aws:lambda:$REGION:$ACCOUNT_ID:function:$FUNCTION_NAME/invocations \
  --region $REGION

echo "✅ Lambda integration created"
echo ""

# Grant API Gateway permission to invoke Lambda
echo "Granting API Gateway permission to invoke Lambda..."
aws lambda add-permission \
  --function-name $FUNCTION_NAME \
  --statement-id AllowAPIGatewayInvoke \
  --action lambda:InvokeFunction \
  --principal apigateway.amazonaws.com \
  --source-arn "arn:aws:execute-api:$REGION:$ACCOUNT_ID:$API_ID/*/*" \
  --region $REGION

echo "✅ Lambda permission granted"
echo ""

# Deploy the API
echo "Deploying API..."
STAGE_ID=$(aws apigateway create-deployment \
  --rest-api-id $API_ID \
  --stage-name prod \
  --region $REGION \
  --query 'id' \
  --output text)

echo "✅ API deployed to prod stage"
echo ""

# Get the invoke URL
INVOKE_URL="https://$API_ID.execute-api.$REGION.amazonaws.com/prod/webhook"

echo "======================================"
echo "✅ API Gateway Ready!"
echo "======================================"
echo ""
echo "Your webhook URL is:"
echo ""
echo "  $INVOKE_URL"
echo ""
echo "Next: Add this URL to your GitLab repo webhooks:"
echo "1. Go to: https://gitlab.aws.dev/YOUR_USERNAME/YOUR_REPO_NAME"
echo "2. Settings → Webhooks → Add webhook"
echo "3. URL: $INVOKE_URL"
echo "4. Trigger events: Merge requests (check 'Merge request events')"
echo "5. When to trigger: Select 'Update' (fires on merge)"
echo ""
