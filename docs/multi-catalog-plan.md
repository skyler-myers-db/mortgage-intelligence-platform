# Multi-catalog templatization — design plan (R6-01)

**Status:** IMPLEMENTED (2026-04-23). The rendered-files pattern described
in option (b) below is now the deploy default — every `databricks bundle
validate` / `deploy` path runs `tools/render_sql.py` first, and the
bundle reads from `sql/_rendered/**` (gitignored) rather than `sql/**`.
Canonical sources under `sql/**` keep the readable `mip.*` prefixes and
remain the review-of-record.

## Problem

CLAUDE.md mandates a zero-click deploy through `./scripts/deploy.sh -t dev`
(or `make deploy-dev`): the command of record must provision, populate,
promote, and smoke-check every resource the app needs, with no manual UI steps
and no out-of-band preprocessing. That promise was violated for any customer
whose UC catalog is not named `mip`.

- The Python layer was already multi-catalog safe via `backend/services/databricks_sql_helpers.qualify(schema, table)` — `MIP_DEFAULT_CATALOG=mip_prod` reroutes every backend SQL caller.
- The SQL layer was NOT. `docs/runbook-multi-catalog.md` documented a `sed` preprocessing workaround that had to be run outside the bundle deploy. That is a packaging bug per CLAUDE.md ("Manual click-ops in the Databricks UI are a packaging bug" generalizes to manual preprocessing steps).

This is now resolved — see "Implementation" below.

## Inventory of hardcoded `mip.<schema>.` references (historical baseline)

Scanned 2026-04-23 across `sql/` (56 .sql files total):

| Directory | Files with `mip.` refs | Total occurrences |
|---|---:|---:|
| `sql/transformations/` | 20 | 147 |
| `sql/ddl/` | 19 | 109 |
| `sql/metric_views/` | 3 | 25 |
| `sql/ref/` | 3 | 6 |
| `sql/uc_functions/` | 4 | 4 |
| **Total** | **49** | **291** |

Post-implementation re-scan (2026-04-23 rollout): 56 files processed, 313 substitutions applied per render pass — slight drift above the original 291 reflects subsequent SQL edits between design and rollout. The renderer reports the exact counts on every invocation.

Prefixes in use: `mip.gold.`, `mip.silver.`, `mip.ref.`, `mip.semantics.`, `mip.raw.`. No other three-part names appear.

## Option analysis

### (a) `${bundle.variables.uc_catalog}` interpolation in bundle SQL tasks

Databricks Asset Bundles do substitute `${var.xxx}` inside YAML, but that only reaches SQL files when the variable is passed as a SQL-task **parameter** (e.g. `sql_task.parameters.catalog`) and the statement references it as `:catalog`. It does NOT rewrite three-part names inside the statement body. So every `FROM mip.gold.borrower_360` would need to become `FROM IDENTIFIER(:catalog || '.gold.borrower_360')` — which works in Databricks SQL, but requires editing every 291 identifier individually AND rewriting every CTE reference, CTAS target, UDF qualifier, and CREATE TABLE target. The cost is the same as option (c), and it makes the SQL harder to read in isolation.

**Verdict:** rejected.

### (b) Preprocessing step inside `databricks bundle deploy` — SHIPPED

Run `tools/render_sql.py` to materialize `sql/_rendered/**` from `sql/**` with a regex-based substitution on the five documented prefixes. Bundle declares `sql_task.file.path` pointing at `sql/_rendered/...` and the rendered directory is gitignored.

Pros:
- Zero per-file edits — the canonical sources keep the readable `mip.*` identifiers.
- Integrates with `databricks bundle deploy` via the `scripts/deploy.sh` wrapper and the `render-sql` Makefile prerequisite; no manual step.
- The source SQL stays canonical (no `IDENTIFIER()` ceremony).

Cons:
- Adds a build step. Mitigated by making `render-sql` a dependency of every `bundle-*` Makefile target.
- Linting tools (sqlfluff) now run against readable source rather than rendered output — arguably a win.

### (c) Session variables + dynamic SQL

Use `SET var.catalog = 'mip';` at the top of every file and reference `${catalog}` in statements. Databricks SQL supports session variables but NOT in DDL/CTAS three-part names without `EXECUTE IMMEDIATE`. This would force every file into a dynamic-SQL wrapper.

**Verdict:** rejected.

## Implementation

### Renderer — `tools/render_sql.py`

Regex-free Jinja, just `re.sub` over the five prefixes:

- `\bmip\.gold\.`        → `{catalog}.gold.`
- `\bmip\.silver\.`      → `{catalog}.silver.`
- `\bmip\.ref\.`         → `{catalog}.ref.`
- `\bmip\.semantics\.`   → `{catalog}.semantics.`
- `\bmip\.raw\.`         → `{catalog}.raw.`

Safety:

- The word-boundary `\b` prevents `mip_app.approvals` (the Lakebase schema) from matching.
- The trailing `.<schema>.` literal prevents bare `mip.gold` without a trailing dot from matching unrelated prose.
- The renderer only walks `sql/**` and skips `sql/_rendered/**`, so a second run never chews its own tail.

CLI:

```bash
# Default — reads settings.mip_default_catalog (env var MIP_DEFAULT_CATALOG),
# falls back to "mip" when the backend module cannot be imported.
python tools/render_sql.py

# Explicit target
python tools/render_sql.py --catalog summit_mortgage

# Custom paths (rare; used by CI for diff-based validation)
python tools/render_sql.py --source sql --dest sql/_rendered
```

Output line:

```
render_sql: catalog=mip processed=56 written=N substitutions=313 source=sql dest=sql/_rendered
```

Idempotent: the renderer writes a destination file only when its content differs from what is already there, so re-running with the same catalog short-circuits to zero writes.

### Bundle wiring

1. Every `path: sql/...` in `databricks.yml` now reads `path: sql/_rendered/...`.
2. `Makefile` targets `render-sql`, `bundle-validate`, `bundle-deploy`, `bundle-validate-env`, `bundle-deploy-dev` all depend on `render-sql` (the target is a thin wrapper around `tools/render_sql.py --catalog "$${MIP_DEFAULT_CATALOG:-mip}"`).
3. `scripts/deploy.sh` runs the renderer as step 1a before the frontend build.
4. `sql/_rendered/` is listed in `.gitignore`.

### Identity check for the default catalog

When `--catalog mip` is passed (the project default), every substitution is an identity rewrite. The rendered tree is byte-identical to source — customers who keep the default catalog name pay zero semantic cost.

## Validation

```bash
# Identity render: sql/ and sql/_rendered/ are byte-identical.
python tools/render_sql.py --catalog mip
diff -r sql/transformations sql/_rendered/transformations
# (no output)

# Non-default render: mip.<schema>. becomes summit_mortgage.<schema>.
python tools/render_sql.py --catalog summit_mortgage
diff sql/transformations/gold_borrower_360.sql \
     sql/_rendered/transformations/gold_borrower_360.sql | head
# shows CREATE OR REPLACE TABLE summit_mortgage.gold.borrower_360 AS ...

# Bundle validate passes against the rendered tree.
databricks bundle validate -t ci
# Validation OK!
```

## Out of scope

- **Lakebase schema** (`mip_app`): Postgres, not UC. Keep the existing `settings.mip_lakebase_schema` knob.
- **Bundle resource names** (`mip_refresh_scores`, `mip-lakebase`): these are Databricks-side identifiers that uniquely name the tenant's bundle install. They are keyed on tenant, not catalog, and should stay literal.
- **`app.yaml` / Python runtime**: already multi-catalog safe via `qualify()`.
- **`resources/jobs.yml`**: not auto-included by `databricks.yml`; inline declarations in `databricks.yml` are authoritative. See memory `project_bundle_resources_are_inline.md`.

## Changelog

- 2026-04-23 — initial design doc (R6-01). Prototype marker added to `sql/transformations/gold_borrower_360.sql` header. Rollout deferred.
- 2026-04-23 — IMPLEMENTED: `tools/render_sql.py` shipped; `databricks.yml` paths repointed to `sql/_rendered/`; Makefile + `scripts/deploy.sh` wire the renderer as a pre-deploy step. The `sed` workaround in `docs/runbook-multi-catalog.md` is retired.
