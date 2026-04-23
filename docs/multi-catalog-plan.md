# Multi-catalog templatization — design plan (R6-01)

**Status:** Design + one-file prototype. Rollout deferred to a follow-up slice.

## Problem

CLAUDE.md mandates a zero-click deploy: `databricks bundle deploy -t dev` must provision every resource the app needs, with no manual UI steps and no out-of-band preprocessing. Today that promise is violated for any customer whose UC catalog is not named `mip`.

- The Python layer is already multi-catalog safe via `backend/services/databricks_sql_helpers.qualify(schema, table)` — `MIP_DEFAULT_CATALOG=mip_prod` reroutes every backend SQL caller.
- The SQL layer is NOT. `docs/runbook-multi-catalog.md` documents a `sed` preprocessing workaround that has to be run outside the bundle deploy. That is a packaging bug per CLAUDE.md ("Manual click-ops in the Databricks UI are a packaging bug" generalizes to manual preprocessing steps).

## Inventory of hardcoded `mip.<schema>.` references

Scanned 2026-04-23 across `sql/` (56 .sql files total):

| Directory | Files with `mip.` refs | Total occurrences |
|---|---:|---:|
| `sql/transformations/` | 20 | 147 |
| `sql/ddl/` | 19 | 109 |
| `sql/metric_views/` | 3 | 25 |
| `sql/ref/` | 3 | 6 |
| `sql/uc_functions/` | 4 | 4 |
| **Total** | **49** | **291** |

(An additional four files carry `mip.` in comments/docstrings only — inert but still a find target.)

Prefixes in use: `mip.gold.`, `mip.silver.`, `mip.ref.`, `mip.semantics.`, `mip.raw.`. No other three-part names appear.

## Option analysis

### (a) `${bundle.variables.uc_catalog}` interpolation in bundle SQL tasks

Databricks Asset Bundles do substitute `${var.xxx}` inside YAML, but that only reaches SQL files when the variable is passed as a SQL-task **parameter** (e.g. `sql_task.parameters.catalog`) and the statement references it as `:catalog`. It does NOT rewrite three-part names inside the statement body. So every `FROM mip.gold.borrower_360` would need to become `FROM IDENTIFIER(:catalog || '.gold.borrower_360')` — which works in Databricks SQL, but requires editing every 291 identifier individually AND rewriting every CTE reference, CTAS target, UDF qualifier, and CREATE TABLE target. The cost is the same as option (c), and it makes the SQL harder to read in isolation.

**Verdict:** reject. High per-file cost, worse readability, no upside over (c).

### (b) Jinja preprocessing step inside `databricks bundle deploy`

Add a `pre_bytecode` or `sync.paths` hook that runs `tools/render_sql.py` to materialize `sql/_rendered/**` from `sql/**` with a `{{ uc_catalog }}` Jinja variable. Bundle declares `sql_task.file.path` pointing at `sql/_rendered/...` and the rendered directory is gitignored.

Pros:
- Zero per-file edits — the template language is line-noise-free (`{{ catalog }}.gold.borrower_360` reads almost the same).
- Integrates with `databricks bundle deploy` via the existing `sync` phase — no manual steps.
- The source SQL stays canonical (no IDENTIFIER() ceremony).

Cons:
- Adds a build step. The rendered files must be produced before `databricks bundle deploy` reads them, so we need a `pre-deploy` script shim (Makefile target or `tools/pre_deploy.py`) and docs/CI updates.
- Diverges from "pure bundle" posture — Databricks Bundles does not have a first-class template hook, so we're bolting Jinja on.
- Linting/formatting tools (sqlfluff, ruff-sql) run against the source, not the rendered output — usually fine, but sqlfluff may complain about `{{ catalog }}`.

### (c) Bundle variable substitution via a new `sql/_catalog.sql` include + dynamic SQL

Use `SET var.catalog = 'mip';` at the top of every file and reference `${catalog}` in statements. Databricks SQL supports session variables but NOT in DDL/CTAS three-part names without `EXECUTE IMMEDIATE`. This would force every file into a dynamic-SQL wrapper.

**Verdict:** reject. Worst readability of the three, and breaks `databricks bundle validate` which parses static SQL.

### Recommendation

**Option (b): Jinja preprocessing.** The build step is small (single-pass text substitution, deterministic), the source SQL stays readable, and it fits inside `databricks bundle deploy` via a Makefile/pre-deploy script. The implementation is ~40 lines of Python + a `sql_rendered_at` variable.

## Prototype — one-file marker

`sql/transformations/gold_borrower_360.sql` now carries a top-comment block (header of the file) that names the multi-catalog contract and points here. It is illustrative — no substitution is actually performed. When the rollout lands, the intended pattern is:

```sql
-- SOURCE (checked in):
CREATE OR REPLACE TABLE mip.gold.borrower_360 AS ...
FROM mip.silver.lien_current AS lc ...

-- RENDERED via `uv run tools/render_sql.py --catalog mip_prod`:
CREATE OR REPLACE TABLE mip_prod.gold.borrower_360 AS ...
FROM mip_prod.silver.lien_current AS lc ...
```

Exact substitutions (regex-based, whole-word on the catalog segment):

- `\bmip\.gold\.` -> `{catalog}.gold.`
- `\bmip\.silver\.` -> `{catalog}.silver.`
- `\bmip\.ref\.` -> `{catalog}.ref.`
- `\bmip\.semantics\.` -> `{catalog}.semantics.`
- `\bmip\.raw\.` -> `{catalog}.raw.`

Same list as `docs/runbook-multi-catalog.md` Option A — the proposal is to move that `sed` step from "customer runs it manually" to "bundle deploy runs it automatically".

## Rollout plan (deferred)

1. Add `tools/render_sql.py` (Jinja-less, just regex swap on the five prefixes above — simpler than Jinja and no extra dep).
2. Wire it into `Makefile`'s `validate` + `deploy` targets.
3. Rewrite `databricks.yml` SQL-task paths from `sql/**/*.sql` to `sql/_rendered/**/*.sql`.
4. Add `sql/_rendered/` to `.gitignore`.
5. Delete `docs/runbook-multi-catalog.md` Options A + B — the runbook becomes "set `--var="uc_catalog=X"`, deploy".
6. Update CI to fail if any `.sql` under `sql/` (excluding `_rendered/`) contains a bare `mip.<schema>.` AFTER the intended rename convention is `{catalog}.<schema>.` — a lightweight lint.

Estimated cost: half a slice. The 291 occurrences never need to be hand-edited — the renderer does it.

## Out of scope

- **Lakebase schema** (`mip_app`): Postgres, not UC. Keep the existing `settings.mip_lakebase_schema` knob.
- **Bundle resource names** (`mip_refresh_scores`, `mip-lakebase`): these are Databricks-side identifiers that uniquely name the tenant's bundle install. They are keyed on tenant, not catalog, and should stay literal.
- **`app.yaml` / Python runtime**: already multi-catalog safe via `qualify()`.

## Changelog

- 2026-04-23 — initial design doc (R6-01). Prototype marker added to `sql/transformations/gold_borrower_360.sql` header. Rollout deferred.
