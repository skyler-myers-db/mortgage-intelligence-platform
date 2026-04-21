---
description: Validate a UI route
allowed-tools: Bash(git status:*), Bash(git diff:*), Bash(npm --prefix frontend run build:*), Bash(pytest:*), Bash(python tools/verify_scaffold.py:*)
---

# validate-page

Validate a UI route. Use $ARGUMENTS as the route. Check visual fidelity, accessibility, evidence chips, approval gates, mock-data fallback, and API wiring. Include commands.

Always apply the product anchors from `CLAUDE.md`. Always end with:

- Files to edit
- Tests/validation to run
- Risks
- Next action
