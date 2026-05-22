# GitHub Actions — Workflow map + required secrets

This repo runs two workflows:

| File | Trigger | Credentials |
|---|---|---|
| [`ci.yml`](ci.yml) | `pull_request`, `push` to `main` / `feature/*` | **None.** Every job is credential-free. |
| [`nightly.yml`](nightly.yml) | `schedule` (10:00 UTC daily) + `workflow_dispatch` | Required — see below. |

The PR workflow (`ci.yml`) is designed to stay green for any contributor
including fork-based PRs: it uses placeholder BUNDLE_VARs and pytest
fixtures that explicitly install in-process repositories where needed.
No real workspace is touched.

The nightly workflow (`nightly.yml`) intentionally talks to the real
dev Databricks workspace so drift between the SQL UDFs + Python
scoring mirrors, Lakebase, and Genie is caught within 24 hours. It
fails loudly — pytest tests that would normally SKIP on missing creds
are gated behind a `fail-fast` step that exits non-zero if any
secret is empty.

---

## Required repo secrets (nightly only)

All listed below must be set under **Settings → Secrets and variables
→ Actions → Repository secrets**. Missing any one of them causes the
`parity-live` job's `Fail fast if required secrets are missing` step
to abort with a clear `::error::` pointing at the offending var.

| Secret | What it is | How to obtain |
|---|---|---|
| `DATABRICKS_HOST` | Workspace URL (e.g. `https://<id>.cloud.databricks.com`). | Databricks UI → workspace URL in browser address bar. |
| `DATABRICKS_TOKEN` | Personal Access Token with permissions to read `mip.*`, run statements on the dev warehouse, and call admin-gated app drill endpoints. | Databricks UI → User Settings → Developer → Access Tokens → Generate new token. Rotate every 90 days (see `docs/runbook.md` §5). |
| `DATABRICKS_WAREHOUSE_ID` | ID of the dev serverless SQL warehouse. Same value `databricks bundle validate` resolves from `databricks.yml`. | Databricks UI → SQL Warehouses → click the warehouse → copy from URL or Connection Details. |
| `GENIE_SPACE_ID` | Mortgage Lead Intelligence Genie Space ID. | Databricks UI → Genie → open the space → copy ID from URL. Also tracked at `genie/space_id.txt`. |
| `DATABRICKS_CLIENT_ID` | Service-principal client ID used by the live Playwright app smoke. | Databricks service principal configured for the dev app workspace. |
| `DATABRICKS_CLIENT_SECRET` | Service-principal client secret used by the live Playwright app smoke. | Databricks service principal secret. Rotate with the same cadence as the workspace token. |
| `MIP_APP_URL` | Deployed Databricks App URL for the live Playwright spec (for example `https://mip-dev.databricksapps.com`). | `databricks apps get <app-name> --profile DEFAULT -o json` or the Databricks Apps UI. |
| `LAKEBASE_HOST` | Lakebase Postgres endpoint hostname. | Databricks UI → Lakebase instance → Connection Details → Host. |
| `LAKEBASE_USER` | Lakebase Postgres user. | Same Connection Details panel. |
| `LAKEBASE_PASSWORD` | Lakebase Postgres password (or workspace identity token if using identity auth). | Same Connection Details panel. |
| `LAKEBASE_DATABASE` | Lakebase database the `mip_app` schema lives in. | Defaults to `mip_app_state`; confirm in bundle outputs. |

## Optional secrets (nightly, gated features)

| Secret | What it enables | Notes |
|---|---|---|
| `MIP_API_URL` | API origin if different from `MIP_APP_URL` (e.g. you run the backend on a different subdomain). | If unset, derived from `MIP_APP_URL` by swapping `:5173`→`:8000`. |

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
