> **Internal implementation artifact. Not approved for public release.**

# Production deploy dry-run — 2026-04-22

**Target:** `prod` (mode: production)
**Command run:** `databricks bundle validate -t prod`
**Operator:** skyler@entrada.ai
**Verdict:** **BLOCKED** — one validation error + two dev-default footguns that will misfire in prod.

## Summary

Running `databricks bundle validate -t prod` against the current `main` fails with:

```
Error: target with 'mode: production' cannot include a pipeline with 'development: true'
```

This is a single-line bundle-authoring bug that must be fixed before the first customer deploy. A second class of finding — jobs with hardcoded `pause_status: UNPAUSED` — does not block `validate` but does produce wrong behavior in *dev* (the jobs run hourly/weekly even when `mode: development` is supposed to auto-pause them) and is therefore already mis-modelled for prod's strict inverse. Both are packaging bugs per the CLAUDE.md rule "manual click-ops in the Databricks UI are a packaging bug."

`dev` target: `Validation OK!`
`ci` target: `Validation OK!`
`prod` target: **1 error**.

## Blockers (must fix before prod deploy)

### B1. Lakeflow pipeline hardcodes `development: true`

`databricks.yml:502-504` declares:

```yaml
pipelines:
  mip_feature_pipeline:
    ...
    photon: true
    development: true          # <-- blocks prod validate
    continuous: false
```

Databricks bundle validator refuses to include a `development: true` DLT pipeline under a `mode: production` target. The fix is to remove the hardcoded `development: true` and let the mode-driven defaults handle it (dev → development; prod → not-development), or substitute `${bundle.target == "dev"}` templating. Removing the line entirely is the smallest viable fix — DAB's `mode: development` preset already sets pipelines to development mode, and `mode: production` sets them to production mode.

**Fix location:** `databricks.yml:503`. Drop the `development: true` key. `continuous: false` is fine to keep (it's a genuine config, not a dev/prod toggle).

### B2. `mip_fred_rates_ingest` hardcodes `pause_status: UNPAUSED` — wrong in both targets

`databricks.yml:452-455`:

```yaml
schedule:
  quartz_cron_expression: "0 0 6 ? * FRI *"
  timezone_id: America/Chicago
  pause_status: UNPAUSED       # <-- fires hourly in dev against empty workspace
```

Confirmed via `databricks bundle validate -t dev -o json`:

```
mip_fred_rates_ingest: schedule={'pause_status': 'UNPAUSED', ...}
```

This is the exact antipattern the author of `mip_sync_lifecycle_state` warned about at `databricks.yml:354-357` ("Do NOT hardcode `pause_status: UNPAUSED` here — that override also fires in dev and runs the job hourly against an empty Lakebase, which was observed and fixed 2026-04-22"). The sibling job correctly omits `pause_status` and relies on `mode: development` / `mode: production` to pause/unpause. The FRED job does not follow the same rule.

**Fix location:** `databricks.yml:455` and the sibling mirror at `resources/jobs.yml:171`. Drop `pause_status: UNPAUSED`. Rely on target mode.

Impact in prod: neutral (prod wants UNPAUSED anyway, and the mode default gives us that). Impact in dev: FRED job fires every Friday 06:00 CT against the workspace regardless of whether an operator is actively developing — this is exactly the compute-burn regression the lifecycle comment describes.

### B3. `prod` is the same workspace as `dev` — no isolation boundary

`databricks.yml:32-36`:

```yaml
prod:
  mode: production
  workspace:
    host: https://dbc-3aa503a9-4fa8.cloud.databricks.com   # same host as dev
```

`var.uc_catalog` defaults to `mip` in both targets; no per-target override. That means a `databricks bundle deploy -t prod` would write to the *same* Unity Catalog objects and the *same* Lakebase instance that the dev target writes to. The only separation is the bundle root path (`/Workspace/Users/<user>/...` under prod, `~/.bundle/...` under dev) — which isolates the job + pipeline definitions, but not the data they read and write.

In CLAUDE.md terms this is a "polished enterprise product" posture violation: a customer deploy of `prod` from this bundle would overlay onto the Entrada dev workspace's `mip` catalog. The implementation plan (Phase 7) even claims a production Genie space and larger warehouse, neither of which are declared per-target.

**Fix (not authored here — this is a customer-provisioning decision):**

- Decide whether prod is a *separate workspace host* (preferred) or a *separate catalog in the same workspace* (`mip_prod`). Either way requires a target-level variable override.
- Add `targets.prod.variables.uc_catalog: mip_prod` (or keep `mip` if prod = separate workspace).
- Either add a `targets.prod.workspace.host` that's the customer's prod workspace URL, or document in `.env.local` template that prod overrides come from customer env vars.

This is flagged as blocker because "ready to deploy with `databricks bundle deploy -t prod`" cannot be true if running that command would overwrite dev data. The main agent should triage whether to fix in-bundle now or defer to the customer SE provisioning checklist.

## Findings — non-blocking but tracking

### F1. Warehouse size: 2X-Small in both targets

`databricks.yml:121`:

```yaml
sql_warehouses:
  mip_serverless_sql:
    cluster_size: 2X-Small
    auto_stop_mins: 15
```

Phase 7 of `docs/implementation-plan.md:194` claims "Prod target: same bundle, larger warehouse, production Genie space" — but the bundle does not differentiate warehouse size by target. 2X-Small is the smallest serverless tier and is fine for Module 0 read volumes as described; however, the implementation-plan prose is aspirational and the bundle does not implement it. Either:
(a) update the bundle to override `cluster_size: Medium` (or equivalent) under `targets.prod.resources.sql_warehouses.mip_serverless_sql.cluster_size`, or
(b) update `docs/implementation-plan.md:194` to match reality ("same bundle, same warehouse size — dev and prod differ only in mode flags and schedules").

Recommendation: leave the warehouse at 2X-Small (Module 0 traffic fits) and update the doc. Larger warehouse is a Phase-2 concern after real customer load.

### F2. Genie space shared, not per-target

`databricks.yml:573`: `var.genie_space_id` has a single default, no per-target override. The implementation plan says "production Genie space" but the bundle uses one Genie space across both targets. Genie space provisioning is documented as out-of-bundle (`databricks.yml:541-542`, `tools/databricks/provision_genie_space.py`).

Current shape is acceptable — Genie space YAML at `genie/mortgage_lead_intelligence_space.yml` is the source of truth, `provision-genie` Make target re-applies it. Prod only needs a different `GENIE_SPACE_ID` in `.env.local`. Document that in a prod-deploy operator runbook.

### F3. Lakebase instance: `mip-app-state` name is workspace-scoped, not target-scoped

`databricks.yml:67`: `name: mip-app-state`. Lakebase instance names are workspace-unique. If `prod` ever points at a separate workspace (the B3 path), this is fine; if prod stays in the same workspace as dev, this is another manifestation of B3 — prod deploy would attach the app to the same Lakebase instance that dev uses. CU_1 tier is the smallest (fine for read-heavy audit traffic; approval write volume is well below threshold per the stress-test design).

### F4. Dashboards declared but `resources/dashboards.yml` is not included

`resources/dashboards.yml` declares `executive_dashboard` + `segment_dashboard` with `file_path: ../dashboards/executive_dashboard.lvdash.json`. `databricks.yml` does not have `include: [resources/*.yml]`. The canonical dashboard declarations in `databricks.yml:519-527` (`mip_executive_dashboard`, `mip_segment_dashboard`) ARE the ones that ship. `resources/dashboards.yml` is a stub mirror (same pattern as `resources/jobs.yml` — the file has self-aware comments calling itself a mirror). Not a bug, but someone editing `resources/dashboards.yml` thinking it's live will be surprised. Consider deleting the unused mirrors or wiring the `include:` block.

### F5. `resources/apps.yml` declares a second `mip_module0_app` that is not included

Same pattern as F4. `databricks.yml` declares `mip_app`; `resources/apps.yml` declares `mip_module0_app`. Only the former ships. The stub-mirror comments in `resources/jobs.yml` are not replicated in `resources/apps.yml` and there is no clear signal to a reader that the file is inert.

### F6. Genie space is still provisioned out-of-bundle

`databricks.yml:541-542` says so explicitly. This is an exception to CLAUDE.md's "zero-click" rule; the `deploy-dev` script in `scripts/deploy.sh` chains `provision-genie` after `bundle deploy`, so the operator experience is still one command. That's acceptable — the point of the rule is "customer SE runs one command" and `scripts/deploy.sh` is that one command. The rule is *not* satisfied if a customer runs only `databricks bundle deploy -t prod`; the talk track + README should point to `./scripts/deploy.sh` or an equivalent prod variant.

Recommendation: add a `deploy-prod` target to the Makefile that wraps `scripts/deploy.sh` with a `--target prod` flag, and document it as the prod deploy command of record. Currently Makefile only has `deploy-dev`.

## Resource checklist — dev vs prod as-declared

| Resource | Kind | Dev name | Prod name | Schedule (dev) | Schedule (prod) | Diff? |
|---|---|---|---|---|---|---|
| `mip_app` | app | `mip-app` | `mip-app` | — | — | none |
| `mip_feature_pipeline` | pipeline | `[dev skyler] mip_feature_pipeline` | (fails: dev=true) | — | — | **B1** |
| `mip_serverless_sql` | warehouse | `[dev skyler] mip_serverless_sql`, 2X-Small | `mip_serverless_sql`, 2X-Small | — | — | F1 |
| `mip_fred_rates_ingest` | job | `[dev skyler] mip_fred_rates_ingest` | `mip_fred_rates_ingest` | UNPAUSED (wrong) | UNPAUSED (intended) | **B2** |
| `mip_lakebase_migrate` | job | `[dev skyler] mip_lakebase_migrate` | `mip_lakebase_migrate` | manual | manual | none |
| `mip_ref_seed` | job | `[dev skyler] mip_ref_seed` | `mip_ref_seed` | manual | manual | none |
| `mip_refresh_scores` | job | `[dev skyler] mip_refresh_scores` | `mip_refresh_scores` | manual | manual | none |
| `mip_refresh_silver` | job | `[dev skyler] mip_refresh_silver` | `mip_refresh_silver` | manual | manual | none |
| `mip_snapshot_dashboards` | job | `[dev skyler] mip_snapshot_dashboards` | `mip_snapshot_dashboards` | manual | manual | tasks list is empty (`tasks: []`) — does this job need to exist? |
| `mip_sync_lifecycle_state` | job | `[dev skyler] mip_sync_lifecycle_state` | `mip_sync_lifecycle_state` | **PAUSED** (correct dev default) | UNPAUSED (mode auto) | correct |
| `mip_app_state` | lakebase | `mip-app-state`, CU_1 | `mip-app-state`, CU_1 | — | — | F3 |
| `mip_executive_dashboard` | dashboard | bundled | bundled | — | — | none |
| `mip_segment_dashboard` | dashboard | bundled | bundled | — | — | none |
| `mip_lead_scoring` | experiment | `[dev skyler] /Shared/mip/lead-scoring` | `/Shared/mip/lead-scoring` | — | — | correct (DAB auto-prefix) |
| Genie space | external | `mortgage_lead_intelligence` (shared) | `mortgage_lead_intelligence` (shared) | — | — | F2, F6 |

The `[dev skyler]` prefix is the DAB `mode: development` automatic sandboxing (per the comment at `databricks.yml:531-536`). It's correct and goes away under `mode: production` — verified above. No hardcoded `skyler` / `dev` / `test` strings found in resource names, paths, or descriptions (grepped).

## Zero-click checklist

Required per CLAUDE.md: `databricks bundle deploy -t <target>` + one `.env.local` fill-in must provision everything.

| Provisioned by bundle? | Resource | Notes |
|---|---|---|
| ✅ | UC catalog + schemas | `sql/ddl/001_catalogs_schemas.sql` via `mip_refresh_silver.init_catalog_schemas` |
| ✅ | Ref tables (`mip.ref.lender_dictionary`) | `mip_refresh_silver.init_ref_tables` + `seed_ref_lender_dictionary` |
| ✅ | Silver tables | `mip_feature_pipeline` DLT — but gated by B1 in prod |
| ✅ | Gold tables (CTAS chain) | `mip_refresh_scores` |
| ✅ | Semantics views | `refresh_semantics_views` task |
| ✅ | Lakebase instance + catalog | `resources.database_instances.mip_app_state` + `resources.database_catalogs.mip_app_state_catalog` |
| ✅ | Lakebase schema + seed | `mip_lakebase_migrate` → `lakebase/schema.sql` + `lakebase/seed_campaigns.sql` |
| ✅ | FRED ingest + seed file | `mip_fred_rates_ingest` — seed CSV at `data/seeds/fred_mortgage30us_seed.csv` guarantees data on first boot (no external API dependency at deploy time) |
| ✅ | Dashboards | `mip_executive_dashboard` + `mip_segment_dashboard` via `.lvdash.json` files |
| ✅ | MLflow experiment | `mip_lead_scoring` |
| ✅ | Databricks App | `mip_app` |
| ⚠️ | Genie space | Out-of-bundle (`tools/databricks/provision_genie_space.py`). `scripts/deploy.sh` chains it so single-command deploy still holds — but *only* if the operator runs `scripts/deploy.sh`, not `databricks bundle deploy -t prod` alone. See F6. |
| ⚠️ | M2M OAuth app + client-id | `tools/databricks/provision_m2m_oauth.py` creates the service principal + secret on first run. `docs/security/m2m-oauth-setup.md` documents the flow. Chained into `scripts/deploy.sh`. |
| ❓ | `mip_snapshot_dashboards` job | Declared with `tasks: []` — does nothing. Dead resource. Remove or populate. |

No README / setup doc was found telling the operator to "click X in the Databricks UI" — grepped `README.md`, `CONTRIBUTING.md`, `AGENTS.md`, `docs/` for the usual manual-step patterns and found only in-talk-track click paths (which refer to the app itself, not workspace setup). Zero-click posture holds modulo F6.

## Recommended follow-ups (main agent to triage)

1. **Fix B1** (one-line edit: drop `development: true` from the pipeline). Re-run `databricks bundle validate -t prod` — should be green.
2. **Fix B2** (drop two `pause_status: UNPAUSED` lines). Re-run `databricks bundle validate -t dev -o json` and confirm `mip_fred_rates_ingest.schedule.pause_status == 'PAUSED'` in dev.
3. **Decide on B3** (separate workspace vs separate catalog for prod). Author target-level variable override for `uc_catalog` and either `workspace.host` or a per-customer `.env.local` template.
4. Add `deploy-prod` Makefile target that mirrors `deploy-dev` but with `--target prod` (F6 recommendation).
5. Delete `mip_snapshot_dashboards` job (empty `tasks: []`) or populate it.
6. Decide fate of `resources/{apps,dashboards,jobs}.yml` stub mirrors — delete or wire up `include:`. Currently they are inert and confusing.
7. Update `docs/implementation-plan.md:194` to match bundle reality on warehouse sizing (F1).

Re-run `databricks bundle validate -t prod` after (1)–(3). When that is green, dry-run can flip to **READY**.
