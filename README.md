# Executive recommendation

Build **Module 0 first** as the DAIS-ready product demo: **Top-of-Funnel Lead Generation & Borrower Segmentation**. The demo should show a lender how to create a marketable lead population from Cotality public-record and mortgage intelligence data, identify the best borrower opportunities, explain *why now*, recommend *what offer*, and prepare a human-approved outreach action. This is the right first slice because Cotality confirmed it is valuable, demoable with public-record data, and does not require real LOS, CRM, servicing, Encompass, or MSP data to be convincing.

The full Mortgage Intelligence Platform spec defines a modular Databricks-native application across pipeline command center, loan officer workbench, underwriting copilot, and portfolio risk/retention; your Module 0 draft correctly adds the missing pre-pipeline layer: “who should we contact, why now, and with what offer?”  

For the DAIS product demo, the architecture should be:

**Databricks App frontend** → **FastAPI backend** → **Databricks SQL / Unity Catalog gold tables** → **Cotality Delta Share + synthetic demo overlays** → **Genie + metric views** → **Agent Bricks / MCP / UC functions for agentic explanation and next-best-action** → **Lakebase for saved campaigns, feedback, action audit, and app state**.

This keeps the demo credible, modern, and Databricks-native while avoiding a risky attempt to productionize every module before DAIS.

---

# 1. Latest Databricks feature research and design implications

## The partner architecture lens

This should be treated primarily as a **Built-On Databricks** partner architecture, with Cotality acting as the **Data Collaboration / Marketplace / Delta Sharing** partner. The Partner Well-Architected Framework says Built-On solutions use Databricks as the core intelligence layer for customer-facing analytics, AI, model serving, agentic behavior, and custom user experiences. It also calls out data sharing, custom apps, and AI-native acceleration as common Built-On characteristics. ([databrickslabs.github.io][1])

The Built-On requirements matter because this app is intended to become a product, not just a one-off SI demo. The architecture checklist requires Unity Catalog adoption, cost attribution tags, OAuth rather than PATs, IaC through Terraform / Declarative Automation Bundles, and a clearly selected deployment model. It also strongly recommends Databricks-native orchestration, AI/BI dashboards, Lakebase for OLTP, Agent Bricks / Databricks AI stack, and monitoring through system tables. ([databrickslabs.github.io][2])

## Databricks Apps

**Use Databricks Apps as the main app container.** Apps now support secure data and AI applications running directly on Databricks serverless infrastructure, with Unity Catalog governance, Databricks SQL querying, and OAuth integration. They support local development and deployment using Python, Node.js, or a combination of both, including React, Angular, Svelte, Express, Streamlit, Dash, and Gradio. ([Databricks Documentation][3])

Design implication: use **React + TypeScript + FastAPI** inside Databricks Apps. Streamlit is fine for Cotality’s setup app or a utility accelerator, but for this DAIS demo you need a product-grade, multi-page, map-heavy, workflow-oriented enterprise UI. Databricks Apps supports hybrid Node/Python apps, so the frontend can be modern React while the backend remains Python for Databricks SDK, SQL, MLflow, and agent integrations. ([Databricks Documentation][4])

One implementation constraint: Databricks App files cannot exceed 10 MB, so do not bundle large map tiles, raw screenshots, sample data dumps, or generated assets into the app directory. Keep demo data in Unity Catalog tables or Lakebase, and keep static UI assets lightweight. ([Databricks Documentation][5])

## AI/BI Dashboards and Genie

Use **AI/BI Dashboards** for fixed KPIs and executive analytics, and use **Genie** for conversational analytics over curated mortgage semantic data. AI/BI combines dashboards, Genie spaces, and Unity Catalog business semantics; dashboards answer predefined business questions, while Genie spaces let users ask broader natural-language questions and improve through feedback and configuration. ([Databricks Documentation][6])

For this app, do **not** rely on AI/BI as the whole UI. Use AI/BI-style cards and charts inside the custom app, and optionally create a companion AI/BI Dashboard for the executive page. The functional spec already notes that geospatial visualization is not a native strength of AI/BI today and that Databricks Apps should own the map experience, with AI/BI and Genie handling tabular KPIs and conversational analytics. 

Published AI/BI dashboards can include a companion Genie space by default, and users can access Ask Genie on published dashboards. However, if you ever externally embed dashboards for non-Databricks users, Ask Genie is not supported there; use the Genie Conversation API instead. Also, Genie can answer from the full dataset query results behind a dashboard, even if a field is not visualized, so only include fields the user is allowed to ask about. ([Databricks Documentation][7])

## Genie API and Genie as an App resource

For the custom app, use **Genie API** or a **Genie space resource**. The Genie API supports stateful conversations and management APIs for programmatic creation/configuration of spaces. Databricks Apps can add Genie spaces as resources, granting the app service principal scoped permissions to submit natural-language questions and receive SQL-backed responses. ([Databricks Documentation][8])

Design implication: create a curated **Mortgage Lead Intelligence Genie Space** over only the gold semantic tables and metric views. Add it to the app with `CAN RUN`, expose it in the `/ask-genie` page, and also use it as a tool for the Supervisor Agent.

## Unity Catalog metric views and business semantics

Use **Unity Catalog metric views** for your semantic layer. Metric views separate metric definitions from dimensions, letting you define business measures once and query them across dimensions at runtime. They are intended for SQL, notebooks, dashboards, Genie spaces, and alerts. ([Databricks Documentation][9])

For this mortgage app, define metric views for lead populations, segment counts, conversion estimates, opportunity value, retention risk, and borrower opportunity scoring. Add agent metadata, including display names, synonyms, formatting, and business context, because Databricks documents that semantic metadata improves visualization and natural-language tools like Genie. ([Databricks Documentation][10])

## Agent Bricks, Supervisor Agent, and MCP

Use **Agent Bricks Supervisor Agent** as the long-term orchestration layer. Current docs say Supervisor Agent coordinates Genie spaces, agent endpoints, Unity Catalog functions, and MCP servers, using task delegation and result synthesis across specialized domains. This maps directly to your app: structured lead analytics through Genie, deterministic business logic through UC functions, Cotality property calls through CLIP-MCP, and narrative generation through a strategy writer agent. ([Databricks Documentation][11])

The official Databricks blog announced Supervisor Agent as GA in February 2026, with Unity Catalog governance, on-behalf-of access controls, and built-in learning/evaluation loops. That makes it appropriate to position the DAIS roadmap around Supervisor Agent, while still keeping a deterministic backend fallback for the actual demo build. ([databricks.com][12])

Use **MCP** for Cotality CLIP-style property intelligence. Databricks managed MCP servers can connect agents to Unity Catalog, Vector Search, Genie spaces, and UC functions, and external MCP servers can connect to third-party APIs through Databricks-managed proxies. Databricks specifically recommends letting the LLM dynamically discover tools at runtime rather than hardcoding tool names or parsing tool outputs programmatically. ([Databricks Documentation][13])

Cotality’s own deck says the mortgage data foundation uses CLIP, Owner Link, Delta Sharing, semantic models, Agent Bricks, and Cotality CLIP MCP on Databricks Marketplace, so MCP is not just architectural wish-casting; it is aligned with the joint partner story. 

## Lakebase

Use **Lakebase Autoscaling** for app state, not for analytical lead computation. Lakebase is Databricks’ managed Postgres OLTP database. The latest Lakebase Autoscaling version supports autoscaling compute, scale-to-zero, branching, instant restore, Databricks Apps integration, and registration in Unity Catalog. ([Databricks Documentation][14])

Design implication: analytical data stays in Delta tables and metric views; transactional state goes to Lakebase. Lakebase tables should store saved campaign definitions, user selections, lead list exports, agent sessions, feedback, and action audit logs. Databricks Apps can connect to Lakebase as a resource with service-principal setup and injected connection details, which avoids manual credential handling. ([Databricks Documentation][15])

## Lakeflow

Use **Lakeflow Jobs** for orchestration, **Lakeflow Spark Declarative Pipelines** for production transformations, and **Lakeflow Connect** for future first-party source ingestion. Lakeflow Connect supports fully managed connectors and custom pipelines, with governance through Unity Catalog and orchestration through Lakeflow Jobs. Managed connectors currently include Salesforce, SQL Server, ServiceNow, and Google Analytics; production Encompass/MSP ingestion will likely need custom APIs, partner connectors, or customer-specific pipelines unless a connector becomes available. ([Databricks Documentation][16])

Lakeflow Jobs should orchestrate demo refresh, gold-table materialization, scoring, metric refresh, and optional Lakebase sync. Jobs can coordinate multiple tasks, conditional logic, and dependencies. Lakeflow Spark Declarative Pipelines should own repeatable batch/streaming transforms from raw Cotality data into silver/gold tables. ([Databricks Documentation][17])

## Delta Sharing and Marketplace

Cotality data should remain a **Delta Sharing / Marketplace** data product. Delta Sharing is the secure Databricks data-sharing platform for sharing data and AI assets across organizations, and Databricks-to-Databricks sharing supports Unity Catalog governance, auditing, usage tracking, volumes, notebooks, models, and more. ([Databricks Documentation][18])

The Marketplace docs also now support data products beyond datasets, including notebooks, ML models, and MCP servers, which strengthens the joint Cotality + Databricks story: Cotality can distribute data and CLIP-MCP capabilities, while Entrada distributes the app/accelerator and implementation blueprint. ([Databricks Documentation][19])

## Unity Catalog governance, lineage, and audit

Everything should be registered and governed in Unity Catalog. Unity Catalog captures runtime lineage across queries, including down to the column level, and lineage can include notebooks, jobs, and dashboards. For auditability, Databricks system tables include audit logs in `system.access.audit`. ([Databricks Documentation][20])

Design implication: every borrower recommendation must include an evidence trail: source table, source signal, timestamp, scoring rule, and agent action. That is also consistent with the functional spec’s requirement that recommendations, risk flags, and competitive signals be traceable to specific Cotality data points and logged for audit. 

## Declarative Automation Bundles

Use **Declarative Automation Bundles**—formerly Databricks Asset Bundles—for CI/CD and environment deployment. Bundles support source control, code review, testing, CI/CD, and resource definitions for Jobs, Lakeflow pipelines, dashboards, model serving endpoints, MLflow experiments, and registered models. There are also bundle examples for Databricks Apps, apps backed by Lakebase, and dashboards. ([Databricks Documentation][21])

## Databricks One

For business-user distribution, Databricks One is relevant after the demo. It gives business users a simplified entry point to dashboards, Genie, and Databricks Apps without requiring them to navigate technical workspace concepts. ([Databricks Documentation][22])

Design implication: the DAIS demo should feel like something a VP of Mortgage Lending or Head of Growth could open through Databricks One, not like a notebook or engineering utility.

---

# 2. What you are trying to build, from high level to low level

## North star

You are building a **Databricks-native Mortgage Intelligence Platform** that helps lenders win more mortgage business using Cotality’s property, ownership, lien, and market intelligence data.

The full product vision is five modules:

| Module                                                          |                                                     Business purpose |          Current priority |
| --------------------------------------------------------------- | -------------------------------------------------------------------: | ------------------------: |
| Module 0: Top-of-Funnel Lead Generation & Borrower Segmentation |                   Find who to contact before the lead enters CRM/LOS |              **DAIS MVP** |
| Module 1: Mortgage Pipeline Command Center                      | Executive view of production, market share, and pipeline performance |                    Future |
| Module 2: Intelligent Loan Officer Workbench                    |                  Help LOs prioritize, research, and engage borrowers | Future, natural follow-on |
| Module 3: Collateral Intelligence & Underwriting Copilot        |              Help underwriters assemble property/collateral dossiers |                    Future |
| Module 4: Portfolio Risk & Retention Intelligence Engine        |       Help risk/servicing teams monitor risk and recapture borrowers |                    Future |

Antoine’s modules 1–4 are useful, but Cotality’s feedback is right: the lender first needs **Module 0** because none of the downstream pipeline, underwriting, or retention workflows matter until the business knows which borrower opportunities to pursue.

## Mortgage primer for you

A **property** is the real estate asset. Cotality’s **CLIP** is the mastered property identifier that lets you join across property records, mortgage records, valuation data, lien history, and other Cotality datasets.

An **owner** is the person or entity that owns one or more properties. **Owner Link** is the mastered owner identity that lets you connect one owner to related properties and historical real-estate behavior.

A **lien** is a legal claim against a property. In mortgage context, a voluntary lien is typically the mortgage or deed of trust recorded when the borrower takes out a loan. An **open lien** means there is likely an active mortgage debt on the property.

A **refinance** replaces an existing mortgage with a new mortgage. A borrower is **in the money** when their current loan, equity position, property value, rate, or maturity timing creates an economic reason to refinance, cash out, or take a HELOC.

A **HELOC** is a home equity line of credit. A **cash-out refinance** is a new mortgage that pays off the old one and lets the borrower extract some equity as cash. A **purchase mortgage** is for buying another home.

A **building permit trigger** suggests the homeowner may need capital for renovations, making them a HELOC or cash-out candidate. A **listing trigger** means the owner has listed a home for sale, which may indicate a purchase mortgage opportunity. A **multi-property owner** or **real-estate investor** may be a repeat borrower or cross-sell opportunity.

A **lead** is a potential borrower to contact. A **pipeline** is what happens after the lead becomes a real sales opportunity or loan application. Module 0 is pre-pipeline.

## Module 0 business goal

Module 0 answers:

**Who should the lender contact?**
A borrower, owner, former customer, current customer, or competitor customer identified from Cotality public-record and mortgage data.

**Why now?**
Because the borrower shows one or more signals: in-the-money refinance, lien maturity, property listed for sale, recent building permits, equity growth, repeat borrowing behavior, competitor activity, or multi-property ownership.

**With what offer?**
Refinance, HELOC, cash-out refinance, purchase mortgage, retention outreach, or recapture campaign.

**What makes this credible?**
Every recommendation shows source evidence from Cotality data: property, lien, owner, mortgage market, listing, permit, AVM, or market-rate assumptions.

## DAIS demo storyline

Use a fictional lender like **Summit Mortgage**. Avoid real contact information and use synthetic borrower names. The app opens with a Head of Growth persona trying to create a campaign in Atlanta or Orange County.

The demo flow:

1. Select a geography, product strategy, and lender relationship.
2. Build a lead population from public records.
3. See segment cards: In the Money, HELOC Candidate, Listed for Sale, Permit Activity, Investor / Multi-Property, Former Customer Recapture.
4. Drill into a map and ranked lead queue.
5. Open a borrower story.
6. Show Customer 360: subject property, owner, related properties, open liens, equity, triggers, and source evidence.
7. Generate a next-best-offer and outreach draft.
8. Ask Genie: “Show me borrowers with more than $100K equity and recent permits in zips where refi activity is increasing.”
9. Human approves the action; the app logs the action to Lakebase.

## MVP scope

The DAIS MVP should include:

| Capability                     | Build for DAIS? | Notes                                                              |
| ------------------------------ | --------------: | ------------------------------------------------------------------ |
| Lead portfolio builder         |             Yes | Geography, occupancy, open lien, lender relationship, target offer |
| Segment cards                  |             Yes | In the Money, HELOC, Listing, Permit, Investor, Former Customer    |
| Map visualization              |             Yes | Custom Databricks App page, not AI/BI-only                         |
| Ranked lead queue              |             Yes | Sortable table with score and rationale                            |
| Borrower 360                   |             Yes | Use Cotality sample Customer 360/persona data where available      |
| Source evidence                |             Yes | Must be visible in the UI                                          |
| Next-best-offer                |             Yes | Rule-based for MVP, MLflow later                                   |
| Outreach draft                 |             Yes | Human-in-loop; no automatic send                                   |
| Genie Q&A                      |             Yes | Curated gold tables only                                           |
| Lakebase audit log             |             Yes | Save campaign, approvals, feedback                                 |
| Real CRM writeback             |              No | Show as “future Salesforce / CRM activation”                       |
| Real Encompass/MSP ingestion   |              No | Architecture only                                                  |
| Real credit data               |              No | Avoid for demo                                                     |
| Production compliance workflow |              No | Show traceability and suppression placeholders                     |

## Data you need

From the current share you have: mortgage domain, mortgage market analytics, owner transfer, property, and voluntary lien status marketing. Based on the last Cotality call, you should ask Cotality for two more demo-friendly files: **Customer 360 sample** and **personas/segment sample**. You also need listing and permit overlays, or synthetic versions of those overlays, because Module 0 depends heavily on listing and permit triggers.

Minimum DAIS data set:

| Data                              | Purpose                                                  |
| --------------------------------- | -------------------------------------------------------- |
| Property / CLIP                   | Property identity and join key                           |
| Owner Link / Customer 360         | Owner-to-property relationships                          |
| Voluntary lien / mortgage records | Open lien status, lender, loan amount, possible maturity |
| Mortgage market analytics         | Market/refi trends by geography                          |
| AVM / property value estimate     | Equity and opportunity sizing                            |
| HPI / market trend                | Local market context                                     |
| MLS/listing sample                | Purchase trigger                                         |
| Building permit sample            | HELOC/cash-out trigger                                   |
| Market-rate assumptions           | In-the-money logic                                       |
| Offer rules                       | Refi/HELOC/cash-out recommendation logic                 |
| Synthetic borrower contact        | Demo-safe names/emails/phones                            |
| Suppression / DNC placeholder     | Compliance realism                                       |

## Core scoring logic for demo

For DAIS, do not overbuild ML. Use deterministic rules and optionally register the scoring function with MLflow later.

A simple 0–100 score:

| Score component               | Weight | Example logic                                          |
| ----------------------------- | -----: | ------------------------------------------------------ |
| Economic incentive            |     35 | Rate spread, equity, estimated LTV                     |
| Intent trigger                |     30 | Listing, permits, lien maturity, local refi surge      |
| Borrower/property fit         |     15 | Occupancy, property type, owner history                |
| Relationship strategy         |     10 | Current customer, former customer, competitor customer |
| Evidence freshness/confidence |     10 | Recent signal, AVM confidence, Cotality source quality |

Example recommendations:

| Segment                   | Trigger                                                         | Recommended offer                    |
| ------------------------- | --------------------------------------------------------------- | ------------------------------------ |
| In the Money              | Current rate above market or strong equity position             | Refinance                            |
| Permit Activity           | High-value permits in last 6–12 months                          | HELOC or cash-out                    |
| Listed for Sale           | Active/recent listing                                           | Purchase mortgage outreach           |
| Investor / Multi-Property | Owner Link shows multiple properties                            | Investor loan / portfolio cross-sell |
| Former Customer           | Prior lien with lender, now with competitor or open opportunity | Recapture                            |
| Existing Borrower         | Equity growth, maturity timing, competitor activity             | Retention outreach                   |

## Low-level data architecture

Use this target table model.

```text
cotality_raw / external shared catalogs
  property
  voluntary_lien
  mortgage_market_analytics
  owner_transfer
  customer_360_sample
  persona_segments_sample
  listing_sample
  building_permit_sample

mip_silver
  property_master
  owner_master
  owner_property_bridge
  lien_current
  mortgage_activity_by_geo
  property_value_features
  property_trigger_features
  lender_relationship_features

mip_gold
  lead_population
  lead_segment_membership
  lead_scores
  borrower_360
  recommended_offers
  evidence_events
  campaign_export_ready

mip_semantics
  lead_generation_metric_view
  segment_performance_metric_view
  borrower_opportunity_metric_view

lakebase app schema
  campaigns
  portfolio_filters
  saved_leads
  outreach_drafts
  approvals
  agent_sessions
  action_audit
  user_feedback
```

## Agent architecture

For DAIS, you can implement the backend deterministically and wrap it with “agentic” UI. For production, wire these as Agent Bricks / Supervisor Agent tools.

| Agent/tool             | Role                                                                |
| ---------------------- | ------------------------------------------------------------------- |
| Lead Portfolio Agent   | Converts user filters into a lead population query                  |
| Segment Analyst Agent  | Explains segment counts and market trends                           |
| Borrower Dossier Agent | Builds Customer 360 and property dossier using CLIP / MCP           |
| Offer Strategy Agent   | Recommends refi, HELOC, cash-out, purchase, retention, or recapture |
| Evidence Agent         | Assembles traceable source evidence                                 |
| Outreach Writer Agent  | Drafts human-approved borrower messaging                            |
| Genie Space            | Answers natural-language analytic questions                         |
| UC functions           | Deterministic scoring, in-the-money logic, offer rules              |
| Lakebase               | Session memory, audit log, saved actions                            |

---

# 3. GitHub repo blueprint

## Repository name

Use:

```text
mortgage-intelligence-platform
```

Internal short name:

```text
mip
```

Why: it maps to the product name in Antoine’s spec, avoids over-coupling the repo name to one partner combination, and is suitable for a future Marketplace / Brickbuilder / partner accelerator.

## Framework decision

Use:

```text
Frontend: React + Vite + TypeScript
UI: Tailwind CSS + shadcn/ui + Radix primitives
Charts: Recharts or ECharts
Maps: MapLibre GL + GeoJSON overlays
Backend: FastAPI + Databricks SDK + Databricks SQL Connector
App runtime: Databricks Apps
Transactional state: Lakebase
Analytics: Unity Catalog Delta tables + SQL Warehouse
Semantics: Unity Catalog metric views
Conversational analytics: Genie API / Genie App resource
Agentic orchestration: Agent Bricks Supervisor Agent + MCP + UC functions
Deployment: Declarative Automation Bundles
```

Do **not** use Dask. Databricks/Spark is the distributed compute layer. Do **not** use Shiny for this demo. It is viable for analytics apps, but it will not give you the high-fidelity, multi-page, agentic SaaS feel this DAIS demo needs. Do **not** use Streamlit as the primary UI unless the timeline collapses; Streamlit is acceptable for Cotality setup utilities, not for the flagship DAIS product experience.

## Repo structure

```text
mortgage-intelligence-platform/
  README.md
  app.yaml
  databricks.yml
  package.json
  pyproject.toml
  uv.lock
  .env.example

  frontend/
    index.html
    vite.config.ts
    src/
      main.tsx
      app.tsx
      routes/
        home.tsx
        portfolio-builder.tsx
        segment-intelligence.tsx
        lead-queue.tsx
        borrower-360.tsx
        offer-orchestrator.tsx
        ask-genie.tsx
        admin-config.tsx
      components/
        layout/
        charts/
        maps/
        mortgage/
        evidence/
        agent/
      design-system/
        tokens.ts
        navigation.tsx
        cards.tsx
        status-badges.tsx
      lib/
        api.ts
        formatters.ts
        mortgage-glossary.ts
      mocks/
        demo-borrowers.ts

  backend/
    main.py
    api/
      health.py
      portfolio.py
      segments.py
      leads.py
      borrowers.py
      offers.py
      outreach.py
      genie.py
      audit.py
      admin.py
    services/
      databricks_sql.py
      lakebase.py
      genie_client.py
      scoring.py
      evidence.py
      cotality_mcp.py
    agents/
      supervisor.py
      lead_portfolio_agent.py
      borrower_dossier_agent.py
      offer_strategy_agent.py
      outreach_writer_agent.py
    schemas/
      portfolio.py
      lead.py
      borrower.py
      offer.py
      audit.py
    config/
      settings.py

  sql/
    ddl/
      001_catalogs_schemas.sql
      002_lakebase_app_tables.sql
      003_gold_tables.sql
    transformations/
      silver_property_master.sql
      silver_lien_current.sql
      silver_owner_property_bridge.sql
      gold_lead_population.sql
      gold_lead_scores.sql
      gold_borrower_360.sql
      gold_evidence_events.sql
    metric_views/
      lead_generation_metric_view.sql
      segment_performance_metric_view.sql
      borrower_opportunity_metric_view.sql
    uc_functions/
      fn_rate_spread.sql
      fn_in_the_money.sql
      fn_lead_score.sql
      fn_next_best_offer.sql

  pipelines/
    lakeflow/
      mip_feature_pipeline.py
      mip_gold_pipeline.py

  jobs/
    refresh_demo_data.yml
    refresh_scores.yml

  dashboards/
    executive_dashboard.lvdash.json
    segment_dashboard.lvdash.json

  genie/
    mortgage_lead_intelligence_space.yml
    sample_questions.md
    instructions.md
    trusted_assets.md

  lakebase/
    schema.sql
    seed_demo_campaigns.sql

  notebooks/
    00_validate_cotality_share.py
    01_explore_module0_data.py
    02_feature_engineering_walkthrough.py
    03_scoring_debug.py

  tests/
    unit/
      test_scoring.py
      test_offer_rules.py
      test_evidence.py
    integration/
      test_sql_queries.py
      test_genie_client.py
    e2e/
      portfolio_builder.spec.ts
      borrower_360.spec.ts

  docs/
    architecture.md
    data-contract.md
    module0-demo-talk-track.md
    partner-review-checklist.md
    cotality-data-request.md
    security-and-compliance.md

  .github/
    workflows/
      ci.yml
      deploy-dev.yml
      deploy-prod.yml
```

## API routes

```text
GET  /api/health
GET  /api/config/options
POST /api/portfolio/preview
POST /api/portfolio/create
GET  /api/portfolio/{portfolio_id}
GET  /api/segments?portfolio_id=...
GET  /api/leads?portfolio_id=...&segment=...
GET  /api/borrowers/{borrower_id}
GET  /api/borrowers/{borrower_id}/evidence
POST /api/offers/recommend
POST /api/outreach/draft
POST /api/outreach/approve
POST /api/genie/start
POST /api/genie/message
POST /api/audit/event
GET  /api/admin/rules
PUT  /api/admin/rules
```

## Databricks resources to define in `databricks.yml`

```text
Databricks App:
  mip-demo-app

SQL Warehouse:
  mip_serverless_sql

Lakeflow Jobs:
  mip_refresh_demo_data
  mip_refresh_scores
  mip_snapshot_dashboards

Lakeflow Pipelines:
  mip_feature_pipeline
  mip_gold_pipeline

AI/BI Dashboards:
  mip_executive_dashboard
  mip_segment_dashboard

Genie Space:
  mortgage_lead_intelligence

Lakebase:
  mip_app_state

MLflow Experiment:
  /Shared/mip/lead-scoring

Unity Catalog:
  catalog: mip_demo
  schemas: raw, silver, gold, semantics, app, audit
```

## `app.yaml` pattern

```yaml
command:
  - uvicorn
  - backend.main:app
  - --host
  - 0.0.0.0
  - --port
  - "8000"

env:
  - name: DATABRICKS_WAREHOUSE_ID
    valueFrom: sql-warehouse

  - name: GENIE_SPACE_ID
    valueFrom: genie-space

  - name: LAKEBASE_HOST
    valueFrom: lakebase

  - name: APP_ENV
    value: demo
```

Exact resource keys depend on how the App resources are configured in the workspace and bundle, but this is the shape to use.

---

# 4. How many web pages the App needs

For the **DAIS Module 0 MVP**, build **8 routes**, with 7 demo-visible pages and 1 admin/config page.

| Route                     | Page                                       |  Demo-visible? | Purpose                                                                 |
| ------------------------- | ------------------------------------------ | -------------: | ----------------------------------------------------------------------- |
| `/`                       | Demo Home / Mortgage Intelligence Overview |            Yes | Set context, show Module 0 → Modules 1–4 roadmap                        |
| `/portfolio-builder`      | Lead Portfolio Builder                     |            Yes | Select geography, occupancy, open lien, lender relationship, product    |
| `/segment-intelligence`   | Segment Intelligence + Map                 |            Yes | Show segment cards, zip/county/MSA map, opportunity metrics             |
| `/lead-queue`             | AI-Prioritized Lead Queue                  |            Yes | Ranked borrower opportunities with score and rationale                  |
| `/borrower-360/:id`       | Borrower 360 / Property Dossier            |            Yes | CLIP, Owner Link, related properties, liens, equity, evidence           |
| `/offer-orchestrator/:id` | Next-Best-Offer + Outreach                 |            Yes | Recommend offer and generate human-approved message                     |
| `/ask-genie`              | Ask Genie / Mortgage Data Analyst          |            Yes | Natural-language questions over curated gold data                       |
| `/admin-config`           | Demo Configuration / Rules                 | No, but useful | Market rates, offer thresholds, lender config, suppression placeholders |

For the **full product**, plan for 14–16 routes later. The future pages would add Pipeline Command Center, Loan Officer Workbench, Underwriting Queue, Collateral Dossier, Portfolio Risk Heat Map, Retention Intelligence, Climate Scenario Analysis, and customer onboarding/admin.

For DAIS, do not build those future pages deeply. Add them as disabled or “coming next” module cards on the home page to show the platform vision without creating implementation risk.

---

# 5. Claude Design prompts

Use these as copy/paste prompts. Start with the design-system prompt, then generate each page.

## Prompt 0 — Design system

**Design type:** design system + product UI direction

```text
Act as a senior enterprise SaaS product designer creating a design system for a Databricks-native Mortgage Intelligence Platform built by Entrada, Cotality, and Databricks.

Design for a DAIS booth demo at 1440x900 desktop resolution. The product is for mortgage lending executives, growth leaders, and loan officers. It uses Cotality property, lien, ownership, listing, permit, AVM, and mortgage market data on Databricks to identify which borrowers to contact, why now, and with what offer.

Create a high-trust B2B fintech design system. It should feel modern, premium, data-dense, and explainable. Avoid consumer-finance styling. Make it look like an enterprise AI/data product that belongs inside Databricks Apps and Databricks One.

Important design principles:
- Every AI recommendation must show source evidence.
- Every score needs a plain-English rationale.
- Human approval is required before outreach.
- Use synthetic demo names only.
- Do not include real logos unless provided; use text placeholders: Entrada × Cotality × Databricks.
- Use a left module rail showing Module 0 through Module 4, with Module 0 active.
- Include a top bar with workspace, demo lender, and environment indicators.
- Include reusable components: KPI card, segment card, evidence chip, borrower score badge, confidence meter, trigger timeline, map tooltip, data-source drawer, human approval banner, Genie chat panel, and audit log row.

Deliver:
1. Visual design system summary
2. Typography and spacing guidance
3. Component inventory
4. Example layout grid
5. Interaction and animation guidance for a live demo
6. Accessibility considerations
```

## Prompt 1 — Demo Home / Mortgage Intelligence Overview

**Design type:** high-fidelity landing page + demo storyboard

```text
Create a high-fidelity desktop UI for the home page of the Mortgage Intelligence Platform.

Route: /
Page title: Mortgage Intelligence Platform
Audience: Databricks DAIS booth attendees, mortgage executives, partner engineers
Active module: Module 0: Top-of-Funnel Lead Generation & Borrower Segmentation

Goal of the page:
Set the story. Show that the platform covers the full mortgage lifecycle, but today’s demo starts with Module 0 because lenders first need to know who to contact, why now, and with what offer.

Layout:
- Top bar: Entrada × Cotality × Databricks, environment “DAIS Demo”, demo lender “Summit Mortgage”
- Left rail: Module 0 active; Modules 1–4 visible but marked “future workflow”
- Hero panel: “Find the next best borrower before they enter your CRM”
- Three value cards:
  1. Build a lead population from public records
  2. Segment borrowers by intent and economic incentive
  3. Activate next-best-offer with source evidence and human approval
- Lifecycle chevrons:
  Module 0 Lead Generation → Module 1 Pipeline → Module 2 LO Workbench → Module 3 Underwriting → Module 4 Risk & Retention
- Demo start CTA: “Build Lead Portfolio”
- Right-side “Data Foundation” panel showing CLIP, Owner Link, Public Records, Voluntary Lien, Mortgage Market Analytics, Listings, Permits, AVM, HPI, Genie, Agent Bricks, Lakebase
- Bottom strip: “All recommendations trace to Cotality source signals governed in Unity Catalog.”

Use synthetic data only. Make the page visually impressive but not cluttered.
```

## Prompt 2 — Lead Portfolio Builder

**Design type:** high-fidelity workflow page

```text
Create a high-fidelity desktop UI for the Lead Portfolio Builder page of a Databricks App.

Route: /portfolio-builder
Persona: Head of Growth or VP of Mortgage Lending
Goal: Let the user define a marketable lead population using Cotality public-record and mortgage data.

Page structure:
- Left module rail, Module 0 active
- Top breadcrumb: Module 0 / Lead Portfolio Builder
- Main heading: “Build a high-intent borrower population”
- Short helper text: “Start with public-record property and lien data. Add ownership, market, listing, permit, and lender relationship filters.”

Builder form sections:
1. Geography
   - State, county, MSA, zip multi-select
   - Example selected: Georgia → Atlanta MSA → 30305, 30309, 30324
2. Property and occupancy
   - Owner-occupied, second home, investment, all
   - Open lien toggle
   - Property type chips
3. Lender relationship
   - Current customers
   - Former customers
   - Competitor customers
   - Any lender
   - Example competitor: ABC Mortgage
4. Target product
   - Refinance
   - HELOC
   - Cash-out
   - Purchase
   - Retention / recapture
5. Offer assumptions
   - Market refinance rate
   - Minimum equity
   - Maximum LTV
   - Permit threshold
   - Recency window

Right preview panel:
- Estimated population count
- Estimated high-intent leads
- Top segment preview
- Data readiness checklist
- “Generate Portfolio” CTA

Add a compact “Source Data” drawer showing Cotality Public Records, Voluntary Lien, Owner Link, Mortgage Market Analytics, Listings, Permits, AVM/HPI.

Make the page feel like a strategic data product, not a generic filter form.
```

## Prompt 3 — Segment Intelligence + Map

**Design type:** high-fidelity geospatial analytics page

```text
Create a high-fidelity desktop UI for the Segment Intelligence page of a Databricks Mortgage Intelligence App.

Route: /segment-intelligence
Persona: Head of Growth / Sales Manager
Goal: Show the generated lead portfolio, segment it by opportunity type, and let the user drill from geography to borrower.

Use a 1440x900 layout.

Page elements:
- Header: “Segment Intelligence”
- Campaign context chips: Atlanta MSA, owner occupied, open lien, former/current/competitor customers, refi + HELOC strategy
- KPI cards:
  - Marketable population
  - High-intent leads
  - Estimated opportunity value
  - Avg equity
  - Top zip by opportunity
- Segment cards:
  1. In the Money Refi
  2. HELOC / Cash-Out Candidate
  3. Listed for Sale
  4. Recent Building Permit
  5. Investor / Multi-Property Owner
  6. Former Customer Recapture
Each segment card should show count, value, top reason, confidence, and “View leads.”

Center-left: interactive map placeholder using zip-code polygons. Show heat by opportunity score. Include hover tooltip with zip, lead count, top segment, market refi trend.

Center-right: ranked zip table with columns:
Zip, Leads, Top Segment, Avg Equity, Refi Activity Change, Top Competitor, Opportunity Value.

Bottom: “Why this matters” explanation panel:
“Cotality market activity shows where borrower intent is rising. Owner Link and CLIP connect the property, owner, related properties, and lien history so the lender can act before the lead enters CRM.”

Include a persistent “Ask Genie about this portfolio” button.
```

## Prompt 4 — AI-Prioritized Lead Queue

**Design type:** high-fidelity data table + explainability page

```text
Create a high-fidelity desktop UI for an AI-Prioritized Lead Queue in a mortgage lead generation app.

Route: /lead-queue
Persona: Sales Manager assigning leads to loan officers
Goal: Rank borrower opportunities and explain exactly why each borrower is on the list.

Page structure:
- Header: “Prioritized Lead Queue”
- Context chips: Atlanta MSA, Module 0, Refi + HELOC, 12-month signal window
- Left filters: segment, score range, zip, offer type, confidence, lender relationship, equity range
- Main table with sticky header:
  - Priority rank
  - Borrower / owner
  - Zip
  - Segment
  - Recommended offer
  - Score
  - Why now
  - Source evidence
  - Assigned LO
  - Action
- Example synthetic leads:
  1. James & Maria Rodriguez — In the Money + HELOC — score 94
  2. David Park — Permit Activity — score 87
  3. Lisa Thompson — Listed for Sale — score 82
- Each row expands to show:
  - Trigger timeline
  - Equity estimate
  - Lien summary
  - Related property count
  - Evidence chips: Voluntary Lien, AVM, Listing, Permit, Mortgage Market Analytics
- Right side panel: “Lead quality explanation”
  - Score breakdown with weighted factors
  - Compliance note: “Outreach requires human approval and suppression check”
  - CTA: “Open Borrower 360”

Make the table dense but elegant. The page should feel credible enough for mortgage executives and technical enough for Databricks partner teams.
```

## Prompt 5 — Borrower 360 / Property Dossier

**Design type:** high-fidelity borrower detail page

```text
Create a high-fidelity Borrower 360 page for a Databricks-native mortgage intelligence app.

Route: /borrower-360/:id
Persona: Loan officer or sales manager
Goal: Show the full borrower/property story with Cotality source evidence.

Use synthetic borrower:
James & Maria Rodriguez
Zip: 30309
Recommended offer: Refinance + HELOC
Score: 94
Trigger rationale: lien matures in 4 months, local refi activity up 28% QoQ, estimated equity $285K, ARM reset Oct 2026.

Page layout:
- Header with borrower name, opportunity score, recommended offer, assigned LO, approval status
- Left column: Borrower profile card
  - Synthetic contact info
  - Relationship: former customer
  - Segment memberships
  - Contactability status
- Center: Property dossier
  - Subject property card
  - CLIP ID placeholder
  - Current AVM and confidence
  - Original purchase / last transfer
  - Current lien estimate
  - Estimated equity
  - Flood/climate placeholder summary
  - Comparable sales mini-list
- Right: Owner Link / Customer 360
  - Related properties
  - Mortgage transaction history
  - Investor/multi-property indicators
  - Prior refi/HELOC behavior
- Bottom: Evidence timeline
  - Public record deed
  - Voluntary lien
  - Market refi activity
  - AVM/HPI
  - Listing/permit signals if applicable
- Include “Build live dossier” interaction where Agent Bricks / CLIP-MCP calls appear as loading steps:
  1. Fetch property
  2. Fetch lien history
  3. Fetch AVM
  4. Fetch related owner properties
  5. Generate recommendation

Make source evidence highly visible. Include a drawer titled “Why we trust this recommendation.”
```

## Prompt 6 — Next-Best-Offer + Outreach Orchestrator

**Design type:** high-fidelity workflow page with human approval

```text
Create a high-fidelity Next-Best-Offer and Outreach Orchestrator page for a mortgage intelligence app.

Route: /offer-orchestrator/:id
Persona: Loan officer preparing borrower outreach
Goal: Convert borrower intelligence into a human-approved action.

Borrower: James & Maria Rodriguez
Recommended offer: Refinance + HELOC bundle
Reason: lien maturity, equity, ARM reset, local refi activity, low collateral risk

Page layout:
- Header: “Next Best Offer”
- Offer recommendation card:
  - Primary offer: Refinance + HELOC
  - Alternative offers: HELOC only, retention check-in
  - Confidence score
  - Expected value
  - Required human review
- Score explanation:
  - Economic incentive
  - Intent trigger
  - Product fit
  - Relationship strategy
  - Evidence confidence
- Draft outreach panel:
  - Subject line
  - Email body
  - SMS snippet
  - Talking points for phone call
  - Editable text area
- Evidence sidebar:
  - Data signals used in the recommendation
  - Source table / source product chips
  - Timestamp
- Approval workflow:
  - Suppression check passed
  - Human approval checkbox
  - Create CRM task button
  - Export lead button
  - Log action button
- Bottom audit preview:
  - “This action will be logged to Lakebase with recommendation inputs, approver, timestamp, and evidence IDs.”

Tone: credible, safe, and compliant. Do not imply the AI sends messages automatically. Make the human-in-the-loop pattern obvious.
```

## Prompt 7 — Ask Genie / Mortgage Data Analyst

**Design type:** high-fidelity conversational analytics page

```text
Create a high-fidelity “Ask Genie” page inside the Mortgage Intelligence Platform.

Route: /ask-genie
Persona: Business user exploring the lead portfolio with natural language
Goal: Provide a conversational analytics experience over curated Module 0 gold tables and metric views.

Page layout:
- Header: “Ask Genie about this lead portfolio”
- Left context panel:
  - Active portfolio
  - Available trusted datasets:
    - Lead population
    - Segment membership
    - Borrower opportunity metrics
    - Market activity by zip
    - Evidence events
  - Suggested questions:
    1. Which zips have the most in-the-money refi candidates?
    2. Show HELOC candidates with more than $150K equity and recent permits.
    3. Which segments have the highest opportunity value?
    4. Where is refi activity rising fastest?
    5. Which leads should Sarah call first and why?
- Main chat panel:
  - User question
  - Genie response with table and chart
  - SQL transparency drawer
  - Trusted answer badge when logic comes from metric views
- Right insight panel:
  - Top drivers
  - Follow-up recommendations
  - “Open leads from this answer”
- Include warning text:
  - “Genie can only access curated demo-safe fields in this space.”

Make it feel like a Databricks-native conversational BI product, not a generic chatbot.
```

## Prompt 8 — Admin Configuration / Rules

**Design type:** low-to-mid fidelity admin workflow page

```text
Create an admin/configuration page for the Mortgage Intelligence Platform demo.

Route: /admin-config
Persona: Solution architect or product admin
Goal: Configure the rules and assumptions behind Module 0 without exposing raw engineering complexity.

Page sections:
1. Demo lender configuration
   - Lender name
   - Competitor names
   - Target geographies
   - LO assignment strategy
2. Market rate assumptions
   - Refi rate
   - HELOC rate
   - Cash-out rate
   - ARM reset window
3. Offer eligibility thresholds
   - Minimum equity
   - Max LTV
   - Rate-spread threshold
   - Permit value threshold
   - Listing recency window
4. Suppression and compliance placeholders
   - Do Not Contact
   - Prior outreach cooldown
   - Missing contact info
   - Human approval required
5. Data source readiness
   - Public Records
   - Voluntary Lien
   - Mortgage Market Analytics
   - Customer 360
   - Personas
   - Listings
   - Permits
   - AVM/HPI
6. Save and audit
   - Save configuration
   - Preview affected lead count
   - Write config change to audit log

This page is not part of the main DAIS talk track but should look polished enough if opened by a technical evaluator.
```

---

# Immediate build plan

Start with a **precomputed gold-table demo**. Do not make every page compute live from raw Cotality tables. Build the transformations so they are real, but materialize the demo outputs to fast tables. The app should feel instant.

Recommended sequence:

1. Finalize Cotality data request: Customer 360 sample, persona sample, listing sample, permit sample, AVM/HPI if not present.
2. Build synthetic config tables: market rates, offer rules, lender/competitor names, contact fields, suppression flags.
3. Create silver/gold SQL models for lead population, segment flags, scores, offers, borrower 360, evidence.
4. Build React app shell and all 8 routes with mocked data.
5. Swap mocked data for FastAPI calls to Databricks SQL.
6. Add Lakebase for campaign save, approvals, audit, and feedback.
7. Add Genie page using curated gold tables and metric views.
8. Add Agent Bricks/Supervisor integration if workspace access is ready; otherwise keep deterministic backend orchestrator and present Supervisor Agent as the production architecture.
9. Rehearse a 6–8 minute DAIS talk track around three borrower stories.

The key is to make Module 0 look like a complete product while keeping the implementation narrow: **build portfolio → segment → rank → explain → recommend → approve → audit**.

[1]: https://databrickslabs.github.io/partner-architecture/built-on "Built On Databricks | Partner Well Architected Framework"
[2]: https://databrickslabs.github.io/partner-architecture/built-on/ARCHITECTURE-REQUIREMENTS-CHECKLIST "Built-On Architecture Requirements Checklist | Partner Well Architected Framework"
[3]: https://docs.databricks.com/aws/en/dev-tools/databricks-apps/ "Databricks Apps | Databricks on AWS"
[4]: https://docs.databricks.com/aws/en/dev-tools/databricks-apps/deploy "Deploy a Databricks app | Databricks on AWS"
[5]: https://docs.databricks.com/aws/en/dev-tools/databricks-apps/app-development "Develop apps | Databricks on AWS"
[6]: https://docs.databricks.com/aws/en/ai-bi/ "Databricks AI/BI | Databricks on AWS"
[7]: https://docs.databricks.com/aws/en/dashboards/genie-spaces "Genie spaces with dashboards | Databricks on AWS"
[8]: https://docs.databricks.com/aws/en/genie/conversation-api "Use the Genie API to integrate Genie into your applications | Databricks on AWS"
[9]: https://docs.databricks.com/aws/en/business-semantics/metric-views/ "Unity Catalog metric views | Databricks on AWS"
[10]: https://docs.databricks.com/aws/en/business-semantics/metric-views/basic-modeling "Model metric views | Databricks on AWS"
[11]: https://docs.databricks.com/aws/en/generative-ai/agent-bricks/multi-agent-supervisor "Use Supervisor Agent to create a coordinated multi-agent system | Databricks on AWS"
[12]: https://www.databricks.com/blog/agent-bricks-supervisor-agent-now-ga-orchestrate-enterprise-agents "Agent Bricks Supervisor Agent is Now GA: Orchestrate Enterprise Agents | Databricks Blog"
[13]: https://docs.databricks.com/aws/en/generative-ai/mcp/managed-mcp "Use Databricks managed MCP servers | Databricks on AWS"
[14]: https://docs.databricks.com/aws/en/oltp/ "Lakebase Postgres | Databricks on AWS"
[15]: https://docs.databricks.com/aws/en/oltp/projects/databricks-apps "Using Lakebase with Databricks Apps | Databricks on AWS"
[16]: https://docs.databricks.com/aws/en/ingestion/overview "What is Lakeflow Connect? | Databricks on AWS"
[17]: https://docs.databricks.com/aws/en/jobs/ "Lakeflow Jobs | Databricks on AWS"
[18]: https://docs.databricks.com/aws/en/delta-sharing/ "What is Delta Sharing? | Databricks on AWS"
[19]: https://docs.databricks.com/aws/en/marketplace "What is Databricks Marketplace? | Databricks on AWS"
[20]: https://docs.databricks.com/aws/en/data-governance/unity-catalog/data-lineage "View data lineage using Unity Catalog | Databricks on AWS"
[21]: https://docs.databricks.com/aws/en/dev-tools/bundles/ "What are Declarative Automation Bundles? | Databricks on AWS"
[22]: https://docs.databricks.com/aws/en/workspace/databricks-one "What is Databricks One? | Databricks on AWS"
