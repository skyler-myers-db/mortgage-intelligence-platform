# Supply-Chain + Dependency Audit

> Internal validation artifact — not approved for public release. Updated after
> remediation of the map-data licensing blocker and dependency-lock hygiene
> findings.

## Current Verdict

The supply-chain posture is shippable for commercial Module 0 deployment after
this remediation pass.

- Browser-shipped production dependencies have no known commercial-use license
  blockers.
- Frontend `npm audit`, 2026-10-27, on the post-W5 integration lock (Part A
  plus the live-gate, scheduled-refresh and deploy-hardening lanes): 0
  advisories at any level across 305 lock entries (npm's metadata `total`;
  19 prod, 287 dev, 46 optional), read with `npm audit --package-lock-only`
  and at the `--audit-level=high` gate. Main's lock at 550dcf97 reads 0
  across 290 by the same `total`; the lines below count prod plus dev, which
  is 291 for that lock and 306 for this one. The
  15 added entries are all dev-only, from the screen-reader lane's
  `@guidepup/virtual-screen-reader` 0.33.0 and its tree
  (`@testing-library/dom` 10.4.2, `@testing-library/user-event` 14.6.7,
  `aria-query`, `@types/aria-query`, `dom-accessibility-api`, `html-aria`,
  `pretty-format` 27.5.1, `react-is` 17.0.2, `lz-string`, `dequal`,
  `@babel/runtime` and three nested copies); nothing was removed, and the only
  other version change is the T1c re-take below. This is a dated result, not
  a standing claim.
- Frontend `npm audit`, 2026-10-26, after tail T1c (`@tanstack/react-virtual`
  3.14.19, the T1c re-take row below): 0 advisories at any level across the
  same 291 lock entries (19 prod, 272 dev, 46 optional; nothing added or
  removed), read with `--package-lock-only` at the `--audit-level=high` gate
  and the advisory `--audit-level=moderate` level. This is a dated result,
  not a standing claim.
- Frontend `npm audit`, 2026-10-26, after W5e tail T4 (below): 0
  advisories at any level across the 291 lock entries, read with
  `--package-lock-only` and on the installed tree at the `--audit-level=high`
  gate. Before T4 the same lock (and main's at 5e0fbe2c) reported 5
  dev-only advisories published 2026-10-20 to 2026-10-23, after every W5e
  commit: 3 high (happy-dom GHSA-834v-8h66-3h79; vite 8.3.0-8.3.2 via its
  bundled launch-editor and server.fs advisories; zod below 4.6.6,
  GHSA-mrhx-c7xc-m3w5, transitive through eslint-plugin-react-hooks) and 2
  moderate (vitest and @vitest/mocker 4.1.11). This is a dated result, not
  a standing claim.
- Frontend `npm audit`, 2026-10-01, after the W5b dependency batch (see the
  2026-10-01 W5b addendum below), read from the refreshed lock with
  `npm audit --package-lock-only`: 0 advisories at any level (0 low, 0
  moderate, 0 high, 0 critical) across 351 packages (19 prod, 333 dev, 46
  optional), at both the `--audit-level=high` gate and the advisory
  `--audit-level=moderate` level. The batch adds web-vitals and stylelint
  (with its 97 dev-only transitive packages). Re-read by the W5b integrator
  on the installed tree after `npm --prefix frontend ci` and the three holds
  below (`@tanstack/react-virtual`, `happy-dom`, the TanStack Query pair):
  `npm audit`, 0 advisories at any level across 351 packages (19 prod, 333
  dev, 46 optional).
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
- Backend `pip-audit`, 2026-10-28, after the databricks-sql-connector 4.8.0
  and oauthlib 4.0.0 bump (see the 2026-10-28 addendum below): CI's command,
  now `pip-audit -r requirements.txt --strict` with no ignore, reports no
  known vulnerabilities from the default PyPI service or from `-s osv`. The
  same command on the old lock reports oauthlib 3.3.1 under PYSEC-2026-4114
  and GHSA-3jp2-3mrg-2vxc (PyPI) and also PYSEC-2026-4113 (`-s osv`).
- Backend `pip-audit`, 2026-10-26, after the aiohttp, certifi, gitpython,
  graphql-core, mlflow, OpenTelemetry and pillow bumps (see the 2026-10-26
  addendum below): CI's command, now `pip-audit -r requirements.txt --strict
  --ignore-vuln GHSA-xpv3-w29h-x7cv --ignore-vuln GHSA-hj66-6f7g-4r5v`,
  reports no known vulnerabilities from the default PyPI service (1 ignored)
  and none from `-s osv` (2 ignored). Over the `uv.lock` pins with no
  ignores, both services report only oauthlib 3.3.1, under the PYSEC aliases
  of the two ignored ids (PyPI: PYSEC-2026-4114; OSV: PYSEC-2026-4113 and
  PYSEC-2026-4114). The GHSA-h7x2-h6g9-p789 ignore is retired: mlflow 3.17.2
  carries its fix.
- Backend `pip-audit`, 2026-10-06, after the multidict, werkzeug and mako
  pins (see the 2026-10-06 addendum below): CI's command, `pip-audit -r
  requirements.txt --strict` with its three ignores, reports no known
  vulnerabilities from the default PyPI service (1 ignored) and none from
  `-s osv` (2 ignored). Over the `uv.lock` pins with no ignores, `-s osv`
  reports only the two ignored oauthlib advisories, under their PYSEC aliases
  (PYSEC-2026-4113 for GHSA-hj66-6f7g-4r5v, PYSEC-2026-4114 for
  GHSA-xpv3-w29h-x7cv).
- Backend `pip-audit`, 2026-09-29, over the `uv.lock` pins
  (`pip-audit -r uv.lock --no-deps --disable-pip --strict`): one known
  vulnerability from the default PyPI service, GHSA-xpv3-w29h-x7cv (oauthlib
  3.3.1), and two from `-s osv`, which also lists GHSA-hj66-6f7g-4r5v
  (oauthlib 3.3.1). Neither has an installable fix and both are unreachable
  here (see the 2026-09-29 addendum and its follow-up below). CI runs
  `pip-audit -r requirements.txt --strict --ignore-vuln GHSA-h7x2-h6g9-p789
  --ignore-vuln GHSA-xpv3-w29h-x7cv --ignore-vuln GHSA-hj66-6f7g-4r5v` and
  carries three ignores, each with its proof at the ci.yml call site:
  - GHSA-h7x2-h6g9-p789, the MLflow tracking-server SSRF: no patched release,
    unreachable here (see the 2026-09-08 addendum below). Review date
    2026-10-08.
  - GHSA-xpv3-w29h-x7cv, the oauthlib server-side PKCE timing attack. Review
    date 2026-10-13.
  - GHSA-hj66-6f7g-4r5v, the oauthlib `RevocationEndpoint` JSONP callback
    injection, ignored before the PyPI feed reports it. Review date
    2026-10-13.
- The prior restricted commercial-use map-data dependency was removed from
  `frontend/package.json`, `frontend/package-lock.json`, and production source.
- State map rendering now uses `us-atlas@3.0.1` (`ISC`) plus the existing
  `topojson-client@3.1.0` (`ISC`) adapter path.
- `requirements.in` carries the direct Python dependency contract, `uv.lock`
  pins the transitive resolution, and `requirements.txt` installs through that
  lock as a constraint file. Its generated first line carries the sha256 of
  both files (`tools/requirements_digest.py`), so the Databricks Apps build
  reinstalls when either changes.
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
./.venv/bin/pip-audit -r requirements.txt --strict
./.venv/bin/python tools/requirements_digest.py --check
./.venv/bin/python -m pytest -q tests/unit/test_supply_chain_licenses.py
./.venv/bin/python -m pytest -q tests/unit/test_error_sanitizer.py tests/unit/test_health_endpoint.py
```

`tests/unit/test_supply_chain_licenses.py` pins this section to CI: the
`--audit-level` named as the gate here must be the one ci.yml runs, and every
advisory ci.yml ignores must be named in this document.

Python lock refresh. A refresh moves only the named packages, and it always
ends by restamping `requirements.txt`:

```bash
uv pip compile requirements.in --python-version 3.11 --format requirements.txt \
  --output-file uv.lock --upgrade-package <package> [--upgrade-package <package> ...]
python tools/requirements_digest.py --write
```

The last step is not optional. The Databricks Apps build compares only the
bytes of `requirements.txt` and does not follow its `-c uv.lock` or
`-r requirements.in` includes, so without the new digest line the running App
keeps its old packages (docs/deployment.md, "App Python dependencies: the
deps-digest line"). `tests/unit/test_requirements_digest.py` fails CI when the
line is stale. The dated "Lock refresh" entries in the addenda below record
what each batch ran at the time. Every refresh before 2026-10-28 predates the
digest line.

## Dependency upgrade checklist

Frontend dependencies move in batches of exact pins (`npm install
<pkg>@<version> --save-exact` for the named packages only, never `npm
update`), and `npm --prefix frontend ci` must run clean on the result. Before
a batch lands, check each of these:

- **Read the current dependency-report issue first.** The open issue labelled
  `dependency-report` (edited in place every Monday by
  `.github/workflows/deps-report.yml`) lists the drift, the advisories, the
  dated holds and waivers, and the workflow tool pins for the current
  `main`; a batch starts from it. The holds themselves live in
  `tools/dependency_holds.json` (see the W5e addendum below).
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
  (`mcr.microsoft.com/playwright:v1.64.0-noble` since W5e tail T3; both the `e2e-visual`
  `container.image` and its `MIP_VRT_IMAGE`, pinned by
  `tests/unit/test_ci_frontend_gates.py`). A Playwright bump moves the image
  in the same change, and the baselines are regenerated once, from that
  bump PR's pinned-image CI renders (never on a developer host).
- **oxlint is an exact dev pin with platform bindings.** `oxlint` 1.85.0
  (MIT; 1.86.0 held in the W5b batch, see the addendum) runs the jsx-a11y ratchet (`tools/oxlint_ratchet.mjs`, see
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
- **typescript-eslint gates TypeScript 7.** typescript-eslint 8.71.0, like 8.70.1, still peers
  `typescript >=4.8.4 <6.1.0`. TypeScript 7 waits until a typescript-eslint
  release admits it; check the peer range with `npm view
  @typescript-eslint/parser peerDependencies`.
- **An axe-core minor can add rules.** Run
  `tests/e2e/fixture/axe.fixture.spec.ts` on the new version. New rule ids
  that fire are fixed in the lane that owns the surface, or the bump is held;
  they are never added to the `KNOWN_VIOLATIONS` ratchet.
- **Update signal.** Ruled 2026-09-30 (owner decision #7): a weekly report
  workflow and no bots, built in W5e (`w5-deps-report`) as
  `.github/workflows/deps-report.yml` over `tools/deps_report`; no bot
  branch (existing contract, see `docs/modernization-todo.md`).

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

## 2026-09-29 addendum: PyJWT 2.14.0, and an oauthlib ignore

Two advisories were published on 2026-09-29, a few hours before the wave-4b
PR's CI run, and reddened `pip-audit` on main as well.

| Advisory | Package | Action |
|---|---|---|
| GHSA-w6j9-cwv2-h6wq (medium): a malformed RSA JWK aborts parsing of the whole JWK Set | `pyjwt` 2.13.0 | Bumped to 2.14.0, the first patched release. `databricks-sql-connector` accepts `pyjwt>=2.0.0,<3.0.0`. Nothing in the repo imports `jwt`, and the connector's `auth.auth_utils` and `auth.oauth` modules (its only `jwt` users) import cleanly on 2.14.0. `uv.lock` was recompiled with `--upgrade-package pyjwt`; no other pin moved. |
| GHSA-xpv3-w29h-x7cv (medium): a timing attack on the PKCE `code_verifier` comparison | `oauthlib` 3.3.1 | Ignored in CI with a review date of 2026-10-13. |

Why the oauthlib advisory is ignored rather than fixed:
- **The fix cannot be installed.** The only patched release is `oauthlib`
  4.0.0, and every `databricks-sql-connector` release from 4.0.0 through 4.6.0
  (the latest) requires `oauthlib>=3.1.0,<4.0.0`. Each release's metadata was
  checked on PyPI.
- **It is unreachable.** The vulnerable comparison is
  `code_challenge_method_s256` / `_plain` in oauthlib's authorization-server
  grant (`grant_types/authorization_code.py`, run by
  `AuthorizationCodeGrant.validate_token_request`). Nothing in the repo
  imports `oauthlib` or `databricks.sql`: the App's SQL goes through the
  databricks-sdk Statements API. The connector's own `auth/oauth.py` uses only
  the client side (`WebApplicationClient`, `OAuth2Error`).
- **When to remove it:** as soon as a connector release admits oauthlib 4, or
  the connector leaves the lock.

### Follow-up, the same day: nine more PyJWT advisories and a second oauthlib advisory

Ten more advisories against the two pins were published on 2026-09-29,
after the bump above was written.

| Advisory | Package | Action |
|---|---|---|
| GHSA-ffc3-869f-jxw9 (critical); GHSA-w2cx-738m-mc7w, GHSA-9v7f-9g4p-ffgj, GHSA-r6x4-923q-g947, GHSA-p4g4-x82p-q773, GHSA-9j54-fg26-wv3r (high); GHSA-2gx3-rcp4-g85q, GHSA-hxm8-2xgr-2p9m, GHSA-8wjv-2p76-3863 (medium) | `pyjwt` 2.13.0 | Already closed by the 2.14.0 pin. For each one, `gh api /advisories/<id>` reports `first_patched_version` 2.14.0, so none of these fixes is a range artifact. A pip-audit run on the pre-bump tree lists all ten PyJWT advisories; the same run on the bumped tree lists none. `requirements.in` now names all ten, so the pin's comment lists every advisory it closes. |
| GHSA-hj66-6f7g-4r5v (medium, CVE-2026-49264): JSONP callback injection in `RevocationEndpoint` | `oauthlib` 3.3.1 | Ignored in CI with GHSA-xpv3-w29h-x7cv's review date, 2026-10-13. |

Why the second oauthlib advisory is ignored, and why before pip-audit
reports it:
- **Same proof as GHSA-xpv3-w29h-x7cv.** The only patched release is
  `oauthlib` 4.0.0, which no connector release admits. The vulnerable code is
  oauthlib's server-side `RevocationEndpoint`
  (`endpoints/revocation.py`), which writes `request.callback` into the
  response body unescaped when `enable_jsonp` is set. No installed module
  outside `oauthlib` names `RevocationEndpoint`, `AuthorizationCodeGrant` or
  any oauthlib server class, and the repo imports neither `oauthlib` nor
  `databricks.sql`.
- **The feeds disagree.** On 2026-09-29 the PyPI vulnerability feed, which is
  pip-audit's default service, listed only GHSA-xpv3-w29h-x7cv for oauthlib
  3.3.1, while OSV listed both. With the other two ignores in place, CI's
  command against OSV (`-s osv`) failed on GHSA-hj66-6f7g-4r5v alone. The
  ignore keeps CI green once PyPI's feed catches up, for a finding whose proof
  has not changed.
- **Upstream unblock:** databricks/databricks-sql-python#964 ("Allow oauthlib
  4.x", open, filed 2026-09-29) asks to lift the connector's
  `oauthlib = "^3.1.0"` cap. The connector's main branch still carries that
  cap. Once a release admits oauthlib 4, move both pins and retire both
  ignores.

## 2026-10-01 addendum: PyJWT 2.15.0 and urllib3 2.8.0

Four advisories published on 2026-10-01 turned the security job red on every
fresh run (first seen on PR #266), with no ignore warranted: each has a
patched release that resolves cleanly.

- **PyJWT** GHSA-42vr-xj54-vc7v, first patched in 2.15.0: `pyjwt==2.15.0` in
  requirements.in (the explicit transitive pin for databricks-sql-connector).
- **urllib3** GHSA-8988-9cw3-xx77, GHSA-gh4c-6fx4-qh6g and GHSA-vxq7-64xx-v4gw,
  each first patched in 2.8.0: a new explicit floor, `urllib3==2.8.0`, so a
  lock refresh cannot reintroduce 2.7.0.
- **Lock refresh:** `uv pip compile ... --upgrade-package pyjwt --upgrade-package
  urllib3`. Only those two packages move in uv.lock, plus urllib3's
  `-r requirements.in` provenance comment, and test_supply_chain_licenses pins
  both.

## 2026-10-01 addendum: the W5b dependency batch

Audit stack-10, W5b lane `w5-wire-contract-deps`. Every package was
re-verified on 2026-10-01 with `npm view <pkg> version` and `npm view
<pkg>@<current major> version`; the batch takes the newest version within each
current major as an exact pin, except the holds below. The lock was refreshed
with `npm install <pkg>@<ver> --save-exact --package-lock-only
--ignore-scripts` over a scratch copy of `package.json` and
`package-lock.json` with no `node_modules` (npm 11), then copied back; no
shared `node_modules` was written. The lanes may not install, so the gates on
the installed tree (build, the full vitest suite, lint with `lint:a11y` and
both typechecks, React Compiler coverage, `dependencyContracts.test.ts`, the
budget and the fixture suite) are the integrator's, after `npm --prefix
frontend ci`; any bump that turns one of them red on another lane's files, or
adds more than +0.3 KiB brotli initial JS or +4.0 KiB total JS, is held at its
previous pin with a dated line here (an advisory-fixing bump is never held for
bytes).

| Package | Current | Latest | Taken | Note |
|---|---|---|---|---|
| `@tanstack/react-query` | 5.100.10 | 5.104.0 | **held 5.100.10** | Held 2026-10-01 by the W5b integrator: on the installed tree the fixture suite goes red in `tests/e2e/fixture/shell-wayfinding.fixture.spec.ts` ('Console motion under reduced motion: closing hides the Console at once', 3 of 3 runs): with query-core 5.104.0 the reduced-motion Console is still `display: flex` one macrotask after Close, where 5.100.10 has already swapped in the hidden placeholder. Bisected on the base tree (base lock green; base + vite 8.3.2 and @rolldown/plugin-babel 0.2.4 green; + TanStack Query 5.104.0 red). The lockstep still holds: one `@tanstack/query-core` and one `query-persist-client-core` in the lock, both 5.100.10. Re-take it with W5d `w5-test-harness-deps-report` once the exit timing is understood (5.104.0 also saved 0.22 KiB br initial JS). |
| `@tanstack/react-query-persist-client` | 5.100.10 | 5.104.0 | **held 5.100.10** | Lockstep with `@tanstack/react-query` (held with it). |
| `@tanstack/react-virtual` | 3.13.24 | 3.14.13 | **held 3.13.24** | Held 2026-10-01 by the W5b integrator: on the installed tree `@tanstack/virtual-core` 3.14.0 -> 3.17.11 grows the shared LeadTable lazy chunk (Lead Queue and Segment Intelligence) by +9.00 raw / +2.59 gzip / +2.28 brotli KiB (37.96 -> 40.31 br, measured by building the batch with and without it), which turns the largest-lazy-chunk budget gate red on base code that `w5-lead-triage-export` also extends. No W5b or W5c item needs a 3.14 API. Re-take it with a lane that funds the bytes (W5c `w5-lead-queue-paging` owns the chunk). |
| `vite` | 8.3.0 | 8.3.2 | 8.3.2 | `rolldown` 1.2.10 -> 1.2.12. Moves VRT and budget bytes at most marginally; re-baselined once by the integrator. |
| `@rolldown/plugin-babel` | 0.2.3 | 0.2.4 | 0.2.4 | Patch. |
| `eslint` | 10.2.1 | 10.11.0 | 10.11.0 | Minor releases add no rule to `recommended`. |
| `@typescript-eslint/eslint-plugin` / `parser` | 8.70.1 | 8.71.0 | 8.71.0 | Still peers `typescript >=4.8.4 <6.1.0`. |
| `happy-dom` | 20.10.3 | 20.14.5 | **held 20.10.3** | Held 2026-10-01 by the W5b integrator: on the installed tree the full vitest suite goes red in `src/components/EvidenceHoverCard.test.tsx` (W5a platform-backend's anchored hover card): 20.14.5 invokes `CSSStyleDeclaration.prototype.setProperty` with a `this` that is not the element's `style` object (a probe on the bare library reproduces it), so the test's same-object proof of where `anchor-name` lands fails although the value is set. The suite is green on 20.10.3 (454 files / 4674 tests). Re-take it with W5d `w5-test-harness-deps-report`, which may re-model that proof on the observable value. |
| `web-vitals` | (new) | 6.2.2 | 6.2.2 | Production dependency, Apache-2.0 (licence row added). Nothing imports it in W5b (0 bytes); W5c `w5-field-vitals` consumes it lazily. |
| `stylelint` | (new) | 17.16.0 | **removed 2026-10-06** | Dev only, MIT; no script called it. Removed on 2026-10-06 because its `micromatch` -> `braces` path carries GHSA-vfj7-8cjw-p6xm with no patched release (see the 2026-10-06 npm addendum); W5e `w5-compiler-lint` re-adds it. |
| `oxlint` | 1.85.0 | 1.86.0 | **held 1.85.0** | Held 2026-10-01: `frontend/oxlint-baseline.json` records the installed oxlint version and `--check` fails on a mismatch, and a new version's jsx-a11y rule list must be named in `.oxlintrc.json`; both need the installed binary, and the ratchet files belong to W5d `w5-compiler-lint`. |
| `typescript` | 6.0.3 | 7.0.2 | **held 6.0.3** | typescript-eslint 8.71.0 peers `<6.1.0`; 6.0.3 is the newest 6.0.x (D-platform-process-a, review_by 2026-11-15). |
| `vitest` | 4.1.11 | 5.0.3 | **held 4.1.11** | Newest 4.x; vitest 5 held by D-platform-process-a (review_by 2026-11-15). |
| `@babel/core` | 7.29.7 | 8.0.6 | **held 7.29.7** | Newest 7.x; Babel 8 held by D-platform-process-a (review_by 2026-11-15). |
| `@playwright/test` | 1.63.0 | 1.63.0 | 1.63.0 | Held to the VRT image (`mcr.microsoft.com/playwright:v1.63.0-noble`); any move belongs to W5d with the image tag. |

Unchanged because already current: `react`, `react-dom`, `react-router`,
`@types/react`, `@types/react-dom`, `@types/topojson-client`,
`@vitejs/plugin-react`, `@axe-core/playwright`, `babel-plugin-react-compiler`,
`eslint-plugin-react-hooks`, `topojson-client`, `us-atlas` and both
`@fontsource-variable` fonts. `rollup-plugin-visualizer` (the bundle treemap)
moved to W5d `w5-test-harness-deps-report` with the treemap itself (integrator
correction C3). Both `GITLEAKS_VERSION` literals in `ci.yml` are now 8.30.1:
the host's gitleaks 8.30.1 scanned all refs (`gitleaks detect --source .
--config .gitleaks.toml --redact --log-opts="--all"`, 2,264 commits) with no
finding, so `.gitleaks.toml` is unchanged.

## 2026-10-06 addendum: multidict 6.9.1, werkzeug 3.1.9 and mako 1.4.2

Two advisories published on 2026-10-05 turned the security job's pip-audit
step red on PR #267 (CI run 37521773357): `Found 2 known vulnerabilities,
ignored 1 in 2 packages`. Re-running CI's command with `-s osv` found a
third, for mako, that PyPI's feed did not list yet. Each has a patched
release that resolves cleanly, so all three are pins, not ignores.

| Advisory | Package | First patched | Action |
|---|---|---|---|
| GHSA-54p9-h82j-f925 (medium, CVE-2026-104874): a reference leak in the C extension's `operand \| md.items()` and `md.items() - operand` | `multidict` 6.7.1 (range `>=6.7.0, <=6.9.0`) | 6.9.1 | `multidict==6.9.1`. Transitive through aiohttp (which caps it `<7`, so 7.0.0 is out of reach) and yarl. |
| GHSA-g6x2-hccm-hh4m (medium, CVE-2026-102598): `safe_join` on Windows admitted special device names | `werkzeug` 3.1.8 (range `<3.1.9`) | 3.1.9 | `werkzeug==3.1.9`. Transitive through flask and flask-cors, which mlflow pulls in for its tracking server. |
| GHSA-5639-2j2p-m4mx (medium, CVE-2026-102991): a drive-letter `TemplateLookup` URI escaped the template directory on Windows | `mako` 1.3.12 (range `<=1.4.1`) | 1.4.2 | `mako==1.4.2`. Transitive through alembic, which mlflow pulls in. Listed by OSV but not yet by PyPI's feed for 1.3.12 on 2026-10-06; without the pin, CI would go red again when the feed caught up. |

- **The fix versions come from the advisories.** `gh api
  /advisories/<id>` reports a `first_patched_version` for each of the three
  (6.9.1, 3.1.9, 1.4.2), so none is a range artifact. Listing advisories by
  package (`gh api '/advisories?ecosystem=pip&affects=<pkg>'`) found no other
  open advisory against the new pins.
- **No first-party code uses these packages.** Over backend, tools, jobs,
  pipelines, scripts and tests, `grep -rhoE
  '\b(multidict|werkzeug|mako)(\.[A-Za-z_][A-Za-z0-9_]*)+'` returns 0 dotted
  tokens and there are 0 import lines. The same grep for `mlflow` returns 36
  tokens, so the 0 is a real result. To check compatibility, every name the
  installed consumers (aiohttp, yarl, flask, flask-cors, alembic, mlflow and
  websockets) take from the three packages was resolved with importlib in a
  private venv built from `requirements.txt`: 55 names, none unresolved. A
  made-up name fails the same check.
- **Upstream changelogs, checked against what the consumers use.** multidict
  6.8.0 to 6.9.1 drops Python 3.9 and free-threaded 3.13 (this repo runs
  3.11). Lookup and removal methods now reject extra keyword arguments with
  `TypeError`; nothing in the repo calls multidict. The rest is C-extension
  memory and threading fixes. werkzeug 3.1.9 is a patch release: the
  `safe_join` fix plus parsing fixes (`get_host` port range, basic-auth
  characters, `Range: -0`, urlencoded forms bounded by `max_content_length`
  only) and faster header parsing. mako 1.4.0 to 1.4.2 raises the floors to
  Python 3.10 and MarkupSafe 2.0 (the lock has 3.0.3). It also changes where
  compile-time `SyntaxWarning`s and multi-line `SyntaxException`s are
  reported, and makes the traversal check use `posixpath`. alembic uses mako
  only to write migration files (`mako.template.Template` for its revision
  template, `mako.pygen.PythonPrinter` for autogenerate output), never
  `TemplateLookup`. The App and the deploy tools track to `databricks`; the
  only local SQL store is the sqlite registry in
  `tests/integration/test_gateway_mlflow_registry.py`, whose schema upgrade
  imports mako through alembic and passes on 1.4.2.
- **Lock refresh:** `uv pip compile requirements.in --python-version 3.11
  --format requirements.txt --output-file uv.lock --upgrade-package multidict
  --upgrade-package werkzeug`, then `--upgrade-package mako`. Only those three
  packages move in uv.lock, each gaining a `-r requirements.in` provenance
  line, and test_supply_chain_licenses pins all three in both files.
- **Proof** (CI's literal command, run through the symlink-forcing driver
  with pip-audit 2.9.0): before the change, the PyPI run reproduces CI's two
  findings exactly, and `-s osv` adds mako. After it, `No known
  vulnerabilities found, 1 ignored` (PyPI) and `No known vulnerabilities
  found, 2 ignored` (`-s osv`). The lock-only read (`-r uv.lock --no-deps
  --disable-pip`) and an installed-mode read of the private venv agree.
  A targeted pytest run against that venv passes: the supply-chain test, the
  mlflow-importing gateway and agent-eval suites,
  `tests/integration/test_gateway_mlflow_registry.py`, the health endpoint
  and the error sanitizer, 456 tests. The run loaded mako 1.4.2 through
  alembic.

## 2026-10-06 addendum: npm audit, source-map-js 1.2.2 and stylelint removed

PR #267's second CI run turned the `npm --prefix frontend audit --audit-level=high` step red on two high advisories (both published 2026-09-18, surfaced by the npm feed between the run's two pushes):

| Advisory | Package | Range | First patched | Path | Action |
|---|---|---|---|---|---|
| GHSA-68fv-2mgg-jv7q | `source-map-js` | `>= 1.0.0, < 1.2.2` | 1.2.2 | `postcss` / `vite` (build time) | Lock-only bump to 1.2.2 (`npm update source-map-js --package-lock-only`); `main` carries 1.2.1 too, so this also clears the ambient red there once W5b merges. |
| GHSA-vfj7-8cjw-p6xm | `braces` | `<= 3.0.3` | none | `stylelint` -> `micromatch` -> `braces` (dev only) | No patched release exists. `stylelint` was added by the W5b batch with no script calling it, so it is removed (`npm uninstall stylelint --package-lock-only`) rather than excused; `braces` is no longer in the lock. `tests/unit/test_dependency_pins.py::test_stylelint_waits_for_a_patched_braces` pins its absence until W5e `w5-compiler-lint` re-adds it on a patched path. |

After `npm --prefix frontend ci` on the refreshed lock: `npm audit --audit-level=high` reports 0 vulnerabilities. The only other lock change is npm's dedupe re-hoisting `fast-json-stable-stringify` 2.1.0 once stylelint's tree left.

## 2026-10-14 addendum: the weekly report and the dated holds (W5e)

Audit quality-v2 / stack-10, decision D-platform-process-a, W5e lane
`w5-deps-report`.

- **The report.** `.github/workflows/deps-report.yml` runs every Monday at
  13:17 UTC and on dispatch. Its `report` job (contents and actions read
  only) runs `python -m tools.deps_report`, which reads the exact pins
  (`npm view` with nothing installed; PyPI JSON for the direct requirements
  of `requirements.in`, exact or range-pinned, at their `uv.lock` versions), `npm audit
  --package-lock-only` at every severity, `pip-audit -r uv.lock --no-deps
  --disable-pip --strict -f json`, the literal tool pins in every workflow,
  the `Review date:` comments in `ci.yml`, the file-size allowlist expiries
  and the hold dates. Its `publish` job (issues write only, no checkout, no
  package code) re-validates the markdown and edits the one open
  `dependency-report` issue in place. Every value in that public issue is
  shape-checked and set in a code span; advisory titles and descriptions are
  never rendered.
- **What turns it red (exit 1).** An unignored npm high or critical
  advisory, any pip-audit finding not ignored on `ci.yml`'s `run: pip-audit`
  line, a `ci.yml` review date in the past, a file-size allowlist entry
  expired or within 7 days, a hold whose `review_by` has passed, and a tool
  pinned to two different literals with no unexpired `tool-literal` hold.
  Drift alone never reds; a review date within 7 days is a warning. Exit 2
  means a source could not be read (named under "Report error"). The live
  release-gate section is informational and never changes the exit status.
- **The holds are the source of truth.** `tools/dependency_holds.json` holds
  every dated hold (`ecosystem` npm, pypi or tool-literal; `below` or
  `literals`; `reason`; `review_by`). A held package renders as held, never
  as pending. `tests/unit/test_dependency_pins.py` checks that every hold
  names a pinned package whose pin is below its bound; an expired
  `review_by` is the report's signal, not a test failure. The hold rows in
  the W5b batch table above are the history; a new hold is a JSON row plus a
  dated line here.
- **Run it offline.** `python -m tools.deps_report --offline-fixtures
  tests/fixtures/deps_report --today 2026-10-14 --json /tmp/deps.json
  --markdown /tmp/deps.md` reads only the fixture directory (the hermetic dry
  run `tests/unit/test_deps_report.py` pins byte for byte). Without
  `--offline-fixtures` it reads the live registries, with `GITHUB_TOKEN` or
  `GH_TOKEN` for the GitHub release lookups.
- **The expected first runs are red.** The two `ci.yml` review dates (the
  mlflow ignore, 2026-10-08, and the two oauthlib ignores, 2026-10-13) have
  passed, so the first run exits 1 with two `review_date_past` lines. That
  is the designed signal: this lane may not change the ignores, their proofs
  or their dates, and a date bump is not a re-review. The re-review belongs
  to the integrator's security pass right after the first run.
- **Re-reviewed at the W5e integration (2026-10-15).** GHSA-h7x2-h6g9-p789:
  `first_patched_version` is still null and the range still ends at 3.15.2
  (advisory last updated 2026-08-31); the `mlflow (server|gateway)` grep still
  returns nothing, so the ignore stays (next review 2026-11-15). The two
  oauthlib advisories: 4.0.0 is still the only patched release; the
  connector's main branch now admits `oauthlib >=3.1.0,<5.0.0`
  (databricks-sql-python#964, closed as completed 2026-10-06), but no release
  past 4.6.0 (which still caps `<4.0.0`) exists and the repo pins 4.4.0, so
  both ignores stay (next review 2026-11-01, when the first connector release
  past 4.6.0 is the trigger). Both proofs and dates are written at the
  `ci.yml` call site; the first deps-report run is no longer red on
  `review_date_past`.

### W5e dependency tails (stacked on `ux/w5e-deps-report-tail`, never merged as a branch)

Each tail is one commit whose lock was computed with `npm install <pkg>@<ver>
--save-exact --package-lock-only --ignore-scripts`. The integrator RE-RAN that
command on the merged tree (over a copy of its `frontend/package.json` +
`package-lock.json` with no `node_modules`; never a cherry-picked lock hunk),
ran `npm --prefix frontend ci` and the installed-tree gates, and applied the
W5b hold rule: a tail that turns a gate red on another lane's files, adds more
than +0.3 KiB br initial JS or +4.0 KiB br total JS, or breaks maxLazy is
dropped and its hold row stays in `tools/dependency_holds.json` with a fresh
dated reason (post-wave owner: the next dependency batch, tracked weekly by
the report). `npm audit --package-lock-only --audit-level=high` is 0 on every
applied lock, no tail adds a `micromatch` or `braces` path, and stylelint
stays out.

| Tail | Package | From | To | Outcome (W5e integration, 2026-10-15) | Lock changes on the merged tree |
|---|---|---|---|---|---|
| T1a | `happy-dom` | 20.10.3 | 20.14.6 | Applied after `w5-test-harness-deps-report` (its EvidenceHoverCard re-model): full vitest 612 files / 6,528 tests green on the installed tree; test-only, no bundle byte | `happy-dom` only |
| T1b | `@tanstack/react-query` + `-persist-client` | 5.100.10 | 5.104.2 | Applied: test-harness C4 outcome (i), shell-wayfinding 21/21 and the re-expressed rAF probe 'closing hides the Console at once' 7/7 on 5.104.2; initial JS -0.19 KiB br | the two packages and `query-core` / `query-persist-client-core`, all 5.104.2 (one entry each) |
| T1c | `@tanstack/react-virtual` | 3.13.24 | 3.14.19 | HELD: `virtual-core` 3.19.4 adds +2.80 KiB br / +10.73 raw / +3.25 gzip to the leadsQuery (LeadTable) chunk, and its raw 181.65 KiB breaks the 180 KiB maxLazy gate re-baselined at the `w5-lead-table-compile` merge (br 50.50 of 51 and gzip 60.86 of 61 leave under 1%); lead-queue +2.83, segment-intelligence +2.78 KiB br | (not applied) `react-virtual` 3.14.19, `virtual-core` 3.14.0 -> 3.19.4 |
| T2 | `oxlint` | 1.85.0 | 1.86.0 | Applied with `w5-compiler-lint`: `frontend/oxlint-baseline.json` re-recorded with `--ratchet` (it records the oxlint version; 6 files, no new, grown or suppressed hit); 1.86.0 lists 36 jsx-a11y rules and `.oxlintrc.json` names all 36, so no new rule to name; dev-only, no bundle byte | `oxlint` and all 19 `@oxlint/binding-*` platform entries, 1.86.0 |
| T3 | `@playwright/test` | 1.63.0 | 1.64.0 | Applied LAST, with the `mcr.microsoft.com/playwright:v1.64.0-noble` VRT image (ci.yml `e2e-visual` container and `MIP_VRT_IMAGE`, test_dependency_pins, test-infra.fixture.spec.ts, the playwright.config.ts comment, docs/testing.md); 1.64.0 ships Chromium 156, Firefox 157 and WebKit 27.2; every e2e-visual baseline moves and is refreshed from the pinned-container CI actuals (orchestrator) | `@playwright/test`, `playwright`, `playwright-core`, 1.64.0 |
| T4 | `vite`, `vitest`, `happy-dom`, `zod` (transitive) | 8.3.2, 4.1.11, 20.14.6, 4.3.6 | 8.3.5, 4.1.12, 21.0.4, 4.6.7 | Applied at the W5e fix round (2026-10-26) to clear the five dev-only advisories above, re-run lock-only on the merged tree: `npm install vite@8.3.5 vitest@4.1.12 --save-exact --save-dev --package-lock-only --ignore-scripts`, `npm update zod --package-lock-only --ignore-scripts` (eslint-plugin-react-hooks admits `^4.0.0`, so no override), then `npm install happy-dom@21.0.4 --save-exact --save-dev --package-lock-only --ignore-scripts` (a major: full vitest 630 files / 6,833 tests green on the installed tree, so no hold). vitest stays inside its `below 5.0.0` hold. Budgets on the merged tree: initial JS 147.68 KiB br (147.79 before, with the compliance-caveat fix in the same build), total JS 687.66, leadsQuery 47.98 br / 171.29 raw; every gate passes | `vite` 8.3.5, `rolldown` and its 15 `@rolldown/binding-*` entries 1.2.12 -> 1.2.15, `@oxc-project/types` 0.155.0, `postcss` 8.5.31, `nanoid` 3.3.20; `vitest` and its seven `@vitest/*` packages 4.1.12, `chai` 6.3.0, `tinyrainbow` 3.2.0; `happy-dom` 21.0.4; `zod` 4.6.7; nothing added or removed (291 entries), no `micromatch` / `braces` path, stylelint stays out |
| T1c (re-take) | `@tanstack/react-virtual` | 3.13.24 | 3.14.19 | APPLIED 2026-10-26 by the post-W5 lane `leadtable-chunk-react-virtual` (branch `post-w5/leadtable-chunk-react-virtual` from main `550dcf97`), after a LeadTable chunk cut in the same branch: the expanded Lead Queue row's body (RowPreview, LeadRowWorkflowPanel and AssignmentLifecycleAdvance) became its own lazy chunk, warmed on idle and on row engagement, so leadsQuery went 171.54 -> 150.53 raw / 57.91 -> 51.04 gzip / 47.99 -> 42.76 KiB br (archive builds on one host, where `550dcf97` measures 171.54 raw; the W5e fix-round comments' 171.29 are another host's). The recorded command, re-run over a copy of that tree's `frontend/package.json` + `package-lock.json` with no `node_modules`, gives the lock of `cfc5818a` byte for byte; after `npm --prefix frontend ci`, virtual-core 3.19.4 adds +10.72 raw / +3.13 gzip / +2.75 br to leadsQuery, which ends at 161.25 raw / 54.17 gzip / 45.51 br under the unchanged 180 / 61 / 51 maxLazy gates (10.4% / 11.2% / 10.8% left). Hold rule: initial JS 147.69 -> 147.67 KiB br (gate 152), total JS +2.73 br (690.29 -> 693.02, gate 723), every route and lazy-module gate green (lead-queue 93.84 -> 96.63 br, segment-intelligence 114.25 -> 117.08). The same lock without the cut still breaks maxLazy raw (182.27 > 180) and gzip (61.13 > 61). The `@tanstack/react-virtual` row leaves `tools/dependency_holds.json`. `npm audit --package-lock-only --audit-level=high`: 0 | `react-virtual` 3.14.19, `virtual-core` 3.14.0 -> 3.19.4; nothing added or removed (291 entries) |

## 2026-10-26 addendum: the October Python advisories, and the GHSA-h7x2-h6g9-p789 ignore retired

Advisories published 2026-10-19 to 2026-10-23 turned the security job's
pip-audit step red on the W5e PR #270 (CI run 40311237233): `Found 52 known
vulnerabilities, ignored 1 in 7 packages`. `main` at 5e0fbe2c fails the same
way. Re-running CI's command with `-s osv` found 56 (2 ignored): four more
pillow ids, all fixed by the same release. Every finding has a patched
release that resolves cleanly, so all of them are pins, not ignores.

| Advisory | Package | First patched | Action |
|---|---|---|---|
| GHSA-5wr8-gcqf-gqf3, GHSA-2g87-pp6c-29x5, GHSA-9xw5-w5jj-pvcg (high); GHSA-pqxq-w44r-73xw, GHSA-6cjc-53gc-5rqm, GHSA-f9xw-jh8m-3mwq (medium); GHSA-494j-54xm-rwp3, GHSA-gx76-c99x-2c96 (low) | `aiohttp` 3.14.3 | 3.14.4 | Floor `aiohttp>=3.14.4`; the lock takes 3.14.5. 3.14.4 requires `yarl>=1.25.1`, so `yarl` moves 1.24.2 -> 1.25.1 with it. Transitive through mlflow. |
| PYSEC-2026-4320 (GHSA-6q48-6c7w-9gw2): the bundle still trusted the emSign roots whose intermediate CA keys were compromised | `certifi` 2026.4.22 | 2026.10.19 | `certifi==2026.10.23` (the latest bundle). Transitive through requests, httpx and httpcore. |
| GHSA-83vg-56qc-22m7 (high): a submodule's `.git` path was validated only after cloning into it | `gitpython` 3.1.62 | 3.2.1 | Floor `gitpython>=3.2.1,<4`. Transitive through mlflow-skinny; 3.2.0 drops Python 3.7 and hardens command and config parsing. |
| GHSA-vj8h-fx38-h3vc, GHSA-r77w-qph3-7vf8 (high): quadratic `OverlappingFieldsCanBeMerged` validation; comment tokens bypass `max_tokens` | `graphql-core` 3.2.11 | 3.2.12 (3.3.0rc1 on the 3.3 line) | `graphql-core==3.2.12`. Transitive through graphene and graphql-relay (mlflow's tracking-server GraphQL API); graphene caps it `<3.3`, so 3.2.12 is the reachable fix. OSV's record names only 3.3.0rc1 as the fix, but both services are silent on 3.2.12. |
| GHSA-wqmh-qccw-pqwf (high): FileStore trace IDs allow path traversal; GHSA-2wqp-q3c6-ccj5 (medium): SSRF through third-party scorer kwargs | `mlflow` 3.16.0 | 3.17.2; 3.17.0 | `mlflow==3.17.2`, with `mlflow-skinny` and `mlflow-tracing` (exact pins of mlflow). `GATEWAY_MODEL_REQUIREMENTS` in `backend/agents/gateway_contract.py` (the gateway model's serving environment) moves with it, as it did for 3.16.0, so the gateway proxy source hash changes and the next deploy registers a new gateway model version. |
| GHSA-xcq7-p3pf-p88h (medium): unbounded `tracestate` parsing | `opentelemetry-api` 1.41.1 | 1.45.1 | Floors `opentelemetry-api`, `-sdk` and `-exporter-otlp` `>=1.45.1,<2`. The SDK pins the API to its own version, so the family moves together: api, sdk, exporter-otlp, exporter-otlp-proto-grpc, -proto-http, -proto-common and proto 1.41.1 -> 1.45.1, semantic-conventions 0.62b1 -> 0.66b1, and two new Apache-2.0 distributions, `opentelemetry-exporter-otlp-common` and `opentelemetry-exporter-http-transport` (0.66b1). |
| 37 ids on PyPI's feed, 41 on OSV (12 of them GitHub-reviewed) | `pillow` 12.3.0 | 13.0.0 (the only fixed release for every id) | `pillow==13.0.0`. Transitive through matplotlib (via mlflow, `pillow>=9`). Pillow 13 needs Python 3.11, which `pyproject.toml` already requires. |

- **The fix versions come from the advisories.** `gh api /advisories/<id>`
  reports a `first_patched_version` for every non-pillow GHSA id above, and
  each matches the table; the 12 GitHub-reviewed pillow advisories all name
  13.0.0, and pip-audit lists 13.0.0 as the only fix for all 41 OSV ids.
  Listing GitHub advisories by package (`gh api
  '/advisories?ecosystem=pip&affects=<pkg>@<version>'`) for every new pin
  returns none; the same query on the old pins returns the ids above (the
  control).
- **GHSA-h7x2-h6g9-p789 is retired, not re-dated.** MLflow 3.17.0's release
  notes list "Validate gateway secret `api_base` to prevent SSRF
  (GHSA-h7x2-h6g9-p789)" (mlflow#25761). In the installed 3.17.2,
  `_create_gateway_secret` and `_update_gateway_secret` route `auth_config`
  through `_validate_gateway_secret_auth_config`, whose
  `_validate_gateway_api_base` admits public HTTPS only by default (a
  `169.254.169.254` or `10.x` `api_base` raises `MlflowException`, a public
  HTTPS one passes), and `raw_proxy` calls `_enable_upstream_ssrf_protection`
  before proxying; 3.16.0 has none of the three (AST-compared across both
  installs). The advisory still names no `first_patched_version` and marks
  `>=3.13.0,<=3.15.2`, so pip-audit is silent on 3.17.2 with or without the
  ignore. CI now carries two ignores, both oauthlib (review date 2026-11-01,
  unchanged).
- **No first-party code imports aiohttp, git, certifi or graphql.** The
  first-party importers of the bumped packages are
  `backend/services/observability.py` (OpenTelemetry), 15 modules that import
  mlflow, and the dev-only `tools/render_brand_icons.py` (Pillow). Every
  dotted `mlflow.*`, `opentelemetry.*` and `PIL.*` name the repo uses resolves
  with importlib on the new pins, the same set as on the old ones (the six
  `mlflow.*` names that do not resolve on either side are tag and span
  attribute keys such as `mlflow.ownerEmail`, not API; a made-up name fails).
  Every name the installed consumers (mlflow, graphene, graphql-relay,
  aiohttp, matplotlib, requests, httpx, botocore, google-auth and others)
  import from the bumped packages resolves the same way.
- **Behaviour checks at the broken layer.** The OTLP wiring sequence in
  `backend/services/observability.py` (OTLP HTTP log exporter,
  `LoggerProvider`, `LoggingHandler`, `BatchLogRecordProcessor`, `Resource`)
  runs on 1.45.1 and emits the same single `LoggingHandler`
  DeprecationWarning 1.41.1 already did, so `log_export=otlp` does not fall
  back to stdout. `tools/render_brand_icons.py` renders byte-identical PNGs
  on Pillow 13.0.0 and 12.3.0 with DeprecationWarnings as errors, equal to the
  committed `favicon.png` and `apple-touch-icon.png`. mlflow 3.17's
  changelog adds a schema upgrade for self-hosted SQL tracking servers; the
  repo tracks to `databricks`, and the sqlite registry in
  `tests/integration/test_gateway_mlflow_registry.py` migrates on 3.17.2.
- **Lock refresh, in two steps that equal one.** `uv pip compile
  requirements.in --python-version 3.11 --format requirements.txt
  --output-file uv.lock` with `--upgrade-package` for aiohttp, certifi,
  gitpython, graphql-core, pillow and the eight OpenTelemetry distributions
  above, then `--upgrade-package mlflow --upgrade-package mlflow-skinny
  --upgrade-package mlflow-tracing`. The result is byte-identical to a single
  run with all of them. Only the packages in the table, `yarl` and the two new
  OpenTelemetry distributions change version; `importlib-metadata` only
  loses its opentelemetry-api provenance line. `requirements.txt` (`-c
  uv.lock` + `-r requirements.in`) is unchanged, and
  test_supply_chain_licenses pins the new literals in both files.
- **Proof** (CI's literal command, run through the symlink-forcing driver
  with pip-audit 2.9.0): before the change, the PyPI run reproduces CI's 52
  findings in 7 packages exactly (1 ignored), and `-s osv` reports 56 (2
  ignored). After it, `No known vulnerabilities found, 1 ignored` (PyPI) and
  `No known vulnerabilities found, 2 ignored` (`-s osv`), with the three-
  and the two-ignore command alike. An installed-mode read of a private
  Python 3.11 venv built from the new `requirements.txt` agrees on both
  services, and the same read of a venv built from the old one reproduces the
  52. A targeted pytest battery (44 files: the mlflow-, OpenTelemetry- and
  deploy-payload-importing suites, the gateway, agent-eval, observability,
  health, capabilities, supply-chain, pins and deps-report tests, and
  `tests/integration/test_gateway_mlflow_registry.py`) gives the same result
  on the old tree with the old venv and on the new tree with the new venv:
  1,704 passed and the same 19 failures, all in
  `test_deploy_dev_workflow_contract.py`'s bash harnesses, which fail
  identically on the unchanged tree under macOS bash 3.2 (`set -u` on an
  empty array) and are not dependency-related.
- **Licences.** No changed package changes licence (aiohttp's metadata moves
  the same `Apache-2.0 AND MIT` into `License-Expression`); the two new
  OpenTelemetry distributions are Apache-2.0 like the rest of the family.
  `certifi` (MPL-2.0) becomes a direct pin, so `docs/THIRD_PARTY_LICENSES.md`
  now lists it.

## 2026-10-28 addendum: oauthlib 4.0.0, and the last two ignores retired

On 2026-10-27 GHSA-3jp2-3mrg-2vxc (high) reached pip-audit's PyPI feed and
turned the `pip-audit` rail red on a pull request whose requirements had not
changed. It is a third oauthlib 3.3.1 authorization-server advisory: the
token endpoint's `DeviceCodeGrant.validate_token_request` never validates
`device_code` (RFC 8628 §3.4). Like GHSA-xpv3-w29h-x7cv and
GHSA-hj66-6f7g-4r5v, it is unreachable here, and its only patched release is
oauthlib 4.0.0.

| Advisory | Package | First patched | Action |
|---|---|---|---|
| GHSA-3jp2-3mrg-2vxc (high): device-flow bearer token without a valid `device_code` | `oauthlib` 3.3.1 | 4.0.0 | Fixed: `oauthlib==4.0.0` through `databricks-sql-connector==4.8.0`. |
| GHSA-xpv3-w29h-x7cv (medium), GHSA-hj66-6f7g-4r5v (medium) | `oauthlib` 3.3.1 | 4.0.0 | Fixed the same way; both CI ignores (review date 2026-11-01) are retired. |

- **The fix became installable on 2026-10-20.** databricks-sql-connector
  4.7.0 (2026-10-20) is the first release that requires
  `oauthlib>=3.1.0,<5.0.0`; every release from 4.0.0 through 4.6.0 required
  `<4.0.0`. 4.7.0 is the release the 2026-10-15 re-review named as its
  trigger ("the first connector release past 4.6.0"), and it arrived before
  that review's 2026-11-01 date. `requirements.in` moves the connector
  4.4.0 -> 4.8.0 (2026-10-24, the latest release; it adds kernel and
  locked-dependency updates over 4.7.0) and adds an `oauthlib>=4.0.0,<5`
  security floor, so a future connector pin cannot drag oauthlib back.
  4.8.0's non-extra requirements equal 4.4.0's except `oauthlib<5` and
  `thrift<0.26` (both from 4.7.0); the first-party `thrift>=0.24.0,<0.25`
  floor holds the lock at 0.24.0. Only the `kernel` extra (not installed)
  moves further.
- **Lock refresh, narrow.** `uv pip compile requirements.in --python-version
  3.11 --format requirements.txt --output-file uv.lock --upgrade-package
  databricks-sql-connector --upgrade-package oauthlib` moves exactly two
  pins: databricks-sql-connector 4.4.0 -> 4.8.0 and oauthlib 3.3.1 -> 4.0.0
  (now also `via -r requirements.in`). `requirements.txt` is unchanged, and
  test_supply_chain_licenses pins the new literals in both files.
- **Still nothing first-party imports either package.** Over backend, jobs,
  tools, pipelines and scripts, `grep -rnE "^\s*(import|from)
  (oauthlib|databricks\.sql)"` and `grep -rnE "^\s*from databricks import
  (.*\b)?sql\b"` return nothing. In a Python 3.11 venv built from the new
  `requirements.txt`, `databricks.sql`, `databricks.sql.auth.oauth` and
  `databricks.sql.auth.auth_utils` import, and the connector's only oauthlib
  names (`oauthlib.oauth2.WebApplicationClient`,
  `oauthlib.oauth2.rfc6749.errors.OAuth2Error`) resolve on 4.0.0.
- **Proof** (CI's literal command through the symlink-forcing driver, pip-audit
  2.9.0, no ignore): the new tree reports `No known vulnerabilities found` on
  the PyPI service and on `-s osv`. The old lock with the same command reports
  2 findings in oauthlib 3.3.1 on PyPI (PYSEC-2026-4114,
  GHSA-3jp2-3mrg-2vxc) and 3 on `-s osv` (adding PYSEC-2026-4113), the
  control.
- **CI and the weekly report.** `.github/workflows/ci.yml` runs
  `pip-audit -r requirements.txt --strict` with no ignore; the retired
  ignores' proofs stay at the call site as history, and with them went the
  last `Review date:` line, so the deps report has no review date to gate on.
  `test_deps_report.py` pins both (no real ignore and no real review date);
  the synthetic fixture keeps the parser exercised.
- **Licences.** Unchanged: oauthlib 4.0.0 is BSD-3-Clause and
  databricks-sql-connector 4.8.0 is Apache-2.0, as before.
