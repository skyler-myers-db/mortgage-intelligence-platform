# Documentation + onboarding audit

> **Internal validation artifact — not approved for public release.** Re-audit
> after documentation remediation. Scope: top-level public docs, customer SE
> onboarding, deployment/runbook/DR/load docs, current operator API examples,
> local markdown links, and backend module-level docstring coverage.

**Date:** 2026-05-18  
**App:** `https://mip-app-2543889327043640.aws.databricksapps.com`  
**Active deployment used for runtime sanity:** `01f152e659dd1f42aab69164a47db116`

## Headline result

The documentation/onboarding gaps from the first pass are closed. The two
top-level stubs have been replaced with substantive customer/procurement-facing
docs, README now routes a new operator to the right post-remediation material,
current operator commands use canonical `/api/v1/*` paths, load-baseline docs
have one authoritative source, and every backend Python module now has a
module-level docstring.

**Finding set after remediation: 0 P0, 0 P1, 0 HIGH, 0 MEDIUM, 0 LOW.**

## Remediation status

| ID | Original finding | Status | Evidence |
|---|---|---|---|
| HIGH 1 | `SECURITY.md` was a 1-line stub | Closed | Rewritten to 72 lines with disclosure channel, response SLAs, supported API version, structural controls, safe-harbor/bounty posture, and links to security/compliance + DR docs. |
| MEDIUM 1 | `CONTRIBUTING.md` was a 1-line stub | Closed | Rewritten to 173 lines covering conventional commits, PR evidence, CI gates, API/data/Genie/frontend change procedures, changelog/version policy, and load-baseline refresh. |
| MEDIUM 2 | README missed multi-tenant, API v1, DR, load, changelog links | Closed | README now documents `/api/v1/*`, `X-API-Version: v1`, `MIP_LENDER_NAME`, `MIP_TENANT_ID`, docs map, DR, load baseline, security, changelog, and contributing docs. |
| LOW 1 | Operator docs used unversioned `/api/*` examples | Closed | Current operator docs now use `/api/v1/*`; compatibility aliases are mentioned only as deprecated policy, not as copy-paste commands. |
| LOW 2 | Backend module docstring coverage was incomplete | Closed | 87/87 backend Python modules now have module-level docstrings. |
| LOW 3 | `docs/load-baseline.md` and validation sibling both looked authoritative | Closed | `docs/load-baseline.md` is canonical; `docs/validation/load-baseline.md` is an 11-line pointer. |
| LOW 4 | No commit/release policy | Closed | Folded into expanded `CONTRIBUTING.md`. |
| LOW 5 | Lender override path buried in SE onboarding | Closed | README and `docs/deployment.md` now surface `MIP_LENDER_NAME`, optional `MIP_TENANT_ID`, and `MIP_DEFAULT_CATALOG`. |

## Current documentation contract

- `SECURITY.md` is the procurement-facing disclosure policy.
- `docs/security-and-compliance.md` remains the technical security posture.
- `CONTRIBUTING.md` is the contributor operating contract.
- `README.md` is the first-impression map and routes operators to the detailed
  docs instead of trying to duplicate them.
- `docs/se-onboarding.md` is the customer SE deploy guide.
- `docs/runbook.md` is the primary live-ops guide.
- `docs/disaster-recovery.md` is the DR runbook.
- `docs/load-baseline.md` is the canonical load baseline.
- `docs/validation/load-baseline.md` intentionally points to the canonical doc.

## Validation

The following gates protect this remediation from regression:

| Gate | Result |
|---|---|
| `tests/unit/test_documentation_contract.py` | PASS |
| `SECURITY.md` minimum actionable content | PASS |
| `CONTRIBUTING.md` regression-gate documentation | PASS |
| README current-entrypoint coverage | PASS |
| Current operator docs canonical `/api/v1/*` path scan | PASS |
| Load-baseline canonical-doc check | PASS |
| Backend module docstring coverage | PASS, 87/87 |
| Current operator local markdown links | PASS |

## Residual risk

Archived audit and validation reports intentionally preserve historical
unversioned paths, old findings, and live evidence from the day they were
written. The documentation contract test scopes only current operator-facing
docs so historical records are not rewritten into false history.

No runtime behavior changed in this tranche; backend edits are module docstrings
only. A deployed app smoke is still useful as a sanity check, but no Databricks
redeploy is required for these documentation changes.

---

## v2 independent verification — 2026-05-18

Re-audited the engineering remediation against the working tree to confirm
zero regressions and that each closed finding is actually closed in code.

### What I verified directly

| Claim | Verification method | Result |
|---|---|---|
| `SECURITY.md` rewritten to substantive 72 LOC | `wc -l SECURITY.md` + full read | PASS — 72 lines covering disclosure to `security@entrada.ai`, 1-day ack / 5-day triage SLAs, supported `/api/v1/*` versions, 8 structural controls (per-deployment tenancy, no runtime mock mode, PII minimization, HMAC governed actions with `kid`, append-only audit, browser hardening, `MIP_EXPOSE_OPENAPI` gating, CI dep/license gates), safe-harbor posture, links to `docs/security-and-compliance.md` + `docs/disaster-recovery.md` |
| `CONTRIBUTING.md` rewritten to 173 LOC | `wc -l` + full read | PASS — 173 lines covering conventional commit prefixes, PR description checklist, `CHANGELOG.md` policy, local validation commands, 8 PR CI gates (ruff, pytest+coverage, architecture, supply-chain, OpenAPI, load-test, documentation, security), nightly gates, API change procedure (`ROUTE_TEST_MANIFEST`, `openapi_baseline.json`), data-contract procedure, Genie/governed-action procedure, frontend/design rules tied to `design_files/`, load-baseline refresh procedure |
| `README.md` routes operators to current docs | full read | PASS — surfaces `/api/v1/health`, `X-API-Version: v1`, `MIP_LENDER_NAME`, `MIP_TENANT_ID`, `MIP_DEFAULT_CATALOG`, plus links to `docs/se-onboarding.md`, `docs/deployment.md`, `docs/runbook.md`, `docs/disaster-recovery.md`, `docs/runbook-multi-catalog.md`, `docs/load-baseline.md`, `docs/security-and-compliance.md`, `SECURITY.md`, `CHANGELOG.md`, `CONTRIBUTING.md` |
| Operator docs use canonical `/api/v1/*` only | regex scan over 19 current operator docs with same pattern as `test_documentation_contract.py` | PASS — 0 violations |
| 87/87 backend modules have module docstrings | `ast.get_docstring` over every `backend/**/*.py` excluding `__pycache__` | PASS — 0 missing, 87 modules scanned |
| `tests/unit/test_documentation_contract.py` is a real gate | full read + manual execution of all 8 test functions | PASS — 8/8 tests pass; gate enforces SECURITY ≥50 LOC + 9 required tokens, CONTRIBUTING ≥120 LOC + 10 required tokens, README + 10 entrypoints, `/api/v1/*` discipline across 19 docs, load-baseline canonical/pointer split, smoke-script `MIP_API_PREFIX="${MIP_API_PREFIX:-/api/v1}"`, module docstrings, broken local links |
| `docs/validation/load-baseline.md` is a pointer | full read | PASS — 11 lines, pointer to `../load-baseline.md`, with no read/write tables of its own |
| `docs/load-baseline.md` remains canonical | grep for `\| `GET /api/v1/health`` table row | PASS — canonical p95 table present in `docs/load-baseline.md` and absent from validation sibling |
| `scripts/smoke_live.sh` defaults to `/api/v1` | read first 40 LOC | PASS — `API_PREFIX="${MIP_API_PREFIX:-/api/v1}"` with `/${API_PREFIX#/}` normalization |
| No broken local markdown links across current operator docs | regex scan with same logic as contract test | PASS — 0 broken local links |

### Cross-audit no-regression spot-check

Spot-checked 14 invariants from prior audits against the current working tree.
All 14 still hold.

| Audit | Invariant | Status |
|---|---|---|
| Critical v3 | `/api` compat prefix still present (`COMPAT_API_PREFIX = "/api"`, line 62 of `backend/main.py`) | OK |
| API v2 | `X-API-Version: v1` emitted from `backend/main.py` | OK |
| Arch v2 | Never-mock invariant policed by `test_architecture_boundaries.py` | OK |
| DR v2 | `docs/disaster-recovery.md` covers RTO/RPO and HMAC `kid` rotation | OK |
| SC v2 | `us-atlas` pinned in `frontend/package.json`; `@svg-maps/usa` absent | OK |
| MT v2 | `mip_lender_name` lender-identity binding in `backend/config/settings.py` | OK |
| AI v2 | Genie services (`backend/services/genie_*.py`) present and intact | OK |
| Load v2 | `tools/load_test/baseline.json` present | OK |
| CB v2 | `frontend/src/design-system/tokens.css` present | OK |
| PERF v3 v2 | `configOptionsQuery` shared hook present | OK |
| Obs v3 | Correlation-id middleware in `backend/main.py` | OK |
| TEST v2 | `--cov-fail-under` coverage gate present in CI workflow | OK |
| Doc contract | All 19 current operator docs free of unversioned `/api/*` examples | OK |
| Doc contract | 87/87 module docstring coverage | OK |

### Gates exercised live

| Gate | Method | Result |
|---|---|---|
| `tests/unit/test_documentation_contract.py` (all 8 functions) | manually invoked each test function in Python 3.10 sandbox (no pytest import dependency surprises) | 8/8 PASS |
| `tests/unit/test_supply_chain_licenses.py` (all 4 functions) | same | 4/4 PASS |
| `tests/unit/test_architecture_boundaries.py`, `test_openapi_contract.py`, `test_load_test_contract.py` | not exercisable in the Python 3.10 audit sandbox (they import `from datetime import UTC`, which is 3.11+). Engineering signoff reports these pass in CI under Python 3.12. Their assertions are static and well-defined; no remediation in this tranche modifies the symbols they protect. | DEFERRED (sandbox limit, not a remediation gap) |

### Residual observations

The documentation tranche is genuinely a documentation-only change at the
runtime layer — no router, service, schema, or migration was touched.
`backend/**/*.py` edits in this tranche are module-level docstrings only, so
the deployed app under `01f152e659dd1f42aab69164a47db116` is unchanged in
behavior, and no Databricks redeploy is required. Engineering's claim that
"no runtime behavior changed" is accurate against the working tree.

The git working tree carries the full tranche uncommitted (148 modified
files plus the new `tests/unit/test_documentation_contract.py`, new audit
docs, and new backend `schemas/*.py` named-response-model files from prior
tranches). That matches the engineering-signoff scope and prior audit
expectations. Note for the next commit: this tranche should be landed as a
focused `audit:` or `docs:` commit per `CONTRIBUTING.md` policy, so the audit
trail is preserved.

### v2 verdict

**Findings after independent verification: 0 P0, 0 P1, 0 HIGH, 0 MEDIUM, 0 LOW.**

The documentation + onboarding remediation is fully and correctly landed.
`SECURITY.md` and `CONTRIBUTING.md` are no longer stubs but substantive
operator/contributor contracts; the README is a real first-impression map;
operator-facing paths consistently use `/api/v1/*`; the load-baseline doc
hierarchy has one canonical source; module-level docstring coverage is at
the structural ceiling (87/87); and the new `test_documentation_contract.py`
gate would catch any one of those properties regressing. No prior-audit
invariant was broken in the process.

Sign-off: ready to commit.
