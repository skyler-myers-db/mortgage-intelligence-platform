---
name: shared-checkout-branch-race
description: Concurrent subagents share the main checkout — another agent's `git checkout -b` retargets YOUR commits and their `git add -A` swallows your uncommitted edits. Work in a worktree.
metadata:
  type: project
---

The main checkout at `/Users/entrada-mac/repos/mortgage-intelligence-platform` is
**shared by every concurrently running subagent**. Git branch state is global to a
checkout, so a peer agent's `git checkout -b their-branch` silently moves YOUR
HEAD, and their `git add -A` / `git commit -a` sweeps YOUR uncommitted edits into
THEIR commit.

**Why:** observed 2026-08-08. I created `fix/ux-walk-frontend`, then a backend
agent ran `git checkout -b fix/ux-walk-backend` before my first commit. All five
of my next commits landed on *their* branch (`fix/ux-walk-frontend` never left
its creation SHA — its reflog had a single `branch: Created from origin/main`
entry), and two of their commits contained hunks of my half-finished
`analytics.sales-ops.tsx`, comments and all. Nothing errored; nothing warned.

**How to apply:**

1. **Do the work in a worktree from the start** when the task will span more than
   one commit: `git worktree add <scratchpad>/wt-<slug> <branch>`, then symlink
   node_modules per [[project-worktree-frontend-validation]]. Branch state there is
   yours alone.
2. **If you are already committing in the shared checkout**, run
   `git rev-parse --abbrev-ref HEAD` immediately before every commit, and always
   stage narrowly (`git add -A frontend/src`, never a bare `git add -A`) so you
   cannot pick up a peer's backend/SQL edits.
3. **Recovery when it has already happened** — the content is not lost, it is just
   on the wrong branch:
   - `git reflog show <your-branch>` and `git reflog` reveal exactly when HEAD was
     retargeted.
   - Check whether the peer's commits touched YOUR files
     (`git show <sha> -- <path>`); if the hunks are your text, their commit is a
     sweep, not a real change.
   - Rebuild your branch in a worktree by SNAPSHOTTING file state per commit
     (`git checkout <your-sha> -- <paths>` then commit) rather than
     `git cherry-pick` — a cherry-pick of your commit fails or mangles when part
     of the same file's change lives in the peer's earlier commit. Verify the
     reconstruction with `git diff <peer-head> <your-head> -- <paths>` (empty).
   - Restore the shared checkout afterwards (`git checkout -- <your paths>`) so
     the peer does not re-sweep the same edits.
4. **Tell the master agent** which of your commits are stranded on the peer's
   branch. Both branches will carry the same content, and whoever merges second
   needs to know why.
