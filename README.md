# Mortgage Intelligence Platform — Module 0

Databricks-native application for **Top-of-Funnel Lead Generation & Borrower Segmentation** built for the Entrada × Cotality × Databricks mortgage data estate story.

Module 0 answers the lender's first question before LOS/CRM pipeline optimization matters:

> **Who should we contact, why now, and with what offer?**

The app uses live Cotality public-record, lien, ownership, valuation, MLS listing, HELOC propensity, refi propensity, and mortgage market intelligence data through Databricks to build lead populations, score borrower opportunity, explain source evidence, draft next-best-offer outreach, and require human approval before action. Filed Building Permits remain an explicit pending source-readiness gap; the app does not infer permit filings from HELOC propensity.

## Product flow

1. Open `/` and explain the full platform vision: Module 0 lead generation, Module 1 pipeline, Module 2 LO workbench, Module 3 underwriting, Module 4 risk/retention.
2. Go to `/portfolio-builder` and build a lead population from geography, occupancy, open lien, lender relationship, target product, and assumptions.
3. Go to `/analytics` for the in-app executive, geography, economics,
   segment, and signal-mix views. This is the primary user-facing analytics
   surface; app users do not need Lakeview, SQL Warehouse, Data Explorer, or
   other Databricks workspace UI access. Access to the app itself still follows
   Databricks Apps sharing/SSO until an external auth front door exists.
4. Go to `/segment-intelligence` and show the map + segment cards.
5. Go to `/lead-queue` and expand a high-scoring borrower.
6. Go to `/borrower-360/:id` and show masked property/owner refs, liens, equity, related properties, triggers, and evidence.
7. Go to `/offer-orchestrator/:id` and approve a human-in-the-loop action.
8. Go to `/ask-genie` and ask one curated question over Module 0 gold tables.

## Stack

- Frontend: React + Vite + TypeScript
- UI: CSS tokens from the design prototype, shadcn/Radix-ready structure
- Backend: FastAPI + Pydantic + Databricks SDK and SQL connector (live Unity Catalog)
- Runtime: Databricks Apps
- Analytics: in-app React analytics route backed by typed FastAPI responses
  over Unity Catalog Delta tables + SQL Warehouse. Databricks AI/BI dashboards
  remain an operator/admin companion, not the required user path.
- Semantics: Unity Catalog metric views
- Conversational analytics: Genie App resource / Genie API wrapper
- Transactional state: Lakebase Postgres for campaigns, approvals, feedback, audit
- Agentic workflow: governed action orchestrator for demo-safe workflows; Agent Bricks/Supervisor available as an optional production extension
- Deployment: Databricks Declarative Automation Bundles using the direct deployment engine

## Quick start locally

```bash
cp .env.example .env.local
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
npm --prefix frontend install
npm --prefix frontend run dev
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
```

Frontend: <http://localhost:5173>  
Backend: <http://localhost:8000/api/v1/health>

The canonical API surface is `/api/v1/*`; legacy `/api/*` routes are
deprecated compatibility aliases during the Module 0 transition window. API
responses emit `X-API-Version: v1`.

## Build and run as a Databricks App

```bash
npm --prefix frontend ci
npm --prefix frontend run build
python -m backend.runtime
```

`app.yaml` runs `python -m backend.runtime`. That module reads `DATABRICKS_APP_PORT`/`UVICORN_PORT` if Databricks injects one, otherwise it uses port `8000` locally.

## Databricks deploy

For a customer fork, first rebind the bundle's single workspace-host anchor
and set the lender identity in `.env.local`:

```bash
./scripts/configure-workspace.sh https://<customer-workspace>.cloud.databricks.com
MIP_LENDER_NAME="Acme Mortgage"
# Optional; defaults from MIP_LENDER_NAME when unset.
MIP_TENANT_ID="acme_mortgage"
```

```bash
make deploy-dev
```

`make deploy-dev` runs `scripts/deploy.sh`: build, env-aware direct bundle
validate/plan/deploy, app snapshot promotion, refresh jobs, Genie rebinding,
and smoke checks. `make bundle-validate` and `make bundle-plan` are safe
read-only diagnostics because they run `tools/databricks/bundle_env.py`.
`make bundle-deploy` is a lower-level resource-recovery command: it requires
the command-of-record deployment to have created the pipeline namespace first.

After the command-of-record first install has created the governed UC
namespace, the Entrada dev target also supports the plain Databricks bundle
resource-recovery path:

```bash
databricks bundle deploy -t dev --profile DEFAULT
```

That path is resource-only; run `databricks apps deploy mip-app --mode SNAPSHOT`
afterward if you need to promote a freshly built app snapshot without running the
full deploy script.

## Documentation map

- [`docs/se-onboarding.md`](docs/se-onboarding.md) — customer SE deployment
  checklist, tenancy posture, first-boot verification, and handover.
- [`docs/deployment.md`](docs/deployment.md) — local, app, bundle, OTLP, and
  release-checklist details.
- [`docs/runbook.md`](docs/runbook.md) — operator diagnostics for cold starts,
  dependency breakers, caches, auth, Lakebase, Genie, and geo/footprint issues.
- [`docs/disaster-recovery.md`](docs/disaster-recovery.md) — Lakebase PITR,
  gold rebuild, app rollback, Genie re-provisioning, audit archival, RTO/RPO.
- [`docs/runbook-multi-catalog.md`](docs/runbook-multi-catalog.md) —
  non-default catalog deployment and `MIP_DEFAULT_CATALOG` workflow.
- [`docs/load-baseline.md`](docs/load-baseline.md) — committed read/write load
  baseline and how to rerun `tools/load_test/`.
- [`docs/security-and-compliance.md`](docs/security-and-compliance.md) and
  [`SECURITY.md`](SECURITY.md) — security controls and disclosure process.
- [`CHANGELOG.md`](CHANGELOG.md) — release notes and API/operator-visible
  changes.
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — PR workflow, CI gates, API/data/Genie
  change procedure, and release policy.

## Agentic coding setup

Start Claude Code from the repo root and run:

```text
/init
/memory
/agents
/mcp
/plan-sprint Build the Module 0 product in vertical slices
```

Claude should read `CLAUDE.md`, `AGENTS.md`, `.claude/skills/*/SKILL.md`, and `.claude/commands/*.md`. Keep the master agent focused on orchestration and delegate file-heavy work to the project subagents.

## Scope guardrails

Build for Module 0 first:

- Fast, polished, explainable UI.
- Precomputed gold-table runtime path.
- Deterministic scoring and offer rules.
- Synthetic borrower PII only.
- No automatic outreach.
- No real credit data.
- No production Encompass/MSP connector implementation before Module 0 is stable.

## Repo map

See `docs/implementation-plan.md` and `docs/agentic-workflow.md` for the full build plan and Claude Code operating model.
