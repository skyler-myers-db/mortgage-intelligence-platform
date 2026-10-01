---
name: w5b-integration-lessons
description: W5b (9-lane) integration traps the lanes could not see - dependency-batch holds proven only on the installed tree, prefix-replay Postgres suites, mid-test actor-gate resets, budget gates file split, zsh path modifier, hash-name brotli noise, repo plan vs orchestrator rebalance lane names
metadata:
  type: project
---

W5b was integrated on `ux/wave-5b` (2026-10-01). Lanes cannot `npm ci`, so a
dependency batch is only proven when the integrator installs it.

- Three bumps from the W5b batch were HELD after `npm ci` (dated rows in
  docs/audits/supply-chain-audit.md): TanStack Query 5.104.0 (the reduced-motion
  Console stayed `display: flex` one macrotask after Close, shell-wayfinding
  fixture, 3/3), react-virtual 3.14.13 (+2.28 KiB br on the LeadTable chunk),
  happy-dom 20.14.5 (setProperty runs with a `this` that is not `el.style`).
  **Why:** only the full fixture suite and the budget gate on the installed
  tree caught them; the first one surfaced only after a 37-minute full run.
  **How to apply:** bisect a dependency red by `npm ci` of the BASE lock into a
  scratch dir, swap `frontend/node_modules` for a symlink to it (move the
  private tree aside, never `rm -rf`, never point at the main checkout), add
  bumps one group at a time, rebuild, run the one failing spec.
- Prefix-replay real-PostgreSQL suites (`_SCHEMA[: index(marker)]`) must wrap
  `_apply` in `tests/fixtures/lakebase_contract_prefix.contract_as_of` once ANY
  later block adds triggers/routines; a lane that appends in parallel misses
  the sibling's sweep (HG x AA in W5b). Run the PG suites, not only unit tests.
- A vitest case that calls `_resetActorScopeForTests` mid-test restores the
  browser `resetDocument`; it must `_setResetDocumentForTests(vi.fn())` before a
  proven actor change, and model the NEW document afterwards (GV x IR).
- The budget numbers moved to `tools/frontend_budget_gates.mjs` (pure move) so
  the per-wave attribution comments never push the script past the 900-line cap.
- zsh: `git show $b:frontend/x` applies the `:f` modifier; write `"${b}:frontend/x"`.
- A CSS-only commit moved JS brotli (initial +0.06, total -0.36 KiB br) with JS
  raw bytes identical: hashed CSS asset names are embedded in JS chunks.
  **How to apply:** compare raw before attributing a br delta; A/B by building
  the parent from `git archive <sha> frontend tools design_files backend tests
  sql lakebase data` (tsc needs backend/resources and tests/fixtures JSON) with
  `frontend/node_modules` symlinked to the worktree's private tree.
- The repo's report 12.6 records the original 32-lane, four-batch plan; the
  orchestrator's rebalance (a fifth batch W5e, new lanes such as
  lead-queue-columns, growth-watchlists, deps-report, actor-binding) lives only
  in workflow-inputs `w5cd_rebalance.json` / `w5c_revision.json`.
  **How to apply:** route W5b-era deferrals under the repo plan's lane names
  and let the W5c merge restate re-homed owners; do not mix W5e labels into
  the docs until 12.6 itself is restated.

Related: [[project_ui_ux_audit_2026_09]], [[feedback_workflow_resume_prefix_cache]].
