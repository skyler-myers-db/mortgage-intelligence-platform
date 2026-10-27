---
name: bundle-experiment-name-rewrite
description: Databricks CLI rewrites every bundle experiment name as dir + "/" + name_prefix + base in EVERY target and mode; a workspace-root name renders "//name"
metadata:
  type: project
---

CLI v1.10.0's presets step rewrites each `resources.experiments.*.name` as `path.Dir(name) + "/" + name_prefix + path.Base(name)`, in every target, not only `mode: development` (the prefix is just empty elsewhere). So `/mip-lead-scoring` rendered `//[dev <user>] mip-lead-scoring` (dev) and `//mip-lead-scoring` (prod, no-mode). Proven 2026-10-27 with read-only `bundle validate -p paychex` of the repo bundle and a scratch bundle with dev/prod/no-mode targets. Fixed on post-w5/deploy-hardening by moving it to `/Shared/mip-lead-scoring${var.install_suffix}` (pinned by tests/unit/test_bundle_experiment_path.py).

**Why:** a root-level name looked fine in YAML and in `-t ci`, and nobody looked at the rendered JSON; the readiness audit only caught it by reading validate output.

**How to apply:** for any bundle name that is a workspace path (experiments), always read the rendered `bundle validate -o json` value, never the YAML. Keep the parent one level under a directory that exists in every workspace (`/Shared`); deeper parents break a fresh zero-click apply (24b502ce), and a `${workspace.current_user.userName}` template makes `-t ci` authenticate. The paychex live experiment is still `/Shared/mip/[dev <user>] lead-scoring` until the first governed apply renames it.
