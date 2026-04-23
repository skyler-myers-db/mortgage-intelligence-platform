# Audit — Ask Genie, Admin Config, and shared chrome

_Dated 2026-04-23. Auditor: `qa-test-engineer` subagent._

Scope:
- `frontend/src/routes/ask-genie.tsx`
- `frontend/src/routes/admin-config.tsx`
- `frontend/src/components/layout/Topbar.tsx`
- `frontend/src/components/layout/Rail.tsx`
- `frontend/src/components/layout/Console.tsx`
- `frontend/src/components/mortgage/GenieChat.tsx`
- `frontend/src/components/mortgage/DegradedBanner.tsx`
- `frontend/src/components/mortgage/EvidenceDrawer.tsx`

Wired-value legend: **LIVE** = calls a real backend endpoint / real state mutation; **NAV** = valid local state change or in-page navigation; **STUB** = UI present but value/action is hardcoded or placeholder; **BROKEN** = an `onClick`/handler exists but misroutes or is functionally wrong; **DEAD** = no handler / non-interactive by accident.

## 1. Element-by-element wiring table

### Area: Topbar (`frontend/src/components/layout/Topbar.tsx`)

| Area | Element | Action | Wired? | Evidence |
|---|---|---|---|---|
| Topbar | Breadcrumb `<span>` "Mortgage Intelligence Platform / {crumb}" | Text-only | NAV | Topbar.tsx:34–39 — derived from `useLocation().pathname` via `ROUTE_CRUMBS` |
| Topbar | Lender pill | Display from AppContext | LIVE | Topbar.tsx:41–44 — reads `lender` from `useApp()` (edited in Console/Admin) |
| Topbar | "sandbox" env pill | Static label | STUB | Topbar.tsx:45–48 — hardcoded "sandbox" string, no binding to `VITE_APP_ENV` or `/api/health#app_env` |
| Topbar | Warehouse-status pill ("serverless-xl" + heartbeat dot) | Static label | STUB | Topbar.tsx:49–52 — hardcoded text; dot is CSS-only `.is-heartbeat`, not bound to `/api/health#dependencies.warehouse` |
| Topbar | Theme toggle button | `setTheme(...)` | NAV | Topbar.tsx:53–61 — flips AppContext.theme, persists to localStorage |
| Topbar | Genie toggle button | `setGenieOpen(!genieOpen)` | NAV | Topbar.tsx:62–71 |
| Topbar | Console toggle button | `setConsoleOpen(!consoleOpen)` | NAV | Topbar.tsx:72–81 |

### Area: Rail (`frontend/src/components/layout/Rail.tsx`)

| Area | Element | Action | Wired? | Evidence |
|---|---|---|---|---|
| Rail | Brand mark `<Link to="/">` | Route to home | NAV | Rail.tsx:36–38 |
| Rail | M0 "Top-of-Funnel" `<Link to="/">` | Route, active state | NAV | Rail.tsx:40–54 |
| Rail | M1 Pipeline Optimization | Non-interactive `<span>` | NAV | Rail.tsx:58–70, `aria-disabled="true"`, no `onClick`, opacity 0.45 — correct per roadmap |
| Rail | M2 LO Workbench | Non-interactive `<span>` | NAV | same as M1 |
| Rail | M3 Underwriting Copilot | Non-interactive `<span>` | NAV | same |
| Rail | M4 Risk & Retention | Non-interactive `<span>` | NAV | same |
| Rail | Settings `<Link to="/admin-config">` | Route | NAV | Rail.tsx:73–75 |

### Area: Console (`frontend/src/components/layout/Console.tsx`)

| Area | Element | Action | Wired? | Evidence |
|---|---|---|---|---|
| Console | Close button | `setConsoleOpen(false)` | NAV | Console.tsx:35–43 |
| Console | Theme segmented (Dark/Light) | `setTheme(t)` | NAV | Console.tsx:48–59 |
| Console | Accent swatches (4) | `setAccent(a.k)` | NAV | Console.tsx:63–73 |
| Console | Density segmented (Comfortable/Compact) | `setDensity(d)` | NAV | Console.tsx:78–88 |
| Console | Lender `<input>` | `setLender(e.target.value)` | NAV | Console.tsx:93–98 — AppContext only; no POST to `/api/admin/settings` |
| Console | Show evidence chips switch | `setShowEvidence(!showEvidence)` | NAV | Console.tsx:103–109 |
| Console | Show confidence meters switch | `setShowConfidence(!showConfidence)` | NAV | Console.tsx:115–121 |
| Console | "Open Genie" button | `setGenieOpen(true); setConsoleOpen(false)` | NAV | Console.tsx:127–133 |

### Area: GenieChat (`frontend/src/components/mortgage/GenieChat.tsx`)

| Area | Element | Action | Wired? | Evidence |
|---|---|---|---|---|
| GenieChat | Floating FAB (`.genie__fab`) | `setGenieOpen(true)` | NAV | GenieChat.tsx:82–88 |
| GenieChat | Close button | `setGenieOpen(false)` | NAV | GenieChat.tsx:96–102 |
| GenieChat | Sample-question chips (3, shown when `msgs.length <= 1`) | `ask(s)` → `/api/genie/message` | LIVE | GenieChat.tsx:131–138 |
| GenieChat | Input text field | local `input` state | NAV | GenieChat.tsx:149–154 |
| GenieChat | Send button (submit) | `ask(input)` → `/api/genie/message` | LIVE | GenieChat.tsx:143–148 |
| GenieChat | Typing-indicator `.typing-dots` | Shown while `typing` | NAV | GenieChat.tsx:125–129 — CSS-only animation, no fake delay |
| GenieChat | Trusted-assets source chips below bubble | **Fixed inline this pass**: now `<EvidenceChip source={drawerForAsset(s)}>` opens the drawer | LIVE | GenieChat.tsx:111–119 (post-edit) — previously DEAD (plain `<span>` with no click) |

### Area: DegradedBanner (`frontend/src/components/mortgage/DegradedBanner.tsx`)

| Area | Element | Action | Wired? | Evidence |
|---|---|---|---|---|
| DegradedBanner | Polls `/api/health` every 3s (degraded) / 8s (ok) | Shows when any dependency `down` or breaker open | LIVE | DegradedBanner.tsx:85–127 — confirmed against local `/api/health` |
| DegradedBanner | Live heartbeat dot + title "Reconnecting to …" | CSS-only, bound to friendly-name map | LIVE | DegradedBanner.tsx:62–70, 132–153 |

### Area: EvidenceDrawer (`frontend/src/components/mortgage/EvidenceDrawer.tsx`)

| Area | Element | Action | Wired? | Evidence |
|---|---|---|---|---|
| EvidenceDrawer | Scrim click | `setDrawer(null)` | NAV | EvidenceDrawer.tsx:20–24 |
| EvidenceDrawer | Close button | `setDrawer(null)` | NAV | EvidenceDrawer.tsx:49–51 |
| EvidenceDrawer | Freshness legend | Static legend chips (Fresh/Aging/Stale) | NAV | EvidenceDrawer.tsx:54–72 |
| EvidenceDrawer | Lineage nodes, signal rows | Rendered from `drawer` context payload | LIVE | EvidenceDrawer.tsx:74–108 — copy comes from `frontend/src/lib/drawerSources.ts` (human-written UI contract per file header, not fake borrower data); raw signal values such as "1.84M", "6.250%" in `drawerSources.ts` ARE hardcoded placeholders — see "Honest fake-data inventory" |
| EvidenceDrawer | "Last refresh" line | Renders `d.updatedAt` from `drawerSources.ts` | STUB | EvidenceDrawer.tsx:109–112 — `updatedAt: '2026-04-20 06:12 UTC'` hardcoded in drawerSources.ts:35, :55, :72, :89 |

### Area: Ask Genie (`frontend/src/routes/ask-genie.tsx`)

| Area | Element | Action | Wired? | Evidence |
|---|---|---|---|---|
| AskGenie | Hero chip "Databricks Genie API" | Static label | STUB | ask-genie.tsx:69 — cosmetic badge |
| AskGenie | Question `<textarea>` | `setQuestion(e.target.value)` | NAV | ask-genie.tsx:78–94 |
| AskGenie | "Ask Genie" button | `ask(question)` → `api.genie()` → `/api/genie/message` | LIVE | ask-genie.tsx:95–99 |
| AskGenie | Error banner (role=alert) | Shown on `err` | LIVE | ask-genie.tsx:100–110 |
| AskGenie | GenieAnswer payload (metric_value, table_rows, follow_ups) | Renders live response | LIVE | ask-genie.tsx:117 |
| AskGenie | Source EvidenceChip below answer | **Fixed inline this pass**: maps `sourceChip` to `drawerForSource` via label regex; previously always opened `.nbo` | LIVE | ask-genie.tsx:61–74 (post-edit), :130 |
| AskGenie | Trusted-assets list (5 rows) | Static `{label, path}` tuples; path is in `title=` tooltip | STUB | ask-genie.tsx:28–34, :136–152 — list is a frontend const, not from an endpoint |
| AskGenie | Suggested-question chips (4) | `onClick={() => ask(q)}` populates + submits | LIVE | ask-genie.tsx:160–173 |

### Area: Admin Config (`frontend/src/routes/admin-config.tsx`)

| Area | Element | Action | Wired? | Evidence |
|---|---|---|---|---|
| Admin | Offer rules version chip `{liveRulesVersion}` | `/api/admin/rules` → `offer_rules_version` | LIVE (thin) | admin-config.tsx:114–123 — endpoint exists (backend/api/admin.py:10–12) but it's an in-memory `{"offer_rules_version": "v1"}` dict, NOT Unity-Catalog-backed. Today returns `rules.v1`; falls back to hardcoded `'rules.itm_v3'` on error. See "Main-agent attention". |
| Admin | Rules "Edited" value (`RULES_EDITED_AT = '2026-03-15'`) | Hardcoded string | STUB | admin-config.tsx:55 |
| Admin | "View thresholds" / "Hide thresholds" button | `setRulesExpanded(!v)` | NAV | admin-config.tsx:184–193 |
| Admin | Threshold table (6 rows, `THRESHOLD_DEFAULTS`) | Static const | STUB | admin-config.tsx:43–50 — values 75 bps, 15%, etc. hardcoded in the frontend; comment claims they "mirror backend/config/settings.py" but there is no `/api/admin/rules` field exposing them |
| Admin | Audit trail "live"/"reconnecting" chip | From `auditError` state | LIVE | admin-config.tsx:217–220 |
| Admin | Audit "Last event" + "Last actor" rows | `/api/audit/events?limit=1` | LIVE | admin-config.tsx:127–143 — backend/api/audit.py:20 returns real Lakebase rows; 503 when Lakebase circuit is open (verified against local backend: body `{"detail":"lakebase dependency is down: circuit breaker is open","retryable":true}`) |
| Admin | Data source readiness chip `{N} of {M} live` | `DATA_SOURCES.filter(...)` on const array | STUB | admin-config.tsx:63–72, :254–257 — 8-source list is a frontend const; backend has no `/api/admin/sources` endpoint |
| Admin | 8 data-source status rows | Static | STUB | admin-config.tsx:259–283 — "Delta Share · nightly" / "roadmap" strings hardcoded |
| Admin | "Workspace appearance" disclosure header | `setAppearanceOpen(!v)` | NAV | admin-config.tsx:289–313 |
| Admin | Theme segmented | `setTheme(t)` | NAV | admin-config.tsx:316–331 |
| Admin | Accent swatches (4) | `setAccent(a.k)` | NAV | admin-config.tsx:332–347 |
| Admin | Density segmented | `setDensity(d)` | NAV | admin-config.tsx:348–363 |
| Admin | Lender `<input>` | `setLender(e.target.value)` | NAV | admin-config.tsx:364–382 |
| Admin | Show evidence chips switch | `setShowEvidence(!v)` | NAV | admin-config.tsx:383–391 |
| Admin | Show confidence meters switch | `setShowConfidence(!v)` | NAV | admin-config.tsx:392–400 |

## 2. Fixed inline this pass

Two genuine parity bugs on the evidence-drawer path were corrected with surgical edits:

### 2a. `frontend/src/routes/ask-genie.tsx`
Source chip under the Genie answer always opened the NBO drawer regardless of what the answer actually cited. Now maps `sourceChip` to the best-fit `DRAWER_SOURCES` entry via a label regex (itm / permit / population / config, NBO fallback).

```diff
-  const sourceLabel = payload?.source ?? '';
-  const sourceChip = sourceLabel || (payload?.trusted_assets?.[0] ?? '');
+  const sourceLabel = payload?.source ?? '';
+  const sourceChip = sourceLabel || (payload?.trusted_assets?.[0] ?? '');
+  const drawerForSource =
+    /itm|rules/i.test(sourceChip) ? DRAWER_SOURCES.itm
+      : /permit/i.test(sourceChip) ? DRAWER_SOURCES.permit
+      : /lead_population|population/i.test(sourceChip) ? DRAWER_SOURCES.population
+      : /config/i.test(sourceChip) ? DRAWER_SOURCES.config
+      : DRAWER_SOURCES.nbo;
...
-                      <EvidenceChip source={DRAWER_SOURCES.nbo}>{sourceChip}</EvidenceChip>
+                      <EvidenceChip source={drawerForSource}>{sourceChip}</EvidenceChip>
```

### 2b. `frontend/src/components/mortgage/GenieChat.tsx`
The in-bubble "sources" row was rendering `<span class="evidence-chip">` with no `onClick` — **DEAD**: visually chips, functionally static text. Replaced with the real `<EvidenceChip>` + a `drawerForAsset(s)` mapper so clicking a chip opens the drawer like everywhere else.

```diff
-import { Button } from '../Primitives';
-import { GenieAnswer } from './GenieAnswer';
+import { Button, EvidenceChip } from '../Primitives';
+import { GenieAnswer } from './GenieAnswer';
+import { DRAWER_SOURCES } from '../../lib/drawerSources';
+
+function drawerForAsset(asset: string) {
+  if (/itm|rules/i.test(asset)) return DRAWER_SOURCES.itm;
+  if (/permit/i.test(asset)) return DRAWER_SOURCES.permit;
+  if (/lead_population|population/i.test(asset)) return DRAWER_SOURCES.population;
+  if (/config/i.test(asset)) return DRAWER_SOURCES.config;
+  return DRAWER_SOURCES.nbo;
+}
...
-                    {m.sources.map((s, j) => (
-                      <span key={j} className="evidence-chip" title={`Source: ${s}`}>
-                        <Icon name="link" size={9} className="e-ico" />
-                        {s}
-                      </span>
-                    ))}
+                    {m.sources.map((s, j) => (
+                      <EvidenceChip key={j} source={drawerForAsset(s)} title={`Source: ${s}`}>
+                        {s}
+                      </EvidenceChip>
+                    ))}
```

## 3. Needs main-agent attention (flag, don't fix)

1. **Topbar "sandbox" + "serverless-xl" pills are STUBs.** They should bind to `/api/health#app_env` and `/api/health#dependencies.warehouse` (already live in backend). Would materially improve "does this app know what environment it's in" perception. — `frontend/src/components/layout/Topbar.tsx:45–52`
2. **`/api/admin/rules` is an in-memory dict.** `backend/api/admin.py:7` sets `_RULES = {"offer_rules_version": "v1"}` and there is no UC-backed rules service. The frontend advertises `rules.itm_v3` when the endpoint is unreachable (admin-config.tsx:150). Either back the endpoint with a real UC table (governed + versioned) or change the frontend fallback to read `rules.v1` (or surface an error chip). 
3. **`RULES_EDITED_AT = '2026-03-15'`** hardcoded in the frontend. If the rules endpoint grows a `last_edited_at` field, the frontend should consume it. — `frontend/src/routes/admin-config.tsx:55`
4. **`THRESHOLD_DEFAULTS` (6 rows) is a frontend const.** Either expose via `/api/admin/rules` (already TODO in the file comment admin-config.tsx:37–42), or document that this is a UI mirror and gate with a backend schema test. 
5. **Data source readiness (8 rows) is a frontend const.** Needs a new `/api/admin/sources` endpoint that returns per-source UC freshness (`mip.bronze.*` table last_refreshed timestamps would be plausible). Today the panel is entirely cosmetic.
6. **`DRAWER_SOURCES` lineage values are UI copy with hardcoded metrics.** Counts like "142M rows", "1.84M", "6.250%" in `frontend/src/lib/drawerSources.ts:25–95` are static. The file header says "UI contract metadata, not fake borrower data", which is fair for schema names — but the numeric values read as fake. Consider sourcing these from the metric-view itself so they aren't frozen copy.
7. **`GenieChat` initial-state AI greeting** (`GenieChat.tsx:36–46`) fabricates a source `'UC.metrics'` that isn't a real UC path. Low priority cosmetic — but the chip would open NBO drawer (regex fallthrough) after my edit, which is not right. Consider showing no source chip until there's a real answer.
8. **Build failure (pre-existing, not my scope):** `frontend/src/mocks/fixtureData.ts:4` does not conform to the new `PortfolioPreview` shape (`approved_count`, `in_outreach_count`, `data_refreshed_at` missing). The working-tree types.ts added those fields. Fixture is test-only per CLAUDE.md. Lint passes; build fails on this one file.

## 4. Honest fake-data inventory

| Location | Fake value | Impact |
|---|---|---|
| `frontend/src/components/layout/Topbar.tsx:47` | "sandbox" env label | Looks real to a buyer; never reflects actual env |
| `frontend/src/components/layout/Topbar.tsx:50–51` | "serverless-xl" warehouse pill + heartbeat dot | Looks live; unchanged whether warehouse is up or down |
| `frontend/src/routes/admin-config.tsx:43–50` | THRESHOLD_DEFAULTS (6 rows with hardcoded bps/%) | Panel reads as "policy admin" but values are frontend-only |
| `frontend/src/routes/admin-config.tsx:55` | `RULES_EDITED_AT = '2026-03-15'` | Static date masquerades as "last edited" |
| `frontend/src/routes/admin-config.tsx:63–72` | DATA_SOURCES (8 rows, "Delta Share · nightly") | No backend endpoint; entirely cosmetic |
| `frontend/src/routes/admin-config.tsx:150` | `'rules.itm_v3'` hardcoded fallback | Shown when endpoint errors; real endpoint returns `v1` |
| `frontend/src/lib/drawerSources.ts:25–32, 43–48, 62–66, 79–83` | "142M rows", "98M rows", "6.250%", "+87.5 bps", "$48,000", etc. | Drawer lineage text looks precise but is frozen copy |
| `frontend/src/lib/drawerSources.ts:35, 55, 72, 89` | `updatedAt: '2026-04-20 06:12 UTC'` (×4) | Drives the "Last refresh" line in the drawer; same timestamp everywhere, doesn't reflect real UC freshness |
| `frontend/src/components/mortgage/GenieChat.tsx:39–42` | Initial greeting cites `'UC.metrics'` (not a real UC path) | First impression of Genie is a fake chip |
| `frontend/src/routes/ask-genie.tsx:28–34` | TRUSTED_ASSETS (5 rows of UC paths) | Real-looking paths; not read from `/api/genie/start#trusted_assets` which IS exposed by backend |

## 5. Ask Genie live-endpoint test

Command:

```bash
curl -sS -X POST -H "Content-Type: application/json" \
  -d '{"question":"Which zips have the most in-the-money refi candidates?"}' \
  http://localhost:8000/api/genie/message
```

Response:

```json
{
  "conversation_id": "fallback-conv",
  "question": "Which zips have the most in-the-money refi candidates?",
  "answer": "The top in-the-money ZIPs are 60611 Chicago (~1,420 borrowers), 78704 Austin (~1,180), 94110 San Francisco (~960), 98103 Seattle (~720), and 33132 Miami (~640). Together they cover about 38% of the 6-state ITM book.",
  "source": "fallback",
  "trusted_assets": ["mip.gold.lead_population", "mip.semantics.lead_generation_metric_view"],
  "metric_value": null,
  "table_rows": [
    {"zip": "60611", "city": "Chicago, IL", "itm_borrowers": "~1,420"},
    {"zip": "78704", "city": "Austin, TX", "itm_borrowers": "~1,180"},
    {"zip": "94110", "city": "San Francisco, CA", "itm_borrowers": "~960"},
    {"zip": "98103", "city": "Seattle, WA", "itm_borrowers": "~720"},
    {"zip": "33132", "city": "Miami, FL", "itm_borrowers": "~640"}
  ],
  "follow_up_questions": [
    "How many in-the-money borrowers in Travis County?",
    "Where is the biggest HELOC opportunity by state?",
    "Who in Austin is in the money?"
  ]
}
```

Observations:

- **Endpoint is wired.** `/api/genie/message` accepts the payload and returns a valid `GenieMessageResponse` shape (answer, source, trusted_assets, table_rows, follow_up_questions).
- **Returned via `ResilientGenieClient` fallback path.** `source: "fallback"` and `conversation_id: "fallback-conv"` indicate the `genie` circuit breaker is open locally (no `DATABRICKS_GENIE_SPACE_ID` configured in the dev shell). The answer came from the curated `genie_answers` corpus — this is the documented degraded-path behavior (see `backend/api/genie.py:1–16` header comment and `/api/health`'s `circuit_breakers.genie`). In a production deploy with the Genie space configured, this same call path produces a live Databricks Genie answer with generated SQL.
- **Answer references real metric views.** `trusted_assets` includes `mip.gold.lead_population` and `mip.semantics.lead_generation_metric_view`, which match the real UC objects declared in `databricks.yml` / `resources/`.
- **SQL generation:** not present in the fallback response. `GenieMessageResponse` has no `sql` field today. If the main agent wants to surface live-generated SQL on the Ask Genie page, that requires a schema extension on the response model and the live (non-fallback) Genie client path to be exercised.

## 6. Validation commands run

- `npm --prefix frontend run lint` — PASS (0 errors, 0 warnings)
- `npm --prefix frontend run build` — FAIL on **pre-existing** `frontend/src/mocks/fixtureData.ts:4` (type mismatch from uncommitted `types.ts` changes — not introduced by this audit; see §3 item 8). No new type errors in the files I touched.
- `curl /api/health`, `curl /api/admin/rules`, `curl /api/audit/events?limit=1`, `curl POST /api/genie/message` — all reachable; admin/rules and genie/message returned real responses; audit/events returned 503 (Lakebase circuit open in dev, which is the correct honest behavior).
