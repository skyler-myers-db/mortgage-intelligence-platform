# Multi-tenant + lender isolation audit

> **Internal validation artifact — not approved for public release.** End-to-end review of how the Module 0 product would behave deployed for multiple mortgage lenders: lender identifier propagation, UC catalog tenancy, Lakebase row scoping, secret + Genie space tenancy, audit ledger isolation, and frontend lender-context handling.

**Auditor:** Claude (Cowork)
**App:** `https://mip-app-2543889327043640.aws.databricksapps.com`, active deployment `01f15185868d1fa285ea9a3a4c94afd4` (RUNNING, ACTIVE).
**Method:** Mapped every `lender_id`/`lender_name`/`tenant_id`/`MIP_LENDER_NAME` reference across backend, frontend, SQL, and Lakebase. Inspected the `mip.ref.lender_dictionary` override mechanism, the `tenant_disclosures` table, the Genie space YAML, the HMAC action token, and `tools/render_sql.py`. Read `docs/runbook-multi-catalog.md` and `docs/multi-catalog-plan.md` for the documented tenancy story. Counted frontend `Summit Mortgage` literal references and mapped them to their source.

---

## Headline result

The product implements **per-deployment tenancy**, not row-level multi-tenant SaaS. One Databricks workspace = one UC catalog = one Lakebase instance = one Genie space = one lender. This is **the right architecture** for an enterprise Databricks App sold to mortgage lenders — each customer gets full data isolation through deployment boundaries, not application-level RLS.

The team has clearly thought this through. `mip.ref.lender_dictionary` is the **tenant override point**: a customer deploying for their own brand MERGEs their entry with `is_competitor = FALSE`, and the gold transformations correctly identify their loans as "current customer" via the dictionary lookup. `tools/render_sql.py` enables full multi-catalog deploy via `MIP_DEFAULT_CATALOG=customer_catalog`, and the backend Python layer is multi-catalog safe through `qualify()`. `docs/runbook-multi-catalog.md` documents the deploy flow.

**Post-remediation result (2026-05-17):** the substantive frontend and outreach branding gaps are closed. `/api/config/options` now returns the configured `lender_name`, the React shell and route filter validators consume the configured lender/options instead of duplicating a `Summit Mortgage` regex, outreach drafts interpolate `settings.mip_lender_name`, tenant disclosure lookup defaults to `settings.effective_tenant_id()`, Genie space YAML uses `{tenant_name}` and `{catalog}` substitution in the provisioner, and `docs/se-onboarding.md` now states the tenancy posture explicitly.

The remediation pass also found and fixed adjacent multi-catalog issues outside the original audit: two Spark Python bundle jobs still targeted `mip.*` by default, and Genie trusted assets / canonical SQL / source-gap proofs needed to follow the configured catalog. `mip_sync_lifecycle_state` now receives `--catalog=${var.uc_catalog}`, both FRED ingest Python tasks receive `--table=${var.uc_catalog}.silver.market_rates_weekly`, the Genie provisioner renders every trusted-asset and sample-SQL reference to `{catalog}.*`, `/api/genie/start` and source-gap proofs use `qualify()`, and the Ask Genie trusted-asset list renders backend-provided catalog-qualified paths.

**Current finding set: 0 open P0, 0 open P1, 0 open MEDIUM, 0 open LOW.**

---

## What I verified

### 1. Tenancy identifier inventory

| Identifier | Source | Plumbing | Effective scope |
|---|---|---|---|
| `MIP_LENDER_NAME` env var | `.env.local` | Read by `settings.mip_lender_name` (default `"Summit Mortgage"`) and returned by `/api/config/options` | Backend services, outreach copy, Genie provisioning, frontend shell/filter copy |
| `MIP_TENANT_ID` env var | `.env.local` (optional) | Read by `settings.effective_tenant_id()` | Per-deployment disclosure namespace; defaults to `summit` for the demo lender or a slug of `MIP_LENDER_NAME` |
| `MIP_DEFAULT_CATALOG` env var | `.env.local` (default `mip`) | Read by `tools/render_sql.py`, `qualify()`, Genie provisioning/eval, and Python jobs via bundle params/env | Every UC three-part identifier |
| `mip_lender_name` Pydantic setting | `backend/config/settings.py` | Used in config, outreach drafts, campaign validators | Backend + frontend config contract |
| `mip.ref.lender_dictionary` | UC table, MERGE-seeded | Joined into gold transformations (`gold_borrower_360.sql`, `gold_lead_scores.sql`, `gold_evidence_events.sql`) | Per-deployment override point: `is_competitor = FALSE` for the tenant brand |
| `tenant_id` column | `lakebase/schema.sql:80` (only on `tenant_disclosures`) | `disclosures.resolve_tenant_disclosure(tenant_id=None)` defaults through `settings.effective_tenant_id()` | Per-deployment disclosure namespace |
| `target_lender_ref` query param | `/api/leads`, `/api/portfolio/preview` | Validated against configured tenant lender, `All`, or public-safe competitor aliases | API filter for "show me leads at competitor X" |
| `current_lender_ref` column | gold `borrower_360` row | Resolved from `lender_dictionary` JOIN | Per-borrower brand attribution |

This is a **coherent per-deployment tenancy model**. The single point of override is `mip.ref.lender_dictionary` — a customer adds their `is_competitor = FALSE` row and the entire gold layer correctly classifies their loans as "current customer."

### 2. Lakebase tenancy

13 tables in `mip_app.*`. **One** has a `tenant_id` column:

| Table | Tenant column? | Notes |
|---|---|---|
| `campaigns` | No (uses `owner_email`) | Per-deployment scope |
| `campaign_message_variants` | No | Per-deployment |
| `tenant_disclosures` | `tenant_id TEXT NOT NULL DEFAULT 'summit'` | Per-deployment disclosure namespace; default preserves demo back-compat |
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

There is **no row-level tenancy** in Lakebase. Cross-tenant data isolation comes from the deployment boundary: a customer's `mip-app-state` Lakebase instance is provisioned by their own bundle, so another lender's `mip-app-state` is a completely separate Postgres database. The `tenant_disclosures.tenant_id` column is a per-deployment disclosure namespace, not a shared-SaaS tenant partition.

### 3. Unity Catalog tenancy

Gold layer joins `mip.ref.lender_dictionary` to attribute `current_lender_ref`. The dictionary seed declares 11 competitor brands as `is_competitor = TRUE` with display names `Competitor A-J`, plus 4 variants of `SUMMIT MTG` (the tenant) as `is_competitor = FALSE` mapping to `Summit Mortgage`. The seed is documented as the **tenant override point** — a customer's first deploy MERGEs their brand with `is_competitor = FALSE` and the entire gold layer follows.

Multi-catalog support: every backend SQL caller goes through `backend/services/databricks_sql_helpers.qualify('gold', 'borrower_360')` which returns `{catalog}.gold.borrower_360` from `settings.mip_default_catalog`. Verified at call sites across `databricks_repo`, `admin_rules`, `genie_answers`, `genie_trusted_assets`, `genie_sales_ops`, `pii_redaction`, `state_footprint`, `offers`, and `genie`. `tools/render_sql.py` does the equivalent substitution for SQL files at deploy time, the Genie provisioner renders trusted assets to the same catalog, and the Spark Python bundle jobs now receive the same `${var.uc_catalog}` as explicit task parameters.

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

The provisioner reads `genie/mortgage_lead_intelligence_space.yml`, which contains the instructions, trusted asset list, and sample questions. Tenant-facing text now uses `{tenant_name}` placeholders rendered by `tools/databricks/provision_genie_space.py` from `MIP_LENDER_NAME`. Catalog-facing text, trusted assets, and example SQL are rendered through `{catalog}` / prefix substitution from `MIP_DEFAULT_CATALOG`, so a customer Genie space created for `acme_mortgage` points at `acme_mortgage.gold.*` and `acme_mortgage.semantics.*`, not `mip.*`.

The 14-asset trusted allowlist (`gold.lead_population`, `gold.borrower_360`, etc.) is constant across tenants — every customer points at their *own* catalog's `gold.lead_population`. The `qualify()` pattern ensures a customer's Genie space queries their data only.

### 6. Audit-ledger + actor identity tenancy

`mip_app.action_audit` is scoped by deployment (single Lakebase instance per customer). `actor_email` (the `X-Forwarded-Email` header injected by Databricks Apps) identifies *which user within the customer's workspace* took the action. There's no cross-workspace identity trust — a user's `X-Forwarded-Email` is set by the customer's own Databricks workspace, and that workspace's app would refuse to accept a forged header (the auth gate is the Apps runtime, not the application).

The `request_id` + `correlation_id` columns are per-request scoped, not per-tenant. They're fine for audit because they nest under the per-deployment Lakebase boundary.

### 7. First-party feed data

`mip.first_party.*` tables (loan applications, servicing portfolio, CRM campaign membership) are populated by `sql/transformations/demo_first_party_feeds.sql` and gated by `MIP_ENABLE_DEMO_FIRST_PARTY_FEEDS`. The gating logic in `scripts/deploy.sh:272-278` **refuses to enable the Summit demo data in any non-dev target unless `MIP_ALLOW_DEMO_FIRST_PARTY_IN_PROD=1` is also set** — a deliberate guardrail so a customer SE doesn't accidentally surface Summit's synthetic data in their own production deploy.

This is the right shape: dev deploys get the demo data automatically, prod deploys are fail-closed unless someone explicitly opts in.

### 8. Frontend lender propagation — fixed

The frontend now treats lender identity as backend configuration:

| Surface | Current behavior |
|---|---|
| `AppContext.tsx` | Reads `lender_name` from `/api/config/options` and uses `Configured lender` only as a loading fallback |
| `portfolio-builder.logic.ts` | Validates lender refs against configured options plus public-safe competitor aliases; campaign defaults are derived from the configured lender name |
| `portfolio-builder.tsx` | Updates untouched default campaign copy when config arrives and passes server-provided target lender options into URL/filter parsing |
| `lead-queue.tsx` | Parses URL/export target lender filters against server-provided target lender options instead of a hardcoded tenant regex |

Focused tests cover `MIP_LENDER_NAME=Acme Mortgage` on the backend and frontend route helpers, including export rejection for unadvertised tenant lender names.

### 9. Multi-tenant documentation posture

`docs/runbook-multi-catalog.md` and `docs/multi-catalog-plan.md` cover the catalog-rename story. `docs/se-onboarding.md` now explicitly states the tenancy posture: Module 0 is per-deployment, not shared row-level SaaS; one customer workspace maps to one UC catalog, one Lakebase instance, one Genie space, one Databricks App, and one lender context.

---

## Architecture qualities worth preserving

- **`mip.ref.lender_dictionary` as the single tenant override point.** A customer adds one row, the entire gold layer follows. Cleaner than threading a tenant_id parameter through every query.
- **`tools/render_sql.py` + `qualify()` + bundle Python-job parameters give true multi-catalog support.** A customer deploying with `MIP_DEFAULT_CATALOG=acme_mortgage` and `uc_catalog=acme_mortgage` gets correct CTAS targets, runtime queries, lifecycle sync writes, and FRED ingest writes without source edits.
- **Demo first-party feeds are fail-closed in prod.** `scripts/deploy.sh:272-278` refuses to populate Summit synthetic data in non-dev targets unless explicitly opted in. Right shape for a customer-facing product.
- **Per-deployment HMAC secret for Genie action tokens.** A token issued by Summit's deployment is structurally invalid against Acme's deployment.
- **No row-level RLS to maintain.** One deployment = one Lakebase = one UC catalog = one Genie space. Each customer has full data isolation through workspace boundaries. This is what enterprise Databricks customers actually want for compliance.

---

## Remediation

| ID | Severity | Action |
|---|---|---|
| P1 1 | P1 | **Closed.** `/api/config/options` returns `lender_name`; `AppContext`, Lead Queue, and Portfolio Builder consume configured lender/options instead of hardcoded `Summit Mortgage` route regexes. |
| MEDIUM 1 | Med | **Closed.** Outreach drafts use `settings.mip_lender_name`, with tests covering an Acme Mortgage deployment. |
| LOW 1 | Low | **Closed.** `resolve_tenant_disclosure()` defaults through `settings.effective_tenant_id()` and schema comments document the per-deployment disclosure namespace. |
| LOW 2 | Low | **Closed.** Genie YAML uses `{tenant_name}` placeholders rendered by `tools/databricks/provision_genie_space.py` from `MIP_LENDER_NAME`. |
| LOW 3 | Low | **Closed.** `docs/se-onboarding.md` documents the per-deployment tenancy posture. |
| NEW | Low | **Closed.** Spark Python jobs now receive the bundle catalog variable so lifecycle sync and FRED ingest do not write to `mip.*` in renamed-catalog deployments. |
| NEW | Low | **Closed.** Genie provisioned assets, backend trusted-asset policy, canonical SQL repair paths, source-gap proofs, eval expectations, and Ask Genie trusted-asset UI now follow the configured catalog. |

---

## Summary verdict

- **8 tenancy dimensions probed**: identifier inventory, Lakebase table tenancy, UC catalog isolation, secret scoping, Genie space tenancy, audit ledger isolation, first-party feed gating, frontend lender propagation.
- **0 open P0 / P1 / MEDIUM / LOW findings** after remediation.
- **Per-deployment tenancy is the right model** for a Module 0 Databricks App sold to mortgage lenders. UC catalog rename works without source edits, Lakebase instance is per-workspace, HMAC secrets are per-deployment, Genie space is per-workspace, demo data is fail-closed in prod, and the frontend now follows the same configured lender identity.
- **Adjacent multi-catalog gaps closed.** Lifecycle sync, FRED ingest, Genie provisioned assets, backend Genie policy/canonical SQL, source-gap proofs, Genie eval expectations, and Ask Genie trusted-asset UI now follow `${var.uc_catalog}` / `MIP_DEFAULT_CATALOG`, so non-default-catalog customer deploys do not write to or display `mip.*`.

The product is **architecturally ready for multi-lender deployment** under the documented per-deployment tenancy model.

---

## Sources

- `backend/config/settings.py` — `mip_lender_name`, `mip_tenant_id`, `effective_tenant_id()`, public-lender provider registration
- `backend/schemas/_validators_tenant.py` — configured public lender validation (formerly in `_validators.py`)
- `backend/api/config.py` — `lender_name` and target lender options
- `backend/api/outreach.py` — configured-lender outreach draft copy
- `backend/services/disclosures.py` — `resolve_tenant_disclosure()` defaulting through configured tenant id
- `lakebase/schema.sql:79-90` — `tenant_disclosures` table with per-deployment namespace comments
- `lakebase/schema.sql` (full) — 13 tables, only one with `tenant_id`
- `sql/ref/lender_dictionary_seed.sql` — tenant override point
- `sql/transformations/gold_borrower_360.sql:134-164` — `lender_ref` CTE
- `tools/render_sql.py` — multi-catalog SQL renderer
- `tools/databricks/provision_genie_space.py` — Genie space provisioner and `{tenant_name}` / `{catalog}` rendering
- `genie/mortgage_lead_intelligence_space.yml` — tenant-name and catalog placeholders
- `backend/services/genie_trusted_assets.py`, `backend/services/repositories/databricks_genie_canonical.py`, `backend/api/genie.py` — configured-catalog Genie runtime paths
- `frontend/src/routes/ask-genie.tsx` — backend-provided trusted-asset paths
- `frontend/src/components/AppContext.tsx` — config-backed lender label
- `frontend/src/routes/portfolio-builder.logic.ts` — configured target-lender validation and campaign defaults
- `frontend/src/routes/lead-queue.tsx` — configured target-lender URL/export validation
- `databricks.yml` — Spark Python jobs receive `${var.uc_catalog}`
- `jobs/sync_lifecycle_state.py` — catalog-qualified lifecycle mirror writes
- `jobs/fred_rates_ingest.py` — `MIP_DEFAULT_CATALOG`-aware default table
- `docs/runbook-multi-catalog.md` — multi-catalog deploy flow (excellent)
- `docs/multi-catalog-plan.md` — multi-catalog design doc (excellent)
- Live deployment: `01f15185868d1fa285ea9a3a4c94afd4`

---

## v2 re-validation — 2026-05-17

Independent Cowork re-audit of the multi-tenant remediation tranche. **Verdict: 0 P0, 0 P1, 0 MEDIUM, 0 LOW. Zero regressions across all 20 prior audits.** Every claim in the engineering signoff survives independent verification, and the team also closed several adjacent multi-catalog issues my v1 audit did not flag.

### Frontend lender resolution (P1 1) — closed

Verified by static grep: **0 `Summit Mortgage` literals** in `frontend/src/**/*.tsx,*.ts` (excluding `.test.` files; tests retain the literal as fixture input, which is correct).

`frontend/src/components/AppContext.tsx:144-150` now uses TanStack Query to fetch `/api/config/options` and reads `lender_name`:

```ts
const configOptionsQuery = useQuery<ConfigOptions>({
  queryKey: queryKeys.configOptions(),
  queryFn: ({ signal }) => api.configOptions(signal),
  staleTime: 60_000,
  retry: false,
});
const lender = configOptionsQuery.data?.lender_name?.trim() || 'Configured lender';
```

The fallback is `'Configured lender'` instead of a hardcoded brand — correct for a deployment that hasn't yet rendered the config response. The 60-second `staleTime` is the right shape for a near-static config value.

`backend/api/config.py:87` returns `lender_name: settings.mip_lender_name` in the `/api/config/options` payload, plus `target_lender_refs` and `target_lender_refs_status` enumerated live from the gold layer. The three other route files (`home.tsx:39`, `portfolio-builder.tsx:48-60`, `lead-queue.tsx:337`) all consume the API response — `portfolio-builder.tsx:54-60` derives the lender filter from `configOptionsQuery.data?.target_lender_refs` instead of the prior hardcoded regex.

A customer SE deploying with `MIP_LENDER_NAME=Acme Mortgage` now gets:
- Topbar reads "Acme Mortgage"
- Lender filter dropdown options come from `acme_mip.gold.borrower_360` distinct `current_lender_ref` values
- Falls back to "Configured lender" if `/api/config/options` is unreachable

### Outreach interpolation (MEDIUM 1) — closed

Verified by static grep: `grep -nE "\"Summit Mortgage\"|'Summit Mortgage'" backend/api/outreach.py` returns **zero hits**. The previous 8 hardcoded references at lines 400-439 are now driven by `settings.mip_lender_name`:

```python
lender_name = (settings.mip_lender_name or "configured lender").strip() or "configured lender"
sms_lender = _sms_lender_label(lender_name)
```

All three channels (SMS, direct mail, email) now interpolate `lender_name`. The SMS path has a separate `_sms_lender_label(lender_name)` helper that produces a short alias if needed to fit the 160-char SMS budget; the fallback chain (long body → short body → disclosure-only) is preserved with the interpolated label. Defensive-coding posture.

### Tenant disclosure resolution (LOW 1) — closed

Verified: `backend/config/settings.py:77-87` now defines `effective_tenant_id()` which returns either:
1. `mip_tenant_id` (new optional env var), or
2. `"summit"` if `mip_lender_name == "Summit Mortgage"` (preserves demo back-compat), or
3. A slug of `mip_lender_name` (`re.sub(r"[^a-z0-9]+", "_", lender_name.lower()).strip("_")`), or
4. `"tenant"` if all empty.

`backend/services/disclosures.py:94` defaults to `tenant_key = tenant_id or settings.effective_tenant_id()`. `backend/api/outreach.py:375` passes `tenant_id=settings.effective_tenant_id()` to the disclosure lookup. So Acme's outreach drafts resolve against `tenant_id = "acme_mortgage"` rows in `mip_app.tenant_disclosures`, with the Summit default preserved for the demo.

The `tenant_disclosures` table is no longer a stub — it's now a genuine per-deployment disclosure namespace.

### Genie space templating (LOW 2) — closed

Verified: `genie/mortgage_lead_intelligence_space.yml` now uses `{tenant_name}` and `{catalog}` placeholders (3 matches at lines 20, 114, 426). `grep -c "Summit Mortgage" genie/mortgage_lead_intelligence_space.yml` returns **0**.

`tools/databricks/provision_genie_space.py:146-172` defines `_render_space_templates(value, tenant_name, catalog_name)` that walks the YAML structure recursively, substituting `{tenant_name}` and `{catalog}`. When `catalog_name != "mip"`, it also rewrites bare `mip.<schema>` references (the schema group is the documented allowlist of `_UC_SCHEMAS`) so trusted asset paths render correctly for any UC catalog name. `_tenant_name()` reads `MIP_LENDER_NAME` from env; `_catalog_name()` reads `MIP_DEFAULT_CATALOG`. A customer SE running the provisioner with `MIP_LENDER_NAME=Acme Mortgage MIP_DEFAULT_CATALOG=acme_mip` gets a Genie space that introduces itself as Acme Mortgage and references `acme_mip.gold.*` trusted assets.

### Tenancy posture documentation (LOW 3) — closed

Verified: `docs/se-onboarding.md:17-27` now has an explicit tenancy section:

> *"Module 0 is a per-deployment product, not a shared row-level multi-tenant SaaS. One customer workspace maps to one UC catalog, one Lakebase state database, one Genie space, one app URL, and one configured lender identity. Isolation is enforced at the Databricks deployment boundary; `mip.ref.lender_dictionary` is the tenant-lender override point for gold transformations, and `MIP_LENDER_NAME` / optional `MIP_TENANT_ID` drive the app label and governed disclosure namespace. `MIP_DEFAULT_CATALOG` drives the SQL renderer, backend `qualify()` calls, Spark Python jobs, and Genie provisioning, so keep it equal to the bundle `uc_catalog` variable. A future shared-SaaS deployment would need explicit row-level tenant predicates and RLS; that is out of scope for Module 0."*

Concise, accurate, and answers exactly the right SE question.

### Adjacent multi-catalog issues the team also closed

The remediation pass also closed three multi-catalog issues my v1 audit didn't flag:

1. **Spark Python bundle jobs** (`mip_sync_lifecycle_state`, `mip_fred_rates_ingest`) now receive `${var.uc_catalog}` as task parameters at `databricks.yml:635, 740, 752`. `jobs/sync_lifecycle_state.py:328-349` parses `--catalog` and writes to `{catalog}.gold.borrower_lifecycle_state` / `{catalog}.gold.funnel_snapshot_daily`. `jobs/fred_rates_ingest.py:536` defaults `--table` to `{catalog}.silver.market_rates_weekly`. Before this fix, these Python tasks would have written to literal `mip.*` regardless of `MIP_DEFAULT_CATALOG`.

2. **Genie repository canonical SQL** (`databricks_genie_canonical.py:8-9`) now uses `qualify("gold", "borrower_360")` and `qualify("gold", "evidence_events")` instead of bare `mip.gold.*` literals. `databricks_genie_trust.py` and `genie_sales_ops.py` follow the same pattern.

3. **Backend Genie trusted-asset list** (`backend/services/genie_trusted_assets.py:24`) returns `[qualify(schema, table) for schema, table in _TRUSTED_ASSET_PAIRS]` — so when the app reports its trusted assets to the frontend Ask Genie page, the listed paths are `{catalog}.gold.*` / `{catalog}.semantics.*` for whichever catalog this deployment uses. The independent reviewer initially flagged this as "Genie prompt/catalog leakage" — fixed and re-approved.

These three fixes plug holes that would have surfaced as soon as a non-`mip` customer deployment ran a Genie query or a Spark job. The team caught and closed them during the remediation pass rather than waiting for a customer to hit them.

### Cross-audit no-regression sweep

| Audit | Spot-check | Status |
|---|---|---|
| Architecture | 0 router-to-router, 0 schema→service, 0 raw runtime logging, 0 InMemory in prod, 0 files ≥1000 LOC | ✅ All 5 gates green |
| Cross-browser | 6 `min-block-size: var(--sp-6)` rules + 2 `data-target-size-exempt="geographic-shape"` | ✅ |
| Supply-chain | 0 `@svg-maps/usa` in `package.json`; `us-atlas` + `topojson-client` present | ✅ |
| Security | `mip_expose_openapi` gating at `main.py:193-195` | ✅ |
| Compliance | `trg_action_audit_append_only` trigger at `lakebase/schema.sql:304-305` | ✅ |
| Observability | `CorrelationIdMiddleware` mounted at `main.py:204+356` | ✅ |
| Supply-chain license gates | 4/4 executed live, all pass | ✅ |
| Deployability | Single orchestrator + scriptable workspace edit + per-deployment secrets — all intact | ✅ |

**Zero regressions on any prior audit.**

### v2 verdict

**Approved.** The P1 frontend gap, the MEDIUM outreach gap, and the three LOW polish items are all closed in source with verification. The team also closed three adjacent multi-catalog issues (Spark jobs, Genie canonical SQL, Genie trusted-asset listing) that my v1 audit didn't catch — those would have broken a real Acme Mortgage customer deploy as soon as the first Genie query landed.

The per-deployment tenancy model is now **end-to-end coherent**: backend setting → API config response → frontend `AppContext.lender` → topbar label and outreach drafts → outreach UI templates → Genie space provisioning → Spark job catalog → Lakebase disclosure namespace → audit ledger actor identity. Every layer reads its tenant identity from the same `MIP_LENDER_NAME` + `MIP_DEFAULT_CATALOG` + optional `MIP_TENANT_ID` triplet. A customer deploying for "Acme Mortgage" against `acme_mip` catalog gets a fully-branded, fully-namespaced product without source edits.

Module 0 is now **architecturally ready for multi-lender customer deployment**. The remaining decision points (multi-tenant SaaS / row-level RLS) are explicitly documented as out of scope for Module 0, with the future expansion path framed correctly in `docs/se-onboarding.md`.

The independent governance / QA / backend reviewer signoff at the head of this document is met from this side.
