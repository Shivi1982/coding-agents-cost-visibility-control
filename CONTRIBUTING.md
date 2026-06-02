# Contributing

Thank you for your interest in contributing to this project! Here's how to get started.

---

## Getting Started

1. **Fork the repository** on GitLab
2. **Clone your fork** locally:
   ```bash
   git clone git@ssh.gitlab.aws.dev:YOUR_USERNAME/coding-agents-cost-visibility-control.git
   cd coding-agents-cost-visibility-control
   ```
3. **Create a feature branch:**
   ```bash
   git checkout -b feature/your-feature-name
   ```
4. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

---

## Development Workflow

### Branch Naming Convention

- `feature/` — New features or enhancements
- `fix/` — Bug fixes
- `docs/` — Documentation updates
- `refactor/` — Code restructuring without behavior change

### Commit Messages

Follow conventional commit format:
```
feat: add per-developer cost breakdown to dashboard
fix: handle empty webhook payload gracefully
docs: update quickstart guide with CloudWatch Agent instructions
```

---

## Code Standards

- **Python 3.9+** — use type hints where practical
- **PEP 8** — standard Python formatting
- **Docstrings** — all public functions need docstrings
- **No hardcoded secrets** — use environment variables or AWS Secrets Manager
- **No hardcoded account IDs** — use variables or `os.environ`

---

## Testing

Run tests before submitting:
```bash
pytest lambda/test_anomaly.py -v
```

For local OTEL testing:
```bash
python otel/otel_receiver.py  # Terminal 1
# Configure Claude Code env vars in Terminal 2
```

---

## Submitting Changes

1. Push your branch to your fork
2. Create a Merge Request (MR) targeting `main`
3. Add a clear description of what changed and why
4. Wait for review (or self-merge if you have permissions)

---

## Reporting Issues

Open a GitLab Issue with:
- Clear title describing the problem
- Steps to reproduce
- Expected vs. actual behavior
- AWS region and Python version

---

## Questions?

Reach out to the maintainers or open a discussion on the GitLab repo.
