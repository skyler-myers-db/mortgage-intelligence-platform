---
name: feedback-load-the-built-app
description: Always load the built app and look at the page after a UI behavior change — green tests do not cover on-screen copy that promises the old behavior.
metadata:
  type: feedback
---

After changing how a UI flow *works*, build the app and actually look at the
page before pushing. Grep the rendered `innerText` for copy describing the
old behavior.

**Why:** 2026-08-08, PR #182 changed the map drill from state→county→ZIP to
state→ZIP. Lint, `tsc`, and 1012 unit tests were all green — and the home
page still headlined **"State → county → ZIP → borrower"**, advertising a
level that no longer existed. No assertion pinned that string, so nothing in
the suite could have caught it. Loading the built dist and reading the DOM
found it in under a minute. Section headers, eyebrows, empty-state copy, and
`aria-label`s are the usual offenders: they describe behavior but are rarely
asserted.

**How to apply:**

- Cheap recipe (no backend needed, per
  [[project-worktree-frontend-validation]]): `npm --prefix frontend run build`
  (a required validation anyway), serve it —
  `python3 -m http.server <port> --directory frontend/dist` — then
  `preview_start` with that **`url`** (the `url` form is fine; it is the
  dev-server `name` form that serves the wrong checkout).
- API calls 404, which is itself useful: it exercises the degraded-state UI.
  Distinguish expected 404/501s from real JS errors in the console.
- Then assert on the DOM, don't just eyeball a screenshot:
  `javascript_tool` with a regex over `document.body.innerText` for the old
  vocabulary, plus counts of the elements that should no longer exist
  (e.g. `document.querySelectorAll('path[aria-label$="County"]').length === 0`).
- When you find such copy, fix it in the same PR and say in the commit
  message that tests could not have caught it.
