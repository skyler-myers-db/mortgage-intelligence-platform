---
name: feedback-signoff-push-state
description: Don't report a stale git ahead-count / "N commits await push" — re-check git status immediately before writing, or omit push state
metadata:
  type: feedback
---

When closing out a task, do NOT report a git "ahead by N / N commits await your
push" figure captured earlier in the turn. The user (or a push) frequently
advances `origin/main` between when the snippet runs and when the report is
written, so the count goes stale and reads as wrong.

**Why:** Re-Audit #6 flagged this as a *recurring signoff hygiene defect — third
occurrence* (rounds 3, 4, and the r5 remediation each reported a stale
ahead-count; e.g. claimed "5 commits await your push" when `origin/main` was
already at HEAD, 0 ahead). The bash `git status -sb` was captured pre-push and
never re-checked before the signoff.

**How to apply:** Either (a) re-run `git status -sb` in the SAME message that
reports push state so the number is fresh, or (b) stop reporting push/ahead
state entirely and just say the work is committed on `main` and the push is
user-owned. Prefer (b) when unsure — the push is the user's to run regardless,
so the exact ahead-count rarely matters. Related: [[project_deploy_mechanics]].
