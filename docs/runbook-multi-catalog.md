# Runbook: deploying the Mortgage Intelligence Platform into a non-default Unity Catalog

## TL;DR

Module 0 defaults its UC catalog to `mip`. Customers who require a
different catalog name (`mip_prod`, `lender_uc`, `cotality_mip`, …)
deploy with a bundle variable:

```bash
databricks bundle deploy -t prod \
    --var="uc_catalog=mip_prod" \
    --var="lakebase_instance=mip-prod-lakebase"
```

The Python API layer reads the catalog from
`settings.mip_default_catalog` (env var `MIP_DEFAULT_CATALOG`) at
runtime via `backend.services.databricks_sql_helpers.qualify(schema,
table)`, so every backend query routes against the configured catalog
automatically. **The SQL transformation + DDL files still hardcode
`mip.*` and need one of the workarounds below for catalogs other than
`mip`.**

## Python layer — already multi-catalog safe

As of hole-finder round-2 #19 (2026-04-23) every production Python
caller names UC objects through `qualify()`:

```python
from backend.services.databricks_sql_helpers import qualify

sql = f"SELECT count(*) FROM {qualify('gold', 'borrower_360')}"
# -> "SELECT count(*) FROM mip_prod.gold.borrower_360" on the prod workspace
```

Refactored call-sites:

- `backend/services/repositories/databricks_repo.py` (portfolio / segment / lead / borrower / offer / geo readers)
- `backend/services/admin_rules.py` (`/api/admin/rules` + sources)
- `backend/services/genie_answers.py` (safe-corpus `trusted_assets`)
- `backend/services/pii_redaction.py` (`lender_dictionary` lookup)
- `backend/services/state_footprint.py` (`state_footprint` lookup)
- `backend/api/offers.py` (offer-evidence citation)
- `backend/api/genie.py` (trusted-asset list on /start)

`backend/services/scoring.py` keeps `SOURCE_DISPLAY_LABELS` keyed on the
default `mip.*` prefix for back-compat, but `source_display_label()`
now falls back to a `schema.object` lookup so business labels still
resolve when the catalog is renamed.

## SQL layer — KNOWN LIMITATION

`sql/transformations/gold_*.sql` and `sql/ddl/*.sql` still hardcode
`mip.<schema>.<table>`. These files run from Databricks Jobs / Lakeflow
pipelines, not through the FastAPI service; the `qualify()` helper
does not reach them.

Two workarounds, pick whichever fits your release process:

### Option A — bundle pre-processor (recommended for CI/CD)

Add a pre-deploy step that substitutes `${var.uc_catalog}` for each
occurrence of `mip.` (limited to `mip.gold.`, `mip.silver.`,
`mip.semantics.`, `mip.ref.`, `mip.raw.`) inside `sql/**/*.sql` before
`databricks bundle deploy`. Example shell step:

```bash
CATALOG="${UC_CATALOG:-mip}"
if [[ "$CATALOG" != "mip" ]]; then
    find sql -name '*.sql' -print0 | xargs -0 sed -i \
        -e "s|\\bmip\\.gold\\.|${CATALOG}.gold.|g" \
        -e "s|\\bmip\\.silver\\.|${CATALOG}.silver.|g" \
        -e "s|\\bmip\\.semantics\\.|${CATALOG}.semantics.|g" \
        -e "s|\\bmip\\.ref\\.|${CATALOG}.ref.|g" \
        -e "s|\\bmip\\.raw\\.|${CATALOG}.raw.|g"
fi
databricks bundle deploy -t prod --var="uc_catalog=$CATALOG"
```

Run the step only from CI, never against your working tree — the edits
are deploy-time substitutions, not committed changes.

### Option B — one-time `sed` across the tree (for a hard fork)

If you maintain a vendored fork with a permanent catalog name, you can
do a single `sed -i` run and commit the result:

```bash
NEW="mip_prod"
find sql -name '*.sql' -print0 | xargs -0 sed -i \
    -e "s|\\bmip\\.gold\\.|${NEW}.gold.|g" \
    -e "s|\\bmip\\.silver\\.|${NEW}.silver.|g" \
    -e "s|\\bmip\\.semantics\\.|${NEW}.semantics.|g" \
    -e "s|\\bmip\\.ref\\.|${NEW}.ref.|g" \
    -e "s|\\bmip\\.raw\\.|${NEW}.raw.|g"
git add sql && git commit -m "chore(sql): vendor for uc_catalog=${NEW}"
```

## Out-of-scope names that still hold

- **Lakebase schema (`mip_app`)** lives in Postgres, not Unity Catalog.
  It has its own name and is governed by `settings.mip_lakebase_schema`.
  Do not rename via the UC workarounds above.
- **Bundle resource blocks in `databricks.yml`** already reference
  `${var.uc_catalog}`; no change needed.
- **Admin rules seed (`ref.offer_rules_config`)** — the Python reader
  qualifies through `qualify('ref', 'offer_rules_config')`, but the seed
  SQL in `sql/ref/` hardcodes `mip.ref.`. Use Option A or B above if
  the tenant catalog is non-default.

## Validation

```bash
# Smoke-test the Python layer against a renamed catalog without
# touching the warehouse:
MIP_DEFAULT_CATALOG=mip_prod python -c "
from backend.services.databricks_sql_helpers import qualify
assert qualify('gold', 'borrower_360') == 'mip_prod.gold.borrower_360'
print('OK')
"

# Confirm admin /settings reports the right catalog:
curl -s http://localhost:8000/api/admin/settings | jq .catalog
```

## Changelog

- 2026-04-23 — hole-finder round-2 #19: introduced `qualify()` helper;
  refactored the Python API layer; documented the SQL-layer gap.
