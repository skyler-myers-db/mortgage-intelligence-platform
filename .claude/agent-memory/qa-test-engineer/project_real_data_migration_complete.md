---
name: Module 0 real-data migration complete (Slice 9 closed 2026-04-21)
description: 10-slice migration from mock-only Module 0 to live UC landed; CI + nightly + runbook + talk-track lint now gate future edits.
type: project
---

The `feature/module0-real-data` branch closed Slice 9 on 2026-04-21. Ten
commits total; PR rollup to `main` was the next recommended step at the
time of completion.

**Why:** DAIS 2026 booth requires live Unity Catalog + Cotality Delta Share
(no mock fallback in deployed app). CI nightly + docs/runbook.md + talk-
track word-count gate + e2e against real UC together prevent silent drift.

**How to apply:** When working on Module 0 post-migration, the mock path
only exists in `tests/fixtures/` — never in `backend/`. Adding a test
fixture to `backend/` is a layering regression. CI gates are:
- `backend-tests` / `frontend-tests` (PRs, credential-free)
- `bundle-validate` (PRs, placeholder BUNDLE_VARs)
- `talk-track-lint` (PRs, enforces `[1000, 1500]` spoken words in
  `docs/module0-talk-track.md`)
- `playwright-e2e-offline` (PRs, in-process repos)
- `parity-live` + `playwright-e2e-live` (nightly, real UC; fails loud
  if secrets missing)

Key gated spec: `tests/e2e/real_data.spec.ts` runs only when `E2E_LIVE=1`.
Required secrets documented in `.github/workflows/README.md`.

Historical Atlanta/GA residuals were fully swept in Slice 9; Chicago/IL
is the anchor metro end-to-end (mock_population, demoData, MapPlaceholder,
genie_answers, portfolio-builder, segment-intelligence, config.py).
