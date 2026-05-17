# Multi-tenant + lender isolation audit

> **Internal validation artifact — not approved for public release.** End-to-end review of how the Module 0 product would behave deployed for multiple mortgage lenders: lender identifier propagation, UC catalog tenancy, Lakebase row scoping, secret + Genie space tenancy, audit ledger isolation, and frontend lender-context handling.

**Auditor:** Claude (Cowork)
**App:** `https://mip-app-2543889327043640.aws.databricksapps.com`, active deployment `01f15185868d1fa285ea9a3a4c94afd4` (RUNNING, ACTIVE).
**Method:** Mapped every `lender_id`/`lender_name`/`tenant_id`/`MIP_LENDER_NAME` reference across backend, frontend, SQL, and Lakebase. Inspected the `mip.ref.lender_dictionary` override mechanism, the `tenant_disclosures` table, the Genie space YAML, the HMAC action token, and `tools/render_sql.py`. Read `docs/runbook-multi-catalog.md` and `docs/multi-catalog-plan.md` for the documented tenancy story. Counted frontend `Summit Mortgage` literal references and mapped them to their source.

---

## Headline result

The product implements **per-deployment tenancy**, not row-level multi-tenant SaaS. One Databricks workspace = one UC catalog = one Lakebase instance = one Genie space = one lender. This is **the right architecture** for an enterprise Databricks App sold to mortgage lenders — each customer gets full data isolation through deployment boundaries, not application-level RLS.

The team has clearly thought this through. `mip.ref.lender_dictionary` is the **tenant override point**: a customer deploying for their own brand MERGEs their entry with `is_competitor = FALSE`, and the gold transformations correctly identify their loans as "current customer" via the dictionary lookup. `tools/render_sql.py` enables full multi-catalog deploy via `MIP_DEFAULT_CATALOG=customer_catalog`, and the backend Python layer is multi-catalog safe through `qualify()`. `docs/runbook-multi-catalog.md` documents the deploy flow.

**The substantive gap is on the frontend**: five `Summit Mortgage` literal references (one in `AppContext.tsx`, four in route logic) are hardcoded and don't reflect the `MIP_LENDER_NAME` setting. A customer SE deploying for "Acme Mortgage" gets a UI still branded "Summit Mortgage" — the backend correctly resolves their data via the dictionary override, but the frontend brand doesn't follow.

**Finding set: 0 P0, 1 P1, 1 MEDIUM, 3 LOW.**

🟠 **P1 1 — Frontend hardcodes `Summit Mortgage` in five places; doesn't read `MIP_LENDER_NAME` or `/api/config`.**

| File | Line | What it does |
|---|---:|---|
| `frontend/src/components/AppContext.tsx` | 144 | `const lender = 'Summit Mortgage';` — comment says *"tenant label is display-only"* but the value is hardcoded |
| `frontend/src/routes/portfolio-builder.logic.ts` | 47 | `PUBLIC_LENDER_REF_RE = /^(All\|Summit Mortgage\|Competitor ...)/` validates user input against the literal lender name |
| `frontend/src/routes/portfolio-builder.logic.ts` | 82, 84 | Outreach template strings reference `Summit Mortgage` |
| `frontend/src/routes/lead-queue.tsx` | 47 | Same regex as portfolio-builder |

A customer deploying with `MIP_LENDER_NAME=Acme Mortgage` and an `Acme Mtg` row in `mip.ref.lender_dictionary` will see:
1. Topbar branded **Summit Mortgage** (wrong)
2. Lender filter dropdown rejects **Acme Mortgage** as an invalid value (wrong)
3. Outreach drafts say **Summit Mortgage** (wrong)

The backend has the right plumbing: `backend/config/settings.py:63` exposes `mip_lender_name: str = "Summit Mortgage"` as an env-driven setting, `/api/config/options` returns `target_lender_options` from the gold layer, and the `_PUBLIC_LENDER_REF_RE` regex in `backend/schemas/_validators.py` is closer to the right shape. But the frontend doesn't consume any of these — the lender label is read from a constant, and the regexes are duplicated as literals on the frontend side. Fix: surface `lender_name` from `/api/config/options` (or a new `/api/config/lender` endpoint), pipe it through `AppContext`, and derive the public-ref regex from the same response.

🟡 **MEDIUM 1 — Outreach draft templates in `backend/api/outreach.py` hardcode "Summit Mortgage" in 8 places.** Lines 400-439 contain literal strings like *"Summit Mortgage: review your {offer}. Reply YES."* and *"As a Summit Mortgage customer, your current loan profile is ready for review."* A customer deploying for Acme Mortgage gets outreach drafts that say "Summit Mortgage." This is more serious than the frontend display because the drafts are governed content that gets approved by a human and sent to real borrowers. The fix is to interpolate `settings.mip_lender_name`. Spot-fix unblocks any multi-lender deploy.

🟡 **LOW 1 — `tenant_disclosures` table has `tenant_id` but it's stub-shaped.** `lakebase/schema.sql:80` declares `tenant_id TEXT NOT NULL DEFAULT 'summit'` with the literal default `'summit'`. `backend/services/disclosures.py:88` accepts `tenant_id: str = "summit"` parameter with a hardcoded default. No code path actually varies `tenant_id` per request — every call uses the default. This is **harmless** (it just makes future multi-tenant easier) but the column-and-default give a false impression of multi-tenant capability that doesn't exist elsewhere.

🟡 **LOW 2 — Genie space YAML mentions "Summit Mortgage" in two places.** `genie/mortgage_lead_intelligence_space.yml:113` (instructions block: *"For this workspace the tenant is Summit Mortgage"*) and line 426 (sample question: *"Where should Summit Mortgage spend its next 10000 outreach touches this week"*). The first is informational and would need re-wording per customer; the second is a sample question that a customer SE could customize before deploy. `tools/databricks/provision_genie_space.py` reads this YAML, so a customer who runs the provisioner without editing the YAML gets a Genie space that introduces itself as "Summit Mortgage."

🟡 **LOW 3 — Per-deployment tenancy is correct architecturally but not explicitly documented as a tenancy posture.** `docs/runbook-multi-catalog.md` is excellent for the catalog-rename story, but a customer SE asking "is this multi-tenant?" doesn't have a clear answer. Recommend adding a 5-line tenancy section to `docs/se-onboarding.md` that says: *one workspace = one lender; isolation is at the deployment boundary; lender_dictionary is the brand override point; no shared infrastructure across lenders.*

---

## What I verified

### 1. Tenancy identifier inventory

| Identifier | Source | Plumbing | Effective scope |
|---|---|---|---|
| `MIP_LENDER_NAME` env var | `.env.local` (or `MIP_DEMO_LENDER`) | Read by `settings.mip_lender_name` (default `"Summit Mortgage"`) | Backend services + scoring + outreach copy |
| `MIP_DEFAULT_CATALOG` env var | `.env.local` (default `mip`) | Read by `tools/render_sql.py` + `qualify()` | Every UC three-part identifier |
| `mip_lender_name` Pydantic setting | `backend/config/settings.py:63` | Used in `outreach.py` outreach drafts | Backend-only |
| `mip.ref.lender_dictionary` | UC table, MERGE-seeded | Joined into gold transformations (`gold_borrower_360.sql`, `gold_lead_scores.sql`, `gold_evidence_events.sql`) | Per-deployment override point: `is_competitor = FALSE` for the tenant brand |
| `tenant_id` column | `lakebase/schema.sql:80` (only on `tenant_disclosures`) | `disclosures.resolve_tenant_disclosure(tenant_id="summit")` | Stub default — see LOW 1 |
| `target_lender_ref` query param | `/api/leads`, `/api/portfolio/preview` | Validated against `_PUBLIC_LENDER_REF_RE` ("Summit Mortgage \| Competitor [A-Z] \| Competitor Other") | API filter for "show me leads at competitor X" |
| `current_lender_ref` column | gold `borrower_360` row | Resolved from `lender_dictionary` JOIN | Per-borrower brand attribution |

This is a **coherent per-deployment tenancy model**. The single point of override is `mip.ref.lender_dictionary` — a customer adds their `is_competitor = FALSE` row and the entire gold layer correctly classifies their loans as "current customer."

### 2. Lakebase tenancy

13 tables in `mip_app.*`. **One** has a `tenant_id` column:

| Table | Tenant column? | Notes |
|---|---|---|
| `campaigns` | No (uses `owner_email`) | Per-deployment scope |
| `campaign_message_variants` | No | Per-deployment |
| `tenant_disclosures` | `tenant_id TEXT NOT NULL DEFAULT 'summit'` | Stub for future multi-tenant — see LOW 1 |
| `sales_team` | No | Per-deployment |
| `lead_assignments` | No | Per-deployment |
| `call_dispositions` | No | Per-deployment |
| `approvals` | No | Per-deployment |
| `saved_leads` | No | Per-deployment (scoped by `actor_email`) |
| `outreach_drafts` | No | Per-deployment (scoped by `actor_email`) |
| `action_audit` | No | Per-deployment (audit_id is UUID, actor_email is identity) |
| `genie_sessions` / `genie_messages` / `genie_cohorts` / `genie_cohort_members` | No | Per-deployment |
| `agent_sessions` | No | Per-deployment |
| `feedback` | No | Per-deployment |

There is **no row-level tenancy** in Lakebase. Cross-tenant data isolation comes from the deployment boundary: a customer's `mip-app-state` Lakebase instance is provisioned by their own bundle, so another lender's `mip-app-state` is a completely separate Postgres database.

### 3. Unity Catalog tenancy

Gold layer joins `mip.ref.lender_dictionary` to attribute `current_lender_ref`. The dictionary seed declares 11 competitor brands as `is_competitor = TRUE` with display names `Competitor A-J`, plus 4 variants of `SUMMIT MTG` (the tenant) as `is_competitor = FALSE` mapping to `Summit Mortgage`. The seed is documented as the **tenant override point** — a customer's first deploy MERGEs their brand with `is_competitor = FALSE` and the entire gold layer follows.

Multi-catalog support: every backend SQL caller goes through `backend/services/databricks_sql_helpers.qualify('gold', 'borrower_360')` which returns `{catalog}.gold.borrower_360` from `settings.mip_default_catalog`. Verified at 8 call sites across `databricks_repo`, `admin_rules`, `genie_answers`, `pii_redaction`, `state_footprint`, `offers`, `genie`. `tools/render_sql.py` does the equivalent substitution for SQL files at deploy time.

A customer deploying with `MIP_DEFAULT_CATALOG=acme_mortgage` gets `acme_mortgage.gold.*` everywhere, with **zero source-code edits**. This is unusually good.

### 4. Secret + credential scoping

Every secret in `databricks.yml` is **per-deployment**:

| Secret | Source | Per-tenant? |
|---|---|---|
| `MIP_GENIE_ACTION_SECRET` (HMAC) | `.env.local` of the deploying workspace | Yes — each deployment has its own |
| `MIP_COTALITY_ID_MASK_SECRET` | Same | Yes |
| `otel_headers` (OTel collector creds) | Databricks Secret scope `${var.otel_headers_secret_scope}` declared per-workspace | Yes |
| `FRED_API_KEY` (optional) | `.env.local` | Yes (and not currently used — public unauthenticated endpoint) |
| Lakebase password | Minted at runtime via `WorkspaceClient().database.generate_database_credential(...)` for the deploying identity | Yes |
| Databricks workspace OAuth | Workspace identity issued by Apps runtime | Yes |

No shared secrets across deployments. The HMAC key is per-workspace, so a Genie action token from Acme's deployment is structurally invalid when presented to Summit's deployment (signature verification fails on the wrong secret).

### 5. Genie space tenancy

`databricks.yml:69` pins a single `genie_space_id` (`01f13d4968af1b249dc388fd5b18b195`) for the dev target. A customer deploying for their own workspace would:
1. Run `tools/databricks/provision_genie_space.py` (deploy.sh step 0a) to mint a new Genie space in their workspace.
2. Bundle deploy reads their new space ID via `BUNDLE_VAR_genie_space_id` from `.env.local`.
3. The app's `genie_space` resource binding wires their backend to the new space.

The provisioner reads `genie/mortgage_lead_intelligence_space.yml`, which contains the instructions, trusted asset list, and sample questions. The instructions block (line 113) hard-references "Summit Mortgage" — that's LOW 2.

The 14-asset trusted allowlist (`gold.lead_population`, `gold.borrower_360`, etc.) is constant across tenants — every customer points at their *own* catalog's `gold.lead_population`. The `qualify()` pattern ensures a customer's Genie space queries their data only.

### 6. Audit-ledger + actor identity tenancy

`mip_app.action_audit` is scoped by deployment (single Lakebase instance per customer). `actor_email` (the `X-Forwarded-Email` header injected by Databricks Apps) identifies *which user within the customer's workspace* took the action. There's no cross-workspace identity trust — a user's `X-Forwarded-Email` is set by the customer's own Databricks workspace, and that workspace's app would refuse to accept a forged header (the auth gate is the Apps runtime, not the application).

The `request_id` + `correlation_id` columns are per-request scoped, not per-tenant. They're fine for audit because they nest under the per-deployment Lakebase boundary.

### 7. First-party feed data

`mip.first_party.*` tables (loan applications, servicing portfolio, CRM campaign membership) are populated by `sql/transformations/demo_first_party_feeds.sql` and gated by `MIP_ENABLE_DEMO_FIRST_PARTY_FEEDS`. The gating logic in `scripts/deploy.sh:272-278` **refuses to enable the Summit demo data in any non-dev target unless `MIP_ALLOW_DEMO_FIRST_PARTY_IN_PROD=1` is also set** — a deliberate guardrail so a customer SE doesn't accidentally surface Summit's synthetic data in their own production deploy.

This is the right shape: dev deploys get the demo data automatically, prod deploys are fail-closed unless someone explicitly opts in.

### 8. Frontend lender propagation — the gap

The frontend has **five hardcoded `Summit Mortgage` references**:

| File | Line | Code |
|---|---:|---|
| `AppContext.tsx` | 144 | `const lender = 'Summit Mortgage';` |
| `portfolio-builder.logic.ts` | 47 | `PUBLIC_LENDER_REF_RE = /^(All|Summit Mortgage|Competitor ...)$/` |
| `portfolio-builder.logic.ts` | 82 | `subjectA: 'Summit Mortgage review for your current loan options'` |
| `portfolio-builder.logic.ts` | 84 | `bodyA: 'Review current mortgage fit with Summit Mortgage ...'` |
| `lead-queue.tsx` | 47 | Same regex |

The comment at `AppContext.tsx:141-143` explicitly notes *"the tenant label is display-only; lender predicates are resolved by backend configuration and the Unity Catalog gold views"* — but the display label itself is still a constant. A customer with `MIP_LENDER_NAME=Acme Mortgage` in `.env.local` gets:

- Topbar shows "Summit Mortgage"
- Lender filter regex rejects "Acme Mortgage"
- Outreach UI templates reference "Summit Mortgage"

This is the P1 finding. The fix is small: add a `lender_name` field to `/api/config/options` (or a new `/api/config/lender` endpoint) that returns `settings.mip_lender_name`, plumb it through `AppContext`, and derive `PUBLIC_LENDER_REF_RE` from the `/api/config/options` lender list rather than a hardcoded regex.

### 9. Multi-tenant documentation posture

`docs/runbook-multi-catalog.md` (excellent — 40+ lines, exhaustive coverage of the deploy flow for non-default catalogs) and `docs/multi-catalog-plan.md` (design doc) cover the catalog-rename story. But neither explicitly says "the tenancy model is per-deployment, not row-level multi-tenant." A customer SE reading these without architectural context might assume more isolation than exists, or wonder whether row-level tenancy is on the roadmap. Recommend a single paragraph in `docs/se-onboarding.md` or a new `docs/tenancy-model.md`.

---

## Architecture qualities worth preserving

- **`mip.ref.lender_dictionary` as the single tenant override point.** A customer adds one row, the entire gold layer follows. Cleaner than threading a tenant_id parameter through every query.
- **`tools/render_sql.py` + `qualify()` give true multi-catalog support.** A customer deploying with `MIP_DEFAULT_CATALOG=acme_mortgage` gets correct CTAS targets and runtime queries without source edits.
- **Demo first-party feeds are fail-closed in prod.** `scripts/deploy.sh:272-278` refuses to populate Summit synthetic data in non-dev targets unless explicitly opted in. Right shape for a customer-facing product.
- **Per-deployment HMAC secret for Genie action tokens.** A token issued by Summit's deployment is structurally invalid against Acme's deployment.
- **No row-level RLS to maintain.** One deployment = one Lakebase = one UC catalog = one Genie space. Each customer has full data isolation through workspace boundaries. This is what enterprise Databricks customers actually want for compliance.

---

## Remediation

| ID | Severity | Action |
|---|---|---|
| P1 1 | P1 | **Frontend reads lender from backend.** Add `lender_name` to `/api/config/options` response (or create `/api/config/lender` returning `{name, public_ref_regex, competitor_labels}`). Pipe through `AppContext.lender`. Replace the four hardcoded `Summit Mortgage` references in routes + the regex with values derived from the API response. Half-day fix that unblocks per-customer branding. |
| MEDIUM 1 | Med | **Outreach drafts use `settings.mip_lender_name`.** Replace the 8 hardcoded `"Summit Mortgage"` strings in `backend/api/outreach.py:400-439` with f-string interpolation of `settings.mip_lender_name`. One commit. |
| LOW 1 | Low | Either generalize `tenant_disclosures.tenant_id` to drive real per-tenant disclosure resolution (and add a tenant_id parameter to `resolve_tenant_disclosure`), OR remove the column and parameter to clarify that the table is per-deployment-scoped. Currently it's neither. |
| LOW 2 | Low | Make `genie/mortgage_lead_intelligence_space.yml`'s instruction block parameterize the tenant name (`{tenant_name}` placeholder, substituted by `tools/databricks/provision_genie_space.py` from `MIP_LENDER_NAME`). Update the sample question at line 426 to use the same substitution. |
| LOW 3 | Low | Add a 5-line tenancy section to `docs/se-onboarding.md` (or a new `docs/tenancy-model.md`) that documents: per-deployment tenancy = one workspace = one lender; deployment-boundary isolation; `lender_dictionary` is the override point; future multi-tenant SaaS would require row-level RLS, which is out of scope for Module 0. |

---

## Summary verdict

- **8 tenancy dimensions probed**: identifier inventory, Lakebase table tenancy, UC catalog isolation, secret scoping, Genie space tenancy, audit ledger isolation, first-party feed gating, frontend lender propagation.
- **0 P0**, **1 P1** (frontend doesn't read `MIP_LENDER_NAME` — customer SE for any non-Summit lender gets a UI mis-branded "Summit Mortgage"), **1 MEDIUM** (outreach drafts hardcode the lender name in 8 strings), **3 LOW** (stub `tenant_id` column, Genie YAML literal, missing tenancy doc).
- **Per-deployment tenancy is the right model** for a Module 0 Databricks App sold to mortgage lenders. The team has implemented it well at the data and infrastructure layers — UC catalog rename works without source edits, Lakebase instance is per-workspace, HMAC secrets are per-deployment, Genie space is per-workspace, demo data is fail-closed in prod.
- **The frontend brand-rendering gap is the single substantive issue.** Backend correctly resolves a customer's data via `mip.ref.lender_dictionary`, but the React app still says "Summit Mortgage" in the topbar and the outreach template strings because the values aren't piped through from the backend. Half-day fix.

The product is **architecturally ready for multi-lender deployment** once the P1 and MEDIUM are closed. The infrastructure layer is excellent; the UI just needs to follow the same env-driven brand identity.

---

## Sources

- `backend/config/settings.py:63` — `mip_lender_name` Pydantic setting
- `backend/schemas/_validators.py:7` — `_PUBLIC_LENDER_REF_RE`
- `backend/api/config.py:24-39` — `_target_lender_options` (live lender enumeration from `gold.borrower_360`)
- `backend/api/outreach.py:400-439` — outreach draft templates with hardcoded "Summit Mortgage"
- `backend/services/disclosures.py:88` — `resolve_tenant_disclosure(tenant_id="summit", ...)`
- `lakebase/schema.sql:79-90` — `tenant_disclosures` table with `tenant_id TEXT DEFAULT 'summit'`
- `lakebase/schema.sql` (full) — 13 tables, only one with `tenant_id`
- `sql/ref/lender_dictionary_seed.sql` — tenant override point
- `sql/transformations/gold_borrower_360.sql:134-164` — `lender_ref` CTE
- `tools/render_sql.py` — multi-catalog SQL renderer
- `tools/databricks/provision_genie_space.py` — Genie space provisioner (reads `genie/mortgage_lead_intelligence_space.yml`)
- `genie/mortgage_lead_intelligence_space.yml:113, 426` — "Summit Mortgage" literal in instructions + sample
- `frontend/src/components/AppContext.tsx:141-144` — hardcoded `lender` constant + comment
- `frontend/src/routes/portfolio-builder.logic.ts:47, 82, 84` — hardcoded regex + outreach templates
- `frontend/src/routes/lead-queue.tsx:47` — duplicate of the regex
- `docs/runbook-multi-catalog.md` — multi-catalog deploy flow (excellent)
- `docs/multi-catalog-plan.md` — multi-catalog design doc (excellent)
- Live deployment: `01f15185868d1fa285ea9a3a4c94afd4`
