# GitHub Webhook Setup Guide

Complete step-by-step guide for connecting a GitHub repository to the Claude Code DevProductivity pipeline.

---

## Architecture Overview

```
GitHub PR Merge Event
    │
    ▼
┌─────────────────────────────┐
│  X-Hub-Signature-256        │  HMAC-SHA256 validation
│  (webhook secret)           │
└─────────────┬───────────────┘
              │ POST
              ▼
┌─────────────────────────────┐
│  API Gateway (cg1qaoi970)   │
│  /github-webhook            │  prod stage
└─────────────┬───────────────┘
              │ AWS_PROXY
              ▼
┌─────────────────────────────┐
│  Lambda                     │
│  claude-code-roi-github-    │  Extracts 9 metrics from PR
│  webhook                    │  payload + writes structured log
└──────┬──────────────┬───────┘
       │              │
       ▼              ▼
┌──────────┐   ┌────────────────┐
│CloudWatch│   │ CloudWatch     │
│ Metrics  │   │ Logs           │
│ (9)      │   │ /claude-code/  │
│          │   │ dev-productivity│
└──────────┘   └────────────────┘
```

Both GitHub and GitLab webhooks write to the **same** CloudWatch namespace and log group, so the existing dashboard shows data from both sources.

---

## Part 1: Generate a Webhook Secret

The webhook secret is used for HMAC-SHA256 signature validation. GitHub signs every webhook payload with this secret, and the Lambda verifies it before processing.

### Generate a strong random secret:

```bash
# Option 1: openssl (recommended)
openssl rand -hex 32

# Option 2: python
python3 -c "import secrets; print(secrets.token_hex(32))"

# Option 3: uuidgen (less entropy but works)
uuidgen | tr -d '-'
```

**Save this value** — you'll need it in two places:
1. GitHub webhook configuration (step 2)
2. Lambda environment variable (step 3)

---

## Part 2: Add Webhook in GitHub

### For GitHub Cloud (github.com)

1. Navigate to your repository on GitHub
2. Go to **Settings** → **Webhooks** → **Add webhook**
3. Fill in the form:

| Field | Value |
|---|---|
| **Payload URL** | `https://cg1qaoi970.execute-api.us-west-2.amazonaws.com/prod/github-webhook` |
| **Content type** | `application/json` |
| **Secret** | *(paste the secret from Part 1)* |
| **SSL verification** | ✅ Enable SSL verification |
| **Which events?** | Select: **Let me select individual events** |
| **Events** | ☑️ **Pull requests** (uncheck everything else) |
| **Active** | ✅ |

4. Click **Add webhook**

### For GitHub Enterprise Server (GHES)

The setup is identical, but your URL path is the same:

1. Navigate to your repository on your GHES instance
2. Go to **Settings** → **Hooks** → **Add webhook**
3. Same form as above — the Lambda handles both GitHub Cloud and GHES payloads

> **Note:** GHES may send slightly different timestamp formats. The Lambda handles both `2024-01-15T10:30:00Z` and `2024-01-15T10:30:00+00:00` formats.

### Verify: Ping Event

After adding the webhook, GitHub sends a **ping** event. Check the webhook's **Recent Deliveries** tab — you should see:

- **Status:** ✅ 200
- **Response body:**
  ```json
  {
    "message": "Pong! Webhook configured successfully.",
    "zen": "..."
  }
  ```

---

## Part 3: Configure the Lambda Secret

Store the webhook secret as a Lambda environment variable:

```bash
aws lambda update-function-configuration \
  --function-name claude-code-roi-github-webhook \
  --environment 'Variables={GITHUB_WEBHOOK_SECRET=YOUR_SECRET_HERE}' \
  --region us-west-2 \
  --no-cli-pager
```

Replace `YOUR_SECRET_HERE` with the same secret you entered in GitHub.

### Verify the secret is set:

```bash
aws lambda get-function-configuration \
  --function-name claude-code-roi-github-webhook \
  --region us-west-2 \
  --query "Environment.Variables" \
  --output json \
  --no-cli-pager
```

You should see:
```json
{
    "GITHUB_WEBHOOK_SECRET": "your-secret-value"
}
```

> **Security Note:** For production deployments, consider storing the secret in AWS Secrets Manager and retrieving it at runtime instead of using environment variables.

---

## Part 4: Test the Webhook

### Option A: Create a real PR and merge it

1. Create a feature branch:
   ```bash
   git checkout -b test-github-webhook
   echo "# Test file" > test_webhook.md
   git add test_webhook.md
   git commit -m "test: verify GitHub webhook pipeline"
   git push origin test-github-webhook
   ```

2. Create a Pull Request on GitHub (base: main ← compare: test-github-webhook)

3. Merge the PR

4. Check webhook delivery in GitHub: **Settings → Webhooks → Recent Deliveries**

5. Check CloudWatch metrics:
   ```bash
   aws cloudwatch get-metric-statistics \
     --namespace ClaudeCode/DevProductivity \
     --metric-name PRsMerged \
     --dimensions Name=Developer,Value=YOUR_GITHUB_USERNAME \
     --start-time $(date -u -v-10M +%Y-%m-%dT%H:%M:%S)Z \
     --end-time $(date -u +%Y-%m-%dT%H:%M:%S)Z \
     --period 300 \
     --statistics Sum \
     --region us-west-2
   ```

### Option B: Send a test payload with curl

```bash
# Generate a test payload
PAYLOAD='{
  "action": "closed",
  "pull_request": {
    "number": 999,
    "title": "Test PR for webhook verification",
    "user": {"login": "test-developer"},
    "merged": true,
    "created_at": "2026-04-30T10:00:00Z",
    "merged_at": "2026-04-30T12:30:00Z",
    "merge_commit_sha": "abc123def456",
    "head": {"ref": "test-branch", "sha": "def456abc789"},
    "base": {"ref": "main", "repo": {"full_name": "myorg/myrepo"}},
    "additions": 42,
    "deletions": 10,
    "changed_files": 3,
    "commits": 2,
    "review_comments": 5,
    "labels": [{"name": "enhancement"}],
    "merged_by": {"login": "reviewer"},
    "mergeable_state": "clean",
    "requested_reviewers": [],
    "requested_teams": []
  },
  "repository": {"full_name": "myorg/myrepo"}
}'

# Compute HMAC signature
SECRET="your-webhook-secret"
SIGNATURE=$(echo -n "${PAYLOAD}" | openssl dgst -sha256 -hmac "${SECRET}" | awk '{print "sha256="$2}')

# Send the webhook
curl -X POST \
  "https://cg1qaoi970.execute-api.us-west-2.amazonaws.com/prod/github-webhook" \
  -H "Content-Type: application/json" \
  -H "X-GitHub-Event: pull_request" \
  -H "X-Hub-Signature-256: ${SIGNATURE}" \
  -d "${PAYLOAD}"
```

Expected response:
```json
{
  "message": "GitHub webhook — Metrics + Logs recorded",
  "metrics_published": 9,
  "developer": "test-developer",
  "repository": "myorg/myrepo",
  "pr_number": 999,
  "cycle_time_hours": 2.5,
  ...
}
```

---

## Part 5: What Gets Tracked

### 9 CloudWatch Metrics

Published to namespace `ClaudeCode/DevProductivity` with dimensions `Developer` + `Repository`:

| # | Metric | Source Field | Unit |
|---|--------|-------------|------|
| 1 | PRsMerged | (always 1) | Count |
| 2 | PRCycleTimeHours | created_at → merged_at | None (hours) |
| 3 | BuildPassRate | mergeable_state / merge_commit_sha | None (0.0 or 1.0) |
| 4 | LinesAddedPerPR | pr.additions | Count |
| 5 | LinesRemovedPerPR | pr.deletions | Count |
| 6 | FilesChangedPerPR | pr.changed_files | Count |
| 7 | ApprovalsPerPR | reviews (if available) | Count |
| 8 | ReviewCommentsPerPR | pr.review_comments | Count |
| 9 | CommitsPerPR | pr.commits | Count |

### Structured Log Event

Written to `/claude-code/dev-productivity` log group as JSON:

```json
{
  "event_type": "pr_merged",
  "source": "github",
  "developer": "octocat",
  "repository": "myorg/myrepo",
  "pr_number": 42,
  "pr_title": "Add new feature",
  "source_branch": "feature-branch",
  "target_branch": "main",
  "created_at": "2026-04-30T10:00:00Z",
  "merged_at": "2026-04-30T14:30:00Z",
  "merged_by": "reviewer",
  "cycle_time_hours": 4.5,
  "lines_added": 150,
  "lines_removed": 30,
  "files_changed": 8,
  "commits_count": 3,
  "approvals": 1,
  "review_comments": 7,
  "build_status": "merged_with_protections",
  "build_passed": true,
  "labels": ["enhancement", "ready-to-merge"],
  "merge_commit_sha": "abc123...",
  "head_sha": "def456...",
  "commit_shas": ["abc123...", "def456..."]
}
```

### Session↔PR Linkage

The `commit_shas` field enables linking Claude Code sessions to merged PRs:

```sql
-- CloudWatch Logs Insights query
fields @timestamp, developer, pr_number, commit_shas, cycle_time_hours
| filter event_type = "pr_merged"
| filter source = "github"
| sort @timestamp desc
| limit 20
```

To correlate with Claude Code sessions, match `commit_shas` against the git commits made during a Claude Code session (captured via the OTel hooks).

---

## Part 6: Differences from GitLab Webhook

| Aspect | GitLab | GitHub |
|--------|--------|--------|
| Event type | `merge_request` (object_kind) | `pull_request` (X-GitHub-Event header) |
| Merge signal | `state == "merged"` | `action == "closed"` + `merged == true` |
| Security | Token-based (optional) | HMAC-SHA256 (X-Hub-Signature-256) |
| Developer field | `user.username` | `pr.user.login` |
| Repository field | `project.path_with_namespace` | `repository.full_name` |
| Code stats | `object_attributes.additions/deletions` | `pull_request.additions/deletions` |
| Review comments | `user_notes_count` | `review_comments` |
| Build status | `last_pipeline.status` | `mergeable_state` / merge protection |
| Log `source` field | (not set) | `"github"` |
| Lambda function | `claude-code-roi-webhook` | `claude-code-roi-github-webhook` |
| API endpoint | `/webhook` | `/github-webhook` |

Both write to the **same** CloudWatch namespace and log group, so dashboard and Logs Insights queries work across both sources.

---

## Part 7: Troubleshooting

### Webhook returns 403 "Invalid signature"

- Verify the secret in GitHub matches the Lambda env var exactly
- Check for trailing whitespace or newlines in the secret
- Verify you selected `application/json` (not `application/x-www-form-urlencoded`)

```bash
# Check the Lambda env var
aws lambda get-function-configuration \
  --function-name claude-code-roi-github-webhook \
  --region us-west-2 \
  --query "Environment.Variables.GITHUB_WEBHOOK_SECRET" \
  --output text --no-cli-pager
```

### Webhook returns 200 but says "skipped"

- Make sure you selected **Pull requests** event (not just Pushes)
- The Lambda only processes merged PRs — closing without merge is ignored
- Check the response body for the skip reason

### No metrics appearing in CloudWatch

- Metrics can take 1-2 minutes to appear
- Verify the Developer dimension matches what you're querying:
  ```bash
  aws cloudwatch list-metrics \
    --namespace ClaudeCode/DevProductivity \
    --metric-name PRsMerged \
    --region us-west-2 --no-cli-pager
  ```

### Lambda execution errors

Check the Lambda's own CloudWatch Logs:
```bash
aws logs tail /aws/lambda/claude-code-roi-github-webhook \
  --region us-west-2 --since 1h --no-cli-pager
```

### Rate limiting / Lambda throttling

The Lambda is configured with 256MB memory and 30s timeout — more than sufficient for webhook processing. If you see throttling, check your Lambda concurrency limits in the AWS console.

---

## Part 8: Security Best Practices

1. **Always validate signatures** — Never deploy without `GITHUB_WEBHOOK_SECRET` set
2. **Rotate secrets periodically** — Update both GitHub and Lambda env var simultaneously
3. **Use Secrets Manager for production** — Store the secret in AWS Secrets Manager instead of env vars:
   ```python
   # In Lambda code:
   import boto3
   sm = boto3.client("secretsmanager")
   secret = sm.get_secret_value(SecretId="github-webhook-secret")["SecretString"]
   ```
4. **Monitor failed validations** — Set a CloudWatch alarm on 403 responses
5. **IP allowlist (optional)** — GitHub publishes their webhook IP ranges at:
   `https://api.github.com/meta` → `hooks` array
