---
name: mutation-harness-default-profile
description: a mutation or test that builds a bare WorkspaceClient() reads ~/.databrickscfg DEFAULT, which is pr105; pin DATABRICKS_CONFIG_FILE to a missing path in the harness env
metadata:
  type: feedback
---

When a mutation swaps a deployer-client helper (`deployment_workspace_client()`) for a bare
`WorkspaceClient()`, or a test path reaches one unmocked, the SDK resolves `~/.databrickscfg`
DEFAULT. On this machine DEFAULT is the pr105 workspace, which agents must never call.

**Why:** the hard limits forbid any Databricks call outside `-p paychex`, and a mutation run
inherits the shell env (no DATABRICKS_* vars are set, so the SDK falls back to DEFAULT). Found
2026-10-27 while mutation-checking `tools/databricks/verify_scheduled_refresh_state.py`.

**How to apply:** give every mutation subprocess `DATABRICKS_CONFIG_FILE=/nonexistent/databrickscfg`
(and no DATABRICKS_HOST/TOKEN), so a bare client fails at construction with no network call. Same
for ad-hoc scripts: set `DATABRICKS_CONFIG_PROFILE=paychex` explicitly before importing anything
that may build a client. Related: [[dab-complex-variable-channels]].
