# Modernization TODO

Status legend: `[ ]` not started, `[~]` in progress, `[x]` complete, `[?]` needs an owner decision.

This tracker captures the remaining work to keep Module 0 aligned with modern web-app practice as of May 2026. Each item must close with validation evidence, not just code changes.

## P0 - Production Growth Blockers

- [x] **Dependency and toolchain contract**
  - Replace floating `latest` frontend package ranges with exact versions from the validated lockfile.
  - Remove stale root-level React 18 dependencies; the production app lives under `frontend/`.
  - Add Node/npm engine and package-manager contracts.
  - Validation: `npm --prefix frontend ci`, `npm --prefix frontend run lint`, `npm --prefix frontend run test`, `npm --prefix frontend run build`, production dependency audit.

- [x] **Databricks bundle deploy path**
  - Root-cause the recurring `databricks bundle deploy -t dev` app permission `403`: bare dev deploy inherited the CI placeholder `genie_space_id`, and Databricks Apps reported the invalid Genie binding as an opaque "Can View" permission error.
  - Make the documented bundle path deploy the app without falling back to direct `databricks apps deploy`: dev target now pins the governed Entrada Genie space binding instead of inheriting the root placeholder.
  - Validation: `databricks bundle validate -t dev --profile DEFAULT -o json` resolves a non-placeholder Genie space binding; `databricks bundle deploy -t dev --profile DEFAULT` completed successfully; `/api/health` on the deployed app returned `warehouse/lakebase/genie=up`.

- [x] **Backpressure and load protection**
  - Add authenticated per-actor/per-route budgets for high-cost endpoints.
  - Add dependency concurrency guards for warehouse, Genie, Lakebase writes, and expensive borrower dossiers.
  - Return `429` with `Retry-After` and a machine-readable body when a caller exceeds a budget.
  - Validation: `tests/unit/test_backpressure.py`, focused API boundary/health/observability regression tests, broader API/resilience regression set, scoped backend lint.

- [x] **Production observability and RUM**
  - Wire OpenTelemetry export support in the deployed app image rather than optional-only docs.
  - Add backend latency/error/circuit-break/cache metrics with route and dependency dimensions.
  - [x] Add browser Real User Monitoring for navigation load, SPA route load, LCP, INP, CLS, and long tasks.
  - [x] Add a PII-safe `/api/telemetry/rum` endpoint that rejects query strings, borrower ids, UUIDs, and emails.
  - [x] Add a self-contained OTLP proof lane that preserves stdout-only boot when optional OTEL wheels are missing, verifies handler attachment with a mocked exporter, and asserts endpoint/header/token secrets do not leak into exported log bodies.
  - [x] Add a non-default `prod_otlp` bundle target with an app secret resource for `MIP_OTEL_HEADERS`, plus `tools/databricks/otlp_deploy_payload.py` so production deployments can promote a full env list without committing collector headers or breaking default dev deploys.
  - [x] Add `tools/databricks/otlp_customer_retention_gate.py` so customer durable retention cannot be claimed from prose alone; it requires customer-owned collector ownership, secret reference, retention/ACL proof, and a fresh collector query proof for the deployed-app correlation id.
  - Validation: `tests/unit/test_rum_telemetry.py`, `frontend/src/lib/rum.test.ts`, frontend lint/build/budget, scoped backend lint, API/resilience regression set; deployed app accepted sanitized batched RUM with `202` while rejecting a borrower-ID route with `422`.
  - External OTLP proof: a temporary deployment included the base app env/resource bindings plus `MIP_OTEL_ENDPOINT`; `/api/admin/health` reported `log_export=otlp`, dependencies up, and all breakers closed. A sanitized RUM request returned a correlation id, and the external collector received an OTLP HTTP protobuf payload containing the matching `http_request` log body for `/api/telemetry/rum`.
  - Secret-backed OTLP proof: a temporary deployment used `MIP_OTEL_HEADERS` via Databricks App resource `value_from=otel_headers`, backed by a temporary non-sensitive `mip/otel-headers` Databricks Secret. `/api/admin/health` reported `log_export=otlp`; a sanitized `POST /api/telemetry/rum` returned `202`; the external collector received the matching `/api/telemetry/rum` log body and expected proof header.
  - Restored sandbox posture: the active deployment is running without OTLP env vars, the temporary app secret resource was removed, the temporary `mip/otel-headers` secret was deleted, and `/api/admin/health` reports `log_export=stdout-only`; customer production still needs a customer-owned collector endpoint, customer-owned secret-backed headers, collector-side retention/ACL proof, and collector-side query proof before durable off-platform retention can be claimed for that environment.

## P1 - Scale and Maintainability

- [x] **Server cache hardening**
  - Add max-size/LRU bounds to `TTLCache`.
  - Add single-flight request coalescing for hot aggregate cache misses.
  - Add stale-if-error for safe read-only aggregates.
  - Emit cache hit/miss/eviction metrics.
  - Portfolio `preview()` stale-if-error is classified here as read-only resilience hardening, not as no-behavior large-file decomposition.
  - 2026-06-10 follow-through: geo repository's six remaining bare `get`/`set` sites (state/county/zip rollups, filtered + unfiltered, `_geography_scope`) ported to `get_or_set(..., stale_if_error=True)` — geo map drills now coalesce concurrent misses and serve last-good on a warehouse flap; cold-cache failures still propagate (no fabricated empty map). Segment list intentionally remains fail-visible per its pinned test.
  - Validation: `tests/unit/test_resilience.py`, `tests/unit/test_config_cache.py`, repository cache regression tests, broader API/resilience regression set; `tests/unit/test_geo_repository.py` singleflight/stale-if-error/cold-propagation cases (35 geo tests green).

- [x] **Lakebase connection pooling**
  - Replace one-connection-per-call with a bounded `psycopg_pool` or equivalent.
  - Keep per-connection OAuth password refresh semantics.
  - Ensure transactions still preserve approval-plus-audit atomicity.
  - Validation: unit tests for pool lifecycle plus Lakebase bootstrap/resilience/API regressions; deployed app smoke passed `scripts/smoke_live.sh --no-genie`, including outreach draft and approval audit write; earlier live smoke drafted, approved, assigned, dispositioned, read lifecycle, and found the expected audit rows; five concurrent approve retries with one governed request id returned one approval id and exactly one audit row.

- [x] **React Compiler pilot**
  - [x] Add React Compiler through the current Vite 8 `reactCompilerPreset` path.
  - [x] Add React Hooks `recommended-latest` compiler-safety lint rules.
  - Keep `react-hooks/set-state-in-effect` advisory-only until the remaining query-layer migration removes route-local state mirroring.
  - Remove redundant manual memoization only where compiler coverage is proven.
  - Validation: frontend lint, focused Vitest, production build, bundle budget; deployed app passed route-performance timing/layout canaries with 12/12 tests and the expanded browser/device/accessibility matrix with 52/52 tests.

- [x] **Large-file decomposition**
  - [x] Split `routes/analytics.tsx` (1,443 lines — grew past the standard after the original pass) into `analytics.tsx` (282, route + data wiring), `analytics.lib.ts` (297, pure helpers), `analytics.charts.tsx` (431, chart/table primitives), `analytics.sections.tsx` (536, composed views); static-import siblings keep one route chunk (33.23 KiB byte-comparable), test imports resolve via re-exports, `'use no memo'` pragma carried to hook-bearing siblings. 2026-06-10.
  - [x] Extract shared Databricks projection/redaction helpers into `backend/services/repositories/databricks_shared.py` while preserving `databricks_repo.py` compatibility imports.
  - [x] Split `LeadTable.tsx` into CSV, pure table logic, constants/types, expanded-row preview, and decision-panel modules.
  - [x] Split `USChoroplethMap.tsx` geometry/bucketing/county-label helpers into a tested utility module.
  - [x] Split `GenieAnswer.tsx` markdown/restatement rendering into a focused module.
  - [x] Split `GenieAnswer.tsx` chart, proof-panel, governed-action, and pure table/chart logic into focused modules.
  - [x] Extract canonical Genie SQL/prompt-scope helpers from `backend/services/repositories/databricks_repo.py` into `backend/services/repositories/databricks_genie_canonical.py` while preserving compatibility re-exports.
  - [x] Extract pure Portfolio repository helpers and `DatabricksPortfolioRepository` into `backend/services/repositories/databricks_portfolio.py` while preserving the `databricks_repo.py` compatibility import path.
  - [x] Extract `DatabricksLeadRepository` into `backend/services/repositories/databricks_leads.py` while preserving the `databricks_repo.py` compatibility import path.
  - [x] Extract `DatabricksBorrowerRepository`, `DatabricksOfferRepository`, and `DatabricksOutreachRepository` into `backend/services/repositories/databricks_borrowers.py` while preserving the `databricks_repo.py` compatibility import path.
  - [x] Extract `DatabricksGenieRepository` and Genie response/canonical-answer helpers into `backend/services/repositories/databricks_genie.py` while preserving `databricks_repo.py` compatibility re-exports for current and hidden tests.
  - [x] Extract `DatabricksSegmentRepository` and `DatabricksGeoRepository` into `backend/services/repositories/databricks_geo.py` while preserving the `databricks_repo.py` compatibility import path.
  - [x] Extract Genie SQL policy/parser helpers into `backend/services/repositories/databricks_genie_policy.py` so the security-critical SQL parser surface is isolated from response/action rendering.
  - [x] Extract Genie governed-action route/criteria helpers into `backend/services/repositories/databricks_genie_actions.py` so row-to-action derivation is isolated from Genie response adaptation and trusted-SQL proofing.
  - [x] Split `portfolio-builder.tsx` filter/url/campaign helpers into `portfolio-builder.logic.ts`, state picker into `portfolio-builder.components.tsx`, and focused helper tests.
  - [x] Split `offer-orchestrator.tsx` borrower cache, constants/types, pure label helpers, and presentational panels into focused route-local modules.
  - [x] Preserve the `databricks_portfolio.py` Lakebase compatibility shim intentionally: visible and hidden tests still patch the historical `databricks_repo.get_lakebase_client` seam, so removing it is not a no-behavior-change cleanup today.
  - Current size checkpoint: `databricks_repo.py` 172 LOC, `databricks_genie.py` 1,384 LOC, `databricks_genie_actions.py` 413 LOC, `databricks_genie_policy.py` 330 LOC, `databricks_geo.py` 760 LOC, `databricks_portfolio.py` 875 LOC, `databricks_borrowers.py` 345 LOC, `databricks_leads.py` 536 LOC, `LeadTable.tsx` 1,059 LOC, `USChoroplethMap.tsx` 965 LOC, `GenieAnswer.tsx` 225 LOC, `portfolio-builder.tsx` 638 LOC, `offer-orchestrator.tsx` 576 LOC.
  - Validation: borrower extraction focused gates pass (`ruff`, repository `compileall`, borrower/search/schema/PII/marketing tests, and broader borrower/offer/audit integration tests); Genie action extraction focused gates pass (`ruff`, repository `compileall`, Genie repository/policy/action tests, public schema guards, and portfolio compatibility tests); full backend/frontend local gates pass; restored deployed app passed `scripts/smoke_live.sh --no-genie` and route-performance timing/layout canaries with 12/12 tests.

- [x] **CI bundle performance budgets**
  - Add bundle analyzer output on every frontend build.
  - Fail CI if initial JS, route chunks, CSS, or font budgets regress beyond agreed thresholds.
  - Validation: `npm run budget` green on current build.

- [x] **Playwright route timing and overlap gate**
  - [x] Add a browser gate for Home, Lead Queue, Segment Intelligence, Borrower 360, Offer, Ask Genie, and Admin.
  - [x] Assert no obvious text/control overlap, no horizontal overflow, no `undefined`/`NaN` leakage, and bounded route-load timings on desktop.
  - [x] Add mobile shell canaries for Home, Segment Intelligence, Lead Queue, and Ask Genie.
  - Validation: deployed active app passed `E2E_LIVE=1 npm --prefix frontend run e2e -- tests/e2e/route_performance.spec.ts --project=chromium --reporter=list --workers=1` with 12/12 tests, including the audit-safe Lead Queue canary.

- [x] **Server compression, static caching, and config caching**
  - Add gzip compression for app responses and static assets.
  - Add immutable `Cache-Control` for hashed Vite assets while keeping `index.html` no-store.
  - Cache `/api/config/footprint` and `/api/config/options` on a short TTL.
  - 2026-06-10 upgrade: build-time precompression + content negotiation. `tools/precompress_assets.mjs` (Node built-ins only, wired into `npm run build`) emits `.br`/`.gz` siblings for hashed assets — measured raw 932.81 KiB → brotli 254.09 KiB (−73%) vs gzip 291.80 KiB (−69%). `backend/services/static_assets.py` + an explicit `/assets/{path}` route serve the smallest accepted variant (`Vary: Accept-Encoding`, media type from the original suffix, traversal-guarded); identity fallback keeps un-precompressed dists working. Runtime `GZipMiddleware` now `compresslevel=6` and only covers dynamic JSON (Starlette skips already-encoded responses). Deploy/CI inherit the step for free via the build script; `databricks.yml` syncs `frontend/dist/**` so variants upload with the bundle.
  - Validation: `tests/unit/test_api_boundaries.py`, `tests/unit/test_config_cache.py`, frontend build/budget; `tests/unit/test_static_assets.py` (negotiation, q-values, traversal, media types).

- [x] **Frontend query client, font trim, and print basics**
  - Add TanStack Query client defaults and operational invalidation helpers.
  - Disable automatic focus refetch by default because several reads intentionally write audit events.
  - Clear the client cache when `/api/health` reports a changed actor cache key.
  - Trim Geist imports to the weights the design system actually uses.
  - Add `@media print` stylesheet for dossier/audit surfaces.
  - Validation: frontend lint/test/build/budget, backend health endpoint tests.

- [x] **Route and hidden-panel chunking**
  - Lazy-load route modules with explicit preload handles.
  - Prefetch likely next route chunks after first idle and route chunks on nav hover/focus.
  - Lazy-load default-hidden Genie and workspace Console panels while preserving shell affordances.
  - Move RUM observer implementation and warming-up retry hook code out of the critical bootstrap chunk.
  - Validation: frontend lint/test/build/budget, Playwright spec collection, route-performance no-protected-prefetch canary.

## P2 - Hygiene and Future Readiness

- [x] **Root repo artifact cleanup**
  - [x] Remove local ignored root screenshot artifacts.
  - [x] Remove tracked build metadata such as `tsconfig.tsbuildinfo`.
  - [x] Ignore root `tsconfig.tsbuildinfo` so TypeScript incremental builds do not dirty the repo.
  - Keep committed prototype and Playwright visual snapshots in Git as intentional design/regression contracts; keep generated `test-results/`, `playwright-report/`, and `tsconfig.tsbuildinfo` local-only.
  - Validation: generated-output audit cleaned `frontend/tsconfig.tsbuildinfo`, `frontend/test-results`, and root `test-results`; `git ls-files` shows only intentional prototype/validation/snapshot images and the deleted root `tsconfig.tsbuildinfo` removal.

- [x] **Query-layer completion**
  - [x] Move high-value route reads onto QueryClient where it improves consistency.
  - [x] Add mutation invalidation after Lead Queue and Offer Orchestrator approve/reject/assignment/disposition changes.
  - [x] Add Home -> Segments -> Home cache canary so hot Home reads do not refetch inside the stale window.
  - [x] Keep Lead Queue static prefetch audit-safe: hover/focus/row-expand must not call `/api/borrowers/{id}` because that endpoint writes a governed `VIEW_BORROWER` audit row.
  - [x] Move AppContext workspace hydration, FootprintProvider footprint hydration, Portfolio Builder config options, and Portfolio Builder campaign list reads onto QueryClient.
  - [x] Leave `HealthProvider` outside QueryClient by design: the actor-cache key drives QueryClient clearing on identity changes, so moving health into the same cache could erase the boundary signal before the reset completes.
  - [x] Keep focus/refocus behavior audit-safe: QueryClient disables automatic focus refetch by default, and governed dossier/audit/Genie paths opt out of focus refetch rather than silently rereading evidence on tab focus.
  - [x] Keep static prefetch audit-safe: hover/focus/idle preloads are code/data-neutral and do not call governed borrower, lead, audit, or evidence endpoints.
  - [x] Keep mutation invalidation bounded: approve/reject/assign/disposition actions mark operational query families stale with `refetchType: 'none'`, so user actions do not force active `VIEW_LEADS` / `VIEW_BORROWER` rereads. A future navigation or explicit refresh may reread by user intent.
  - Keep conversational Genie send flows as user-triggered query/mutation fetches with focus refetch disabled; do not prefetch or background-refresh conversation answers.
  - Validation: route-performance spec now asserts hover/focus/row-expand issue zero governed borrower dossier reads, and explicit Borrower 360 navigation is the first dossier read; `frontend/src/lib/queryClient.test.ts` asserts operational invalidation does not touch footprint, config options, campaigns, workspace, or Genie start.

- [x] **Evidence drawer prefetch**
  - Prefetch the evidence drawer/source mapping chunk after first route idle.
  - Validation: first source-chip source mapping is in a lazy chunk, initial JS budget remains within threshold, protected-data prefetch canary confirms no borrower/lead/audit/evidence API reads happen on static prefetch.

- [x] **Cross-browser and device matrix**
  - [x] Add Playwright Chromium/WebKit/Firefox plus phone and tablet projects behind `E2E_BROWSER_MATRIX=1`.
  - [x] Cover all eight desktop routes, primary nav links, theme toggle, Console density controls, Lead Queue, Segment map, Borrower 360, Offer, Ask Genie, and Admin shell health.
  - [x] Add phone/tablet canaries for Home, Segments, Lead Queue, Ask Genie, and Admin.
  - [x] Wire the nightly deployed-app workflow to install Chromium/Firefox/WebKit and run the procurement/browser-device specs with `E2E_BROWSER_MATRIX=1`.
  - Validation: default `npm --prefix frontend run e2e -- --list`; expanded `E2E_LIVE=1 E2E_BROWSER_MATRIX=1 npm --prefix frontend run e2e -- --list` collects 52 bounded desktop/device/a11y tests across Chromium, Firefox, WebKit, Pixel 7, iPhone 15, and iPad Pro 11 landscape; `.github/workflows/nightly.yml` parses as YAML; deployed active app passed `E2E_LIVE=1 E2E_BROWSER_MATRIX=1 npm --prefix frontend run e2e -- tests/e2e/accessibility_procurement.spec.ts tests/e2e/cross_browser_matrix.spec.ts --reporter=list --workers=1` with 52/52 tests.

- [x] **Accessibility procurement gate**
  - [x] Expand beyond serious/critical axe checks to keyboard-only focus order, named controls, target sizing, reduced motion, and Lead Queue virtualization/keyboard row a11y.
  - [x] Raise compact control hit areas to meet the WCAG 2.2 AA 24x24 CSS px floor without changing the dense table layout contract.
  - [x] Make the local Playwright webServer path deterministic by invoking repo-local `.venv/bin/python -m uvicorn` and `npm --prefix frontend run dev`.
  - [x] Wire the deployed nightly job to run the procurement accessibility spec as the Chromium accessibility gate while browser/device coverage stays route-focused.
  - Validation: local Chromium live harness exposed and fixed focus-order, target-size, reduced-motion, and false-skip issues; deployed active app passed the authenticated procurement accessibility gate with live Lead Queue rows, including virtualized row metadata and keyboard expansion.
  - 2026-06-10 polish: choropleth keyboard-drill discoverability (`.map-legend__hint` revealed on `:focus-within`, `aria-keyshortcuts="Enter"` on focusable geographies; copy matches the real Enter/Space handlers — no Esc claim because the map has no Esc handler); LeadTable bulk-approve focus restoration (refocus trigger on partial outcome, focusable table region on full success; pinned by two new pure-helper Vitest cases); Genie dialog `aria-keyshortcuts="Escape"` + "Close (Esc)" title; `USChoroplethMapTooltip` BEM extension beyond the prototype vocabulary documented in-code per the CLAUDE.md deviation rule (all extended classes verified to have backing CSS).

- [x] **Dependency update automation disabled for branch hygiene**
  - Dependabot version-update config was removed to preserve the repo contract that `main` stays the only persistent branch unless a human creates a bounded feature branch.
  - Dependency updates remain manual, intentional feature-branch work covered by CI audit, lint, tests, build, bundle budget, source hygiene, and Playwright gates.
  - Validation: no `.github/dependabot.yml` is present; `git ls-remote --heads origin` shows no Dependabot branches; `gh pr list --state open` shows no Dependabot PRs.

- [x] **Interaction-affordance polish**
  - [x] Make Home data-estate lane chips and asset rows expandable/clickable with contract status, row count, freshness, governed object, proof status, and lineage drawer access using existing `/api/data-estate` data only.
  - [x] Add composer-level sample-question chips to `/ask-genie`, matching the floating Genie panel suggestions without adding background governed reads.
  - [x] Make `/ask-genie` trusted assets actionable: clicking a trusted asset opens the existing drawer context, marks the asset active, and scopes the next question to the exact UC path.
  - [x] Replace the display-only Offer Orchestrator follow-up chip with a link to the governed Admin offer-rules panel.
  - [x] Make the Admin rules-version chip toggle the in-page offer-rules detail panel and expose the deterministic rules hash as an explicit row.
  - Validation: focused Vitest and ESLint/TypeScript checks for Ask Genie/design-system affordances, plus frontend build/e2e gates in the checkpoint below.

## Deferred By Design

- [?] **SSR / framework migration**
  - Current architecture remains React SPA + FastAPI inside Databricks Apps. Do not migrate to Next.js/SSR/RSC unless a concrete requirement appears.

- [?] **Service worker / offline mode**
  - Current product must reflect live Unity Catalog/Lakebase state or fail visibly. Offline cache is not aligned with the governance posture unless scoped to immutable static assets.

- [?] **GraphQL/BFF rewrite**
  - Current typed REST contracts are explicit and auditable. Revisit only if cross-page composition becomes a bottleneck.

## Validation Checkpoint

Latest local validation pass after the current modernization tranche:

- `.venv/bin/ruff check backend tests tools jobs pipelines`
- `.venv/bin/python -m pytest -q`
- `databricks bundle validate -t dev --profile DEFAULT`
- `databricks bundle validate -t ci --profile DEFAULT`
- `databricks bundle validate -t prod_otlp --profile DEFAULT --var genie_space_id=<governed-genie-space-id>`
- `npm --prefix frontend run lint`
- `npm --prefix frontend run test`
- `npm --prefix frontend run build`
- `npm --prefix frontend run budget`
- `npm --prefix frontend run e2e -- --list`
- `npm --prefix frontend run e2e -- tests/e2e/route_performance.spec.ts --project=chromium --reporter=list --workers=1` (non-live skip guard: local web servers start, then all 12 tests skip because the spec requires `E2E_LIVE=1`)
- Vitest result: 177 tests passed across 31 files.
- OTLP production-retention setup result: `prod_otlp` validates and declares an `otel_headers` app secret resource pointing at the configured Databricks secret scope/key with `READ`; `tools/databricks/otlp_deploy_payload.py` emits the full app env list with `MIP_OTEL_HEADERS` set by `value_from`, never a header value. A temporary deployed proof verified `MIP_OTEL_HEADERS` resolved through Databricks Secrets by showing a collector request with the expected proof header; `tools/databricks/otlp_customer_retention_gate.py` now validates customer evidence packets, but customer durable retention still needs customer collector, real customer secret, retention/ACL proof, and collector query proof.

Fresh deployed evidence (2026-06-11, perf/polish slice — geo singleflight, precompression, analytics decomposition, a11y polish, budget policy):

- Deploy: `./scripts/deploy.sh -t dev --skip-silver --no-confirm` — bundle validate/plan/deploy completed ("0 to add, 0 to change, 0 to delete, 15 unchanged"). The explicit app-snapshot step initially failed twice on real platform races (app auto-STOPPED; then the bundle deploy's own triggered app deployment still IN_PROGRESS). Root-caused and fixed in `scripts/deploy.sh` with `wait_for_app_deployable()` (starts a stopped app, polls pending/active deployments before promoting). Final app deployment `SUCCEEDED` at 2026-06-11T02:16:27Z with compute `ACTIVE`.
- Live health: `/api/v1/health` returned `status=ok`, `mode=live`, warehouse/lakebase/genie `up`, all breakers `closed`.
- Live brotli proof (new negotiated `/assets` route): `index-*.js` with `Accept-Encoding: br` → `content-encoding: br`, 69,799 bytes vs 262,756 identity (−73%), `content-type: text/javascript`, `vary: Accept-Encoding`, `cache-control: public, max-age=31536000, immutable`; gzip negotiation → 81,012 bytes.
- Live RUM: sanitized batch → `202` (`enabled:false` — sandbox env keeps ingestion off while schema validation stays on); borrower-ID route → `422`.
- Live smoke: `scripts/smoke_live.sh` full PASS — health, portfolio preview, ranked leads, borrower dossier, evidence timeline, data estate proof, admin gate rejection, geo state/county/zip rollups (now single-flight + stale-if-error server-side), outreach draft, outreach approval audit write, genie message.
- Live Playwright: all four `real_data.spec.ts` groups passed (`set -euo pipefail` aborts on any group failure; one conditional skip: `lead-queue: inline approval writes selected evidence ids to audit`); `route_performance.spec.ts` 14/14 passed including timing/overlap canaries, Home cache canary, and the governed-read prefetch canaries.
- UX-truthfulness slice (2026-06-11, operator-reported): (1) the evidence-drawer "Not exposed here" disclosure read as an ACL denial to a workspace admin — recopy'd to "Privacy by design" stating it applies regardless of permissions; (2) failed governed-metadata reads rendered as "Freshness Unavailable", conflating outage/403 with "source has no timestamp" — the chip now has distinct checking/error states; (3) the hero population + itm KPI drawers had no governed anchor at all, so the metadata read never fired — both now anchor to `mip.gold.borrower_360`; (4) clock times: the wire mixes naive-UTC and ISO-Z strings, and `new Date()` parsed naive as viewer-LOCAL (a 03:32 UTC refresh rendered as "Jun 11 03:32 AM") — new `frontend/src/lib/time.ts` pins naive strings to UTC and every clock-time surface (admin runs, audit log, drawers, data estate, Genie proof, asset cards) renders with an explicit short zone name; (5) the "Configured tenant lens" signal row could collide value and label text — the grid value column is capped at `fit-content(50%)` with wrap on both columns. Verified in real Chromium against the deployed app: drawer shows "Fresh"/"live", 5.16M rows, "Modified Jun 11, 2026, 1:36 AM EDT", "Business refresh: Jun 11, 2026, 1:35 AM EDT"; audit rows "01:39:52 EDT"; admin "Latest refresh · Jun 11, 2026, 1:35 AM EDT"; zero console errors. 272 Vitest cases green (12 new time-lib tests).
- Admin-allowlist incident (2026-06-11, post-deploy): every admin surface (asset detail, the admin-gated audit feed) 403'd for all users. Root cause chain: the security sweep correctly emptied the `admin_emails` source default; Databricks Apps deployment `env_vars` are a full replacement; `.env.local` lacked `MIP_ADMIN_EMAILS`; and the payload deliberately does not bootstrap the deployer into admin (pinned by `test_app_deploy_payload`). Resolution kept that governance posture: redeployed with `MIP_ADMIN_EMAILS` exported as an explicit operator decision; `scripts/deploy.sh` preflight now warns loudly when the allowlist resolves empty; `scripts/smoke_live.sh` admin-gate probe is posture-aware (403 proves deny path for non-admin bearers; 200 for a configured admin bearer falls through to the full governed-payload contract checks). Verified in real Chromium through the Apps edge: `/api/v1/audit/events` 200, home renders with no audit-unavailable banner, and `/data-estate/assets/borrower_360` shows the full governed metadata view (rows/files/size/freshness, proof panel) with no "Admin access required" hero.

Retained deployed evidence from the previous deploy tranche (not a fresh deploy of the local interaction-affordance edits above):

- Active deployment smoke: `databricks bundle deploy -t dev --profile DEFAULT` and direct snapshot deploy from the bundle workspace files path completed.
- Live health result: `/api/health` returned `status=ok`, `mode=live`, `warehouse/lakebase/genie=up`; `/api/admin/health` returned all breakers closed, `recent_errors_count=0`, `fallback_identity_fallbacks_total=0`, and `log_export=stdout-only`.
- Live route-performance result: 12/12 passed against the active deployment, including the audit-safe Lead Queue canary that hover/focus/row-expand issue zero governed borrower dossier reads.
- Live browser/device/accessibility result: 52/52 passed against the active deployment across Chromium, Firefox, WebKit, Pixel 7, iPhone 15, and iPad Pro 11 landscape.
- Live smoke result: `scripts/smoke_live.sh --no-genie` passed against the active deployment, including health, ranked leads, borrower dossier, evidence timeline, data estate proof, source readiness, geo rollups, outreach draft, and outreach approval audit write.
- Live RUM/OTLP result: batched sanitized telemetry accepted with `202`; borrower-ID route rejected with `422`; local self-contained OTLP proof verifies stdout-only boot fallback plus mocked exporter redaction; temporary deployed collector proof showed `/api/admin/health log_export=otlp` and collector receipt for the matching sanitized RUM correlation id; temporary secret-backed proof showed `/api/admin/health log_export=otlp`, `MIP_OTEL_HEADERS value_from=otel_headers`, a matching collector request, and the expected proof header; the active sandbox was then restored to `stdout-only`, the app secret resource was removed, and the temporary Databricks secret was deleted.
- Live Lakebase result: a synthetic borrower fixture was drafted/approved/assigned/dispositioned with lifecycle readback and audit rows present; five concurrent approve retries for a second synthetic borrower fixture with one governed request id returned one approval id and one audit row. Exact borrower, request, and audit identifiers are intentionally omitted from the tracker to avoid carrying governed row IDs in repo docs.

Current bundle budget evidence (2026-06-10 re-baseline — gates are now actuals + ~5% headroom by documented policy in `tools/check_frontend_budgets.mjs`; five gates TIGHTENED, the zero-headroom aggregate gate given real margin):

- Initial JS: 256.60 KiB raw / 79.03 KiB gzip, below the 270 KiB / 83 KiB gate (gate tightened from 300/90).
- Initial CSS: 101.22 KiB raw / 18.13 KiB gzip, below the 107 KiB / 19.1 KiB gate.
- Total JS: 832.42 KiB raw / 274.22 KiB gzip across 36 chunks, below the 875 KiB / 288 KiB gate (the old 832 KiB gate had 0.03 KiB of slack and tripped on a 0.4 KiB a11y fix — exactly the failure mode the headroom policy now prevents).
- Largest lazy JS: shared components+drawerSources chunk 98.40 KiB raw / 32.06 KiB gzip, below the 104 KiB / 34 KiB gate (gate tightened from 160/60).
- Fonts: 14 files / 215.42 KiB, below the exact-14-file / 227 KiB gate.
- Precompression: 35 assets emit `.br`/`.gz` siblings at build time — raw 932.81 KiB → brotli 254.09 KiB (−73%) / gzip 291.80 KiB (−69%); served via content negotiation on `/assets`, excluded from budget accounting as strictly-smaller duplicates.
- Known-cosmetic build warning: rolldown `INEFFECTIVE_DYNAMIC_IMPORT` on `lib/drawerSources.ts` — the AppShell idle `import()` intentionally warms the SHARED lazy chunk that static importers already place it in; initial JS is byte-identical with/without it (documented at the preloader site in `AppShell.tsx`).

Latest browser-gate collection:

- Default Chromium Playwright collection: 126 tests in 9 files.
- Route-performance non-live skip guard: local web servers start, then 12 tests skip until `E2E_LIVE=1` is supplied.
- Expanded browser/device matrix collection: 52 tests in 2 files, bounded by project-level grep across Chromium, Firefox, WebKit, Pixel 7, iPhone 15, and iPad Pro 11 landscape.

Remaining open items before declaring the whole modernization tracker closed:

- None in repo. Customer durable log retention, Cotality MLS/Permits, and per-release customer evidence are external/environment gates tracked in `docs/observability.md`, `docs/data-sources-gap-analysis.md`, and `docs/enterprise-readiness-checklist.md`.

## 2026-06-11 Full-Stack Audit Response (independent validation + remediation)

An external full-stack audit (now committed, marked Internal, at
`docs/audits/full-stack-audit-2026-06-11.md`) was independently validated
claim-by-claim — three read-only validation agents (backend / SQL+bundle /
frontend+hygiene) plus first-party reproduction — and every claim judged
valid was remediated on `fix/audit-2026-06-11-remediation`. Scorecard:
**of the 6 P1s: 5 confirmed + fixed, 1 split (P1-2 is a decision, recorded
below). Of the P2/P3 set: most confirmed + fixed; 5 claims REFUTED with
evidence; 2 deliberately deferred with rationale.**

Confirmed + fixed (commit refs):
- P1-1 scoring parity (e9d08c5): reproduced exactly — float sum
  85.49999999999999 vs exact 85.5 on (92,94,94,85,25); 0.666% lattice
  divergence. `lead_score` now computes in `decimal.Decimal` +
  ROUND_HALF_EVEN; drift-zone golden case_13 pinned in JSON + SQL harness;
  200k seeded sweep + exhaustive half-boundary lattice vs an independent
  integer-hundredths oracle.
- P1-5 narrative seed (99705ff): five REAL `gold.borrower_360` IDs
  (state-consistent with their campaigns; rationale stats match live
  dossiers), schema migration purges legacy `B-\d{5}` rows + NOT
  VALID→validate CHECK on the masked-ID format, deterministic re-selection
  helper `tools/select_narrative_borrowers.sql`, contract tests.
- P1-6 leads hot path (fdb3fdc): measured live 6.6/4.3s cold vs 0.95s warm.
  Root causes: the fail-closed `marketing_eligibility="Eligible only"`
  default routes even filterless requests down the borrower_360 5.16M-row
  path, AND the freshness marker embedded `time_ns()` making every query
  textually unique (warehouse result cache useless). Fixed with
  startup + refresh-ahead warming of the exact route-default cache keys
  (parity pinned by a zero-extra-SQL test) and TTL-bucketed freshness
  markers (zero added staleness; cross-worker warehouse-cache reuse).
  Plus the home Genie CTA full-page reload → SPA navigate.
- P1-3/P1-4 fresh-workspace deploy (cfa359a): deploy.sh step 4c applies
  UC grants to the app SP (client id resolved from `databricks apps get`,
  fatal-with-pointer on failure), the Lakebase migrate runner applies the
  GRANTS.md role matrix (pg_roles discovery, append-only audit preserved),
  step 4d provisions `mip`/`pii-salt-v1` create-if-missing/never-rotate;
  the SQL path's silent fallback to a source-committed salt literal was
  REMOVED (predictable hashing, silently — worse than failing); dead DLT
  fallback constant + phantom "preflight" comment cleaned.
- P2-5 approver gate (ebb7cb9): optional `MIP_APPROVER_EMAILS` allowlist
  on /outreach/approve|reject — empty default preserves the documented
  Module 0 demo posture; admins always pass (allowlist-incident lesson).
- P2-7 fair-lending over-blocking (ebb7cb9): safe-phrase masking (loan-age
  vocabulary, protected-token+geographic-noun compounds) before the
  protected-term scan; phrase-local so protected usage still refuses;
  12-case allow/refuse matrix pinned.
- P2-8/10/11/12 SQL plane (045f9e8): all 13 gold CTAS re-declare CLUSTER
  BY / column COMMENTs / TBLPROPERTIES (programmatic DDL parity check);
  QUALIFY dedup guards on 4 silver MERGEs; `ltv` now prefers CLTV exactly
  like `equity_pct`; metric-view COMMENTs distinguish real columns from
  read-time aggregations.
- P2-1 data-estate shredding (fd0c36f): live-reproduced (65px label
  column, 5–6 line mid-word wraps with Console open — overruling a
  validator's INVALID verdict); lanes now auto-fit with a 13.5rem floor.
- P2-9 dead mirrors (8257662, 626d0d9): resources/*.yml + jobs/*.yml
  mirrors deleted; anti-regression guard keeps them dead; CLAUDE.md
  updated; empty `mip_snapshot_dashboards` removed; bundle validates.
- P2-14 type gate (a23d24d): ratcheted mypy across all 106 backend
  modules in CI/make — 21-module shrink-only exemption list for the 73
  pre-existing errors; 7 stale type-ignores removed.
- P2-15 + P3 batch (fd0c36f, 8257662): documented --text-3 WCAG-AA
  divergence both themes; dated TODO on the eslint disable; aria-sort on
  the <th> columnheader (the audit's suggested button placement would be
  invalid ARIA); aria-modal removed from the non-modal Genie panel;
  intentional desktop-hidden FAB documented; DC/PR/VI display labels;
  FRED readiness row_count now observation volume; FILE_MANIFEST deleted;
  tsbuildinfo untracked; dead frontend files removed; CHANGELOG +
  CLAUDE.md drift corrected; deploy env-template name fixed;
  MIP_COTALITY_ID_MASK_SECRET preflight warning; file-size allowlist
  expiry documented as the deliberate post-Summit forcing function.

REFUTED audit claims (evidence, no change made):
- P2-6 "audit INSERT not idempotent under retry": the approval INSERT is
  `ON CONFLICT (request_id) DO NOTHING RETURNING` and the audit event is
  written in the SAME transaction only when the row actually inserted
  (`backend/api/outreach.py:104-288`); retries return the existing
  approval id with NO second audit row. Pinned since R5-01 by
  `test_outreach_reject.py:458/632/706/764`.
- P3 "TTLCache can't cache falsy": `resilience.py` uses `is not None`
  sentinels throughout; empty lists/0 cache correctly.
- P2-13 "prod run_as drift": the cited comment is a customer-SE checklist
  instruction, not a claim about current state.
- P3 "Genie FAB 0x0 at desktop": parity with the prototype's topbar
  entry (Module 0 Prototype.html:1237); now documented at the CSS site.
- P2-7(session) "each panel open fires /genie/start": `genie_start` is a
  lightweight Lakebase latest-conversation read + static content; no
  remote Genie conversation is created until first message. Working as
  designed.

Found BEYOND the audit (its sandbox could not run pytest):
- Import-time `load_dotenv` in `provision_genie_space.py` poisoned the
  whole pytest process with the operator's `.env.local` (which now
  contains MIP_ADMIN_EMAILS per deploy docs) — flipping the fail-closed
  settings contract and no-bootstrap payload tests on dev machines while
  CI stayed green. Fixed by moving the overlay under the true `__main__`
  guard (an intermediate fix inside `main(argv)` reproduced the leak via
  the dry-run test — proven empirically), a dotfile seam + operator-var
  scrub in the payload tests, and a new AST guardrail banning
  module-scope `load_dotenv` (28fd452).

Decisions recorded:
- P1-2 design fork: per CLAUDE.md, `design_files/` IS the design
  contract; the claude.ai share link's current iteration (Acme Lending —
  which violates the Summit Mortgage naming rule — different hero/KPIs)
  is treated as a divergent draft, NOT canonical. OPERATOR ACTION: update
  the share link to match the repo snapshot before side-by-side demos
  (John West Thu/Fri, Movement Tue 3p), or explicitly bless its deltas
  through the normal design_files change process.
- P2-2 awaiting-feed framing: already decided in
  `docs/module0-talk-track.md` (honest pending-feed cards + "predicates
  auto-unblock" language); chasing the Cotality MLS/permits shares
  remains an external partner item.
- P2-4 router-layer drift (~3k lines): pure-move refactors deliberately
  DEFERRED past Summit — churn risk with zero user-visible gain days
  before the booth; scheduled with the 2026-06-21 file-size-allowlist
  forcing function.
- P3 dependency bumps: deferred post-Summit per the audit's own
  recommendation.
- Legacy `module0.spec.ts` still pins fixture-era B-48291 values and
  cannot pass against real data (pre-existing); superseded by the
  real-data nightly spec — post-Summit cleanup candidate.

### 2026-06-11 audit-remediation deployed evidence (signoff ritual)

- Deploy: `./scripts/deploy.sh -t dev --no-confirm` — full 15-step pipeline.
  Two real failures found AND fixed by the run itself: (1) `databricks
  secrets list-scopes -o json` emits a bare array on current CLI (parser now
  accepts both shapes); (2) the typeless CTAS column-COMMENT list is a
  PARSE_SYNTAX_ERROR on DBSQL — first live gold run failed exactly as the
  data-modeler agent's risk note warned; rewritten to bare CTAS (CLUSTER BY
  + TBLPROPERTIES retained) + post-CTAS `COMMENT ON COLUMN` statements (269
  across 13 files), proven by `mip_refresh_scores` TERMINATED SUCCESS.
- New deploy steps proven live: Lakebase migrate applied schema + real-ID
  seed + app-role grants (`pg_roles` discovery hit the SP client-id role);
  UC grants step issued all three GRANTs through the warehouse;
  pii-salt step short-circuited on the existing secret ("never rotate");
  MIP_COTALITY_ID_MASK_SECRET preflight warning fired (expected: sandbox).
- P2-8 proven by DESCRIBE after refresh: `property_owner_bridge` clustering
  ["owner_link_id"], borrower_360 ["state","clip"], autoOptimize
  TBLPROPERTIES present, 61 commented columns on borrower_360.
- Final smoke: 12/12 ok + PASS against the deployed app.
- Verification battery (live, authenticated):
  - `/api/v1/leads` ×3: 2175ms → 144ms → 159ms (audit baseline: 3.6-5.4s on
    EVERY load); boot warm + refresh-ahead active.
  - P1-1 proof sweep: 25 live borrowers, 0 integrity gaps, 0 non-200s.
  - Seed trio resolves as real dossiers: B-0CPWBTJMAPFY2 (IL, 70),
    B-1IB0UGBTFYM20 (TX, 69), B-102FL7THC6Q3L (IL, 88).
  - Fair-lending live: "average loan age in Illinois" → source=genie
    (answered); "Average borrower age" → source=refused.
  - Data-estate with Console rail open: worst label 3 word-boundary lines @
    108px (was 6 mid-word lines @ 65px). A second live probe then caught the
    "demo synthetic" governance chip overflowing floor-width lanes by
    68-81px — fixed (meta wraps under the name; chip never ellipsized) and
    re-proven: zero overflow elements, zero chip overlaps; screenshot
    inspected.
  - Home "Ask Genie" CTA: SPA navigation, 0 full reloads.
  - Lead queue: 7 `th[aria-sort]` columnheaders; zero console errors.

## 2026-06-11 Re-Audit Response (adversarial verification of the signoff)

The re-audit (`docs/audits/re-audit-2026-06-11-post-remediation.md`)
adjudicated the remediation signoff: 15 confirmed / 4 partial / 1 signoff
claim refuted, 4 of 5 original-audit refutations sustained, 1 overturned.
Every correction and new finding was independently re-verified and fixed
on `fix/re-audit-2026-06-11-response`. Verdicts on its claims: ALL
sustained — including two it understated:

- **mypy wildcard (worse than stated):** my "backend/api is clean" probe
  had auto-loaded pyproject, so the `backend.api.*` wildcard suppressed
  its own evidence; removal exposed 43 errors in 12 routers. Honest
  ratchet now: 12 routers enumerated explicitly (scheduled with the
  post-Summit P2-4 router slice — same files), adoption ledger corrected
  to 33 modules / 116 errors, and `test_typecheck_ratchet.py` fails on
  any addition, wildcard, or stale ledger (60caf58).
- **Genie FAB (overturned, conceded):** the prototype renders the fixed
  FAB at ALL widths (Module 0 Prototype.html:773-785) — my earlier
  validator conflated "topbar entry exists" with "FAB absent." The
  desktop hide is reclassified as a documented deviation-by-choice
  (34e58d4); tracker + CSS comment corrected.
- **CTAS-metadata escapes closed (ee03ebd):** the lifecycle sync job's
  bare rebuild (which deploy runs right AFTER the gold refresh restores
  metadata) now re-declares clustering/properties and re-applies the DDL
  comments; demo_first_party_feeds re-applies all five tables' table+
  column comments. NEW `test_gold_column_comment_guard.py` pins
  transformation/job comments == DDL both directions for every rebuild
  surface — its first runs caught real pre-existing equity/ltv DDL drift
  plus two parser traps (multiline ARRAY<STRUCT> and a `'< 0.03'`
  comment that defeated bracket counting).
- **equity + ltv = 101 (ee03ebd):** Spark ROUND is half-up, so an
  exact-.5 CLTV rounded both ways; equity_pct is now
  100 - <the same clamped/rounded ltv expression> (complement by
  construction, no-signal default stays 0), DDL + COMMENT wording
  aligned, construction test-pinned.
- **Frontend truth (34e58d4):** admin-config's zone-naive clock formatter
  routed through lib/time (the re-audit's "every clock time" gap);
  freshness chip gains distinct --loading (accent pulse, reduced-motion
  safe) and --error (warning) states; committed aria-sort render test
  (7 columnheaders, none -> descending -> ascending walk).
- **Deploy + Genie (a7a9117):** grants step retries 3x to absorb
  warehouse warm-up before declaring authority failure; the Genie space
  defines loan age = time since ORIGINATION (lockin_cohort), bans
  refresh/ingest timestamps as loan-age inputs (live answer had returned
  0.00 years from the refresh date). STAGE NOTE: avoid loan-age
  questions in the booth demo until the rebound space is spot-checked.
- **Docs truth (6ef050b):** silver_property_master header + data-contract
  §7 no longer describe the removed salt fallback or call rotation
  acceptable (borrower_id is salt-independent xxhash64(clip) — precision
  the re-audit's own wording missed); .env.example documents the salt's
  secret-scope home; leads freshness docstring carries the honest ~2x-TTL
  compounded staleness bound; CLAUDE.md says eleven route modules; the
  data-modeler memory that taught the live-parse-error CTAS syntax as
  "working" is rewritten around the proven COMMENT ON COLUMN pattern;
  hermeticity guard scans jobs/ + tests/ too.

Re-audit notes accepted without code change: warm-after-idle first hit
(~2.6s after app restarts — the rewarm loop is process-local by design;
steady-state repeats are the booth path), and the prod run_as
deploy-from-CI-as-SP recommendation (pre-existing checklist guidance,
prod target untouched this close to Summit).

### Re-audit response deployed evidence (2026-06-11)

- Deploy: full pipeline, exit 0, smoke 12/12 PASS; `mip_refresh_scores`
  and `mip_sync_lifecycle_state` TERMINATED SUCCESS (the sync job's new
  CLUSTER BY/TBLPROPERTIES + COMMENT ON statements parse and run live).
- Complementarity at population scale: 5,156,184 borrower_360 rows —
  4,347,482 sum equity+ltv = 100 exactly, 808,702 no-signal (0+0),
  **0 violations** (the 101% class is dead).
- Lifecycle table after its rebuild: clustering ["borrower_id"],
  autoOptimize properties present, 8/8 column comments.
- Browser (authenticated Chromium): admin audit rows render zoned
  ("Jun 11, 1:50 PM EDT"); the drawer freshness chip walks
  --loading (accent pulse) -> --fresh "Fresh" once governed metadata
  lands; zero console errors.

## Re-audit #3 response (2026-06-12, branch fix/third-audit-response)

Adjudication of "Re-Audit #3: Signoff Adjudication + Exhaustive Functional
Pass" (docs/audits/re-audit-2026-06-11-r3-functional-pass.md). Every claim
verified against code before fixing; one finding came back materially
LARGER than reported, one is refuted-in-code pending live confirmation.

### Part 1 (signoff partials + diff defects) — all accepted

- **Git framing correction (accepted):** the prior signoff said "8
  vertical commits"; the range was 6 vertical commits + the --no-ff
  merge (112b585) + the evidence commit (d46ae95), and origin was
  already up to date at audit time (the operator had pushed mid-session).
  This tracker is the correction.
- **"264 Vitest green" (accepted):** no committed artifact pinned the
  number; this round's full-suite log is quoted below with the actual
  count from `npm --prefix frontend run test`.
- **silver_property_master header overbreadth (fixed):** "rotation
  changes every masked identifier" contradicted the same slice's own
  borrower-id precision; now says rotation shifts owner_name_hash
  surfaces only, borrower_id (xxhash64(clip)) survives.
- **_ddl_comment_map shadowing (fixed — and 18x the audit's estimate):**
  the audit flagged "001 vs 003 first-party tables" as a latent blind
  spot. Conflict-detecting merge revealed EVERY duplicated table was
  shadowed: 165 disagreements — 156 text drifts where 003/004 lagged the
  per-table DDL specs (the live transformations match the per-table
  text), plus 9 first_party column comments (synthetic_demo, feed_mode,
  source_system, customer_key_hash, ...) declared only in 001 and absent
  from the live rebuild surface. Reconciled: numbered files now carry
  canonical text; demo_first_party_feeds.sql re-applies the 9; new
  test_duplicate_ddl_declarations_agree pins cross-file agreement
  forever.

### Part 2 (functional-pass defects)

- **P1 Save-build freeze (fixed):** root cause is window.prompt() — a
  SYNCHRONOUS native dialog that blocks the renderer (and hangs any
  CDP/Playwright session that doesn't handle the dialog; tab-kill is the
  only automation escape). Replaced with an in-page naming form
  (prefilled, Enter submits, Cancel closes). Second latent defect fixed
  with it: the Save guard keyed on `preview === null`, which is false
  during a background refetch of an unchanged build key — Save now
  gates on the hook's new `isFetching` (exposed from useWarmingUpRetry),
  and Run shows "Running…" for the whole in-flight window. Pinned by
  portfolio-builder.save.test.tsx (in-flight disable, no-prompt source
  pin, typed-name create) — the test stubs window.prompt to FAIL if any
  path reaches for it again.
- **A/R hotkeys (fixed the real defect; promise verified-in-code):** the
  window-level handler correctly fires from row-internal focus (button
  focus is not an editable target) — pinned by new
  LeadTable.hotkeys.test.tsx (keydown 'a' bubbling from the borrower
  button approves; editable targets never do). The audit's zero-POST
  experience matches a TERMINAL row: its three attempts followed two
  in-session approvals, and the expanded preview was showing stale
  "Approval: pending" because RowPreview read lead.approval_status and
  ignored the optimistic override — so a by-design no-op looked broken.
  RowPreview now renders the effective approval (same value as the
  status chip), the no-op is test-pinned and legible, and the promise
  copy in both surfaces says "while the expanded row is still pending."
- **Expanded-row actions clipping at ~1413px with Console open (fixed):**
  the colSpan-15 preview spanned the table's full scroll width; the
  inner block is now position:sticky left:0 capped at the main
  container's width (100cqw minus card padding) with container-query
  column collapse at 1280/960 — Approve/Open/Build stay in the visible
  scrollport at any width.
- **Topbar search "dead control" (refuted in code; live confirm
  pending):** the input is a controlled component with debounced
  /api/borrowers/search (borrower exact/prefix, ZIP exact/prefix, city
  contains, county name + FIPS, state name/code, CLIP), result listbox,
  and Enter-to-open. Keystrokes that "render no value" into a controlled
  React input are the signature of untrusted synthetic key events
  (script-dispatched events don't perform text insertion; only trusted
  input does). To be adjudicated live with trusted CDP input post-deploy.
- **Light theme patchwork (no code-level basis found; live confirm
  pending):** data-theme is written to documentElement only; the light
  token block overrides bg/gradient/lines/text completely; no literal
  dark colors outside tokens.css; the axe both-themes suite resolves the
  cascade. The reported white-cards-on-dark-body state is only possible
  if the attribute sat BELOW <body> in the probe's DOM. To be
  adjudicated live by clicking the real toggle.
- **P3 hygiene (fixed):** ZIP-tile -> queue handoff caption (ranked
  leads = scored marketing-eligible subset); Signals "Evidence Events
  Per Day" caption now states which signals carry refresh-batch dates
  (bulk landings) vs true source dates (AVM, market, transfers);
  Economics "Top Borrowers" header note explains owner-labeled borrower
  rows and repeat owners; map breadcrumb crumbs ellipsize
  ("Cook Cou" mid-glyph cut); stale dev-session approvals purged by
  Lakebase migration 2026_06_12_purge_dev_session_approvals (pre-June-1
  state rows + their activation_outbox dependents; canonical narrative
  five kept; immutable action_audit untouched).

### Re-audit #3 response deployed evidence (2026-06-11, merges b21c02d + budget-fix)

- Validation: pytest exit 0; **Vitest 270/270 (50 files)** — the committed
  answer to the "264 uncommitted" partial; ruff/mypy/eslint/CSS-literal
  clean; tsc+vite build exit 0; bundle validate OK.
- Deploy: ./scripts/deploy.sh -t dev --no-confirm exit 0 — migrate,
  silver, FRED, scores, lifecycle sync all TERMINATED SUCCESS; smoke
  PASS. (First attempt failed at preflight: the interactive confirm
  read EOF under a non-TTY shell — operator note: use --no-confirm in
  automation.) Budget-fix app snapshot redeployed: SUCCEEDED.
- **P1 freeze dead (live)**: Run build -> immediate Save leaves the
  renderer responsive (evaluate proves the loop alive; the old prompt()
  hung CDP here); inline naming form opens; full save roundtrip
  POST /api/v1/portfolio/create -> 200 with campaign id + audit_event_id
  after the budget fix. Live-found defect fixed along the way: the
  optional Budget field sent budget_usd: null and the schema 422'd EVERY
  default save ("must be numeric") — null now means omitted, pinned in
  test_marketing_safety.py. Verification campaign archived via PATCH
  (status=archived, rationale on record).
- **A/R hotkeys (live, trusted input)**: borrower-button focus + key 'a'
  -> POST /v1/outreach/draft 200 -> /v1/outreach/approve 200 -> cell
  flips "Approved", preview shows the effective state. The audit's
  zero-POST experience reproduces only on terminal rows (by-design
  no-op), which the stale preview had mislabeled.
- **Topbar search REFUTED as dead (live, trusted input)**: typed value
  renders ("Cook"), suggestions listbox opens, "60617" + Enter navigates
  to borrower-360. The audit's swallowed keystrokes match untrusted
  synthetic events, which cannot insert text into a controlled input.
- **Light theme REFUTED as patchwork (live, real toggle)**: html
  data-theme=light with body rgb(244,247,250), rail + surface white —
  full-canvas flip, screenshot on file; toggled back to dark for booth.
- Clipping: expanded-row primary action right edge 274px inside a
  1413px viewport with Console open. Crumbs: US/Illinois/Cook County all
  computed text-overflow: ellipsis at ZIP depth.
- Purge proof: /api/leads?funnel_stage=approved&aged_days=14 -> 0 rows.
- **Genie loan-age stage note CLEARED**: "What is the average loan age?"
  -> 5.25 years via DATEDIFF(current_date(), origination_date)/365.25
  FROM mip.gold.lockin_cohort (refresh date is provenance only). The
  broader phrasing ("across the portfolio") fail-closes via the numeric-
  claims verifier rather than guessing — honest, but stick to the plain
  phrasing on stage.
- Console: the single page error during verification was the
  since-fixed portfolio-create 422; the budget fix removes its trigger.

## Re-audit #4 response (2026-06-12, merge after 0887f76)

Adjudication of "Re-Audit #4: Signoff Adjudication + Buyer-Wow Annex". The
audit accepted signoff #4 and corrected three of MY framing inflations —
all three conceded:

- **Push state**: "main 9 ahead — push is yours" was stale; the operator
  had already pushed and origin/main == HEAD. Verified before this round
  (git rev-list origin/main..main = 0). I will state push state from a
  live `git fetch`, not from my last local view.
- **Commit count**: "7 vertical commits" double-counted the evidence
  commit. Correct framing: N vertical + evidence + merges.
- **Prompt ban breadth**: the prior guard was a one-file source pin, not a
  repo-wide rule. Fixed this round (see eslint ban below).

### Nits fixed (all real, all from the diff)

- **Save-name discard on failure**: onConfirmSave closed the panel BEFORE
  the await, so a failed save silently dropped the operator's typed name.
  Now the panel stays open with the name + a "Save failed — your name is
  kept" alert and a Saving… state; only a successful save closes it.
  Live-verified (forced-abort probe: panel stayed open, name intact).
- **Hotkey effect rebind**: the A/R keydown effect had no dep array and
  re-bound the window listener every render. A dep array can't fix it
  cleanly (unstable closure identity), so it now binds once via a
  latest-handler ref. Covered by the existing hotkey test.
- **Sticky-expand CSS had no pin**: added a components.test.ts assertion
  for position:sticky/left:0/100cqw/container-collapse.
- **Repo-wide dialog ban**: no-restricted-globals + no-restricted-
  properties now ban prompt/alert/confirm everywhere (the prior pin was
  one file); pinned by an eslint-config assertion in the save test.

### Booth-hygiene bug found while acting on the audit's "archive the stray
campaign" note

The audit asked me to archive one default-named dev build. Doing so
exposed a real bug: list_campaigns returned EVERY status, and a governed
PATCH-to-archived sets updated_at=now() — so archiving junk would BUMP it
to the top of Saved Campaigns rather than hide it (my own r3
verification-campaign archive had been making it MORE visible). Fixed:
default listing now excludes archived (explicit status='archived' still
returns them); pinned by test_campaign_list_excludes_archived_by_default.
Then archived 86 dev-detritus campaigns (70 load-test/Genie-draft +
16 QA/validation fixtures) via governed PATCH — booth Saved Campaigns now
leads with the three canonical Summit campaigns, no load-test noise.
(Note: the PATCH rationale validator rejects two-capitalized-words as
"human-name-shaped" — the sweep uses an all-lowercase rationale. Durable
fix for test-created campaigns: a test marker/exclusion, post-Summit.)

### Buyer-Wow Annex — adjudicated through a booth-stability lens

- **#7 Campaign ROI projector — BUILT.** A transparent, fully client-side
  projection in Portfolio Builder: leads × response rate → fundings;
  × avg balance → volume; × revenue rate → gross; − outreach cost → net.
  Every assumption is on screen and editable (conservative mortgage
  defaults), recomputes live, guards invalid input with an em-dash (no
  NaN), and never shows on day-zero/empty cohorts. 16 tests (13 calc +
  3 render). Live: "$644K projected origination revenue from 3,157
  high-intent leads"; doubling the response rate moved it to $1.3M live;
  empty rate → em-dash.
- **#2 KPI count-up — DECLINED.** Re-introduces a pattern the team
  deliberately removed (KpiCard.tsx: "demo-ticker, not a settled
  enterprise metric"). Reading the code first avoided "improving" a
  known-rejected thing back in.
- **#8 evidence hover-preview, #1 ⌘K palette, #3 Genie narrative,
  #4 map animation, #5 Sankey, #6 morning briefing, #9 Genie follow-ups —
  DEFERRED** as flagged next builds. #8 is mis-scoped as "S": EvidenceChip
  is a ubiquitous primitive inside overflow:auto scroll containers (the
  lead table), so a hover popover needs a portal with scroll-aware
  positioning (M), and the chip already carries a native source+freshness
  tooltip. #1 ⌘K is the highest-wow next build (search is already wired)
  but is the largest new keyboard/focus/a11y surface — worth a dedicated
  slice, not a rushed pre-booth add. #3/#9 add live-Genie dependencies and
  #4 re-animates the hero map — both against the deterministic-booth
  mandate days before DAIS.

### Deployed + live-verified evidence (2026-06-11)

- pytest exit 0; **Vitest 279/279 (51 files)** (+9 from ROI calc/render +
  config-pin tests); ruff/mypy/eslint/CSS-literal clean; build exit 0;
  bundle validate OK.
- Deploy ./scripts/deploy.sh -t dev --no-confirm exit 0 — migrate, FRED,
  silver, scores, lifecycle all TERMINATED SUCCESS; smoke PASS.
- Live battery 5/6 PASS (the 6th is the test's own injected route-abort
  showing as a console error, not a product defect): ROI projector
  visible + recomputes live + invalid-guard; save-name retained on forced
  failure; default campaigns list clean (11 rows, 0 archived, 0
  load-test). Screenshots on file.

## Buyer-Wow tranche (2026-06-11, merge 68cde41) — ⌘K palette, evidence hover-preview, sleek KPI entrance

Built the two flagged next builds plus a sleek take on #2, all deterministic
and booth-safe, gated by a UNANIMOUS independent-subagent signoff and live
browser confirmation.

### Features
- **#1 ⌘K command palette** — keyboard-first launcher over the wired
  borrower/geography search + a local action registry (every product-flow
  route + workspace toggles). Accessible combobox/listbox with
  aria-activedescendant, focus trap, Esc/⌘K close with symmetric teardown,
  focus restore; the '/' topbar search shortcut is untouched. Mounted once
  in AppShell; renders nothing while closed.
- **#8 evidence hover micro-preview** — the safe version the deferral asked
  for: a PORTAL card (document.body, position:fixed) so it never clips in
  the lead table's overflow:auto; shows source + freshness + one signal on
  hover/focus; pointer-events:none so it never steals the chip click that
  opens the governed drawer; aria-hidden (the drawer is the a11y path);
  touch-safe. freshnessBucket extracted to freshness.ts to break the
  chip↔card import cycle.
- **#2 sleek KPI entrance** — NOT the count-up the team removed. A one-time
  fade-up of the settled value + left-to-right sparkline stroke-draw, gated
  by useFirstAppearance so route re-entry never replays it, fully disabled
  under prefers-reduced-motion. Reads as an enterprise metric settling, not
  a ticker.

### Unanimous independent-subagent signoff (fresh context each, BLOCK/APPROVE)
- frontend/a11y: APPROVE — ARIA model, focus trap, token/BEM parity,
  reduced-motion all solid.
- performance: APPROVE (×2) — hover-card scroll/resize listeners attach
  ONLY while a card is shown (not per-chip); palette idle-cost ~nil; all
  animations GPU-friendly one-shot; CSS budget bump cites measured actuals.
- governance/security: APPROVE — only masked/governed data (same fields as
  the topbar search; masked borrower_id + city/state/zip), no PII/secrets,
  evidence→drawer path intact, no mutation/outreach/approval bypass.
- principal-architect: APPROVE — clean layering (pure logic split, hooks in
  lib, CSS in design system), the freshness extraction genuinely breaks the
  cycle, blast radius contained, booth-safe.
- qa/test: BLOCK → APPROVE — the first pass correctly blocked on the
  palette's borrower-search path and mouse-click activation being untested;
  after I added them (live-rows-merge, Enter→dossier, click→dossier, MAX
  cap, error-state, click-to-activate, backdrop-close, empty-state, ⌘K
  reset-hygiene, hover-card scroll-hide + timer-cancel), the re-review
  confirmed via MUTATION TESTING that each new test fails under regression,
  then APPROVED.

Also taken from signoff: ⌘K toggle-close now runs the same teardown as Esc
(no stale-query flash); removed a dead double-undefined ternary in
EvidenceChip; Sparkline gradient id uses useId() so two KPIs can't collide.

### Validation + deployed evidence
- pytest 0 (backend untouched); **Vitest 313/313 (55 files)** (+15 feature
  tests + 3 CSS pins); ruff/mypy/eslint/CSS-literal clean; build 0; bundle
  budget passes (CSS gate bumped to 112/20 KiB for the ~7 KiB feature CSS,
  measured actuals 108.28/19.21).
- Deploy ./scripts/deploy.sh -t dev --no-confirm exit 0 — migrate, FRED,
  silver, scores, lifecycle all TERMINATED SUCCESS; smoke PASS.
- Live battery 9/9 (one was a test-query timing artifact, re-confirmed):
  ⌘K opens on chord, filters actions (Lead Queue top for "lead"),
  borrower search "60611" renders the Borrowers group (6 masked rows,
  CHICAGO IL · 60611), arrow+Enter routes to /borrower-360/B-0YDVKFE7GDEQ1,
  Esc closes; evidence hover-card shows on hover, portaled to body +
  aria-hidden, click still opens the drawer; KPI values settle; ZERO
  console errors. Screenshots on file.

### Remaining Buyer-Wow items (scoped for a future round, not this tranche)
- #3 "Tell the story" Genie narrative on Borrower 360 (M; live-Genie dep —
  route through the numeric-claims verifier).
- #4 animated geography transitions + ZIP borrower constellation (M;
  re-animates the hero map — needs care).
- #5 Funnel Sankey on Analytics Executive (M; new viz component).
- #6 morning briefing card on Home (M-L; needs the delta-snapshot scaffolding).
- #9 Genie follow-up chips + pin-to-Home (M; live-Genie dep).

## Funnel Sankey tranche (2026-06-12, merge 4f7135d) — Buyer-Wow #5

Took the next deterministic wow item with the same unanimous-signoff loop.

### Why this one (and not the other four)
Investigated all five remaining items for determinism: only #5 is cleanly
deterministic. #5's funnel-stage data (addressable → in_the_money →
high_opportunity → offer_recommended → approved → actioned) is ALREADY
fetched and rendered by the existing Pipeline Metrics bars — a Sankey is a
pure client-side re-viz, no new backend, no Genie. Deferred again: #6
morning briefing (needs new backend snapshot-diff; dev has one snapshot so
"deltas pending"), #4 map animation (the choropleth is architected for
instant SVG/DOM swaps — cross-level tweening is unsafe), #3/#9 (live-Genie
dependency). All against the deterministic-booth mandate.

### Feature
A flowing pipeline funnel on Analytics Executive: connected ribbons that
narrow with each stage's drop-off, the value story at a glance. Pure SVG
over the existing FunnelStage[]. Geometry is a pure, unit-pinned model
(buildFunnelSankeyModel in analytics.lib); each stage is a keyboard-
focusable SVG link (role=link, Enter/Space) routing to its slice of the
lead queue via the existing leadQueueHrefForFunnelStage contract; ribbons
draw in ONCE on first appearance (useFirstAppearance), reduced-motion off.
The exact figures stay in the Pipeline Metrics bars below.

### Non-monotonic conversion fix (caught by the gate)
The real funnel is NOT monotonic: offer_recommended (4,467,395) balloons
past high_opportunity (3,878) because the next-best-offer engine runs
across the whole addressable base. The naive conversion=count/prev would
have rendered "~115000%" at the booth. Three independent reviewers (qa,
governance, architect) flagged it; fixed pre-merge — conversion shows a %
only for a genuine narrowing (≤100%); a grown stage and divide-by-zero
(prev=0) show no label, aria-label omits the clause. Node heights still
reflect true counts. Confirmed live: only "2.2% / 3.5% / 0.0%" show; the
offers balloon shows no label.

### Unanimous independent-subagent signoff (fresh context each)
- frontend/a11y: APPROVE — role=group + focusable role=link (Enter/Space,
  aria-labels) is the correct accessible SVG pattern, consistent with the
  repo's audited choropleth precedent; reduced-motion gated.
- qa/test: APPROVE (after the conversion fix + new tests; re-confirmed via
  MUTATION TESTING that reverting the >100% suppression, the prev=0 null,
  the aria omission, or the one-time-draw gating each fails a test).
- performance: APPROVE — thin renderer over a useMemo-keyed model, GPU-
  friendly one-shot opacity draw, lazy analytics chunk, budget passes.
- governance/security: APPROVE — aggregate-only funnel totals, no PII/
  secret, reuses the existing nav contract, read-only, no new network.
- principal-architect: APPROVE — clean layering (pure geometry in .lib,
  renderer in .charts, wiring in .sections), deterministic, blast radius
  contained to the Executive tab; keeping both Sankey + exact-figure bars
  is the right call.

### Validation + deployed evidence
- pytest 0 (backend untouched); **Vitest 327/327 (56 files)** (+13 Sankey
  geometry/render/a11y + CSS pin); lint/build/budget clean.
- Deploy ./scripts/deploy.sh -t dev --no-confirm exit 0 — all jobs
  TERMINATED SUCCESS; smoke PASS.
- Live 7/7: Sankey renders on /analytics, 6 focusable stage links, NO
  >100% conversion label (only 2.2%/3.5%/0.0% on narrowing stages),
  aria-labels correct, In-the-Money node navigates to
  /lead-queue?funnel_stage=in_the_money, Pipeline Metrics exact figures
  retained, ZERO console errors. Screenshot on file.

### Still deferred (Genie / new-surface bets, against the booth mandate)
~~#3 Genie narrative, #4 map animation, #6 morning briefing, #9 Genie
follow-ups.~~ → All four now shipped; see the tranche below.

## Final Buyer-Wow tranche (2026-06-12) — #6, #4, #3, #9 (each its own merge)

Overnight directive: take the four remaining buyer-wow items ONE BY ONE,
each through the full unanimous independent-context subagent signoff loop,
do not stop until all four are implemented, validated, and live-verified.

### Features (each a `--no-ff` merge to main)
- **#6 Morning briefing card on Home** (merge 87d75fa) — a "what changed
  overnight" card driven by the live preview `trends` (delta_pct / direction
  / comparison_label). Pure-frontend (`lib/morningBriefing.ts` +
  `MorningBriefing.tsx`); shows an honest PENDING state on day-zero / when
  trend_status != live, never a fabricated delta. No new backend (verified
  the deltas already exist in the preview payload — 36 snapshots live).
- **#4 Geography level-transition animation** (merge d441711) — keyed
  `.map-levels` wrapper so state→county→ZIP transitions replay a one-shot
  settle, plus a staggered ZIP-tile entrance. Additive CSS + flex-transparent
  wrapper; NO cross-level geometry tweening (would risk the hero map).
  Architect caught a hover-lift regression pre-merge (the `both` fill-mode
  pinned `transform: scale(1)` over `:hover`) — fixed by making the keyframe
  opacity-only. Reduced-motion gated.
- **#3 Borrower 360 "Tell the story"** (merge b74ac7f) — a deterministic,
  honestly-labeled 3-sentence narrative with a real numeric-claims verifier
  (every figure in the prose is checked against the dossier; scale-aware
  K/M parsing, 6% tolerance; unverifiable tokens flagged). Booth-safe: no
  live-LLM dependency, labeled "grounded in this dossier's evidence."
- **#9 Genie follow-ups + Pin-to-Home** (merge 6795263) — deterministic
  follow-up FALLBACK when Genie returns none (never a dead end), plus a
  client-side, actor-scoped Pin-to-Home (`useSyncExternalStore` over
  localStorage) feeding a Home "Pinned insights" card. NOT a governed
  mutation: no backend write, no outreach, no audit row — a personal
  bookmark, cleared on actor change with the other actor-scoped state.

### Unanimous independent-subagent signoff (fresh context each, all 4 items)
- Every item passed a 5-reviewer loop: frontend/a11y, qa/test, performance,
  governance/security, principal-architect — each in fresh context against
  the branch diff, BLOCK/APPROVE.
- **#9 architect BLOCK (corroborated by qa #1), fixed + re-reviewed:** the
  Pin-to-Home gate used `source === 'genie'`, a stricter allowlist than the
  app's trust boundary. It silently HID the pin button on `trusted_sql` /
  `sales_ops` answers — exactly the canonical booth answers (top borrowers
  by state, top ITM ZIPs). Fixed by centralizing `NON_PERSISTABLE_SOURCES`
  + `isTrustedGenieSource` in `lib/pinnedInsights.ts` (single source of
  truth, reused by `GenieChat.shouldPersistConversation`) and gating on the
  denylist. Architect + qa re-reviewed → APPROVE. Boundary now pinned by
  tests (trusted_sql pinnable, all five degraded sources not).

### Validation + deployed evidence
- Vitest **376/376** (63 files) on merged main (+8 from the #9 boundary
  tests); lint/build/budget clean; backend untouched (pure frontend).
- Deploy `./scripts/deploy.sh -t dev --no-confirm` exit 0 — Genie space
  rebound (14 trusted assets), **13/13 live smoke PASS** (health, geo
  state/county/zip rollups, outreach approval audit write, genie message).
- **Live browser inspection 4/4 PASS** against the deployed app
  (`tests/e2e/buyer_wow_live.spec.ts`, E2E_LIVE=1, workspace bearer):
  - #6 briefing renders on Home (live grid or honest pending).
  - #4 `.map-levels` keyed wrapper renders; drilling a dynamically-discovered
    in-footprint state (out-of-footprint states are no-ops by design) grows
    the breadcrumb trail and re-renders the wrapper without error.
  - #3 "Tell the story" reveals a grounded narrative with ≥1 verified claim
    chip on a real dossier.
  - #9 Genie answer offers follow-up chips AND a Pin-to-Home button on the
    trusted live answer; pinning surfaces it on the Home "Pinned insights"
    card (shared store), then unpins clean. This exercises the exact
    trusted-source boundary the architect blocker was about.

## Re-Audit #5 remediation (2026-06-12) — buyer-wow stress findings

Source: `docs/audits/re-audit-2026-06-12-r5-buyer-wow-stress.md` (live
adversarial break-test, all findings P3, none blocking). Each claim was
verified against code before fixing. Two independent reviewers (qa-test,
governance) APPROVED the diff.

### Fixed (valid)
- **D1 — Pinned-card raw markdown + mid-token truncation (Home hero).**
  `buildPinFromAnswer` now flattens the Genie markdown vocabulary (`**bold**`,
  `` `code` ``, bullets) to plain text and truncates at a word boundary with
  an ellipsis, trimming any dangling separator/open-bracket. No more literal
  "**" or a dangling "(**" on the hero. Verified live (pinned summary asserted
  markdown-free).
- **D2 — Refusal answers offered a synthesized follow-up pivot.** The
  deterministic fallback is now gated on the same `isTrustedGenieSource`
  denylist as the pin — a governed/fair-lending refusal no longer suggests
  "Which segments drive this?". Explicit backend follow-ups are still honored
  (generic governed sample questions on warm-start `degraded`/outreach
  `refused`, never refusal-referential). Verified live (refusal → no pin, no
  follow-up chips).
- **D5 — KPI one-time entrance replayed on a mid-session data refresh.** Keyed
  on the label alone, not label+value; `useFirstAppearance` freezes `isFirst`
  at mount so the loading→loaded transition still animates when the number
  lands, but a later refresh never re-keys.
- **D6a — Morning-briefing headline could read "up 0.0%"** for a direction-only
  mover with a null delta. The headline now states direction, appends the
  percent only when finite, and prefers a finite-delta mover.
- **D6b — Deleted dead `useCountUp.ts`** (no importers).
- **D6c — ROI projector had no upper money bound** ("$1000000.0B"). Implausible
  avg-balance (>$100M) / cost-per-lead (>$100K) are now rejected as invalid →
  "—" (consistent with `clampPct`, which rejects rather than clamps), and the
  compact formatter gained a trillion tier.

### Audit-MISSED (found while remediating)
- **Funnel Sankey entrance had the identical D5 bug.** `useFirstAppearance` was
  keyed on `${stageOrder}=${count}` (volatile), so a refreshed snapshot that
  remounts the chart would replay the ribbon draw-in. Re-keyed on the stage
  STRUCTURE. The existing once-only test only remounted with identical stages
  (never exercised a refresh); strengthened it to remount with changed counts.

### Adjudicated NON-fixes (verified or by-design, with rationale)
- **D8 evidence hover (ledger #8) — NOT a defect.** Every Supporting-evidence
  chip passes a non-null source; the hover-card open delay is 110 ms (far under
  the audit's 1 s manual hover). Verified LIVE that the card attaches and
  appears on a Supporting-evidence chip — the audit's single 2:30 AM hover was
  transient. Added live regression coverage.
- **Sankey mid-funnel balloon (ledger #3) — honest data relationship, kept.**
  Offer-Recommended (4.47M) exceeds High-Opportunity (3.88K) because the
  next-best-offer engine scores the whole addressable base, not the high-opp
  subset. Node heights reflect TRUE counts; the conversion % is already
  suppressed for grown stages (prior tranche fix) and the aria-label omits the
  meaningless ratio. "Approved 0.0%" is a real rounding of a genuine narrowing.
  Restructuring the funnel (branch offers in parallel) is a semantics redesign,
  riskier and less honest 3 days pre-booth — this is a talk-track item.
- **Pin roaming (headline #2 / ledger #7) — by design.** Pins are a client-side,
  actor-scoped localStorage bookmark (the governance-approved #9 pattern), not a
  governed Lakebase mutation; they don't roam machines. Operational note: pin on
  the presenting machine. A roaming pin would need a backend endpoint + audit and
  is arguably the wrong pattern for personal view-state.
- **Denylist default-allow (ledger #7) — by design, errs safe.**
  `isTrustedGenieSource` reuses the app's single-source `NON_PERSISTABLE_SOURCES`
  denylist; degraded sources are enumerated and blocked, anything else is a
  genuine answer. Both directions are test-pinned. A shared backend/frontend
  enum or backend-parity test would harden it (future hardening, not a blocker).
- **Briefing leads with the purge artifact (ledger #4) — operational.** The
  headline is honest (it flags the step-change); re-seed approvals pre-booth.

### Framing correction (from the audit)
- The signoff this round only ever claimed the FOUR features it shipped
  (accurate). On the broader annex, it is **9 of 10** implemented — kiosk mode
  (#10) is deliberately deferred post-Summit and has no code.

### Validation + deployed evidence
- Vitest **387/387** (65 files, +11 regression tests across the fixes);
  lint/build/budget green. Backend untouched (pure frontend).
- Budget: the Buyer-Wow epic had already pushed initial JS gzip to 82.99 (the
  83 ceiling, 79.03 baseline); the D1 sanitizer tipped it to 83.15 →
  initialJsGzip bumped 83→87 (actual +~5%), documented.
- Deploy `./scripts/deploy.sh -t dev --no-confirm` exit 0, **13/13 live smoke
  PASS**; redeployed to roll the Sankey fix forward (deployed == HEAD).
- **Live verification 5/5 PASS** (`buyer_wow_live.spec.ts`): the 4 features
  plus D1 (markdown-free pinned summary), D2 (refusal → no pin/no follow-ups),
  and D8 (evidence hover-card attaches to a Supporting-evidence chip).
