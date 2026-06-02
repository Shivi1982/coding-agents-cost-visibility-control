AWS PROFILES GUIDE
==================
Based on: aws configure list-profiles output
Profiles: default, ClineBedrock, teamA-demo, teamB-demo, idc-admin, genai-research, genai-apps, claude-code

What is an AWS Profile?
-----------------------
A profile is a named collection of settings (credentials + region + output format) stored in
~/.aws/config and ~/.aws/credentials. Instead of hardcoding credentials in every script or
command, you create named profiles and switch between them with --profile <name>.

  aws bedrock list-foundation-models --profile genai-research --region us-west-2
  aws s3 ls --profile teamA-demo

There are three types of credential mechanisms your profiles use:

  TYPE A — Static Keys (long-lived IAM User key)
  Stored in ~/.aws/credentials. Never expire. Least secure.

  TYPE B — SSO via IAM Identity Center (recommended)
  Browser-based login. Vends temporary credentials (expire in ~8h). Most secure for humans.
  Requires: aws sso login --profile <name>  before use

  TYPE C — Credential Process (external tool vends credentials)
  AWS CLI calls an external binary that returns JSON credentials.
  Used for: custom auth flows, IDE integrations, Isengard (Amazon internal).


YOUR 8 PROFILES IN DETAIL
==========================

1. default
----------
Type: A — Static IAM User Key
Source: ~/.aws/credentials [default]
Region: us-east-1

This is the fallback profile. Used when you run any AWS CLI command without --profile.
The access key here is a permanent IAM User credential — it doesn't expire on its own.
Keep this rotated (every 90 days). Use it only for quick ad-hoc work, not production scripts.

ARN pattern: arn:aws:iam::<account_id>:user/<iam_username>


2. ClineBedrock
---------------
Type: C — Credential Process
Region: us-east-1
Method: runs external binary "credential-process"

Used by the Cline VS Code extension to authenticate to Bedrock.
The external binary handles auth and returns temporary credentials in JSON format.
You don't manage these credentials directly — the process handles rotation.


3. teamA-demo
-------------
Type: B — SSO via IAM Identity Center
Account: YOUR_ACCOUNT_ID
Role: TeamA_BedrockDeveloper
Region: us-west-2
SSO Portal: https://d-9267e699f5.awsapps.com/start

One of two demo profiles for showing per-team Bedrock cost isolation.
TeamA_BedrockDeveloper role has Bedrock permissions scoped to Team A.

Login: aws sso login --profile teamA-demo
ARN pattern: arn:aws:iam::YOUR_ACCOUNT_ID:assumed-role/TeamA_BedrockDeveloper/<your-sso-email>

Use case: Demos showing that Team A's Bedrock costs are tracked separately from Team B.
In CUR 2.0: identity_line_item_id will show the TeamA_BedrockDeveloper assumed-role ARN.


4. teamB-demo
-------------
Type: B — SSO via IAM Identity Center
Account: YOUR_ACCOUNT_ID
Role: TeamB_Permission
Region: us-west-2

Mirror of teamA-demo with a different role. Used to demonstrate multi-team cost isolation.
Both teamA-demo and teamB-demo hit the same AWS account (YOUR_ACCOUNT_ID) but show as
different identities in billing because they assume different roles.

Login: aws sso login --profile teamB-demo
ARN pattern: arn:aws:iam::YOUR_ACCOUNT_ID:assumed-role/TeamB_Permission/<your-sso-email>


5. idc-admin
------------
Type: B — SSO via IAM Identity Center
Account: YOUR_ACCOUNT_ID
Role: AdministratorAccess
(No region set — uses CLI default)

Full admin access via SSO. Use for:
  - Enabling CUR 2.0 / IAM Principal Cost Attribution (must be payer account)
  - Creating/modifying IAM roles, SCPs
  - Configuring Bedrock invocation logging
  - Managing AWS Budgets and Cost Explorer settings

Do NOT use this for day-to-day development. AdministratorAccess can delete anything.

Login: aws sso login --profile idc-admin
ARN pattern: arn:aws:iam::YOUR_ACCOUNT_ID:assumed-role/AdministratorAccess/<your-sso-email>


6. genai-research
-----------------
Type: B — SSO via IAM Identity Center
Account: YOUR_ACCOUNT_ID
Role: GenAI-Research
Region: us-west-2
SSO Session: reuses idc-admin session (same browser login covers both)

Your PRIMARY profile for GenAI/Bedrock research work.
The GenAI-Research role is scoped specifically to what you need for Bedrock API calls,
model invocations, and related research — less privileged than AdministratorAccess.

Since it reuses the idc-admin SSO session: one aws sso login --profile idc-admin
covers both idc-admin and genai-research.

ARN pattern: arn:aws:iam::YOUR_ACCOUNT_ID:assumed-role/GenAI-Research/<your-sso-email>

For Claude Code: this is likely what shows in CUR when you use genai-research profile.
Your cost attribution: all Bedrock calls appear under the GenAI-Research role ARN.


7. genai-apps
-------------
Type: B — SSO via IAM Identity Center
Account: YOUR_ACCOUNT_ID
Role: GenAI-Apps
SSO Session: User (separate from idc-admin session)

Separate from genai-research — GenAI-Apps role is likely scoped to app deployment
(Lambda, API Gateway, S3, CloudFormation) rather than raw Bedrock invocations.
Uses its own SSO session ("User") — requires separate login.

Login: aws sso login --profile genai-apps


8. claude-code
--------------
Type: C — Credential Process (isengardcli)
Account: YOUR_ACCOUNT_ID
Role: Admin
Region: us-west-2
Output: json

Amazon-internal profile using isengardcli. isengardcli is Amazon's internal tool for
vending credentials to Isengard accounts using your corporate Midway/Kerberos session.

How it works:
  - AWS CLI calls: isengardcli credentials --awscli YOUR_ACCOUNT_ID --role Admin --region us-west-2
  - isengardcli uses your active Mwinit/Kerberos ticket to authenticate
  - Returns temporary Admin credentials (expire ~1h, auto-refreshed on next call)
  - No manual login needed IF your Mwinit session is active

ARN pattern: arn:aws:iam::YOUR_ACCOUNT_ID:assumed-role/Admin/<your-amazon-alias>@amazon.com

This is what Claude Code uses when ANTHROPIC_MODEL is set and Claude Code connects to
Bedrock using this profile. The Admin role gives full access to the account.

IMPORTANT: Even though both idc-admin and claude-code hit account YOUR_ACCOUNT_ID with
full admin, they appear as DIFFERENT identities in CUR 2.0 because:
  - idc-admin: assumed-role/AdministratorAccess/<email>
  - claude-code: assumed-role/Admin/<email>
Different role names = separate line items in billing.


HOW PROFILES DIFFER — QUICK COMPARISON
=======================================

Profile         Auth Type       Expires?    Account         Role                    Best For
---------       ---------       --------    -------         ----                    --------
default         Static Key      Never       Own IAM acct    IAM User (permanent)    Quick CLI, legacy
ClineBedrock    Cred Process    Yes (~1h)   Varies          Via ext. binary         Cline IDE
teamA-demo      SSO             Yes (~8h)   YOUR_ACCOUNT_ID    TeamA_BedrockDeveloper  Demo - team isolation
teamB-demo      SSO             Yes (~8h)   YOUR_ACCOUNT_ID    TeamB_Permission        Demo - team isolation
idc-admin       SSO             Yes (~8h)   YOUR_ACCOUNT_ID    AdministratorAccess     Admin ops (rare)
genai-research  SSO             Yes (~8h)   YOUR_ACCOUNT_ID    GenAI-Research          Primary dev work
genai-apps      SSO             Yes (~8h)   YOUR_ACCOUNT_ID    GenAI-Apps              App deployment
claude-code     Isengardcli     Yes (~1h)   YOUR_ACCOUNT_ID    Admin                   Claude Code on Bedrock


HOW SSO LOGIN WORKS (for SSO profiles)
=======================================

Step 1 — Log in once per session:
  aws sso login --profile genai-research

Step 2 — Browser opens → you authenticate → credentials cached locally
  Cached at: ~/.aws/sso/cache/<hash>.json

Step 3 — Use the profile normally:
  aws bedrock list-foundation-models --profile genai-research
  export AWS_PROFILE=genai-research   # set it globally for Claude Code

Step 4 — Check who you are:
  aws sts get-caller-identity --profile genai-research
  # Returns: Account, UserId, Arn
  # The Arn here is EXACTLY what appears in CUR 2.0 identity_line_item_id

SSO credentials expire after ~8 hours. Re-run aws sso login when they expire.
The credential cache file in ~/.aws/sso/cache/ is updated on each login.


HOW ISENGARDCLI WORKS (claude-code profile)
============================================

isengardcli uses your active Amazon Mwinit Kerberos ticket:
  mwinit -s   # refresh your Amazon corp session (do this daily)

Then the credential_process in [profile claude-code] runs automatically:
  isengardcli credentials --awscli YOUR_ACCOUNT_ID --role Admin --region us-west-2

This returns:
  {
    "Version": 1,
    "AccessKeyId": "ASIA...",
    "SecretAccessKey": "...",
    "SessionToken": "...",
    "Expiration": "2026-04-27T11:00:00Z"
  }

The AWS CLI / SDK uses these until they expire, then calls the process again.
No manual refresh needed as long as mwinit session is active.


WHAT SHOWS IN BEDROCK COST REPORTS
====================================

When each profile calls Bedrock, this is what appears in CUR 2.0:

Profile         identity_line_item_id
---------       ---------------------
default         arn:aws:iam::ACCT:user/<iam-username>
genai-research  arn:aws:iam::YOUR_ACCOUNT_ID:assumed-role/GenAI-Research/your-alias@company.com
claude-code     arn:aws:iam::YOUR_ACCOUNT_ID:assumed-role/Admin/your-alias@company.com
teamA-demo      arn:aws:iam::YOUR_ACCOUNT_ID:assumed-role/TeamA_BedrockDeveloper/your-alias@company.com
teamB-demo      arn:aws:iam::YOUR_ACCOUNT_ID:assumed-role/TeamB_Permission/your-alias@company.com
idc-admin       arn:aws:iam::YOUR_ACCOUNT_ID:assumed-role/AdministratorAccess/your-alias@company.com

This is the foundation of per-user Bedrock cost attribution.
Each role = separate cost line in billing. Each person assuming the same role
appears separately because the session name (email/alias) differs.
