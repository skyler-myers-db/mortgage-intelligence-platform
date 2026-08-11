---
name: shared-worktree-git-add
description: Never use `git add -A` in this repo — other agents edit and commit to the same branch in the same working tree concurrently
metadata:
  type: feedback
---

Stage explicit file paths, never `git add -A` / `git add .`, and never rewrite
branch history.

**Why:** This repo is worked by several agents at once in a *single shared
working tree* on a *single shared branch*. During the 2026-08-08 UX-walk
batch, `git add -A` swept another workstream's in-flight edits
(`backend/main.py`, `backend/schemas/growth_agent.py`,
`frontend/src/routes/analytics.sales-ops.tsx`) into two of my commits, and
that agent's own commits landed interleaved with mine on the same branch. A
`git status` snapshot taken at task start is stale within minutes.

**How to apply:**
- Stage by explicit path for every commit. Re-read `git status --short` right
  before staging; anything you did not edit belongs to someone else.
- Untracked files you did not create are someone else's WIP. Leave them. They
  will also show up in `ruff check` — scope lint to your files with
  `--exclude <their-file>` to confirm your own work is clean rather than
  "fixing" theirs.
- Do NOT `git reset`/rebase to clean up a sweep. Their commits are on the same
  branch; a rewrite destroys work. Report the muddled attribution instead.
- Expect the PR you open to contain other agents' commits. That is normal here,
  not a mistake to correct.
- `tests/fixtures/openapi_baseline.json` is a GENERATED shared file. Any Pydantic
  response-schema change forces `tools/regen_openapi_baseline.py`, and the regen
  absorbs whatever other agents' schema edits are live at that moment — on
  2026-08-11 an executive-provenance change swept in another agent's
  `SegmentSummary.contactable` / `StateRollup.contactable`. Regenerating twice is
  normal (their edit landed between my first regen and the contract test). Do not
  hand-edit the baseline to exclude their fields; the file must match the app.
  Just say in the report which foreign schemas the regen picked up.

**Rebuilding a contaminated branch** (the coordinator will ask for this):
1. `git worktree add -b <name>-clean <scratchpad>/wt origin/main` — work in an
   isolated checkout. The shared tree keeps churning under you otherwise; a
   third agent's edits appeared mid-run during the 2026-08-08 batch.
2. Replay YOUR files only:
   `git diff <sha>^ <sha> -- <explicit file list> > /tmp/p.diff && git apply --3way /tmp/p.diff`.
   Never pipe `git apply` output through `head` — SIGPIPE kills it mid-patch,
   it rolls back atomically, and the truncated stdout still shows
   "Applied patch … cleanly". Cost 20 minutes chasing a phantom.
   Note the shell is **zsh**: unquoted `$FILE_LIST` does not word-split. Wrap
   the loop in `/bin/bash -c '…'` or the `--` pathspec silently matches nothing.
3. Verify scope with `git diff origin/main...HEAD --name-only` before pushing.
4. Re-run the full suite in the worktree (`<repo>/.venv/bin/pytest` by absolute
   path, cwd = worktree).

Related: [[fake-lakebase-sql-substring-dispatch]]
