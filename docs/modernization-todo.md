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
