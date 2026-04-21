# Mortgage Intelligence Platform — Module 0 DAIS Demo

Databricks-native application for **Top-of-Funnel Lead Generation & Borrower Segmentation** built for the Entrada × Cotality × Databricks mortgage data estate story.

The DAIS MVP answers the lender's first question before LOS/CRM pipeline optimization matters:

> **Who should we contact, why now, and with what offer?**

The app uses Cotality public-record, lien, ownership, listing, permit, AVM, HPI, and mortgage market intelligence data through Databricks to build lead populations, score borrower opportunity, explain source evidence, draft next-best-offer outreach, and require human approval before action.

## Target demo flow

1. Open `/` and explain the full platform vision: Module 0 lead generation, Module 1 pipeline, Module 2 LO workbench, Module 3 underwriting, Module 4 risk/retention.
2. Go to `/portfolio-builder` and build a lead population from geography, occupancy, open lien, lender relationship, target product, and assumptions.
3. Go to `/segment-intelligence` and show the map + segment cards.
4. Go to `/lead-queue` and expand a high-scoring borrower.
5. Go to `/borrower-360/:id` and show CLIP, Owner Link, liens, equity, related properties, triggers, and evidence.
6. Go to `/offer-orchestrator/:id` and approve a human-in-the-loop action.
7. Go to `/ask-genie` and ask one curated question over Module 0 gold tables.

## Stack

- Frontend: React + Vite + TypeScript
- UI: CSS tokens from the design prototype, shadcn/Radix-ready structure
- Backend: FastAPI + Pydantic + Databricks SDK/SQL connector stubs
- Runtime: Databricks Apps
- Analytics: Unity Catalog Delta tables + SQL Warehouse
- Semantics: Unity Catalog metric views
- Conversational analytics: Genie App resource / Genie API wrapper
- Transactional state: Lakebase Postgres for campaigns, approvals, feedback, audit
- Agentic workflow: Agent Bricks/Supervisor roadmap + deterministic demo orchestrator
- Deployment: Databricks Declarative Automation Bundles
- Local mode: `MIP_MOCK_MODE=true`

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

## Bundle commands

```bash
databricks bundle validate -t dev
databricks bundle deploy -t dev
databricks bundle run refresh_silver -t dev
```

Fill the environment-specific variables in `databricks.yml` or set them through your Databricks CLI profile.

## Agentic coding setup

Start Claude Code from the repo root and run:

```text
/init
/memory
/agents
/mcp
/plan-sprint Build the Module 0 DAIS demo in vertical slices
```

Claude should read `CLAUDE.md`, `AGENTS.md`, `.claude/skills/*/SKILL.md`, and `.claude/commands/*.md`. Keep the master agent focused on orchestration and delegate file-heavy work to the project subagents.

## Scope guardrails

Build for DAIS first:

- Fast, polished, explainable demo UI.
- Precomputed gold-table demo path.
- Deterministic scoring and offer rules.
- Synthetic borrower PII only.
- No automatic outreach.
- No real credit data.
- No production Encompass/MSP connector implementation before Module 0 is demo-stable.

## Repo map

See `docs/implementation-plan.md` and `docs/agentic-workflow.md` for the full build plan and Claude Code operating model.
