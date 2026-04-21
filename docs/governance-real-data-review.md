# Module 0 — Governance & Security Review: Real-Data Migration

**Scope:** Pre-implementation risk review for swapping the Module 0 booth demo off synthetic fixtures onto live `cotality_mortgage_data.corelogic` Delta Share.
**Reviewer:** governance-security-reviewer subagent
**Date:** 2026-04-21
**Paired with:** `docs/data-sources-gap-analysis.md` (the migration plan this review gates)
**Target surface:** `backend/services/mock_data.py` and `frontend/src/mocks/demoData.ts` are the UI contract; `backend/schemas/{lead,offer,portfolio,common,why,audit}.py` are the over-the-wire payloads; `backend/services/databricks_sql.py` is the placeholder that will reach the share; `lakebase/schema.sql` is the app-state store (also placeholder).

---

## Executive summary

- **Verdict: CONDITIONAL-GO.** The share is safe to use as a *signal source* for Module 0 at the booth, but **not safe to redisplay raw at the browser**. The three canonical demo borrowers (B-48291, B-48294, B-48295) must remain the synthetic trio seen on stage. Real-data power is exposed via **segment counts, score distributions, geography rollups, and redacted/masked detail** — not via real owner names or real street addresses.
- **Three hard pre-implementation blockers** (see "Top 3 must-fixes" below): (1) a gold-layer PII redaction contract before any `/api/*` response is wired to real data; (2) a Unity Catalog column-mask + row-filter policy on `silver.*` tables that hold names/addresses; (3) a Databricks secret scope (`mip`) that replaces every `.env`-resident token and kills the current `profile: DEFAULT` pathway for app runtime.
- **Licensing stance:** treat the booth as an **external redisplay surface** under Cotality's licensing posture. Assume property/borrower-level detail is **not** licensed for DAIS attendee redisplay. The demo stays on the synthetic trio for dossier-level screens; real-data surfaces are limited to cohort counts, aggregates, and already-obfuscated fields. This is a booth risk decision that needs legal sign-off from Entrada before turning the silver→gold pipeline on against the share.

---

## 1. PII in the share → UI pipeline

The share carries real owner and borrower identity. The UI renders dossier cards, ranked tables, evidence drawers — every one of those is a potential PII egress point. Per-field ruling below.

| Field (source table) | Sensitivity | Ruling | How to enforce |
|---|---|---|---|
| `owner_1_full_name` (property_v3) | **PII (direct identifier)** | **Never in any `/api/*` response.** Hash to `owner_hash = SHA-256(lower(trim(value)) \|\| daily_salt)` for join keys only. Dossier `display_name` must remain the synthetic trio (B-48291/4/5). | Gold DDL: `owner_hash` column only; drop `owner_1_full_name` before writing `gold.borrower_360`. UC column mask `mip.silver.mask_name(x)` returning NULL for non-privileged readers. |
| `owner_2_full_name`, `buyer_1_full_name`, `buyer_2_full_name`, seller names (owner_transfer) | **PII** | Same as above. Hash for join, never surface. | Same column-mask UDF applied at silver. |
| `situs_street_address`, `situs_city`, `situs_zip_code` (property_v3, voluntary_lien) | **Quasi-identifier** (street-level is re-identifying; ZIP + city is not) | **Street not in `/api/*`.** `city`, `state`, `zip` are fine at cohort granularity. `Borrower360.subject_property` must be generalized to `"{city}, {state} {zip}"` — drop the street. | Gold: `gold.borrower_360` selects city/state/zip only; no `situs_street_address` column. Mask at silver for non-privileged. |
| `mailing_street_address`, `mailing_city`, `mailing_state` (property_v3) | **PII (direct mail)** | **Silver-only.** Use for absentee/investor flag derivation (`is_absentee = mailing_zip != situs_zip`), then drop. | Boolean derived column in `gold.borrower_360`; mailing fields never leave silver. |
| `borrower_1_identifier`, `borrower_1_full_name` (mortgage_domain) | **PII** | Never in `/api/*`. Hash for recapture join. | Same redaction pattern as owner_1_full_name. |
| `clip` (all tables) | **Sensitive (quasi-identifier, licensed id)** | **Not in `/api/*` as raw CLIP.** `Borrower360.clip_id` currently emits `clip_demo_*` synthetic strings — keep that pattern. For real data surface `clip_ref` = `SHA-256(clip \|\| daily_salt)[:12]` so the UI can deep-link without leaking the real CLIP. | Gold materializes `clip_ref`; raw `clip` stays in silver for joins. |
| `owner_1_identifier` (Owner Link) | **Sensitive (licensed id)** | Same as `clip` — hash to `owner_link_ref` for UI. Real value stays silver. | Gold: `owner_link_ref` only. |
| `first_position_lender_company_name`, `first_position_currently_assigned_lender_company_name`, `second_position_lender_company_name` (voluntary_lien) | **Competitively sensitive** — real lender names (WELLS FARGO BK NA, etc.) tied to specific properties | **Redact to a controlled vocabulary** before `/api/*`. Map via `ref.lender_ref(real_name) → {Summit Mortgage, Competitor A, Competitor B, ...}` so demo retains "competitor refi" narrative without naming competitors at addresses. | New `ref.lender_dictionary` table; applied in `gold.borrower_360` as `current_servicer_ref` / `origin_lender_ref`. |
| Lender NMLS IDs (mortgage_domain) | **Sensitive** | Silver-only. | Drop from gold projection. |
| Loan amount, interest rate, origination date, term, purpose code (voluntary_lien + mortgage_domain) | **Non-PII in isolation; quasi-identifying at street+amount combo** | **Safe for `/api/*` once address is generalized.** `avm_value`, `current_lien_balance`, `current_rate`, `ltv`, `rate_spread_bps` already in the wire model. | Keep as-is; generalize address per the row above is the mitigation. |
| AVM (`estimated_value_mktg`, equity, LTV) | **Non-PII** | Safe for `/api/*`. | No masking needed. |
| Foreclosure stage, REO indicator (property_v3, owner_transfer) | **Sensitive (distress signal on a real person)** | **Never display distress at the dossier layer for real records.** Aggregate-only at cohort level. Segment 7 (Distress) in the booth stays on the synthetic trio. | Gold: `segment.distress_count` yes; `gold.borrower_360.foreclosure_stage` no for real rows. |
| Tax amount, year, exempt status | Non-PII in isolation | Safe at cohort/geography; not on dossier for real records. | Drop from gold projection on real rows. |
| `block_level_latitude`, `block_level_longitude` | Quasi-identifier at block resolution | **Snap to CBSA or ZIP centroid** before rendering on the map. Never render the raw block-level coords. | Gold: `geo_point = st_centroid(zip_polygon)`; raw lat/lon stays silver. |
| `borrower_1_identifier` hashes across mortgage_domain | Non-PII after hashing | Safe | Use `borrower_hash` consistently across gold tables. |

### The "display trio stays synthetic" rule

`Borrower360.display_name` MUST remain `{"James & Maria Rodriguez", "David Park", "Lisa Thompson"}` for the canonical demo path. Rationale: the dossier view is the screen where a leak would be most damaging, the synthetic trio is pinned by golden fixtures, and there is no demo benefit from swapping these three to real names. Real-data power is shown through **counts, distributions, geography, and evidence timelines** — not through "this is Jane Doe's actual mortgage".

Concretely: `gold.borrower_360` on the real-data path emits two kinds of rows — three **pinned synthetic dossiers** (display_name = trio) merged from `backend/services/mock_data.py`, plus the **real-data cohort** where `display_name` is `f"Borrower {clip_ref[:6]}"` (never the real name). The ranked-borrower table is safe to render for the real cohort because no real name or street is exposed.

### `Borrower360` wire-schema diff (apply before real-data migration)

Fields currently on `Borrower360` in `backend/schemas/lead.py` that need policy:

- `display_name` — OK; contract: synthetic trio OR `Borrower {clip_ref[:6]}`.
- `clip_id` — OK; contract: `clip_demo_*` OR `clip_ref_{12-char-hash}`. Never the raw CLIP.
- `owner_link_id` — same pattern.
- `subject_property` — **change**: drop street; emit `"{city}, {state} {zip}"` only.
- `city`, `state`, `zip` — OK.
- `avm_value`, `current_lien_balance`, `current_rate`, `ltv`, `rate_spread_bps` — OK.
- `evidence_events[].source_table` — **change**: emit logical names (`gold.evidence_events`, `silver.lien_current`), never the raw share table path `cotality_mortgage_data.corelogic.entrada_eval_*`.

**Risk:** High — every `/api/*` route that returns a `Borrower360` is a potential leak point.
**Likelihood:** Near-certain if no explicit redaction layer is introduced (current code would happily plumb whatever a real-data query returns).
**Impact:** Severe — direct PII exposure to DAIS attendees over a public network; Cotality license breach; reputational damage.
**Mitigation:** Gold-layer redaction contract + Pydantic validator on `Borrower360` that **rejects** any `display_name` not matching `^(Borrower [a-f0-9]{6}|James & Maria Rodriguez|David Park|Lisa Thompson)$` in non-mock mode. Ditto for `subject_property` not matching the generalized shape. Fail closed, loudly.
**Owner:** backend + data-modeler.
**Pre-implementation blocker:** **YES.**

---

## 2. Licensing / redistribution

**Posture assumption (needs Entrada legal confirmation):** Cotality's licensing to Entrada is for evaluation/product-development use, not external redisplay at property-or-borrower resolution. A DAIS booth with attendees filming screens is an external redisplay surface.

**Specific risks:**

1. **Real owner names on a demo screen** — The share carries `owner_1_full_name`. Displaying it at the booth is redisplay of licensed PII. Mitigation: per §1, display the synthetic trio only at dossier level.
2. **Real lender names on a competitor-refi narrative** — Showing "Wells Fargo refinanced this borrower away" to an audience that includes Wells Fargo competitors (and possibly Wells Fargo themselves) is a redisplay issue *and* a competitor-intelligence leak. Mitigation: `ref.lender_dictionary` maps real lenders to `Summit Mortgage / Competitor A / Competitor B` before `/api/*`.
3. **Real street addresses on the map** — Block-level lat/lon rendered at zoom-in is property-level identification. Mitigation: snap to ZIP centroid or CBSA polygon only.
4. **Aggregate redistribution** — Segment counts ("12,840 In the Money borrowers in our footprint") are fine. Per-borrower detail is not.
5. **Screenshots and the recording** — The booth will be photographed. Any screenshot containing a real name/address is an after-the-fact redisplay artifact. Mitigation: nothing below the generalization line should ever appear on screen, so screenshots are safe by construction.

**Mitigation bundle:**
- Synthetic overlay for dossier-level screens (trio stays pinned).
- Lender-name redaction vocabulary.
- ZIP-centroid geography on the map.
- One licensed entity (Summit Mortgage, synthetic) is the lens — never "a real lender's real book".
- Mock-mode screenshot parity: every dashboard that's demoed live must be renderable in `MIP_MOCK_MODE=true` with visually identical output, so any publication material is pulled from mock mode.

**Risk:** High.
**Likelihood:** Certain if the raw share fields reach the browser.
**Impact:** Severe — licensing breach with Cotality (contract risk), competitor-intelligence leak (commercial risk).
**Mitigation:** Per §1 + `ref.lender_dictionary` + ZIP-centroid geography.
**Owner:** Entrada legal (sign-off), data-modeler (implementation).
**Pre-implementation blocker:** **YES** — licensing stance needs a legal ACK before the silver→gold pipeline is pointed at the share.

---

## 3. Unity Catalog governance

Target catalog is `mip` with schemas `raw`, `silver`, `gold`, `semantics`, `app`, `audit` per `databricks.yml` (`variables.uc_schemas`).

### Grant model

| Principal | `raw` | `silver` | `gold` | `semantics` | `app` | `audit` |
|---|---|---|---|---|---|---|
| App service principal (`sp-mip-app`) | — | — | `SELECT` | `SELECT` | — | `SELECT` (read-back for audit UI) |
| Data engineers (humans) | `SELECT` | `SELECT`, `MODIFY` | `SELECT`, `MODIFY` | `SELECT`, `MODIFY` | — | `SELECT` |
| Demo operators (humans, read-only at booth) | — | — | `SELECT` | `SELECT` | — | `SELECT` |
| Pipeline job service principal (`sp-mip-etl`) | `SELECT` | `SELECT`, `MODIFY` | `SELECT`, `MODIFY` | `SELECT`, `MODIFY` | — | `MODIFY` |
| Lakebase app writer (audit/approvals) | — | — | — | — | `SELECT`, `MODIFY` | `MODIFY` |

**Key rule:** the app service principal **does not** have access to `silver` or `raw`. This is the enforcement mechanism for §1 — if the code accidentally queries `silver.lien_current.owner_1_full_name`, it fails with `PERMISSION_DENIED` before reaching the browser.

### Column masks (silver layer)

Create UC column-mask UDFs and apply to silver columns that hold PII. Mask returns NULL for anyone not in the `data_engineers` group.

- `mip.silver.mask_name(s STRING)` applied to: `silver.property_master.owner_1_full_name`, `silver.owner_transfer_events.buyer_1_full_name`, `silver.mortgage_events.borrower_1_full_name`.
- `mip.silver.mask_address(s STRING)` applied to: `silver.property_master.situs_street_address`, `silver.property_master.mailing_street_address`.
- `mip.silver.mask_identifier(s STRING)` applied to: `silver.property_master.owner_1_identifier` (returns `SHA-256 hex[:12]`, not NULL — needed for joins by non-privileged readers).

### Row filters

- `silver.*` row filter: `situs_state IN ('IL','CA','FL','TX','WA','CO')` — matches real footprint (per gap analysis §1), rejects accidental out-of-scope queries.
- `gold.borrower_360` row filter: `row_kind IN ('synthetic_trio') OR (row_kind = 'real' AND displayable = true)` — belt-and-suspenders for the `Borrower360` Pydantic validator in §1.

### CTAS pattern (enforcement at write-time, not read-time)

Belt-and-suspenders: instead of relying only on column masks at read, the gold DDL **never selects** the masked columns in the first place:

```sql
CREATE OR REPLACE TABLE mip.gold.borrower_360 AS
SELECT
  substring(sha2(concat(clip, current_date()), 256), 1, 12) AS clip_ref,
  substring(sha2(concat(owner_1_identifier, current_date()), 256), 1, 12) AS owner_link_ref,
  -- display_name derived, NEVER owner_1_full_name
  concat('Borrower ', substring(sha2(concat(clip, current_date()), 256), 1, 6)) AS display_name,
  situs_city AS city, situs_state AS state, situs_zip_code AS zip,
  concat(situs_city, ', ', situs_state, ' ', situs_zip_code) AS subject_property,
  -- economics are safe
  estimated_value_mktg AS avm_value,
  total_amount_of_open_mortgage_liens AS current_lien_balance,
  first_position_mortgage_interest_rate AS current_rate,
  -- lender redacted via ref dictionary
  coalesce(ld.ref_label, 'Competitor X') AS current_servicer_ref,
  ...
FROM mip.silver.lien_current l
LEFT JOIN mip.semantics.lender_dictionary ld
  ON ld.real_name = l.first_position_currently_assigned_lender_company_name
WHERE l.situs_state IN ('IL','CA','FL','TX','WA','CO')
```

The masked columns (`owner_1_full_name`, `situs_street_address`) are **never referenced** by the gold CTAS. A developer has to consciously go edit the DDL to add them, which is the audit signal we want.

**Risk:** Medium.
**Likelihood:** Medium — without masks, a well-intended query from the data-engineer side surfaces raw fields.
**Impact:** Severe if read by the app and plumbed to the browser.
**Mitigation:** Two layers (column masks at silver + gold DDL omits) and the service principal lacks `silver` access anyway.
**Owner:** data-modeler + backend.
**Pre-implementation blocker:** **YES** (masks + grants must exist before `backend/services/databricks_sql.py` points at real tables).

---

## 4. Audit trail

Today's audit surface:
- `backend/services/audit_store.py` is an **in-memory list** (`AuditStore._events`) — loses history on restart, cannot support multi-replica, cannot support post-demo review.
- `backend/schemas/audit.py` captures `actor`, `action`, `entity_type`, `entity_id`, `payload_json`, `evidence_ids`, `created_at`.
- `backend/api/audit.py` exposes `/api/audit/events` and `/api/audit/event`.
- `lakebase/schema.sql` is a **placeholder** (1-line file).

### Gaps against the real-data bar

| Audit need for real data | Today? | Gap |
|---|---|---|
| Who viewed which CLIP/dossier | Partially — only on explicit `/api/audit/event` POST | **Gap:** view-level events aren't emitted. Need to log `view_borrower_360` from `backend/api/borrowers.py` and `view_leads_ranked` from `backend/api/leads.py`. |
| Which scores were rendered (score + component breakdown) | No | **Gap:** `payload_json` on dossier-view events should capture `opportunity_score`, the five components, `rate_spread_bps`, `equity_pct`, thresholds applied. |
| Which outreach was approved, by whom, with what evidence | Yes — `OutreachApproveResponse.audit_event_id` is wired. | OK. |
| Which evidence_ids were cited at the time of the score | Partially — `evidence_ids` captured at approval, not at render. | **Gap:** record `evidence_ids` on view events, so we can reconstruct "the approver saw this exact evidence set". |
| Durable across restart | No — in-memory | **Gap:** must move to Lakebase. |
| Immutable (append-only) | No — Python list, mutable | **Gap:** Lakebase table with no DELETE grant for app writer. |
| Timestamped (UTC) | Yes | OK. |
| Actor identity (real, not `demo-user`) | No — `OutreachApproveRequest.actor` defaults to `"demo-user"` | **Gap:** in the booth this is fine, but for real data we need the authenticated user (Databricks Apps passes `X-Forwarded-Email` / `X-Forwarded-User` headers — plumb them into `actor`). |

### Required Lakebase schema (populate `lakebase/schema.sql`)

```sql
CREATE SCHEMA IF NOT EXISTS mip_app;

CREATE TABLE IF NOT EXISTS mip_app.action_audit (
  event_id       TEXT PRIMARY KEY,
  actor          TEXT NOT NULL,
  action         TEXT NOT NULL,       -- view_borrower_360, view_leads_ranked, approve_outreach, reject_outreach, ...
  entity_type    TEXT NOT NULL,       -- borrower, lead_list, outreach_draft, ...
  entity_id      TEXT NOT NULL,       -- clip_ref (NEVER raw CLIP)
  payload_json   JSONB NOT NULL DEFAULT '{}'::jsonb,
  evidence_ids   TEXT[] NOT NULL DEFAULT '{}',
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_action_audit_entity ON mip_app.action_audit (entity_type, entity_id, created_at DESC);
CREATE INDEX idx_action_audit_actor  ON mip_app.action_audit (actor, created_at DESC);
REVOKE DELETE, UPDATE ON mip_app.action_audit FROM PUBLIC;
```

### `AuditStore` swap

Replace `backend/services/audit_store.py:AuditStore` with a Lakebase-backed implementation in non-mock mode; retain the in-memory one behind a factory for `MIP_MOCK_MODE=true`. The two must share the `AuditEvent` schema and the `.write()/.list()` contract so routers don't change.

### View-event emission

In non-mock mode, add middleware or an explicit call in these routers:
- `backend/api/borrowers.py` — on `GET /api/borrowers/{id}`, emit `action=view_borrower_360`, `entity_id=clip_ref`, payload includes score, components, thresholds.
- `backend/api/leads.py` — on `GET /api/leads/ranked`, emit one `action=view_leads_ranked` event with the list of `clip_ref`s rendered.
- `backend/api/offers.py` — on `POST /api/offers/recommend`, emit `action=view_offer_recommendation`.

**Risk:** Medium.
**Likelihood:** Medium — without this we lose the "who saw what" answer if an incident happens.
**Impact:** Moderate for the booth demo; severe for any post-demo customer evaluation.
**Mitigation:** Lakebase schema + view-event emission + `actor` from header.
**Owner:** backend + data-modeler.
**Pre-implementation blocker:** No (can ship in the slice that flips mock→real, not before).

---

## 5. Secrets

### Current state

- `app.yaml` declares `MIP_MOCK_MODE=true` and `APP_ENV=demo`. **Good** — no secrets.
- `backend/config/settings.py` reads `DATABRICKS_HOST`, `DATABRICKS_WAREHOUSE_ID`, `GENIE_SPACE_ID` from env (not secrets by themselves, but close to PAT-adjacent) and loads `.env` + `.env.local`.
- `.env.example` is tracked. Risk is that a developer copies it to `.env` and commits — `.gitignore` review needed.
- The `profile: DEFAULT` usage in `databricks.yml` and the `.env.local` Makefile flow are developer-local; they must **not** be the production app runtime pattern.

### Required state for real data

**All live-data tokens go in a Databricks secret scope — never in `.env`, `app.yaml`, logs, or screenshots.**

Create scope `mip` and populate:
- `mip/workspace_host` — workspace URL (not technically a secret, but colocated for discoverability)
- `mip/sql_warehouse_id` — warehouse id
- `mip/genie_space_id` — genie space id
- `mip/lakebase_host` + `/lakebase_user` + `/lakebase_password` — Lakebase connection
- `mip/fred_api_key` — optional FRED key (ingestion job only, not app)

**App runtime** picks these up via the Databricks App resource binding in `resources/apps.yml` (`sql_warehouse`, `genie_space`, `database` blocks already wired — no PAT needed for these, the App platform issues on-behalf-of tokens). For anything that still needs a PAT (e.g., the FRED ingestion job), read from the secret scope via `dbutils.secrets.get("mip", "fred_api_key")` — never from env.

**Gitignore + pre-commit:**
- Verify `.env`, `.env.local` are in `.gitignore`.
- Add a pre-commit hook that greps staged files for `dapi` (Databricks PAT prefix), `sk-`, `AKIA` (AWS), and common password patterns. Block the commit if found.

**Logs:**
- Add a structured-log redactor that strips any query parameter or header matching `authorization`, `token`, `password`, `secret`, `api_key` before emission. FastAPI default logging will happily print query strings on 500s.

**Screenshots:**
- Any screenshot published (`/*.png` in repo root, `design_files/*.png`) is a pre-real-data artifact. Before the demo, re-take screenshots from `MIP_MOCK_MODE=true` so there's no risk of publishing real-data imagery.

**Risk:** High.
**Likelihood:** Medium — repo already has `.env` loading in settings.py, developer convenience drives accidental commits.
**Impact:** Severe — leaked PAT = workspace-wide access = real-data breach.
**Mitigation:** Secret scope + pre-commit secret scanner + log redactor + `app.yaml` stays clean (it already is).
**Owner:** backend + principal-architect.
**Pre-implementation blocker:** Partial — secret scope must exist and `.env`-resident PATs must be purged before the first real-data slice deploys. Log redactor and pre-commit scanner are can-ship-alongside, not blockers.

---

## 6. Booth-failure posture

`MIP_MOCK_MODE=true` is the fallback. `app.yaml` defaults it to `"true"`. `backend/services/mock_data.py` is the ground truth in that mode. The question is: **what triggers the flip from real to mock mid-demo, and where does the flip logic live?**

### Failure modes to detect

| Failure | Detect at | Detection signal |
|---|---|---|
| SQL warehouse cold start (>5s first query) | `backend/services/databricks_sql.py` | `execute_sql()` timeout ≥ 3s |
| SQL warehouse auth failure (expired token) | `databricks_sql.py` | `databricks.sdk.errors.Unauthenticated` or 401 |
| Network loss to workspace | `databricks_sql.py` | `ConnectionError`, `TimeoutError` |
| Genie API failure | `backend/services/genie_client.py` | non-2xx or timeout |
| Lakebase connect failure | `backend/services/lakebase.py` | `psycopg` `OperationalError` |

### Fallback activation pattern

Put the decision in a single **service-level decorator** `@with_mock_fallback` that wraps each real-data service call. On first failure per-request, it:

1. Logs a structured warning (`booth_fallback_activated`, failure mode, service name) — redacted.
2. Sets a process-wide `circuit_open=true` flag with a 30s cooldown for that service (so we don't thrash).
3. Returns the mock-mode equivalent from `backend/services/mock_data.py`.
4. The `/api/health` endpoint surfaces `{"mode": "real" | "degraded" | "mock", "circuits": {...}}` so the operator knows without leaving the demo.

**Where it lives:** a new `backend/services/resilience.py` wrapping every data-tier service method. This keeps routers ignorant of fallback logic — they always call the service, the service decides whether to hit the warehouse or the in-memory fixtures.

**Emergency kill switch:** the operator can send `POST /api/admin/mock-mode` (already stubbed at `backend/api/admin.py`) to force `MIP_MOCK_MODE=true` for the process without redeploying. This is the ops-console escape hatch.

**Parity contract:** every real-data endpoint must return a response shape **byte-compatible** with its mock counterpart — if the real-data path produces a field the mock path doesn't (or vice-versa), the frontend breaks on fallback. Enforce with a pytest parametrization: each endpoint runs twice (mock + real-mocked-via-fixture), and responses must match schema.

**Risk:** Medium.
**Likelihood:** Medium — Databricks SQL cold starts at 2X-Small are a known DAIS-floor risk.
**Impact:** Severe if unhandled (dead demo); minor if the fallback pattern works (audience sees nothing change).
**Mitigation:** `@with_mock_fallback` decorator + health endpoint mode indicator + admin kill switch.
**Owner:** backend.
**Pre-implementation blocker:** No — can be built as part of the first real-data slice, but must exist before the first booth rehearsal.

---

## Top 3 pre-implementation must-fixes

These block the first real-data slice until resolved. Nothing against the share happens until all three are green.

1. **Gold-layer PII redaction contract — implemented and tested.** `gold.borrower_360` DDL never references `owner_1_full_name`, `situs_street_address`, raw `clip`, raw `owner_1_identifier`, or real lender names. `Borrower360` Pydantic model grows a validator rejecting non-synthetic `display_name` / non-generalized `subject_property` in non-mock mode. Unit test asserts that a hand-crafted real-data row fails validation, and a hand-crafted generalized row passes. Files: `sql/transformations/gold_borrower_360.sql` (new), `backend/schemas/lead.py`, `tests/unit/test_pii_redaction.py` (new).

2. **Unity Catalog governance applied before the service principal reads.** Column masks on silver (`mask_name`, `mask_address`, `mask_identifier`), row filters (`situs_state IN (...)`), and grants: app SP gets `SELECT` on `gold` + `semantics` + `audit` **only**. Verify with a `ruff`-style policy test: a GRANT audit script lists every privilege on `mip` and compares against a checked-in allow-list. Files: `sql/uc_governance/grants.sql` (new), `sql/uc_governance/column_masks.sql` (new), `tests/integration/test_uc_grants.py` (new).

3. **Secret scope `mip` exists and `.env`-resident PATs are purged.** Pre-commit secret-scanner hook blocks `dapi*` patterns. `backend/config/settings.py` reads secrets via the Databricks SDK `WorkspaceClient.secrets` in non-mock mode, never via `.env`. `.env.example` contains **no** sample PAT (redacted to `DATABRICKS_TOKEN=<set-in-secret-scope-not-here>`). Files: `.pre-commit-config.yaml` (new or updated), `backend/config/settings.py`, `.env.example`.

---

## Open questions for the user

Only the minimum — things the user must decide; everything else I've decided internally above.

1. **Legal sign-off on Cotality licensing posture.** Can Entrada confirm (in writing, even a Slack message from legal is fine) that the booth-level redisplay policy in §2 — synthetic trio for dossiers, redacted lender names, ZIP-centroid geography, aggregate-only real data — is within the current Cotality license? If legal is stricter ("no real counts displayed"), we fall back to 100% mock mode for the booth and use real data only in private follow-up demos.

2. **Demo lender footprint.** The real share covers IL/CA/FL/TX/WA/CO. The mock trio lives in Atlanta (GA), which **is not in the footprint**. For the real-data cohort rollups to be geographically coherent with the dossier trio, either: (a) move the synthetic trio to a real-footprint metro (recommend Chicago/IL, largest cohort), or (b) keep trio in GA and explicitly frame them as "here's how it looks for one Summit customer" alongside "here's the real market in the Midwest". Which is the talk track?

3. **Authenticated actor identity at the booth.** Databricks Apps will pass the workspace user in `X-Forwarded-Email`. Is the booth going to run under a single shared demo account, or under each presenter's account? This changes whether `actor` in audit events is meaningful for attribution.

---

## Files touched / proposed

- **Proposed — this review is the deliverable:** `docs/governance-real-data-review.md` (this file).
- **Next-slice implementation targets** (not edited here — flagged for the master agent):
  - `sql/uc_governance/grants.sql` (new)
  - `sql/uc_governance/column_masks.sql` (new)
  - `sql/transformations/gold_borrower_360.sql` (new)
  - `backend/schemas/lead.py` (add validator)
  - `backend/services/resilience.py` (new — `@with_mock_fallback`)
  - `backend/services/audit_store.py` (Lakebase adapter)
  - `lakebase/schema.sql` (populate from placeholder)
  - `.pre-commit-config.yaml` (secret scanner)
  - `backend/config/settings.py` (read from secret scope in non-mock mode)
  - `tests/unit/test_pii_redaction.py` (new)
  - `tests/integration/test_uc_grants.py` (new)

## Validation required before first real-data deploy

```bash
pytest tests/unit/test_pii_redaction.py -q
pytest tests/integration/test_uc_grants.py -q
ruff check backend
databricks bundle validate -t dev
# manual: operator runs `databricks secrets list-scopes` and confirms `mip` exists
# manual: operator runs `git log --all -S 'dapi' --source --remotes` and confirms no PAT ever committed
```
