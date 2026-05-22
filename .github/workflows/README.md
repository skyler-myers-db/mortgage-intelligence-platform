# GitHub Actions — Workflow map + required secrets

This repo runs four workflows:

| File | Trigger | Credentials |
|---|---|---|
| [`ci.yml`](ci.yml) | `pull_request`, `push` to `main` / `feature/*` | **None.** Every job is credential-free. |
| [`nightly.yml`](nightly.yml) | `schedule` (10:00 UTC daily) + `workflow_dispatch` | Required — see below. |
| [`deploy-dev.yml`](deploy-dev.yml) | `workflow_dispatch` | Required — Databricks dev deployment credentials. |
| [`deploy-prod.yml`](deploy-prod.yml) | `workflow_dispatch` | Required — Databricks production deployment credentials. |

The PR workflow (`ci.yml`) is designed to stay green for any contributor
including fork-based PRs: it uses placeholder BUNDLE_VARs and pytest
fixtures that explicitly install in-process repositories where needed.
No real workspace is touched.

The nightly workflow (`nightly.yml`) intentionally talks to the real
dev Databricks workspace so drift between the SQL UDFs + Python
scoring mirrors, Lakebase, Genie, deployed-app auth, and the
degraded-banner proof is caught within 24 hours. It fails loudly:
live jobs check their own required secrets and exit non-zero instead
of silently skipping release gates.

---

## Required repo secrets (nightly only)

All listed below must be set under **Settings → Secrets and variables
→ Actions → Repository secrets**. Missing any one of them aborts the
job that needs it with a clear `::error::` pointing at the offending
var.

| Secret | What it is | How to obtain |
|---|---|---|
| `DATABRICKS_HOST` | Workspace URL (e.g. `https://<id>.cloud.databricks.com`). | Databricks UI → workspace URL in browser address bar. |
| `DATABRICKS_TOKEN` | Personal Access Token with permissions to read `mip.*`, run statements on the dev warehouse, and call admin-gated app drill endpoints. | Databricks UI → User Settings → Developer → Access Tokens → Generate new token. Rotate every 90 days (see `docs/runbook.md` §5). |
| `DATABRICKS_WAREHOUSE_ID` | ID of the dev serverless SQL warehouse. Same value `databricks bundle validate` resolves from `databricks.yml`. | Databricks UI → SQL Warehouses → click the warehouse → copy from URL or Connection Details. |
| `GENIE_SPACE_ID` | Mortgage Lead Intelligence Genie Space ID. | Databricks UI → Genie → open the space → copy ID from URL. Also tracked at `genie/space_id.txt`. |
| `DATABRICKS_CLIENT_ID` | Non-admin service-principal OAuth client ID used by deployed Playwright and the non-admin RBAC smoke. | Provision with `tools/databricks/provision_m2m_oauth.py` or `docs/security/m2m-oauth-setup.md`. |
| `DATABRICKS_CLIENT_SECRET` | Secret for the non-admin OAuth client. Nightly fails if this is absent; it must not fall back to the admin PAT. | Rotate with the same cadence as the workspace token. |

## Optional secrets (nightly, gated features)

| Secret | What it enables | Notes |
|---|---|---|
| `MIP_APP_URL` | Explicit deployed Databricks App URL for live Playwright and real-infra drill jobs. | If unset for Playwright, the workflow resolves `mip-app` through the Databricks Apps API. The opt-in real-infra drill still requires this secret. |
| `LAKEBASE_DATABASE` | Lakebase database the `mip_app` schema lives in. | Defaults to `mip_app_state`; the app and live tests use workspace-identity Lakebase credentials, not static Lakebase password secrets. |

## No-secret jobs (run on every PR)

The PR workflow intentionally has no `secrets:` references. If you add
a job that needs a secret, consider whether that job belongs in
`nightly.yml` instead; PR jobs that fail for fork-based PRs due to
missing secrets create friction with external contributors.

## Triage

- Parity-live red → `docs/runbook.md` §3.
- Missing-secret errors → rotate / re-add per the table above.
- Cold-warehouse timeouts on first run → expected once every ~2 weeks
  depending on workspace activity; the failure issue auto-filed by the
  `notify-on-failure` job links directly to the workflow run.

The auto-filed issue is labelled `ci-failure` + `nightly`; close it
once the next nightly is green.
