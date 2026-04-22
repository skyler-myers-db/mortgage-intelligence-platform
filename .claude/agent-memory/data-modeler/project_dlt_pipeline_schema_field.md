---
name: DLT pipelines use `schema`, not `target`
description: In databricks.yml DLT pipeline resources, `target:` is deprecated; use `schema:` plus `catalog:`.
type: project
---

In `databricks.yml` under `resources.pipelines.*`, the `target:` field is deprecated. Use `schema:` alongside `catalog:` instead.

**Why:** Databricks CLI bundle schema diagnostic flags `target:` as deprecated (observed at v0.297.2+). `databricks bundle validate` still passes with `target:`, but IDE diagnostics complain, and future CLI versions may reject it.

**How to apply:** When wiring a DLT pipeline resource (e.g. `mip_feature_pipeline`), declare:

    catalog: ${var.uc_catalog}
    schema: silver     # or gold, semantics, etc.

NOT `target: silver`. The rest of the pipeline shape (`libraries`, `photon`, `continuous`, `development`) is unchanged.
