---
name: deploy-mechanics
description: Databricks app deploy races (stopped app, bundle-triggered deployment) are handled by wait_for_app_deployable() in scripts/deploy.sh; bundle path works, old apps-deploy workaround is obsolete
metadata:
  type: project
---

`databricks bundle deploy -t dev` itself triggers an app deployment (the app
is a bundle resource), so an immediate explicit `databricks apps deploy`
afterwards races it ("active/pending deployment in progress"). The app can
also be auto-STOPPED, which fails snapshot deploys outright.

**Why:** both failure modes were hit live on 2026-06-11 during the perf/polish
slice; fixed in `scripts/deploy.sh` via `wait_for_app_deployable()` (starts a
stopped app, polls pending/active deployment state up to 15 min before
promoting the snapshot).

**How to apply:** never re-introduce manual `databricks apps deploy
--source-code-path` workarounds — the historical bundle-deploy 403 was a
placeholder `genie_space_id` (root-caused per the tracker) and the bundle
path now works. If a deploy fails at the snapshot step, check
`databricks apps get mip-app -o json` for `compute_status.state` and
`pending_deployment`/`active_deployment` before assuming a packaging bug.
Also note: piping deploy output to `tail` without `set -o pipefail` masks
the script's non-zero exit. Deployed sandbox keeps `MIP_RUM_ENABLED=false`
(RUM validation on, ingestion off) by design — that's now the CODE default
too (`mip_rum_enabled: bool = False`).

**Admin allowlist (2026-06-11 incident):** `admin_emails` defaults to ""
in source (security sweep removed the personal email), deployment env_vars
fully replace prior env, and the deploy payload deliberately does NOT
bootstrap the deployer into admin (pinned by
`tests/unit/test_app_deploy_payload.py::test_payload_does_not_bootstrap_current_user_admin`
— do not "fix" by auto-granting). A deploy without `MIP_ADMIN_EMAILS`
(env or .env.local) therefore 403s every admin surface INCLUDING the
admin-gated audit feed (`/audit/events` has `AdminDep`), which the UI
renders as "Audit feed is briefly unavailable" — easily mistaken for a
Lakebase outage. Remediation: export `MIP_ADMIN_EMAILS=<operator>` for the
deploy (or put it in .env.local); deploy.sh preflight now warns when the
allowlist resolves empty; smoke_live.sh admin probe is posture-aware
(403 = deny path proven, 200 = configured admin, falls through to payload
contract checks).
