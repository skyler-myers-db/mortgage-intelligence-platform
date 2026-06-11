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
(RUM validation on, ingestion off) by design.
