# Runbook: deploying the Mortgage Intelligence Platform into a non-default Unity Catalog

## TL;DR

Module 0 defaults its UC catalog to `mip`. Customers who require a different catalog name (`mip_prod`, `lender_uc`, `cotality_mip`, …) set one variable and deploy. The Python API layer, Spark Python jobs, and SQL transformation layer are now multi-catalog safe — there is no manual preprocessing step.

```bash
# In .env.local on the SE's laptop:
MIP_DEFAULT_CATALOG=summit_mortgage

# Then deploy:
./scripts/deploy.sh           # or: make deploy-dev
```

That is the entire operator contract. `scripts/deploy.sh` (step 1a) runs `tools/render_sql.py --catalog "${MIP_DEFAULT_CATALOG:-mip}"` before the bundle validate/deploy phase, which materializes `sql/_rendered/**` with the target catalog substituted into the five documented UC prefixes. The bundle's SQL tasks all read from `sql/_rendered/**`, so every CTAS / DDL / metric view / UC function lands in the right catalog on first deploy.

For the bundle-variable path (non-default workspace target), keep the two-var form:

```bash
MIP_DEFAULT_CATALOG=mip_prod \
databricks bundle deploy -t prod \
    --var="uc_catalog=mip_prod" \
    --var="lakebase_instance=mip-prod-lakebase"
```

(`uc_catalog` is the bundle-side variable consumed by `pipelines.mip_feature_pipeline.catalog` and the Spark Python job parameters; `MIP_DEFAULT_CATALOG` drives the SQL renderer and the Python runtime. Keep them equal.)

## Python layer — multi-catalog safe since hole-finder R2 #19

As of 2026-04-23 every production Python caller names UC objects through `qualify()`:

```python
from backend.services.databricks_sql_helpers import qualify

sql = f"SELECT count(*) FROM {qualify('gold', 'borrower_360')}"
# -> "SELECT count(*) FROM mip_prod.gold.borrower_360" on the prod workspace
```

Refactored call-sites:

- `backend/services/repositories/databricks_repo.py` (portfolio / segment / lead / borrower / offer / geo readers)
- `backend/services/admin_rules.py` (`/api/v1/admin/rules` + sources)
- `backend/services/genie_answers.py` (Genie wire models + prompt suggestions)
- `backend/services/pii_redaction.py` (`lender_dictionary` lookup)
- `backend/services/state_footprint.py` (`state_footprint` lookup)
- `backend/api/offers.py` (offer-evidence citation)
- Genie router trusted-asset list on `/api/v1/genie/start`

`backend/services/scoring.py` keeps `SOURCE_DISPLAY_LABELS` keyed on the default `mip.*` prefix for back-compat, but `source_display_label()` falls back to a `schema.object` lookup so business labels still resolve when the catalog is renamed.

## SQL layer — multi-catalog safe since R6-01 rollout

The `sql/transformations/gold_*.sql`, `sql/ddl/*.sql`, `sql/ref/*.sql`, `sql/metric_views/*.sql`, and `sql/uc_functions/*.sql` files keep `mip.<schema>.<table>` as the canonical prefix for readability. `tools/render_sql.py` rewrites those prefixes for the target catalog into `sql/_rendered/**` at deploy time, and the bundle's SQL tasks read from the rendered tree.

Substitutions applied (regex-anchored; `mip_app.*` is NEVER matched because the word boundary + trailing dot + known-schema list rule out the Lakebase schema):

```
mip.gold.      -> {catalog}.gold.
mip.silver.    -> {catalog}.silver.
mip.ref.       -> {catalog}.ref.
mip.semantics. -> {catalog}.semantics.
mip.raw.       -> {catalog}.raw.
```

Wiring:

- **`Makefile` targets** (`render-sql`, `bundle-validate`, `bundle-deploy`, `bundle-validate-env`, `bundle-deploy-dev`) all depend on `render-sql`. The `render-sql` target runs `tools/render_sql.py --catalog "$${MIP_DEFAULT_CATALOG:-mip}"` — idempotent, fast, zero dependencies.
- **`scripts/deploy.sh`** (step 1a) runs the renderer before the frontend build, so the rendered tree is present before the bundle is touched.
- **`databricks.yml`** declares every `sql_task.file.path` under `sql/_rendered/...` rather than `sql/...`. The canonical sources under `sql/**/*.sql` stay committed; the rendered copies under `sql/_rendered/**` are gitignored.
- **Identity when `--catalog mip`:** every substitution is a byte-identical rewrite on the default catalog, so customers who keep the default name pay nothing.

Operators never run the renderer by hand. If for some reason you want to inspect the rendered output manually:

```bash
make render-sql                                # honours MIP_DEFAULT_CATALOG
python tools/render_sql.py --catalog <name>    # ad-hoc one-off
```

## Spark Python jobs — multi-catalog safe since 2026-05-17

Two bundle jobs execute Python against Unity Catalog tables instead of SQL files:

- `mip_sync_lifecycle_state` runs `jobs/sync_lifecycle_state.py` with `--catalog=${var.uc_catalog}` and incrementally MERGEs durable Lakebase rows into `${var.uc_catalog}.gold.borrower_lifecycle_state`. The normal app hook uses the same canonical MERGE through the SQL warehouse; the job is durable failure recovery and explicit repair.
- `mip_fred_rates_ingest` runs `jobs/fred_rates_ingest.py` with `--table=${var.uc_catalog}.silver.market_rates_weekly` for both seed and live FRED refresh tasks.

This closes the last non-SQL path that could otherwise land state in `mip.*` during a renamed-catalog customer deploy.

Both scheduled fallback jobs deploy with `pause_status: PAUSED` in every target.
If a customer unpauses a recurring FRED or lifecycle cadence, first confirm that
target writes to an isolated catalog; two unpaused targets writing the same
catalog can queue redundant recovery/snapshot runs and create avoidable
compute spend.

## Genie space and eval — multi-catalog safe since 2026-05-17

`tools/databricks/provision_genie_space.py` renders both tenant and catalog
placeholders before publishing `genie/mortgage_lead_intelligence_space.yml`.
For `MIP_DEFAULT_CATALOG=acme_mortgage`, trusted assets, instructions, and
example SQL are published as `acme_mortgage.gold.*` /
`acme_mortgage.semantics.*`.

The backend Genie runtime uses the same catalog through `qualify()`:

- `/api/v1/genie/start` returns configured-catalog trusted assets.
- Trusted-SQL policy checks use `backend/services/genie_trusted_assets.py`.
- Canonical answer/repair SQL in `databricks_genie_canonical.py` is generated
  from `settings.mip_default_catalog` at app boot.
- Source-gap responses cite `{catalog}.gold.source_readiness`.
- The Ask Genie route renders the backend-provided trusted assets instead of a
  hardcoded `mip.*` list.
- `tools/genie_eval.py` rewrites expected citations and canonical SQL from
  `MIP_DEFAULT_CATALOG`, so customer-catalog evals do not fail on default
  `mip.*` expectations.

## Out-of-scope names that still hold

- **Lakebase schema (`mip_app`)** lives in Postgres, not Unity Catalog. It has its own name and is governed by `settings.mip_lakebase_schema`. The renderer's word-boundary + known-schema regex deliberately does NOT match it.
- **Bundle resource blocks in `databricks.yml`** already reference `${var.uc_catalog}` for pipeline `catalog:` fields and Spark Python job task parameters; no change needed.
- **Admin rules seed (`ref.offer_rules_config`)** — the Python reader qualifies through `qualify('ref', 'offer_rules_config')` and the seed SQL in `sql/ref/` is now covered by the renderer.

## Validation

```bash
# Smoke-test the Python layer against a renamed catalog without
# touching the warehouse:
MIP_DEFAULT_CATALOG=mip_prod python -c "
from backend.services.databricks_sql_helpers import qualify
assert qualify('gold', 'borrower_360') == 'mip_prod.gold.borrower_360'
print('OK')
"

# Smoke-test the SQL renderer — output is byte-identical when --catalog mip.
python tools/render_sql.py --catalog mip
diff -r sql/transformations sql/_rendered/transformations  # empty output

# Non-default catalog swaps the prefix in every file.
python tools/render_sql.py --catalog summit_mortgage
grep 'CREATE OR REPLACE TABLE' sql/_rendered/transformations/gold_borrower_360.sql
# -> CREATE OR REPLACE TABLE summit_mortgage.gold.borrower_360 AS ...

# Bundle validate reads from the rendered tree.
databricks bundle validate -t ci   # Validation OK!

# Confirm Spark Python jobs receive the same catalog variable.
grep -n -- '--catalog=${var.uc_catalog}' databricks.yml
grep -n -- '--table=${var.uc_catalog}.silver.market_rates_weekly' databricks.yml

# Confirm admin /settings reports the right catalog at runtime:
curl -s http://localhost:8000/api/v1/admin/settings | jq .catalog
```

## Changelog

- 2026-04-23 — hole-finder round-2 #19: introduced `qualify()` helper; refactored the Python API layer; documented the SQL-layer gap.
- 2026-04-23 — R6-01 rollout: shipped `tools/render_sql.py`; `databricks.yml` and `Makefile` now consume `sql/_rendered/**`; retired the manual `sed` workaround.
- 2026-05-17 — multi-tenant audit remediation: wired Spark Python lifecycle and FRED jobs to `${var.uc_catalog}` so renamed-catalog deploys stay isolated outside SQL-task paths too.
- 2026-07-14 — lifecycle cost remediation: centralized sparse Delta MERGE logic across app/job paths, removed full-universe seeding from the job, and made queued Jobs runs the durable retry surface.
