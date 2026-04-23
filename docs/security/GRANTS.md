# Unity Catalog grants for the MIP app service principal

**Audience.** The Entrada/Databricks SE (or customer workspace admin) who
runs `databricks bundle deploy -t dev|prod` against a fresh customer
workspace. This file is the runbook — every SQL block below is
copy-paste-able and every click path is linear.

**Precondition.** The bundle has been deployed once (`databricks bundle
deploy -t dev`) so the `mip-app` resource, the SQL warehouse, the
Lakebase instance, and the UC catalog `mip` all exist. The grants below
bind the app's workspace identity to the UC objects it already owns
logically but cannot yet read.

**Identity.** Databricks Apps runs as the workspace-bound service
principal named after the app (`mip-app`). Every `GRANT ... TO `
`mip-app`` below targets that SCIM principal name. If your workspace
renames the SP (some customers prefix with tenant code), substitute the
real SP name in every block.

**Companion docs.** [`docs/se-onboarding.md`](../se-onboarding.md) is the
end-to-end walkthrough; this file is the "grants reference" it links
to. [`docs/runbook.md`](../runbook.md) covers operator recovery after a
live incident — not first-deploy setup.

**Who runs these statements.** A metastore admin (the only principal
that can `GRANT USE CATALOG` on a newly-created catalog). The SE's own
workspace login is usually insufficient — confirm `current_user()` is
metastore-admin before running, or pair-deploy with a customer admin.

---

## 1. Catalog `mip`

```sql
GRANT USE CATALOG ON CATALOG mip TO `mip-app`;
```

**What breaks if missing.** Entire app fails to start. Every SQL query
the FastAPI backend issues (portfolio, leads, borrower 360, segments,
audit sync) is prefixed with `mip.` and the warehouse returns
`PERMISSION_DENIED: USE CATALOG denied for mip`. `/api/health` reports
`warehouse: "down"` and the app boots into the global degraded banner.

---

## 2. Schema `mip.gold` (product surfaces — required)

```sql
GRANT USE SCHEMA, SELECT ON SCHEMA mip.gold TO `mip-app`;
```

**Objects covered.** `lead_population`, `lead_score`, `borrower_360`,
`borrower_dossier`, `evidence_events`, `property_owner_bridge`,
`county_rollup`, `zip_rollup`, `state_top_segment`, `lockin_cohort`,
`segment_population`, `borrower_lifecycle_state`,
`funnel_snapshot_daily`, and the UC SQL functions
`fn_lead_score`, `fn_in_the_money`, `fn_rate_spread`,
`fn_next_best_offer`.

**What breaks if missing.** Every customer-visible page is empty.
Portfolio preview returns 503, `/api/leads` returns 500, the map
renders blank, the segment cards show zeros. Not a degraded banner —
an outage. Grant this first.

---

## 3. Schema `mip.ref` (reference/configuration — required)

```sql
GRANT USE SCHEMA, SELECT ON SCHEMA mip.ref TO `mip-app`;
```

**Objects covered.** `lender_dictionary` (PII redaction vocabulary),
`offer_rules_config` (admin-tunable offer thresholds), `state_footprint`
(the 6-state IL/CA/FL/TX/WA/CO whitelist), `refresh_run_state` (one-row
anchor for deterministic `refreshed_at` across the gold DAG).

**What breaks if missing.** Lender names redact to the raw uppercase
share string (ugly but non-fatal). Offer rules fall through to the
hard-coded defaults in `backend/config/settings.py` — meaning admin
overrides configured via `/api/admin/rules` do not apply. The
`refresh_run_state` read fails silently and every gold table's
`refreshed_at` chip drifts by seconds.

---

## 4. Schema `mip.silver` (admin/sources — optional)

```sql
GRANT USE SCHEMA, SELECT ON SCHEMA mip.silver TO `mip-app`;
```

**Objects covered.** `property_master`, `lien_current`,
`mortgage_events`, `owner_transfer_events`, `market_rates_weekly`,
`owner_property_bridge`.

**What breaks if missing.** The `/api/admin/sources` endpoint degrades
per-source with `status: "permission_denied"` on each silver row. The
Admin → Sources page renders a red chip per table instead of row counts
+ last-refreshed timestamps. The product flow itself (portfolio → leads
→ borrower → approve) is unaffected because the app reads gold, not
silver. **Grant this on prod; it is optional on dev.**

---

## 5. Schema `mip_app` (Lakebase Postgres — required)

Lakebase is a Postgres instance, not a UC schema — but UC registers it
as `mip_app_state` database catalog for cross-plane reads. Two layers
of grant:

**5a. UC database catalog (read-only federated view of Lakebase):**

```sql
GRANT USE CATALOG ON CATALOG mip_app_state TO `mip-app`;
GRANT USE SCHEMA, SELECT ON SCHEMA mip_app_state.public TO `mip-app`;
```

**5b. Lakebase Postgres role (primary write path).** The `mip-app`
binding declared in [`databricks.yml`](../../databricks.yml) lines
126–131 with `permission: CAN_CONNECT_AND_CREATE` is what actually
provisions the Postgres role. No separate `GRANT` SQL is issued against
Lakebase — the bundle deploy plus the `mip_lakebase_migrate` job
idempotently applies `lakebase/schema.sql` + `lakebase/seed_campaigns.sql`
using workspace-identity short-lived credentials. If you are coming from
a customer whose Lakebase is external (not the bundle-provisioned
instance), grant the SP `USAGE` on the `mip_app` schema and
`SELECT, INSERT, UPDATE, DELETE` on every table in it:

```sql
-- Only for externally-managed Lakebase. The bundle-provisioned
-- instance grants the app binding automatically via CAN_CONNECT_AND_CREATE.
GRANT USAGE ON SCHEMA mip_app TO "mip-app";
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA mip_app TO "mip-app";
ALTER DEFAULT PRIVILEGES IN SCHEMA mip_app
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO "mip-app";
```

**What breaks if missing.** `/api/audit/events` returns 503. Approval
writes (POST `/api/outreach/approve`) fail with `LakebaseError`. The
"Human approval writes a row to the Lakebase audit table" completion
criterion is not met — governance review will block release.

---

## 6. Cotality Delta Share

Cotality publishes the source data via a Delta Sharing provider.
Customer workspace subscribes to the share once; the app reads from
shared tables via a provider catalog (typically named
`cotality_mortgage_data` or whatever the customer negotiated).

**Click path (Databricks UI — no SQL, provider-level grant):**

1. **Catalog Explorer → Delta Sharing → Shared with me**.
2. Locate the provider (e.g. `cotality_delta_share`) and the two shares
   this app depends on: `shared-share.cotality_public_records` +
   `cotality_mortgage_signals`.
3. **Create catalog from share** → name it `cotality_mortgage_data` (or
   whatever `pipelines/lakeflow/mip_feature_pipeline.py` references —
   grep the pipeline for the literal catalog name before naming).
4. On the new provider catalog: **Permissions → Grant → `mip-app` →
   `USE CATALOG`, `SELECT`**.

**SQL equivalent** (metastore admin):

```sql
GRANT USE PROVIDER ON METASTORE TO `mip-app`;
GRANT USE CATALOG ON CATALOG cotality_mortgage_data TO `mip-app`;
GRANT USE SCHEMA, SELECT ON SCHEMA cotality_mortgage_data.corelogic TO `mip-app`;
```

**What breaks if missing.** The `mip_refresh_silver` Lakeflow pipeline
fails on first run with `PERMISSION_DENIED` on the provider catalog
read. Silver tables never materialize, so gold cannot build, so the app
boots but every page is empty. First visible symptom is `/api/health`
reporting `"silver_max_ingested_at": null`.

---

## 7. Genie space `mortgage_lead_intelligence`

**Click path (workspace UI — no SQL):**

1. **Workspace → Genie → Spaces → mortgage_lead_intelligence**. If the
   space does not exist, run `python tools/databricks/provision_genie_space.py`
   (this is the same invocation `scripts/deploy.sh` step 9 runs —
   idempotent, creates or updates).
2. **Space settings → Permissions → Add → Service principal →
   `mip-app` → `CAN RUN`**.
3. Verify the space's trusted-assets list includes the three
   `mip.semantics.*` views (`lead_generation`, `segment_performance`,
   `borrower_opportunity`). If empty, re-run `mip_refresh_scores` (step
   7 in [`docs/runbook.md`](../runbook.md) §4) — the `refresh_semantics_views`
   task publishes them and the provisioning script rebinds.

**SQL equivalent** (Genie permissions are workspace-level, not UC — no
SQL form; use the UI or the Databricks REST API
`/api/2.0/genie/spaces/{space_id}/permissions`).

**What breaks if missing.** `/api/genie/ask` returns `source: "fallback"`
for every question (safe corpus answers them with a provenance chip).
Not an outage — the degraded posture is by design — but the product
demo loses its "real Genie" proof point.

**Genie's own grants.** The Genie space itself queries the semantics
views as the space owner. If the space owner is a human user who leaves
the org, the space breaks. Own the space with a dedicated service
principal (`mip-genie-owner`) and grant that SP `USE SCHEMA` + `SELECT`
on `mip.semantics` and `mip.gold`.

---

## 8. SQL warehouse `mip_serverless_sql`

This is covered by the app binding in
[`databricks.yml`](../../databricks.yml) lines 115–119
(`permission: CAN_USE`) and requires no extra GRANT. Verify at deploy
time:

```sql
SHOW GRANTS ON WAREHOUSE `mip_serverless_sql`;
-- expect: `mip-app` with CAN_USE (or stronger).
```

**What breaks if missing.** Same as §1 — the app cannot execute SQL and
`/api/health` reports `warehouse: "down"` on every probe.

---

## 9. Verification queries

Run after completing §§1–8 to confirm every grant is live:

```sql
-- Catalog + schemas
SHOW GRANTS `mip-app` ON CATALOG mip;
SHOW GRANTS `mip-app` ON SCHEMA mip.gold;
SHOW GRANTS `mip-app` ON SCHEMA mip.ref;
SHOW GRANTS `mip-app` ON SCHEMA mip.silver;
SHOW GRANTS `mip-app` ON SCHEMA mip_app_state.public;

-- Cotality share (catalog name depends on customer)
SHOW GRANTS `mip-app` ON CATALOG cotality_mortgage_data;

-- Warehouse
SHOW GRANTS ON WAREHOUSE `mip_serverless_sql`;

-- Concrete round-trip
SELECT COUNT(*) FROM mip.gold.borrower_360;     -- expect > 0 after refresh
SELECT COUNT(*) FROM mip.ref.offer_rules_config; -- expect > 0 after seed
SELECT COUNT(*) FROM mip.silver.property_master; -- optional; §4 gate
```

---

## 10. Trust boundary — X-Forwarded-* headers

Databricks Apps is the authoritative identity edge. The platform strips
every inbound `X-Forwarded-Email`, `X-Forwarded-User`, and
`X-Forwarded-Groups` header from customer traffic and injects its own
values based on the authenticated workspace user. The FastAPI backend
reads those headers to attribute audit rows
([`backend/services/audit_store.py::resolve_actor`](../../backend/services/audit_store.py))
and to gate the admin surface
([`backend/services/rbac.py::require_admin`](../../backend/services/rbac.py)).

The setting `MIP_TRUST_FORWARDED_HEADERS` (default `True`) controls this
behavior:

- **`True` — Databricks Apps posture (default).** The backend trusts
  `X-Forwarded-*` values because the Databricks Apps edge has already
  validated the caller. This matches every Entrada-shipped deploy.
- **`False` — fail-closed for unusual deploys.** If the customer fronts
  the FastAPI process with a reverse proxy that does NOT strip inbound
  `X-Forwarded-*` headers (a misconfigured NGINX, an Envoy sidecar
  without `use_remote_address`, a load-balancer in legacy mode), a
  caller could forge headers and claim any identity. Flipping to `False`
  makes the backend:

  * Ignore `X-Forwarded-Email` / `X-Forwarded-User` in `resolve_actor`
    and write audit rows attributed to `unknown-actor@untrusted-edge` —
    a distinct marker string that is trivially greppable and will never
    collide with a real workspace email.
  * Ignore `X-Forwarded-Groups` in `require_admin`, which means the
    group-membership admit path is disabled entirely. Only the email
    allowlist (`MIP_ADMIN_EMAILS`, a server-side env var) can admit to
    admin routes — and with the email header untrusted, even that path
    fails. Effective posture: admin surface is closed until the deploy
    is corrected.

Flip this flag only if you cannot guarantee the edge strips
`X-Forwarded-*`. The default is correct for Databricks Apps; changing
it for an Apps-hosted deploy will make the product unusable without
gaining any real safety.

---

## 11. Negative grants (things you should NOT give the app SP)

- **`MANAGE` or `ALL PRIVILEGES`** on `mip` catalog. The app only reads
  gold/ref/silver and writes to `mip_app` — never DDL. A leaked app
  credential should not be able to drop tables.
- **`MODIFY`** on `mip.gold` / `mip.silver`. Gold/silver are
  materialized by bundle jobs under a separate jobs SP; the app SP
  should never write there.
- **`CAN_MANAGE`** on the app resource. That belongs to the Entrada
  delivery team's admin group, not the app identity itself.
- **Direct Postgres `SUPERUSER`** on the Lakebase role. The default
  `CAN_CONNECT_AND_CREATE` is sufficient; elevation is an over-grant.

---

*Owner: governance-security-reviewer + principal-architect. Review
cadence: any time a new `mip.*` schema or share is introduced. Every
new schema needs its own §N in this file and a smoke query in §9.*
