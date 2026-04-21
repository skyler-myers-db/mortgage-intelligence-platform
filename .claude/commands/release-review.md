---
description: Run a release-readiness review
allowed-tools: Bash(git status:*), Bash(git diff:*), Bash(npm --prefix frontend run build:*), Bash(pytest:*), Bash(python tools/verify_scaffold.py:*)
---

# release-review

Run a release-readiness review. Check tests, CI, bundle validation, mock mode, Databricks App config, secrets, Lakebase audit, and demo reliability.

Always apply the product anchors from `CLAUDE.md`. Always end with:

- Files to edit
- Tests/validation to run
- Risks
- Next action
