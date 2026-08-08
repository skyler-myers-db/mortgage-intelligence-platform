---
name: css-budget-headroom-is-thin
description: The frontend initial-CSS budget passes but sits within ~1 KiB raw / ~0.1 KiB gzip of its limit — measure your delta before adding CSS.
metadata:
  type: project
---

**Superseded the 2026-07-07 "budget is red on main" note.** As of **2026-08-06** `npm --prefix frontend run budget` **passes**. The limits in `tools/check_frontend_budgets.mjs` were re-baselined (`initialCssBytes: 148 * KiB`, `initialCssGzipBytes: 25 * KiB`).

Measured on a clean tree that day: **initial CSS 144.41 KiB raw / 24.46 KiB gzip**. That leaves roughly **3.5 KiB raw and 0.5 KiB gzip** of total headroom for everything still to land — thin enough that two ordinary slices can breach it.

**Why:** the gate is real and the margin is small; a slice that adds 60 lines of component CSS can eat the entire remaining gzip allowance on its own.

**How to apply:**
1. Before adding CSS, note the current numbers. After, run `npm --prefix frontend run build && npm --prefix frontend run budget` and report your delta explicitly.
2. To measure YOUR delta specifically, `git stash push -- frontend/src/design-system/components.css`, rebuild, read the numbers, then `git stash pop`. Scope the stash to that one path — **this repo's working tree often carries other agents' in-progress backend/SQL changes**, and a bare `git stash` would sweep them up.
3. Reuse existing BEM primitives instead of authoring new blocks — see [[reuse-later-declared-css-primitives]] for the cascade trap that comes with this. Reusing `.filter-menu` / `.filter-menu__item` for a new dropdown cut ~0.6 KiB raw off one slice.
4. Never self-bump the budget to go green. The policy comment in the budget file says so, and re-baselining is a cross-cutting call the master agent owns. Report the number and let them decide. Related: [[feedback-design-contract]].
