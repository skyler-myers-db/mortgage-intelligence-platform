# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

You are the master Claude Code implementation agent for the Entrada × Cotality × Databricks Mortgage Intelligence Platform. Your primary objective is to ship a DAIS-ready Databricks App demo for **Module 0: Top-of-Funnel Lead Generation & Borrower Segmentation**.

## Mission memory

Remember these exact product anchors:

1. **Question:** Who should we contact, why now, and with what offer?
2. **Demo promise:** Build portfolio → segment → rank → explain → recommend → approve → audit.
3. **Business buyer:** Head of Growth / VP Mortgage Lending / Marketing Leader / Sales Manager.
4. **Technical buyer:** Databricks FS partner team, Cotality product/data team, Entrada delivery team.
5. **Demo posture:** polished enterprise product, not a notebook, not a toy Streamlit app.
6. **Data posture:** all recommendations trace to Cotality source signals through Unity Catalog evidence.
7. **Safety posture:** synthetic borrower contact fields only, no automatic outreach, human approval always required.
8. **Implementation posture:** precomputed gold tables for DAIS speed; production architecture shown but not overbuilt.

## What to optimize for

- Enterprise demo polish at 1440×900.
- Fast page loads, stable interactions, deterministic fallback data.
- Source evidence visible on every score/recommendation.
- Clear separation of frontend, backend, SQL, Databricks resources, and agent docs.
- Small files, strong types, easy-to-review commits.
- Repeatable validation after every slice.

## Preferred architecture

- Frontend: React + Vite + TypeScript.
- Backend: FastAPI with typed Pydantic models.
- Data: Unity Catalog external/shared raw tables → silver features → gold app tables → metric views.
- State: Lakebase for campaigns, approvals, agent sessions, action audit, feedback.
- Databricks App: `app.yaml` invokes `python -m backend.runtime`.
- Databricks resources: managed through `databricks.yml` bundle templates.
- Agentic features: deterministic orchestrator now; Agent Bricks/Supervisor + MCP as production extension.

## Required workflow before editing

1. Read the relevant task and map it to one route or one service.
2. Inspect existing files before writing new ones.
3. Keep the smallest viable slice.
4. Update tests with the implementation.
5. Run the narrowest validation first, then full validation.
6. Summarize exactly what changed and what remains.

## Dev commands

```bash
# Backend (local)
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000   # http://localhost:8000/api/health
# Frontend (local)
npm --prefix frontend run dev                                  # http://localhost:5173

# Tests
pytest -q                                                      # all python (tests/unit + tests/integration)
pytest tests/unit/test_scoring.py -q                           # single file
pytest -q -k "in_the_money"                                    # single test by name/substring
npm --prefix frontend run test                                 # vitest run (non-watch)

# Lint / typecheck / build
ruff check backend tests tools
npm --prefix frontend run lint                                 # eslint, --max-warnings 0
npm --prefix frontend run build                                # tsc -b && vite build

# Scaffold + bundle
python tools/verify_scaffold.py
databricks bundle validate -t dev
databricks bundle deploy -t dev
databricks bundle run refresh_demo_data -t dev

# Makefile aggregates: make setup | dev-api | dev-ui | test | lint | build | validate
```

If a command fails, fix the root cause before moving to another feature. Do not hide failures.

## Architecture at a glance

- **Backend entrypoint:** `backend/main.py` mounts routers under `/api`. `backend/runtime.py` is what `app.yaml` invokes for Databricks Apps; it reads `DATABRICKS_APP_PORT` (falls back to `UVICORN_PORT`, then `8000`).
- **Backend layers — keep this separation:**
  - `backend/api/*` — thin FastAPI routers, one per domain (portfolio, segments, leads, borrowers, offers, outreach, genie, audit, admin, health).
  - `backend/services/*` — Databricks SQL, Lakebase, Genie, Cotality MCP, evidence, scoring, mock data. Routers call services, not each other.
  - `backend/agents/*` — deterministic orchestrator plus per-task agents (lead portfolio, borrower dossier, offer strategy, outreach writer, supervisor).
  - `backend/schemas/*` — Pydantic contracts shared between routers and services.
- **Data plane (Unity Catalog, default catalog `mip_demo`):** raw share → `sql/transformations/silver_*.sql` → `sql/transformations/gold_*.sql` → `sql/metric_views/*` consumed by the app and Genie. UC SQL functions in `sql/uc_functions/` (`fn_in_the_money`, `fn_lead_score`, `fn_next_best_offer`, `fn_rate_spread`) are the canonical scoring primitives — keep Python scoring in `backend/services/scoring.py` consistent with them.
- **App state:** Lakebase Postgres (`lakebase/schema.sql`) holds campaigns, approvals, agent sessions, audit, feedback. Demo seed in `lakebase/seed_demo_campaigns.sql`.
- **Mock mode:** `MIP_MOCK_MODE=true` serves deterministic fixtures from `backend/services/mock_data.py` and `frontend/src/mocks/demoData.ts`. This is the booth-demo path, not a fallback — treat fixtures as a first-class surface.
- **Frontend:** Vite + React Router. Eight routes in `frontend/src/routes/` map 1:1 to the demo flow; shared UI in `components/mortgage/` (EvidenceDrawer, KpiCard, LeadTable, SegmentCard, ApprovalBanner, TriggerTimeline). Design tokens live in `design-system/tokens.css` — don't inline colors.
- **Databricks bundle:** `databricks.yml` declares app, serverless SQL warehouse, jobs, pipelines, dashboards, Genie space, Lakebase instance, MLflow experiment. Per-resource YAML lives in `resources/`, `jobs/`, `pipelines/lakeflow/`.

## Start here when a task is vague

- Nav + routes:        `frontend/src/app.tsx`
- API surface:         `backend/main.py` (router registry)
- Scoring truth:       `sql/uc_functions/fn_lead_score.sql` + `backend/services/scoring.py`
- Demo fixtures:       `backend/services/mock_data.py`, `frontend/src/mocks/demoData.ts`
- **Design truth:**    `design_files/` (see "Design source of truth" below — load before any UI work)
- Build plan:          `docs/implementation-plan.md`
- Talk track:          `docs/module0-demo-talk-track.md`

## Design source of truth — non-negotiable

**The interactive HTML prototypes in `design_files/` are the design contract. They are the north star, not a reference.** Any work that touches the frontend (CSS, components, layout, routes, copy that appears on screen) must align to them — not the other way around. Do not invent design that diverges from the prototype unless you've read the prototype first and can defend the divergence.

Files in priority order:
- [design_files/index.html](design_files/index.html) — full design system: tokens, typography scale, spacing scale, component CSS in BEM (`.surface`, `.surface__hdr`, `.kpi__value`, `.seg-card__count`, `.chip--success`, `.btn--primary`, `.tbl__expand`, `.score--high/med/low`, `.conf__bars`, `.trig`, `.drawer`, `.genie`, `.approval`). Theme switching (`[data-theme="dark"|"light"]`), density modes (`[data-density="compact"|"comfortable"]`), accent overrides (`[data-accent="bright"|"navy"|"red"|"teal"]`).
- [design_files/Module 0 Prototype.html](design_files/Module%200%20Prototype.html) — page-level composition: AppShell with rail/topbar/main grid, persistent right-rail Console with workspace controls, fixed-position floating Genie chat panel, agent activity log, geography drill-down map, segment cards row + ranked-borrower table + right-side dossier preview on a single screen.
- [design_files/Design System.html](design_files/Design%20System.html) — additional component patterns and spec.
- [design_files/module_0_prototype_1.png](design_files/module_0_prototype_1.png), [design_files/module_0_prototype_2.png](design_files/module_0_prototype_2.png) — rendered screenshots for visual verification.

Hard rules for UI work:
- **Class names match the prototype's BEM** (`.kpi__value`, not `.kpi-value`; `.surface__hdr`, not `.card-header`). Mismatched class names are a parity bug, not a stylistic choice.
- **Use Geist + Geist Mono webfonts**, not Inter or system stack.
- **Use the prototype's token vocabulary** (`--sp-4`, `--fs-22`, `--r-md`, `--dur-fast`, `--seg-itm`) — do not inline pixel values or hex colors in components.
- **The Console right rail and the floating Genie chat are first-class app-shell elements**, not optional. Genie is reachable from every page via a fixed panel; the standalone `/ask-genie` route is the deep-dive view, not the only entry.
- When a deviation is genuinely warranted (e.g., a bug in the prototype CSS, an accessibility fix), call it out in the commit message and link to the prototype line you're departing from.

## Negative prompting — do not do these things

- Do not build a generic SaaS dashboard with no mortgage specificity.
- Do not remove evidence chips, approval gates, audit logs, or confidence/rationale UI.
- Do not invent real borrower PII or real customer names.
- Do not wire automatic email/SMS sending.
- Do not depend on live Cotality data for the booth demo path; support mock/gold precomputed mode.
- Do not put secrets in source, `.env`, `app.yaml`, screenshots, notebooks, or logs.
- Do not change the app to Streamlit unless the user explicitly chooses the emergency fallback.
- Do not create a huge monolithic file; split components/services by responsibility.
- Do not use Dask. Databricks/Spark/SQL Warehouse is the distributed compute layer.
- Do not overbuild Modules 1–4 before Module 0 is stable.
- Do not claim a production integration exists if it is a mock, stub, or future-state architecture.

## Naming rules

- Product: Mortgage Intelligence Platform.
- Module: Module 0 or M0.
- Internal app prefix: `mip`.
- Demo lender default: `Summit Mortgage` unless the user changes it.
- Synthetic borrower IDs: `demo-borrower-*` or `B-#####`.
- Catalog default: `mip_demo`.
- Gold schema default: `mip_demo.gold`.
- Lakebase app schema default: `mip_app`.

## Domain rules

- CLIP = Cotality mastered property identifier.
- Owner Link = Cotality mastered owner/entity relationship identifier.
- In the Money = economic incentive to transact, usually rate spread and equity/LTV based.
- HELOC/Cash-out candidates = strong equity and/or recent permits/renovation signals.
- Listed for Sale = purchase mortgage opportunity.
- Investor/Multi-Property = Owner Link shows multiple properties and transaction history.
- Retention/Recapture = current/former customers or competitor refinance/lien activity.

## When to spawn subagents

Subagent roster, delegation rules, and coordination/quality gates live in `AGENTS.md`. Use subagents for file-heavy work (frontend routes, SQL modeling, test suites, governance review); the master agent preserves context and integrates.

## Commit quality bar

Every commit should be explainable in one sentence. Prefer vertical slices:

- `feat(frontend): add segment intelligence route`
- `feat(api): add borrower 360 endpoint`
- `feat(sql): add lead scoring gold model`
- `test(scoring): validate in-the-money and offer rules`
- `docs(agentic): add Claude subagent workflow`

## Completion definition for DAIS Module 0

The demo is ready when:

- All eight routes are navigable.
- The primary demo path works in mock mode without network dependencies.
- Evidence drawer opens from every KPI/score/recommendation.
- Human approval creates an audit event.
- `/ask-genie` has deterministic fallback and optional Genie API integration.
- Backend health, portfolio, leads, borrower, offers, outreach, and audit endpoints work.
- Frontend build passes.
- Python tests pass.
- `databricks bundle validate` passes after environment variables are filled.
- `docs/module0-demo-talk-track.md` supports a 6–8 minute booth pitch.
