---
name: Lakebase is a first-class bundle resource type as of CLI 0.297.x
description: Discovered-the-hard-way note that `resources.database_instances` and `resources.database_catalogs` are valid bundle schema entries, not future-state.
type: project
---

The Module 0 databricks.yml (slice 5, 2026-04-21) now declares:

```yaml
resources:
  database_instances:
    mip_app_state:
      capacity: CU_1
      node_count: 1
      retention_window_in_days: 7
      enable_readable_secondaries: false
  database_catalogs:
    mip_app_state_catalog:
      name: mip_app_state
      database_instance_name: ${resources.database_instances.mip_app_state.name}
      database_name: ${var.lakebase_database_name}
      create_database_if_not_exists: true
```

Confirmed via `databricks bundle schema` on CLI 0.297.x. The app
resource binding in `resources/apps.yml` uses `instance_name` +
`database_name` + `permission` (all required) on the `database`
binding; `CAN_CONNECT_AND_CREATE` lets the app open a connection and
run DDL on first boot.

**Why:** An earlier comment in databricks.yml claimed Lakebase was not
yet a bundle-managed resource type. That was true for CLI <0.290 but
is obsolete; leaving the comment encouraged manual out-of-bundle
provisioning and broke the self-contained-deploy posture.

**How to apply:** Any new module that needs Lakebase should declare
the instance + catalog inline and wire the migrate job
(`jobs/lakebase_migrate.py`) to apply idempotent DDL. Capacity `CU_1`
is the smallest tier -- appropriate for demo write volume; lift to
CU_2 for production. `retention_window_in_days` must be between 2 and
35; default of 7 is fine. `gen_random_uuid()` requires `CREATE
EXTENSION IF NOT EXISTS pgcrypto` in the schema SQL; it's included in
lakebase/schema.sql.
