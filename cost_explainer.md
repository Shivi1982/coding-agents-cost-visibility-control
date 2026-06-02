# AWS Cost & Identity: Core Concepts Explained

---

## 1. What is CUR (Cost and Usage Report)

### What It Is
CUR stands for **Cost and Usage Report**. It is a detailed billing data file that AWS generates and writes into an S3 bucket you own — once a day (or hourly if you want). Every single dollar you spend on AWS, every API call that has a cost, shows up as one row in this file.

Think of it as a raw data export of your entire AWS bill, broken down to the most granular level AWS can provide.

### What's in a CUR Row
Each row in a CUR file represents one line-item charge and includes:

| Column | Example | What it means |
|---|---|---|
| `line_item_product_code` | `AmazonBedrock` | Which AWS service |
| `line_item_usage_type` | `Tokens-Input:claude-sonnet-4-6` | What you used |
| `line_item_usage_amount` | `2340000` | How much (tokens, GB, etc.) |
| `line_item_blended_cost` | `4.23` | Dollar cost |
| `line_item_usage_start_date` | `2026-04-26` | When it happened |
| `identity_line_item_id` | `arn:aws:iam::123456789:assumed-role/...` | **Who made the call** (NEW — CUR 2.0 only) |
| `resource_tags_user_team` | `data-science` | Any custom tags you applied |

### CUR 1.0 vs CUR 2.0
| | CUR 1.0 (legacy) | CUR 2.0 (current) |
|---|---|---|
| File format | CSV (gzip) | Parquet |
| Schema | Older, fewer columns | Richer, new columns added regularly |
| IAM Principal column | ❌ Not available | ✅ `identity_line_item_id` |
| Query tool | Athena | Athena |
| Status | Being sunset | This is what you should use |

### How to Set Up CUR 2.0

#### Step 1 — Create the Report in Billing Console
```
AWS Console → Billing and Cost Management → Data Exports → Create export
  Name: bedrock-cost-export
  Export type: Cost and Usage Report (Standard)
  Format: Parquet
  Time granularity: Daily
  S3 bucket: your-cur-bucket-name
  S3 prefix: cur/
```

#### Step 2 — Create Athena Table to Query It
AWS provides a CloudFormation template that auto-creates the Athena database + crawler. After running it, you can query directly:

```sql
-- Basic: all Bedrock spend this month
SELECT 
  line_item_usage_start_date,
  line_item_usage_type,
  SUM(line_item_blended_cost) AS cost_usd
FROM your_cur_database.your_cur_table
WHERE line_item_product_code = 'AmazonBedrock'
  AND line_item_usage_start_date >= date_trunc('month', current_date)
GROUP BY 1, 2
ORDER BY cost_usd DESC;
```

```sql
-- Per-developer spend (requires CUR 2.0 + IAM Principal Attribution enabled)
SELECT 
  SPLIT_PART(identity_line_item_id, '/', 3) AS developer,
  SUM(line_item_blended_cost) AS total_cost_usd,
  COUNT(*) AS api_calls
FROM your_cur_database.your_cur_table
WHERE line_item_product_code = 'AmazonBedrock'
  AND line_item_usage_start_date >= current_date - interval '7' day
  AND identity_line_item_id IS NOT NULL
GROUP BY 1
ORDER BY total_cost_usd DESC;
```

#### Step 3 — Verify the Column Exists
```sql
-- If this returns all NULLs, IAM Principal Attribution is not yet activated
SELECT DISTINCT identity_line_item_id
FROM your_cur_database.your_cur_table
WHERE line_item_product_code = 'AmazonBedrock'
  AND line_item_usage_start_date > current_date - interval '3' day
LIMIT 10;
```

### Where to Find CUR in the Console
```
Billing and Cost Management → Data Exports
```
The S3 bucket where your CUR lands is also shown there. You can browse the Parquet files directly in S3 console or use Athena for SQL queries.

---

## 2. What is IAM (Identity and Access Management)

### What It Is
IAM is AWS's **permission system** — it controls:
- **Who** can access AWS (users, roles, services)
- **What** they can do (which API calls are allowed)
- **Under what conditions** (specific resources, regions, time windows)

### The Three Core Building Blocks

#### IAM User
A permanent identity with long-lived credentials (access key + secret). Created once, exists until deleted.
```
arn:aws:iam::123456789012:user/alice.chen
```
- Has its own access keys stored in `~/.aws/credentials`
- Credentials don't expire (unless you rotate them)
- ⚠️ Less secure — if the key is leaked, the attacker has permanent access

#### IAM Role
A set of permissions that can be *assumed* temporarily. No long-lived credentials.
```
arn:aws:iam::123456789012:role/claude-code-developer
```
- Has a **trust policy** that defines who/what can assume it
- When assumed, AWS issues temporary credentials (15 min to 12 hrs)
- ✅ More secure — credentials expire automatically

#### IAM Policy
A JSON document that defines permissions. Attached to users or roles.
```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": [
      "bedrock:InvokeModel",
      "bedrock:Converse"
    ],
    "Resource": "arn:aws:bedrock:us-west-2::foundation-model/anthropic.claude-*"
  }]
}
```

### How IAM Relates to Bedrock Cost Tracking
When Claude Code calls Bedrock, it uses the developer's IAM identity (user or assumed role). Bedrock records **who made each call** as the `identity_line_item_id` in CUR 2.0. This is the foundation of per-developer cost tracking — no code changes needed.

---

## 3. What is an Assumed Role

### What It Is
"Assuming a role" means temporarily borrowing a role's permissions. You call the AWS STS (Security Token Service) API with `AssumeRole`, and AWS gives you back temporary credentials (access key + secret + session token) that expire after a set time.

### The Flow
```
Your Identity                  STS API                    AWS Services
     │                            │                            │
     │  AssumeRole(roleARN)       │                            │
     │ ──────────────────────────►│                            │
     │                            │  Validates trust policy    │
     │  ◄── temp credentials ─────│                            │
     │      (expires in 1-12h)    │                            │
     │                            │                            │
     │  Call Bedrock InvokeModel with temp credentials         │
     │ ───────────────────────────────────────────────────────►│
     │                            │  Records: assumed-role/... │
```

### What Your ARN Looks Like After Assumption
```
# Before assuming a role — your permanent IAM user:
arn:aws:iam::123456789012:user/shivi

# After assuming a role — your temporary session identity:
arn:aws:iam::123456789012:assumed-role/claude-code-developer/shivi.bhatia
                                        ▲                     ▲
                                    role name            session name
                                                   (you control this — 
                                                    set to your username 
                                                    for cost tracking)
```

### Why Session Name Matters for Cost Tracking
The session name is the part after the last `/` in an assumed-role ARN. If you set it to the developer's username:
```bash
aws sts assume-role \
  --role-arn "arn:aws:iam::ACCT:role/bedrock-dev" \
  --role-session-name "shivi.bhatia"    # ← this becomes visible in CUR
```
Then `identity_line_item_id` in CUR 2.0 shows `assumed-role/bedrock-dev/shivi.bhatia` — you know exactly who ran what.

### SSO / Federated Assumption
When you log in via SSO (AWS IAM Identity Center, Okta, Entra ID), the same mechanism happens automatically:
- Your browser authenticates to the SSO portal
- SSO calls `AssumeRoleWithSAML` or `AssumeRoleWithWebIdentity` on your behalf
- You get temporary credentials with a session tied to your SSO username
- All your AWS API calls are attributed to `assumed-role/AWSReservedSSO_.../your-email@amazon.com`

---

## 4. Your AWS Credentials & Config Explained

### 4.1 `~/.aws/credentials` — What You Have

```ini
[default]
aws_access_key_id = REDACTED
aws_secret_access_key = REDACTED
```

**What this is:** A **static IAM User credential**. This is a long-lived access key tied directly to an IAM User in AWS account. 

**Key facts:**
- The `[default]` profile is used when you run any AWS CLI command without specifying `--profile`
- These credentials never expire on their own — they stay valid until you explicitly rotate or delete them
- ⚠️ **Security note:** Static keys in `~/.aws/credentials` are the least secure form of AWS auth — if this file is ever exposed, an attacker has permanent access until you rotate the key
- For production/customer work, always prefer SSO profiles (which give temp credentials that auto-expire)

**What identity this gives you in AWS:**
```
arn:aws:iam::<account_id>:user/<iam_username>
```

---

### 4.2 `~/.aws/config` — Profile by Profile

#### `[default]`
```ini
region = us-east-1
```
Uses the static key from `~/.aws/credentials`. Region is us-east-1. No special auth method — just the plain access key.

---

#### `[profile ClineBedrock]`
```ini
credential_process = credential-process
region = us-east-1
```
**What it is:** Uses an external binary or script called `credential-process` to vend credentials. The AWS CLI runs this command and parses the JSON output (must return `AccessKeyId`, `SecretAccessKey`, `SessionToken`, `Expiration`).

**Why it exists:** Credential process providers let you plug in custom auth flows — useful for tools like Cline, VS Code extensions, or any IDE integration that needs Bedrock access without hardcoding keys.

---

#### `[profile teamA-demo]`
```ini
sso_session = teamA-demo
sso_account_id = YOUR_ACCOUNT_ID
sso_role_name = TeamA_BedrockDeveloper
region = us-west-2
```
**What it is:** An SSO profile using IAM Identity Center. 

**How it works:**
1. When you run `aws sso login --profile teamA-demo`, your browser opens the SSO portal at `https://d-9267e699f5.awsapps.com/start`
2. You authenticate (your Amazon login)
3. AWS grants you the `TeamA_BedrockDeveloper` role in account `YOUR_ACCOUNT_ID`
4. Temporary credentials are cached locally (usually in `~/.aws/sso/cache/`)
5. All calls with `--profile teamA-demo` use these temp credentials

**Identity in AWS:**
```
arn:aws:iam::YOUR_ACCOUNT_ID:assumed-role/TeamA_BedrockDeveloper/<your-sso-username>
```
This ARN is what shows up in Bedrock invocation logs and CUR 2.0 `identity_line_item_id`.

---

#### `[profile teamB-demo]`
```ini
sso_account_id = YOUR_ACCOUNT_ID
sso_role_name = TeamB_Permission
region = us-west-2
```
Same structure as teamA-demo, different role: `TeamB_Permission`. Used to demonstrate multi-team isolation — TeamA and TeamB have different Bedrock permissions and their costs track separately in CUR.

---

#### `[profile idc-admin]`
```ini
sso_account_id = YOUR_ACCOUNT_ID
sso_role_name = AdministratorAccess
```
**What it is:** Full admin access to account `YOUR_ACCOUNT_ID` via SSO. No region specified (inherits CLI default).

**Use case:** Admin operations — enabling CUR 2.0, creating IAM roles, configuring Bedrock invocation logging, managing Budgets. Do NOT use this for day-to-day development — AdministratorAccess can do anything including deleting resources.

---

#### `[profile genai-research]`
```ini
sso_session = idc-admin       ← reuses idc-admin's SSO session token
sso_account_id = YOUR_ACCOUNT_ID
sso_role_name = GenAI-Research
region = us-west-2
```
**What it is:** Your primary GenAI research role — scoped to GenAI/Bedrock work specifically. Reuses the same SSO session as `idc-admin` (so one login covers both). Lower permissions than AdministratorAccess — just what you need for Bedrock API calls.

**This is likely the profile you use for Claude Code** — `genai-research` with the `GenAI-Research` role.

---

#### `[profile genai-apps]`
```ini
sso_session = User
sso_account_id = YOUR_ACCOUNT_ID
sso_role_name = GenAI-Apps
```
Uses a separate SSO session called `User`. Role `GenAI-Apps` — likely scoped to application deployment (Lambda, API Gateway, etc.) vs. raw model invocation.

---

#### `[profile claude-code]`
```ini
output = json
region = us-west-2
credential_process = isengardcli credentials --awscli YOUR_ACCOUNT_ID --role Admin --region us-west-2
```
**What it is:** Uses `isengardcli` — Amazon's internal CLI tool for Isengard accounts.

**How it works:**
1. `isengardcli credentials` is an Amazon-internal binary that knows how to fetch credentials for Isengard accounts
2. It authenticates using your Amazon Midway/Kerberos session (your laptop's corp auth)
3. Returns temporary Admin credentials for account `YOUR_ACCOUNT_ID`
4. These expire every hour or so — the credential_process is called automatically each time they expire

**Identity in AWS:**
```
arn:aws:iam::YOUR_ACCOUNT_ID:assumed-role/Admin/<your-amazon-alias>@amazon.com
```

**Important for Bedrock cost tracking:** Since this assumes the `Admin` role, all your Claude Code calls made under this profile show up in CUR as `assumed-role/Admin/your-alias@company.com`. If you and another SA both have `claude-code` profiles pointing at the same account with the same `Admin` role — your costs will appear separately because the session name (your email) differs.

---

### 4.3 Summary — Which Profile to Use When

| Task | Use This Profile | Why |
|---|---|---|
| Claude Code development | `claude-code` or `genai-research` | Scoped to what you need |
| Admin operations (enable CUR, IAM) | `idc-admin` | Full admin, use sparingly |
| Demo — Team A isolation | `teamA-demo` | Demonstrates per-team tracking |
| Demo — Team B isolation | `teamB-demo` | Demonstrates per-team tracking |
| Running scripts, automation | `genai-research` | Principle of least privilege |
| Fallback (no profile specified) | `default` | Uses static key — least secure |

### 4.4 Security Hygiene

```bash
# Check which identity you're currently using
aws sts get-caller-identity --profile genai-research
# Returns: Account, UserId, and your full ARN

# This is exactly what shows in CUR 2.0 identity_line_item_id
# So the ARN from get-caller-identity = what you see in cost reports
```

```bash
# Rotate static key (default profile) — good practice every 90 days
aws iam create-access-key --user-name <your-iam-username>
# Then update ~/.aws/credentials with new key
# Then delete old key:
aws iam delete-access-key --access-key-id OLD_KEY_ID
```
