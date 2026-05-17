# Mortgage Intelligence Platform — Module 0

Databricks-native application for **Top-of-Funnel Lead Generation & Borrower Segmentation** built for the Entrada × Cotality × Databricks mortgage data estate story.

Module 0 answers the lender's first question before LOS/CRM pipeline optimization matters:

> **Who should we contact, why now, and with what offer?**

The app uses live Cotality public-record, lien, ownership, valuation, and mortgage market intelligence data through Databricks to build lead populations, score borrower opportunity, explain source evidence, draft next-best-offer outreach, and require human approval before action. MLS listing and Building Permit overlays are visible as blocked/pending segments until Cotality shares those Delta Share products.

## Product flow

1. Open `/` and explain the full platform vision: Module 0 lead generation, Module 1 pipeline, Module 2 LO workbench, Module 3 underwriting, Module 4 risk/retention.
2. Go to `/portfolio-builder` and build a lead population from geography, occupancy, open lien, lender relationship, target product, and assumptions.
3. Go to `/segment-intelligence` and show the map + segment cards.
4. Go to `/lead-queue` and expand a high-scoring borrower.
5. Go to `/borrower-360/:id` and show masked property/owner refs, liens, equity, related properties, triggers, and evidence.
6. Go to `/offer-orchestrator/:id` and approve a human-in-the-loop action.
7. Go to `/ask-genie` and ask one curated question over Module 0 gold tables.

## Stack

- Frontend: React + Vite + TypeScript
- UI: CSS tokens from the design prototype, shadcn/Radix-ready structure
- Backend: FastAPI + Pydantic + Databricks SDK and SQL connector (live Unity Catalog)
- Runtime: Databricks Apps
- Analytics: Unity Catalog Delta tables + SQL Warehouse
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
Backend: <http://localhost:8000/api/health>

## Build and run as a Databricks App

```bash
npm --prefix frontend ci
npm --prefix frontend run build
python -m backend.runtime
```

`app.yaml` runs `python -m backend.runtime`. That module reads `DATABRICKS_APP_PORT`/`UVICORN_PORT` if Databricks injects one, otherwise it uses port `8000` locally.

## Databricks deploy

For a customer fork, first rebind the bundle's single workspace-host anchor:

```bash
./scripts/configure-workspace.sh https://<customer-workspace>.cloud.databricks.com
```

```bash
make deploy-dev
```

`make deploy-dev` runs `scripts/deploy.sh`: build, env-aware direct bundle
validate/plan/deploy, app snapshot promotion, refresh jobs, Genie rebinding,
and smoke checks. For narrow resource-only recovery, `make bundle-validate`,
`make bundle-plan`, and `make bundle-deploy` are safe because they run
`tools/databricks/bundle_env.py`.

The Entrada dev target also supports the plain Databricks bundle path:

```bash
databricks bundle deploy -t dev --profile DEFAULT
```

That path is resource-only; run `databricks apps deploy mip-app --mode SNAPSHOT`
afterward if you need to promote a freshly built app snapshot without running the
full deploy script.

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
