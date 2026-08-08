---
name: project-worktree-frontend-validation
description: How to run frontend tests and see the app in a browser from a .claude/worktrees checkout — node_modules symlink, and why preview_start serves the wrong checkout.
metadata:
  type: project
---

A `.claude/worktrees/<agent-id>` checkout has **no `frontend/node_modules`**, and the
usual dev-server tooling silently serves the MAIN checkout instead of the worktree.

**Why:** git worktrees only carry tracked files, and `node_modules` is ignored.
`preview_start` (and `npm --prefix <abs-path> run dev`) resolves the dev server
against the repo root the tool launched from, not the worktree — so the served
bundle looks stale and your edits appear to do nothing. Confirmed 2026-08-07: the
served sourcemap's `file:` field pointed at the main checkout while the edits sat
in the worktree.

**How to apply:**

1. Tests / lint / build — symlink node_modules first (diff `package-lock.json`
   against the main checkout; identical when you branched off `origin/main`):
   `ln -s <main-repo>/frontend/node_modules <worktree>/frontend/node_modules`,
   then `npm --prefix frontend run test|lint|build` behaves normally. Remove the
   symlink before you finish — `.gitignore` has `frontend/node_modules/` **with a
   trailing slash**, which does not match a symlink, so it shows as untracked.
2. Visual / browser verification — don't fight the dev server. Run
   `npm --prefix frontend run build` (a required validation anyway) and serve it:
   `python3 -m http.server 5174 --directory <worktree>/frontend/dist`, then point
   the browser pane at `localhost:5174`. API calls 404 without a backend, but the
   AppShell, CSS, and layout render — enough to measure geometry via
   `javascript_tool` + `getBoundingClientRect`.
3. Running vite directly from the worktree dies on
   `Cannot find package 'babel-plugin-react-compiler'`: the react-compiler babel
   preset resolves from the process CWD, so it needs a node_modules beside it.
   Same root cause as the `npx --root` trap in [[project-component-test-context-mock]].

Measuring in a real browser is worth the setup: the 2026-08-07 topbar-overlap fix
was sized from measured track widths (296/704/296 → 392/512/392), not guessed.
