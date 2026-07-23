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
Lakebase instance, and governed UC catalog exist. Unrestricted bundle mutation
is not an operator recovery path and cannot establish this precondition on a
fresh workspace. The grants below bind the app's workspace identity to the UC
objects it already owns logically but cannot yet read.

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

The deployment-only catalog bootstrap publishes these three reviewed helpers
with `CREATE OR REPLACE FUNCTION`, which removes their object-level `EXECUTE`
grants. Customer-triggerable Admin Operations and nightly gold refreshes do
not publish them. `scripts/deploy.sh` attempts all six exact app and dedicated
agent-runtime grants after every attempted bootstrap and `mip_refresh_scores`
run, including a partially failed run, and proves their direct effective state
before continuing. A deployment that cannot prove all six grants stops and
quiesces the App instead of restoring a signed release with incomplete access.

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

The Unity Catalog boundary first runs before the runtime credential can perform
even its bounded Lakebase bootstrap mutation. On a true first install where the
MIP catalog is not present yet, this pre-bootstrap view treats every ordinary
catalog as foreign; after catalog creation, the repeated preflight excludes only
the configured MIP catalog. Both require the expected workspace administrator
to prove it directly owns the current metastore. The postflight then requires
two independent views of the exact runtime application ID. First, that same
administrator uses that control-plane authority to
enumerate every ordinary foreign catalog, including unbound catalogs. An
`OPEN` catalog or an `ISOLATED` catalog bound to the deployment workspace must
reject any direct or inherited catalog, child-object, or registered-model
privilege. An `ISOLATED` catalog whose complete authoritative binding inventory
excludes the deployment workspace is accepted only when its catalog name,
owner, catalog type, isolation mode, immutable workspace IDs, and binding types
exactly match the versioned
`MIP_UC_FOREIGN_CATALOG_BINDING_POLICY` deployment contract. This is a stronger
workspace-level deny: Databricks enforces the exclusion ahead of explicit
grants and exposes no child objects to the unbound workspace. The sealed
control-plane proof records grant-audited catalogs separately from the complete
typed binding-denial evidence. If a binding-denied catalog or registered model
becomes visible to the runtime-authenticated inventory, the proof fails rather
than suppressing the lookup.

The same control-plane pass resolves the runtime service principal exactly once
in both account and MIP workspace SCIM and requires the account identity to be
active. One bounded temporary runtime credential then reads that identity's own
direct, nested, and dynamic `/Me.groups` collection; cleanup failure is fatal.
The temporary secret is created and deleted against the immutable account SCIM
ID, while `/Me.id` must equal the separately resolved workspace SCIM ID and
`/Me.userName` must equal the runtime application ID.
That credentialed result is the frozen authoritative membership snapshot for
the entire pass. Any membership inferred positively from account group members
must appear under the same immutable group ID and name in the snapshot, but
account-member omission is not treated as negative evidence under Automatic
Identity Management. Every ordinary group in the snapshot is rejected. The
Databricks-managed `account users` baseline is the only exception: Databricks
may return it from hydrated account members, the target snapshot, or only one
of those planes because the membership is implicit. When both planes return
it, its immutable ID and name must agree. Its immutable identity must still be
present in at least one plane; if both omit it, the audit cannot exclude an
opaque system-group ID and fails closed.

The pass enumerates every workspace assigned to the current metastore and
requires exactly one direct, immutable-ID-matched `USER` assignment in the MIP
workspace, with no direct or group-derived assignment to a workspace retained
by the foreign-catalog policy. It separately checks the owner on every
inventoried catalog, schema, table, function, volume, and registered model.
Objects visible to MIP retain the full approved-owner workspace/account
resolution. For an exactly policy-matched catalog whose bindings deny MIP, the
pass does not require an unrelated owner to exist in MIP workspace SCIM;
instead, it excludes the runtime application ID, both immutable SCIM IDs,
observed runtime display aliases, every frozen group ID and name, and implicit
`account users` from the catalog and registered-model owner fields. Direct or
group-derived runtime ownership therefore fails without weakening the workspace
binding proof. Unknown isolation modes, incomplete or duplicate identity
evidence, unsupported binding types, policy drift, account-group drift,
workspace-assignment drift, and binding-read errors fail closed. Ownership
cannot masquerade as an empty grants response. The exact `__databricks_internal`
catalog is excluded only when its SDK identity remains `INTERNAL_CATALOG`,
`System user`-owned, and `OPEN`; lookalike names or source drift fail.
The runtime-authenticated half also treats ownership as authority: the runtime's
application ID, immutable SCIM ID, and immutable-ID-backed effective group names
must not own the MIP catalog, any schema, any function, any ordinary table or
volume, or any unrelated registered model. Only the exact contract-hashed
Gateway model and inference-table families may remain runtime-owned, and those
must be owned directly by the application ID with matching signed provenance.
An authorization error, incomplete identity, unknown isolation mode, malformed
binding, pagination error, or worker error likewise fails this audit; none is
interpreted as zero access. The deploy then
repeats the MIP and reviewed platform inventory while authenticated as the
dedicated agent runtime (never as the deployer), using `include_browse` listings
plus the authoritative Grants `get_effective` API and reading every response
page. The two views are coupled in one process by a typed proof bound to the
application ID, catalog, metastore, workspace, and complete foreign-catalog set;
the exact issued object is identity-registered, snapshot-checked, and consumed
atomically on its single runtime verification, so copies, mutation, or replay
cannot extend its authority. Only exact ordinary foreign catalogs already
cleared by that proof are omitted
from the runtime's otherwise impossible grant lookup. A direct or inherited
grant on a non-MIP child therefore fails before the runtime receives any UC
mutation authority, while a runtime-side authorization denial cannot hide a
grant from the metastore-owner view. Lakebase OAuth-role recovery may then use
the same service-principal credential only for an exact runtime identity check
and `database.get_database_instance` positive-control read; the one-use
bootstrap principal and retained PostgreSQL backend perform the role mutation.
That helper remains jointly bracketed by account and proof-signing authority.
Bounded parallel catalog walks keep both exact checks practical.
Registered models are listed globally because the Databricks SDK rejects
catalog-only model listings without a schema.

Shared-metastore remediation is a separately reviewed, fail-closed operation.
`tools/databricks/converge_foreign_catalog_workspace_bindings.py` accepts only a
clean committed source SHA, an active signed App deployment lease, the stopped
App's immutable identity, the exact account authority, the distinct runtime
account/workspace SCIM IDs and display aliases, and a versioned binding policy.
Its signed manifest preserves every
non-MIP metastore workspace as `READ_WRITE` for a formerly `OPEN` catalog and
preserves the exact existing bindings of every already-`ISOLATED` catalog.
Catalog metadata is read from the authoritative `include_unbound` inventory,
not a workspace-scoped `get`. The manifest records the exact direct grants of
each visible `OPEN` catalog. For an already-`ISOLATED` catalog that excludes the
MIP workspace, `direct_grants: null` is explicit evidence that the grants API is
binding-denied; an authorization error is accepted only for that exact
metadata-and-binding state.
Before each catalog write it persists a signed immutable intent under the
lease-protected workspace root, then revalidates the lease, stopped App,
account assignments, metastore inventory, policy, source SHA, catalog owner,
catalog type, bindings, and whichever direct-grant evidence is observable in
that state. After isolation excludes MIP, an exact authorization denial replaces
the pre-state grant list rather than copying or inventing post-state evidence.
A killed runner resumes only from the signed pre-state, exact desired state, or
a narrower `ISOLATED` transitional state. It never automatically reopens a
catalog after failure. Automated `ISOLATED`-to-`OPEN` rollback is forbidden:
while MIP is binding-denied, retained workspaces can change child grants that
the MIP control plane cannot observe, so matching only the former catalog-level
grant list would not prove that reopening restores the former access boundary.
Recovery therefore resumes the signed fail-closed desired state. The final
postflight re-reads the entire policy rather than trusting per-catalog success.
The current signed-manifest schema is v4. Correctly signed v3 fences remain
readable so completed/stale operations can still be classified and interrupted
operations can be reauthorized into a fresh v4 boundary. A v3 manifest can
never be persisted anew, applied, resumed, or verified as mutation authority;
it must first be reauthorized under a descendant lease, which seals the
distinct live account and workspace runtime identities. Only that explicit
v3-parent migration may cross a historical source SHA: `recover-local` requires
the parent lease ID, and the signed v4 child records the current clean SHA.
Ordinary v4 recovery remains exact-source-pinned.
Every signed account, App, and runtime identity field must already be a
canonical trimmed string; validation never compares a normalized copy while
retaining a different raw identity.
Because the excluded MIP workspace cannot enumerate foreign direct grants after
convergence, the live postflight must separately use each retained bound
workspace to prove its expected access and grant behavior.

The command of record is the manual `deploy-dev` workflow with its one-time
input, after the exact environment policy has been independently reviewed:

```bash
BRANCH="$(git branch --show-current)"
gh workflow run deploy-dev.yml --ref "$BRANCH" \
  -f remediate_foreign_catalog_bindings=true \
  -f skip_silver=false \
  -f skip_smoke=false
```

That workflow supplies the separated workspace, account-SCIM, runtime, and
proof-signing authorities. `scripts/deploy.sh` acquires the signed App lease and
starts its parent-PID-fenced heartbeat. It requires an existing stable App,
stops the exact immutable App identity before any recovery or fresh catalog
write, and then runs `snapshot`, `apply`, and `verify`. A signed immutable
completion record is written only after the whole-policy verification passes.
The ordinary UC preflight also runs while the App remains stopped. Only after
that postflight may signed-blue reconciliation start the App; signed blue is
re-proven before the first Lakebase recovery mutation. An absent or unstable
first-install App is rejected.

The lease is released from normal deployment cleanup without discarding
same-deployer recovery authority. Before the next acquire, the append-only
signed generation chain is authenticated and every lease ID in the current
same-holder recovery-root lineage is exported newest-first, including across a
runtime-writer rotation. A different deployer identity receives no authority
from that lineage, but an intervening released-lease handoff cannot erase the
original holder's signed recovery route; a returning holder resumes its most
recent root. Runtime identity is rebound by manifest classification and
reauthorization rather than by hiding signed ancestors. The deployment probes
each lineage fence until it finds the newest incomplete operation, a signed
completion, or proves all candidates absent. Discovery is read-only: only the
winner of the immutable lease successor race changes the shared root ACL, so a
losing historical holder cannot overwrite the active winner's holder/writer
permissions. Invalid or ambiguous evidence is never treated as absence. An
incomplete operation is reauthorized under the fresh descendant lease and then
resumed and verified. A completed operation with identical release inputs is
independently reauthorized and reverified; a completed operation from older
inputs terminates the historical search and causes a fresh signed snapshot
under the current reviewed policy. This also recovers an
interruption during reauthorization before its child fence exists, because the
older parent lease remains discoverable. Fence recovery verifies historical
signers through the configured key registry; it does not re-sign old evidence
with the current key.

An operator investigating a still-active lease may materialize the authoritative
workspace copy without creating a new snapshot:

```bash
python -m tools.databricks.converge_foreign_catalog_workspace_bindings \
  recover-local \
  --app-name "$MIP_APP_NAME" \
  --application-id "$DATABRICKS_AGENT_RUNTIME_CLIENT_ID" \
  --expected-inventory-principal "$DEPLOY_INVENTORY_PRINCIPAL" \
  --expected-account-id "$DATABRICKS_ACCOUNT_ID" \
  --expected-account-client-id "$DATABRICKS_ACCOUNT_CLIENT_ID" \
  --mip-catalog "$MIP_DEFAULT_CATALOG" \
  --lease-id "$MIP_APP_DEPLOYMENT_LEASE_ID" \
  --manifest /tmp/mip-foreign-catalog-manifest.json
```

There is intentionally no rollback CLI that reopens these catalogs. A request
to restore `OPEN` access requires a separately reviewed change plan that
inventories current child and effective privileges from every affected
workspace; the deployment automation cannot infer that state from its signed
pre-isolation catalog grants.

The same bounded account and proof-signing environment used by deploy is
required for these diagnostic commands. Never copy credentials into arguments,
never run without the lease heartbeat, and release the lease with
`tools.databricks.app_deployment_lease release` when the governed operation
ends.

The data-plane allowlist is `USE CATALOG` on MIP, `USE SCHEMA` on `gold` and
`audit`, `EXECUTE` on the three reviewed functions, runtime ownership of the
exact signed proxy-model family and contract-hashed inference-table families,
and the documented metastore `USE_MARKETPLACE_ASSETS` baseline. Non-MIP
exceptions are source- and inheritance-bound: the fixed Databricks-managed
`system` schema/function/model inventory, the `System user`-owned `samples`
catalog, direct `account users` metadata access to each catalog's fixed
`information_schema` table set. Lakebase database catalogs are a narrower
metadata exception: only an exact `MANAGED_ONLINE_CATALOG` may report its
generated `information_schema` schema and children as owned by the exact
non-runtime parent catalog owner instead of the literal `System user`. Schema
and child full names must remain bound to that parent, the owner still passes
the direct and group-derived runtime non-ownership proof, and the agent runtime
must have zero effective privilege on the catalog and every inventoried child.
This does not authorize the separate Databricks App service principal's
reviewed Lakebase sync grants and is not a reason to isolate the MIP state
catalog from its workspace. The managed `system.data_quality_monitoring`
family is anchored to its non-runtime schema owner and every child must retain
that exact owner; other reviewed system and samples children remain literal
`System user` objects. New system models or metadata tables fail until reviewed.
Even metadata-only `BROWSE`, and all `SELECT`, `MODIFY`, `MANAGE`, or
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
GRANT USE SCHEMA ON SCHEMA mip_app_state.mip_sync TO `mip-app`;
GRANT SELECT ON TABLE mip_app_state.mip_sync.source_readiness TO `mip-app`;
GRANT SELECT ON TABLE mip_app_state.mip_sync.segment_population TO `mip-app`;
GRANT SELECT ON TABLE mip_app_state.mip_sync.funnel_snapshot_daily TO `mip-app`;
```

The deploy applies these grants only after the Databricks synced-table
API has created and proven `mip_app_state.mip_sync`; a fresh database catalog
initially exposes Postgres system/`public`/`mip_app` schemas, not `mip_sync`.
Granting the configured sync schema earlier is therefore a first-install race.
The app does not receive UC `SELECT` on the authoritative `mip_app` Postgres
schema; primary Lakebase reads and writes use its separately constrained
Postgres role. Before synced-table provisioning, deploy inventories the entire
registered database catalog and removes all direct App catalog, table, and
schema privileges from every non-system schema, including `public`, `mip_app`,
and the configured sync schema. Because Unity Catalog excludes `MANAGE` and
`EXTERNAL USE SCHEMA` from `ALL PRIVILEGES`, convergence revokes those
privileges explicitly on each applicable catalog/schema/table object as well.
After provisioning it restores only direct `USE CATALOG`, direct `USE SCHEMA`,
and direct table-scoped `SELECT` for the exact reviewed
`MIP_LAKEBASE_SYNC_TABLES` allowlist. It never grants schema-wide `SELECT`.
The catalog-wide postflight resolves nested workspace groups and fails on any
unreviewed schema/table access, broader inherited privileges, or App/group
ownership. `information_schema` is inventoried explicitly as UC-owned system
metadata and is never included in the mutable application-schema set.

**5b. Lakebase Postgres role (primary write path).** The `mip-app` database
binding declared under `resources.apps.mip_app.resources` in
[`databricks.yml`](../../databricks.yml), with
`permission: CAN_CONNECT_AND_CREATE`, provisions the Postgres role. The deploy
first creates every non-App bundle dependency, resolves the binding to concrete
workspace resource IDs, and applies only those bindings with the Apps API. A
first install uses `apps create --no-compute`; an upgrade uses `apps update`
and proves that active/pending source deployment and compute state did not
change. Merely binding DAB state is not treated as proof that the live resource
binding was applied. The `mip_lakebase_migrate` job then applies the pre-seed portion of
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
`CREATE`; migration revokes that privilege. Because this is a dedicated MIP
application-state database, it also revokes database `TEMPORARY` from PUBLIC
and directly from both runtime roles. Postflight requires effective
`CONNECT=true`, `CREATE=false`, and `TEMPORARY=false` for each identity.
Schema+seed and ACL reconciliation each run as rollback-capable transactions.
For an externally managed Lakebase, apply the same matrix to its app role. The
audit ledger is append-only:
`action_audit`, `generated_outreach_drafts`, and
`campaign_message_variants` get `SELECT, INSERT` only and must not receive
`UPDATE`, `DELETE`, or `TRUNCATE`. `approvals` retains table `UPDATE` only for
its one-time response/audit finalization; a row trigger rejects changes to
borrower, actor, campaign, variant, channel, or any other decision field.
`lakebase/schema.sql` also installs
`trg_action_audit_append_only`, a statement-level trigger that rejects
`UPDATE` / `DELETE` / `TRUNCATE` even if an identity later receives broader grants, plus
equivalent immutable triggers on the two outreach-evidence tables.
The first catalog gate, before any schema or seed statement, inventories every
preserved executable expression that `CREATE TABLE IF NOT EXISTS` would leave
in place: column defaults, generated columns, CHECK expressions, rewrite rules,
row policies, expression indexes, and partial-index predicates. Module 0
permits no generated columns, user rewrite rules, or row policies. Every
recorded routine/operator dependency must match an exact reviewed identity and
signature; non-system types, operators, routines, and collations fail closed.
The expression text is also scanned against the small schema-derived function
allowlist because PostgreSQL omits pinned built-ins from `pg_depend`; privileged
functions such as `pg_read_file`, `current_setting`, `set_config`, and
`lo_import` therefore cannot pass merely by living in `pg_catalog`. The sole
sequence-backed default is exact-bound to
`mip_app.action_audit_audit_sequence_seq`. Six reviewed campaign CHECKs that
call app validators must match their exact dependency contract and migration
executor owner, then are dropped under `ACCESS EXCLUSIVE` lock before any
schema SQL and recreated by the post-seed suffix. Any mismatch rolls back with
zero schema statements executed.

After that first gate, migration inventories
every non-internal trigger that exists on every non-system table-like object in
the dedicated database. A clean first install may omit the reviewed triggers,
but every trigger that already exists must exactly match the code-owned contract
or the migration rolls back without executing schema/seed SQL. After that read-only
preflight, migration takes an `ACCESS EXCLUSIVE` lock on every affected table
and transactionally drops only the reviewed triggers proven to exist. This
quarantine prevents a same-shape malicious function-body rewrite from firing
during an early schema backfill; the locks remain held until commit or rollback.
The same inventory must equal the complete reviewed contract inside the
schema/seed transaction immediately before commit, so any post-migration drift
rolls back its DDL and DML. The exact code-owned contract
binds table, trigger, trigger function and signature, event/timing/row shape,
enabled state, arguments, `UPDATE OF` column lists, transition tables,
condition/constraint and deferrable state, return type, function-owner
relationship, exact table and function ownership by `current_user`, and
`SECURITY INVOKER`. An extra trigger in `public`, a
`SECURITY DEFINER` rewrite, a runtime-owned trigger function, or a missing,
disabled, column-scoped, transition-table, or deferred reviewed trigger aborts
the applicable rollback-capable transaction before commit. The later ACL
transaction independently repeats the exact inventory for both runtime roles.
Revoking function `EXECUTE` is not treated as sufficient trigger neutralization.
Because PostgreSQL stores DDL event triggers in `pg_event_trigger`, not
`pg_trigger`, schema preflight and postflight independently require the complete
three-row Databricks-managed event-trigger contract: `on_create_schema`,
`on_create_sequence`, and `on_create_table_or_view`. The contract pins each
trigger's event, exact command tags, enabled state, `cloud_admin` event and
function ownership, `public` function identity, empty signature,
`event_trigger` return type, `plpgsql` language, volatility/parallel/strict/
leakproof/security-definer/config/binary attributes, raw UTF-8 `prosrc`
SHA-256, and source byte length. `NULL` command tags remain distinct from an
empty array because `NULL` would permit every DDL command. The only accepted
function ACL states are the exact provider representations reviewed by the
code-owned contract, including the historical `cloud_admin`-owner-only state.
Migration never tries to rewrite these provider-owned routines. A partial
inventory, extra trigger, source-byte drift, extra
grantee, or any shape mismatch aborts before quarantine or schema SQL.

These provider-plane hooks grant Databricks' gateway, superuser, reader, and
writer roles access to newly created objects. Module 0 trusts that pinned
`cloud_admin` provider implementation while independently converging the App
and AI Gateway verifier matrices below. Live release verification must audit
the provider roles' recursive memberships and effective access; a provider
implementation change requires a new source digest and governance review.
Only the vanilla-PostgreSQL integration fixture may explicitly allow the
managed inventory to be absent. The production entrypoint has no environment-
or target-based opt-out, including for staging `dev` deployments.

The provider boundary also pins the `__db_system` namespace itself. It must be
owned by `databricks_control_plane`; every contained owner-bearing object must
be inventoried and owned by that role or the one exact
`databricks_writer_<current database oid>` role; any future namespace object
catalog outside that exhaustive inventory fails closed; and
the App and verifier must have no ownership or effective schema, table, column,
sequence, or routine capability there. The two provider views exposed in
`public`, `databricks_list_roles` and `databricks_synced_table_managers`, are
pinned by owner, view kind, security options, raw view-definition SHA-256 and
byte length, PUBLIC `SELECT` shape, and absence of direct runtime grants.
Lakebase also installs additional `cloud_admin`-owned routines and relations in
`public` whose ACLs the deployer cannot mutate. The dedicated MIP database
therefore pins the exact
`public` schema owner/provider ACL, removes PUBLIC `USAGE`, and requires App and
verifier to have neither `USAGE` nor `CREATE`. Direct runtime relation, column,
routine, and provider-default grants are audited independently even while
dormant. Only after those proofs may migration exclude provider objects from
mutation-generating inventories. Provider PUBLIC relation/routine ACLs remain
latent behind the closed schema. Production has no name-only exclusion or
provider-schema absence seam. Because Lakebase records the PUBLIC grant under
the `pg_database_owner` pseudo-role, closure first proves the executor has exact
`SET` authority, assumes that role transaction-locally for the revoke, and
resets it before postflight; a plain ambient-owner revoke is not accepted.

Public-schema closure commits in a short fail-safe transaction before schema
hooks or the broader ACL transaction. The schema transaction and later ACL
transaction each require zero App/verifier database sessions, so no backend
that resolved a provider OID before closure survives; every later session
begins behind the hardened namespace. Module 0 defines no
user views or materialized views and preflight rejects their `_RETURN` rules;
only `cloud_admin`-owned `public` relations are exempt after the ownership,
direct-ACL, stored-dependency, and closed-schema proofs succeed.
Callable App routines must have zero argument defaults and no stored dependency
on a `cloud_admin` public routine or relation. Existing default, generated-expression,
constraint, rule, policy, expression-index, and trigger inventories cover the
other stored execution surfaces.

The separate ACL transaction repeats the exact managed event-trigger preflight
before its first `REVOKE`, `GRANT`, or `ALTER DEFAULT PRIVILEGES` statement and
repeats the postflight after both identity matrices immediately before commit.
Event-trigger drift therefore cannot produce a false-green ACL reconciliation.

AI Gateway proof is a deployment-verifier boundary, not a runtime write path.
The app role receives `SELECT` only on `ai_gateway_proof_ledger`. The separate
OAuth service-principal role named by `MIP_AI_GATEWAY_VERIFIER_CLIENT_ID`
receives `SELECT, INSERT, UPDATE` on that table and no privilege on any other
`mip_app` table or sequence. Migration rejects identical app/verifier roles and
postflights both matrices. It rejects both directions of role membership:
neither runtime role may inherit a direct/recursive parent, and neither may be
used as a direct/recursive group role by another principal that could inherit
or `SET ROLE` into app/verifier capabilities.

Both OAuth database roles must have the exact PostgreSQL profile
`NOSUPERUSER NOCREATEROLE NOCREATEDB NOREPLICATION NOBYPASSRLS INHERIT LOGIN`,
no parent or descendant role relationships, and the one exact
`databricks_auth` security label bound to the workspace service-principal SCIM
id. This is not inferred from the three attributes exposed by the Lakebase role
API. Live testing proved that the legacy Database Instances create-role path can
set `rolreplication=true` and permit a replication-protocol `IDENTIFY_SYSTEM`
command even though `CREATEDB`, `CREATEROLE`, and `BYPASSRLS` are false.

The deployment therefore creates roles through Databricks' documented
`databricks_create_role()` SQL function, which creates an OAuth role with LOGIN
only. Before invoking that privileged primitive, deployment pins its exact
provider contract: `public.databricks_create_role(text,text)`, function kind,
return type, `cloud_admin` owner, C language, volatility/parallel/strict/
leakproof/security-definer/config attributes, extension identity/version/
namespace, database-specific `databricks_writer_<database oid>` extension
owner, binary path, raw source SHA-256 and byte length, and the exact provider
PUBLIC-executable ACL required by the one-use caller. An owner-only state is a
provider-remediation failure; deployment does not attempt to grant on a
`cloud_admin` function. Any
metadata drift or missing extension membership fails before creating a
credential or database role. If an App binding or legacy provisioner already created the exact unsafe
profile, replacement is allowed only while the App is stopped/quiesced and only
after proving exact service-principal metadata, zero ownership, zero role
relationships, and ACL dependencies limited to this database's reviewed App or
verifier objects. Exact reviewed ACLs are revoked before the control-plane role
delete and the dependency audit must then be empty. Cross-database or
unreviewed dependencies fail closed. The
role is recreated under the same service-principal client-id name, its OAuth
label and the complete role profile are rechecked: connection limit, validity,
password and per-role configuration, per-database settings, security labels,
every membership edge, and every `pg_shdepend` dependency are exact. Because
the creator `ADMIN` membership emitted by `databricks_create_role()` is recorded
as granted by `cloud_admin`, the caller cannot revoke it directly. Deployment
instead uses a newly created, one-use workspace service principal and its
provider-created SQL role as the caller. The bootstrap role receives neither
database `CREATE` nor `public` schema access.

The full state transition is serialized by one session advisory lock acquired
on the canonical `databricks_postgres` administration database. Its key is
derived from the raw instance identity and normalized target identity, so the
same lock covers a present target database, an absent target database, recovery,
and creation. Every mutation proves the exact advisory-lock row and original
backend PID immediately before acting. Finalization proves the exact unlock and
zero residual advisory-lock rows. `current_user` and `session_user` must both be
the expected deployer on administrative connections and the expected bootstrap
identity on the one-use invocation connection.

After proving the closed `public` boundary and the exact provider function
contract, deployment atomically publishes a target-bound private schema and one
zero-argument `LANGUAGE SQL SECURITY INVOKER` wrapper with an SQL-
standard `BEGIN ATOMIC` body. The exact deployer must be the database owner,
have database `CREATE`, and have `SET` authority for `pg_database_owner`.
PostgreSQL's pseudo-role does not itself inherit the real owner's database
`CREATE`: the deployer therefore creates the schema with explicit
`AUTHORIZATION pg_database_owner`, then assumes that role transaction-locally
for every remaining owner-scoped mutation; the pseudo-role owns both the schema and function
so a later database owner can recover them without depending on one persistent
deployer. Schema creation, provider-default-ACL removal,
function creation, PUBLIC revocation, bootstrap grants, and exact postflight
share that transaction. The initial schema ACL must match the live provider
chain exactly: `pg_database_owner` grants grant-option access to
`databricks_superuser`, which grants writer/reader access, while
`pg_database_owner` grants gateway access directly. An owner-authorized
`CASCADE` revoke removes only that already-proven provider chain before the
wrapper pins its owner, function kind, zero argument/default/variadic shape,
return type, SQL language, invoker security, volatility, parallel safety,
leakproof/strict flags, `search_path=pg_catalog`,
`createrole_self_grant=''`, non-null parsed `prosqlbody`, and the full canonical
`pg_get_functiondef()` text, SHA-256, and byte length through the deployer. The
deployer then captures the function OID, catalog transaction identity, raw
`prosqlbody` hash/length, and all caller-invariant metadata. The disposable
bootstrap session must match that exact publication fingerprint immediately
before invocation; it does not compare its caller-dependent deparse because
that identity intentionally cannot see `public` in its effective search path.
Its parsed body hard-binds
the reviewed application ID, casts both provider arguments to
`pg_catalog.text`, and returns `NULL` unless `current_user` and `session_user`
both equal the exact disposable bootstrap application ID. A normal `pg_depend`
edge must bind the wrapper to the exact
`public.databricks_create_role(text,text)` OID. The bootstrap role receives only
direct `USAGE` on the private schema and `EXECUTE` on the wrapper, without
PUBLIC access, `public` schema `USAGE`, or grant option. The provider primitive,
wrapper, both temporary grants, and their object/shared-dependency rows are
rechecked through the deployer immediately before opening the single invocation
connection. Because the wrapper is security-invoker, any provider-created
creator edge is attributable only to the disposable bootstrap caller; an edge
to the wrapper owner or deployer is rejected before commit.

The bootstrap credential is kept only in memory. Before a secret or database
role exists, deployment persists a separate inactive, credential-free signed
tombstone. The provider-owned role and private wrapper are created with zero
secrets and zero bootstrap sessions. Only after their exact contracts pass does
deployment create one 600-second OAuth secret, require the same singleton
immutable secret ID on the workspace and account planes, authenticate the exact
bootstrap identity, mint exactly one bounded database credential, and retain
exactly one autocommit database backend. The retained backend is pinned by PID,
role OID, username, current and session user, database, application name,
backend start, backend type, and client address. Deployment then deletes the
secret through both control planes and proves three stable empty secret inventories. It
requires the exact principal to remain active and assigned on both planes,
disables statement, idle-in-transaction, and supported whole-transaction
timeouts on the disposable session, re-proves stable empty secret inventories
at the final invocation boundary, and requires more than 120 seconds on both
captured authentication leases immediately before the provider call.

The retained backend starts an explicit transaction, repeats the lock,
principal, singleton-backend, provider, wrapper, event-trigger, and
target-absence proofs, and invokes the target-bound wrapper while the provider
can still authorize the live Databricks principal. The result, OAuth label, and
exact creator edge are validated but remain uncommitted. A provider error or
any later retirement-proof error rolls the transaction back; only an ambiguous
commit response may forward-reconcile an exact target. An exact target can
never erase a pre-commit or post-commit lifecycle failure.

While the provider result remains uncommitted, deployment deletes the exact
signed principal through account SCIM and requires direct immutable-ID GETs to
show continuous account and workspace absence for 30 seconds within a
180-second deadline, proving it absent from both the account and workspace. The
exact DELETE is repeated if the account object reappears, and every observation
also proves zero account-workspace assignments
and zero App bindings. Service-principal LIST omission never proves deletion.
Workspace PATCH/deactivation and workspace principal deletion are not cleanup
authority: live identity-federated workspaces ignored `externalId`; workspace deletion only removed the assignment
while leaving the account principal active.
Dedicated bounded account OAuth is therefore required for this
transition.

After initial OAuth authentication, deployment captures only the access token's
numeric JWT expiry and rejects a non-M2M client, a missing/non-numeric expiry, or
a remaining lifetime outside the 30-second-to-65-minute policy; the bearer token
itself is never retained in proof state. After control-plane retirement,
deployment always waits through the later of the captured OAuth access-token and
database-credential expiries, plus 120 seconds. No early authentication error
may optimize that boundary away. It then requires three
spaced, fresh-client attempts to use the destroyed bootstrap credential. Every
attempt is bracketed by two newly constructed OAuth-M2M clients for the
identity-pinned agent-runtime control, and all three clients perform the same
exact read-only Lakebase instance API call. A PAT/workspace client cannot serve
as that control. The control application ID is validated before its read, and
the observations are separated by five seconds. Deployment also requires three
fresh-deployer-bracketed attempts to reuse the old database token. The read
probe cannot mint a second database credential, preserving the one-mint
contract. Only PostgreSQL SQLSTATE class 28 counts as database rejection, and
only a reviewed OAuth authentication rejection counts as destroyed-credential
rejection. A generic network, TLS, timeout, or transport error is inconclusive.
The rejection probes use pg8000's TLS/SCRAM path because libpq collapses
startup authentication failures into a connection error without exposing the
server field; pg8000 must return the structured PostgreSQL `C` field, and error
message text is never promoted to class-28 evidence.
Throughout the mandatory wait, every opened probe backend is closed and drained
while the canonical lock, principal absence, and sole retained backend are
heartbeated. Only the resulting secret-free retirement proof permits the
already-validated provider transaction to commit. Immediately before commit,
deployment rechecks the same backend, provider and wrapper fingerprints,
bootstrap role, exact uncommitted target/profile/label/creator edge, managed
event triggers, and canonical lock. This deliberately keeps a catalog-role
transaction open across the expiry boundary so no target is published before
retirement proof. The disposable session verifies all supported transaction
timeouts remain disabled before invocation; live pre-deploy verification must
also confirm the isolated lock footprint.

The deployer also cannot `ALTER`, comment, or directly drop Databricks'
provider-owned bootstrap role. No governed mutation occurs between the final
pre-invocation fence and the target-bound wrapper call. On cleanup and recovery
paths, deployment captures the bootstrap
role OID and exact PIDs, terminates only the independently bound backends, and
requires three stable zero-session observations. It rechecks the immutable SQL and
control-plane contract immediately before using the provider role-delete API,
then proves the captured sessions, role OID, SQL role, and control-plane role
absent. A failed secret proof, account deletion, session fence, retirement proof,
or role contract retains the role, OAuth security-label handle, and signed
tombstone and blocks release; a legacy
comment alone never authorizes deletion.

Wrapper revokes and `DROP ... RESTRICT` teardown are atomic and run after
transaction-locally assuming `pg_database_owner`. Exact wrapper metadata,
managed event-trigger inventory, and the canonical lock are reproved before
each mutation and immediately before commit. A fresh deployer connection
performs final reconciliation after every bootstrap attempt; the potentially
failed invocation or DDL connection is never reused as evidence. Provider-call
or commit transport ambiguity requires three stable observations. An exact
committed target is adopted as success, stable absence propagates the original
failure, and an indeterminate target is retained with credentials revoked,
sessions drained where identity proof permits, and durable signed evidence. It
blocks release rather than authorizing target deletion. A generic error never
authorizes deletion.

Recovery accepts only the four finite wrapper ACL states and, separately, the
four historical direct-provider ACL states emitted by reviewed versions. Mixed
wrapper/legacy states are rejected. Every surviving state pins schema/function
ownership to `pg_database_owner`, the full caller/target-bound canonical
definition, and exact object, provider-function, and shared dependencies. An
extra object, ACL, dependency, relationship, live session, ambiguous
termination, or incomplete cleanup retains the inactive,
credential-free marker and blocks release. The resulting target graph and
transient wrapper inventory must both be empty before the ACL migration, which
must commit before App activation.

The one-use workspace principal uses an exact 100-byte signed `displayName`.
Its compact prefix reserves one namespace derived from the instance, database,
and target application ID, and its full Ed25519 signature binds the complete
reservation and internal ownership marker. The SCIM `externalId` must remain
unset because live Databricks create and PATCH operations did not persist it.
Every deploy retry recovers both the verifier marker and any existing App
marker under the canonical lock before build or bundle work. A clean first
install must instead prove three stable observations of both the Lakebase
instance and the signed workspace marker being absent. The instance is checked
again after principal inventory and before each workspace mutation.
Absent-instance recovery remains bound to the initially resolved immutable
SCIM ID: if another deploy deletes principal P and creates P2 with the same
reserved namespace, the stale worker cannot follow that namespace onto P2.

The deterministic v3 tombstone is also exactly 100 bytes and is created before
the first credential or SQL role mutation. Its display is `p`, a five-character
target digest, the 22-character encoded original application UUID, the
eight-character encoded immutable numeric SCIM ID, and the 64-character encoded
Ed25519 signature tail. The tombstone's explicit SCIM `applicationId` stores
the signature's first 16 bytes. The signature binds the complete target marker,
original application ID, and original SCIM ID. Recovery reconstructs the full
signature and accepts only the configured current, previous, or historical
proof-key registry.

Deployed v2 markers remain strictly readable but do not contain a principal ID
and therefore cannot authorize application-ID-only account deletion. Before
role cleanup, recovery may upgrade v2 to v3 only when the original immutable
SCIM ID is independently supplied by the exact OAuth security label or a direct
SCIM proof. It publishes and verifies v3 before retiring v2, tolerates only that
finite controlled overlap, and re-discovers a unique v3 marker before role
deletion so no recovery-authority gap exists. When workspace LIST omits an
original principal, the exact OAuth label supplies its immutable SCIM ID and
recovery performs a direct GET before selecting the
absent-principal secret path. The tombstone is account-deleted only after exact secret,
account-principal, role, OID, PID, SQL, and control-plane absence is proven. If
retirement succeeds, a separate bounded 180-second window must then prove the
marker's immutable account/workspace ID remains absent for 30 continuous
seconds; provider latency in retirement cannot consume that final proof window.
If any step is unproven, the remaining signed authority is retained and the
release fails. If the canonical lock is lost during ordinary recovery, no workspace,
account, or provider-role mutation is permitted. The finalizer's only exception
is credential quarantine of the exact immutable principal
created and already verified by that run; it may not discover or mutate a replacement identity.
If the entire Lakebase instance is absent, no SQL inventory or advisory lock is
available. That path uses repeated immutable direct GET absence for 30 seconds
and is strictly read-only. Any bootstrap principal or signed tombstone blocks
recovery and is retained until an existing reviewed instance connection can
prove and remove the corresponding SQL role; production never uses an unlocked
test bypass to mutate it.
When the target database is absent, recovery executes on
`databricks_postgres`, proves SQL and control-plane absence before each role
mutation, and switches to full target recovery if the database reappears.

For an existing Databricks App, deployment reads and pins the immutable App,
client, and SCIM IDs and proves the exact App is stopped before updating its
Lakebase resource binding or converging/replacing its database role. An App
identity mismatch aborts without name-only mutation.
Databricks documents that App Lakebase bindings
reuse a role with that name and that `databricks_create_role()` creates LOGIN-
only OAuth roles:

- https://docs.databricks.com/aws/en/dev-tools/databricks-apps/lakebase
- https://docs.databricks.com/aws/en/oltp/projects/postgres-roles

Before the migration command below, converge both identities at the stopped
deployment boundary. The reviewed agent-runtime identity is the distinct fresh
OAuth-M2M positive control; supply it only to the convergence child:

```bash
export MIP_LAKEBASE_BOOTSTRAP_CONTROL_CLIENT_ID="$DATABRICKS_AGENT_RUNTIME_CLIENT_ID"
export MIP_LAKEBASE_BOOTSTRAP_CONTROL_CLIENT_SECRET="$DATABRICKS_AGENT_RUNTIME_CLIENT_SECRET"
.venv/bin/python -m tools.databricks.converge_lakebase_oauth_role \
  --lakebase-instance "$LAKEBASE_INSTANCE_NAME" \
  --lakebase-database "$LAKEBASE_DATABASE" \
  --application-id "<app-service-principal-client-id>" \
  --role-contract app \
  --app-name "$MIP_APP_NAME" \
  --stop-app-for-mutation \
  --repair-legacy-replication
.venv/bin/python -m tools.databricks.converge_lakebase_oauth_role \
  --lakebase-instance "$LAKEBASE_INSTANCE_NAME" \
  --lakebase-database "$LAKEBASE_DATABASE" \
  --application-id "$MIP_AI_GATEWAY_VERIFIER_CLIENT_ID" \
  --role-contract verifier \
  --repair-legacy-replication
```

Live release verification then authenticates separately as the App and
verifier, proves `current_user`, the exact profile, security label, memberships,
absence of `public.USAGE`/`public.CREATE`, and expected normal query, and
requires a replication-mode `IDENTIFY_SYSTEM`
attempt to be rejected with structured PostgreSQL SQLSTATE `42501` through
pg8000's replication startup path. It creates no replication slot. A successful
replication command is a release blocker regardless of ordinary ACL postflight.

The catalog-driven migration is the only supported path for an externally
managed Lakebase instance. Do not copy a static GRANT list: it cannot safely
enumerate pre-existing schemas, views, foreign tables, overloaded routines,
object owners, table-column ACLs, default ACLs, or recursive role memberships.

Run the same idempotent migration used by the bundle after configuring the
external connection and the two exact OAuth identities:

```bash
export MIP_APP_NAME="<deployed-app-name>"
export MIP_AI_GATEWAY_VERIFIER_CLIENT_ID="<verifier-client-id>"
export LAKEBASE_INSTANCE_NAME="<external-instance-name>"
export LAKEBASE_DATABASE="<external-database-name>"
export MIP_LENDER_NAME="<exact-reviewed-legal-lender-name>"
export MIP_LENDER_NMLS_ID="<exact-reviewed-nmls-id>"
export MIP_TENANT_ID="<reviewed-tenant-slug>"
.venv/bin/python -m jobs.lakebase_migrate \
  --app-name "$MIP_APP_NAME" \
  --lakebase-instance "$LAKEBASE_INSTANCE_NAME" \
  --lakebase-database "$LAKEBASE_DATABASE" \
  --lender-name "$MIP_LENDER_NAME" \
  --lender-nmls-id "$MIP_LENDER_NMLS_ID" \
  --tenant-id "$MIP_TENANT_ID" \
  --ai-gateway-verifier-client-id "$MIP_AI_GATEWAY_VERIFIER_CLIENT_ID" \
  --require-ai-gateway-verifier
```

That command resolves the app's authoritative
`service_principal_client_id` through the Databricks Apps SDK, commits the
fail-safe public-schema quarantine, proves zero old target sessions, then
inventory-reconciles both identities across every non-system schema, table-like
object, sequence, overloaded routine, direct table-column ACL, direct/default
ACL, and recursive role-membership path in a rollback-capable ACL transaction.
The bundle task carries the same verifier client ID as a reviewed bundle
variable and passes the required flag. A missing/template verifier therefore
fails in the job before it opens Lakebase or applies schema SQL; a local shell
export alone is not considered remote-job configuration.
The bundle pins `run_as.user_name` to the resolved deployment identity in both
development and production targets; live release evidence must confirm the
created migration job reports that exact run-as instead of relying on its
historical creator.
It removes direct and PUBLIC column grants before applying the table-level
matrices, then independently rejects any effective
`has_any_column_privilege` capability that is not backed by the corresponding
table privilege. It intentionally revokes PUBLIC `EXECUTE` on deployer-owned
user routines and
removes the built-in future-routine default for every role that can create in a
user schema; immutable `cloud_admin` routines and relations in `public` remain
only behind the closed namespace described above. This Lakebase database is dedicated to MIP
app state. Only
the immutable reviewed validator functions regain app `EXECUTE`; the verifier
receives no routine execution. A missing owner authority, unsafe role
attribute, any direct or recursive parent membership (including inherited
`USAGE`, `SET`, and ADMIN-option paths), unreviewed `SECURITY DEFINER` path, or
postflight mismatch aborts the migration.

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
the code-owned privilege matrix after reviewing its runtime statements; otherwise the
migration postflight rejects the deployment. `schema_migrations`,
`action_audit_archive_runs`, and `agent_sessions` intentionally receive no app
privileges. Postflight also rejects effective database `CREATE`, schema
`CREATE`, any unreviewed table/sequence/routine, unsafe role attributes,
database `TEMPORARY`, every direct or recursive parent role, any direct/PUBLIC
column ACL, and any privilege outside the exact code-owned matrix. The verifier
postflight independently rejects access to every table except
`ai_gateway_proof_ledger`, all sequence and routine access, and any future
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

This is covered by the `sql_warehouse` entry under
`resources.apps.mip_app.resources` in
[`databricks.yml`](../../databricks.yml) (`permission: CAN_USE`) and requires
no extra GRANT. Verify at deploy time:

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
SHOW GRANTS `mip-app` ON SCHEMA mip_app_state.mip_sync;
SHOW GRANTS `mip-app` ON TABLE mip_app_state.mip_sync.source_readiness;
SHOW GRANTS `mip-app` ON TABLE mip_app_state.mip_sync.segment_population;
SHOW GRANTS `mip-app` ON TABLE mip_app_state.mip_sync.funnel_snapshot_daily;
-- Expect no App or effective App-group privilege on either legacy schema:
SHOW GRANTS `mip-app` ON SCHEMA mip_app_state.public;
SHOW GRANTS `mip-app` ON SCHEMA mip_app_state.mip_app;

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
