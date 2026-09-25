# Supply-Chain + Dependency Audit

> Internal validation artifact — not approved for public release. Updated after
> remediation of the map-data licensing blocker and dependency-lock hygiene
> findings.

## Current Verdict

The supply-chain posture is shippable for commercial Module 0 deployment after
this remediation pass.

- Browser-shipped production dependencies have no known commercial-use license
  blockers.
- Frontend `npm audit`, 2026-09-25, after the wave-4 test-infra batch
  (Playwright 1.63.0 and oxlint 1.85.0; no other package moved): 0
  advisories at any level (0 low, 0 moderate, 0 high, 0 critical) across 251
  packages, read at both the `--audit-level=high` gate and the advisory
  `--audit-level=moderate` level. The batch adds oxlint and its 19 optional
  platform bindings and drops Playwright's nested fsevents (232 -> 251).
- Frontend `npm audit`, 2026-09-24, after the wave-2 minor dependency batch
  (vitest 4.1.11, React 19.3.0, React Router 8.4.0, Vite 8.3.0,
  @vitejs/plugin-react 6.1.1, @axe-core/playwright 4.13.0,
  typescript-eslint 8.70.1): 0 advisories at any level (0 low, 0 moderate,
  0 high, 0 critical) across 232 packages. Before the batch it reported 2
  moderate dev-only advisories (vitest and @vitest/mocker,
  GHSA-82fw-gwwq-j7x9, fixed in vitest 4.1.11). This is a dated result, not
  a standing claim. The CI gate is `npm --prefix frontend audit
  --audit-level=high` (the `npm audit` step of the `security-scan` job in
  `.github/workflows/ci.yml`): a high or critical advisory fails CI;
  moderate and low advisories in dev-only tooling are tolerated and reviewed
  at the next dependency batch.
- Backend `pip-audit`, 2026-09-24, over the `uv.lock` pins
  (`pip-audit -r uv.lock --no-deps --disable-pip --strict`): no known
  vulnerabilities. CI runs `pip-audit -r requirements.txt --strict
  --ignore-vuln GHSA-h7x2-h6g9-p789` and carries that one ignore: the MLflow
  tracking-server SSRF has no patched release and is unreachable here (see
  the 2026-09-08 addendum below and the proof at the ci.yml call site). Its
  review date is 2026-10-08.
- The prior restricted commercial-use map-data dependency was removed from
  `frontend/package.json`, `frontend/package-lock.json`, and production source.
- State map rendering now uses `us-atlas@3.0.1` (`ISC`) plus the existing
  `topojson-client@3.1.0` (`ISC`) adapter path.
- `requirements.in` carries the direct Python dependency contract, `uv.lock`
  pins the transitive resolution, and `requirements.txt` installs through that
  lock as a constraint file.
- `docs/THIRD_PARTY_LICENSES.md` tracks the weak-copyleft and attribution
  obligations that procurement teams are likely to ask about.

## Remediated Findings

| Finding | Remediation |
|---|---|
| P0 commercial-license blocker in the prior state geography package | Replaced with `us-atlas@3.0.1` (`ISC`). Added a shared state-topology adapter so Home, Segment Intelligence, and Genie maps keep the existing lowercase-USPS map contract. |
| Missing supply-chain license gate | Added `tests/unit/test_supply_chain_licenses.py`, which fails production browser dependencies containing commercial-use blockers and asserts the retired map package cannot return through the manifest or lockfile. |
| LGPL / MPL notices were undocumented | Added `docs/THIRD_PARTY_LICENSES.md` covering production `psycopg` LGPL and dev/test MPL packages. |
| `uvicorn` and Databricks SQL connector lagged behind current releases | Bumped `uvicorn[standard]` to `0.47.0` and `databricks-sql-connector` to `4.2.6`; local install and targeted backend tests validate import/runtime compatibility. |
| `uv.lock` was a placeholder | Replaced it with a generated universal requirements-style lock; `requirements.in` is the refresh input and `requirements.txt` installs it through the lock as a pip constraint file. |

## Validation Gates

Run after any dependency change:

```bash
npm --prefix frontend audit --audit-level=high      # the CI gate
npm --prefix frontend audit --audit-level=moderate  # advisory local read; not a gate
npm --prefix frontend run test -- USChoroplethMap
npm --prefix frontend run build
./.venv/bin/pip-audit -r requirements.txt --strict --ignore-vuln GHSA-h7x2-h6g9-p789
./.venv/bin/python -m pytest -q tests/unit/test_supply_chain_licenses.py
./.venv/bin/python -m pytest -q tests/unit/test_error_sanitizer.py tests/unit/test_health_endpoint.py
```

`tests/unit/test_supply_chain_licenses.py` pins this section to CI: the
`--audit-level` named as the gate here must be the one ci.yml runs, and every
advisory ci.yml ignores must be named in this document.

## Dependency upgrade checklist

Frontend dependencies move in batches of exact pins (`npm install
<pkg>@<version> --save-exact` for the named packages only, never `npm
update`), and `npm --prefix frontend ci` must run clean on the result. Before
a batch lands, check each of these:

- **react-router `UNSAFE_DataRouterContext`.** `UnsavedChangesGuard` reads
  this unstable export to detect a data router. It is pinned by
  `frontend/src/lib/dependencyContracts.test.ts`, together with
  `createBrowserRouter`, `useBlocker` and `react-router/dom`'s
  `RouterProvider`. A red test there means the guard needs a new detection
  path before the bump can land.
- **React Compiler compatibility.** A React, compiler or
  `@vitejs/plugin-react` bump can change what the compiler memoizes, and a
  bailout is silent. Run the whole vitest suite (`npm --prefix frontend run
  test`), not a subset, and `node tools/react_compiler_coverage.mjs
  --only-issues`, and compare the summary with the previous batch.
- **Playwright is held to the VRT container image.** `@playwright/test`
  stays at the version of the pinned visual-regression image
  (`mcr.microsoft.com/playwright:v1.63.0-noble`; both the `e2e-visual`
  `container.image` and its `MIP_VRT_IMAGE`, pinned by
  `tests/unit/test_ci_frontend_gates.py`). A Playwright bump moves the image
  in the same change, and the baselines are regenerated once, from that
  bump PR's pinned-image CI renders (never on a developer host).
- **oxlint is an exact dev pin with platform bindings.** `oxlint` 1.85.0
  (MIT) runs the jsx-a11y ratchet (`tools/oxlint_ratchet.mjs`, see
  docs/testing.md). Its native binaries are the `@oxlint/binding-*`
  packages, declared as optional dependencies (npm installs only the host's
  one); its optional peers `vite-plus` and `oxlint-tsgolint` are never
  installed. Refresh the lock with `--package-lock-only` over no
  `node_modules`: regenerating it over an installed tree can drop the other
  platforms' optional entries, which
  `tests/unit/test_supply_chain_licenses.py` catches (the linux-x64-gnu
  binding CI and the VRT container use, the darwin-arm64 one for local
  work). An oxlint bump names any new jsx-a11y rule in
  `frontend/.oxlintrc.json` explicitly and re-runs `node
  tools/oxlint_ratchet.mjs --ratchet frontend/oxlint-baseline.json`, which
  records the new version.
- **typescript-eslint gates TypeScript 7.** typescript-eslint 8.70.1 peers
  `typescript >=4.8.4 <6.1.0`. TypeScript 7 waits until a typescript-eslint
  release admits it; check the peer range with `npm view
  @typescript-eslint/parser peerDependencies`.
- **An axe-core minor can add rules.** Run
  `tests/e2e/fixture/axe.fixture.spec.ts` on the new version. New rule ids
  that fire are fixed in the lane that owns the surface, or the bump is held;
  they are never added to the `KNOWN_VIOLATIONS` ratchet.
- **Update signal.** Dependency-update signal pending owner decision #7; no
  bot branch (existing contract, see `docs/modernization-todo.md`).

Additional manual checks:

```bash
./.venv/bin/python -m pytest -q tests/unit/test_supply_chain_licenses.py
npm --prefix frontend ls us-atlas topojson-client --depth=0
npm --prefix frontend ls '<retired-noncommercial-map-package>' --depth=0
```

The final command should fail with an empty dependency tree because the retired
map package is no longer installed.

---

## v2 re-validation — 2026-05-15

Independent Cowork re-audit of the supply-chain remediation, with special attention to the map experience the user specifically asked me to verify ("ensure the app looks amazing and blows people away in every regard, including UX"). **Verdict: 0 P0, 0 P1, 0 MEDIUM, 0 LOW. Zero regressions. The new map renders dramatically better than the CC-BY-NC-4.0 package it replaces.**

### License blocker — closed in source, lockfile, and runtime

| Check | Result |
|---|---|
| `@svg-maps/usa` in `frontend/package.json` | **0 hits** |
| `@svg-maps/usa` in `frontend/package-lock.json` | **0 hits** |
| `@svg-maps/usa` in `frontend/src/**` | **0 hits** |
| `@svg-maps/usa` in `frontend/src/vite-env.d.ts` module declarations | **0 hits** (replaced with `declare module "us-atlas/*.json"`) |
| `frontend/node_modules/@svg-maps/` | **absent** |
| `us-atlas` license | **ISC** (Bostock-authored, permissive, commercially safe) |
| `topojson-client` license | **ISC** |
| `us-atlas@3.0.1` resolved from us-atlas.org/Census Bureau data | confirmed |

### The new map adapter is clean architecture

`frontend/src/components/mortgage/USStateMapData.ts` (22 lines) is a single, well-scoped adapter that does exactly one thing — lazy-load `us-atlas/states-albers-10m.json` plus `topojson-client`, decode the TopoJSON into a GeoJSON FeatureCollection, and pass it through the existing `buildUsaStateMapPayload` helper. The lowercase-USPS state ID contract is preserved via `USCODE_TO_FIPS` + `FIPS_TO_USCODE` in `USChoroplethMap.utils.ts:12-25`, so existing rollup lookups, drill links, and the Genie state-map normalization (`expect(illinois?.id.toUpperCase()).toBe('IL')`) continue to work unchanged.

The unit test `frontend/src/components/mortgage/USChoroplethMap.utils.test.ts:142-165` exercises the adapter end-to-end against the real `us-atlas` TopoJSON: 51 locations produced (50 states + DC), every path starts with `M`, viewBox is finite and positive, DC's display name is correctly humanized to "Washington, DC".

### Supply-chain license gate is a real gate

`tests/unit/test_supply_chain_licenses.py` (83 lines, 4 tests):

1. `test_frontend_production_dependencies_have_no_commercial_license_blockers` — walks every non-dev entry in `frontend/package-lock.json` and fails if `license` contains `agpl`, `gpl`, `lgpl`, `cc-by-nc`, `noncommercial`, or `commons clause`. Note: **also blocks LGPL on the frontend**, which is correct — LGPL is fine for Python (dynamic linking) but problematic for bundled browser JS. Backend's psycopg LGPL is not affected because the gate is frontend-only.
2. `test_svg_maps_noncommercial_package_is_not_in_the_frontend_contract` — asserts the retired package name is absent from both `package.json` (deps + devDeps) and every `packages` key in the lockfile. The package name is split (`"@svg-maps" + "/usa"`) so the test file itself doesn't trip the license scanner.
3. `test_third_party_license_notice_covers_weak_copyleft_and_map_data` — asserts `THIRD_PARTY_LICENSES.md` mentions `psycopg`, `LGPL-3.0-only`, `@axe-core/playwright`, `MPL-2.0`, `hypothesis`, `us-atlas`, `ISC`, and `topojson-client`. Each is present in the live doc.
4. `test_python_requirements_use_real_transitive_lockfile` — asserts `requirements.txt` references both `-c uv.lock` and `-r requirements.in`, that `uv.lock` is not a placeholder, that `uvicorn[standard]==0.47.0` and `databricks-sql-connector==4.2.6` are in `requirements.in`, and that the resolved lock pins `uvicorn==0.47.0`, `databricks-sql-connector==4.2.6`, `psycopg==3.3.4`, `opentelemetry-sdk==1.41.1`.

All four gates would catch a regression of their respective contract. They are the right shape.

### Python dependency hygiene

| Artifact | Status |
|---|---|
| `requirements.in` | Direct deps, exact pinned, 15 entries |
| `uv.lock` | 208 lines, autogenerated by `uv pip compile requirements.in --universal --format requirements.txt --output-file uv.lock` |
| `requirements.txt` | Two lines: `-c uv.lock` + `-r requirements.in` (lock-as-constraint install pattern) |
| `uvicorn` pin | 0.47.0 in `requirements.in` AND `uv.lock` (the prior 0.34.0 → 0.47.0 bump landed) |
| `databricks-sql-connector` pin | 4.2.6 in both places (the prior 3.7.0 → 4.2.6 major bump landed) |
| Lock provenance comment | First two lines of `uv.lock` document the generation command verbatim — a future reviewer can reproduce the lock exactly |

### Third-party license notice quality

`docs/THIRD_PARTY_LICENSES.md` is well-organized: separate sections for production runtime (`psycopg`, `us-atlas`, `topojson-client`) and dev/test tooling (`@axe-core/playwright`, `hypothesis`, `lightningcss`). The notice explicitly links the GNU LGPL 3.0 text URL for psycopg attribution. Section "Explicitly Prohibited In Production Browser Dependencies" cross-references the test gate. Procurement-grade.

### Live map experience — does it look amazing?

This is the part the user specifically asked me to verify. I drove the deployed app (`01f150e19b301b7db1850cf67e716569`, RUNNING/ACTIVE) and captured map renders on every surface that uses the choropleth.

**Segment Intelligence (`/segment-intelligence`).** Captured at 1440×900. The map carries the full lower-48 plus Alaska (compact inset, lower-left) and Hawaii (compact inset, mid-lower) under the **Albers USA projection** — which is the cartographic standard for U.S. choropleths and a substantial visual upgrade over the simpler Mercator-style outline that `@svg-maps/usa` shipped. State boundaries are crisp at this zoom, no jagged simplification artifacts, no missing geometry. Five footprint states (Washington, California, Illinois, Texas, Florida) sit in lighter blue against the dark-navy out-of-footprint base — clear visual separation of the live coverage versus the rest of the country.

The bottom-left legend reads "Borrowers in selection 6,235 · Lower → Higher · Colored by: opportunity within itm". The bucketing scale gradient is rendered in four steps. The header chip shows "6 counties · click to drill". Everything is on-brand with the design-system tokens (`var(--accent)`, `var(--ink-2)`, `--bg-2`, etc.).

Hover over Illinois opens an inline tooltip with the live state-level rollup: **Illinois · MARKETABLE BORROWERS 3,158 · AVG. OPPORTUNITY SCORE 61 · Filter: filtered by In the Money · Source: `mip.gold.state_rollup`**. The state-ID lookup (`Illinois` → FIPS `17` → USPS `il` → `loc.id === 'il'`) works end-to-end, confirming the lowercase-USPS contract preserved by `FIPS_TO_USCODE`.

**Home (`/`).** Map renders identically with the marketable-population coloring (5,156,184 borrowers in selection) and the agent action audit log alongside. Same beautiful Albers projection, no console errors, full Alaska/Hawaii visible.

**State → county drill-down.** Clicked Illinois on the Home map. The map smoothly redrew into a **detailed county-level view of Illinois** with all 102 counties rendered, breadcrumb `US › Illinois`, a coverage chip derived from the current refreshed footprint, and marketable-population color encoding still active. The county detail level is dramatically richer than the retired state-only map package could have shown. The new `us-atlas/counties-albers-10m.json` provides full county polygons with the same precision.

**Ask Genie state breakdown.** I ran the trusted suggestion "Break down in-the-money borrowers by current coverage state; which state leads?". Genie returned a structured answer in ~10s — inline prose, an "IN THE MONEY BORROWERS BY STATE" bar chart (IL 67,858, FL 19,010, TX 16,986, CA 16,706, WA 13,881, CO 1,079), a data table with avg opportunity score per state, the `trusted` chip, "Show proof" toggle, and two governed actions (open cohort in Lead Queue, create draft campaign) with State/Segment/result-count filter chips. Data values are **internally consistent** with what I saw in Segment Intelligence (CA at 16,706 ITM borrowers matches the parity-remediation note in `docs/validation/segment-count-parity.md`).

**Live runtime invariants probed via DOM:**

| Probe | Result |
|---|---|
| `svg path[role="button"][data-target-size-exempt="geographic-shape"]` count | **51** — exactly the 50 states + DC the adapter test asserts |
| `document.documentElement.scrollWidth - clientWidth` | **0** — no horizontal overflow |
| Body innerText `\\b(undefined\|NaN)\\b` match | **none** |
| `<html data-theme>` | `dark` |
| `<html data-accent>` | `bright` |
| `<html data-density>` | `comfortable` |
| Total page resources | 38 (modest, performance budget posture intact) |
| Console errors / warnings matching `error|warning|exception|undefined|NaN|svg-maps|us-atlas|topojson|TypeError|Failed` | **none** |

The map experience is **substantially more polished** than the prior `@svg-maps/usa` rendering would have been. Albers USA projection, county-level drill, lazy-loaded TopoJSON chunks, smooth hover interactions, on-brand color encoding, live data tooltips, full lowercase-USPS contract preservation. The remediation didn't just close a legal blocker — it **upgraded** the hero visual.

### Cross-audit no-regression sweep

| Audit | Spot-check | Status |
|---|---|---|
| Architecture | 0 router-to-router, 0 schema→service, 0 raw runtime logging, 0 InMemory in prod, 0 files ≥1000 LOC | ✅ All five gates green |
| Cross-browser | 6× `min-block-size: var(--sp-6)` rules in `components.css`, 2× `data-target-size-exempt="geographic-shape"` in `USChoroplethMap.tsx` | ✅ Closed |
| Security | OpenAPI gating via `mip_expose_openapi` at `backend/main.py:193-195`, SecurityHeadersMiddleware mounted | ✅ Closed |
| Compliance | `trg_action_audit_append_only` trigger at `lakebase/schema.sql:301-302` | ✅ Closed |
| Observability | `CorrelationIdMiddleware` mounted at `main.py:356`, `_request_validation_handler` at `main.py:430` | ✅ Closed |
| Performance | 38 total resources on Segment Intelligence load, 0 horizontal overflow | ✅ Within budget |
| Data quality | Genie state breakdown (CA=16,706) matches segment-count-parity remediation note | ✅ Coherent |

Zero regressions on any prior audit dimension.

### v2 verdict

**Approved with enthusiasm.** The P0 license blocker is closed in source, lockfile, runtime, and test gate. The new `us-atlas` map is not merely a "license-equivalent" swap — it is a visual upgrade that strengthens the hero geography surface the product brief calls out as "a hero surface, not a nice-to-have." County-level drill works, Albers projection is correct, all 51 paths render, lowercase-USPS contract preserved, real-data tooltips display, no console errors, no horizontal overflow, no runtime token leaks. The supply-chain posture is now **shippable for commercial Module 0 deployment**.

The remediation also threaded a quality lift through the rest of the dependency surface: `uvicorn` bumped to 0.47.0, `databricks-sql-connector` bumped to 4.2.6, `uv.lock` replaced from placeholder to a real autogenerated lock, transitive Python deps pinned for reproducibility, and a procurement-grade `THIRD_PARTY_LICENSES.md` lives in the repo for customer security reviews.

This was a clean tranche.

## 2026-09-08 addendum: PYSEC-2026-3552 ignore retired

PR #238 cleared the twelve fixable pip-audit advisories (gitpython 3.1.62,
sqlparse 0.6.0, thrift 0.24.0 via databricks-sql-connector 4.4.0) and left the
mlflow + cryptography move as a follow-up because both are also pinned in
`GATEWAY_MODEL_REQUIREMENTS`, the registered model's serving environment.
This follow-up moves them together and re-runs the registry integration test:

| Package | Before | After | Note |
|---|---|---|---|
| `mlflow` (+ `-skinny`, `-tracing`) | 3.15.1 | 3.16.0 | First release whose bound (`cryptography<51`) admits the 50.x fix. Does **not** close GHSA-h7x2-h6g9-p789: the `_create_gateway_secret` handler is byte-identical to 3.15.1's and the advisory has no patched release, so CI keeps that ignore with its server-side unreachability proof. |
| `cryptography` | 49.0.0 | 50.0.1 | Fixes PYSEC-2026-3552 (PKCS#7 EnvelopedData oracle). The CI `--ignore-vuln PYSEC-2026-3552` carve-out is retired. Repo usage is Ed25519 verification only. |

`uv.lock` was recompiled with `--upgrade-package` limited to these four
distributions; no other pin moved. CI's pip-audit step now carries a single
ignore, GHSA-h7x2-h6g9-p789, whose proof is unchanged.
