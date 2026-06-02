# GitLab Setup Instructions for Pipeline 3

Complete step-by-step guide for setting up your GitLab repo and testing Pipeline 3 end-to-end.

---

## Part 1: SSH Key Setup (One-Time)

### Why SSH?
GitLab on `gitlab.aws.dev` requires Midway-signed SSH keys for security. HTTPS and standard SSH both fail with 403 errors.

### Step 1: Generate ECDSA Key

```bash
ssh-keygen -t ecdsa -C "your-alias@company.com" -f ~/.ssh/id_ecdsa -N ""
```

You should see:
```
Your identification has been saved in ~/.ssh/id_ecdsa
Your public key has been saved in ~/.ssh/id_ecdsa.pub
```

### Step 2: Sign with Midway

If your security key has "On-Token PIN" enforced (most Amazon security keys do), use the `-f` flag:

```bash
mwinit -f -k ~/.ssh/id_ecdsa.pub
```

Follow the prompts:
1. Enter your Midway PIN
2. Touch your security key when prompted

Success = you see a certificate created at `~/.ssh/id_ecdsa-cert.pub`

### Step 3: Configure SSH

Add this to your `~/.ssh/config`:

```bash
echo "Host ssh.gitlab.aws.dev
    User git
    IdentityFile ~/.ssh/id_ecdsa
    CertificateFile ~/.ssh/id_ecdsa-cert.pub
    IdentitiesOnly yes
    ProxyCommand none
    ProxyJump none" >> ~/.ssh/config
```

### Step 4: Test SSH Connection

```bash
ssh -T ssh.gitlab.aws.dev
```

**Success** looks like:
```
Welcome to GitLab, @YOUR_USERNAME!
```

If this works, you're ready to clone.

---

## Part 2: Clone Your Repo

### Clone Command

Use the `ssh://` URL format (NOT `git@gitlab.aws.dev`):

```bash
git clone ssh://git@ssh.gitlab.aws.dev/YOUR_USERNAME/YOUR_REPO_NAME.git
cd claude-code-Cost_Control_ROI-demo
```

### Verify Clone

You should see:
```
Cloning into 'claude-code-Cost_Control_ROI-demo'...
remote: Enumerating objects: 3, done.
...
```

And your directory now contains a `README.md` file (the initial repo content).

---

## Part 3: Git Workflow for Testing Pipeline 3

### The Goal

1. Create a test branch
2. Make a commit
3. Push to GitLab
4. Create a Merge Request (MR)
5. Merge the MR
6. Watch the webhook fire → Lambda executes → CloudWatch metric recorded

### Step-by-Step

#### Step 1: Create a Test Branch

```bash
git checkout -b test-feature
```

This creates a new local branch called `test-feature` and switches to it.

#### Step 2: Make a Change

Edit an existing file or create a new one:

```bash
echo "# Feature branch test" >> test.md
```

#### Step 3: Stage the Change

```bash
git add test.md
```

#### Step 4: Commit the Change

```bash
git commit -m "test feature for pipeline 3"
```

You should see:
```
[test-feature abc1234] test feature for pipeline 3
 1 file changed, 1 insertion(+)
 create mode 100644 test.md
```

#### Step 5: Push to GitLab

```bash
git push origin test-feature
```

You should see:
```
To ssh://ssh.gitlab.aws.dev/YOUR_USERNAME/YOUR_REPO_NAME.git
 * [new branch]      test-feature -> test-feature
```

#### Step 6: Create a Merge Request (MR) on GitLab Web UI

1. Go to `https://gitlab.aws.dev/YOUR_USERNAME/YOUR_REPO_NAME`
2. You should see a blue banner: **"Create merge request"** button
3. Click it → fills in:
   - **From:** `test-feature`
   - **To:** `main`
4. Fill in title: `Test feature for Pipeline 3`
5. Click **"Create merge request"**

#### Step 7: Merge the MR

On the MR page:
1. Scroll to the green **"Merge"** button (bottom right)
2. Click **"Merge"**
3. Done — the branch is merged into `main`

**At this moment, the webhook fires:**
- GitLab sends a payload to your API Gateway endpoint
- Lambda processes it
- CloudWatch metric `PRsMerged` is incremented

#### Step 8: Check CloudWatch Metrics

Run this command to see if the metric was recorded:

```bash
aws cloudwatch get-metric-statistics \
  --namespace ClaudeCode/DevProductivity \
  --metric-name PRsMerged \
  --dimensions Name=Developer,Value=YOUR_USERNAME \
  --start-time $(date -u -v-10M +%Y-%m-%dT%H:%M:%S)Z \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S)Z \
  --period 300 \
  --statistics Sum \
  --region us-west-2
```

**Success** — you see output with `Sum: 1.0`:
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

## Part 4: Other Useful Git Commands

### Switch Branches

```bash
git checkout main
git checkout test-feature
```

### See Your Current Branch

```bash
git branch
```

The `*` shows which branch you're on.

### Push to a Different Branch

```bash
git push origin <branch-name>
```

### Delete a Local Branch

```bash
git branch -d <branch-name>
```

### Delete a Remote Branch

```bash
git push origin --delete <branch-name>
```

### See Commit History

```bash
git log --oneline
```

### Undo Uncommitted Changes

```bash
git checkout -- <file>
```

### Undo Last Commit (keep changes)

```bash
git reset --soft HEAD~1
```

---

## Part 5: Common Issues & Fixes

### "SSH Connection Failed / WSSH Proxy Error"

**Problem:** `WSSH Proxy returned an error with code 403`

**Fix:** Verify you're using the correct hostname in the clone URL:
- ❌ WRONG: `git@gitlab.aws.dev:...`
- ✅ CORRECT: `ssh://git@ssh.gitlab.aws.dev/...`

### "mwinit: command not found"

**Problem:** Midway CLI not found

**Fix:** It's Amazon-internal tooling. If you're on an Amazon machine and this fails, contact your IT team. It should be pre-installed on Amazon laptops.

### "Git Push Fails: Host Key Verification Failed"

**Problem:** First-time SSH connection — Git asks if you trust the host

**Fix:** Type `yes` and press Enter. It will add GitLab's key to `~/.ssh/known_hosts`.

### "Permission Denied (publickey)"

**Problem:** SSH key not being read

**Fix:** Make sure your SSH config points to the right key:
```bash
cat ~/.ssh/config | grep -A 5 "ssh.gitlab.aws.dev"
```

You should see `IdentityFile ~/.ssh/id_ecdsa` and `CertificateFile ~/.ssh/id_ecdsa-cert.pub`

---

## Part 6: Where to See Results

### CloudWatch Metrics Dashboard

Open CloudWatch in AWS Console:
```
https://console.aws.amazon.com/cloudwatch/
```

1. Left sidebar → **Metrics** → **Custom namespaces** → **ClaudeCode/DevProductivity**
2. Click on the **PRsMerged** metric
3. Filter by Developer = `YOUR_USERNAME`
4. View the graph — you should see a data point when your MR was merged

### Alternative: Query via AWS CLI

```bash
aws cloudwatch get-metric-statistics \
  --namespace ClaudeCode/DevProductivity \
  --metric-name PRsMerged \
  --dimensions Name=Developer,Value=YOUR_USERNAME \
  --start-time 2026-04-28T00:00:00Z \
  --end-time 2026-04-28T23:59:59Z \
  --period 3600 \
  --statistics Sum \
  --region us-west-2
```

### Log Streams

Lambda execution logs appear in CloudWatch Logs:
```
/aws/lambda/claude-code-roi-webhook
```

---

## Summary

**Full Pipeline 3 Test Flow:**
1. ✅ SSH key setup + Midway signing (Part 1)
2. ✅ Clone repo (Part 2)
3. ✅ Create branch → commit → push → MR → merge (Part 3)
4. ✅ Webhook fires automatically
5. ✅ Check CloudWatch metrics (Part 6)

**You now have end-to-end ROI tracking working!**
