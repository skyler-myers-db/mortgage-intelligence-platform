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
* §Lakebase app/verifier-role grants are applied by `jobs/lakebase_migrate.py`
  after schema + seed. The job resolves exactly
  `databricks apps.get(name).service_principal_client_id`, waits only for
  that Postgres role plus the explicit
  `MIP_AI_GATEWAY_VERIFIER_CLIENT_ID`, revokes prior privileges, applies the
  reviewed per-table matrices, and fails the deploy on lookup, role,
  inventory, or postflight errors.
* §provider/§silver (ETL identity) grants still require a metastore admin
  when the deploying identity does not own the share — the deploy step's
  failure message points here.

**Audience.** The Entrada/Databricks SE (or customer workspace admin) who
runs `./scripts/deploy.sh -t dev|prod` against a fresh customer workspace.
That script wraps the Databricks bundle resource deploy plus app promotion and
population jobs. Every SQL block below remains copy-paste-able for manual
recovery and review.

**Precondition for manual recovery.** The command-of-record deployment
(`./scripts/deploy.sh -t dev|prod`) has completed its minimal namespace and
bundle-resource phases once, so the `mip-app` resource, SQL warehouse,
Lakebase instance, and governed UC catalog exist. A bare
`databricks bundle deploy` is only a post-bootstrap resource-recovery path; it
cannot establish this precondition on a fresh workspace. The grants below bind
the app's workspace identity to the UC objects it already owns logically but
cannot yet read.

**Identity.** Unity Catalog grants target the workspace-bound service
principal associated with the app (shown as `mip-app` in the UC examples).
The Lakebase Postgres role is different: it is exactly the app resource's
`service_principal_client_id` returned by `databricks apps get mip-app` /
`WorkspaceClient().apps.get("mip-app")`. App names, service-principal display
names, and numeric ids are not accepted as Lakebase role substitutes.
Live automation has separate normal, admin, AI Gateway verifier, and
agent-runtime service principals. Only the admin principal is a member of
`mip-admin`; the verifier is not app-admin and uses its OAuth application/client
id as its Lakebase role. The agent runtime owns the managed Supervisor, outer
Gateway endpoint, registered proxy model, MLflow experiment, and the exact
inference tables Databricks creates for that endpoint. It has no App, Lakebase,
warehouse, admin-group, campaign, borrower-table, or unrelated audit-table
access.
Deployment proves that negative claim globally: runtime-credentialed UC checks
walk every effective catalog child, while an exact principal-pinned workspace
admin enumerates all Apps, Lakebase instances, Genie spaces, and
customer-created serving endpoints. ID-less, creator-less Databricks
`system.ai` foundation endpoints are covered by the fixed UC system-model
inventory rather than treated as customer serving ACLs. Runtime
may hold exact direct `CAN_MANAGE` only on its reviewed green endpoints (and a
pinned runtime-owned blue endpoint during cutover) plus exact direct `CAN_RUN`
on the reviewed Genie space; verifier may hold exact direct `CAN_QUERY` only on
the reviewed Gateway. Any unrelated, inherited, group-derived, or broader
access fails closed, and the endpoint audits repeat after blue retirement.
The App authenticates the deployer-signed exact resource envelope and inspects
only the outer Gateway it can query; it never receives direct Supervisor,
registered-model, or experiment permissions. The runtime-owned proxy performs
the full private-resource and ACL proof before each inference.

**Companion docs.** [`docs/se-onboarding.md`](../se-onboarding.md) is the
end-to-end walkthrough; this file is the "grants reference" it links
to. [`docs/runbook.md`](../runbook.md) covers operator recovery after a
live incident — not first-deploy setup.

**Who runs these statements.** The approved catalog owner or a metastore admin
with grant authority. The SE's own workspace login is usually insufficient;
use the same approved deploying identity as the command-of-record or pair with
a customer metastore administrator.

For the governed treatment boundary, the current deployer canonical
`user_name` is trusted automatically. If the metastore, catalog, audit schema,
or treatment table is owned by another approved user, service-principal
application ID, or governance group, list its canonical principal name in
`MIP_UC_APPROVED_OWNER_PRINCIPALS` (comma-separated). Group-owned objects
also require dedicated account OAuth credentials. Account SCIM binds the App
and group to immutable IDs, but is deliberately not used as negative
membership evidence: Automatic Identity Management can omit effective members
from account/group SCIM responses. For each forbidden app-facing M2M and
existing target-App identity, deployment creates a five-minute OAuth secret and
uses it to request that target's own raw Current User SCIM
`id,userName,groups` projection. The read-only
`groups` collection covers direct, nested, and dynamically calculated
membership and is matched to the approved group's immutable account ID. A
positive result, omitted or malformed evidence, failed API request, identity or
group mismatch, or unproven secret cleanup fails the deployment closed. No SQL
warehouse access is required. The account OAuth identity therefore needs
account-admin authority for a first install with an approved group owner,
because the bundle-created target App does not exist early enough for delegated
management to be granted in advance. After that install, downscope it to
Service Principal Manager on every forbidden normal, operator2, admin,
verifier, agent-runtime, and now-existing target-App principal whose membership
is probed. It must itself be distinct from every app-facing M2M and target-App
principal. The
deployment also fails closed on unresolved, ambiguous, or App-owned objects.
Configure that separate identity through `DATABRICKS_ACCOUNT_HOST`,
`DATABRICKS_ACCOUNT_ID`, `DATABRICKS_ACCOUNT_CLIENT_ID`, and
`DATABRICKS_ACCOUNT_CLIENT_SECRET`; workspace PAT configuration is never
reused for account operations.

Deployment-side workspace operations are also identity-bound. The deploy
wrapper copies the reviewed workspace PAT (or selected CLI profile) into a
dedicated deployer-only binding before reading App M2M credentials. The normal
App client ID and secret remain shell-local and are exposed only to explicit
token-mint or identity-verification child processes. UC convergence and App
stop compensation therefore cannot silently authenticate as the normal App
service principal.

The deployment keeps the governed treatment write path fail-closed throughout
rollout. The source-controlled App baseline carries
`MIP_CAMPAIGN_TREATMENT_RUNTIME_ENABLED=0`; the failure trap is armed before
the existing App is inspected or quiesced; and the App remains read-only while
constraints, table properties, ownership, and effective privileges converge.
Only a promoted App snapshot carries marker `1`. After green health and
hosted-tool proof, the signed last-good record is persisted and verified while
runtime `MODIFY` remains quiesced. Capture then restores exact treatment access
and repeats App, resource, health, and signed-lease verification. The protected
workspace lease binds the active deployment UUID into both payload and health;
it renews continuously and is never auto-replaced merely because its signed TTL
elapsed. Any persistence, lease, or active/pending-deployment drift immediately
re-quiesces treatment authority. A later failure must prove the App stopped; if
that proof fails, deployment also attempts treatment-write quiescence and exits
with a dedicated compensation failure instead of reporting the original step
as the complete failure state.

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
`fn_estimated_upb`, `fn_next_best_offer`, plus the reviewed Growth Agent
read-only helper functions `fn_build_cohort`, `fn_segment_counts`, and
`fn_lead_queue_url`.

**Lifecycle mirror writes (two-table MODIFY exception).** The app service
principal holds `MODIFY` on exactly two gold tables:
`mip.gold.borrower_lifecycle_state` (sparse changed-row `MERGE`) and
`mip.gold.funnel_snapshot_daily` (idempotent `MERGE INTO` keyed on
snapshot_date/state/segment) — the pair the event-triggered post-approval
sync writes. The second table surfaced live only after the first grant
landed (2026-07-08): the sync fails at its first missing permission, so
sibling gaps hide behind the leading one. The post-approval lifecycle mirror is
event-triggered from the app (see `backend/services/job_trigger.py` for why
event-triggered beats scheduled here) and writes via a keyed Delta `MERGE`
into the DDL-created table — never CTAS, so no CREATE or
ownership rights are needed (external audit 2026-07-08: the earlier CTAS
path 403'd with PERMISSION_DENIED under the SELECT-only schema grant). Every
other gold object remains read-only to the app.

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
GRANT USE CATALOG ON CATALOG mip TO `mip-app`;
GRANT USE SCHEMA ON SCHEMA mip.audit TO `mip-app`;
GRANT SELECT, MODIFY ON TABLE mip.audit.campaign_treatment_snapshot TO `mip-app`;
-- Substitute only the exact contract-hashed payload table discovered by
-- grant_ai_gateway_inference_table.py; never grant a fixed or family-wide name.
GRANT SELECT ON TABLE mip.audit.mip_agent_gateway_growth_agent_<resource-hash-12>_payload TO `mip-app`;
GRANT USE CATALOG ON CATALOG mip TO `verifier-client-id`;
GRANT USE SCHEMA ON SCHEMA mip.audit TO `verifier-client-id`;
GRANT SELECT ON TABLE mip.audit.mip_agent_gateway_growth_agent_<resource-hash-12>_payload TO `verifier-client-id`;
GRANT USE CATALOG ON CATALOG mip TO `agent-runtime-client-id`;
GRANT USE SCHEMA ON SCHEMA mip.gold TO `agent-runtime-client-id`;
GRANT EXECUTE ON FUNCTION mip.gold.fn_build_cohort TO `agent-runtime-client-id`;
GRANT EXECUTE ON FUNCTION mip.gold.fn_segment_counts TO `agent-runtime-client-id`;
GRANT EXECUTE ON FUNCTION mip.gold.fn_lead_queue_url TO `agent-runtime-client-id`;
GRANT USE SCHEMA ON SCHEMA mip.audit TO `agent-runtime-client-id`;
-- Bootstrap-only; deploy.sh revokes both on EXIT after exact resource convergence.
GRANT CREATE MODEL ON SCHEMA mip.audit TO `agent-runtime-client-id`;
GRANT CREATE TABLE ON SCHEMA mip.audit TO `agent-runtime-client-id`;
```

**Objects covered.** The runtime app receives `SELECT, MODIFY` on the single
append-only `mip.audit.campaign_treatment_snapshot` table. Campaign creation
writes the exact T0 treatment/holdout assignment and Lakebase pins the committed
Delta version; outreach reads that pinned version and intersects it with current
eligibility. The verifier receives no privilege on this table.

The treatment table retains both Delta transaction logs and deleted data files
for 2,555 days. Both properties are required: the Lakebase manifest pins a
concrete `VERSION AS OF`, so shortening deleted-file retention (or vacuuming
below that governed window) can make an otherwise valid campaign proof
unreadable. Verify both effective properties and a pinned-version read in the
live staging drill before claiming this boundary is deployable.

All other audit access is limited to the MIP-owned AI Gateway inference-log tables
whose names match the configured prefix `MIP_AI_GATEWAY_INFERENCE_TABLE`
(generated prefix
`<catalog>.audit.mip_agent_gateway_growth_agent_<resource-hash-12>`).
`scripts/deploy.sh`
runs `tools/databricks/grant_ai_gateway_inference_table.py` after AI
Gateway provisioning to discover the concrete prefixed table names and grant
`SELECT` on those tables only to both the runtime app and dedicated verifier.
`tools/databricks/provision_m2m_oauth.py --identity-role verifier` separately
converges `CAN_QUERY` on the configured serving endpoint. Both identities need
the catalog's non-data-bearing `USE CATALOG` privilege before `USE SCHEMA` and
table-scoped `SELECT` can take effect; neither identity gets schema-wide table
reads.

The fifth agent-runtime identity receives no explicit inference-table grant,
but Databricks makes the endpoint creator the owner of its inference tables;
ownership necessarily includes read and write capabilities on those exact
payload tables. This is not treated as isolation from payloads the same runtime
already processes in flight. The boundary instead forbids every unrelated
audit table, borrower table, Lakebase role, warehouse grant, App permission,
and admin membership. The deploy script re-audits those exclusions, proves
direct Genie `CAN_RUN`, exact UC-function `EXECUTE`, and runtime creator fields.
Temporary schema-level creation privileges are revoked before App promotion
and by EXIT compensation after any failed run.
Each deploy also audits that the runtime has no Lakebase role or effective SQL
warehouse ACL, then runs credentialed negative probes proving App HTTP,
App-permission administration, service-principal secret listing, and warehouse
statement execution are denied. The exact Supervisor definition/tools and
declared MLflow resources bound into the source hash are the runtime's only
governed data paths.

The Unity Catalog postflight is a runtime-credentialed, workspace-global
visibility inventory. It authenticates as the dedicated agent runtime (never
as the deployer), uses `include_browse` listings plus the authoritative Grants
`get_effective` API, and reads every response page. A direct or inherited grant
on a non-MIP child makes that catalog/schema/object visible to the runtime and
therefore fails the release gate even when the runtime lacks `USE CATALOG`.
Bounded parallel catalog walks keep this exact check practical without relying
on a deployer whose inventory may be incomplete. Registered models are listed
globally because the Databricks SDK rejects catalog-only model listings without
a schema.

The data-plane allowlist is `USE CATALOG` on MIP, `USE SCHEMA` on `gold` and
`audit`, `EXECUTE` on the three reviewed functions, runtime ownership of the
exact signed proxy-model family and contract-hashed inference-table families,
and the documented metastore `USE_MARKETPLACE_ASSETS` baseline. Non-MIP
exceptions are source- and inheritance-bound: the fixed Databricks-managed
`system` schema/function/model inventory, the `System user`-owned `samples`
catalog, direct `account users` metadata access to each catalog's fixed
`information_schema` table set. New system models or metadata tables fail until
reviewed. Even metadata-only `BROWSE`, and all `SELECT`, `MODIFY`, `MANAGE`, or
creation authority inherited from `account users` on an ordinary customer
catalog, fails the gate unless added to this fixed platform inventory after
review; an identical action granted directly to the runtime also fails because
the verifier preserves the principal and inheritance origin for every
effective action. Shares and clean rooms still require separate governance
because they are not catalog children.

**What breaks if missing.** The AI Gateway capability row remains
`configured` / non-claimable because the deployment verifier cannot mark a
fresh `mip_app.ai_gateway_proof_ledger` row as verified for the current
`MIP_GIT_SHA`, and the runtime probe cannot corroborate current deployment
Gateway traffic. This does not break the rest of the app; it prevents
claiming AI Gateway governance live.

**What not to grant.** Do not grant `SELECT ON SCHEMA mip.audit` to either the
runtime app or verifier service principal. That would expose every current and
future audit table in the schema. Each needs `USE SCHEMA`; the runtime app has
the exact treatment-table grant above plus Gateway-prefix `SELECT`, while the
verifier has only Gateway-prefix `SELECT`. The verifier must fail its identity
boundary if `campaign_treatment_snapshot` is visible, must not join `mip-admin`,
and the runtime app must not write the Lakebase proof ledger. The verifier
boundary also rejects forbidden metastore privileges and any direct, inherited,
or hidden-group privilege/ownership on non-target schemas and securables inside
the complete MIP product catalog, including empty containers that would not
appear in a table-only scan.

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
126–131 with `permission: CAN_CONNECT_AND_CREATE` provisions the Postgres
role. The `mip_lakebase_migrate` job then applies the pre-seed portion of
`lakebase/schema.sql`, `lakebase/seed_campaigns.sql`, and the post-seed schema
finalizer in one transaction using workspace-identity short-lived credentials.
The finalizer maps only the five exact legacy narrative approval ids, infers a
missing proof binding only when one immutable campaign variant is possible, and
hard-validates every proof constraint. Unknown malformed, ambiguous, or orphaned
history aborts before commit. It never deletes approvals, generated drafts, or
activation outbox rows.

Before that transaction commits, a rollback-only PostgreSQL probe on the same
connection verifies exact campaign/variant/channel binding, validated proof
constraints, one-time approval finalization, append-only audit evidence,
immutable message proof, approval removal protection, TRUNCATE-trigger coverage,
and zero probe residue. Missing roles, a failed integrity probe, or a failed grant
postflight fail the migration rather than leaving a read-healthy app whose audited
writes return 503. The resource permission initially carries database-level
`CREATE`; migration revokes that privilege and postflight requires effective
`CONNECT=true` and `CREATE=false`. Schema+seed and ACL reconciliation each run as
rollback-capable transactions. For an externally managed Lakebase, apply the same
matrix to its app role. The audit ledger is append-only:
`action_audit`, `generated_outreach_drafts`, and
`campaign_message_variants` get `SELECT, INSERT` only and must not receive
`UPDATE`, `DELETE`, or `TRUNCATE`. `approvals` retains table `UPDATE` only for
its one-time response/audit finalization; a row trigger rejects changes to
borrower, actor, campaign, variant, channel, or any other decision field.
`lakebase/schema.sql` also installs
`trg_action_audit_append_only`, a statement-level trigger that rejects
`UPDATE` / `DELETE` / `TRUNCATE` even if an identity later receives broader grants, plus
equivalent immutable triggers on the two outreach-evidence tables.

AI Gateway proof is a deployment-verifier boundary, not a runtime write path.
The app role receives `SELECT` only on `ai_gateway_proof_ledger`. The separate
OAuth service-principal role named by `MIP_AI_GATEWAY_VERIFIER_CLIENT_ID`
receives `SELECT, INSERT, UPDATE` on that table and no privilege on any other
`mip_app` table or sequence. Migration rejects identical app/verifier roles and
postflights both matrices.

The SQL below uses `"service-principal-client-id"` as a placeholder. Replace
it with the exact `service_principal_client_id`; retain double quotes so UUIDs
and other non-identifier characters are handled as one Postgres role name.

```sql
-- Applied automatically by mip_lakebase_migrate for bundle-provisioned
-- Lakebase; apply this matrix directly for an externally managed instance.
-- Revoke every reviewed object first. A newly added table is absent from this
-- list and therefore makes the automated inventory postflight fail closed.
REVOKE CREATE ON DATABASE mip_app_state FROM "service-principal-client-id";
REVOKE ALL PRIVILEGES ON SCHEMA mip_app FROM "service-principal-client-id";
REVOKE ALL PRIVILEGES ON TABLE mip_app.schema_migrations FROM "service-principal-client-id";
REVOKE ALL PRIVILEGES ON TABLE mip_app.campaigns FROM "service-principal-client-id";
REVOKE ALL PRIVILEGES ON TABLE mip_app.campaign_message_variants FROM "service-principal-client-id";
REVOKE ALL PRIVILEGES ON TABLE mip_app.tenant_disclosures FROM "service-principal-client-id";
REVOKE ALL PRIVILEGES ON TABLE mip_app.sales_team FROM "service-principal-client-id";
REVOKE ALL PRIVILEGES ON TABLE mip_app.lead_assignments FROM "service-principal-client-id";
REVOKE ALL PRIVILEGES ON TABLE mip_app.call_dispositions FROM "service-principal-client-id";
REVOKE ALL PRIVILEGES ON TABLE mip_app.approvals FROM "service-principal-client-id";
REVOKE ALL PRIVILEGES ON TABLE mip_app.saved_leads FROM "service-principal-client-id";
REVOKE ALL PRIVILEGES ON TABLE mip_app.outreach_drafts FROM "service-principal-client-id";
REVOKE ALL PRIVILEGES ON TABLE mip_app.activation_destinations FROM "service-principal-client-id";
REVOKE ALL PRIVILEGES ON TABLE mip_app.activation_outbox FROM "service-principal-client-id";
REVOKE ALL PRIVILEGES ON TABLE mip_app.lead_outcomes FROM "service-principal-client-id";
REVOKE ALL PRIVILEGES ON TABLE mip_app.action_audit FROM "service-principal-client-id";
REVOKE ALL PRIVILEGES ON TABLE mip_app.action_audit_archive_runs FROM "service-principal-client-id";
REVOKE ALL PRIVILEGES ON TABLE mip_app.generated_outreach_drafts FROM "service-principal-client-id";
REVOKE ALL PRIVILEGES ON TABLE mip_app.genie_sessions FROM "service-principal-client-id";
REVOKE ALL PRIVILEGES ON TABLE mip_app.genie_messages FROM "service-principal-client-id";
REVOKE ALL PRIVILEGES ON TABLE mip_app.genie_cohorts FROM "service-principal-client-id";
REVOKE ALL PRIVILEGES ON TABLE mip_app.genie_cohort_members FROM "service-principal-client-id";
REVOKE ALL PRIVILEGES ON TABLE mip_app.agent_sessions FROM "service-principal-client-id";
REVOKE ALL PRIVILEGES ON TABLE mip_app.growth_agent_runs FROM "service-principal-client-id";
REVOKE ALL PRIVILEGES ON TABLE mip_app.growth_agent_monitors FROM "service-principal-client-id";
REVOKE ALL PRIVILEGES ON TABLE mip_app.growth_agent_notification_drafts FROM "service-principal-client-id";
REVOKE ALL PRIVILEGES ON TABLE mip_app.ai_gateway_proof_ledger FROM "service-principal-client-id";
REVOKE ALL PRIVILEGES ON TABLE mip_app.feedback FROM "service-principal-client-id";
REVOKE ALL PRIVILEGES ON TABLE mip_app.loan_officers FROM "service-principal-client-id";
REVOKE ALL PRIVILEGES ON TABLE mip_app.kpi_snapshots FROM "service-principal-client-id";
REVOKE ALL PRIVILEGES ON TABLE mip_app.user_visits FROM "service-principal-client-id";
REVOKE ALL PRIVILEGES ON TABLE mip_app.genie_feedback_requests FROM "service-principal-client-id";
REVOKE ALL PRIVILEGES ON SEQUENCE mip_app.action_audit_audit_sequence_seq FROM "service-principal-client-id";
ALTER DEFAULT PRIVILEGES IN SCHEMA mip_app
  REVOKE ALL PRIVILEGES ON TABLES FROM "service-principal-client-id";
ALTER DEFAULT PRIVILEGES IN SCHEMA mip_app
  REVOKE ALL PRIVILEGES ON SEQUENCES FROM "service-principal-client-id";

GRANT USAGE ON SCHEMA mip_app TO "service-principal-client-id";
GRANT SELECT, INSERT, UPDATE ON TABLE mip_app.campaigns TO "service-principal-client-id";
GRANT SELECT, INSERT ON TABLE mip_app.campaign_message_variants TO "service-principal-client-id";
GRANT SELECT ON TABLE mip_app.tenant_disclosures TO "service-principal-client-id";
GRANT SELECT ON TABLE mip_app.sales_team TO "service-principal-client-id";
GRANT SELECT, INSERT, UPDATE ON TABLE mip_app.lead_assignments TO "service-principal-client-id";
GRANT SELECT, INSERT, UPDATE ON TABLE mip_app.call_dispositions TO "service-principal-client-id";
GRANT SELECT, INSERT, UPDATE ON TABLE mip_app.approvals TO "service-principal-client-id";
GRANT SELECT, INSERT, UPDATE ON TABLE mip_app.saved_leads TO "service-principal-client-id";
GRANT SELECT, INSERT, UPDATE ON TABLE mip_app.outreach_drafts TO "service-principal-client-id";
GRANT SELECT ON TABLE mip_app.activation_destinations TO "service-principal-client-id";
GRANT SELECT, INSERT, UPDATE ON TABLE mip_app.activation_outbox TO "service-principal-client-id";
GRANT SELECT, INSERT, UPDATE ON TABLE mip_app.lead_outcomes TO "service-principal-client-id";
GRANT SELECT, INSERT ON TABLE mip_app.action_audit TO "service-principal-client-id";
GRANT SELECT, INSERT ON TABLE mip_app.generated_outreach_drafts TO "service-principal-client-id";
GRANT SELECT, INSERT, UPDATE ON TABLE mip_app.genie_sessions TO "service-principal-client-id";
GRANT SELECT, INSERT ON TABLE mip_app.genie_messages TO "service-principal-client-id";
GRANT SELECT, INSERT ON TABLE mip_app.genie_cohorts TO "service-principal-client-id";
GRANT SELECT, INSERT, UPDATE ON TABLE mip_app.genie_cohort_members TO "service-principal-client-id";
GRANT SELECT, INSERT, UPDATE ON TABLE mip_app.growth_agent_runs TO "service-principal-client-id";
GRANT SELECT, INSERT, UPDATE ON TABLE mip_app.growth_agent_monitors TO "service-principal-client-id";
GRANT SELECT, INSERT, UPDATE ON TABLE mip_app.growth_agent_notification_drafts TO "service-principal-client-id";
GRANT SELECT ON TABLE mip_app.ai_gateway_proof_ledger TO "service-principal-client-id";
GRANT SELECT, INSERT, UPDATE ON TABLE mip_app.feedback TO "service-principal-client-id";
GRANT SELECT ON TABLE mip_app.loan_officers TO "service-principal-client-id";
GRANT SELECT ON TABLE mip_app.kpi_snapshots TO "service-principal-client-id";
GRANT SELECT, INSERT ON TABLE mip_app.user_visits TO "service-principal-client-id";
GRANT SELECT, INSERT, UPDATE ON TABLE mip_app.genie_feedback_requests TO "service-principal-client-id";
GRANT USAGE ON SEQUENCE mip_app.action_audit_audit_sequence_seq TO "service-principal-client-id";

-- Dedicated deployment verifier. Replace "verifier-client-id" with the
-- DATABRICKS_VERIFIER_CLIENT_ID / MIP_AI_GATEWAY_VERIFIER_CLIENT_ID value.
REVOKE CREATE ON DATABASE mip_app_state FROM "verifier-client-id";
REVOKE ALL PRIVILEGES ON SCHEMA mip_app FROM "verifier-client-id";
REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA mip_app FROM "verifier-client-id";
REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA mip_app FROM "verifier-client-id";
ALTER DEFAULT PRIVILEGES IN SCHEMA mip_app
  REVOKE ALL PRIVILEGES ON TABLES FROM "verifier-client-id";
ALTER DEFAULT PRIVILEGES IN SCHEMA mip_app
  REVOKE ALL PRIVILEGES ON SEQUENCES FROM "verifier-client-id";
GRANT USAGE ON SCHEMA mip_app TO "verifier-client-id";
GRANT SELECT, INSERT, UPDATE ON TABLE mip_app.ai_gateway_proof_ledger
  TO "verifier-client-id";
```

**LeadOutcome source/audit boundary.** The outcome API rejects name-shaped
`source_record_ref` input. Accepted external references are domain-separated
with `source_system` and HMACed using `MIP_COTALITY_ID_MASK_SECRET` into the
form `auto-<32 lowercase hex>` before repository writes, API responses, or
audit emission. Lakebase also rejects an unhashed source reference, so direct
or bypassed writes fail closed.

`USAGE` on sequences is required for defaults such as
`action_audit.audit_sequence BIGSERIAL`. Table `INSERT` alone does not grant
permission to call the backing sequence's `nextval()`. `SELECT`, `UPDATE`, and
ownership on sequences are intentionally not granted. Future-sequence default
privileges are explicitly revoked. Any new table or sequence must be added to
the code and this matrix after reviewing its runtime statements; otherwise the
migration postflight rejects the deployment. `schema_migrations`,
`action_audit_archive_runs`, and `agent_sessions` intentionally receive no app
privileges. Postflight also rejects effective database `CREATE`, schema
`CREATE`, any unreviewed table/sequence, and any privilege outside the exact
matrix above. The verifier postflight independently rejects access to every
table except `ai_gateway_proof_ledger`, all sequence access, and any future
default privilege.

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
-- Substitute the exact generated table printed by the grant postflight.
SHOW GRANTS `mip-app` ON TABLE mip.audit.mip_agent_gateway_growth_agent_<resource-hash-12>_payload;
SHOW GRANTS `mip-app` ON SCHEMA mip_app_state.public;

-- Cotality share (catalog name depends on customer) -- ETL/deploy identity only
SHOW GRANTS `sp-mip-etl` ON CATALOG cotality_mortgage_data;

-- Warehouse
SHOW GRANTS ON WAREHOUSE `mip_serverless_sql`;

-- Concrete round-trip
SELECT COUNT(*) FROM mip.gold.borrower_360;     -- expect > 0 after refresh
SELECT COUNT(*) FROM mip.ref.offer_rules_config; -- expect > 0 after seed
SELECT COUNT(*) FROM mip.audit.mip_agent_gateway_growth_agent_<resource-hash-12>_payload
WHERE client_request_id LIKE 'mip-capability-%'; -- expect > 0 after live capability probe
-- Optional ETL-only proof; run as `sp-mip-etl`, not `mip-app`.
SELECT COUNT(*) FROM mip.silver.property_master;
```

---

## 11. Trust boundary — X-Forwarded-* headers

Databricks Apps is the authoritative identity edge. Its documented identity
contract supplies `X-Forwarded-Email` and `X-Forwarded-User` for the
authenticated workspace principal. `X-Forwarded-Groups` is **not** in that
contract and is never a deployed authorization source. The FastAPI backend
resolves the documented email header first and user header second to attribute audit rows
([`backend/services/audit_store.py::resolve_actor`](../../backend/services/audit_store.py))
and matches that resolved actor against the server-owned exact
`MIP_ADMIN_IDENTITIES` / `MIP_APPROVER_IDENTITIES` allowlists (plus reviewed
human email allowlists) to gate privileged surfaces
([`backend/services/rbac.py::require_admin`](../../backend/services/rbac.py)).
The group header path exists only for local/test compatibility and is disabled
when `MIP_APP_ENV` is `sandbox`, `dev`, `prod`, `production`, or `customer`.

The setting `MIP_TRUST_FORWARDED_HEADERS` (default `True`) controls this
behavior:

- **`True` — Databricks Apps posture (default).** The backend trusts the
  documented `X-Forwarded-Email` / `X-Forwarded-User` identity values because
  the Databricks Apps edge has already validated the caller. It does not make
  `X-Forwarded-Groups` authoritative in a deployed environment.
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
  * Fail every exact identity/email authorization check because the actor is
    the untrusted-edge marker. Effective posture: admin and approver surfaces
    are closed until the deploy is corrected. The compatibility group header
    remains disabled outside local/test regardless.

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
`X-Forwarded-User` headers. A caller can then send any actor identity, making
audit attribution and exact allowlist authorization forgeable. Group claims
remain non-authoritative outside local/test.

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
- **`SELECT ON SCHEMA mip.audit`**. The app only needs the exact
  `campaign_treatment_snapshot` table and MIP-owned AI Gateway inference-log
  prefix described in §4, not every audit object that may later land in the schema.
- **`CAN_MANAGE`** on the app resource. That belongs to the Entrada
  delivery team's admin group, not the app identity itself.
- **Direct Postgres `SUPERUSER` or database `CREATE`** on the Lakebase role.
  The Apps binding currently provisions `CAN_CONNECT_AND_CREATE`; migration
  immediately removes database `CREATE` and proves the runtime role is
  connect-only outside the reviewed `mip_app` object matrix.
- **Proof-ledger writes on the runtime app role, or broad Lakebase access on
  the verifier role.** Runtime is `SELECT` only on
  `ai_gateway_proof_ledger`; verifier is `SELECT, INSERT, UPDATE` there and has
  no privileges on other app tables, sequences, or the `mip-admin` group.

---

*Owner: governance-security-reviewer + principal-architect. Review
cadence: any time a new `mip.*` schema or share is introduced. Every
new schema needs its own §N in this file and a smoke query in §10.*
