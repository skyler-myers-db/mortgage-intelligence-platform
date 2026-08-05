# GitHub Actions — Workflow map + required secrets

This repo runs four workflows:

| File | Trigger | Credentials |
|---|---|---|
| [`ci.yml`](ci.yml) | `pull_request`, `push` to `main` / `feature/*` | **None.** Every job is credential-free. |
| [`nightly.yml`](nightly.yml) | `workflow_dispatch` only | Required — see below. |
| [`deploy-dev.yml`](deploy-dev.yml) | `workflow_dispatch` | Required — Databricks dev deployment credentials. |
| [`deploy-prod.yml`](deploy-prod.yml) | `workflow_dispatch` | **None. Non-deploying scaffold gate only.** |

The PR workflow (`ci.yml`) is designed to stay green for any contributor
including fork-based PRs: it uses placeholder BUNDLE_VARs and pytest
fixtures that explicitly install in-process repositories where needed.
No real workspace is touched.

The live validation workflow (`nightly.yml`, retained under its historical
filename) intentionally talks to the real dev Databricks workspace so drift
between the SQL UDFs + Python scoring mirrors, Lakebase, Genie, deployed-app
auth, and the degraded-banner proof is caught before a release. It is manual
only because the refresh jobs, Genie prompts, and browser matrix burn real
customer/workspace compute. It fails loudly: live jobs check their own required
secrets and exit non-zero instead of silently skipping release gates.

The dev deploy workflow (`deploy-dev.yml`) is also manual-only. This repository
currently has one maintainer, so its required environment review cannot provide
separation of duties. Every dispatch must explicitly set
`acknowledge_single_maintainer_break_glass=true`, and the same maintainer must
approve the protected `dev` environment while recording the governing PR or
issue in the approval comment. This is an audited self-approval exception, not
independent review. The environment must restrict deployment to the reviewed
branch and forbid administrator bypass. The original workflow run stays bound
to the `workflow_dispatch` SHA; it is not re-dispatched through a moving branch
reference. After approval, that run builds the reviewed commit, creates an
ephemeral `.env.local` containing only non-secret app configuration, passes
runtime secrets to the provisioning step through the process environment,
seeds a temporary `DEFAULT` Databricks CLI profile, runs
`scripts/deploy.sh -t dev --no-confirm`, and keeps the deployed app smoke test
enabled unless the operator explicitly selects `skip_smoke`. Run it before live
validation when the code under review changes app, bundle, job, SQL, or frontend
behavior. The workflow shares a single non-cancelling concurrency lane
(`mip-dev-live-state`) with live validation because
parallel deploys overlap expensive refresh jobs and app promotion.

The live mutation gate encodes the GitHub run and attempt into a reviewed,
non-PII campaign name. It removes abandoned marked fixtures before pytest and
uses a fresh operator/admin OAuth pair in an `if: always()` postflight to
archive the current run and prove three active-inventory absences. An
interrupted build remains immutable until its five-minute lease expires; only
then may the admin transition atomically quarantine it as `failed` and
`archived` through the public campaign API.

The `rebase_unverified_app` input is a one-time adoption control, defaulting to
false. Select it only when the existing dev App predates the signed server-owned
rollback record; that run stops the unverified App and must prove/capture a new
green contract before service resumes. Routine roll-forwards leave it false.

The `repair_normal_credential` input is a bounded recovery control, also
defaulting to false. Use it only when `DATABRICKS_CLIENT_SECRET` is absent or
known invalid. Before dispatch, temporarily set the `dev` environment secret
`MIP_GITHUB_CREDENTIAL_SINK_TOKEN` to a single-repository, short-expiry
fine-grained token with only Actions Secrets write access. The `dev` environment
must require review, forbid administrator bypass, and restrict deployment to the
reviewed recovery branch. Because this repository currently has no second
maintainer, dispatch and environment approval must follow the explicit audited
self-approval exception above. The recovery run does not deploy or change App
access: it acquires the signed App lease, creates one replacement
normal-operator credential, confirms the GitHub sink repeatedly, and retires
the prior credential. Revoke the temporary token at GitHub immediately after
the recovery run succeeds (deleting only its environment-secret copy is not
sufficient), then dispatch a normal deploy with
`repair_normal_credential=false`.

Despite its legacy filename, `deploy-prod.yml` does not deploy or authenticate
to a production workspace. It is an explicit scaffold-only placeholder. The
only implemented mutable workflow is the governed dev deployment above;
production requires a separately reviewed environment contract before a real
workflow may call `scripts/deploy.sh -t prod`.

---

## Required repo secrets (dev deploy and live validation)

All listed below must be set under **Settings → Secrets and variables
→ Actions → Repository secrets**. Missing any one of them aborts the
job that needs it with a clear `::error::` pointing at the offending
var.

| Secret | What it is | How to obtain |
|---|---|---|
| `DATABRICKS_HOST` | Workspace URL (e.g. `https://<id>.cloud.databricks.com`). | Databricks UI → workspace URL in browser address bar. |
| `DATABRICKS_TOKEN` | Workspace-admin Personal Access Token used as the principal-pinned full-inventory/deployment authority. It is not reused as an app bearer or treated as the product app-admin identity. | Databricks UI → User Settings → Developer → Access Tokens → Generate new token. Confirm `current-user me` contains the `admins` group; rotate every 90 days (see `docs/runbook.md` §5). |
| `DATABRICKS_WAREHOUSE_ID` | ID of the dev serverless SQL warehouse. Same value `databricks bundle validate` resolves from `databricks.yml`. | Databricks UI → SQL Warehouses → click the warehouse → copy from URL or Connection Details. |
| `GENIE_SPACE_ID` | Mortgage Lead Intelligence Genie Space ID. | Databricks UI → Genie → open the space → copy ID from URL. Also tracked at `genie/space_id.txt`. |
| `DATABRICKS_CLIENT_ID` | Non-admin service-principal OAuth client ID used by deployed Playwright and the non-admin RBAC smoke. | Provision with `tools/databricks/provision_m2m_oauth.py` or `docs/security/m2m-oauth-setup.md`. |
| `DATABRICKS_ACCOUNT_ID` | Databricks account UUID used by deploy identity proof and the verifier's account-admin denial probe. | Copy from the Databricks account console; this is an identifier, not a credential. |
| `DATABRICKS_ACCOUNT_HOST` | Databricks account-console host for exact SCIM identity proof and the verifier's read-only account-admin denial probe. | Optional on AWS (`https://accounts.cloud.databricks.com` is the workflow default); set explicitly for another cloud. |
| `DATABRICKS_ACCOUNT_CLIENT_ID` | Dedicated account-SCIM OAuth client used for exact identity/role inspection and bounded owner exclusion. | Must retain account-admin authority and differ from every app-facing M2M and target App. Account-admin inventory is required on every deploy because accessible UC owners can legitimately exist at account scope without MIP workspace assignment; Service Principal Manager alone is insufficient. Also grant Service Principal Manager on every forbidden normal, operator2, admin, verifier, agent-runtime, and target App so five-minute target credentials can be created and revoked. Keep this identity deploy/verifier-only. Deployment fails closed if credentials or proof are absent or inconclusive. |
| `DATABRICKS_ACCOUNT_CLIENT_SECRET` | Secret for the dedicated account-SCIM OAuth client. | Used only by bounded identity/role verification and the group-owner target-identity probes; workspace PAT credentials are never reused. |
| `DATABRICKS_CLIENT_SECRET` | Secret for the non-admin OAuth client. Live validation fails if this is absent; it must not fall back to the admin PAT. | Rotate with the same cadence as the workspace token. |
| `DATABRICKS_OPERATOR2_CLIENT_ID` | Dedicated second non-admin operator client ID for the live per-actor recovery/isolation proof. | Must differ from the normal, admin, and verifier client IDs. Provision with `--identity-role operator2`. |
| `DATABRICKS_OPERATOR2_CLIENT_SECRET` | Secret for the second non-admin operator OAuth client. | Used only to mint the short-lived `MIP_OPERATOR2_BEARER_TOKEN` in the on-demand live gate. |
| `DATABRICKS_ADMIN_CLIENT_ID` | Dedicated app-admin service-principal OAuth client ID. | Must differ from the normal and verifier client IDs. |
| `DATABRICKS_ADMIN_CLIENT_SECRET` | Secret for the dedicated app-admin OAuth client. | The workflow mints a fresh `MIP_ADMIN_BEARER_TOKEN`; no bearer is stored as a repository secret. |
| `DATABRICKS_VERIFIER_CLIENT_ID` | Dedicated AI Gateway verifier service-principal OAuth client ID. | Must have no App permission or admin membership. |
| `DATABRICKS_VERIFIER_CLIENT_SECRET` | Secret for the dedicated AI Gateway verifier OAuth client. | Used only for exact inference-row and verifier-identity proof. |
| `DATABRICKS_AGENT_RUNTIME_CLIENT_ID` | Dedicated runtime-owner service-principal application ID. | Owns only the reviewed Supervisor, Gateway, model, and per-runtime MLflow experiment; it must have no App, Lakebase, or warehouse access. |
| `DATABRICKS_AGENT_RUNTIME_CLIENT_SECRET` | Secret for the isolated runtime-owner OAuth client. | Exposed only to allowlisted provisioning/verification subprocesses and never to the App. |
| `DATABRICKS_AGENT_PROXY_CREDENTIAL_BUNDLE` | Canonical JSON containing the managed-Supervisor caller client ID, immutable credential ID, and one-shot client secret. | This is the sole GitHub source of truth and is written atomically at mint time. Live deploy and nightly jobs derive the public IDs from this bundle before writing the credential-versioned Databricks secret or verifying the runtime contract. |
| `MIP_AI_GATEWAY_PROOF_SIGNING_KEY` | Ed25519 private signing key for exact inference-row, App rollback, and destructive cutover-journal attestations. | Store only in deploy/verifier automation. The App and agent runtime receive only the derived public key; neither receives this private key. |
| `MIP_AI_GATEWAY_PROOF_PREVIOUS_VERIFY_KEY` | Optional prior proof/rollback Ed25519 public key during a bounded rotation. | Remove after every signed App and exact-proof record has converged to the current key. Never store a prior private key. |
| `MIP_GATEWAY_MODEL_ATTESTATION_SIGNING_KEY` | Independent Ed25519 private key for immutable proxy-model registration attestations. | Must differ from `MIP_AI_GATEWAY_PROOF_SIGNING_KEY`. Expose it only to the bounded runtime-owner model-registration command. |
| `MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY` | Public Ed25519 verification key derived from the model-attestation signing key. | Store as a GitHub Actions variable; nightly read-only jobs must never receive the private signing key. |
| `MIP_GATEWAY_MODEL_ATTESTATION_PREVIOUS_VERIFY_KEY` | Optional prior model-attestation public key during a bounded model-key rotation. | Keep it while any retained non-candidate model remains signed by that epoch. Deploy allocates a distinct current-key green family and never rewrites retained model tags. |

The Gateway endpoint, Agent Model version, managed Supervisor identity, and
inference table are not repository secrets. Live validation discovers them
from the source-owned resource names and verifies their immutable runtime
contract before it mutates data or spends browser/evaluation compute. Remove
any historical `MIP_AI_GATEWAY_ENDPOINT` or
`MIP_AI_GATEWAY_INFERENCE_TABLE` repository secrets; the workflow deliberately
does not consume them.

## Required repo or environment variables (dev deploy)

At least one of these non-secret variables must be set under **Settings →
Secrets and variables → Actions → Variables** before `deploy-dev.yml` runs. The
workflow fails closed if both are empty, so dev deploys do not implicitly grant
app-admin access to the Databricks PAT owner.

| Variable | What it is | Notes |
|---|---|---|
| `MIP_ADMIN_EMAILS` | Comma-separated app-admin email allowlist for the dev app. | Use when a named operator must access `/api/v1/admin/*`. |
| `MIP_ADMIN_GROUP_NAME` | Optional local/test compatibility group name. | Deployed automation uses the exact `MIP_ADMIN_IDENTITIES` value derived from `DATABRICKS_ADMIN_CLIENT_ID`; group headers are not authoritative in sandbox/production. |
| `MIP_DEFAULT_CATALOG` | Target Unity Catalog name. | Defaults to `mip`; deploy and every nightly proof/render step use the same value. |
| `MIP_APP_NAME` | Bundle-managed Databricks App name. | Defaults to `mip-app`; use a unique DNS-style value for every isolated staging/customer workspace. |
| `MIP_LAKEBASE_INSTANCE` | Bundle-managed Lakebase instance name. | Defaults to `mip-app-state`; it must match `LAKEBASE_INSTANCE_NAME` if that compatibility alias is also set. |
| `MIP_LAKEBASE_SYNC_CATALOG` | Bundle-managed Lakebase synced-table catalog. | Defaults to `mip_app_state`; use a unique Unity Catalog identifier outside the established dev installation. |
| `LAKEBASE_DATABASE` | Database inside the Lakebase instance that owns `mip_app`. | Defaults to `mip_app_state`; it must match `MIP_LAKEBASE_DATABASE_NAME` if that compatibility alias is also set. |
| `MIP_GENIE_SPACE_NAME` | Human-readable governed Genie space title. | Defaults to `Mortgage Lead Intelligence`; use an environment-qualified title when several spaces share an account. |
| `MIP_RUNTIME_SECRET_SCOPE` | Databricks secret scope for App runtime bindings. | Defaults to `mip-runtime`; isolate it per deployment. |
| `MIP_APP_ROLLBACK_SECRET_SCOPE` | Databricks secret scope for signed last-good App state. | Defaults to `mip-app-rollback`; isolate it per deployment. |
| `MIP_REVIEWED_FUNCTION_OWNER` | Exact owner returned unanimously by the live reviewed UC function inventory. | Set only from the deployed catalog after the deployer's authenticated owner capture succeeds. Nightly pins this value and rejects owner drift. |
| `MIP_LIFECYCLE_REPLAY_REVIEW_SHA256` | SHA-256 of the committed canonical lifecycle replay MERGE sequence. | Regenerate `docs/validation/lifecycle-replay-sql-2026-07-29.json` with `python -m tests.integration.test_lifecycle_delta_replay_live --catalog <catalog> --out <artifact>` and update this variable only after independent governance review. Before any Delta DDL, the live test validates the artifact's self-digest and requires both runtime SQL and this variable to equal that committed review. |

Optional non-secret Gateway family variables are `MIP_AI_GATEWAY_AGENT_MODEL_FAMILY`
(default `<catalog>.audit.mortgage_growth_supervisor_proxy`),
`MIP_AI_GATEWAY_AGENT_EXPERIMENT_BASE` (default
`mip-agent-runtime-gateway-proxy`), and `MIP_AI_GATEWAY_TABLE_PREFIX` (default
`mip_agent_gateway_growth_agent`). The experiment value is a family name, not
a `/Shared` path. Provisioning derives the runtime-owned `/Users/<runtime-id>/...`
experiment and contract-hashed endpoint, model, and table-family names. Do not
store those generated names or their binding/resource digests as secrets; the
deploy and nightly jobs export and verify them from live immutable resources.

## Optional secrets (live validation, gated features)

| Secret | What it enables | Notes |
|---|---|---|
| `MIP_APP_URL` | Explicit deployed Databricks App URL for live Playwright and real-infra drill jobs. | If unset for Playwright, the workflow resolves `MIP_APP_NAME` through the Databricks Apps API. The opt-in real-infra drill still requires this secret. |

`nightly.yml` still reads the historical `LAKEBASE_DATABASE` secret as a
temporary compatibility fallback, but new installations should use the
non-secret repository/environment variable above. The app and live tests use
workspace-identity Lakebase credentials, not static database passwords.

## No-secret jobs (run on every PR)

The PR workflow intentionally has no `secrets:` references. If you add
a job that needs a secret, consider whether that job belongs in
manual live validation instead; PR jobs that fail for fork-based PRs due to
missing secrets create friction with external contributors.

## Triage

- Parity-live red → `docs/runbook.md` §3.
- Missing-secret errors → rotate / re-add per the table above.
- Cold-warehouse timeouts on first run → expected once every ~2 weeks
  depending on workspace activity; the failure issue auto-filed by the
  `notify-on-failure` job links directly to the workflow run.

The auto-filed issue is labelled `ci-failure` + `live-validation`; close it
once the next manual live validation run is green.
