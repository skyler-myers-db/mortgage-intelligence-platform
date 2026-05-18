# Security Policy

Module 0 handles mortgage borrower intelligence and governed workflow state.
Treat every deployment as security-sensitive even when the bundled demo data is
synthetic.

## Reporting Vulnerabilities

Report suspected vulnerabilities privately to `security@entrada.ai`. If your
customer contract names a dedicated security contact, use that contract channel
instead.

Include:

- The affected deployment, app URL, or repository commit when known.
- The vulnerable endpoint, route, SQL object, or workflow.
- Reproduction steps with sanitized evidence only.
- Impact assessment, including whether borrower PII, secrets, audit integrity,
  or cross-lender isolation may be affected.

Do not attach real borrower PII, Databricks tokens, Lakebase credentials,
customer secrets, raw Cotality identifiers, or exploit payloads containing
customer data. If sensitive artifacts are required, request the current
Entrada-approved secure transfer channel or PGP key before sending them.

Expected response:

- Acknowledgment within 1 business day.
- Initial triage response within 5 business days.
- Severity, owner, and remediation plan after reproduction.
- Coordinated disclosure timing agreed with the reporter and affected customer.

No public bug bounty or broad safe-harbor program is currently offered. Good
faith research against your own authorized deployment is welcome; do not test
against customer environments, shared Databricks workspaces, or production data
without written authorization.

## Supported Versions

The supported public contract is API `v1`, served from canonical
`/api/v1/*` paths with `X-API-Version: v1`. Deprecated `/api/*` compatibility
aliases remain available during the Module 0 transition window, but new clients
and runbooks must use `/api/v1/*`.

Security fixes are applied to the current active Module 0 release line. If a
customer runs a fork or older deployed snapshot, the first remediation step is
to reproduce on the current tree and then deploy the patched snapshot with
`./scripts/deploy.sh`.

## Structural Security Controls

- Per-deployment tenancy: one Databricks workspace, Unity Catalog catalog,
  Lakebase instance, Genie space, and HMAC secret per lender.
- No runtime mock mode: production code has no `MIP_MOCK_MODE`/`USE_MOCKS`
  fallback and never silently swaps to fixtures.
- PII minimization: raw CLIP, Owner Link, owner names, addresses, phone, email,
  SSN-like values, and competitor servicer names are denied or redacted at API,
  SQL-policy, audit, and Genie row-output boundaries.
- Governed Genie actions: confirmation tokens are HMAC signed, include key IDs,
  bind actor/action/cohort/criteria/source assets/request IDs, and support
  dual-secret rotation.
- Audit integrity: Lakebase audit rows are append-only, carry correlation IDs,
  and state-changing workflows fail closed if the audit write cannot complete.
- Browser hardening: API responses include HSTS, content-type, frame,
  referrer, permissions-policy, and CSP headers.
- OpenAPI exposure is off by default through `MIP_EXPOSE_OPENAPI`; customer
  operators can enable it only for controlled contract review.
- Dependency and license gates run in CI: gitleaks, bandit, pip-audit,
  npm audit, supply-chain license checks, and third-party notice coverage.

Technical details live in [`docs/security-and-compliance.md`](docs/security-and-compliance.md).
Operational incident recovery lives in [`docs/disaster-recovery.md`](docs/disaster-recovery.md).
