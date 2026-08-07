---
name: r6-09-health-disclosure-scope
description: R6-09's threat model is ANONYMOUS recon of the public Apps URL only; the marginal-exposure test for any new authenticated-health key is whether /api/lineage/manifest or /api/data-estate already leaks it
metadata:
  type: project
---

R6-09 (commit `a34ffe41`, swarm cycle 13) split `/api/health` to close **unauthenticated
reconnaissance of the public Databricks Apps URL** — nothing broader. Anonymous callers get
exactly `{status, mode}`; the diagnostic body (warehouse ids, app_env, breaker flap counts,
identity-fallback counters) moved behind admin RBAC at `/api/admin/health`.

**Why:** the Databricks Apps URL is publicly reachable because the platform LB does anonymous
liveness probes there. An external scanner was getting free infra intel. R6-09 did NOT establish
a general "authenticated callers should see less" principle — the authenticated body deliberately
still carries dependency + breaker state for the degraded-state UI.

**How to apply:** when someone proposes adding a key to the AUTHENTICATED health body, the test is
*marginal* exposure, not absolute sensitivity. Two routes are the benchmark because they have
**no FastAPI-layer auth dependency at all** and are therefore strictly more reachable than health's
authenticated branch (which at least requires a trusted `X-Forwarded-Email`):
- `backend/api/lineage.py` → `/api/lineage/manifest` — zero deps; emits `{host}/explore/data/...`
  for every node via `catalog_explorer_url_for()`.
- `backend/api/data_estate.py` → `/api/data-estate` — `AdminRulesServiceDep` is a plain service
  factory (`get_admin_rules_service`), NOT an RBAC gate; emits the same URLs.
`backend/main.py` adds no auth middleware (Backpressure, VisitTracking, CorrelationId,
SecurityHeaders, GZip only). Auth on those routes comes from the Databricks Apps edge, which 401s
before FastAPI sees the request. If a proposed key is already derivable from those payloads, it adds
zero marginal exposure and R6-09 is not implicated. Verify the anonymous branch is untouched and
that a test pins `set(body.keys()) == {"status","mode"}` with the new value configured.

**Workspace host is config, not a secret in this repo:** the literal is committed in
`databricks.yml` as the `&default_host` anchor plus three `workspace.host:` references, appears in
~9 committed docs, and `docs/governance-real-data-review.md` classifies `DATABRICKS_HOST` as "not
secrets by themselves." Databricks Apps auto-injects it into the runtime. Do not treat a proposal
to surface it as a secrets-disclosure question.

Related: [[address-lookup-governance-standard]], [[ai-gateway-probe-proof]]
