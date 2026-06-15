# Full implementation plan — Module 0

## Definition of success

Module 0 is ready when a Databricks/Cotality/Entrada walkthrough lead can show a polished Databricks App that creates a high-intent mortgage borrower population from Cotality public-record intelligence, ranks and explains borrower opportunities, recommends offers, drafts human-approved outreach, and logs every action.

## Architectural target

```text
React/Vite UI
  ↓
FastAPI backend
  ↓
Service adapters
  ├─ Databricks SQL Warehouse → UC gold tables and metric views
  ├─ Lakebase/Postgres → app state and audit
  ├─ Genie resource/API → conversational analytics
  └─ Cotality CLIP-MCP / Agent Bricks → optional production dossier tools
  ↓
Cotality Delta Share live feeds, including MLS listings + pending Building Permits feed → silver/gold tables
```

## Phase 0 — Agentic environment setup

1. Clone repo and open in VS Code.
2. Install Claude Code and authenticate.
3. Start Claude Code in repo root.
4. Run `/init`, then confirm `CLAUDE.md` loaded.
5. Review `.claude/settings.json` and keep `.claude/settings.local.json` for personal tool allowances.
6. Configure MCP by copying `.mcp.example.json` to `.mcp.json` only after local credentials are ready.
7. Run `python tools/verify_scaffold.py`.

Acceptance:
- Claude can list project subagents.
- Claude can see project skills.
- `python tools/verify_scaffold.py` passes.

## Phase 1 — Stabilize design into real routes

Convert the current single-file prototype into 8 routes:

| Route | Goal | Acceptance |
|---|---|---|
| `/` | Story/lifecycle landing page | Module 0 active, Modules 1–4 future-state |
| `/portfolio-builder` | Create lead population | filters update preview and call API |
| `/segment-intelligence` | Segment cards + map | card click filters leads, map drill available |
| `/lead-queue` | Ranked borrower table | expandable rows, evidence, score breakdown |
| `/borrower-360/:id` | Borrower/property dossier | masked property/owner refs, lien, triggers, evidence |
| `/offer-orchestrator/:id` | Next-best-offer + approval | draft + approve writes audit |
| `/ask-genie` | Conversational analytics | live Genie call, trusted SQL proof, honest degraded state |
| `/admin-config` | Rules/config | governed rules and refresh-applied thresholds visible |

Implementation steps:
1. Build app shell: top bar, module rail, content container.
2. Add design tokens and reusable components.
3. Add route-level data loading through `frontend/src/lib/api.ts`.
4. Stub repositories live under `tests/fixtures/` and are wired via FastAPI `dependency_overrides` in unit tests — they never ship in the production app. Frontend fixtures under `frontend/src/mocks/fixtureData.ts` are Vitest/Storybook-only and are not imported by production routes.
5. Every route queries live Unity Catalog through `backend/services/repositories/databricks_repo.py`. When a dependency is down the route returns 503 and the frontend renders the degraded-state banner — there is no silent mock fallback (see [CLAUDE.md](../CLAUDE.md) "Negative prompting").

Validation:
```bash
npm --prefix frontend run lint
npm --prefix frontend run test
npm --prefix frontend run build
```

## Phase 2 — Backend API vertical slice

Endpoints:

```text
GET  /api/v1/health
GET  /api/v1/config/options
POST /api/v1/portfolio/preview
POST /api/v1/portfolio/create
GET  /api/v1/segments
GET  /api/v1/leads
GET  /api/v1/borrowers/{borrower_id}
GET  /api/v1/borrowers/{borrower_id}/evidence
POST /api/offers/recommend
POST /api/v1/outreach/draft
POST /api/v1/outreach/approve
POST /api/v1/genie/message
GET  /api/v1/audit/events
POST /api/v1/audit/event
```

Implementation rules:
- Routers call services; services own data access.
- API schemas are Pydantic and mirrored by TS types.
- Live Unity Catalog is the only runtime path — there is no mock-mode toggle in the running app (see [CLAUDE.md](../CLAUDE.md) "Implementation posture"). Fixtures under `tests/fixtures/` and `frontend/src/mocks/` are unit-test/Storybook-only and are never imported by production routers.
- SQL mode uses parameterized queries or validated enum filters.
- Approval endpoint writes to Lakebase; a degraded-state banner surfaces if the breaker opens, but no mock-memory fallback.

Validation:
```bash
pytest -q
curl http://localhost:8000/api/v1/health
```

## Phase 3 — Data foundation

Create SQL models:

1. `property_master`: mastered CLIP/property fields.
2. `owner_property_bridge`: Owner Link to properties.
3. `lien_current`: open lien summary.
4. `property_trigger_features`: equity and AVM-derived features; listing signals are live when MLS rows are present; filed Building Permits stay pending until Cotality/partner shares that feed.
5. `lead_population`: ranked filtered universe.
6. `segment_population`: segment rollups (one row per segment_code/state + national `_ALL`).
7. `lead_scores`: deterministic score components.
8. `borrower_360`: joined borrower story; carries `segment_codes` (ARRAY<STRING>), `recommended_offer_code`, `recommended_offer` inline — next-best-offer is a column, not its own table.
9. `borrower_dossier`: 1:1 with `borrower_360`; pre-joins evidence_events as ARRAY<STRUCT> for single-row `/api/v1/borrowers/{id}` reads.
10. `evidence_events`: traceable source evidence.
11. `lockin_cohort`: 2020-2022 sub-3% originations — retention/HELOC/cash-out addressable cohort.

Data-source request to Cotality:
- Landed: MLS Listings Delta Share, keyed to CLIP and listing status/date.
- Remaining P0: Building Permits Delta Share, keyed to CLIP with permit value/date/type.
- Optional accelerator: Customer 360 and persona/segment samples if Cotality wants to provide precomputed examples, but Module 0 should not depend on those samples.

Validation SQL examples:
```sql
select count(*) from mip.gold.lead_population;
-- segment_population already stores member counts on its `count`
-- column; read that directly (filtering to the '_ALL' national
-- rollup). A COUNT(*) ... GROUP BY segment_code would return 1 per
-- segment (= number of (segment, '_ALL') rows) which validates
-- nothing.
select segment_code, count
  from mip.gold.segment_population
  where state = '_ALL'
  order by count desc;
select recommended_offer_code, count(*) from mip.gold.borrower_360 group by 1;
select * from mip.gold.evidence_events where borrower_id is null limit 10;
```

## Phase 4 — Genie and metric views

1. Create metric views for lead generation, segment performance, borrower opportunity.
2. Add descriptions and synonyms for business terms: in the money, HELOC, CLIP, Owner Link, voluntary lien, permit activity.
3. Configure a Genie space using only curated gold/semantic assets.
4. Add suggested questions.
5. Wire `/ask-genie` to `backend/services/genie_client.py`.
6. If Genie is unavailable, show the honest degraded state and ask the user to retry; do not serve local analytic answers.

Validation:
- Ask: “Which zips have the most in-the-money borrowers?”
- Ask: “Show HELOC candidates with more than $150K equity and recent permits.”
- Confirm SQL references only approved gold/metric-view objects.

## Phase 5 — Lakebase state and audit

Tables:
- campaigns
- portfolio_filters
- saved_leads
- outreach_drafts
- approvals
- agent_sessions
- action_audit
- user_feedback

Acceptance:
- Approving outreach creates an `approvals` row and an `action_audit` row.
- Audit row includes actor, action, entity id, evidence ids, timestamp, request id.
- No raw secrets or sensitive data in audit payload.

## Phase 6 — Agent Bricks / MCP production roadmap

For the Module 0 walkthrough, present these as optional production adapters:

- Property Intelligence Agent: calls CLIP-MCP for property dossier.
- Segment Analyst Agent: uses metric views and Genie.
- Offer Strategy Agent: calls deterministic UC functions / MLflow model.
- Outreach Writer Agent: drafts content, never sends automatically.
- Supervisor Agent: coordinates workflow and writes audit context.

Do not block the walkthrough on live Agent Bricks availability. Use loading choreography and a visible unavailable state if those optional adapters are not configured.

## Phase 7 — CI/CD and deployment

CI:
- Python lint/tests.
- Frontend lint/tests/build.
- Scaffold verifier.
- Secret scan.
- Bundle validate when Databricks env vars exist.

Deployment:
- Dev target: live UC + Lakebase + Genie space, small serverless SQL warehouse. Every target is live; there is no mock-mode target.
- Production certification requires target-specific workspace, catalog, warehouse, Genie, and permissions variables. The repo does not claim a separate production warehouse or Genie space by default.
- Use branch protection and PR reviews.

## Phase 8 — Pre-deployment validation

Run the talk track (dry-run):

1. “These numbers show where to act before pipeline begins.”
2. Build portfolio.
3. Click Prime Refi Candidates.
4. Drill map county → ZIP → borrower.
5. Open borrower 360.
6. Explain evidence and score.
7. Generate/refine offer.
8. Approve and show audit.
9. Ask Genie one question.
10. Close with Module 1–4 expansion story.

Backup path:
- If Genie fails, the circuit breaker trips and `/ask-genie` shows an honest degraded response. Do not claim an answer was computed if no trusted SQL/result was returned.
- If Databricks SQL is slow, the SWR-cached health probe keeps the Console footer honest, the degraded-state banner shows at the top of the page, and the talk track names it as resilience in action. No mock swap.
- If map fails, use table-first layout.
- If Lakebase fails, approval shows an error toast and no audit row is written — fail visibly rather than fake success.
