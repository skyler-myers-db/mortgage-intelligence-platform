---
name: prestop-probes
description: deploy.sh pre-stop probes (Supervisor retirement ack, reserved names, Genie group + verifier grants, unresolvable principals) run before the lease; paychex facts, residuals and traps
metadata:
  type: project
---

`tools/databricks/deploy_prestop_probes.py` (orchestration, Supervisor and
principal checks) and `tools/databricks/prestop_genie_grants.py` (Genie ACL
reads, per-source describe and remedies) run read-only from
`deploy_step_automation_identities.sh`, after the M2M credential checks and
before the signed lease and the first `APP_FAIL_CLOSED_ARMED=1`, so known
post-step-0a failures refuse before the run stops the App (post-W5 lane L4,
2026-10-27). `MIP_RETIRE_HUMAN_SUPERVISOR=<id>` (the exact, case-sensitive id,
never a boolean) acknowledges deleting a canonical Supervisor the runtime SP
did not create. It is checked three times: the probe, `pin_journal`, and
`retire_pinned_supervisor` (only while the old agent still exists).

**Why:** with no signed blue, any failure after step 0a leaves the live App
stopped; paychex has a user-created "Mortgage Growth Agent", six
Genie spaces granting `users`, and no runtime or verifier SP (checked
2026-10-27). Three of the six `users` grants are INHERITED CAN_MANAGE from
a `/directories/<object id>` parent, which is `/Workspace/Shared`.

**How to apply:**
- An inherited Genie grant cannot be revoked on the space. Never write a remedy
  that sends it to the space, and never suggest editing `/Workspace/Shared`'s
  `users` ACL casually (it affects every workspace user): the probe carries
  `inherited_from_object` and builds the remedy per kind (direct / inherited /
  both). A mixed direct+inherited entry is NOT "direct".
- An unresolvable runtime/verifier SP (0 or 2+ matches, no SCIM id) or an App
  with an incomplete identity is a refusal in the same pass, still audited for
  `users`; only read errors (ACL, apps.get other than NotFound, SCIM walk)
  propagate. Keep it that way so the operator sees every refusal at once.
- Test trap: every early probe test fed `resolve_groups` an EMPTY mapping, so
  discarding its result or passing the label instead of the SCIM id survived
  174 tests. Any principal-wiring change needs a test with a resolver that
  puts ONE principal in a non-`users` group and asserts the call list.
- `tools/databricks/cutover_agent_runtime_supervisor.py` sits at ~895 of the
  900-line hard limit (`>=900` fails): put new logic in a helper module.
- The probe refuses the WHOLE reserved family (`<canonical> [mip-agent-runtime]`,
  `<canonical> [mip-agent-runtime-<12hex>]` + optional `-mq1`) for non-runtime
  creators (any non-runtime SP or person), because the current contract hash
  needs the governed Genie space, known only after the post-gold rebind. A
  parity test pins it to `reconcile_historical_agent_endpoints._supervisor_family`.
- The verifier's own principal grants (direct or inherited) are probed, not
  only its group grants (`--forbid-all-genie` needs no space id). App/runtime
  direct grants need the governed space, so they stay post-stop.
- The agent-proxy SP is NOT in the probe's principal set on purpose: its Genie
  access runs through deploy-managed proxy groups whose lifecycle the probe
  cannot judge, so adding it would refuse the deploy's own managed grant.
- Admin SCIM walk (`resolve_effective_groups`) DOES find the App SP in `users`
  even though the SP's own `groups` projection omits it; the probe also unions
  `users` unconditionally.
- docs/deployment.md trap: inserting a `###` heading mid-paragraph reparents every
  later section; `test_the_probe_section_does_not_reparent_unrelated_deployment_content`
  pins the structure. Keep the probe section the last H3 before `## Presenter mode`.
- Residuals left open: App SP access to unrelated serving endpoints
  (`mip-agent-gateway`) still fails post-stop; AIM-only nested groups are invisible
  pre-stop (no `/Me` read for runtime/verifier); a journal whose canonical name
  differs from `MIP_AGENT_SUPERVISOR_NAME` refuses only at the retirement step,
  after any pinned old Gateway step (retire() runs `_delete_pinned_gateway` first).
- In an isolated worktree, the harness refuses compound shell commands that mix
  `git`/`python -m pytest` with heredocs, variables or `$(...)`; put edits and
  loops in a scratchpad script and run plain commands, one per call.
