---
name: bundle resources are inline in databricks.yml, not auto-included from resources/
description: The repo has `resources/*.yml` files but databricks.yml has NO include block, so those files are documentation mirrors only. Real resources must be added inline in databricks.yml.
type: project
---

As of 2026-04-21, `databricks.yml` does not declare an `include:` block. The per-resource files under `resources/` (e.g. `jobs.yml`, `dashboards.yml`, `apps.yml`) EXIST alongside inline declarations in `databricks.yml`, but `databricks bundle validate -o json` shows only the inline declarations materialize. The `resources/*.yml` files are architectural inventory documents, not sources of truth.

**Why:** Verified empirically: `resources/dashboards.yml` declares `executive_dashboard`/`segment_dashboard` with no `mip_` prefix, while `databricks.yml` declares `mip_executive_dashboard`/`mip_segment_dashboard` inline. The materialized bundle shows the `mip_` variants, not the resources/ variants. This pattern is consistent across jobs, dashboards, and apps.

**How to apply:** When adding any new bundle resource (job, pipeline, dashboard), put the canonical definition INLINE in `databricks.yml` so it materializes. Optionally mirror into `resources/<type>.yml` as inventory doc — but keep the two in sync, and flag the divergence for cleanup when a future slice adds `include: [resources/*.yml]`.
