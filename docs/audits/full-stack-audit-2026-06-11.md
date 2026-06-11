# Module 0 — Full-Stack Critical Audit

> **Internal validation artifact — not approved for public release.** Point-in-time
> findings for the Entrada delivery team. State/coverage figures below are
> observations of the share footprint at audit time; product coverage is dynamic
> and must never be pinned to a fixed state list. Validated independently and
> remediated on `fix/audit-2026-06-11-remediation` (see docs/modernization-todo.md).

**Date:** 2026-06-11 (gating Data+AI Summit demos, week of June 15)
**Repo:** `main` @ `99e55d6`, working tree clean — one merge behind the remediation baseline `fb2154a`, which had already fixed the drawer-freshness P2 below
**Live app:** https://mip-app-2543889327043640.aws.databricksapps.com (RUNNING; data refreshed Jun 10, 11:41 PM EDT)
**Method:** three parallel deep code audits (backend / frontend+design / SQL+bundle+deploy), live-app Chrome session at ~1440×900 (all routes, network timings, console, flows), Mac desktop visual signoff, validation runs, and cross-checks against the Module 0 spec (docx), the Apr 2026 Functional Spec (FUS), the partnership deck, 10 Granola meetings (Jan 15–Jun 5), Gmail threads, and the claude.ai design share link.

---

## Verdict

**No P0 blockers. The app is demo-ready on the existing dev workspace.** Engineering quality is well above demo-grade: no mock fallback anywhere in production paths, parameterized SQL throughout, real retry/circuit-breaker/warm-start resilience, append-only audited approvals, clean console across every route, and ~100% BEM/token design parity with the committed prototypes.

Six P1s need attention before June 15 (one scoring-parity bug that surfaces as a false "integrity gap" in the proof drawer, a forked design contract vs the share link, two fresh-workspace deploy gaps, a broken narrative seed, and the `/v1/leads` hot-path latency). Everything else is scheduled hardening.

### Scorecard

| Area | Grade | Notes |
|---|---|---|
| Backend architecture & security | A− | Excellent injection/secrets/authz posture; router-layer drift, one parity bug |
| Frontend & design parity | A | Zero console errors; class/token parity ~100%; one CTA bug, one word-break bug |
| SQL / data plane | A− | Exceptional documentation & idempotency; CTAS metadata loss, MERGE dedup gaps |
| Bundle / deploy | B+ | Strong orchestrator + smoke gates; zero-click contract broken on fresh workspaces |
| Live UX & performance | B+ | DCL ~200 ms, most APIs <1 s; `/v1/leads` 3.6–5.4 s; Genie 23 s |
| Data quality (live) | A− | 5.16M borrowers across the six states observed in the current share / consistent cross-surface math; 2 segments awaiting Cotality feeds |
| Spec & commitment coverage | A− | All 10 docx capabilities implemented or honestly pending external data |

---

## P0 — none found

## P1 — fix before June 15

**P1-1. `lead_score` Python/SQL parity bug → false "integrity gap" warnings in the proof drawer (~0.7% of borrowers).**
`backend/services/scoring.py:141-148` computes the weighted score in binary float then `round()`; `sql/uc_functions/fn_lead_score.sql:48-63` computes in exact DECIMAL with `BROUND`. On exact-.5 boundaries float drift decides the direction: verified example `(92,94,94,85,25)` → SQL 86, Python 85; random sampling shows **0.666% divergence**. `_build_borrower_proof` (`backend/services/repositories/databricks_borrowers.py:699-711`) recomputes and appends a customer-visible *"Recomputed opportunity score does not match…"* gap — ~1 in 150 borrowers shows a false data-integrity warning in the exact surface the demo sells. The 12 golden fixtures all sit where float and decimal agree, so unit + live parity tests pass while production diverges. **Fix:** compute with `decimal.Decimal` + `ROUND_HALF_EVEN` in `scoring.py`; add a Hypothesis property test and a golden case in the drift zone. Small, isolated, highest demo ROI. (Live spot-check during this audit: B-18BPIE96UKQFW recomputed 84 = 84 ✓ — the bug is probabilistic, not constant.)

**P1-2. The design contract has forked: claude.ai share link ≠ committed `design_files/` ≠ app copy.**
The share link (claude.ai/design/p/ec8ebe9a…, verified live in Chrome) currently renders: tenant **"Acme Lending"**, hero **"Find borrowers worth a call."**, KPI set *Marketable population 284,120 / In-the-Money 12,840 / **Avg NBO Confidence 78/100** / **Queued for Outreach 1,284 (needs approval)***, workspace breadcrumb "entrada-mortgage-ai / Module 0: Top-of-Funnel", and `demo.sandbox` / `serverless-xl` pills. The committed `design_files/Module 0 Prototype.html:2145` has hero "Who should we contact, why now, and with what offer?" (which the app matches) and contains no Acme/Summit tenant at all. The app follows the repo contract and CLAUDE.md's mission anchors — but not the share link's current iteration; its design-session log also shows the prototype file was recently broken and rebuilt (the embedded preview rendered blank on two of three loads). **Decision needed:** either re-sync `design_files/` from the design project (then reconcile hero/KPI/tenant deltas — noting "Acme Lending" itself violates the CLAUDE.md `Summit Mortgage` naming rule), or bless the repo snapshot as canonical and update the share. Until then, "the design follows the link" is not literally true.

**P1-3. Zero-click deploy is broken on a fresh workspace (UC grants are manual).**
`docs/security/GRANTS.md` is a 382-line copy-paste runbook a metastore admin must run after first deploy; `sql/ddl/001_catalogs_schemas.sql:21-29` confirms no GRANT is issued by the bundle, and `scripts/deploy.sh` has no grants step. On a clean workspace the app boots to `PERMISSION_DENIED` on every endpoint and the deploy fails at smoke — with manual SQL as the documented remediation. CLAUDE.md calls this exact pattern a packaging bug. Already satisfied on the Entrada dev workspace (demo unaffected). **Fix:** idempotent grants step in deploy.sh (resolve app SP via `databricks apps get`) or bundle `resources.schemas` with `grants:` blocks.

**P1-4. PII-salt secret (`mip`/`pii-salt-v1`) is required by the silver pipeline but never provisioned, and both documented fallbacks are illusory.**
`pipelines/lakeflow/mip_feature_pipeline.py:116-131` references a "secret-scope preflight" that doesn't exist anywhere; `_PII_SALT_FALLBACK` (line 82) is dead code; the warehouse path's `COALESCE(TRY_CAST(secret(…)))` (`sql/transformations/silver_property_master.sql:69-78`) doesn't survive a missing scope. Fresh-workspace deploys fail mid-silver-refresh with an unexplained secret error. **Fix:** create scope + random salt in deploy.sh before the silver step; document in `.env.example`; wire or delete the fallback.

**P1-5. Lakebase seed approvals use legacy 5-digit borrower IDs (`B-48291…B-48295`) that violate `B-[0-9A-Z]{13}` and join to no real borrower.**
`lakebase/seed_campaigns.sql:83-140` — the "canonical trio pinned by the product narrative" resolves to zero rows in `gold.borrower_360` (IDs derive from CLIP hashes, `gold_borrower_360.sql:102`). The Module 0 spec explicitly requires a final panel with "three high-value borrower examples" — the seed meant to guarantee that story is orphaned. Approval-rate metrics skip the rows; the audit UI shows malformed IDs. **Fix:** regenerate from three stable demo CLIPs; add a CHECK or test on ID format.

**P1-6. Live hot-path performance: `/v1/leads` 3.6–5.4 s on every load; Home "Ask Genie" CTA does a full page reload.**
Measured live (three separate loads, warm app): leads 5,360 ms / 4,562 ms / 3,604 ms while sibling calls run 0.2–2.2 s — the ranked-borrowers table is the slowest thing in the product and it's on the two hero routes (Leads, Segments). Recommend a longer-TTL cache entry (or pre-warmed materialization) for the default-filter top-500. Related one-liner: `frontend/src/routes/home.tsx:340` uses `window.location.href='/ask-genie'` — the only non-SPA navigation in the app; visible white-flash reload on a hero CTA (fix: `<Link>`/`useNavigate`). Genie end-to-end was 22.8 s against the in-UI promise of 10–20 s — pre-open a warmed session before booth slots.

## P2 — schedule (selected; full details in agent findings below)

1. **Data-estate label columns word-break mid-word on Home** ("Loan originat/ion and applica/tion") at 1440-class widths, and collapse to one-character-per-line vertical text when the Console rail is open (verified live + zoom). The demo flow likely opens the Console for theme switching — fix `min-width`/`word-break` on `.de-source` label cells before the booth.
2. **Two of six hero segment cards show "Awaiting feed"** (Listed for Sale, Permit Activity) — frontend behavior is correct and honest; the dependency is Cotality MLS (governance approval in progress) and Building Permits (partner approval) per Granola. Decide the talk-track framing now; chase Mark/Eric this week.
3. **Evidence drawer shows "Freshness Unavailable"** for hero KPIs while the topbar shows "Refreshed Jun 10, 11:41 PM" — wire the refresh anchor into source freshness or hide the empty state; mixed signals undercut the evidence story.
4. **Router-layer drift (backend):** approval-transaction SQL, outreach copywriting, eligibility policy and ~500 lines of duplicated Genie guardrail blocks live in routers (`backend/api/outreach.py:91-474`, `genie.py:120-830`, `leads.py`, `audit.py`, `config.py`) — pure-move refactors to services.
5. **No app-level role gate on `/outreach/approve|reject`** — any workspace user with app access can approve (attribution is correct; authorization is absent; `sales.py` shows the pattern to mirror). Confirm intent for Module 0 or add an approver role.
6. **Audit `action_audit` INSERT not idempotent under retry** (`backend/services/audit_lakebase_store.py:18-29`) — duplicate ledger rows possible on ack-loss; mirror the Genie `ON CONFLICT` pattern.
7. **Fair-lending prompt guard over-blocks** standalone "age"/"white"/"male" — "average loan age", "White Plains" refuse on stage; rehearse around it; add context-aware exemptions later. Also: each Genie panel open fires a fresh `/v1/genie/start` (three observed in one session) — check session reuse; and Genie sometimes echoes a clarifying question before answering anyway (space-instruction polish).
8. **Every gold CTAS erases liquid clustering, comments, and TBLPROPERTIES** declared in DDL (all 13+ `CREATE OR REPLACE TABLE` transformations; `gold_property_owner_bridge.sql:12-14` claims otherwise). Column comments are part of the Genie grounding story. Move to `INSERT OVERWRITE` or re-declare in CTAS.
9. **`resources/*.yml` and `jobs/*.yml` are dead, drifted mirrors** — `databricks.yml` has no `include:`; the mirrors already diverge and CLAUDE.md still points at them. Delete or wire; update CLAUDE.md.
10. **Silver MERGEs lack source-side dedup guards** — one duplicate CLIP in a share refresh fails the whole pipeline chain days before the conference; add `QUALIFY ROW_NUMBER()=1`.
11. **`equity_pct` and `ltv` derive from different sources** (`gold_borrower_360.sql:291-312`) and can visibly disagree in the dossier in front of mortgage professionals (live spot-checks were consistent).
12. **Metric-view COMMENTs advertise measures that don't exist as columns** (`borrower_opportunity_metric_view.sql`), and these are plain views labeled `metric_view` in the Genie space — invites hallucinated SQL.
13. **`prod` target `run_as` is a user** while its own checklist says service principal (`databricks.yml:91-114`); align with current DAB guidance (deploy prod from CI as the SP).
14. **No Python type-checking step** (mypy/pyright absent from Makefile/CI) despite "strong types" goal; ESLint globally disables `react-hooks/set-state-in-effect` with no expiry (vs the dated file-size allowlist pattern).
15. **Token divergence undocumented:** `--text-3` differs from prototype in both themes (`tokens.css:162,197`) with no rationale comment — every other deviation carries one; annotate or revert.

## P3 — polish backlog (compact)

Dependencies: pydantic 2.10.4→2.13.4, databricks-sdk 0.103→0.116, uvicorn 0.47→0.49; React 19.2.5→.7, Vite 8.0.9→.15 (post-Summit). A11y: `aria-modal` on the non-modal Genie panel; `aria-sort` missing on sortable headers; no jsx-a11y static lint (runtime axe suites cover both themes). Hygiene: nine `tmp-live-*.png` at repo root; `tsconfig.tsbuildinfo` tracked despite gitignore; dead `demo-borrowers.ts`/`tokens.ts`; FILE_MANIFEST.md badly stale (157 vs 815 files, ghost entries); CHANGELOG stuck at 0.1.0 (May 17); CLAUDE.md drift ("eight routes" vs 11, `demoData.ts` vs `fixtureData.ts`, resources/ claim, vestigial `sql_warehouse_id` wording). Bundle: empty `mip_snapshot_dashboards` job; stale "state-filtered" comment (pipeline is dynamic); Genie space script-provisioned not bundle-declared (documented). Data: Admin readiness shows "FRED Market Rates 1 rows" (likely `is_latest` count — confusing display); Retention Risk segment count is 8 (verify the story expects this); state seed omits DC/PR/VI display names; `skyler@entrada.ai` shipped in seeds/tests (use a demo alias for customer deploys); deploy.sh references `.env.local.example` but template is `.env.example`; snapshot-date and freshness-sentinel edge cases; Lakebase lifecycle wipe-then-sync window; file-size allowlist expires 2026-06-21 (six days after the Summit — re-date deliberately); Genie FAB is 0×0 at desktop width (entry via topbar icon — confirm vs prototype's fixed FAB); Cotality ID-mask HMAC falls back to a compile-time constant without `MIP_COTALITY_ID_MASK_SECRET` (have deploy enforce); CSP `style-src 'unsafe-inline'`; `/sales/distribute` N+1 (bounded); TTLCache can't cache falsy.

---

## Live-app session evidence (Chrome, viewport 1456×837; Mac desktop verification)

**Console: zero errors or warnings across the entire session** (Home, Analytics, Portfolio, Segments, Leads, Borrower 360, Offer, Ask Genie, Admin, Console panel, theme switches, Genie round trip).

| Route | Renders | Key API timings (ms, full reload) |
|---|---|---|
| `/` Home | ✓ hero, 4 KPIs + evidence chips, approval queue (31 approved/3 outreach), data-estate, 6-state choropleth, agent activity log, M1–M4 roadmap | DCL 199, load 236; health 259, geo 769, data-estate 692, audit 609, workspace 1281, analytics 2197 |
| `/analytics` | ✓ exec KPIs, funnel, score distribution, 5 sub-tabs, Ask Genie | workspace 1306; analytics 3446 |
| `/portfolio-builder` | ✓ filters (every covered state, occupancy, lien, equity ≥15%, contactability, consent), run-build KPIs, campaign A/B setup, honest "trend lines hidden" note | campaigns 606; preview 1951 |
| `/segment-intelligence` | ✓ 6 segment cards (2 awaiting feeds), AND-filter logic, ranked top-500, geo drill-down | segments 1871; **leads 5360** |
| `/lead-queue` | ✓ 11 queue filters, sales-ops snapshot (6 LOs/190 capacity), 500 of 233,420, PII suppressed, export | sales/team 659–1816; **leads 4562/3604** |
| `/borrower-360/:id` | ✓ dossier (masked clip/owner refs, AVM 408,612, LTV 7%, 41 related properties, pending-feed flags, synthetic first-party chip) | borrower fetch 1449 (SPA) |
| `/offer-orchestrator/:id` | ✓ primary offer + alternatives ruled out, threshold table (75 bps/15%/35%/25%/50), review-only draft + disclosure chip, approval id `cd1af41c…`, activation dry-run outbox | — |
| `/ask-genie` | ✓ deep-dive with trusted-assets rail (8 `mip.gold.*`) | — |
| `/admin-config` | ✓ versioned offer rules (hash-pinned), live audit trail, source readiness 12/19 live | — |

**Flows verified live:** evidence drawer ⇒ full lineage `cotality.public_records.deed_and_mortgage → voluntary_lien → entity.property_clip → owner_link → metrics.borrower_universe` + PII-boundary statement ✓; proof drawer ⇒ Math/Evidence/Lineage/Reproduce with UC-function formulas, recompute matched (84=84) ✓; row expand ⇒ why-now rationale + 6 decision-input chips ✓; floating Genie from any page ⇒ trusted-badge tabular answer (IL 58,361 / FL 16,582 / TX 14,932 / CA 14,305 / WA 12,208 / CO 801 — sums to the 117,189 KPI exactly ✓), source chip, **governed actions** (open cohort in Lead Queue / create draft campaign) ✓, and the query appeared in the Admin audit trail (`genie.run_query`, my actor) and the home activity log (`view_borrower_360`, `view_borrower_proof`) within seconds — **the audit pipeline is demonstrably live** ✓; Console rail ⇒ theme (light verified), 4 accents, density, read-only tenant, 24 saved leads ✓; keyboard A/R shortcuts documented; approval state + id rendered with activation staged as dry-run only (no outreach sending anywhere) ✓.

**Cross-surface consistency checks:** marketable population 5,156,184 identical on Home / map legend / Admin / data-estate ✓; in-the-money 117,189 = Genie state sum = approval-queue copy ✓; borrower IDs match `B-[0-9A-Z]{13}` ✓; geography spans every state in the current share footprint (six at audit time: IL, CA, FL, TX, WA, CO), no metro filter ✓; rate/LTV/equity math coheres in dossier and proof (e.g., 28,000/408,612 = 7% LTV; +356 bps vs 6.48% par) ✓.

---

## Requirements coverage

**Module 0 spec (docx) — 10 key capabilities:** portfolio builder ✓; public-record Customer 360 via CLIP + Owner Link ✓; in-the-money detection ✓; related-property opportunity ✓ (41-property example live); listing trigger ◐ *blocked on Cotality MLS share*; permit trigger ◐ *blocked on Building Permits share*; investor/multi-property segmentation ✓; home-equity propensity ✓; lead scoring + next-best-offer ✓; drill-down to explainable borrower stories ✓. Data outputs: all present (portfolio defs, segment flags, scores, ITM indicators, offer signals, 360 records, campaign-ready lists, agent action audit log). "Three high-value borrower examples" panel ⇒ undermined by P1-5 seed bug. Components: Delta Sharing/Apps/Genie/Lakebase/dashboards/MLflow ✓; **Agent Bricks** = deterministic orchestrator today (per CLAUDE.md, correctly not claimed as production); **CLIP-MCP** = documented roadmap gap (`docs/module0-alignment-todo.md`, `docs/data-sources-gap-analysis.md`) — flag in the talk track, don't imply it's live.

**FUS guiding principles:** modular ✓ (M0 standalone, M1–M4 roadmap cards); agentic-first with human-in-the-loop ✓ (approval gates everywhere); data-grounded with Lakebase audit ✓ — this is the app's strongest story.

**Meeting/email commitments (Granola, 10 meetings; Gmail threads):** data-lineage visibility "more obvious" (Antoine) ✓ — data-estate panel + chips + lineage drawer; first- vs third-party distinction ✓ — `demo synthetic` vs `live` badges; synthetic-only demo data ✓; all queries live, nothing static ✓; Genie with SQL transparency ✓; 100% Databricks + UC audit trail ✓; 5–10 min booth format ⇒ `docs/module0-talk-track.md` is current (live-run style with degraded-state backups); persona-specific views (VP / sales manager) ⇒ analytics exec tabs + sales-ops snapshot exist — run the persona pass before the booth (open Skyler action item); MLS + permits datasets ⇒ pending Cotality governance/partner approval — the two "awaiting" cards; backup demo person + video fallback (Talia's booth note) ⇒ logistics, not repo. Org items (eval agreement, org ID, Slack access, Encompass instance) are outside repo scope and not blockers for the app.

**CLAUDE.md completion definition:** routes ✓ (11 incl. the contracted 8); live UC/Lakebase, no mock runtime ✓; evidence drawer everywhere on real rows ✓; approval ⇒ Lakebase audit row ✓; `/ask-genie` grounded in the real space with deterministic guards ✓; resilience demonstrable ✓ (breaker registry → `/api/health`, warm-start, degraded-state UI, kill-drill e2e); endpoints on real tables ✓; frontend build ✓ (deployed bundle + CI); unit/integration incl. nightly parity ✓ per CI — with the P1-1 blind spot noted; Playwright e2e on live app ✓ (10 specs incl. 1,315-line real-data nightly, axe per route × theme, 1440/1920/2560 matrix); bundle validate ✓ (dev pinned genie id; `sql_warehouse_id` var now vestigial — doc drift); talk track ✓.

---

## Validations run in this audit

| Check | Result |
|---|---|
| `ruff check backend tests tools` | **PASS** (clean) |
| `eslint . --max-warnings 0` + CSS-literal gate | **PASS** |
| `tools/lint_css_literals.mjs` (vitest twin) | **PASS** |
| `python3 tools/verify_scaffold.py` | **PASS** — 13 required files, no forbidden paths |
| Frontend build / vitest / pytest in audit sandbox | **Not executable** — sandbox ships Python 3.10 (repo requires ≥3.11; managed-Python download blocked) and `tsc -b` exceeds the 45 s command ceiling. Covered instead by: live deployed bundle (this session), `ci.yml` lint+test+build gates, and `nightly.yml` parity runs. Re-confirm CI is green on `99e55d6` before the booth. |
| Live smoke (this session) | **PASS** — 9 routes, 5 interactive flows, 0 console errors, audit writes observed end-to-end |

## Demo-day runbook (June 15–18)

1. Ship P1-1 (Decimal scoring) and the one-line Genie CTA fix; regenerate the narrative seed (P1-5).
2. Resolve the design fork (P1-2) — at minimum decide which hero/KPI/tenant set is canonical before anyone compares the app to the share link on a call (John West demo Thu/Fri; Movement sit-down Tue 3p at Summit).
3. Pre-warm before each slot: open the app 10 min early (warehouse warm-start), run one Genie question (first call ~23 s; pre-warmed is the difference between a pause and a stall), and have the video fallback ready per the booth guidance.
4. Fix or avoid: don't open the Console rail while the Home data-estate section is on screen until the word-break fix lands.
5. Decide the MLS/permits "awaiting feed" framing — it's honest and defensible ("feed lands at governance approval, predicates auto-unblock"), but say it before the audience asks.
6. Rehearse Genie around protected-term false positives ("average loan **age**", "**White** Plains") and the eligible-only count split (5,388 eligible ITM on Segments vs 117,189 total ITM on Home — be ready to explain contactability filtering).
7. Post-Summit: P1-3/P1-4 (fresh-workspace deploy) before any customer/SE deployment; then the P2 list.

---

*Full agent findings (file:line level) are preserved in the audit session. No code was modified during this audit.*
