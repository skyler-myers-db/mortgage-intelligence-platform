---
name: forked-anchor-ci-target
description: Post-W5 L5 pr105-host CI staging target - it shares the live UC metastore and account (only workspace-scoped objects are separated), why forked anchor still beat install-scoped names, the warehouse-witness revoke guard, and hidden Databricks-call/pin traps
metadata:
  type: project
---

The CI staging install (deploy-dev.yml) moved to a bundle target `pr105-host`
with its own `&ci_staging_host` anchor (branch post-w5/forked-anchor-target,
2026-10-27). deploy.sh's workspace-auth bind refuses any host but the
target's, and the reserved SPs are resolved by name in that workspace.

**It is NOT a separate metastore or account.** Read-only paychex reads
(2026-10-27): the live metastore holds `mip_pr105_staging` and another
team's catalog (its grant revoked via a pr105 credential 2026-08-03); `mip`,
`mip_app_state`, `cotality_mortgage_data` are OPEN there; 12 ISOLATED
catalogs, none bound to pr105 (workspace 7474645995341779 on record). The
forked anchor separates only workspace-scoped objects (App, warehouse,
endpoints, Genie, secret scopes, Lakebase, `users` group, SP assignments).
**How to apply:** catalog-scoped names (MIP_DEFAULT_CATALOG,
MIP_LAKEBASE_SYNC_CATALOG, Gateway prefix/family) and the foreign-UC audit
are PRIMARY isolation, never "defence in depth"; any grant revoke or binding
change for the pr105 bootstrap is metastore-wide and hits the live workspace.

**Identity separation is an owner invariant, not code.** SPs are
account-level; sharing needs an owner to assign one workspace's reserved SP
to the other AND configure its client id (duplicate-name and app-id-mismatch
refusals catch the rest). paychex held none of the seven reserved names on
2026-10-27.

**Why forked anchor, not install-scoped reserved names:** both share the
metastore, so the UC blockers are the same either way; install-scoped names
additionally keep the CI install in the live workspace (Genie `users` grants,
shared warehouse/endpoint ACL surface) and rework the identity contract in 3
modules.

**Verifier obsolete-endpoint revoke guard:** keyed on the verifier's warehouse
ACL (unsuffixed live grant), NOT endpoint creator: a shared runtime creates
both installs' endpoints, so creator cannot separate them.

**Traps found:**
- `tools/verify_scaffold.py` runs `databricks apps get mip-app` with ambient
  auth unless `CI=true`; run it with `CI=true` under a no-Databricks-call rule.
- `bundle_env.py` always exports `BUNDLE_VAR_genie_space_id`, so the dev
  target's Genie pin binds only for a bare `databricks bundle` call. The pin
  was stale (01f18188... = staging, from 441588f6) until this lane; live is
  01f13d4968af1b249dc388fd5b18b195.
- `bundle validate` on a `git archive` tree fails on missing `sql/_rendered/*`
  until `tools/render_sql.py --catalog mip` runs there, but still emits the
  JSON render; never validate `prod` (pins DEFAULT = pr105) or `pr105-host`.
- deploy-dev.yml comments inside `run: |` are part of the run string: prove a
  comment-only edit with a non-comment diff, not a YAML data compare.

**Round-2 review lessons (2026-10-27):**
- Never name other teams' catalogs or people (catalog owners) in product
  docs/tests: count them by privilege class and give the read-only inventory
  command instead; pin with a backticked-token allowlist.
- The live dev App has NO governed deploy/recovery path (readiness NO-GO):
  deploy.sh never creates principals, and provision_m2m_oauth's only sink is
  repo-level GitHub secrets that belong to the CI install. Never write "the
  live App recovers through deploy.sh"; route to the owner.
- A dict merge `{**env, KEY: x}` was the only guard that the checked target
  equals the CLI target; drop the ambient key first and pin both directions.

Related: [[project_deploy_script_sliced_layout]], [[w5b-integration-lessons]].
