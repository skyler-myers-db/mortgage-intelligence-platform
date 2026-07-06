# Module 0 — Real Data Migration Plan

> **Internal implementation artifact. Not approved for public release.**
> Contains provider/share/table inventory and historical implementation
> planning intended for Entrada, Databricks, and Cotality implementation
> reviewers only.

> **Note:** This plan describes the migration from mock fixtures to live Unity Catalog. The migration completed in the live-data cutover (commit `2f09424`), and `MIP_MOCK_MODE` has been removed — the app now runs on live UC + Lakebase unconditionally. The nine-slice sequence below is preserved as a historical design record. For the current runtime contract see [CLAUDE.md](../CLAUDE.md) "Implementation posture" and [docs/data-contract-module0.md](data-contract-module0.md).

**Author:** principal-architect subagent
**Date:** 2026-04-21
**Branch:** `feature/module0-agentic-scaffold` (source) → `feature/module0-real-data` (child)
**Companion doc:** [docs/data-sources-gap-analysis.md](data-sources-gap-analysis.md)

---

## 1. Executive summary

We are migrating the Module 0 customer walkthrough from end-to-end mock fixtures to a Unity-Catalog-backed real data path sourced from the provisioned Cotality Delta Share (`cotality_mortgage_data.corelogic`, 5 tables, 103M rows). Geography coverage is discovered from gold rollups instead of hardcoded in the app, so a larger future share should flow through without a route/component rewrite. The work is structured as nine independently committable vertical slices that each leave `MIP_MOCK_MODE=true` fully functional, preserving the walkthrough's zero-network-dependency property. The single largest risk is parity drift between the frozen SQL scoring primitives (`fn_lead_score`, `fn_in_the_money`, `fn_rate_spread`, `fn_next_best_offer`) and the Python mirrors in `backend/services/scoring.py` — if real-data plumbing silently changes rounding, thresholding, or NULL semantics, the golden fixtures stop protecting us and every downstream screen goes wrong at once. The second-largest risk is PII leakage: the share contains real owner names and situs addresses, and the current `backend/api/*` routers unconditionally import `mock_data` with no `MIP_MOCK_MODE` seam, so an unreviewed wiring change could surface real names in the UI. We therefore introduce the mock-vs-live seam (Slice 0) *before* any SQL or service wiring changes.

## 2. Vertical-slice sequence

Slices are ordered so each is (a) PR-worthy on its own, (b) reversible in one revert, (c) leaves the walkthrough intact, and (d) unblocks the next slice. Target one commit per slice; target one PR per slice except where explicitly bundled.

### Slice 0 — Introduce the mock/live service seam
- **Intent:** Add the `MIP_MOCK_MODE` boundary to routers so later slices can wire real data without a big-bang switch.
- **Files touched (exact):**
  - `backend/config/__init__.py` (new `settings.mock_mode` helper)
  - `backend/services/__init__.py` (add `get_borrower_repo()`, `get_portfolio_repo()`, `get_evidence_repo()` factory functions)
  - `backend/services/repositories.py` (new — defines `BorrowerRepository`, `PortfolioRepository`, `EvidenceRepository` Protocols; `MockBorrowerRepository` etc. adapt the existing `mock_data` module unchanged)
  - `backend/api/portfolio.py`, `backend/api/leads.py`, `backend/api/segments.py`, `backend/api/borrowers.py`, `backend/api/offers.py`, `backend/api/outreach.py` (replace direct `mock_data` imports with repo factories; zero behavior change when `MIP_MOCK_MODE=true`)
  - `tests/unit/test_api_routes.py` (add one parametrized test proving all app routers still return identical payloads under `MIP_MOCK_MODE=true`)
  - `tests/unit/test_repositories.py` (new — unit-test the factory returns the mock impl when mock mode is on)
  - `.env.example` (document `MIP_MOCK_MODE` default `true`)
- **Owner:** backend-databricks-engineer
- **Acceptance:** Every existing test continues to pass with `MIP_MOCK_MODE=true`. Grepping `backend/api/` for `from backend.services import mock_data` returns zero hits.
- **Validation:** `ruff check backend tests tools && MIP_MOCK_MODE=true pytest tests/unit -q && npm --prefix frontend run build`
- **Blast radius:** Local only (no network, no workspace). Pure refactor.
- **Rollback:** `git revert` — no data state to unwind.
- **Depends on:** —

### Slice 1 — Ingest FRED MORTGAGE30US into silver
- **Intent:** Stand up the only required public dataset so `fn_rate_spread` has a market-rate denominator for real borrowers.
- **Files touched (exact):**
  - `sql/ddl/001_catalogs_schemas.sql` (replace placeholder — create `mip` + schemas `raw,silver,gold,semantics,app,audit`)
  - `sql/transformations/silver_market_rates_weekly.sql` (new — target `mip.silver.market_rates_weekly`)
  - `pipelines/lakeflow/mip_market_rates_pipeline.py` (new — small Python task: fetch FRED CSV, upsert to silver table; idempotent on `observation_date`)
  - `resources/jobs.yml` (add `mip_refresh_market_rates` task, daily schedule off, manual-trigger in dev)
  - `tools/fred_fetch.py` (new — deterministic offline fetcher with cached CSV under `tests/fixtures/fred_mortgage30us.csv` so tests don't hit the network)
  - `tests/integration/test_market_rates_pipeline.py` (new — runs pipeline against cached CSV, asserts row count + schema)
- **Owner:** data-modeler (SQL + pipeline), backend-databricks-engineer (job wiring)
- **Acceptance:** `mip.silver.market_rates_weekly` is materialized in workspace with ≥260 weekly rows back to 2021-01-01; repeat runs are idempotent; unit test seeds from cached CSV and passes offline.
- **Validation:** `ruff check backend tests tools && pytest tests/integration/test_market_rates_pipeline.py -q && databricks bundle validate -t dev`
- **Blast radius:** Workspace (writes one silver table). External on first fetch (FRED HTTPS); cached thereafter.
- **Rollback:** `DROP TABLE mip.silver.market_rates_weekly` + `git revert`.
- **Depends on:** Slice 0 (not strictly, but merges cleanly after it).

### Slice 2 — Silver transformations for lien + property + mortgage events
- **Intent:** 1:1 typed lift from the share into `mip.silver.*`, state-filtered to the configured Module 0 state set.
- **Files touched (exact):**
  - `sql/transformations/silver_property_master.sql` (replace placeholder — from `entrada_eval_property_domain_v3`)
  - `sql/transformations/silver_lien_current.sql` (replace placeholder — from `entrada_eval_voluntary_lien_status_marketing_v2`)
  - `sql/transformations/silver_owner_property_bridge.sql` (replace placeholder — Owner Link rollup via `owner_1_identifier`)
  - `sql/transformations/silver_mortgage_events.sql` (new — from `entrada_eval_mortgage_domain_v1`)
  - `sql/transformations/silver_owner_transfer_events.sql` (new — from `entrada_eval_owner_transfer_domain_v1`)
  - `pipelines/lakeflow/mip_feature_pipeline.py` (replace placeholder — orchestrate the 5 silver SQLs above)
  - `notebooks/00_validate_cotality_share.py` (add row-count + null-rate sanity assertions per §1 of gap-analysis doc)
  - `tests/integration/test_sql_queries.py` (add schema-shape assertions for each silver table using `information_schema`)
- **Owner:** data-modeler
- **Acceptance:** All 5 silver tables materialized. Row counts match current non-null Cotality source-state coverage within ±5%. No fixed state-list filter is present; geography coverage is discovered from refreshed gold rollups. Validation notebook passes without manual intervention.
- **Validation:** `databricks bundle run mip_refresh_silver -t dev` then `pytest tests/integration/test_sql_queries.py -q`
- **Blast radius:** Workspace (writes 5 silver tables, ~10M rows total after state filter). No PII surfaces to app yet.
- **Rollback:** `DROP SCHEMA mip.silver CASCADE` + `git revert`.
- **Depends on:** Slice 1.

### Slice 3 — Gold tables: borrower_360, evidence_events, lead_scores, lead_population
- **Intent:** Precompute the ranked demo surface. Component scores feed the frozen `fn_lead_score`; keep all scoring thresholds in SQL mirrors of Python primitives.
- **Files touched (exact):**
  - `sql/ddl/003_gold_tables.sql` (replace placeholder — gold table DDL with NOT NULL constraints on score columns)
  - `sql/transformations/gold_borrower_360.sql` (replace placeholder — join silver_lien_current + silver_property_master + owner bridge + latest mortgage event per CLIP)
  - `sql/transformations/gold_evidence_events.sql` (replace placeholder — timeline view per CLIP from silver_mortgage_events + silver_owner_transfer_events + foreclosure_stage)
  - `sql/transformations/gold_lead_scores.sql` (replace placeholder — computes 5 component sub-scores per gap-analysis §6, then invokes `mip.gold.fn_lead_score`)
  - `sql/transformations/gold_lead_population.sql` (replace placeholder — top-N ranked cut for the demo surface; default N=500 per metro)
  - ~~`pipelines/lakeflow/mip_gold_pipeline.py`~~ (RETIRED slice13-accuracy: the DLT was a dual-write mirror of the CTAS chain; authoritative gold materialisation is now the `mip_refresh_scores` job in `databricks.yml`.)
  - `sql/metric_views/lead_generation_metric_view.sql`, `segment_performance_metric_view.sql`, `borrower_opportunity_metric_view.sql` (replace placeholders)
  - `tests/integration/test_gold_parity.py` (new — **contract test**: run `fn_lead_score` against `tests/fixtures/lead_score_golden.json` on the warehouse, assert exact integer equality with Python `scoring.lead_score`; repeat for all four primitives)
- **Owner:** data-modeler (SQL), qa-test-engineer (parity test)
- **Acceptance:** Gold tables materialize without cross-catalog reads (only read `mip.silver.*`). All four SQL primitives produce identical output to Python primitives on all golden fixtures. `gold_lead_population` has ≤500 rows per metro for demo responsiveness.
- **Validation:** `databricks bundle run mip_refresh_silver -t dev && pytest tests/integration/test_gold_parity.py -q && pytest tests/unit -q`
- **Blast radius:** Workspace (writes 4 gold tables + 3 metric views). **No PII surfaces yet — app still on mocks.**
- **Rollback:** `DROP SCHEMA mip.gold CASCADE; DROP SCHEMA mip.semantics CASCADE` + `git revert`.
- **Depends on:** Slice 2.

### Slice 4 — Live `DatabricksBorrowerRepository` + `DatabricksPortfolioRepository`
- **Intent:** Wire the `BorrowerRepository` / `PortfolioRepository` Protocols from Slice 0 to `mip.gold.*` via the Databricks SQL connector. **Opt-in via `MIP_MOCK_MODE=false`; mock stays the default for walkthrough.**
- **Files touched (exact):**
  - `backend/services/databricks_sql.py` (replace placeholder — connection helper using `databricks-sql-connector`, reads `DATABRICKS_SERVER_HOSTNAME`, `DATABRICKS_HTTP_PATH`, `DATABRICKS_TOKEN`, warehouse from `DATABRICKS_WAREHOUSE_ID`)
  - `backend/services/repositories.py` (add `DatabricksBorrowerRepository`, `DatabricksPortfolioRepository` implementing the Protocols; queries only `mip.gold.*`)
  - `backend/services/__init__.py` (factory dispatches by `MIP_MOCK_MODE`)
  - `backend/services/pii_redaction.py` (new — owner-name → initials, street-number truncation to block-level; enforced at repository boundary, not router)
  - `backend/schemas/*.py` (add `source_evidence_count: int` to borrower/lead schemas so evidence drawer parity is preserved)
  - `tests/integration/test_databricks_repository.py` (new — hits real warehouse when `DATABRICKS_TOKEN` present; skipped otherwise with `pytest.skip("no workspace creds")`)
  - `tests/unit/test_pii_redaction.py` (new — golden-fixture redaction rules)
- **Owner:** backend-databricks-engineer (repositories), governance-security-reviewer (PII redaction review — blocking)
- **Acceptance:** `MIP_MOCK_MODE=true` demo is byte-identical to before. `MIP_MOCK_MODE=false` returns real-data borrowers with redacted names (initials) and block-level addresses. Governance reviewer signs off on `pii_redaction.py` before merge.
- **Validation:** `ruff check backend && pytest tests/unit -q && MIP_MOCK_MODE=false pytest tests/integration/test_databricks_repository.py -q && MIP_MOCK_MODE=true pytest -q`
- **Blast radius:** Local (workspace reads only, no writes). App reads real names unless redaction is on (enforced).
- **Rollback:** Set `MIP_MOCK_MODE=true` in the Databricks App env; `git revert`.
- **Depends on:** Slice 0, Slice 3.

### Slice 5 — Live evidence + audit plumbing
- **Intent:** Evidence drawer and audit surfaces read from real silver/gold + Lakebase.
- **Files touched (exact):**
  - `backend/services/evidence.py` (replace placeholder — query `mip.gold.evidence_events` for a CLIP, return chronological timeline)
  - `backend/services/lakebase.py` (replace placeholder — `psycopg` client with connection pooling; reads `LAKEBASE_DATABASE_NAME`, workspace identity auth)
  - `backend/services/audit_store.py` (extend to write approvals + agent sessions to Lakebase instead of in-memory)
  - `lakebase/schema.sql` (replace 1-line placeholder — tables: `campaigns`, `approvals`, `agent_sessions`, `audit_events`, `feedback`)
  - `lakebase/seed_campaigns.sql` (new — deterministic Summit Mortgage seed rows for demo)
  - `tests/integration/test_lakebase_round_trip.py` (new — ephemeral schema + round-trip per table)
- **Owner:** backend-databricks-engineer
- **Acceptance:** Evidence drawer opens on real CLIPs and shows 3–7 real events with dates. Approving an offer writes a row to `audit_events` visible in `/api/audit`. `MIP_MOCK_MODE=true` path uses in-memory store still (unchanged).
- **Validation:** `pytest tests/integration/test_lakebase_round_trip.py -q && MIP_MOCK_MODE=false pytest tests/integration -q`
- **Blast radius:** Workspace (Lakebase writes). No outbound network.
- **Rollback:** `DROP SCHEMA mip_app CASCADE` in Lakebase; `git revert`.
- **Depends on:** Slice 4.

### Slice 6 — Reconcile lender footprint + talk track
- **Intent:** Derive the lender geography story from refreshed coverage so Summit Mortgage's book and the narrative match the data. Update fixtures and talk track.
- **Files touched (exact):**
  - `backend/services/mock_data.py` (reshape `PORTFOLIO`, `BORROWERS` to sit inside chosen metro; keep borrower IDs `demo-borrower-*`)
  - `frontend/src/mocks/demoData.ts` (mirror the reshape)
  - `docs/module0-talk-track.md` (rewrite geography paragraphs)
  - `tests/unit/test_scoring.py`, `tests/e2e/module0.spec.ts` (update metro-specific assertions)
- **Owner:** demo-storyteller (narrative), backend-databricks-engineer (fixture reshape)
- **Acceptance:** Walkthrough uses one consistent metro across mock and real paths. Talk track references that metro. Playwright e2e still passes.
- **Validation:** `pytest -q && npx playwright test tests/e2e/module0.spec.ts`
- **Blast radius:** Local.
- **Rollback:** `git revert`.
- **Depends on:** Slice 4 (so the decision is informed by what the real data shows).

### Slice 7 — Real-data Genie space grounding
- **Intent:** Point the existing Genie space at `mip.semantics.*` metric views instead of the current mock SQL. Deterministic fallback (`genie_answers.py`) stays the walkthrough path.
- **Files touched (exact):**
  - `resources/apps.yml` (no change — genie_space_id var stays)
  - `genie/space_config.yml` (if present; otherwise the provisioner in `tools/mcp/`) — refresh table grants to include the 3 metric views
  - `backend/services/genie_client.py` (replace placeholder — thin wrapper over Databricks Genie API; timeout 5s, fallback to `genie_answers.py`)
  - `tests/integration/test_genie_client.py` (extend — real-API test skipped without creds; offline fallback test always runs)
- **Owner:** backend-databricks-engineer
- **Acceptance:** With Genie creds present, `/ask-genie` calls the real API. Without creds or on 5s timeout, falls back to deterministic answers (unchanged behavior). Walkthrough path does NOT require live Genie.
- **Validation:** `pytest tests/integration/test_genie_client.py -q`
- **Blast radius:** Workspace (Genie grants only).
- **Rollback:** `git revert`; Genie space grants restored from prior snapshot.
- **Depends on:** Slice 3.

### Slice 8 — Bundle + CI + smoke gate
- **Intent:** Make `databricks bundle validate -t dev` green with real resources and add a CI smoke job that proves golden fixtures still match after any change to scoring-adjacent SQL.
- **Files touched (exact):**
  - `.github/workflows/ci.yml` (add `scoring-parity` job that runs `pytest tests/integration/test_gold_parity.py -q` against a nightly workspace; and a `bundle-validate` job that runs `databricks bundle validate -t dev`)
  - `databricks.yml` (no structural change; confirm `sql_warehouse_id` / `genie_space_id` BUNDLE_VAR mechanism works end-to-end)
  - `docs/runbook.md` (add "walkthrough-day checklist": warehouse warm-start, mock fallback toggle, rollback steps)
  - `docs/testing.md` (document the mock vs real test matrix)
- **Owner:** qa-test-engineer (CI), principal-architect (runbook)
- **Acceptance:** Pushing to `feature/module0-real-data` runs unit + integration (mock) + bundle-validate; nightly runs add the parity test against the workspace.
- **Validation:** `databricks bundle validate -t dev && gh workflow run ci.yml` (or equivalent dry-run)
- **Blast radius:** CI only.
- **Rollback:** `git revert`.
- **Depends on:** Slices 0–7.

## 3. Dual-path design — the `MIP_MOCK_MODE` seam

**The seam is `backend/services/repositories.py` (introduced in Slice 0).** It defines three `typing.Protocol`s — `BorrowerRepository`, `PortfolioRepository`, `EvidenceRepository` — each with a narrow set of methods that maps 1:1 to a frontend concern. `backend/services/__init__.py` exposes `get_borrower_repo()` etc. as factory callables that dispatch on `settings.mock_mode` (read from env once at process start).

Routers ONLY depend on the Protocols, never on concrete classes. `mock_data.py` is adapted (not rewritten) by a `MockBorrowerRepository` class that returns the existing Pydantic objects. `DatabricksBorrowerRepository` (Slice 4) executes parameterized SQL against `mip.gold.*` and passes every result through `pii_redaction.py` before returning.

**Invariant:** the offline walkthrough path is `MIP_MOCK_MODE=true`, which is the `.env.example` default. No network calls fire. No warehouse queries fire. No Lakebase connection opens. The mock path continues to be golden-fixture-pinned via the existing `scoring.py` primitives, which do NOT change.

**Invariant:** `MIP_MOCK_MODE=false` is opt-in. Toggling it requires `DATABRICKS_TOKEN` (or workspace identity), `DATABRICKS_WAREHOUSE_ID`, `DATABRICKS_SERVER_HOSTNAME`, `DATABRICKS_HTTP_PATH`, and (for audit) `LAKEBASE_DATABASE_NAME`. Missing creds fail fast at startup with a clear error, not silently at first request.

**Fallback:** If `MIP_MOCK_MODE=false` and the warehouse times out at request time, the router returns a 503 with `retry-after: 60` — it does NOT silently fall back to mock data, because mixing modes during a demo is strictly worse than a visible error.

## 4. Risk register

| # | Risk | Owner | Mitigation |
|---|---|---|---|
| R1 | **Scoring parity drift.** Someone edits `fn_lead_score.sql` or `scoring.py` without regenerating the fixture, and real-data path diverges from mock. | qa-test-engineer | Slice 3 adds `test_gold_parity.py` which runs all four golden fixtures through both the SQL UDF (via warehouse) and the Python primitive, asserting integer equality. Nightly CI (Slice 8) catches drift within 24h. Governance-reviewer gate on any PR touching `sql/uc_functions/` or `backend/services/scoring.py`. |
| R2 | **Real PII leak.** Share contains real owner names + situs addresses. Current routers import `mock_data` directly — a careless Slice-4 shortcut could push raw names into the UI. | governance-security-reviewer | `pii_redaction.py` (Slice 4) enforced at the repository boundary, not the router. Unit tests pin exact redaction rules (initials for names, block-level lat/lon, street-number masked). Governance-reviewer sign-off is blocking on Slice 4 PR. `MIP_MOCK_MODE=true` default keeps offline walkthroughs on synthetic data. |
| R3 | **Lakeflow pipeline cost runaway.** Full-fat silver builds scan source rows; gold joins multiply that. Unbounded re-runs on a large warehouse become expensive fast. | backend-databricks-engineer | 2X-Small serverless warehouse with `auto_stop_mins: 10` (set in `databricks.yml`). Silver requires non-null state/CLIP keys and gold discovers current coverage dynamically. Bundle-declared jobs run on manual trigger only in dev (no schedule). Monitor first full run; set a workspace budget alert before enabling any schedule. |
| R4 | **Warehouse auto-stop during walkthrough.** 10-min auto-stop means first query after intermission is a 30–60s cold start; ruins the walkthrough pacing. | demo-storyteller + performance-optimizer | Runbook (Slice 8) adds "warm-start 5 minutes before each walkthrough block" step. Additionally: keep offline walkthroughs on `MIP_MOCK_MODE=true` by default; use real-data toggle only in sit-down meetings where a 30s cold start is acceptable. Frontend shows a "loading real data…" skeleton, not a blank state. |
| R5 | **Genie API flakiness during walkthrough.** Real Genie can return 500s or rate-limit. Switching to the fallback mid-answer is visible. | backend-databricks-engineer | `genie_client.py` (Slice 7) always tries deterministic `genie_answers.py` first when `MIP_MOCK_MODE=true`. In sit-down mode, 5s timeout + pre-cached answers for the three canonical questions from the talk track. A failed real-API call silently uses the cached answer; a failed cache lookup returns a deterministic "Let me connect you to a specialist" response (never an exception bubble). |

## 5. Open decisions requiring user input

### D1. Which metro anchors Summit Mortgage's book?
- **Options:** Chicago (largest share), Seattle (wealthy + high equity — great HELOC story), Denver (smallest but tightest data — easy to walk through).
- **Recommendation:** **Seattle (WA).** 0.74M properties, 0.49M with open liens, avg 4.16% first-position rate → strong in-the-money pool; 47% avg C-LTV → strong HELOC pool. The appreciation narrative (WA home prices vs. refi economics) is the cleanest demo story.
- **Tradeoff:** Chicago has more raw volume; Denver walks through faster. Picking Seattle costs one day of fixture re-anchoring but gives the best screen-time story.

### D2. Confirm dual-path (`MIP_MOCK_MODE`) as the seam?
- **Recommendation:** **Confirm, exactly as described in §3.** Any alternative (factory-by-config-file, runtime hot-swap, per-request header) adds surface area without demo value.
- **Tradeoff:** A single env var is coarse — you can't run half-mock-half-real. That's fine: partial modes produce confusing screenshots.

### D3. Workspace auth: PAT or workspace identity?
- **Options:** PAT in `.env.local` (simple, already documented) vs. workspace identity via Databricks App native credentials (the bundle-managed path).
- **Recommendation:** **Workspace identity in the deployed app; PAT for local dev only.** The app runtime (`app.yaml` → `python -m backend.runtime`) picks up workspace identity automatically; `.env.local` carries a PAT for `uvicorn` local.
- **Tradeoff:** Two auth paths = two failure modes. Mitigated by making `backend/services/databricks_sql.py` one function that probes both.

### D4. PII handling: redact at repo boundary or at router boundary?
- **Recommendation:** **Redact at the repository boundary** (`DatabricksBorrowerRepository.get(...)` calls `pii_redaction.redact(...)` before returning). Routers never see raw PII — they can't accidentally log or serialize it.
- **Tradeoff:** The repository knows about the UI's PII posture, which is a minor layering violation. The alternative (router-level redaction) is worse: it spreads PII knowledge across eight routers and gives the agent orchestrator raw names too.

### D5. Golden fixtures: extend to cover real-data edge cases?
- **Recommendation:** **Yes, but additively — never modify existing cases.** Add `case_20+` rows sampled from real production edges (e.g., an absentee-corporate-owner multi-property row) to lock real-data behavior. Existing `case_01`–`case_19` stay frozen because they protect the scoring math itself.
- **Tradeoff:** More fixtures = slower parity test. At ~30 cases total, still <1s per primitive.

## 6. Test surface plan

| Slice | Unit (mocks) | Integration (real UC) | E2E (Playwright) | Contract (SQL↔Python) |
|---|---|---|---|---|
| 0 | `test_api_routes.py` (mock paths unchanged), `test_repositories.py` (factory dispatch) | — | Existing `module0.spec.ts` (unchanged) | — |
| 1 | `test_market_rates_pipeline.py` (against cached FRED CSV) | Pipeline materializes `silver.market_rates_weekly` in dev workspace | — | — |
| 2 | — | `test_sql_queries.py` extended for 5 silver tables | — | — |
| 3 | — | `test_gold_parity.py` runs all four primitives on dev workspace | — | **Golden fixtures as SQL-vs-Python contract: lead_score, rate_spread, in_the_money, next_best_offer** |
| 4 | `test_pii_redaction.py` (golden redaction) | `test_databricks_repository.py` (skipped without creds) | `module0.spec.ts` parametrized for `MIP_MOCK_MODE={true,false}` | Continues from Slice 3 |
| 5 | — | `test_lakebase_round_trip.py`, `test_audit_persistence.py` | Approval → audit row visible in `/api/audit` | — |
| 6 | Existing `test_scoring.py` re-anchored to chosen metro | — | `module0.spec.ts` updated geography assertions | — |
| 7 | `test_genie_fallback.py` (unchanged) | `test_genie_client.py` extended for real API (skipped without creds) | Ask-Genie deterministic flow unchanged | — |
| 8 | — | — | Nightly CI runs mock + real matrix | Parity test runs nightly against dev workspace |

**Baseline invariants across all slices:**
- `MIP_MOCK_MODE=true pytest -q` must be 100% green on every commit.
- `ruff check backend tests tools` must be clean.
- `npm --prefix frontend run build` must pass (UI parity unchanged).
- `databricks bundle validate -t dev` must pass once `BUNDLE_VAR_sql_warehouse_id` and `BUNDLE_VAR_genie_space_id` are set.

## 7. Commit / branch / PR strategy

- **Source branch:** `feature/module0-agentic-scaffold` (current).
- **Child branch for this work:** `feature/module0-real-data`, created off the current HEAD.
- **Commit cadence:** one commit per slice, signed message following the established style (`feat(services): introduce mock/live repository seam`, `feat(sql): land silver lift for voluntary_lien + property`, etc.).
- **PR cadence:** one PR per slice, merged into `feature/module0-real-data`. The parent branch gets a single rollup PR into `main` once Slices 0–8 are green and walkthrough dry-run passes.
- **Why not PR each slice straight to `main`:** the child branch lets `main` stay walkthrough-ready at any hour; the rollup PR gives governance-security-reviewer one final checkpoint before the real-data path can be opened in a workspace anyone watches.
- **Hard gates before the rollup PR merges:**
  1. All slice PRs merged and CI green.
  2. Governance-reviewer sign-off on PII redaction (Slice 4).
  3. Offline walkthrough dry-run end-to-end with `MIP_MOCK_MODE=true` (no regression).
  4. Sit-down walkthrough dry-run end-to-end with `MIP_MOCK_MODE=false` against dev workspace.
  5. `docs/module0-talk-track.md` updated to reference real metro + real numbers.
  6. Rollback runbook in `docs/runbook.md` tested by flipping `MIP_MOCK_MODE` live.

---

**Ready to execute on approval.** Suggested first action: confirm D1 (metro) and D3 (auth posture), then delegate Slice 0 to `backend-databricks-engineer` with a strict "no behavior change in mock mode" acceptance bar.
