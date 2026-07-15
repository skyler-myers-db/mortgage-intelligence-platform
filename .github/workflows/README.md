# GitHub Actions — Workflow map + required secrets

This repo runs four workflows:

| File | Trigger | Credentials |
|---|---|---|
| [`ci.yml`](ci.yml) | `pull_request`, `push` to `main` / `feature/*` | **None.** Every job is credential-free. |
| [`nightly.yml`](nightly.yml) | `workflow_dispatch` only | Required — see below. |
| [`deploy-dev.yml`](deploy-dev.yml) | `workflow_dispatch` | Required — Databricks dev deployment credentials. |
| [`deploy-prod.yml`](deploy-prod.yml) | `workflow_dispatch` | Required — Databricks production deployment credentials. |

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

The dev deploy workflow (`deploy-dev.yml`) is also manual-only. It builds the
current ref, creates an ephemeral `.env.local` containing only non-secret app
configuration, passes runtime secrets to the provisioning step through the
process environment, seeds a temporary `DEFAULT` Databricks CLI profile, runs `scripts/deploy.sh -t dev
--no-confirm`, and keeps the deployed app smoke test enabled unless the
operator explicitly selects `skip_smoke`. Run it before live validation when
the code under review changes app, bundle, job, SQL, or frontend behavior. The
workflow has a single non-cancelling concurrency lane (`mip-dev-deploy`) because
parallel deploys overlap expensive refresh jobs and app promotion.

---

## Required repo secrets (dev deploy and live validation)

All listed below must be set under **Settings → Secrets and variables
→ Actions → Repository secrets**. Missing any one of them aborts the
job that needs it with a clear `::error::` pointing at the offending
var.

| Secret | What it is | How to obtain |
|---|---|---|
| `DATABRICKS_HOST` | Workspace URL (e.g. `https://<id>.cloud.databricks.com`). | Databricks UI → workspace URL in browser address bar. |
| `DATABRICKS_TOKEN` | Personal Access Token with permissions to read `mip.*` and run statements on the dev warehouse. It is not treated as an app-admin token. | Databricks UI → User Settings → Developer → Access Tokens → Generate new token. Rotate every 90 days (see `docs/runbook.md` §5). |
| `DATABRICKS_WAREHOUSE_ID` | ID of the dev serverless SQL warehouse. Same value `databricks bundle validate` resolves from `databricks.yml`. | Databricks UI → SQL Warehouses → click the warehouse → copy from URL or Connection Details. |
| `GENIE_SPACE_ID` | Mortgage Lead Intelligence Genie Space ID. | Databricks UI → Genie → open the space → copy ID from URL. Also tracked at `genie/space_id.txt`. |
| `DATABRICKS_CLIENT_ID` | Non-admin service-principal OAuth client ID used by deployed Playwright and the non-admin RBAC smoke. | Provision with `tools/databricks/provision_m2m_oauth.py` or `docs/security/m2m-oauth-setup.md`. |
| `DATABRICKS_ACCOUNT_ID` | Databricks account UUID used by the manual live gate to prove the verifier cannot enumerate account service principals. | Copy from the Databricks account console; this is an identifier, not a credential. |
| `DATABRICKS_ACCOUNT_HOST` | Databricks account-console host for the verifier's read-only account-admin denial probe. | Optional on AWS (`https://accounts.cloud.databricks.com` is the workflow default); set explicitly for another cloud. |
| `DATABRICKS_CLIENT_SECRET` | Secret for the non-admin OAuth client. Live validation fails if this is absent; it must not fall back to the admin PAT. | Rotate with the same cadence as the workspace token. |
| `DATABRICKS_OPERATOR2_CLIENT_ID` | Dedicated second non-admin operator client ID for the live per-actor recovery/isolation proof. | Must differ from the normal, admin, and verifier client IDs. Provision with `--identity-role operator2`. |
| `DATABRICKS_OPERATOR2_CLIENT_SECRET` | Secret for the second non-admin operator OAuth client. | Used only to mint the short-lived `MIP_OPERATOR2_BEARER_TOKEN` in the on-demand live gate. |
| `DATABRICKS_ADMIN_CLIENT_ID` | Dedicated app-admin service-principal OAuth client ID. | Must differ from the normal and verifier client IDs. |
| `DATABRICKS_ADMIN_CLIENT_SECRET` | Secret for the dedicated app-admin OAuth client. | The workflow mints a fresh `MIP_ADMIN_BEARER_TOKEN`; no bearer is stored as a repository secret. |
| `DATABRICKS_VERIFIER_CLIENT_ID` | Dedicated AI Gateway verifier service-principal OAuth client ID. | Must have no App permission or admin membership. |
| `DATABRICKS_VERIFIER_CLIENT_SECRET` | Secret for the dedicated AI Gateway verifier OAuth client. | Used only for exact inference-row and verifier-identity proof. |
| `MIP_AI_GATEWAY_PROOF_SIGNING_KEY` | Ed25519 private signing key for exact Gateway proof attestations. | Store only in deploy/nightly automation; the App receives only the derived public verification key. |

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

## Optional secrets (live validation, gated features)

| Secret | What it enables | Notes |
|---|---|---|
| `MIP_APP_URL` | Explicit deployed Databricks App URL for live Playwright and real-infra drill jobs. | If unset for Playwright, the workflow resolves `mip-app` through the Databricks Apps API. The opt-in real-infra drill still requires this secret. |
| `LAKEBASE_DATABASE` | Lakebase database the `mip_app` schema lives in. | Defaults to `mip_app_state`; the app and live tests use workspace-identity Lakebase credentials, not static Lakebase password secrets. |

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
