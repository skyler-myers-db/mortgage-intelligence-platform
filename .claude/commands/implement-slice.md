---
description: Implement one narrow vertical slice
allowed-tools: Bash(git status:*), Bash(git diff:*), Bash(npm --prefix frontend run build:*), Bash(pytest:*), Bash(python tools/verify_scaffold.py:*)
---

# implement-slice

Implement one narrow vertical slice. Use $ARGUMENTS as the slice. First inspect existing files, then propose exact edits, then code, then run tests. Do not broaden scope.

Always apply the product anchors from `CLAUDE.md`. Always end with:

- Files to edit
- Tests/validation to run
- Risks
- Next action
