---
name: dab-complex-variable-channels
description: DAB CLI v1.10.0 refuses BUNDLE_VAR_ and --var for complex (list/map) variables; variable-overrides.json is the only non-target channel; nested ${var.a.${var.b}} does not resolve
metadata:
  type: project
---

Probed 2026-10-27 with read-only `bundle validate -p paychex` on scratch bundles (CLI v1.10.0):

- `BUNDLE_VAR_<complex>='["x"]'` -> hard error "setting via environment variables ... is not supported for complex variable".
- `--var 'name=["x"]'` -> CSV parse error on the flag; not a usable channel either.
- `${var.map.${var.key}}` -> stays a literal string ("expected sequence, found string" warning). No per-install selection by nesting.
- `<bundle root>/.databricks/bundle/<target>/variable-overrides.json` WORKS for complex variables, and a value like `["${workspace.current_user.userName}"]` inside it is interpolated by the CLI.
- Explicit `pause_status: ${var.x}` with BUNDLE_VAR_x=UNPAUSED survives `mode: development`; `presets.trigger_pause_status: UNPAUSED` under dev mode is a validate ERROR (not a silent unpause).

**Why:** the dev-ci CI staging install is the same `dev` target as the live install (only the root leaf differs), so no target-level setting can differ between them; anything list-shaped that must differ (e.g. mip_scheduled_refresh failure email) has to go through the overrides file. scheduled-refresh lane wrote it from `tools/databricks/bundle_env.py` via `tools/databricks/scheduled_refresh_posture.py`.

**How to apply:** for a scalar per-install knob, export BUNDLE_VAR_ from the deploy.sh preflight; for a list/map knob, write the overrides file from bundle_env (merge, never clobber, fail closed on bad JSON) and keep tests pointing `bundle_env.BUNDLE_ROOT` at tmp so they never write into the checkout. Related: [[ci-install-isolation]], [[project_lakebase_bundle_schema]].
