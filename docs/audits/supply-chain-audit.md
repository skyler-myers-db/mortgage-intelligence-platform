# Supply-Chain + Dependency Audit

> Internal validation artifact — not approved for public release. Updated after
> remediation of the map-data licensing blocker and dependency-lock hygiene
> findings.

## Current Verdict

The supply-chain posture is shippable for commercial Module 0 deployment after
this remediation pass.

- Browser-shipped production dependencies have no known commercial-use license
  blockers.
- Frontend `npm audit` reports zero known vulnerabilities.
- Backend `pip-audit -r requirements.txt --strict` reports zero known
  vulnerabilities after the runtime dependency bump.
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
npm --prefix frontend audit --audit-level=moderate
npm --prefix frontend run test -- USChoroplethMap
npm --prefix frontend run build
./.venv/bin/pip-audit -r requirements.txt --strict
./.venv/bin/python -m pytest -q tests/unit/test_supply_chain_licenses.py
./.venv/bin/python -m pytest -q tests/unit/test_error_sanitizer.py tests/unit/test_health_endpoint.py
```

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
