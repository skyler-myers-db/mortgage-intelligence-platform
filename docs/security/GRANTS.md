# Unity Catalog grants for the MIP app service principal

> **Internal implementation artifact. Not approved for public release.**
> Contains workspace object names, grant SQL, and provider/share access
> assumptions intended for implementation operators only.

**Automation status (2026-06-11, audit P1-3).** This document is now the
audit-readable MATRIX, no longer a required manual runbook:

* §catalog/§gold/§ref/§audit grants to the app service principal are
  applied idempotently by `scripts/deploy.sh` **step 4c** and the
  post-agentic AI Gateway table-grant step (resolves the SP client id
  from `databricks apps get`, executes via the deploy warehouse, fails
  the deploy loudly when the deploying identity lacks GRANT authority).
* §Lakebase app-role grants are applied by `jobs/lakebase_migrate.py`
  after schema + seed (role discovered from `pg_roles`; `action_audit`
  stays append-only via REVOKE; missing role warns and converges on the
  next deploy).
* §provider/§silver (ETL identity) grants still require a metastore admin
  when the deploying identity does not own the share — the deploy step's
  failure message points here.

**Audience.** The Entrada/Databricks SE (or customer workspace admin) who
runs `./scripts/deploy.sh -t dev|prod` against a fresh customer workspace.
That script wraps the Databricks bundle resource deploy plus app promotion and
population jobs. Every SQL block below remains copy-paste-able for manual
recovery and review.

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
GRANT EXECUTE ON FUNCTION mip.gold.fn_build_cohort TO `mip-app`;
GRANT EXECUTE ON FUNCTION mip.gold.fn_segment_counts TO `mip-app`;
GRANT EXECUTE ON FUNCTION mip.gold.fn_lead_queue_url TO `mip-app`;
```

**Objects covered.** `lead_population`, `lead_score`, `borrower_360`,
`borrower_dossier`, `evidence_events`, `property_owner_bridge`,
`county_rollup`, `zip_rollup`, `state_top_segment`, `lockin_cohort`,
`segment_population`, `borrower_lifecycle_state`,
`funnel_snapshot_daily`, `address_lookup`, and the UC SQL functions
`fn_lead_score`, `fn_in_the_money`, `fn_rate_spread`,
`fn_next_best_offer`, plus the reviewed Growth Agent read-only helper
functions `fn_build_cohort`, `fn_segment_counts`, and
`fn_lead_queue_url`.

**Governed property loan lookup.** `mip.gold.address_lookup` (added by the
property-loan-lookup slice) is covered by the schema-level
`GRANT SELECT ON SCHEMA mip.gold` above — no per-table grant is required.
It is the address→CLIP→loan lookup spine keyed on a salt-free
`sha2(canonicalized_address || '|' || zip5, 256)`. The app SP reads only
this gold table; it never reads the raw Cotality share. The hash column is
built at ETL refresh time by `ctas_address_lookup` (which runs under the
ETL/deploy identity that already holds §5/§7 share access), and the raw
street address is never projected into the table — only its hash. This
preserves the §5 boundary: the running app cannot see raw/silver street
addresses.

Threat-model honesty (external audit, 2026-07-08): because the gold join
key is a **salt-free** hash, a privileged UC reader who already possesses
candidate street addresses can test membership by hashing them. That
adversary must already hold address data, so the key does not *leak*
addresses — but do not describe it as "not recoverable." The audit ledger
never stores this hash (it records a tenant-secret HMAC token via
`pii_redaction.mask_address_for_audit`). Customer-deploy hardening: derive
the gold key with the tenant secret as well (keyed hash computed by the
ETL via a secret-scope lookup), which removes the dictionary vector for
any reader lacking the secret; tracked as the companion requirement to
`MIP_COTALITY_ID_MASK_SECRET` being mandatory outside dev/sandbox.

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
(US-state display metadata; live coverage comes from gold rollups),
`refresh_run_state` (one-row
anchor for deterministic `refreshed_at` across the gold DAG).

**What breaks if missing.** Lender names redact to the raw uppercase
share string (ugly but non-fatal). Offer rules cannot be read from the
governed Unity Catalog rules table, so the admin rules surface and gold
refresh path fail visibly instead of silently applying stale thresholds.
The `refresh_run_state` read fails silently and every gold table's
`refreshed_at` chip drifts by seconds.

---

## 4. Schema `mip.audit` (AI Gateway inference proof — required when enabled)

```sql
GRANT USE SCHEMA ON SCHEMA mip.audit TO `mip-app`;
-- Table prefix comes from MIP_AI_GATEWAY_INFERENCE_TABLE. The default
-- provisioner value is mip.audit.mip_agent_gateway_llama, which usually
-- materializes at least mip.audit.mip_agent_gateway_llama_payload.
GRANT SELECT ON TABLE mip.audit.mip_agent_gateway_llama_payload TO `mip-app`;
```

**Objects covered.** Only the MIP-owned AI Gateway inference-log tables
whose names match the configured prefix `MIP_AI_GATEWAY_INFERENCE_TABLE`
(default prefix `mip.audit.mip_agent_gateway_llama`). `scripts/deploy.sh`
runs `tools/databricks/grant_ai_gateway_inference_table.py` after AI
Gateway provisioning to discover the concrete prefixed table names and
grant `SELECT` on those tables only.

**What breaks if missing.** The AI Gateway capability row remains
`configured` / non-claimable because the deployment verifier cannot mark a
fresh `mip_app.ai_gateway_proof_ledger` row as verified for the current
`MIP_GIT_SHA`, and the runtime probe cannot corroborate current deployment
Gateway traffic. This does not break the rest of the app; it prevents
claiming AI Gateway governance live.

**What not to grant.** Do not grant `SELECT ON SCHEMA mip.audit` to the
running app service principal. That would expose every current and future
audit table in the schema. The app needs `USE SCHEMA` plus `SELECT` on
the MIP Gateway prefix tables only.

---

## 5. Schema `mip.silver` (ETL only — do not grant to the App)

The running Databricks App service principal should not receive direct
`SELECT` on `mip.silver.*`. The Admin → Sources panel reads
`mip.gold.source_readiness`, a non-PII summary produced by the gold
refresh job. That keeps source readiness live without weakening the
governance boundary that prevents the app from accidentally querying
raw/silver fields.

Grant silver access to the ETL/deploy identity that runs
`mip_refresh_silver` and `mip_refresh_scores`, not to `mip-app`:

```sql
GRANT USE SCHEMA, SELECT ON SCHEMA mip.silver TO `sp-mip-etl`;
```

**What breaks if missing.** The refresh jobs cannot rebuild gold tables
or `mip.gold.source_readiness`. The product flow then goes stale or
fails at refresh time. The app itself still only needs `mip.gold` and
`mip.ref` reads.

---

## 6. Schema `mip_app` (Lakebase Postgres — required)

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
instance), grant the SP only the table permissions its runtime paths
need. The audit ledger is append-only: `mip-app` gets `SELECT, INSERT`
there and must not receive `UPDATE` or `DELETE`. `lakebase/schema.sql`
also installs `trg_action_audit_append_only`, a statement-level trigger
that rejects `UPDATE` / `DELETE` even if a bundle-provisioned identity owns
the table or receives broader grants.

```sql
-- Only for externally-managed Lakebase. The bundle-provisioned
-- instance grants the app binding automatically via CAN_CONNECT_AND_CREATE.
GRANT USAGE ON SCHEMA mip_app TO "mip-app";
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE mip_app.campaigns TO "mip-app";
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE mip_app.approvals TO "mip-app";
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE mip_app.saved_leads TO "mip-app";
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE mip_app.outreach_drafts TO "mip-app";
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE mip_app.genie_sessions TO "mip-app";
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE mip_app.genie_messages TO "mip-app";
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE mip_app.genie_cohorts TO "mip-app";
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE mip_app.genie_cohort_members TO "mip-app";
GRANT SELECT, INSERT ON TABLE mip_app.action_audit TO "mip-app";
REVOKE UPDATE, DELETE ON TABLE mip_app.action_audit FROM "mip-app";
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE mip_app.agent_sessions TO "mip-app";
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE mip_app.feedback TO "mip-app";
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE mip_app.growth_agent_runs TO "mip-app";
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE mip_app.growth_agent_monitors TO "mip-app";
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE mip_app.growth_agent_notification_drafts TO "mip-app";
```

**What breaks if missing.** `/api/audit/events` returns 503. Approval
writes (POST `/api/outreach/approve`) fail with `LakebaseError`. The
"Human approval writes a row to the Lakebase audit table" completion
criterion is not met — governance review will block release.

---

## 7. Cotality Delta Share

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
4. On the new provider catalog: **Permissions → Grant → the ETL/deploy
   identity that runs `mip_refresh_silver` → `USE CATALOG`, `SELECT`**.
   Do not grant the running `mip-app` service principal direct read access
   to Cotality provider/raw catalogs; the app reads curated `mip.gold` and
   `mip.ref` surfaces only.

**SQL equivalent** (metastore admin):

```sql
GRANT USE PROVIDER ON METASTORE TO `sp-mip-etl`;
GRANT USE CATALOG ON CATALOG cotality_mortgage_data TO `sp-mip-etl`;
GRANT USE SCHEMA, SELECT ON SCHEMA cotality_mortgage_data.corelogic TO `sp-mip-etl`;
```

**What breaks if missing.** The `mip_refresh_silver` Lakeflow pipeline
fails on first run with `PERMISSION_DENIED` on the provider catalog
read. Silver tables never materialize, so gold cannot build, so the app
boots but every page is empty. First visible symptom is `/api/health`
reporting `"silver_max_ingested_at": null`.

---

## 8. Genie space `mortgage_lead_intelligence`

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

**What breaks if missing.** `/api/genie/message` returns `source: "degraded"`
for every question. Not an outage — the degraded posture is by design and
does not fabricate metrics — but the product demo loses its "real Genie"
proof point.

**Genie's own grants.** The Genie space itself queries the semantics
views as the space owner. If the space owner is a human user who leaves
the org, the space breaks. Own the space with a dedicated service
principal (`mip-genie-owner`) and grant that SP `USE SCHEMA` + `SELECT`
on `mip.semantics` and `mip.gold`.

---

## 9. SQL warehouse `mip_serverless_sql`

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

## 10. Verification queries

Run after completing §§1–9 to confirm every grant is live:

```sql
-- Catalog + schemas
SHOW GRANTS `mip-app` ON CATALOG mip;
SHOW GRANTS `mip-app` ON SCHEMA mip.gold;
SHOW GRANTS `mip-app` ON SCHEMA mip.ref;
SHOW GRANTS `mip-app` ON SCHEMA mip.audit;
SHOW GRANTS `mip-app` ON TABLE mip.audit.mip_agent_gateway_llama_payload;
SHOW GRANTS `mip-app` ON SCHEMA mip_app_state.public;

-- Cotality share (catalog name depends on customer) -- ETL/deploy identity only
SHOW GRANTS `sp-mip-etl` ON CATALOG cotality_mortgage_data;

-- Warehouse
SHOW GRANTS ON WAREHOUSE `mip_serverless_sql`;

-- Concrete round-trip
SELECT COUNT(*) FROM mip.gold.borrower_360;     -- expect > 0 after refresh
SELECT COUNT(*) FROM mip.ref.offer_rules_config; -- expect > 0 after seed
SELECT COUNT(*) FROM mip.audit.mip_agent_gateway_llama_payload
WHERE client_request_id LIKE 'mip-capability-%'; -- expect > 0 after live capability probe
-- Optional ETL-only proof; run as `sp-mip-etl`, not `mip-app`.
SELECT COUNT(*) FROM mip.silver.property_master;
```

---

## 11. Trust boundary — X-Forwarded-* headers

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

### 11a. Non-Databricks-Apps deploys — explicit guidance

A handful of customers run the FastAPI process outside Databricks Apps
(Azure App Service, GKE, a VM fronted by NGINX). That is a legitimate
but unusual shape, and the `trust_forwarded_headers=True` default is
**unsafe** there: without the Apps edge, there is no guarantee the
upstream proxy strips client-supplied `X-Forwarded-Email` /
`X-Forwarded-Groups` headers. A caller can then send any email and
claim any identity — audit rows become forgeable, and if your proxy
also doesn't strip `X-Forwarded-Groups`, the admin surface is as well.

**Boot-time warning.** On process start
(`backend/config/settings.py::check_trust_boundary_at_startup`), the
app emits a structured WARNING `event=rbac_trust_boundary_unclear`
when `trust_forwarded_headers=True` and the runtime does NOT look like
a Databricks Apps deploy (no `DATABRICKS_APP_PORT` / `DATABRICKS_APP_URL`
env var). Operators should treat that log line as a deploy-shape
smell test: either the Apps marker env var wasn't plumbed through, or
the deploy genuinely is non-Apps and the flag needs attention.
The same condition is surfaced on `/api/v1/admin/health` as
`boundary_warning` so admins do not have to discover the issue only in
stdout logs.

**What to do.** On a non-Apps deploy, set
`MIP_TRUST_FORWARDED_HEADERS=false` in the environment fronting the
Python process. The product shifts to a fail-closed posture:

- Audit rows attribute to `unknown-actor@untrusted-edge` (a distinct,
  greppable string) rather than a caller-supplied email.
- The admin surface closes entirely — only the email allowlist can
  admit, and with the email header ignored, no caller passes.
- The startup WARNING stops firing on the next boot because trust is
  now explicitly off.

This is the correct posture when the edge is not trusted. If your
non-Apps deploy has a reverse proxy that DOES strip inbound
`X-Forwarded-*` (verify with an e2e test that spoofed headers are
dropped), you can leave trust enabled — but document that boundary
assumption in your runbook.

---

## 12. Negative grants (things you should NOT give the app SP)

-- **`MANAGE` or `ALL PRIVILEGES`** on `mip` catalog. The app only reads
  gold/ref and writes to `mip_app` — never DDL. A leaked app
  credential should not be able to drop tables.
- **`MODIFY`** on `mip.gold` / `mip.silver`. Gold/silver are
  materialized by bundle jobs under a separate jobs SP; the app SP
  should never write there.
- **`SELECT ON SCHEMA mip.audit`**. The app only needs the MIP-owned AI
  Gateway inference-log table prefix described in §4, not every audit
  object that may later land in the schema.
- **`CAN_MANAGE`** on the app resource. That belongs to the Entrada
  delivery team's admin group, not the app identity itself.
- **Direct Postgres `SUPERUSER`** on the Lakebase role. The default
  `CAN_CONNECT_AND_CREATE` is sufficient; elevation is an over-grant.

---

*Owner: governance-security-reviewer + principal-architect. Review
cadence: any time a new `mip.*` schema or share is introduced. Every
new schema needs its own §N in this file and a smoke query in §10.*
