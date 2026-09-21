# UI/UX and Frontend Technology Audit — Verified Findings Register (2026-09-21)

Companion to [ui-ux-technology-audit-2026-09-21.md](ui-ux-technology-audit-2026-09-21.md). Generated from the multi-agent audit run `wf_2f7ca37c-738` (39 agents: 1 screenshot capture, 16 dimension auditors, 16 adversarial verifiers, coverage critic + verifier, 3 proposers + judge).

**Read this first**

- The audit read branch `claude/genie-guard-literals`, which was 25 commits behind `origin/main`. On `main`, `components.css`, `lib/api.ts`, `LeadTable.tsx` and `USChoroplethMap.tsx` have already been split, so `file:line` citations into those four files are branch-local; the same code lives in the split partials on `main`. Verifiers confirmed the substantive evidence is unchanged on `main`.
- Every finding was re-opened by an adversarial verifier. `confirmed` = true as written; `corrected` = core true, and the title / recommendation / impact / effort shown here is the verifier's corrected version; `verifier-added` = found by the verifier while checking. Nothing was fully refuted; 153 of 172 auditor findings were narrowed.
- Screenshots were rendered from a production build with Playwright route-intercepted fixtures. Use them for layout and visual judgement only, never data correctness. No Genie answer state, no Analytics sub-tab other than Executive, and no hover/focus states were captured.
- Impact ratings on performance findings are unprofiled unless the text says otherwise.


---

## Technology stack, dependency currency and build-vs-buy strategy (`stack`) — grade B-

**Auditor:** The toolchain is current: Vite 8/Rolldown, React 19.2 with React Compiler, React Router 8, TypeScript 6, 14 production packages, zero `any`. But the app uses almost none of what that stack offers. It has no error boundary anywhere. Its hand-rolled select widgets, Genie markdown renderer, charts and Web Vitals code each have concrete defects that a small headless or math-only library, or a native platform feature, would remove.

**Verifier on the grade:** B- is fair, arguably a notch harsh. Every numeric claim I re-ran reproduced exactly (0 errors under the seven flags, 96 production noUncheckedIndexedAccess errors, 4 e2e type errors, 2 moderate advisories, all npm versions, React 19.3 stable ViewTransition), and having no error boundary anywhere justifies staying below B. The rationale does miscount production packages (9, not 14), the audit ran on a branch 25 commits behind origin/main so its components.css/api.ts/LeadTable citations are branch-local (the substantive evidence files are byte-identical on main), and several prescriptions over-reach: a data-router rewrite for a scroll bug, d3-scale, Renovate against a documented no-bot-branch contract, jsx-a11y on ESLint 10, and showModal on the deliberately non-modal Genie panel.

**Strengths (keep these)**

- **Minimal, clean supply chain** — frontend/package-lock.json is lockfileVersion 3 with 239 entries, of which 14 are production packages. Every entry has an integrity hash and resolves to registry.npmjs.org. frontend/package.json:6-10 uses exact pins plus packageManager and engines (node >=22.22 <26). tests/unit/test_supply_chain_licenses.py gates licenses.
- **Core toolchain adopted early and kept on current majors** — frontend/package.json:21-50 and frontend/vite.config.ts:3-11 show Vite 8 (Rolldown), plugin-react 6 with reactCompilerPreset, React 19.2.8, react-router 8 (ESM-only), TypeScript 6, ESLint 10 flat config and Vitest 4. The only majors behind are TypeScript 7 (2026-07-08), Vitest 5 (2026-09-03) and @babel/core 8 (2026-06-16), and TypeScript 7 is blocked upstream by typescript-eslint.
- **Strong type discipline already in place** — A grep over frontend/src for `as any`, `: any` and `<any>` returned 0 production hits, and 0 for @ts-ignore/@ts-expect-error. There are 2 eslint-disable comments in 320 files. My no-emit tsc probe showed verbatimModuleSyntax, erasableSyntaxOnly, noImplicitOverride, noImplicitReturns, noFallthroughCasesInSwitch and noUnusedLocals/Parameters all pass with 0 violations.
- **Hand-rolling is the right call for icons and the command palette** — components/Icon.tsx is 99 lines holding 42 prototype-verbatim glyphs behind a typed IconName union (179 usages). Adding lucide-react would breach the design contract for no gain. components/command/CommandPalette.tsx:229,242 wires aria-activedescendant, listbox ids and a focus trap in 353 lines, so cmdk is not needed.
- **Governance-aware data-layer defaults** — lib/queryClient.ts:14-18 disables focus refetch because reads write VIEW_* audit rows, and :19-35 uses a reason-aware retry plan. eslint.config.js:51-62 bans alert/confirm/prompt, and :69-94 bans src/mocks imports in production code. SSR and service workers are consciously deferred in docs/modernization-todo.md:171-180.


### `stack-01` No error boundary anywhere: a render error or stale-chunk import blanks the whole app

`critical` · `defect` · effort `M` · corrected

- **Evidence:** app.tsx:69 wraps lazy routes in Suspense only. A grep of frontend/src and index.html for ErrorBoundary, componentDidCatch, getDerivedStateFromError, onUncaughtError, vite:preloadError and unhandledrejection found no boundary or handler. main.tsx:13 passes no createRoot error options. backend/main.py:826-834 returns JSON 404 for missing hashed assets.
- **Problem:** Any render exception unmounts the React tree to a white page. So does a lazy-route import from a tab opened before a redeploy, because the old hashes 404. React.lazy caches the rejection, so the retry reset in lib/lazyPreload.ts:12-15 never recovers. Nothing is reported.
- **Recommendation:** Slice 1 (S): a class boundary keyed by pathname around the route outlet plus a shell boundary, rendering the existing degraded-state surface with Reload; a `vite:preloadError` handler with a sessionStorage one-shot reload guard. Slice 2 (M): createRoot onUncaughtError/onCaughtError posting a new `client_error` metric (error name, sanitized route, boundary id only, never the message, which can carry borrower data) after extending backend/schemas/telemetry.py and its tests. Emit sourcemaps outside the deployed dist (CI artifact) rather than `hidden` inside /assets, which the asset route would serve.
- **Tech:** React 19 createRoot error callbacks (stable). Vite `vite:preloadError` event (stable). react-error-boundary 6.1.6 (npm-verified) or a 30-line class boundary.
- **Verifier:** Reproduced on branch and origin/main: zero boundaries or global handlers; Suspense-only outlet; JSON 404 for stale hashes; only 4 of 12 routes preload. But the RUM schema is closed (Literal metrics, extra=forbid), so telemetry needs backend work, and hidden maps would be served from /assets.
- **Constraint conflict:** RUM schema is deliberately closed and PII-screened (extra=forbid); error telemetry must extend it server-side and must not ship raw error messages.

### `stack-04` React 19 concurrent APIs and view transitions are unused: routes remount with a 4px fade and the Genie panel loses state on close

`high` · `wow` · effort `L` · corrected

- **Evidence:** A grep of frontend/src for ViewTransition, startViewTransition, useTransition, startTransition, useDeferredValue, <Activity, useOptimistic and useEffectEvent returned zero hits each. app.tsx:68 combined with components.css:5012-5017 is a keyed remount plus an opacity/translateY fade. AppShell.tsx:217 unmounts GenieChat whenever it is closed.
- **Problem:** A React 19 app behaves like React 17. Drills and drawers pop in rather than morph. Filter typing competes with table re-render. Closing Genie discards the composer draft and scroll position, and route changes are hard remounts.
- **Recommendation:** Scope a first slice: once 19.3.0 has soaked, wrap the route outlet in `<ViewTransition>` (replacing the keyed fade) and morph the clicked lead row's score chip and borrower id into the Borrower 360 header, assigning view-transition names at click time because rows are virtualized. Use --dur-*/--ease tokens, honour reduced motion, and disable transitions in the visual-snapshot and layout-stability specs. Keep GenieChat mounted after first open with `<Activity>` to preserve the composer draft and an in-flight turn (note Activity tears down effects and hides with display:none). Drop the useDeferredValue item; useEffectEvent in useFocusTrap is a trivial follow-up.
- **Tech:** React 19.3.0 made `<ViewTransition>` and addTransitionType stable (react.dev blog, 2026-09-09, verified). Activity and useEffectEvent have been stable since 19.2. Same-document View Transitions became Baseline Newly available on 2025-10-14.
- **Verifier:** Zero hits reproduced; React 19.3.0 stable ViewTransition verified (react.dev post, npm 2026-09-09). But Lead Queue filters are selects to URL to server query on a virtualized table and Topbar search is debounced, so the useDeferredValue claim fails; the Genie transcript already persists via genieConversationStore.
- **Constraint conflict:** Shared-element morphs are not in the design_files prototypes; additive motion must be declared as a prototype deviation and use the token durations.

### `stack-05` Five divergent hand-rolled listboxes; three give screen readers no active-option cue; dialogs rely on a keydown focus trap

`high` · `defect` · effort `L` · corrected

- **Evidence:** Listboxes live at ui/FilterSelect.tsx:97, layout/Topbar.tsx:323, routes/portfolio-builder.components.tsx:49, routes/analytics.sections.tsx:493 and command/CommandPalette.tsx:242. A grep for aria-activedescendant hits only the last two. The selector in hooks/useFocusTrap.ts:3-10 ignores visibility, summary and contenteditable. The only `inert` hits in src are in comments.
- **Problem:** Three selects leave focus on the button with options that have no ids, so arrowing through them is not conveyed to assistive tech. FilterSelect has no Home/End or typeahead. Its menu is positioned by CSS alone with no flip or collision code (FilterSelect.tsx:7-8,96-97). Six role=dialog surfaces simulate modality with a keydown listener.
- **Recommendation:** Use `<dialog>.showModal()` for the five aria-modal surfaces only; keep the Genie panel non-modal. Dialog enter/exit animation needs @starting-style and allow-discrete (the `overlay` property is Chromium-only), so treat exit motion as progressive enhancement. For listboxes, either extract the already-correct aria-activedescendant implementation in analytics.sections.tsx:485-523 into one shared hook used by FilterSelect, Topbar and portfolio-builder, or adopt Base UI styled only through BEM classNames. That choice is an owner decision: Topbar and FilterSelect sit in the initial shell, so Base UI plus Floating UI needs a documented initial-JS budget bump. Pick one positioning strategy, not two.
- **Tech:** @base-ui/react 1.8.0 (headless and unstyled; npm-verified). `<dialog>` is Baseline widely available. CSS anchor positioning is Baseline 2026 newly available (Chrome 125, Safari 26, Firefox 147, verified).
- **Verifier:** Reproduced: three listboxes lack aria-activedescendant and option ids; FilterSelect has no Home/End/typeahead; Base UI 1.8.0 and anchor-positioning Baseline (Firefox 147) verified. But GenieChat.tsx:386-389 is deliberately non-modal, so showModal fits five surfaces, not six; layering anchor positioning on Base UI's positioner is redundant.
- **Constraint conflict:** showModal() on the floating Genie panel would make the page inert and break the first-class non-modal Genie contract; Base UI in the shell presses the 5%-headroom initial-JS budget.

### `stack-02` Genie markdown renderer handles only bold, inline code and bullets; other LLM markdown leaks as raw syntax

`medium` · `defect` · effort `M` · corrected

- **Evidence:** components/mortgage/GenieAnswer.markdown.tsx:106 has an inline regex covering only **bold** and `code`. Line 149 accepts only -,*,• bullets. Lines 165-166 join consecutive non-blank lines with a space. GenieAnswer.markdown.test.tsx has no ordered-list, #-heading, table, link or italic case.
- **Problem:** Live-first Genie prose is LLM markdown. Numbered lists collapse into one run-on paragraph ('1. … 2. … 3. …'). Headings written as ###, table pipes, [links](…) and *italics* print as raw symbols on the hero AI surface.
- **Recommendation:** First capture live single-turn answers to confirm which constructs really occur (repo doctrine). Cheapest fix: extend the hand parser with ordered lists, #-headings and pipe tables (~60 lines, zero bytes added; keeps the pinnedInsights.ts mirror grammar aligned). If markdown-to-jsx is chosen instead, set disableParsingRawHTML and override `a` and `img` to inert text so only reviewed UC asset links stay clickable; keep renderSourceLinks plus the heading and source-footnote rules; update all three MarkdownAnswer consumers. Never use innerHTML.
- **Tech:** markdown-to-jsx 9.10.3 (npm-verified; MIT; outputs React elements, so it is safe under CSP and Trusted Types). react-markdown 10.1 + remark-gfm is the heavier alternative.
- **Verifier:** Parser read: only bold, code and bullets; numbered lines deterministically collapse into one paragraph. But no captured live turn in the repo shows leakage (genie_eval stores scores only), sweep prompts already ban headings, and rendering arbitrary LLM links/images is a governance risk the recommendation ignores.
- **Constraint conflict:** Mapping LLM-authored [links](...) to live anchors departs from the governed-evidence posture; today only reviewed Unity Catalog asset links are clickable.

### `stack-03` No scroll reset or restoration on the .main scroller, one static tab title, no focus move on navigation

`medium` · `gap` · effort `S` · corrected

- **Evidence:** main.tsx:16 mounts <BrowserRouter>, and app.tsx:68 re-keys a div on pathname. design-system/components.css:409-412 makes `.main` the overflow-y scroller. A grep of src for scrollTo, ScrollRestoration, scrollTop= and document.title found no scroll reset or restoration and no per-route title. The only `<title` match is an SVG title in brand/Entrada.tsx:81.
- **Problem:** Leaving a scrolled Lead Queue lands mid-page on the next route, and Back never restores position. Every tab and history entry reads 'Mortgage Intelligence Platform'. There is no pending-navigation affordance, and no focus move or announcement on route change.
- **Recommendation:** Fix in declarative mode: in AppShell, an effect on location.key plus useNavigationType. PUSH/REPLACE sets main.scrollTop = 0 and focuses #main-content; POP restores from a Map keyed by location.key once cached query data has rendered. Render a React 19 `<title>` per route. Per-route error isolation comes from stack-01's keyed boundary. Treat a createBrowserRouter migration as a separate, optional owner decision (M-L): its main gains (errorElement, useNavigation) are small here because routes do not use loaders and chunks are preloaded.
- **Tech:** React Router 8.4.0 data router (stable); 8.4 adds granular contexts that cut hook re-renders. React 19 document-metadata hoisting (stable).
- **Verifier:** Confirmed: .main is the scroller inside a non-keyed <main>; no scroll reset, document.title or focus move on branch or main. But none of it needs a data router: data is TanStack Query, so useNavigation adds little, while routePreloaders and 41 MemoryRouter test files couple to the current shape.

### `stack-06` Hand-rolled chart maths ships non-round axes and a bare Genie line chart

`medium` · `upgrade` · effort `M` · corrected

- **Evidence:** makeTicks at routes/analytics.lib.ts:198-207 linearly subdivides [min,max], and the fixture Analytics screenshot shows y-ticks 24.1K/18.08K/12.05K/6.03K. GenieAnswerCharts.tsx:129-153 slices to 24 points with no notice, plots r=3 dots at x=0 and x=520 with no padding, and has no y-axis or tooltip. components.css:6322-6325 sets no overflow.
- **Problem:** About 2,570 lines of bespoke chart code and helpers, plus 1,642 lines of tests, recompute scales at six `Math.max(1, ...)` sites. The result still has odd tick values, straight polylines, edge points that are likely clipped, and tooltip-less charts on the AI hero surface.
- **Recommendation:** Keep SVG + BEM + tokens. Use d3-array `ticks`/`nice` (or a 20-line niceTicks) and d3-shape `line().curve(curveMonotoneX)`/`area` only; skip d3-scale. Build one shared ChartFrame (padding, round axes, crosshair tooltip, keyboard point traversal, truncation notice) for analytics.charts.tsx and GenieAnswerCharts.tsx, and re-measure the analytics and Genie chunk budgets in the same commit.
- **Tech:** d3-scale 4.0.2, d3-shape 3.2.0, d3-array 3.2.4 (ISC, stable, tree-shakeable, loaded only in chart chunks; npm-verified).
- **Verifier:** makeTicks and GenieLineChart read as described (silent 24-point slice, unpadded r=3 dots at the viewBox edge, no y-axis or tooltip). The components.css citation is branch-local; main split the CSS. d3-scale drags in d3-interpolate, d3-color, d3-format and d3-time for a one-line linear map.

### `stack-07` TypeScript and ESLint run below their free strictness ceiling, and 9,492 lines of Playwright specs are never typechecked or linted

`medium` · `upgrade` · effort `M` · corrected

- **Evidence:** tsconfig.json:3-5,10,19 sets an ES2020 lib, only `strict`, and include ['src']. eslint.config.js:10,32 lints only src/**, without type information. My no-emit probes found 96 production noUncheckedIndexedAccess errors. Typed lint found 12 floating promises, 25 misused promises and 7 '[object Object]' stringifications. tests/e2e and vite.config.ts had 4 type errors.
- **Problem:** Seven strictness flags have zero violations today but are off. The ES2020 lib rejects .at() and toSorted(), yet tests/e2e/home_summary.spec.ts:205 already calls .at(). The Playwright suite that gates releases has no static analysis.
- **Recommendation:** As written, minus eslint-plugin-jsx-a11y: its peer range stops at ESLint 9 and it last published 2024-10, so it would ERESOLVE on ESLint 10. Keep the axe Playwright gates, or add it only behind a verified override. Plan noUncheckedIndexedAccess as 312 sites, or give tests a looser tsconfig so production code (96 sites) is gated first. Fix the 4 e2e/config type errors first: growth_agent_live.spec.ts:614 and :618 read fields the TS type lacks, and vite.config.ts:19 fails an overload.
- **Tech:** typescript-eslint 8.70.1 projectService (stable). eslint-plugin-jsx-a11y 6.10.2. @tanstack/eslint-plugin-query 5.103.2 (all npm-verified).
- **Verifier:** Reproduced by probe: seven flags give 0 errors (37 s); noUncheckedIndexedAccess gives 96 non-test errors but 312 total because tests share the tsconfig; e2e/config probe gives the same 4 errors. However eslint-plugin-jsx-a11y 6.10.2 peers eslint ^3 to ^9 and the repo runs ESLint 10.

### `stack-08` Hand-rolled Web Vitals are not Core Web Vitals

`medium` · `defect` · effort `M` · corrected

- **Evidence:** lib/rum.ts:161-167 sums every layout-shift for the tab's lifetime with no session windows. Lines 189-196 report the maximum event duration as INP. Lines 142-151 enqueue every LCP candidate. Line 235 observes `longtask`, not long-animation-frame.
- **Problem:** In a long-lived SPA, CLS grows without bound, INP is a single worst outlier, and LCP is sampled several times per load. Budgets and performance claims built on these numbers will not match Lighthouse or CrUX.
- **Recommendation:** Swap the three observers for web-vitals `onLCP`, `onINP`, `onCLS` (S), keeping the sanitize, batch and beacon pipeline. For attribution and long-animation-frame, extend backend/schemas/telemetry.py with new metric names and detail keys plus tests (M); send BEM class names only so the name-shape PII validator passes. Use `reportSoftNavs: true` as Chromium-only progressive enhancement instead of a hand-rolled per-route reset.
- **Tech:** web-vitals 6.2.2 (Google's reference implementation, roughly 2 KB brotli; npm-verified). Long Animation Frames API (Chromium 123+, progressive enhancement).
- **Verifier:** rum.ts read: unwindowed lifetime CLS sum, max-duration INP, every LCP candidate enqueued, longtask observer all confirmed; web-vitals 6.2.2 verified. But backend RumEvent is closed (Literal metric and detail keys, extra=forbid, PII validator), and v6 soft-nav support uses Chromium's Soft Navigation API, not a router-key reset.

### `stack-09` Governed writes are bespoke async state machines: no useMutation, the lint escape TODO is overdue, and nine modules opt out of React Compiler

`medium` · `upgrade` · effort `L` · corrected

- **Evidence:** A grep of src for useMutation, useOptimistic, setQueryData and onMutate returned 0. routes/offer-orchestrator.tsx has 38 useState calls and 0 useQuery. src has 40 set*Loading/Busy calls and 95 set*Error calls. eslint.config.js:38-42 disables react-hooks/set-state-in-effect under a TODO dated 2026-07-15. Nine files carry 'use no memo', and routes/analytics.tsx:1-3 cites the bundle budget as the reason.
- **Problem:** Approve, reject, assign and draft flows each reinvent pending, disabled and error handling, with no shared pending state or dedupe. The lint escape hatch is two months past its own deadline. The Analytics route runs without React Compiler to satisfy a byte gate.
- **Recommendation:** Wrap governed writes in useMutation with mutationKeys, and surface useMutationState in the ApprovalBanner and table rows. Use useOptimistic only for a 'pending approval' visual, never an optimistic 'approved', because the audit row is the truth. Then re-enable set-state-in-effect. Re-measure the analytics compiler opt-out against a revised budget.
- **Tech:** TanStack Query 5 useMutation/useMutationState (stable). React 19 useOptimistic (stable). eslint-plugin-react-hooks 7.1.1 is already installed.
- **Verifier:** Reproduced: 0 useMutation/useOptimistic/setQueryData; offer-orchestrator has 38 useState and no useQuery; TODO dated 2026-07-15; nine 'use no memo' files. A strict regex finds 12 set*Loading/Busy calls, not 40 (immaterial). Sixteen write sites sit in LeadTable, AppContext and offer-orchestrator, each heavily tested.

### `stack-10` Dependency drift with no automation: 19 of 26 packages behind, and the documented 'zero vulnerabilities' claim is now false

`medium` · `gap` · effort `M` · corrected

- **Evidence:** `npm view` on all 26 packages on 2026-09-21 showed 19 behind. The majors are typescript 6.0.3 to 7.0.2, vitest 4.1.4 to 5.0.1 and @babel/core 7.29.7 to 8.0.6. `npm audit` reports 2 moderate advisories (vitest/@vitest/mocker, fixed in 4.1.11). ci.yml:406 gates only at the high level. supply-chain-audit.md:14 claims zero. modernization-todo.md:158-161 records the removal of Dependabot.
- **Problem:** Updates are manual with no signal, so dev tooling stopped moving in April and runtime packages in July. React 19.3, Router 8.4 and Vite 8.1-8.3 features are out of reach. The audit doc and the CI gate disagree on the vulnerability threshold.
- **Recommendation:** Bump vitest to 4.1.11 now. Batch the React 19.3, Router 8.4, Vite 8.3 and lint bumps on one human feature branch, expecting Playwright 1.63 to need visual-snapshot re-baselines. For update signal without bot branches, add a scheduled CI job that reports `npm outdated` and `npm audit --audit-level=moderate`, or run Renovate with dependencyDashboardApproval so no branch exists until approved; owner decision. Reconcile docs/audits/supply-chain-audit.md with the CI `--audit-level=high` threshold. Defer TypeScript 7 until typescript-eslint supports it.
- **Tech:** TypeScript 7.0.2 native compiler has been stable since 2026-07-08, but typescript-eslint 8.70.1 peers typescript <6.1.0 (npm-verified). @typescript/typescript6 6.0.2. Renovate (stable).
- **Verifier:** npm audit reproduces 2 moderate dev-only vitest advisories (fix 4.1.11); every cited version verified via npm view. But Renovate's bot branches contradict modernization-todo.md:158-161, which removed Dependabot deliberately, and my full typecheck took 37 s, not 2m14s, so the TypeScript 7 dual toolchain buys little.
- **Constraint conflict:** Persistent bot branches violate the documented 'main is the only persistent branch' repo contract and trip the all-refs gitleaks scan; update automation is an owner decision.

### `stack-v1` API types are hand-written (~1,750 lines) although a pinned OpenAPI baseline is already committed

`medium` · `gap` · effort `M` · verifier-added

- **Evidence:** lib/api.ts:880 returns `(await res.json()) as T` (apiTransport.ts:513 on main); src/types.ts plus src/types/*.ts are ~1,750 hand-written lines. tests/fixtures/openapi_baseline.json (811 KB) is pinned by tests/unit/test_openapi_contract.py. No codegen in package.json or tools/. My e2e typecheck found growth_agent_live.spec.ts:614 reading fields absent from the TS type.
- **Problem:** Frontend contracts can drift silently from the Pydantic models, and casts hide it. The repo's strong-types rule stops at the network boundary.
- **Recommendation:** Generate src/types/api.gen.ts from the committed baseline with openapi-typescript (dev-only, zero runtime bytes). Re-export the existing type names from the generated schemas incrementally. Add a CI step that regenerates and fails on diff, beside the existing OpenAPI baseline test. No deploy or infrastructure change.
- **Tech:** openapi-typescript 7.13.0 (npm-verified, dev-only); optional openapi-fetch 0.17.0 later.

### `stack-v2` Component tests hand-roll createRoot/act/dispatchEvent in 67 files under happy-dom; nothing below Playwright can exercise native dialog, focus or transitions

`medium` · `gap` · effort `M` · verifier-added

- **Evidence:** 67 test files call createRoot, 69 call act(), 42 set IS_REACT_ACT_ENVIRONMENT themselves, with 72 manual dispatchEvent sites; src/test/setup.ts is a one-line placeholder. package.json has no @testing-library or user-event. Zero *.stories files exist although CLAUDE.md and eslint.config.js reference Storybook.
- **Problem:** happy-dom has no layout, top layer, anchor positioning or view transitions, so the platform features this audit recommends would ship untested below the slow e2e layer.
- **Recommendation:** Add a Vitest browser-mode project using the already-installed Playwright for interaction-heavy components: listboxes, drawers, focus trap, Genie panel. Adopt @testing-library/react and user-event with one shared render helper for new tests; migrate old files opportunistically. Dev-only, no deploy impact. Fix the Storybook references or add stories.
- **Tech:** Vitest Browser Mode (stable since Vitest 4) with version-matched @vitest/browser-playwright; @testing-library/react 16.3.3; user-event 14.6.7 (npm-verified).

### `stack-v3` All component CSS ships in the initial bundle: lazy surfaces' styles are eagerly imported and there is no @layer

`low` · `upgrade` · effort `M` · verifier-added

- **Evidence:** main.tsx:8 imports components.css eagerly (6,630 lines here; a 14-file @import index on origin/main). Analytics, command-palette, Genie chat and Genie proof CSS total ~66 KB raw of the 141 KB initial CSS; check_frontend_budgets.mjs records six bumps of that gate. Only analytics.scatter.css is route-split. @layer count: 0.
- **Problem:** Render-blocking CSS for lazy routes loads on first paint, and the initial-CSS budget keeps being raised rather than split.
- **Recommendation:** Declare `@layer tokens, base, components, routes` once so order is deterministic, then import route and lazy-surface CSS files from the lazy modules that own them so Vite emits per-chunk CSS. Keep BEM names and tokens untouched; keep shell, KPI, table and Genie FAB styles initial. Ratchet the initial-CSS budget down in the same commit.
- **Tech:** Vite CSS code splitting (default behaviour) plus CSS cascade layers (Baseline widely available).

**Overflow (titles only, not verified)**

- LeadTable family (3,770 lines including tests) has sort and virtualization but no column resize, visibility, reorder or saved views. TanStack Table 9.2 (headless) would add them without changing BEM markup.
- Geography hero is a static SVG state choropleth with no pan or zoom, and the ZIP level is a tile grid, not geography (USChoroplethMap.tsx:741). Option: per-state Census ZCTA TopoJSON with d3-geo and d3-zoom, self-hosted and CSP-safe. MapLibre GL would need CSP worker-src blob:, which is an owner decision.
- 87 bare `.toLocaleString()` calls depend on the viewer's locale, next to fixed en-US Intl formatters and three hand-rolled compact formatters (EvidenceDrawer.tsx:158, lib/formatters.ts:82, portfolio-builder.logic.ts:535). Centralise on cached Intl.NumberFormat instances and add a lint ban.
- Three dead stub files containing only `export {};` (design-system/cards.tsx, navigation.tsx, status-badges.tsx) have zero importers. Delete them.
- CLAUDE.md and eslint.config.js reference Storybook and *.stories.*, but no Storybook or stories exist. Only 3 Playwright visual snapshots guard 6,630 lines of CSS. Consider Storybook 10.6 or Vitest browser-mode visual tests for prototype parity.
- React 19.3 Trusted Types support, combined with zero dangerouslySetInnerHTML in src, means the CSP at backend/main.py:485-493 could add `require-trusted-types-for 'script'` as a procurement win.
- Static Geist weights ship as 7 woff2 files (tokens.css:6-12). @fontsource-variable/geist and geist-mono 5.3.0 would ship 2 files and unlock intermediate weights.
- @vitejs/plugin-react 6.1 adds an experimental native React Compiler path (`react({compiler:true})` with oxc-transform-react) that would drop @babel/core and @rolldown/plugin-babel. Track it until stable.
- Vite 8.1 `build.chunkImportMap` (experimental) stops hash cascade across chunks on frequent redeploys, but it needs an inline importmap and therefore a CSP hash. Evaluate.
- vite.config.ts has no build section: no explicit build.target, no hidden sourcemaps, no `css.transformer: 'lightningcss'`. The `/// <reference types="vitest" />` line yields TS2769 at vite.config.ts:19 when typechecked; import defineConfig from 'vitest/config' instead.
- frontend/public/us-counties.json (770 KB) is never fetched by the frontend; only backend/services/county_names.py reads it. Move it out of public/ so it is not shipped as a web asset.
- There is no .nvmrc or .node-version file. deploy-dev.yml:61 and the ci.yml NODE_VERSION both use a floating '22', while engines requires >=22.22.
- Deprecated APIs in use: MediaQueryList.addListener/removeListener (lib/usePrefersReducedMotion.ts:37-38), and the React FormEvent type (AdminAuditExplorer.tsx:118, PropertyLookupPanel.tsx:103).
- Genie turn progress is poll-based (/api/genie/message/progress). SSE streaming of the server-owned stage vocabulary would be compatible with the fail-closed output guards; token streaming of prose is not.
- No TanStack Query devtools in dev. The absence of a query persister is correct given audit-on-read governance; record it as a deliberate decision.

**Limitations:** No build was run and frontend/dist was not read, so bundle-composition statements rely on the orchestrator's facts and on source reading. Library size figures (web-vitals, d3, markdown-to-jsx, Base UI) are from general knowledge, not measured in this bundle.

The strictness and typed-lint counts come from read-only, no-emit probes using scratch configs under the scratchpad (tsc 6.0.3 and the installed typescript-eslint 8.59). The machine was heavily loaded, so the wall times (2m14s, 3m45s, 7m32s) overstate a quiet-machine run.

I could not capture live Genie turns, so the markdown finding (stack-02) proves the renderer's behaviour from code and tests but infers how often Genie emits ordered lists, headings or tables; its confidence is medium for that reason.

Only the analytics screenshots existed when I checked the ui-shots folder, and they use fixture data; I used them only to read axis-tick rendering. I inferred the Genie line chart's likely edge clipping from its SVG geometry and CSS, not from a rendered image.

Release-note claims (React 19.3, React Router 8.x, Vite 8.1, plugin-react 6.1, Vitest 5, TypeScript 7 with typescript-eslint, anchor-positioning and view-transition Baseline status) were checked through WebSearch/WebFetch summaries and npm metadata on 2026-09-21, not by reading full changelogs.

I did not evaluate the CSS architecture of components.css, preload hints or the theme bootstrap in depth; those belong to other dimensions.

---

## Load performance: bundle, code-splitting and asset delivery (`bundle`) — grade B-

**Auditor:** The fundamentals are strong. The app has no heavy libraries, static assets are brotli-11 precompressed and served immutable, every route is lazy, and the toolchain is Vite 8, Rolldown and React Compiler. The delivery layer is unconfigured. vite.config.ts has no build section, index.html has no resource hints, and Home loads through a 4-level waterfall. 88% of the JS is invalidated by most deploys, and a deploy while a tab is open can blank the app. That is well short of the Linear/Vercel bar, where loads look instant and deploys are invisible.

**Verifier on the grade:** Slightly harsh; B is fairer. Only bundle-01 is a true defect, three of the four 'high' impacts were inflated for a ~105 KiB br entry on enterprise networks, and the audit read a branch 25 commits behind origin/main, where api.ts and components.css are already modularised (bundle output unchanged, but efforts and evidence lines shift).

**Strengths (keep these)**

- **Minimal runtime: 9 production dependencies, no chart, map, animation or UI-kit library** — frontend/package.json lists 9 production dependencies. The scratch build gives total JS 1,134.8 KiB raw / 317.7 KiB br across 44 chunks for 11 routes. The heaviest route closure is segment-intelligence at 145.3 KiB raw / 44.6 br, which includes the map and LeadTable. A Recharts or Mapbox stack would add 300-800 KiB.
- **Static asset serving is already best practice** — tools/precompress_assets.mjs:34-40 emits brotli q11 and gzip 9 siblings at build time. backend/services/static_assets.py:66-102 negotiates the smallest variant and sets Vary. backend/main.py:515-519 stamps immutable one-year caching on /assets, and :895-898 serves index.html no-store. CSP is strict and single-origin (main.py:484-495).
- **Lazy-loading infrastructure with preload and retry semantics** — frontend/src/lib/lazyPreload.ts:10-17 caches the import promise and clears it on failure so a real navigation retries. frontend/src/lib/prefetch.ts is an idle scheduler with cancel. RouteNav.tsx:82-83 preloads on hover and focus. Console, GenieChat and RUM are lazy (AppShell.tsx:17-23, AppContext.tsx:209).
- **Current-generation toolchain with modern defaults already active** — The installed packages are Vite 8.0.16 on Rolldown 1.0.3. Vite's types document the `build.cssMinify` default as 'lightningcss' (node_modules/vite/dist/node/index.d.ts:2063). The default target is baseline-widely-available, i.e. chrome111 / safari16.4 (vite/dist/node/chunks/logger.js:175-181). React Compiler is wired at frontend/vite.config.ts:9-11.
- **A bundle budget gate exists, with a written policy and a dated bump history** — tools/check_frontend_budgets.mjs:12-38 documents a headroom policy and rules for changing a number. It runs in CI at .github/workflows/ci.yml:105, and the font file count is pinned exactly. Its flaws are covered in bundle-08, but most apps of this size have no gate.


### `bundle-01` Deploying while a tab is open can white-screen the app: stale chunk 404s are unhandled and cached immutable for a year

`high` · `defect` · effort `S` · confirmed

- **Evidence:** backend/main.py:515-519 stamps `max-age=31536000, immutable` on every /assets/ response, including the 404 returned at :833-834. frontend/src/lib/lazyPreload.ts:12-15 only clears the failed promise. frontend/src/app.tsx:69 has Suspense but no error boundary. A grep for vite:preloadError, getDerivedStateFromError, componentDidCatch and errorElement in frontend/src returned 0 hits.
- **Problem:** Most deploys re-hash 32 of 44 JS files (see bundle-03). An open tab that then opens an unvisited route gets a 404 chunk. React.lazy rejects and, with no boundary, React unmounts the root. Rollbacks to identical hashes stay broken via the cached 404.
- **Recommendation:** Add a `vite:preloadError` listener in frontend/src/main.tsx that reloads once, with a sessionStorage loop guard. Wrap `<Routes>` in an error boundary offering 'New version available, reload'. Return `Cache-Control: no-store` for non-200 /assets responses. Read `git_sha` (already emitted at backend/api/health.py:280-281, never read in frontend/src) in HealthProvider and toast when it changes.
- **Tech:** Vite `vite:preloadError` event (documented, stable since Vite 4.4). React error boundary or React 19 root `onCaughtError`. Existing /api/v1/health poll. No new dependency.
- **Verifier:** Read main.py:515-519: setdefault stamps immutable on the 404 JSONResponse at :833. Grepped frontend/src and origin/main: no error boundary or vite:preloadError. Browser health body emits git_sha (:357), unread by frontend. React.lazy also caches rejections, so transient failures hit the same hole.

### `bundle-02` Cold load is a 4-level waterfall with no resource hints; boot API calls and the hero map start last

`medium` · `upgrade` · effort `M` · corrected

- **Evidence:** frontend/index.html:35 is the only resource tag, and the built HTML has no modulepreload. Chain on `/`: HTML → entry 104.9 KiB br → home closure (11 files, 21.5 KiB br) → frontend/src/components/mortgage/USStateMapData.ts:8-9 dynamic topo 25.2 KiB br. Boot queries (components/AppContext.tsx:191, FootprintProvider.tsx:143) wait for entry execution.
- **Problem:** Nothing downloads in parallel with the entry. docs/audits/performance-scale-audit.md:99 measured /config/footprint at 2,452 ms, yet it cannot start until 398 KiB of JS downloads, parses and mounts. The hero map sits two fetch levels further back.
- **Recommendation:** Drop `preload as=fetch` for API reads: Firefox does not reuse non-cacheable preloads, so session/options/footprint/health would be fetched twice. Instead emit a tiny hashed same-origin boot module that starts those four fetches and hands the promises to the QueryClient. Keep modulepreload and font preloads (fonts need `crossorigin`). Set `build.manifest` to a non-dot filename so `_spa_fallback` does not serve `/.vite/manifest.json`. Start with the `/` variant only; never prime audit-writing reads.
- **Tech:** modulepreload (Baseline widely available), preload as=fetch, Vite `.vite/manifest.json`. CSP-safe because there is no inline script. Do not assume 103 Early Hints behind the Databricks Apps proxy.
- **Verifier:** Waterfall verified in source: index.html:35, lazy HomeRoute, geometry loaded in a mount effect (USChoroplethMap.tsx:185). Built-HTML claim unverifiable (dist off-limits). API responses carry no cache headers, so Firefox refetches `as=fetch` preloads (Bugzilla 1394778). The auditor's own 170 ms model reads medium, not high.
- **Constraint conflict:** As written, API preloads double-request warehouse-backed endpoints in Firefox; no conflict once replaced by the boot-module approach (CSP `script-src 'self'` still satisfied).

### `bundle-03` No vendor chunking: react, router and TanStack ride in the 398 KiB entry, so every app edit re-downloads ~70 KiB br of unchanged vendor code

`medium` · `upgrade` · effort `S` · corrected

- **Evidence:** frontend/vite.config.ts:6-18 has no `build` key. Build: entry 398.2 KiB raw, 104.9 br, about 250 KiB of it vendor. 31 of 43 lazy chunks import the entry by hash, so any entry edit re-hashes 1,020.7 of 1,134.8 KiB (280.9 of 317.7 br). 17 chunks are ≤3.2 KiB.
- **Problem:** Since 2026-06-21, 83 of 170 frontend commits touched entry-resident modules (api.ts alone 50) versus 7 lockfile changes, so the one-year immutable cache resets on almost every deploy. PageShell is a separate 1.2 KiB request on all 12 routes.
- **Recommendation:** Add `vendor-react` and `vendor-data` groups via `build.rolldownOptions.output.codeSplitting.groups` and land it with the closure-based gate from bundle-08. Do not promise more: an `app-core` group containing api/AppContext re-hashes on the same 50-commit cadence, and every chunk importing it re-hashes with it. Before merging, prove the win with a two-build hash diff around a one-line api edit, and check for cross-chunk circular imports.
- **Tech:** Rolldown 1.0.3 `output.codeSplitting`. The installed define-config d.mts:824-834 marks `advancedChunks` deprecated in its favor. Vite 8.0.16, stable.
- **Verifier:** Reproduced 170 frontend commits, api.ts 50, lockfile 7; vite.config has no build key; installed rolldown 1.0.3 types expose codeSplitting and deprecate advancedChunks. But app-core would hold the churniest module, so the hash cascade persists; only vendor (~22% of br bytes) turns stable.

### `bundle-05` Fonts: seven static files discovered late, no preload, no metric-matched fallback, seven dead .woff twins

`medium` · `upgrade` · effort `S` · confirmed

- **Evidence:** frontend/src/design-system/tokens.css:6-12 imports seven static @fontsource faces. The build emits 14 font files: woff2 94.0 KiB plus never-requested woff 121.4 KiB. The package CSS sets `font-display: swap`. tokens.css:53 falls straight back to ui-sans-serif. frontend/index.html has no font preload (searched `preload`).
- **Problem:** Fonts are requested only after the 148 KiB stylesheet parses and text lays out. Each cold load paints system-ui and then reflows into Geist. That is a visible swap plus layout shift on a product judged on polish.
- **Recommendation:** Switch to `@fontsource-variable/geist` and `@fontsource-variable/geist-mono` with hand-written woff2-only @font-face rules. Preload both from index.html (Vite hashes the href). Add a `Geist Fallback` face with `size-adjust` and `ascent-override` over a local font. Reset `fontAssetCount` to 2 in the budget.
- **Tech:** @fontsource-variable/geist 5.3.0 and geist-mono 5.3.0, verified via `npm pack --dry-run`: latin wght woff2 is 29,400 B and 23,128 B. Variable fonts and metric overrides are Baseline widely available.
- **Verifier:** Summed installed font files: woff2 96,284 B, woff 124,308 B; font-display swap; no preload in index.html. npm shows both variable packages at 5.3.0 and pack --dry-run confirms 29,400 and 23,128 B. Prototype loads the same Geist family, so no design departure.

### `bundle-08` The bundle budget gate measures `index-*.js` rather than the initial closure, budgets gzip not brotli, has ~0.06% raw-CSS headroom, and has only moved up since its 2026-06-10 policy

`medium` · `defect` · effort `M` · corrected

- **Evidence:** tools/check_frontend_budgets.mjs:150 counts only `index-*.js` as initial. Line :129 measures gzip though production serves brotli. Live run: CSS 147.91 KiB against a 148 gate (0.06% headroom versus the 5% policy at :15-18). Gate history in git: initial JS 270 → 417, CSS 90 → 148, total JS 780 → 1,170.
- **Problem:** The '55% growth' is partly an artifact. +105.6 KiB appeared on 2026-06-15 when Vite folded an already-initial shared chunk into index (:40-45). Adopting vendor chunks would make this gate report a false ~60% improvement. Rule 2 (ratchet down) has never fired.
- **Recommendation:** As written, plus: re-baseline raw CSS now, because the next CSS rule trips CI. Keep the existing loadEvent budgets in route_performance.spec.ts and add the throttled LCP/TBT assertion as a lab complement to the RUM LCP/INP field data already collected. Manifest closure maths, per-route brotli budgets and a throttled spec together are M, not S. Use a non-dot manifest filename.
- **Tech:** Vite `build.manifest`. node:zlib brotli. Playwright CDP network and CPU throttling (already a dev dependency).
- **Verifier:** Lines :129 and :150 verified; my Lightning CSS estimate reproduces ~147.9 of 148 KiB. But commit 924b257c tightened gates (initial JS 300→270, lazy 160→104, fonts 230→227), so 'only ever ratcheted up' is wrong. Spec already has loadEvent budgets; RUM records LCP/CLS/INP.

### `bundle-09` Prefetch-on-intent covers only the rail; the core queue → dossier → offer path loads cold on click

`medium` · `gap` · effort `S` · corrected

- **Evidence:** `preloadRouteForPath` is called only at frontend/src/components/layout/RouteNav.tsx:82-83 (git grep). frontend/src/lib/routePreloaders.ts:31-38 idle-preloads 4 of 11 routes. Unhinted links: LeadRowPreview.tsx:123 and :130, LeadTable.tsx:945, CommandPalette.tsx:154. Cold costs: borrower-360 82.5 KiB raw / 26.1 br in 14 files, ask-genie 115.8 / 34.7.
- **Problem:** Opening a lead's dossier, an offer, or a Cmd-K result shows the RouteFallback skeleton while 11-14 files download. This is exactly the queue → dossier → offer path a buyer demo walks.
- **Recommendation:** As written, with two fixes: the palette lives at frontend/src/components/command/CommandPalette.tsx (navigate at :152-159; preload the active item there), and idle preloading must skip /admin-config for non-admin sessions and honor `saveData`. Keep data prefetch off every read that writes VIEW_* audit rows (borrower 360, lead detail); chunk preload only on those links.
- **Tech:** Existing lib/routePreloaders.ts plus TanStack Query `prefetchQuery`. Speculation Rules do not apply: verified MPA-only and not Baseline, and inline rules would be blocked by this CSP.
- **Verifier:** Verified preloadRouteForPath is called only at RouteNav.tsx:82-83, the idle preloader covers 4 routes, no prefetchQuery exists, and the LeadRowPreview/LeadTable links are plain. Cited palette path is wrong (components/command/, not layout/). Chunk sizes unverifiable; Speculation Rules reasoning holds.

### `bundle-04` Click-gated surfaces ship in the shell: EvidenceDrawer body, registry prose and the Cmd-K dialog

`low` · `upgrade` · effort `L` · corrected

- **Evidence:** frontend/src/components/layout/AppShell.tsx:8-9 statically imports CommandPalette and EvidenceDrawer, mounted at :173 and :178. CommandPalette.tsx:197 returns null until opened. Entry attribution (pre-minify): api.ts 32.8 KiB, EvidenceDrawer 32.6, drawerSourceRegistry 27.2, drawerSources 15.3, palette 20.2. frontend/src/lib/api.ts:980 is one ~150-member object literal.
- **Problem:** Roughly 60 KiB raw / 16 KiB br of first-paint JS serves interactions that have not happened. AppShell.tsx:27-37 still claims drawerSources lives in a lazy chunk. The build shows the entry dynamically importing itself, so `preloadDrawerSources` (:37) is a no-op.
- **Recommendation:** Do the cheap part first (S-M): keep the Cmd-K key listener and an empty drawer frame (scrim + aside, so the slide-in still animates) in the shell, and lazy-load the palette dialog and drawer body with `lazyWithPreload` on idle/hover. Keep a slim registry index in the shell, honoring the documented 2026-07-13 decision that chips resolve immediately. Rebase first: origin/main already split api.ts into apiClients/* behind a spread facade; moving 63 importers and 39 test mocks off that facade is a separate L task. Fix the stale AppShell comment and dead preloader.
- **Tech:** React.lazy plus the existing frontend/src/lib/lazyPreload.ts and prefetch.ts. ES named exports for tree-shaking. No new dependency. Sizes estimated at rendered bytes × 0.63.
- **Verifier:** Static imports (AppShell.tsx:8-9) and stale comment verified; palette lives in components/command/. The api literal has 88 members, not ~150. Sizes unverifiable. About 16 KiB br is 15% of the entry; the api migration touches 63 importers and 39 test mocks.

### `bundle-06` One render-blocking 147.9 KiB stylesheet carries every route's CSS

`low` · `upgrade` · effort `L` · corrected

- **Evidence:** frontend/src/main.tsx:7-9 imports tokens, components and print CSS eagerly. The build emits index CSS at 147.91 KiB raw / 21.16 br. components.css is 6,630 lines with 268 BEM blocks (genie*, analytics*, campaign*, growth-agent*, offer-mock). Only frontend/src/routes/analytics.equity-scatter.tsx:33 co-locates CSS (6.28 KiB chunk). `@layer` count: 0.
- **Problem:** First paint of any route blocks on CSS for all eleven. One selector tweak re-hashes the whole sheet. The initial-CSS gate has grown 64% since May (90 → 148 KiB) with no structural brake.
- **Recommendation:** Rebase first: origin/main already splits components.css into 14 positional partials, still imported globally. Move only route-exclusive partials (genie chat/markdown, native analytics, command palette) into their lazy chunks, proving no cross-partial selector overlap with tools/refactor_proof.py css. Treat `@layer` as a separate, screenshot-verified change: layering ~200 KiB of specificity-ordered CSS flips existing cascade wins. Ratchet the CSS gate down in the same commit. BEM names unchanged.
- **Tech:** Vite `build.cssCodeSplit` (default on). CSS cascade layers (Baseline widely available since 2022). Lightning CSS minify is already the Vite 8 default.
- **Verifier:** Lightning CSS minify of the three sheets reproduces ~146 KiB plus font-face rules; zero @layer; only scatter CSS co-located. But the blocking cost is 21 KiB br, and origin/main already split components.css into 14 global partials. Small payoff for L effort.

### `bundle-07` Hero map geometry is fetched only after Home mounts; us-counties.json ships publicly though no browser code fetches it

`low` · `upgrade` · effort `S` · corrected

- **Evidence:** frontend/src/components/mortgage/USStateMapData.ts:8-9 dynamic-imports the whole topojson-client namespace (13 modules, 6.7 KiB, only `feature` used) plus states-albers-10m (79.7 KiB raw / 25.2 br, 7,178 points). Home statically imports USChoroplethMap. `git grep us-counties -- frontend/src` returns 0 hits. USChoroplethMap.tsx:50-59 documents the county level's removal.
- **Problem:** The landing page's hero surface needs a fourth sequential fetch plus main-thread topology→GeoJSON→path conversion for a pre-projected map under 975 px wide. The 752 KiB us-counties.json is read only by backend/services/county_names.py:18, yet it ships publicly, unhashed and unprecompressed.
- **Recommendation:** First, S: start `loadUsaStateMap()` when the home chunk evaluates (or inside `HomeRoute.preload`) so geometry downloads beside the route closure, and import `feature` by name. Defer build-time simplification; the state-rollups call, not 25 KiB br of geometry, gates the map. Leave us-counties.json alone unless the places guard, county_names and both tools move together, with a test pinning an identical county-name set.
- **Tech:** topojson-simplify (Visvalingam) as a build-time dev dependency, stable. Vite hashed JSON asset. Existing precompress and immutable pipeline.
- **Verifier:** USStateMapData.ts:8-9, the :185 mount effect and zero frontend us-counties references verified. But marketing_selection_reviewed_places.py:102 and two tools also read that file, not only county_names.py. topojson-simplify last published 2022. The 1,588 ms rollups API gates the map, not geometry.
- **Constraint conflict:** us-counties.json feeds the fail-closed marketing-selection places guard (backend/schemas/marketing_selection_reviewed_places.py:102); swapping it for a derived names file risks silently changing guard vocabulary.

### `bundle-10` Instant shell: paint the rail, topbar and skeleton from index.html before any JavaScript arrives

`low` · `wow` · effort `M` · corrected

- **Evidence:** frontend/index.html:34 ships an empty `#root`, so nothing paints until the 104.9 KiB br entry executes. frontend/src/components/AppContext.tsx:224 sets `data-theme` inside a React effect, so non-default themes flash. CSP at backend/main.py:489-490 forbids inline scripts but allows inline styles.
- **Problem:** First contentful paint is gated on the full JS bundle. The theme is wrong for a frame for anyone off the default. That reads as unpolished next to Linear- or Vercel-class shells.
- **Recommendation:** Split in two. S: ship a tiny external, render-blocking `/theme-boot.js` in `<head>` that copies mip.theme/accent/density from localStorage onto `<html>`; it is CSP-safe (`script-src 'self'`) and needs no cookie or FastAPI templating. M: inject the prototype-BEM skeleton via `transformIndexHtml`, with a test pinning it to AppShell's markup. Expect first paint at HTML plus the 21 KiB br stylesheet, not at HTML arrival: the async-CSS `onload` trick is an inline handler this CSP blocks.
- **Tech:** Vite `transformIndexHtml` hook. FastAPI attribute templating, sharing the index.html variant builder from bundle-02. No inline script, so the CSP is unchanged.
- **Verifier:** Empty #root (index.html:34), effect-time data-theme with default dark (AppContext.tsx:164, :224) and CSP verified. FCP model is optimistic: Vite's head stylesheet still blocks skeleton paint. The theme flash needs no server templating; a same-origin boot script fixes it. Skeleton duplicates shell markup.

### `bundle-v1` Every page downloads a 2048x214 RGBA wordmark PNG to draw a 16 px-high logo: unhashed, no Cache-Control, no width

`low` · `defect` · effort `S` · verifier-added

- **Evidence:** frontend/src/components/brand/Entrada.tsx:115-121 renders `/brand/entrada-wordmark.png` with `height` only. `file` reports 2048x214 RGBA, 34,758 B. Displayed at 16 px (PageShell.tsx:61), 28 and 44 px. backend/main.py mounts /brand through StaticFiles; the immutable header at :515 covers only /assets/.
- **Problem:** The logo outweighs the whole brotli stylesheet, has no content hash or Cache-Control so it is revalidated every load, and reserves no width, so the page header shifts when it decodes.
- **Recommendation:** Import the wordmark through Vite from src/assets so it is hashed and served immutable by the existing /assets route. Ship an SVG, or a roughly 850 px-wide WebP with PNG fallback, and set explicit `width` and `height` from the 2048:214 ratio. Remove the /brand mount once unused.
- **Tech:** Vite static asset imports; SVG or WebP (Baseline widely available); existing immutable /assets pipeline. No new dependency.

**Overflow (titles only, not verified)**

- The premise that the browser fetches the counties TopoJSON is wrong. frontend/public/us-counties.json (752.5 KiB) is never requested by frontend code since the county level was removed on 2026-08-08. It is backend-only data served publicly via the bare FileResponse at backend/main.py:893-894, with no Cache-Control and outside the precompress scope (tools/precompress_assets.mjs:25). Details are in bundle-07.
- Service worker verdict: it is feasible under this CSP, because worker-src falls back to script-src 'self'. It adds little over immutable HTTP caching plus idle route preloading. A navigation-handling service worker could mask Databricks Apps OAuth re-auth redirects. If adopted, limit it to precaching hashed /assets only.
- Dynamic API responses use only gzip level 6 (backend/main.py:539). A brotli q4-5 or zstd ASGI middleware (starlette-compress) would cut large JSON about 15-20%. The prior audit measured 628 KB for /api/leads at limit=500.
- Compression Dictionary Transport (dcb/dcz delta compression between deploys) is verified as Chrome and Edge 130+ only and not Baseline. Park it; bundle-03 captures most of the benefit with stable technology.
- /brand/* (StaticFiles mount, main.py:847) and the root favicons are served without Cache-Control, so they are revalidated on every load.
- The Entrada wordmark is a 2048x214 RGBA PNG (34 KiB) rendered 16-44 px tall with a height but no width attribute (frontend/src/components/brand/Entrada.tsx:116-121). That shifts layout on load and over-decodes. Ship SVG or a 2x AVIF/WebP with explicit width and height.
- The router runs in declarative BrowserRouter mode (frontend/src/main.tsx:16). Data mode (createBrowserRouter + route.lazy + loaders calling queryClient.ensureQueryData) would fetch route code and non-audited data in parallel. It would also unlock the router's viewTransition support.
- React Compiler via @rolldown/plugin-babel consumed 92% of build plugin time (Rolldown PLUGIN_TIMINGS in the scratch build). Scope the babel plugin to src/**/*.tsx, and watch for an Oxc-native compiler.
- There are no production sourcemaps. `build.sourcemap: 'hidden'` would let RUM long-task and error reports be symbolicated without exposing maps publicly.
- Vite's modulepreload polyfill (1.2 KiB) ships because the default target includes Safari 16.4. Set `build.modulePreload.polyfill: false` or raise the target once the browser support policy is confirmed. The gain is trivial.
- RUM installs only after /config/options resolves (frontend/src/components/AppContext.tsx:207-209). Buffered observers recover LCP and CLS, but a boot that fails before config loads reports nothing.
- print.css (2.4 KiB) is bundled into the render-blocking sheet via frontend/src/main.tsx:9. Load it with media=print instead. The gain is trivial.

**Limitations:** - **Nothing was measured live.** Deploys, the Databricks CLI and the deployed app were out of bounds. Every millisecond figure is a model from byte counts at stated bandwidth and RTT. None comes from Lighthouse or RUM.
- **The proxy was not checked.** I could not verify whether the Databricks Apps reverse proxy speaks HTTP/2 or HTTP/3. I also could not verify whether it passes Link, Cache-Control and Content-Encoding headers through unchanged, or whether it caches 404s.
- **The white-screen failure in bundle-01 was not reproduced.** It is derived from code reading: no error boundary or preloadError handler exists, and immutable caching is stamped on all /assets responses.
- **frontend/dist was never read.** Sizes come from the project's own budget tool and one scratch production build of HEAD (3f19b44a). An analysis plugin in that build recorded chunk imports and per-module rendered lengths. Its output matched the budget tool exactly: 398.24 KiB JS and 147.91 KiB CSS.
- **Per-module minified sizes are estimates.** I used rendered bytes times about 0.63 for src code. That ratio comes from the entry total minus a standalone top-level-mangled minify of react-dom at 171 KiB. The vendor share of about 250 KiB raw / 70 KiB br is likewise estimated.
- **The CSS split estimate is rough.** It comes from BEM-block attribution of components.css, not a trial split.
- **No screenshots were reviewed.** The screenshot manifest did not exist when checked, and this dimension does not depend on it.
- **Web verification covered two claims.** Those were Compression Dictionary Transport support and Speculation Rules applicability.
- **Rolldown and Vite option names and defaults were checked locally.** I read them from the installed node_modules type definitions.
- **Variable-font sizes were checked with `npm pack --dry-run`.**

---

## Runtime performance: rendering, state and data fetching (`runtime`) — grade C+

**Auditor:** The data-fetching foundations are genuinely thoughtful (audit-aware QueryClient policy, keepPreviousData, AbortSignal threading, CLS-budgeted e2e), but the React Compiler silently produces no output for every heavy screen, LeadTable has run with neither compiler nor manual memoization since May, one 34-field context fans every interaction out to the whole shell, a lead-queue cache key serves wrong rows, and an in-flight Genie answer dies when the panel closes. Zero adoption of View Transitions, Activity, useTransition/useDeferredValue/useOptimistic, useMutation or CSS containment leaves it well short of the Linear/Stripe bar.

**Verifier on the grade:** Slightly harsh but defensible. The three defects reproduce exactly (lead-queue cache key, in-flight Genie loss, compiler bailouts), yet two "high" impacts are unprofiled and bounded by a 30-row virtual window, and the "zero adoption" list penalises APIs such as ViewTransition that went stable only 12 days ago; against foundations this careful (audit-aware QueryClient, virtualization, preloaders, RUM, budgets) a B- is the fairer mark.

**Strengths (keep these)**

- **Audit-aware TanStack Query default policy** — frontend/src/lib/queryClient.ts:14-35 disables refetchOnWindowFocus with an explicit rationale (read endpoints write VIEW_* audit rows), retries only warming-up 503s using a dependency-specific plan, and sets mutations.retry false. staleTime 30 s / gcTime 5 min bound cache memory.
- **Stable filter refreshes: keepPreviousData plus CLS-budgeted e2e** — 15 filter-driven queries pass keepPreviousData through frontend/src/lib/useWarmingUpRetry.ts:126; frontend/tests/e2e/layout-stability.spec.ts:361-457 observes layout-shift entries and asserts per-anchor position/size/CLS budgets across async refreshes on Segments, Analytics, Portfolio Builder and Lead Queue (467-727).
- **Intent and idle code preloading with a governance guard** — frontend/src/lib/routePreloaders.ts:30-45 idle-preloads four route chunks and RouteNav.tsx:82-83 preloads on hover/focus; frontend/tests/e2e/route_performance.spec.ts:278-352 proves hover/idle prefetch never touches /api/borrowers, /api/leads, /api/audit or evidence endpoints.
- **Cancellation and polling hygiene** — AbortSignal flows from useQuery to fetch (useWarmingUpRetry.ts:123, api.ts:825); HealthProvider.tsx:206-226 and 275-281 run one shared health poll with an in-flight latch that pauses while the tab is hidden; Topbar.tsx:210-241 debounces and aborts borrower search.
- **Bounded Genie memory and a correctly built chart hover** — genieConversationStore.ts:31 caps the transcript at 20 turns and persists settled turns only; GenieAnswer.logic.ts:8-9 caps answer tables at 10 rows x 4 columns; actorScopedBrowserState.ts clears actor-scoped caches. analytics.charts.tsx:308-353 memoizes the chart model and stores only the nearest index, so pointer moves rarely re-render.


### `runtime-01` Closing the Genie panel, pressing Esc or navigating kills the in-flight answer

`high` · `defect` · effort `L` · corrected

- **Evidence:** AppShell.tsx:217 unmounts LazyGenieChat when genieOpen is false; GenieChat.tsx:98-103 aborts the ask on unmount; genieAsk.ts:88-120 stops polling on abort and never persists conversation/message/progress_token. ask-genie.tsx:164-194 runs the ask as a signal-consuming useQuery, cancelled on route leave.
- **Problem:** Genie turns take 30 seconds to minutes. Esc, Close, the topbar toggle or any navigation silently discards the turn; only settled turns persist, so the question vanishes. Users must babysit a first-class surface instead of working.
- **Recommendation:** Own the in-flight turn in a module-level external store (the existing genieConversationStore pattern) holding promise, live progress and ids; panel, route and FAB subscribe via useSyncExternalStore, because useMutationState cannot carry onProgress. Remove the abort-on-unmount effect first: Activity mode hidden also tears effects down, so Activity alone would still abort. Persist conversation, message, token and question in sessionStorage and resume polling on reload; abort and clear them on GENIE_CONVERSATION_RESET_EVENT. Add the FAB progress ring and a completion notice; Activity is optional polish for draft and scroll.
- **Tech:** TanStack Query v5 MutationCache + useMutationState (stable); React 19.2 Activity (stable, already installed); existing genieConversationStore external-store pattern.
- **Verifier:** Opened all four sites: panel unmounts when closed, unmount aborts, poll loop stops, route ask is a signal-consuming query. Token is actor-bound with 15-minute expiry, so resume is feasible. Two 600-850-line surfaces plus reset semantics make this L, not M.
- **Constraint conflict:** None, provided the actor-boundary reset still aborts the turn and clears the persisted progress token (fail-closed identity boundary stays).

### `runtime-02` Lead Queue query key omits `cities`: two city cohorts share one cache entry

`high` · `defect` · effort `S` · confirmed

- **Evidence:** lead-queue.tsx:229 sends cities to api.leadsPage and lists it in deps (253), but the explicit queryKey (268-289) omits it; useWarmingUpRetry.ts:118 ignores deps once a queryKey is passed. genieCellLinks.ts:200 builds links carrying only geo.cities.
- **Problem:** Opening CHICAGO~IL then SPRINGFIELD~IL from a Genie answer resolves to one cache entry: inside the 30 s staleTime the queue shows Chicago rows under a Springfield chip with no refetch. Wrong data in an evidence-first product.
- **Recommendation:** Build one leadsRequest object and pass it to both the fetcher and queryKeys.leads(['lead-queue', leadsRequest]); TanStack hashes object keys deterministically. Delete the vestigial deps parameter from useWarmingUpRetry so the two lists cannot drift. Add a unit test asserting every api.leadsPage argument changes the key; apply the same shape at segment-intelligence.tsx:400.
- **Tech:** TanStack Query v5 queryOptions() factories with object keys (stable); no new dependency.
- **Verifier:** Verified cityFilters reaches api.leadsPage and deps but not the queryKey; all 25 production callers pass queryKey, so deps is dead. My scan of every call found no other real drift. Also triggers on city link then bare Lead Queue within 30 s.

### `runtime-03` React Compiler emits nothing for 25 of 98 component files, including LeadTable, the hero map, GenieChat, ask-genie, offer-orchestrator and portfolio-builder

`high` · `defect` · effort `M` · corrected

- **Evidence:** Ran babel-plugin-react-compiler 1.0.0 with vite.config.ts defaults over src: 38 CompileErrors in 18 files (22 are try/finally) plus 8 'use no memo' pragmas. No memo cache is emitted for AppProvider, LeadTable, USChoroplethMap, GenieChat, Topbar, ask-genie, offer-orchestrator, portfolio-builder or any analytics file.
- **Problem:** 'Compiler ON' holds for leaf components only. The 800 to 1,200-line screens that own the INP-critical interactions run unmemoized, and eslint recommended-latest does not report Todo bailouts, so the gap is invisible.
- **Recommendation:** Ship the one-line USChoroplethMap hoist now (I applied it in memory and the component compiles). Add the logger-based coverage gate with an explicit allowlist; enabling the react-hooks/todo lint rule does not report these bailouts (tested on GenieChat and the map). Then extract try/finally handlers file by file, starting with GenieChat and LeadTable. Treat the six analytics pragmas separately: they were a deliberate bundle-budget trade, so re-measure the budget before removing them.
- **Tech:** babel-plugin-react-compiler 1.0.0 logger/CompileError events (latest stable, verified via npm view); try/finally is a documented 1.0 limitation (facebook/react#34131).
- **Verifier:** Reproduced exactly with the plugin logger: 38 CompileErrors, 18 files, 22 try/finally, 8 pragmas; the line-509 hoist compiles the map. But LeadQueue, SegmentIntelligence, Home, Borrower360, Console and EvidenceDrawer do compile, and analytics opt-outs are a documented bundle-budget choice.

### `runtime-06` Query-layer migration stalled: 12 effect-driven fetches, zero useMutation, hero map blanks on every filter change

`high` · `upgrade` · effort `L` · corrected

- **Evidence:** Scripted scan: 12 useEffect+api sites in 9 files against 52 query hooks; 0 useMutation. USChoroplethMap.tsx:204-232 refetches state rollups uncached, passes undefined as the signal (211) and nulls the facts (207). offer-orchestrator.tsx:110-227 hand-rolls retry over an unbounded Map (offer-orchestrator.cache.ts:27).
- **Problem:** Each segment toggle pulses the hero map to a loading state and leaves superseded warehouse queries running; Home and Segments never share rollups. eslint.config.js:38 carries TODO(2026-07-15) for this migration and queryKeys.offerRecommendation/outreachDraft sit unused.
- **Recommendation:** Phase 1 (M): move map stateRollups and overlay to useQuery with keepPreviousData plus a visible updating state, so old-cohort colors are never presented as the current cohort; Home and Segments then share the cache. Phase 2 (L): remaining effects and the offer trio via useQueries, preserving the borrower/offer snapshot-reconcile loop, which is not a generic retry. useMutation for approve/reject must stay pessimistic (pending until the Lakebase audit write returns); optimistic updates only for save/assign-style actions. Then re-enable react-hooks/set-state-in-effect. Do not claim warehouse savings unless the backend adds statement cancellation.
- **Tech:** @tanstack/react-query 5.10x useQueries/useMutation/keepPreviousData (stable); React 19 useOptimistic optional.
- **Verifier:** Reproduced 12 effect fetches in 9 files, zero useMutation, unused keys, the TODO, and all states flipping to is-loading per toggle. Backend never cancels warehouse statements on disconnect, so abort saves browser work only. Optimistic approve conflicts with the approval gate.
- **Constraint conflict:** Optimistic cache updates for approve/reject would show an approval before the Lakebase audit row exists; the approval-gate and audit posture require pessimistic confirmation on those actions.

### `runtime-04` LeadTable has had neither compiler nor manual memoization since 2026-05-18

`medium` · `defect` · effort `L` · corrected

- **Evidence:** Commit ece907fb removed 21 memo hooks and added 'use no memo' (LeadTable.tsx:76). Each render makes 7 O(n) passes over the default 500 leads (132-146, 434-442, 555, 796), re-spreads every lead object (132), and the inline getItemKey (160) makes virtual-core rebuild all 500 measurements.
- **Problem:** Renders fire on every scroll range change, every keystroke in four inline forms and every AppContext change, and new lead identities defeat the compiled LeadTableRow. frontend-performance-v3-audit.md records this as closed; the code contradicts it.
- **Recommendation:** Profile first (React Profiler at 4x CPU: keystroke in the reject form, scroll, row expand) so the fix is sized by data. Cheap wins in one slice: stable getItemKey via useCallback over a memoized id array, overscan 12 to 5, memoized override merge. Second slice: move reject, disposition and bulk-rationale state into LeadTableDecisionPanels so typing never re-renders the table. Third: extract try/finally handlers and drop the pragma. Correct docs/audits/frontend-performance-v3-audit.md, which records this as closed.
- **Tech:** React Compiler 1.0 once try/finally is extracted; @tanstack/react-virtual 3.14 (stable getItemKey per its memo deps, virtual-core index.js:409-418); Intl.Collator; React 19 startTransition.
- **Verifier:** Commit ece907fb (2026-05-18) removed 22 memo hooks and added the pragma; per-render passes, lead re-spread and inline getItemKey (a virtual-core memo dependency) confirmed. But .tbl-wrap is 520px so about 30 rows render, no profile was supplied, and seven test files cover 1,261 lines.

### `runtime-05` One 34-field AppContext re-renders 24 consumers, including a chip and meter in every table row

`medium` · `upgrade` · effort `M` · corrected

- **Evidence:** AppContext.tsx:424-462 memoizes one value over 17 state fields; 24 files call useApp(). EvidenceChip (Primitives.tsx:98) and ConfidenceMeter subscribe once per lead row. Row expand calls setLastBorrowerId (LeadTable.tsx:1097), drawer open calls setDrawer. HealthProvider.tsx:240-242 publishes a new value every 8 s.
- **Problem:** Expanding a row or opening the evidence drawer re-renders Topbar, Console, CommandPalette, every chip and meter, plus the uncompiled LeadTable, offer-orchestrator and GenieChat. The two most frequent interactions pay for the whole shell.
- **Recommendation:** Keep useApp() as a compatibility facade (39 test files mock it). Add narrow selector hooks on a useSyncExternalStore store (pattern already in pinnedInsights.ts) for the hot consumers first: EvidenceChip, ConfidenceMeter, LeadTable and RouteNav's lastBorrowerId. No zustand dependency needed. Leave HealthProvider alone: its debounce and fast-poll logic is tested and only two shell components consume it. Do this after runtime-03/04, which remove most of the re-render cost.
- **Tech:** React 19 useSyncExternalStore selector stores or zustand 5 (about 1 KB, stable); TanStack Query structural sharing + select.
- **Verifier:** Single memoized 34-field value and 24 useApp files confirmed; EvidenceChip and ConfidenceMeter subscribe per row. Compiled consumers absorb most fan-out, so cost concentrates in the uncompiled ones. 39 test files mock AppContext; the health tick re-renders only Topbar and AppShell.

### `runtime-08` No scroll reset, scroll restoration or list-state restore across navigation

`medium` · `gap` · effort `M` · confirmed

- **Evidence:** .main is the persistent scroller (components.css:409-412); app.tsx:68 re-keys the route on pathname; searching src for ScrollRestoration|scrollTo(|scrollTop finds only GenieChat.tsx:175. LeadTable sort, expanded row and selection are local useState (LeadTable.tsx:114-116, 179), not URL state.
- **Problem:** Open Borrower 360 from row 140 and press Back: the queue remounts at the top, unsorted, collapsed, selection gone. Nothing resets .main on forward navigation either, so routes can open mid-scroll. The loan officer's core loop restarts every time.
- **Recommendation:** Add useMainScrollRestoration keyed by location.key (sessionStorage; reset to 0 on PUSH, restore on POP) and feed the table's saved offset to the virtualizer initialOffset. Put sort key and expanded borrower in search params. Option: keep Lead Queue alive under Activity mode hidden while a borrower route is open.
- **Tech:** React Router 8 useLocation/useNavigationType; TanStack Virtual initialOffset/scrollToOffset; React 19.2 Activity (stable).
- **Verifier:** `.main` is the persistent overflow scroller, BrowserRouter is declarative (no ScrollRestoration), and my search found no reset or restore. LeadTable sort, expanded row and selection are local useState and the route remounts per pathname key. The table's own 520px scroller also needs restoring.

### `runtime-09` Field and lab telemetry cannot catch an interaction regression

`medium` · `gap` · effort `L` · corrected

- **Evidence:** rum.ts:187-220 reports the single max event duration with no target, phases or LoAF, attributed to whichever route is open at pagehide; CLS is cumulative (161-176); 'api_call' (10) is never emitted. settings.py:495 defaults RUM off; telemetry.py:46 only logs. route_performance.spec.ts:179 asserts loadEventEnd within 4-7 s, live-only.
- **Problem:** INP here is neither the spec's metric nor attributable to a component, nothing is stored or charted, and CI has no interaction budget. The LeadTable regression above shipped unnoticed for four months.
- **Recommendation:** Ship the fixture-mode Playwright interaction budget first (page.route fixtures already exist in e2e; CDP 4x CPU throttle; scroll and expand the 500-row queue) because it is the cheapest regression catch. Then adopt the web-vitals 6.2.2 attribution build with reportAllChanges, tagging each report with the route at interaction time (there is no stable soft-navigation reset). Sanitize interactionTarget selectors before sending: the telemetry schema rejects borrower-id-shaped strings. Lakebase persistence via a deploy.sh migration and the Admin p75 view come last. LoAF stays Chromium-only progressive enhancement.
- **Tech:** web-vitals 6.2.2 (verified via npm view); Long Animation Frames API (Chromium 123+ only, degrades elsewhere); Playwright CDPSession Emulation.setCPUThrottlingRate.
- **Verifier:** rum.ts confirmed: max event duration, cumulative CLS, no target, api_call never emitted; RUM defaults off and only logs; perf spec is live-only. Max equals INP under 50 interactions, so the metric claim is overstated. Lakebase table, Admin chart and CI budgets make this L.
- **Constraint conflict:** New Lakebase table must ship as a migration reachable from scripts/deploy.sh; attribution selectors must pass the existing PII-rejecting telemetry schema. Fixture-mode budgets are test-only, not a runtime mock.

### `runtime-10` Compositor-driven View Transitions for navigation, drill-down and the Console reflow

`medium` · `wow` · effort `L` · corrected

- **Evidence:** Searching src for view-transition|ViewTransition|startViewTransition returns 0 hits. app.tsx:68-69 nests Suspense inside the re-keyed div, so each navigation mounts a fresh boundary and shows RouteFallback. components.css:414-419 and 442 animate padding-right on the container-query .main, relayouting the whole route every frame.
- **Problem:** Route changes are a one-property fade with a skeleton flash, the Console slide is the most expensive animation in the app, and nothing visually connects a lead row to its dossier or a state to its ZIP grid.
- **Recommendation:** Phase 1 (M): bump react/react-dom and @types to 19.3.0 after a short soak (release is 12 days old), hoist Suspense above the keyed div, wrap the outlet in ViewTransition and delete the .route-transition keyframe to avoid a double animation; force reduced motion in the visual-snapshot specs. Phase 2: shared-element names, unique per borrower id. Phase 3: Console reflow, which requires moving data-console from the passive effect on <html> (AppContext.tsx:238) into render, otherwise React's snapshot misses the padding change. Transition types now work in Firefox 147+; keep everything progressive.
- **Tech:** React 19.3 ViewTransition/addTransitionType (stable 2026-09-09, verified on react.dev); same-document View Transitions Baseline Newly available (Firefox 144+); Firefox lacks transition types, degrade gracefully.
- **Verifier:** Zero view-transition hits; Suspense sits inside the keyed div; padding-right animates on the container-query .main. react@19.3.0 (published 2026-09-09) exports stable ViewTransition and BrowserRouter wraps navigations in startTransition. Firefox 147 already supports SPA transition types. Full scope is L.
- **Constraint conflict:** Motion is additive to the design_files prototype; CLAUDE.md requires the commit to call out the departure and cite the prototype line. No CSP or SSR impact.

### `runtime-v1` Dragging or resizing the Genie panel re-renders the whole uncompiled GenieChat and its transcript on every pointermove

`medium` · `upgrade` · effort `M` · verifier-added

- **Evidence:** useGenieWindow.ts:228-241 and 284-306 call setPosition/setSize per pointermove; the hook carries 'use no memo' (150). GenieChat emits no compiler cache (my logger run: two try/finally errors), renders msgs.map(GenieAnswer) at 522 and applies inline width/height/left/top (393-406).
- **Problem:** Every pointer event during drag or resize re-renders a 637-line component plus every answer table and chart in the thread, and left/top/width changes force layout each frame. A first-class surface judders exactly when a buyer grabs it.
- **Recommendation:** During the gesture write position and size to the panel element through a ref inside requestAnimationFrame (transform for drag, width/height for resize); commit to React state and localStorage once on pointerup. Keep keyboard resize on state. Extract GenieChat's try/finally handlers so the compiler memoizes the transcript.
- **Tech:** Pointer capture + requestAnimationFrame + CSS transform (universal support); React Compiler 1.0 once try/finally is extracted.

### `runtime-v2` Any Escape keypress anywhere closes Genie and aborts its in-flight turn

`medium` · `defect` · effort `S` · verifier-added

- **Evidence:** GenieChat.tsx:188-200 registers a window-level keydown closing the panel on Escape with no target or layer check. useFocusTrap.ts:46-50,82 (EvidenceDrawer, CommandPalette, dialogs inside GenieAnswer) and FilterSelect.tsx:36-41 handle the same window event without stopping it; unmount then aborts the ask (98-103).
- **Problem:** Pressing Esc to dismiss the evidence drawer, command palette or a filter menu also closes Genie and, per runtime-01, silently discards a running answer. One key closes two layers.
- **Recommendation:** Introduce a small topmost-layer Escape stack shared by useFocusTrap, FilterSelect and GenieChat so only the top overlay handles the key; Genie closes on Escape only when focus is inside the panel and no other layer is open. Add a unit test with the drawer and Genie both open.
- **Tech:** Plain DOM keydown handling with a shared overlay stack; no library or new browser API required.

### `runtime-07` Map hover lives in React state: every mousemove re-renders a 1,044-line uncompiled component

`low` · `upgrade` · effort `S` · corrected

- **Evidence:** USChoroplethMap.tsx:621-623 and 793-795 call setHover({...h,x,y}) per mousemove. The component emits no compiler cache, so each event rebuilds 51 paths with 5 closures each, or at ZIP level re-sorts the full ZIP list (718) and rebuilds 24 tiles.
- **Problem:** Pointer tracking at 60 to 120 Hz drives full React renders on the hero surface, so the tooltip trails the cursor whenever the main thread is busy. Node count (51 paths, at most 24 tiles) is small; Canvas or WebGL is not the fix.
- **Recommendation:** Land runtime-03's hoist first, then profile hover with React Profiler at 4x CPU throttle. Only if per-move renders still cost more than a few milliseconds: keep the hovered id in state, write tooltip x/y to CSS custom properties through a ref inside requestAnimationFrame, and memoize the sorted ZIP list. Canvas/MapLibre stays out of scope for 51 paths.
- **Tech:** Pointer events + requestAnimationFrame + CSS custom properties (universal support); no library needed.
- **Verifier:** Both mousemove handlers call setHover with new coordinates: confirmed. But I compiled the map with runtime-03's one-line hoist applied and it emits a memo cache, which should make per-move renders cheap. Sequence after that fix and profile first.

**Overflow (titles only, not verified)**

- Zero content-visibility and zero contain: declarations in 6,630 lines of components.css (grep); add content-visibility:auto + contain-intrinsic-size to below-fold surfaces (Home map, analytics sections) and contain: layout paint on .surface/.drawer/.genie (content-visibility is Baseline Newly available).
- Zero useTransition/useDeferredValue/useOptimistic in src (grep); Suspense is used only for code-splitting (3 sites: app.tsx:69, AppShell.tsx:179,216) - adopt useSuspenseQuery with route-level boundaries after the query migration.
- No data prefetch-on-intent (0 prefetchQuery/ensureQueryData): allowlist non-audited aggregates (state rollups, home summary, config) on nav hover and extend the guard at route_performance.spec.ts:321 so audited endpoints stay excluded.
- Double retry layering: api.ts:816-838 retries 3x inside QueryClient retry of up to 6 (queryClient.ts:19-27), so one key can issue up to 18 requests against a warming warehouse or open breaker.
- /ask-genie models a POST submit as a useQuery with warming-up retry (ask-genie.tsx:164-194); a retry can re-submit the question to Genie - belongs in a mutation.
- Approve is a 2-call serial waterfall per lead (LeadTable.tsx:255-271 draftOutreach then approve); a bulk of 50 is 100 round trips in batches of 3 - consider a server-side approve-with-governed-draft endpoint that still writes one audit row per lead.
- Paint-bound infinite animations: skeleton shimmer animates background-position (components.css:4947-4953) and the topbar heartbeat animates box-shadow forever (302-309); move to transform/opacity pseudo-elements.
- .reveal-on-scroll keeps will-change: opacity, transform permanently after reveal (components.css:5259); 9 `transition: all` rules (317, 381, 510, 646, 775, 898, 3506, 4059, 4097).
- LEAD_VIRTUALIZATION_THRESHOLD = 120 (LeadTable.constants.ts) mounts up to ~1,800 cells un-virtualized for lists of 120 rows or fewer; lower to about 40.
- Analytics route opted out of the compiler to satisfy a byte budget (analytics.tsx:1-3): revisit the 160 KiB lazy-chunk budget against runtime cost instead of disabling memoization on the chart-heaviest route.
- Borrower search logic is duplicated in Topbar.tsx:210 and CommandPalette.tsx:103 with no shared cache; one debounced useQuery would dedupe and cache.
- Genie progress uses 1.5 s polling (genieAsk.ts:21); SSE via FastAPI StreamingResponse would stream stages and SQL - verify Databricks Apps proxy buffering before committing.
- HealthProvider publishes a new object every 8 s even when nothing changed (HealthProvider.tsx:240-242), re-rendering every GenieAnswer via useWorkspaceHost (GenieAnswer.tsx:78); structural sharing via useQuery fixes it.
- frontend/public/us-counties.json (770 KB) is never fetched by the SPA (grep of frontend/src; only backend consumers) - relocate to backend resources; buildCountiesPayload (USChoroplethMap.utils.ts:243) is test-only dead code.

**Limitations:** Static audit only: no build, no running app, no profiler, so there are no measured INP, frame-time or heap numbers; the machine is shared with many sessions so I reported operation and call counts instead of timings. The React Compiler census was produced by running the installed babel-plugin-react-compiler 1.0.0 with the same default options vite.config.ts passes (scratch scripts rc_probe.cjs / rc_emit.cjs in the scratchpad), not by inspecting the Rolldown build output (frontend/dist is denied). Scroll-position behaviour after navigation (runtime-08) and the wrong-rows window (runtime-02) are derived from code paths, not reproduced in a browser. The screenshot manifest did not exist when checked, so no visual review was done. I did not verify which aggregate endpoints write VIEW_* audit rows (relevant to any data-prefetch allowlist) or whether the Databricks Apps proxy buffers SSE. Web-verified claims: React 19.3 ViewTransition stable (react.dev, 2026-09-09), compiler try/finally limitation (facebook/react#34131), web-vitals 6.2.2 and compiler 1.0.0 latest (npm view), same-document View Transitions and content-visibility Baseline status, LoAF Chromium-only.

---

## Design-system architecture and modern CSS (`css`) — grade B-

**Auditor:** Disciplined, prototype-faithful, fully tokenized CSS with flat specificity and genuine 2023-era modern features (color-mix in oklab, named container queries, :has()), but it stops there: no cascade layers, top layer, color-scheme, forced-colors, view transitions or derived/typed tokens. It also ships verifiable defects (illegible accent x theme combinations, dead overlay exit animations, undefined custom properties) that a Linear/Stripe-grade system would catch in CI.

**Verifier on the grade:** Fair, at most marginally harsh. Every cited defect reproduced (and I found one more: the default-accent light-theme focus ring at 1.91:1), but the CSS is fully tokenized with zero literals in components, a literal-lint gate, container queries, reduced-motion coverage and theme-aware axe runs, and half the listed gaps are low-impact polish at 1440x900, so B-/B is the honest range. Note the cited line numbers are for this branch's monolithic components.css; on origin/main the same lines live in the 14 partials (e.g. 04-approval-and-drawer.css:485, 06-genie-chat.css:25).

**Strengths (keep these)**

- **Prototype vocabulary parity is essentially complete** — Scripted diff of design_files/index.html, Design System.html and Module 0 Prototype.html against tokens.css + components.css: 82/82 prototype custom properties exist in the app; the only prototype classes absent are spec-page classes (swatch, ds, layout-diag) and layoutB/C variants. Only 5 first-definition value drifts, and the two colour ones carry in-file WCAG rationale (frontend/src/design-system/tokens.css:171-176, :211-214).
- **Flat, low-conflict cascade with enforced colour tokenization** — 1,475 selectors: 78% at specificity (0,1,0), 96% at or below (0,2,0), zero id selectors; the only 4 !important sit inside the global reduced-motion reset (frontend/src/design-system/components.css:49-52). tools/lint_css_literals.mjs runs in npm run lint (frontend/package.json:17): 0 hex/rgba outside tokens.css and 0 colour literals in production TS/TSX (scripted scan).
- **Very little dead CSS for a 208 KB hand-written system** — Scripted class inventory: 47 of 981 classes (4.8%) are not referenced by any production .ts/.tsx (40 more matched dynamic-prefix composition and were excluded); 10 of 124 tokens unused. 327 BEM blocks, only 3 non-BEM-shaped names (layoutA-grid is a prototype name).
- **Several modern CSS capabilities are already used well** — 62 color-mix(in oklab) sites; a named inline-size container on .main (components.css:419-420) with a 6-band @container partition (:4863-4922) so layout follows Console open/closed rather than viewport; :has() at :3876, :4851; accent-color and scrollbar-color (tokens.css:341, :352); clamp() type ramp (tokens.css:74-76); 20 prefers-reduced-motion blocks; tabular-nums on .kpi__value, .tbl td.num, .score.
- **Print stylesheet proves the semantic token tier works** — frontend/src/design-system/print.css:10-37 remaps the whole --bg/--line/--text/--accent/--shadow set to system colours Canvas/CanvasText inside @media print, so every component prints correctly without per-component rules. The same pattern is a ready template for a forced-colors block (css-06).


### `css-10` Motion ceiling: route change is a remount fade; no shared-element transitions, anchored popovers or typed properties

`high` · `wow` · effort `L` · corrected

- **Evidence:** frontend/src/app.tsx:68 remounts a keyed div whose only animation is route-in, a 200ms fade (frontend/src/design-system/components.css:5012-5017). grep view-transition|startViewTransition|ViewTransition|anchor-name|@property|animation-timeline over frontend/src: 0 hits. frontend/src/components/EvidenceHoverCard.tsx:58,90 positions via getBoundingClientRect and hides on scroll. package.json pins react 19.2.8.
- **Problem:** The product story is a drill path, portfolio to segment to lead to borrower to evidence, yet every hop is a hard cut. Best-in-class tools morph the clicked object into its destination so users never lose context.
- **Recommendation:** As written, minus @property KPI count-up: CSS counters cannot format $ or thousands separators and are unreliably exposed to assistive tech; KpiCard already has an rAF count-up. Morph targets must render synchronously from route params (masked borrower ID chip), since the 360 header is a skeleton until live UC data arrives. Replace the key={pathname} remount wrapper, keep transitions under 250ms because the snapshot freezes the streaming Genie panel, and gate on reduced motion.
- **Tech:** React 19.3 ViewTransition stable 2026-09-09 (react.dev verified); same-document view transitions Baseline 2025-10; anchor positioning Baseline 2026-01 (MDN verified); @property Baseline 2024-07.
- **Verifier:** Verified: app.tsx:68 keyed fade; zero view-transition/anchor/@property hits; React 19.3.0 is npm latest and react.dev's 2026-09-09 post makes ViewTransition stable; react-router 8.3.0 BrowserRouter wraps navigation in startTransition (lib.js:164), so it activates; anchor positioning Baseline since Firefox 147.

### `css-01` Accent x theme matrix ships illegible combinations (chip text 1.15:1, navy ink and focus ring 1.85:1)

`medium` · `defect` · effort `S` · corrected

- **Evidence:** frontend/src/design-system/tokens.css:246-253 red/teal accents set --chip-text #FFB4A8/#BEF4F6, overriding [data-theme=light] at equal specificity; :272-275 fixes --accent-ink for light only. Computed: light+teal chips 1.15:1, light+red 1.48:1, dark+navy ink/focus ring 1.85:1. frontend/src/components/layout/Console.tsx:169 exposes all four; no e2e sets data-accent.
- **Problem:** Two of four user-selectable accents make the mandated evidence chips unreadable in light theme; navy on dark dims 39 accent-ink text sites and the global focus ring. The prior cross-browser audit verified accents at token level only.
- **Recommendation:** Land explicit overrides now: [data-theme=light][data-accent=red|teal] --chip-text/--chip-bg/--chip-line, and a dark+navy --accent-ink. Add a vitest token-contrast test (pure luminance math over tokens.css) for chip, ink and ring pairs across all 8 combos, plus the 8-combo axe loop in accessibility.spec.ts. Defer OKLCH/relative-color derivation to css-07; light-dark() only works after css-02 adds color-scheme.
- **Tech:** light-dark() (Baseline 2024-05, widely available 2026-11, verified), relative color syntax and oklch() (Baseline 2024 / 2023), color-scheme. Token names unchanged, so prototype vocabulary holds.
- **Verifier:** Reproduced: tokens.css:246-253 accent blocks override [data-theme=light] chip-text at equal specificity; my luminance script gives 1.15, 1.48 and 1.85:1. No e2e sets mip.accent. But the combos are non-default and Console is opt-in; the fix is inherited-prototype values, not architecture.
- **Constraint conflict:** Defect is inherited verbatim from design_files/index.html:164-178, so new values depart from the prototype; CLAUDE.md permits accessibility departures with a commit note linking the prototype line.

### `css-02` No color-scheme, no pre-paint theme bootstrap, no system-preference option

`medium` · `defect` · effort `S` · corrected

- **Evidence:** frontend/src/components/AppContext.tsx:164 reads mip.theme; :222-224 sets data-theme inside useEffect after mount. frontend/index.html:6 static theme-color, no head script. grep color-scheme|prefers-color-scheme over frontend/src and index.html: 0 hits. tokens.css:317-319 transitions body background; 19 native select/textarea/number controls exist.
- **Problem:** Light-theme users get a dark first paint that animates to light on every hard load; native selects, spinners, autofill and textarea scrollbars use the light UA scheme inside the dark app; the OS preference is ignored.
- **Recommendation:** As written, scoped: ship public/theme-boot.js (served by backend/main.py _spa_fallback, satisfies script-src 'self') mirroring readStored validation, declare color-scheme per [data-theme] plus the meta, update theme-color on switch. Treat the System option as a separate S slice (Theme type, Console, tests).
- **Tech:** color-scheme property and meta (Baseline widely available), prefers-color-scheme, matchMedia. No dependency; one static file picked up by the existing Vite public dir.
- **Verifier:** Reproduced: AppContext.tsx:222-225 sets data-theme in useEffect; :root defaults dark; index.html has no head script; zero color-scheme hits; 19 native controls. Impact inflated: default dark is unaffected, scrollbars are already themed via scrollbar-color, selects are custom-styled.

### `css-03` Drawer and Genie exit animations are dead (visibility flips instantly); top-layer migration is a separate, larger upgrade

`medium` · `defect` · effort `L` · corrected

- **Evidence:** frontend/src/design-system/components.css:2049 transitions only transform while :2054 flips visibility:hidden instantly; .genie does the same (:2516-2519). design_files/index.html:665-676 has no visibility rule, so the prototype animates out. frontend/src/components/command/CommandPalette.tsx:197 returns null. Six role=dialog components use useFocusTrap, createPortal and z-index 40/41/1000.
- **Problem:** Panels slide in over 360ms but vanish in 0ms, a regression from the prototype's motion contract. Stacking, scrim, background inertness and focus return are re-implemented per component instead of using the platform top layer.
- **Recommendation:** Slice 1 (S): add visibility var(--dur-slow) to both transition lists AND retain the last DrawerSource during exit, because EvidenceDrawer renders {d && ...} and setDrawer(null) empties the panel before it slides. Slice 2 (L, optional): showModal()/popover. Body-portaled overlays (EvidenceHoverCard, map tooltip) render beneath the top layer and go inert, so re-parent them or make them popovers first. Dialog/backdrop exit animation relies on the overlay property (Chromium only); treat as progressive enhancement. Keep GenieChat non-modal.
- **Tech:** dialog (widely available), popover (Baseline Newly available 2025-01-27, verified), @starting-style and allow-discrete (Baseline 2024-08, verified).
- **Verifier:** Reproduced: components.css:2054 and :2519 flip visibility with no visibility transition; prototype index.html:665-676 has no such rule. But useFocusTrap is one shared 90-line hook, not per-component reimplementation, and the dialog migration touches five components (~2,100 lines) with six test files.

### `css-05` No cascade layers; all component CSS is global, order-dependent and render-blocking for every route

`medium` · `upgrade` · effort `L` · corrected

- **Evidence:** frontend/src/main.tsx:7-9 imports all CSS eagerly. This branch: components.css 6,630 lines, 1,323 rules, 981 classes; origin/main splits it into 14 numbered @import partials (pure move, git show verified). Import-graph script: 64% of rule bytes referenced only by lazy routes. tools/check_frontend_budgets.mjs:81: CSS budget 148 KiB, was 101.
- **Problem:** The cascade contract is the numeric @import order, so any route-level split or reorder can silently change winners. Every visitor parses 1,475 selectors for eleven routes before first paint, and the budget gate follows growth instead of constraining it.
- **Recommendation:** Step 1: add the @layer order and wrap each partial as a pure move, proven with tools/refactor_proof.py css; audit the 4 !important declarations (layer order inverts for them) and keep analytics.scatter.css layered too. Step 2: move only measurably route-only partials (10-native-analytics, 14-genie-markdown-and-proof, glossary) into lazy route imports and ratchet the budget down. Skip nesting and renames: components.test.ts has ~110 raw-text assertions and lint scripts read CSS text.
- **Tech:** @layer (Baseline widely available since 2024-09), CSS nesting (Baseline 2023-12, widely available 2026-06), Vite 8 cssCodeSplit default. No new dependency.
- **Verifier:** Reproduced main.tsx:7-9, zero @layer, budget 101 to 148 KiB. But initial CSS is 25 KiB gzip; all routes are lazy and Home needs chips, KPI, segments, table and map, and partials are organised by component, so a 60% first-load cut is unproven.

### `css-06` No forced-colors or prefers-contrast support; focus ring is five recipes, not a system

`medium` · `gap` · effort `S` · confirmed

- **Evidence:** grep forced-colors|prefers-contrast|prefers-reduced-transparency over all four CSS files: 0 hits. Focus outlines: frontend/src/design-system/tokens.css:345-349 global; components.css:104, :208, :365 (--entrada-bright), :1749 (--accent-ink) differ. :2100, :2695, :4069, :5926, :6031 set outline:none and swap in a 1px border-color change. Six backdrop-filter sites.
- **Problem:** In Windows High Contrast, border colours are forced, so focused form inputs show no indicator at all; low-vision users get no contrast boost. Ring colour, width and offset vary by component, so focus never feels like one product.
- **Recommendation:** Add --focus-ring-color, --focus-ring-width and --focus-ring-offset tokens consumed by one :focus-visible rule in the base layer; inputs keep outline instead of border swaps. Add a forced-colors: active block reusing print.css's Canvas/CanvasText remap with Highlight rings, and prefers-contrast: more raising --line-1..3 and --text-3. Drop blur under prefers-reduced-transparency.
- **Tech:** forced-colors and prefers-contrast (Baseline widely available), CSS system colors, prefers-reduced-transparency (Chromium and Firefox; harmless elsewhere).
- **Verifier:** Zero hits for forced-colors, prefers-contrast, prefers-reduced-transparency in frontend/src. Opened every cited line: four differing ring recipes plus five outline:none inputs that swap a 1px border-color, which forced-colors overrides. print.css:13-21 already has the Canvas/CanvasText remap. Playwright forcedColors emulation can gate it.

### `css-v1` Default light-theme focus ring is 1.91:1: global :focus-visible uses --accent, which data-accent always overrides

`medium` · `defect` · effort `S` · verifier-added

- **Evidence:** frontend/src/design-system/tokens.css:345-349 outlines with var(--accent). AppContext.tsx:225 always sets data-accent (default bright), so tokens.css:254 (#66C5FF) beats the light theme's navy --accent at :228. Computed: 1.91:1 on #FFF, 1.78:1 on --bg-0; teal 1.57:1.
- **Problem:** Every focusable control in the light theme with the default accent fails WCAG 1.4.11's 3:1. axe does not test focus indicators, so the light-theme a11y spec stays green. The light theme's own --accent is dead code.
- **Recommendation:** Add --focus-ring-color: var(--accent-ink) (already AA-tuned per light accent; add the dark+navy value) and consume it in the global :focus-visible and the bespoke rings at components.css:104, :208, :365. Add a vitest token-contrast test asserting ring vs --bg-0/1/2 is at least 3:1 for all 8 theme x accent pairs.
- **Tech:** Plain custom properties plus WCAG relative-luminance math in the existing vitest suite. No dependency.

### `css-04` Undefined custom properties silently void live declarations; no CSS linter exists to catch it

`low` · `defect` · effort `S` · corrected

- **Evidence:** frontend/src/design-system/components.css:204, :1743, :1798 use var(--ease-standard); :3965 var(--stroke); :4253 var(--shadow-1); :3790 var(--surface-2, ...). None is defined in any CSS or TSX (scripted define/use diff). :2867, :3374, :3401 use font-weight 650 and :1886 800, but tokens.css:6-12 loads 400-700 only. No stylelint in package.json.
- **Problem:** The Cmd-K badge and login-summary transitions, the ZIP reconcile divider (USChoroplethMap.tsx:816) and the analytics refresh pill shadow compute to nothing. lint_css_literals.mjs only matches hex/rgba; components.test.ts is regex on text. Same lines persist in origin/main's partials.
- **Recommendation:** Swap to --ease, --line-1, --shadow-sm now. Add stylelint 17 with only csstools/value-no-unknown-custom-properties (importFrom tokens.css), allowlisting TSX-set properties (--seg-color, --tick-pos, --dot-x/y, --hover-x/y, --bin-*, --chip-hue, --tile-i). Defer selector-class-pattern and declaration-property-value-no-unknown: ~980 classes include non-BEM utilities (.h-1, .num, .is-open) and would flood the first run.
- **Tech:** stylelint 17.15.0 (npm view verified) plus stylelint-value-no-unknown-custom-properties; dev-only, no runtime or deploy change.
- **Verifier:** Reproduced with my own define/use script: only --ease-standard, --stroke, --shadow-1, --surface-2 are undefined; same lines exist in origin/main partials. But --surface-2 has a fallback, 650/800 resolve to 700 (not voided), and visible damage is three transitions, one border, one shadow.

### `css-07` Token architecture is two-tier with hand-copied derived colours and three unsynchronised sources of truth

`low` · `upgrade` · effort `M` · corrected

- **Evidence:** frontend/src/design-system/tokens.css:125-140 hard-codes 16 status rgba tokens from the dark signal RGB; :223-226 light theme changes --signal-* but not those, forcing 20 [data-theme=light] component patches (components.css:610-626, :1165, :3540). 62 color-mix sites use 30 distinct percentages. Segment hexes are duplicated in sql/transformations/gold_segment_population.sql:235; no parity test.
- **Problem:** Derived tints do not follow their base colour, so every new status or accent needs per-theme patches in component CSS. The real palette is unbounded, and the prototype, tokens.css and SQL registry can drift unnoticed.
- **Recommendation:** Keep tokens.css hand-authored. Derive status soft/line from --signal-* with color-mix(in oklab) (already used 60 times, widely available), collapse alphas to a five-step scale, and add one parity test asserting segment hexes match across tokens.css, the SQL registry and design_files/index.html. Skip Style Dictionary/DTCG; P3 behind color-gamut is optional polish. Re-baseline demo_visual snapshots for light theme.
- **Tech:** Relative color syntax (Baseline 2024), oklch (Baseline 2023), color-gamut media query, DTCG Format 2025.10 (first stable, verified), Style Dictionary 5.5.5 (2025.10 support partial).
- **Verifier:** Evidence reproduces: 16 hard-coded status rgba tokens (tokens.css:125-140), 20 light patches, 62 color-mix sites with 31 percentages, segment hexes duplicated in gold_segment_population.sql:235 with no parity test. No user-visible defect though, and a DTCG/Style Dictionary pipeline is over-engineering for 361 lines.
- **Constraint conflict:** A generated tokens.css adds an authoring source above design_files (the design contract) and breaks the lint_css_literals allowlist and text assertions that read tokens.css directly.

### `css-08` Elevation is unsystematic: 17 magic z-indexes, 25 shadow recipes, overlays darker than the cards beneath them

`low` · `upgrade` · effort `S` · corrected

- **Evidence:** frontend/src/design-system/components.css z-index: -1,0,1,2,3,5,10,20,29,30,40,41,50,60,100,900, and 1000 three times (:680, :1864, :2064); :2059 comment claims Genie FAB z 900 but :2713 sets 29. 25 box-shadow recipes versus 3 tokens. .drawer :2046, .genie :2504, .cmdk__panel :2079 use --bg-1, darker than .surface --bg-2 (:463). Light: --bg-1 equals --bg-2.
- **Problem:** Stacking is decided by tie-breaks and DOM order. In dark mode the highest layers are the darkest surfaces, inverting the lighter-is-closer convention used by Linear, Apple and Material, so drawers and the palette read as holes rather than raised panels.
- **Recommendation:** Add the --z-* scale, map existing values onto it and fix the stale comment (S, pure refactor). Consolidate shadows opportunistically. Leave overlay surface lightness alone unless the owner explicitly chooses to depart from the prototype.
- **Tech:** Plain custom properties; color-mix(in oklab) for the overlay step; top-layer adoption in css-03 removes most z-index needs.
- **Verifier:** Reproduced: z-index 1000 on .evidence-hovercard, .offer-mock-scrim and .cmdk; :2059 comment says FAB z 900 but :2713 is 29 (900 is .map-tip); 28 distinct shadow declarations vs 3 tokens. No collision demonstrated, and drawer --bg-1 matches prototype index.html:668.
- **Constraint conflict:** The --surface-overlay step departs from the prototype's drawer/genie/cmdk --bg-1 colour; owner decision, as the auditor noted.

### `css-09` Scroll and layout stability primitives unused; layout-animating transitions on the container-query root

`low` · `gap` · effort `S` · corrected

- **Evidence:** Scripted grep: scrollbar-gutter 0, overscroll-behavior 0, content-visibility 0, text-wrap 0 across all CSS. frontend/src/design-system/components.css:414 transitions padding-right on .main, which is the named container (:419-420) and gains 324px at :442. transition: all at :317, :381, :510, :646, :775, :898, :3506. Segment-card snapshot shows ragged two-line titles.
- **Problem:** Classic-scrollbar Windows users see content shift when skeletons resolve; wheel scrolling chains from drawer, Genie and palette into the page. Toggling the Console reflows the whole page per frame and crosses the 1280px container breakpoint mid-animation.
- **Recommendation:** Keep scrollbar-gutter, overscroll-behavior: contain, text-wrap: balance/pretty and explicit transition lists. Drop content-visibility unless a profile shows audit/glossary/Genie lists costing render time; contain-intrinsic-size guesses cause scrollbar jumps and LeadTable already virtualizes.
- **Tech:** scrollbar-gutter (Baseline 2024), overscroll-behavior (2022), content-visibility (2024), text-wrap: balance (2024); pretty is Chrome and Safari 26 only (verified), harmless in Firefox.
- **Verifier:** Zero hits for all four properties; .main transitions padding-right on the named container (components.css:414-420, :442); nine transition: all sites (prototype has 11, so inherited). But CLS is already gated by layout-stability.spec.ts, LeadTable is virtualized, and Console toggling is a rare presenter action.

### `css-v2` Seven static Geist files with no preload or metric-matched fallback; 650/800 weights silently snap to 700

`low` · `upgrade` · effort `S` · verifier-added

- **Evidence:** frontend/src/design-system/tokens.css:6-12 imports seven @fontsource static weights (font-display: swap); frontend/index.html and vite.config.ts contain no font preload; tokens.css:53 falls back to system-ui with no size-adjust. components.css:2867, :3374, :3401 request 650, :1886 requests 800.
- **Problem:** Fonts are discovered only after React mounts, so cold loads paint system-ui then swap to Geist with a visible reflow. Intermediate weights the CSS asks for cannot render with static files.
- **Recommendation:** Switch to @fontsource-variable/geist and geist-mono 5.3.0 (npm view verified): two files, any weight. Preload both woff2 with link rel=preload as=font crossorigin, add a fallback @font-face with size-adjust/ascent-override, and update the exact fontAssetCount budget in tools/check_frontend_budgets.mjs.
- **Tech:** Variable fonts, font metric overrides (size-adjust, ascent-override; widely available), link rel=preload. Still Geist + Geist Mono per the design contract.

**Overflow (titles only, not verified)**

- Fonts: tokens.css:6-12 imports 7 static @fontsource files -> 14 emitted font files (woff2 + legacy woff); weights 650/800 used in CSS cannot render. Adopt @fontsource-variable/geist + geist-mono 5.3.0 (npm view verified; wght axis, one woff2 each), drop woff, add a size-adjust fallback @font-face and preload. Geist has no opsz axis, so font-optical-sizing is not applicable.
- Hand-minified block at components.css:2402-2424 (proof drawer + glossary; 10 lines of 255-525 chars, 3.5 KB) inside a hand-written file: unreviewable diffs, likely written to dodge the line-count gate. Expand it (part of css-05).
- Type scale leaks: font-size literals 10px x18, 11px x10, plus 9/12/13/14/15/16px bypass --fs-*; there is no --fs-10 token; all sizes are px so the browser default-font-size preference is ignored (move tokens to rem, keep names).
- Responsive drift: 12 distinct @media breakpoints in mixed units (88rem, 1279, 1180, 1023, 980, 900, 720, 48rem, 640, 1920/2000/2560px). Only one container (main) exists; make the user-resizable Genie panel, the drawer and .surface their own containers so components respond to their own width.
- components.test.ts is 27 regex-on-source-text assertions: brittle under nesting/@layer refactors and blind to computed results. Move contracts to Playwright computed-style checks and grow the 3-image visual snapshot set (demo_visual.spec.ts-snapshots, dated May 21).
- .btn has hover, disabled and focus states but no :active pressed state (grep ':active' = 1 hit, the Genie resize handle); no @media (hover: hover) guard on the 56 :hover rules for touch devices.
- tokens.css:345-349 global :focus-visible also sets border-radius, mutating element shape on focus; outlines already follow the element radius natively, so drop it.
- .app-shell uses height:100vh / width:100vw (components.css:24-25): switch to 100dvh and 100% to avoid mobile URL-bar jump and classic-scrollbar horizontal overflow.
- Dead weight: 47 unreferenced classes (genie-proof x7, freshness-legend x6, seg-card x4, portfolio-summary x4, dependency-dot x3) and 10 unused tokens (--entrada-navy-700/800/900, --entrada-light, --entrada-grey, --signal-info, --fs-48, --dur-ambient-1/2, --dur-mesh).
- Subgrid (Baseline widely available 2026-03) for the KPI row and segment-card grid so title / description / metric rows align across cards when one title wraps (visible in segment-card-grid-chromium.png).
- Logical vs physical properties are mixed (246 logical vs 69 physical declarations); finish opportunistically only, since there is no RTL requirement.
- Feature matrix - ALREADY USED: container size queries (12 queries, one named container), :has() (5), color-mix in oklab (62), tabular-nums (12 sites incl. .kpi__value, .tbl td.num, .score), accent-color, scrollbar-color, clamp(), prefers-reduced-motion (20 blocks + global reset).
- Feature matrix - ADOPT: @layer, native nesting, light-dark() + color-scheme, relative color / oklch / display-p3, @property, @starting-style + allow-discrete, popover + dialog, anchor positioning (with @supports fallback), view-transition-name, subgrid, text-wrap, scrollbar-gutter, overscroll-behavior, content-visibility, forced-colors, prefers-contrast, variable font wght axis.
- Feature matrix - PROGRESSIVE ENHANCEMENT ONLY (not Baseline, verified Sept 2026): interpolate-size / calc-size() (Chromium only) for .tbl__expand row open/close; scroll-driven animations (no Firefox) for sticky-header and topbar shadow; field-sizing: content for the Genie composer and decision-note textareas; text-wrap: pretty.
- Feature matrix - NOT WORTH IT: @scope (flat BEM already yields 78% of selectors at 0,1,0); container style queries (root data-attributes already cover theme/density/accent); font-optical-sizing (no opsz axis in Geist).

**Limitations:** No rendered screenshots were available: the ui-shots directory held only harness scripts and no manifest.json or PNGs during my whole window, so visual judgements rest on CSS source, design_files/module_0_prototype_1.png and one committed (May 21, masked) segment-card snapshot; contrast ratios are computed from token values, not sampled pixels. No build was run, so CSS byte sizes come from comments in tools/check_frontend_budgets.mjs; the 64% route-only estimate is a static import-graph approximation over whitespace-collapsed rule bytes, and dead-class detection is literal-string based (40 dynamically composed classes heuristically excluded). Important context: the checked-out branch claude/genie-guard-literals is 25 commits behind origin/main, where components.css has already been split into 14 numbered @import partials (2026-09-08); I audited the working tree but verified with read-only git show that every cited defect (undefined tokens, no @layer/color-scheme/forced-colors, visibility rule) persists in main's partials, so line numbers need remapping there. Baseline status was web-verified for light-dark(), popover, anchor positioning, interpolate-size/calc-size, view transitions, @scope, scroll-driven animations, text-wrap: pretty, @starting-style, DTCG 2025.10 and React 19.3; remaining Baseline dates come from model knowledge (cutoff June 2026). One web-features-explorer fetch for anchor positioning returned data contradicting MDN and search results; I relied on MDN's 'Baseline 2026 Newly available' banner and still recommend an @supports fallback.

---

## Visual design quality and prototype parity (`visual`) — grade B-

**Auditor:** The foundation is strong: tokenized CSS with zero literal colours, the prototype's BEM vocabulary kept, a best-in-class command palette and evidence drawer, and a credible light theme. The main work surfaces fail at the 1440x900 design viewport, though. Score and Approve sit off-screen in the Lead Queue, the Console's controls are clipped, native inputs and checkboxes render white in the dark theme, and the segment cards are ragged. A single bordered mono chip style used for almost everything flattens hierarchy well below Linear or Stripe.

**Verifier on the grade:** B- is fair, arguably a touch generous: every CSS-level defect (clipped Console, no color-scheme, underlined mono nav, a 1,564px table in a 1,318px wrap) reproduces from source on first-class surfaces, while the foundation (zero literal colours confirmed, prototype BEM, self-hosted Geist) is genuinely strong. Caveats: the screenshots came from a fixture harness, so data-dependent observations (null-state chips, '—' ZIP tiles) need reconfirming on live data, the auditor misread the prototype card height (about 195px, not 125px), and visual-01 was inflated to critical although Approve stays reachable via the expanded row and hotkeys.

**Strengths (keep these)**

- **Token discipline and prototype vocabulary preserved** — frontend/src/design-system/components.css (6,630 lines) has 0 hex and 0 rgba literals and 248 tokenized font-size declarations (grep counts). Prototype BEM names (.surface__hdr, .kpi__value, .seg-card__count, .tbl__expand, .tweaks) are intact. Prototype deviations carry a written rationale, e.g. the --text-3 AA note at frontend/src/design-system/tokens.css:170-176.
- **Command palette and evidence drawer are best-in-class** — dark__state__command-palette-results.png shows a blurred scrim, grouped borrower results, mono IDs with right-aligned locations and kbd hints. dark__state__evidence-drawer-from-kpi.png shows Overview/Lineage tabs, freshness chips, a governed-asset path and a clean stat grid. Both read at Linear/Vercel quality.
- **Light theme is first-class in composition** — light__borrower-360-detail__viewport.png and light__segment-intelligence__viewport.png are clean and balanced, arguably calmer than dark. frontend/src/design-system/tokens.css:214-272 sets per-theme signal colours and an --accent-ink token so active text clears AA. Problems are limited to accent-coloured chart marks and active borders.
- **Borrower 360 expresses the evidence promise clearly** — dark__borrower-360-detail__viewport.png: 'The story' narrative with verified claim chips, a Refi economics check with its pass chip, thresholds and fn_* evidence, and a Primary offer with score pill. Hierarchy is unmistakable and mortgage-specific.
- **Container-query responsive system and honest degraded banner** — frontend/src/design-system/components.css:4886-4908 defines @container main bands, so layout adapts when the Console opens rather than only to the viewport. dark__state__degraded-warehouse-down__home.png shows a clear amber 'Reconnecting to analytics warehouse' banner and a Degraded status pill, with no fake data.


### `visual-01` Lead Queue at 1440x900: Score, Signal and Approve are off-screen; only three rows fit

`high` · `defect` · effort `L` · corrected

- **Evidence:** DOM probe at 1440x900: .tbl-wrap clientWidth 1318 vs scrollWidth 1752; Score header at x=1533-1607, Approval 1685-1849; rows 68px; 3 rows fit; table starts y=589. frontend/src/components/mortgage/LeadTable.tsx:1005-1019 column order; frontend/src/design-system/components.css:1311-1323 widths, no sticky-right rule. Compact-density screenshot: rows unchanged.
- **Problem:** At the design viewport the queue hides score, signal, offer and Approve behind horizontal scroll, while four workflow columns show 'Other/Unassigned/None/Untouched' chips. Sixteen filter chips and a three-line hero push the table to y=589.
- **Recommendation:** Keep the prototype's column order (Borrower, Location, Segments, Equity, Rate, Offer, Score, Signal, Approval). Merge the four app-added workflow columns (Relationship, Assigned to, Outreach, Last touch = 444px) into one ~130px Status cell showing an em dash when empty, keeping their sort keys in a header menu; that alone brings the table to about 1,250px, inside the 1,318px wrap. Pin Approval with position:sticky; inset-inline-end:0 as the safety net for Console-open widths. Move Log into row-hover actions so --row-h (44/36px) holds. Ship the '+N filters' collapse as a separate slice. Skip @tanstack/react-table (9.2.4 verified current): retrofitting it into a virtualized 1,261-line table is a rewrite that CSS makes unnecessary.
- **Tech:** CSS sticky columns plus existing @container main bands; optional headless @tanstack/react-table 9.2.4 (npm-verified) for pinning/visibility. Matches the prototype, whose table fits 1440px.
- **Verifier:** CSS reproduces it: 15 columns sum 1,564px against a 1,318px wrap, no sticky-right rule; screenshot agrees. Expanded-row preview and A/R hotkeys still reach Approve, so high, not critical. LeadTable is 1,261 lines with 49 tests and three sortable workflow columns: L.
- **Constraint conflict:** The proposed reorder (Offer, Score ahead of Equity, Rate) departs from the prototype column order at design_files/Module 0 Prototype.html:2013-2023 and is unnecessary once the app-added workflow columns are merged.

### `visual-02` Console rail content overflows its 300px panel: Light and Compact sit behind an inner horizontal scroll

`high` · `defect` · effort `S` · corrected

- **Evidence:** Probe, Console open at 1440: .tweaks is 300px; .tweaks__body clientWidth 298 vs scrollWidth 464; 'Light' button spans x=1357-1570. frontend/src/design-system/components.css:4051-4054 grid lacks minmax(0,1fr); :4055 `.tweak-row label` leaks into frontend/src/components/mortgage/PropertyLookupPanel.tsx:126 labels. Visible in dark and light console-open screenshots.
- **Problem:** A first-class shell element renders half its controls outside the panel: Light theme and Compact density are unreachable and Property lookup text is cut mid-word. With the Console open the KPI row falls to 3+1 (components.css:4895-4899).
- **Recommendation:** Set .tweaks__body{grid-template-columns:minmax(0,1fr)} and change `.tweak-row label` to `.tweak-row > label`. Because the track is then constrained to ~266px, also let PropertyLookupPanel's compact .surface__hdr wrap (or drop its nowrap 'Every lookup audited' chip when `compact`), otherwise the header overflows instead. Make the 961-1280px container band 2x2 (or 4-up) for four KPIs. Add a scrollWidth<=clientWidth assertion for .tweaks__body to frontend/tests/e2e/console-layout.spec.ts, which today only checks vertical scroll, and run that spec in PR CI.
- **Tech:** Plain CSS grid and child combinator; no dependency. Console stays the prototype's .tweaks panel.
- **Verifier:** components.css:4051-4055 and Console.tsx:161-196 confirm the unconstrained auto track and leaking `.tweak-row label`; screenshot shows Light clipped. But .tweaks__body is overflow:auto and the topbar has a theme toggle, so 'unreachable' is overstated. KPI 3+1 band confirmed at :4895.

### `visual-03` No color-scheme declared: native checkboxes, inputs and selects render light inside the dark theme

`high` · `defect` · effort `S` · confirmed

- **Evidence:** `grep -rn color-scheme frontend/src frontend/index.html`: 0 hits; probe computed colorScheme 'normal'. frontend/src/design-system/tokens.css:338-342 sets 24px checkboxes with accent-color only. frontend/src/routes/offer-orchestrator.panels.tsx:477-486 renders a bare <input> in .field; components.css:5524 styles only .field__label. Seen in dark Lead Queue and Offer screenshots.
- **Problem:** Dark theme shows white 24px checkboxes down the queue and a white, truncated Subject field on the approval screen; native select popups, time pickers and scrollbars stay light. The money screens read as unfinished.
- **Recommendation:** In tokens.css add `:root,[data-theme=dark]{color-scheme:dark}` and `[data-theme=light]{color-scheme:light}`, plus <meta name=color-scheme> in index.html. Give Subject the existing .form-input class at full width. Restyle checkboxes with appearance:none, a 16px tokenized box and 24px hit area. Later migrate paired tokens to light-dark().
- **Tech:** color-scheme: Baseline widely available. light-dark(): newly available May 2024, widely available Nov 2026 (web-verified); keep [data-theme] overrides until then.
- **Verifier:** Zero color-scheme hits in frontend/src, index.html and design_files; tokens.css:338-342 sets accent-color only; offer-orchestrator.panels.tsx:478 is a bare input and the crop shows it white. light-dark() dates verified. Native checkboxes darken with color-scheme alone, so the appearance:none restyle is optional.

### `visual-04` Segment cards are about 2.2x the prototype height (432 vs ~195px) because of app-added facet chips

`high` · `defect` · effort `M` · corrected

- **Evidence:** Probe: six .seg-card at 210x432px; count tops 317 vs 329. Screenshot: PRODUCT rows at y=502 vs 534. frontend/src/design-system/components.css:885-889 flex column; :4288-4292 .seg-grid. frontend/src/components/mortgage/SegmentCard.tsx:189-225 evidence plus facet chips. Prototype design_files/index.html:488-533 and module_0_prototype_2.png: about 125px cards.
- **Problem:** The route's hero row is ragged: two-line titles shift every row beneath, the evidence chip floats alone at right, and six facet chips per card wrap one per line, pushing filters and the table below the fold.
- **Recommendation:** Define rows on .seg-grid and set .seg-card to grid-template-rows:subgrid with grid-row:span 6 and row-gap:0 (header, count, reconcile, sub, meta, facets); always render the reconcile slot so row counts match; reserve two title lines. Collapse facets to one 4px stacked share bar per facet row where the row itself stays an EvidenceChip trigger, or show the chips only on the selected card while keeping one evidence trigger per row on every card. Note the count stagger exists in the prototype too, so aligning it is an improvement beyond the contract, not a parity fix.
- **Tech:** CSS subgrid (Baseline since 2023: Chrome 117, Safari 16, Firefox 71). Restores prototype proportions; facet bars extend the prototype.
- **Verifier:** Screenshot and SegmentCard.tsx:195-225 confirm 432px ragged cards. Measured module_0_prototype_2.png at native resolution: prototype cards are about 195px, not 125px, and its two-line title already staggers the count. Facet chips are EvidenceChips, so evidence access must survive. Subgrid Baseline verified.
- **Constraint conflict:** Facet chips are EvidenceChips (DRAWER_SOURCES.loanProductType / originationChannel). Replacing them with hover-only bars would remove evidence entry points, which CLAUDE.md forbids; keep one evidence trigger per facet row.

### `visual-05` One bordered Geist Mono chip style does five jobs: nav, tabs, filters, null statuses and evidence

`high` · `defect` · effort `M` · corrected

- **Evidence:** frontend/src/components/layout/RouteNav.tsx:85 builds primary nav from `.filter`; frontend/src/design-system/components.css:3500-3522 gives mono values and no text-decoration; tokens.css:336 is only `a{color:inherit}`, so labels render underlined. Analytics stacks three lookalike rows (y=84/253/302). Census: Geist Mono 1,256 vs Geist 1,060 text elements; 204-240 chips per page.
- **Problem:** Navigation, sub-tabs, filters, null statuses and evidence share one bordered mono style, so nothing signals hierarchy and the evidence chip, the product's trust signature, is diluted. Underlined nav labels read as unstyled links.
- **Recommendation:** Add .route-nav__link (Geist 13/500, text-decoration:none, borderless, 2px accent indicator) and render Analytics sub-tabs as underline tabs; show null workflow states (Other, Unassigned, None, Untouched) as muted text or an em dash. Leave the prototype-contract mono on .chip, segment chips and .filter__value untouched. Ship the text-decoration:none fix first as a one-line S change; the rest is M across RouteNav, analytics tabs and LeadTableRow tests.
- **Tech:** Plain CSS in components.css. The prototype has no route-nav (rail plus .segmented, design_files/index.html:926-935), so this aligns an app-added element to prototype vocabulary.
- **Verifier:** RouteNav.tsx:85 and components.css:3500-3522 confirmed; no anchor text-decoration reset exists (tokens.css:335) and every screenshot shows underlined mono nav. Probe census matches. But prototype .chip and .filter__value are Geist Mono by contract, so scope must stay on app-added elements.
- **Constraint conflict:** 'Reserve bordered mono for evidence chips, IDs and UC paths' would restyle prototype-contract .chip and .filter__value, which are Geist Mono in design_files/index.html. Limit the change to the app-added route nav, Analytics tabs and null-state cells.

### `visual-06` Home: KPI numbers smaller than the H1, 0px gap between cards, geography hero below the fold

`high` · `defect` · effort `M` · corrected

- **Evidence:** Probe: .login-summary bottom=396 and .kpi-row top=396 (0px; 18px elsewhere); map top=910 in a 900px viewport. KPI value 29.12px vs H1 30.08px; prototype 36 vs 28 (design_files/index.html:460-462, frontend/src/design-system/components.css:800-806). frontend/src/routes/home.tsx:329 plus frontend/src/components/layout/PageShell.tsx:61 double wordmark; frontend/src/lib/portfolioStory.ts:106 ' -- '.
- **Problem:** On the landing screen the numbers are smaller than the headline, two cards collide, the narrative runs a 1,270px measure, and the geography hero CLAUDE.md calls a hero surface is invisible until you scroll.
- **Recommendation:** Use --fs-36 KPI values in the 1281px+ band (the clamp is a documented deviation at components.css:802-805). Wrap the Home stack in one grid with gap:var(--gap-grid). Merge 'Since your last login' and 'Your book today' into one briefing band (max-inline-size:72ch, text-wrap:pretty as progressive enhancement since Firefox lacks it, em dash). Keep KPIs a full-width row and place the map in .layoutA-grid 1.4fr/1fr beside the approval queue and briefing, which is how the prototype pairs the map with a side panel. Treat removing the 44px brand-signature wordmark as a brand-owner decision.
- **Tech:** CSS grid; text-wrap balance (Baseline 2024) and pretty (Chrome 117+, Safari 26+, no Firefox, degrades silently; web-verified). Returns to prototype composition.
- **Verifier:** Verified: clamp gives 29.12px KPI vs 30.08px H1 at 1440 (tokens.css:74-75); no spacing between LastLoginSummary and .kpi-row (home.tsx:211-218); double wordmark; ' -- ' at portfolioStory.ts:106; map top about 910. Prototype pairs the map with a side panel, not KPIs.

### `visual-07` /ask-genie buries the question composer at y=1273 beneath a workflow builder

`high` · `gap` · effort `M` · corrected

- **Evidence:** Probe: 'Ask Genie — question' textarea top=1273px (fold 900); both textareas have empty placeholders. frontend/src/routes/ask-genie.tsx:520-535 titles the page 'Mortgage growth co-pilot' and leads with the Growth Agent; composer lives at frontend/src/routes/ask-genie.answer-panel.tsx:229-244. Orphan May baseline ask-genie-empty-desktop-chromium.png shows composer-first.
- **Problem:** The nav says Ask Genie, but the first fold is a five-button workflow builder. The chat box is blank, its button is ghost-styled, and answers render below the composer, the reverse of the floating panel.
- **Recommendation:** Make the route conversation-first using page-level tabs 'Ask | Workflows | Saved monitors' so the Growth Agent keeps its three-column layout (M); moving it into a narrow right-rail tab forces a redesign (L). Composer: add a placeholder, dock with position:sticky; bottom:0 leaving inline-end clearance for the fixed Genie launcher, suggestions above it and the thread above that to match the floating panel's order. Use field-sizing:content with a rows fallback. Budget for the seven ask-genie unit suites and the e2e specs that select on the current structure. Genie live-first logic and guards stay untouched.
- **Tech:** Layout only; textarea auto-grow via field-sizing:content (Baseline newly available June 2026, web-verified) with rows fallback. Genie live-first logic and guards untouched.
- **Verifier:** Full-page screenshot confirms the composer at y≈1273 under the Growth Agent; no placeholder in ask-genie.answer-panel.tsx. The Ask button is variant='primary' but disabled while empty, not ghost. field-sizing Baseline June 2026 verified (Firefox 152). Moving the 3-column Growth Agent into a rail tab is L.

### `visual-09` Geography hero stops being a map at ZIP level: a tile list over an empty canvas

`high` · `wow` · effort `L` · corrected

- **Evidence:** dark__state__map-zip-drill.png: Texas drill shows twelve '77002 —' tiles above roughly 300px of empty canvas. frontend/src/components/mortgage/USChoroplethMap.tsx:741-810 renders a .zip-tiles list; :50-59 documents the dropped county level. docs/design-review.md item 6 (MapLibre/GeoJSON) never landed. Legend reads only Lower/Higher (:983-984).
- **Problem:** The geography hero stops being a map exactly when the user drills in: no shape, adjacency or clustering, so 'where the opportunity lives' becomes ZIP codes floating in a void.
- **Recommendation:** Generate per-state ZCTA TopoJSON with a committed build script (Census ZCTA boundaries plus the ZCTA-to-state relationship file, mapshaper simplify), pre-projected per state into the map viewBox so runtime needs only topojson-client and the existing geometryToPath (USChoroplethMap.utils.ts:293), no d3-geo. Lazy-fetch on drill, reuse lvl-1..4 bins, tween the viewBox. ZIPs with no ZCTA (PO-box, new ZIPs) stay in the tile list, which remains the accessible view. Commit the outputs so deploy.sh never needs Census reachable; budget roughly 10-15MB of repo weight and add the files to the precompression step.
- **Tech:** topojson-client 3.1.0 already installed, plus d3-geo; same-origin static files satisfy CSP connect-src 'self' (backend/main.py:484-495). MapLibre GL 6.10 + PMTiles 4.5 (npm-verified) is the heavier alternative.
- **Verifier:** USChoroplethMap.tsx:741 renders .zip-tiles over an empty canvas (screenshot); county rung documented as dropped; design-review item 6 never landed; topojson-client installed, d3-geo not. CSP connect-src 'self' permits same-origin files. ZCTA is not ZIP and 2020 ZCTAs carry no state attribute.
- **Constraint conflict:** The MapLibre alternative needs blob workers; the CSP (backend/main.py:484-495, default-src 'self', no worker-src) blocks them unless the self-hosted CSP worker build is used. Generated geometry must be committed, since deploy may not depend on an external source.

### `visual-v1` Offer Orchestrator: approval gate and LO routing sit 480px below the fold beside a half-empty Primary offer card

`high` · `defect` · effort `M` · verifier-added

- **Evidence:** dark__offer-orchestrator-detail-populated__full.png: ApprovalBanner at y≈1380 in a 900px viewport; Primary offer card has about 320px of empty space. frontend/src/routes/offer-orchestrator.tsx:738-786 renders LO routing then ApprovalBanner last; :604-606 documents a hero Approve workaround.
- **Problem:** On the approval screen the human-approval gate and loan-officer routing are below the fold while the left card is half empty; the hero Approve shortcut lets reviewers approve before ever seeing routing.
- **Recommendation:** Stack Considered alternatives and Thresholds under Primary offer in the left column to fill the dead space; dock LO routing plus ApprovalBanner as a position:sticky; bottom:0 action bar inside main, then retire the hero Approve workaround. Keep the .approval BEM block and the audit write unchanged. Add a fold assertion to the e2e layout specs.
- **Tech:** CSS grid and position:sticky with the existing ApprovalBanner; no dependency. The prototype shows .approval above the fold.

### `visual-08` Chart craft below the bar: non-round ticks, a histogram drawn as a line, hairline Sankey, 1.9:1 marks in light theme

`medium` · `upgrade` · effort `M` · corrected

- **Evidence:** frontend/src/routes/analytics.lib.ts:199-208 makeTicks splits the range evenly, so the axis reads 24.1K/18.08K/12.05K/6.03K. frontend/src/routes/analytics.charts.tsx:389 draws the score distribution as a polyline. frontend/src/design-system/components.css:4664-4667 strokes var(--accent): measured #66C5FF on #FFF is 1.91:1 in light. `grep -- --chart-` in tokens/components: 0.
- **Problem:** Non-round ticks, a histogram drawn as a line, a Sankey that is a hairline for three of four stages above dead canvas, and sub-3:1 marks in light theme make Analytics look hand-rolled beside Hex or Databricks One.
- **Recommendation:** Add a zero-dependency niceTicks() (1/2/5 steps) in analytics.lib.ts; render bucketed distributions as bars; give the funnel a minimum ribbon thickness with conversion labels on connectors and an explicit 'not to scale' note rather than a sqrt scale, which would misstate proportions in a product sold on reconcilable numbers; add per-theme --chart-1..6, --chart-grid, --chart-axis tokens with marks at 3:1 or better; print quantile breaks on the map legend (USChoroplethMap.tsx:976-986), which extends the prototype's Lower/Higher legend.
- **Tech:** d3-scale 4.0.2 (npm-verified, DOM-free, tree-shakeable) or zero-dependency helper; OKLCH tokens through existing color-mix(in oklab). Keeps hand-rolled SVG and prototype look.
- **Verifier:** makeTicks (analytics.lib.ts:199-208) splits the range evenly; both bucketed distributions use the LineChart polyline (analytics.sections.tsx:120, 211); default accent 'bright' overrides light --accent to #66C5FF, 1.91:1 computed; no --chart- tokens. d3-scale 4.0.2 verified. A sqrt funnel misstates proportions.

### `visual-10` No pixel-level visual regression: zero toHaveScreenshot calls and three orphaned May baselines

`medium` · `gap` · effort `M` · confirmed

- **Evidence:** `grep -rn toHaveScreenshot frontend/tests frontend/src`: 0 hits. frontend/tests/e2e/demo_visual.spec.ts-snapshots holds three PNGs last committed 2026-05-11 that no test references; they show 230px segment cards and a composer-first Ask Genie. `ls frontend/.storybook`: absent.
- **Problem:** Nothing fails when layout regresses, so the clipped Console, white Subject input, 0px card gap and off-screen Score column all shipped. For a product sold on polish, visual quality is unguarded.
- **Recommendation:** Promote this audit's fixture harness (Playwright route interception over a production build) into frontend/tests/e2e/visual.fixture.spec.ts: toHaveScreenshot for 11 routes x dark/light x Console open, animations disabled, plus a no-horizontal-overflow assertion per .surface. Run in PR CI inside the pinned Playwright container; delete the orphan PNGs.
- **Tech:** @playwright/test 1.59 toHaveScreenshot, already installed; reuses route-mock patterns from console-layout.spec.ts. Fixtures stay test-only, consistent with the no-mock-runtime rule.
- **Verifier:** Zero toHaveScreenshot/toMatchSnapshot hits; three PNGs in demo_visual.spec.ts-snapshots last committed 2026-05-11 with no referencing call; no .storybook. PR CI runs only two fixture specs (ci.yml:296-298). Geometry specs exist but missed every defect here. Fonts are self-hosted, so baselines are deterministic.

### `visual-v2` Borrower 360 story opens with a sentence fragment for every single-property borrower

`medium` · `defect` · effort `S` · verifier-added

- **Evidence:** frontend/src/lib/borrowerStory.ts:81-85 emits 'This {city, ST} homeowner.' when related_property_count <= 1; dark__borrower-360-detail__viewport.png renders 'This Chicago, IL homeowner. Carries a 5.75% rate…'. borrowerStory.test.ts:34-39 pins only the 41-property investor case.
- **Problem:** The hero narrative on the dossier reads as broken English for the common single-property borrower, undercutting the 'explain' step of the product promise on a buyer-facing screen.
- **Recommendation:** When no multi-property clause exists, join sentences one and two ('This Chicago, IL homeowner carries a 5.75% rate, 88 bps above market…'); keep claim registration and the number verifier unchanged; add a single-property golden test beside the investor case.
- **Tech:** Pure string composition in borrowerStory.ts; vitest golden case.

**Overflow (titles only, not verified)**

- Typography scale: 48% of visible text elements render at 11px or smaller, 207 of them at an off-scale 10px (components.css has 18 raw `font-size: 10px`). 13 distinct sizes are in use. Weight 650 is declared at components.css:2867/3374/3401 but only static 400-700 files are loaded (tokens.css:6-12); adopt @fontsource-variable/geist 5.3.0 (npm-verified). `text-wrap` is never used.
- Brand expression: EntradaWordmark is a navy raster PNG (frontend/public/brand/entrada-wordmark.png) that measures 1.51:1 against the dark page, so the brand is nearly invisible in the default theme and needs a dark-theme SVG variant. No Cotality x Databricks co-brand lockup exists anywhere in the shell. The Admin hero repeats the dim wordmark.
- Borrower 360: the right column ends about 600px before the left, leaving a void under Supporting evidence. Eight negative flags ('Not investor', 'No 2nd lien', ...) render as equal-weight chips. Surface headers mix boxed and bare icons. The dossier has no equity/LTV bar or rate-vs-par visual.
- Offer Orchestrator: the Primary offer card stretches with about 320px of empty space. Two primary-styled Approve CTAs compete (header and approval banner). Native selects differ from .filter styling.
- Portfolio Builder: the economics grid prints 'Not qualified' five times at value weight, in 105px tiles with three-line caps labels. Subject inputs are blank and 135px wide. The KPI evidence chip wraps to two centred lines.
- Degraded Home: the KPI row and login summary disappear instead of holding skeleton geometry, which causes a layout jump between healthy and degraded states.
- Admin readiness matrix: status chips truncate ('not trained …', 'no certification cl…', 'not provisioned · p…'). Error surfaces print raw backend detail strings.
- The same metric is formatted two ways: Home shows 89,553, Analytics shows 89.55K.
- Off-token radii of 7px and 8px are in use alongside --r-sm/--r-md/--r-lg (probe census).
- Analytics surface titles use the 18px .h-3 without icon tiles; every other route uses 14px .h-4 headers with icons.
- The segment palette is Tailwind-default hex hues (tokens.css:36-49); move to an OKLCH equal-lightness ramp. The choropleth ramp follows the user accent, so a red accent makes high density look like danger.
- Compact density has no visible effect on Lead Queue rows (68px in both modes), because stacked chips and the Log button drive row height rather than --row-h.
- The floating Genie header subtitle truncates ('Trusted Unity Catalog asset…'). The panel has about 230px of dead space between suggestions and composer in its empty state.
- Lead table header labels sit on two baselines (sortable button headers vs plain th), about 4px of jitter visible at y=609 vs 613.
- The Borrower 360 index and ZIP-drill empty states leave large unused canvases; use them for recent borrowers or a preview list.
- The story card copy fragment 'This Chicago, IL homeowner. Carries…' may depend on the fixture; verify against live data.

**Limitations:** **Screenshots and fixtures.** All judgements rest on fixture-driven screenshots from the capture agent's scratch production build: Chromium headless, DPR 1, animations disabled.

**States I could not assess.** No Genie answer state was captured, so answer cards, charts, the proof panel and the process trace are unjudged. Hover, focus, motion, toasts, modals and open dropdowns were not captured either. Analytics sub-tabs other than Executive appear only in their 503 state, so their chart design is unjudged. KPI sparklines and deltas are absent because fixture trends are empty.

**What the captures cannot show.** There were no HiDPI, Firefox/WebKit or mobile captures, and the navy and red accents were not captured. 'Full page' shots use a tall viewport, so sticky and vh behaviour is not representative.

**DOM probe.** My own read-only probe (scratchpad/visual-probe/probe.mjs → probe.json) ran against that same scratch build with the capture agent's mock API, on a machine at load ~50. It never touched the live app, frontend/dist or the repo. Its measurements (overflow widths, element positions, the font-size/family census) are exact for fixture content. They may shift with live strings; for example the Relationship column width is driven by the 'Suppressed: unresolved_owner' chip.

**Prototype parity.** I compared against the two prototype PNGs and the prototype CSS. I did not render the prototype HTML, and Design System.html was not reviewed exhaustively.

**Web checks.** Browser-support and version claims were verified on 2026-09-21 by web search and npm view, for the following only:
- CSS: text-wrap, light-dark(), field-sizing
- npm: @fontsource-variable/geist, maplibre-gl, pmtiles, d3-scale, @tanstack/react-table

Subgrid and color-scheme status come from my own knowledge. Data correctness was out of scope and was not assessed.

---

## App shell, navigation, information architecture and routing (`shell`) — grade C+

**Auditor:** The foundations are good: an accessible command palette, idle and hover chunk preloading, skip links and landmarks, URL-backed workflow filters, and an honest status pill. Against the Linear/Stripe/Vercel bar, the shell has no error boundary, a Console that renders clipped at 1440x900, one static tab title, no scroll or hash handling, a declarative router that fetches on render with no pending UI, and no identity, breadcrumb or notification layer.

**Verifier on the grade:** Fair. The two headline defects reproduced in a real browser against a scratch build (a 404'd lazy chunk left #root with zero children; Console rows render 432px wide inside the 300px panel), and scroll carry-over, the static title and the keyboard-dead topbar results also reproduced. Several listed gaps (Data router, inbox, identity menu) are enhancements rather than defects and the a11y/palette/preload/health foundations are genuinely good, so C+ to B- is the honest band; C+ is not harsh.

**Strengths (keep these)**

- **Command palette is implemented to a high accessibility standard** — CommandPalette.tsx:222-242 is a combobox with a listbox and aria-activedescendant. Line 99 adds a focus trap that restores focus on close, and lines 103-131 debounce and abort the search. Admin actions are gated by commandActionsForAccess (commandActions.ts:59-64), and state resets on both open and close (:61-76).
- **Deliberate code-splitting and speculative chunk preloading** — lazyPreload.ts:10-21 caches the import promise and clears it on failure so a retry works. routePreloaders.ts:31-38 idle-preloads the four likeliest routes, and RouteNav.tsx:82-83 preloads on hover and focus. Console and GenieChat are lazy and idle-warmed (AppShell.tsx:17-26).
- **Workflow filters live in the URL** — lead-queue.tsx:82-147 parses about 25 validated search params. analytics.tsx:66-99 keeps the tab and filters in the URL. portfolio-builder.tsx:274 pushes a shareable filter URL. Segment Intelligence hands its map selection to the Lead Queue through params (segment-intelligence.tsx:556-575).
- **Shell accessibility and identity hygiene** — AppShell.tsx:163-174 adds a second skip link only while the Console is open, with tabIndex=-1 targets and nav/banner/main landmarks. Lines 133-144 clear the query cache, app state, browser storage and memory caches when actor_cache_key changes.
- **Honest, compact system status** — Topbar.tsx:39-111 merges three always-on pills into one Live/Degraded/Probing/Unreachable pill. An 'unreachable' state was added after an expired session showed green. One shared health poll drives both the pill and DegradedBanner. `.main` is a named inline-size container (components.css:419-420), so layouts reflow when the Console opens.


### `shell-01` No error boundary anywhere: one render exception, or a stale chunk after a redeploy, blanks the whole app

`critical` · `defect` · effort `M` · corrected

- **Evidence:** grep -rniE 'errorboundary|componentDidCatch|getDerivedStateFromError|errorElement|onCaughtError|preloadError' frontend/src: 0 hits. main.tsx:13 createRoot passes no error options; app.tsx:69 one Suspense wraps twelve lazy routes; backend/main.py:833 returns 404 for a missing hashed asset; lib/rum.ts observes vitals only, no error listeners.
- **Problem:** React unmounts the whole root on an uncaught render error, so one bad row, or a lazy-chunk 404 after any deploy.sh roll-forward replaces dist/assets under an open tab, leaves a blank page with no recovery, correlation id or telemetry.
- **Recommendation:** Put the per-route boundary in app.tsx RouteTransition around Suspense (its key={pathname} already resets it); a boundary inside PageShell cannot catch lazy-chunk or route-level throws. Add a root boundary, createRoot onCaughtError/onUncaughtError, and a one-shot guarded reload on vite:preloadError. For telemetry, extend backend RumMetricName (backend/schemas/telemetry.py:10) with an error metric carrying only error name and route, no message text, behind the existing rum_enabled flag. Add Playwright chunk-404 and throw-in-route cases.
- **Tech:** React 19 createRoot error callbacks (stable); react-error-boundary 6.1.6 (npm verified) or a 30-line class; Vite vite:preloadError event (present in installed 8.0.16).
- **Verifier:** Reproduced: 404ing the glossary chunk in a scratch-build browser probe left #root with 0 children. Grep for boundaries/error listeners: 0 hits. But lazy failures throw above PageShell, and RumMetricName is a closed, PII-scrubbed Literal needing a backend change, so effort is M.

### `shell-02` Console rail renders clipped at 1440x900: Light/Compact controls pushed off-canvas, embedded form labels corrupted, and it stacks over Genie

`high` · `defect` · effort `S` · corrected

- **Evidence:** Shots dark/light__state__console-open-home.png: Theme shows only 'Dark', Density only 'Comfortable', chips and inputs cut at the panel edge. components.css:4051-4053 .tweaks__body{display:grid} has no minmax(0,1fr) track; :4055 '.tweak-row label' restyles PropertyLookupPanel labels (Console.tsx:195); .tweaks z-index 50 (:4032) over .genie 30 (:2498).
- **Problem:** A first-class shell element looks broken by default: half of each segmented control needs horizontal scrolling. Opening it costs 324px, orphans the fourth KPI card and covers Genie; one-time appearance prefs sit above daily work.
- **Recommendation:** Ship the defect fix alone first: grid-template-columns:minmax(0,1fr) on .tweaks__body, scope the label rule to '.tweak-row > label', let the compact PropertyLookupPanel header chip wrap (its nowrap chip sets the 432px min-content), and assert scrollWidth<=clientWidth in frontend/tests/e2e/console-layout.spec.ts (it checks only vertical geometry today). Treat section reordering, Genie offset (GenieChat geometry is inline from useGenieWindow) and drag-resize as separate owner decisions.
- **Tech:** CSS grid minmax(0,1fr); [data-console] offset rule; pointer-event resize reusing useGenieWindow.ts, or react-resizable-panels 4.13.1 (npm verified); Playwright geometry assertion.
- **Verifier:** Reproduced at 1440x900: every .tweak-row is 432px inside the 300px panel (Property lookup min-content 432); Light button spans x1357-1570 past panel edge 1424; lookup labels compute display:block uppercase. Prototype .tweaks__body is a flex column; the app's grid is the divergence.
- **Constraint conflict:** Reordering Console sections and adding drag-resize depart from the prototype Tweaks panel (Module 0 Prototype.html:1441-1481); owner decision. The overflow fix itself restores prototype behaviour (prototype .tweaks__body is a flex column, line 913).

### `shell-03` Navigation loses the user's place: main scroller never reset or restored, glossary hash links land nowhere, sort, expanded row and map drill live only in memory

`high` · `defect` · effort `L` · corrected

- **Evidence:** components.css:409-412 makes .main the scroller and AppShell.tsx:174 never remounts it; grep 'scrollTo|scrollTop|ScrollRestoration' in src: only GenieChat.tsx:175. GlossaryTerm.tsx:21 links /glossary#id but routes/glossary.tsx has no hash handling. LeadTable.tsx:114-116 sort/expanded and USChoroplethMap.tsx:112-128 drill level are useState.
- **Problem:** Opening a borrower from a scrolled queue can land mid-page; Back returns to the queue top with sort and expanded row gone; every glossary link opens the wrong place; the hero map drill cannot be shared or backed out of.
- **Recommendation:** Split it. First (S): a useMainScroll hook keyed on location.key for .main: reset on PUSH, restore on POP once cached content has height, scrollIntoView for hashes with scroll-margin-top under the sticky route nav. Second (M): sort, expanded row and map drill into typed search params, extending the existing lead-queue.filters.ts pattern; nuqs 2.10.1 (v8 adapter verified on npm) is optional. Track /ask-genie/:conversationId separately; it needs backend actor scoping.
- **Tech:** history location.key + sessionStorage; CSS :target and scroll-margin-top; nuqs 2.10.1 with its react-router/v8 adapter (npm verified, peer range ^8) for typed, batched URL state.
- **Verifier:** Reproduced on warm routes: Home scrollTop 689 then Analytics opens at 570; Glossary 1500 then Home 689; Back to the queue returns 0 not 770; client nav to /glossary#clip never scrolls (hard load does). Bundle also adds URL state, nuqs and a backend route: L, not M.
- **Constraint conflict:** A shareable /ask-genie/:conversationId must stay actor-scoped and fail closed; conversation stores are cleared on actor change (AppShell.tsx:133-144).

### `shell-04` Queue-to-dossier triage loop has no wayfinding: static unclickable crumb, no return-to-results, no previous/next borrower

`high` · `gap` · effort `M` · confirmed

- **Evidence:** Topbar.tsx:290-294 renders two span crumbs from a static map (:142-165); shot dark__borrower-360-detail shows only 'Borrower 360'. borrower-360.tsx links to /lead-queue only in empty and error states (:105, :169). LeadRowPreview.tsx:121-124 navigates without state. Prototype TopBar accepts a crumbs array (Prototype.html:1214).
- **Problem:** Reviewers work a ranked list one borrower at a time, yet each dossier is a dead end: the Leads chip drops their filters, the crumb names neither borrower nor origin, and there is no next-lead action.
- **Recommendation:** Pass the queue's search string and ordered ids through Link state (sessionStorage fallback). Render linked crumbs 'Lead Queue · IL · In the Money > B-48291', a '3 of 23' pager with J/K keys, and advance to the next lead after Approve or Reject. Apply the same pattern to Offer Orchestrator and asset detail, whose crumb has no index page.
- **Tech:** React Router Link state / useLocation().state; existing hotkey pattern (LeadTable.tsx:789); nav aria-label=Breadcrumb with aria-current=page; prototype .topbar__crumbs BEM unchanged.
- **Verifier:** Opened Topbar.tsx:290-294 (two static spans), borrower-360.tsx:105,169 (queue links only in empty/error states), LeadRowPreview.tsx:121 (no Link state); grep for location.state in src: none. Prototype TopBar takes a crumbs array (Prototype.html:1205-1219). Effort M is fair.

### `shell-05` Router runs in Declarative mode behind a keyed remount: fetch-on-render waterfalls, no pending-navigation UI, no route-level lazy or error handling

`medium` · `upgrade` · effort `L` · corrected

- **Evidence:** main.tsx:16 BrowserRouter; app.tsx:68 div key={pathname} remounts Suspense and Routes every navigation. grep 'prefetchQuery|ensureQueryData|useNavigation|createBrowserRouter' in src: 0. RouteNav.tsx:82 preloads chunks only. Installed react-router 8.3.0 docs/start/modes.md lists pending states, view transitions and preventScrollReset as Data/Framework-only.
- **Problem:** Data requests start only after the chunk downloads and the route mounts, so warehouse-backed pages always paint skeletons; the keyed wrapper forces a fallback flash instead of holding the previous screen; nothing signals a navigation in flight.
- **Recommendation:** Do the cheap part first: remove key={pathname} (BrowserRouter already wraps navigations in startTransition, so React holds the previous screen) and render resolved preloaded routes synchronously. If Data mode is still wanted, run loaders on real navigations only; never on hover for /leads or /borrowers/{id}. useNavigation in Topbar forces createMemoryRouter in shell tests (41 files use MemoryRouter), so L stands. Stay a static SPA.
- **Tech:** React Router 8.3 Data mode (stable; verified in installed package), TanStack Query 5 ensureQueryData/prefetchQuery, useNavigation, per-route ErrorBoundary, stable mask and useTransitions props.
- **Verifier:** BrowserRouter, key={pathname} and zero prefetch/useNavigation hits verified; probe saw the fallback commit on first visits. But chunks are already idle/hover-preloaded, so loaders save a render tick, not a round trip, and hover loaders would write VIEW_* audit rows.
- **Constraint conflict:** Hover-run data loaders would call GET /leads and GET /borrowers/{id}, which write VIEW_LEADS / VIEW_BORROWER audit rows (backend/api/leads.py:602, borrowers.py:190; frontend/src/lib/queryClient.ts:14-18 disables focus refetch for exactly this reason). Speculative prefetch would inflate the governance audit trail.

### `shell-06` Shell is identity- and role-blind: no user menu, can_approve is ignored, admin deep links bounce silently

`medium` · `gap` · effort `M` · confirmed

- **Evidence:** backend/api/session.py:11-21 returns can_access_admin and can_approve; frontend types/workspace.ts:37-39 declares only can_access_admin; grep 'can_approve|canApprove' in src: 0. app.tsx:55 Navigate to '/' with no message. Topbar.tsx:355-419 holds tenant pill, status pill and three icon buttons, no user.
- **Problem:** In a product sold on its audit trail, users never see whose name their approvals are recorded under; non-approvers get live Approve buttons that fail; a denied admin link silently dumps them on Home.
- **Recommendation:** Extend /api/session with display name, email and role labels from the Databricks Apps forwarded headers already read in backend/services/rbac.py. Add a topbar avatar menu: identity, role chips, appearance, shortcuts, glossary. Disable Approve/Reject with a 'Requires approver role' tooltip when can_approve is false. Replace the silent redirect with a 403 surface naming the required role.
- **Tech:** Existing FastAPI session router and Pydantic model; headless Menu from Base UI 1.8 (@base-ui/react, npm verified, unstyled so prototype BEM and tokens apply) or the Popover API.
- **Verifier:** session.py:11-21 returns can_approve; frontend SessionResponse (types/workspace.ts:37-39) omits it; grep can_approve/canApprove/approver gating in src: 0. app.tsx:55 redirects silently. rbac.py reads X-Forwarded-Email. Topbar has no identity element. Medium/M is reasonable.
- **Constraint conflict:** None, provided the displayed identity is only the actor's own forwarded email and stays out of RUM payloads (telemetry schema scrubs emails).

### `shell-07` Two competing global searches: topbar results are keyboard-unreachable, and the Cmd-K palette covers only routes and live borrower rows, with substring matching and no domain synonyms, recents or Genie handoff

`medium` · `upgrade` · effort `M` · corrected

- **Evidence:** Topbar.tsx:304-312 input has no onKeyDown or combobox ARIA and :309 closes results 120ms after blur, so Tab never reaches an option. commandActions.ts:75-90 scores with includes(); shot dark__state__command-palette-query: 'refi' matches nothing. CommandPalette.tsx:250 renders one group of 13 static actions; search fetch duplicated (Topbar.tsx:210-242).
- **Problem:** In a mortgage product, typing 'refi' finds nothing. The palette cannot reach segments, glossary terms, data assets, saved leads, analytics tabs or recent borrowers, shows no shortcuts, and the inline search duplicates it with worse accessibility.
- **Recommendation:** Keep the recommendation with two fixes. The 'refi' miss is a vocabulary gap: add mortgage synonyms (refi, refinance, cash-out, HELOC, recapture) to action keywords before any fuzzy scorer. The 'Ask Genie: <query>' row must submit through the existing guarded ask path, never a new endpoint. The palette already has live borrower/ZIP rows; add segments, glossary, assets, saved leads and recents as further providers, and share one useBorrowerSearch hook.
- **Tech:** Keep the hand-rolled accessible combobox; fuzzysort 4.0.2 (npm verified) or a 40-line scorer; one useBorrowerSearch on TanStack Query with keepPreviousData and useDeferredValue; aria-keyshortcuts.
- **Verifier:** Probe: ArrowDown in topbar search does nothing, Tab closes results; 'refi' returns zero actions. But the palette already merges live borrower/ZIP rows in a second 'Borrowers' group (CommandPalette.tsx:34-36,139-141,272), so 'no entities' and 'one group' are wrong.
- **Constraint conflict:** Genie handoff must use the existing fail-closed prompt guard path; no bypass.

### `shell-08` Five hand-kept route tables drift: nav and palette disagree on icons and names, tab title never changes, route changes are silent to assistive tech, nav chips show default link underlines

`medium` · `defect` · effort `M` · corrected

- **Evidence:** Routes are re-declared in app.tsx:71-89, RouteNav.tsx:24-33, Topbar.tsx:142-154, commandActions.ts:30-49 and routePreloaders.ts:17-29; icon 'flow' means Analytics in the nav but Lead Queue in the palette. grep 'document.title' in src: 0; index.html:7 is static. tokens.css:335 only sets a{color:inherit}, so NavLink chips render underlined (all shots).
- **Problem:** Every tab, bookmark and history entry reads 'Mortgage Intelligence Platform' (WCAG 2.4.2, Level A); route changes move no focus and announce nothing; Analytics interrupts the portfolio-segment-rank-explain-offer order; non-admins have no audit destination.
- **Recommendation:** As written, except the title: document.title resolves to the first <title> in tree order, and React 19 appends its hoisted <title> after the static one in index.html:7, so either set document.title in a PageShell effect or remove the static tag and render the default from the shell. Drop the 'no audit destination' claim; Console 'My recent activity' already serves non-admins.
- **Tech:** React 19 native document-metadata hoisting (stable); TypeScript satisfies plus template-literal path types; aria-live=polite announcer; no new dependencies.
- **Verifier:** Five tables, icon drift (flow/user/doc), static title, underlined chips (no text-decoration on .filter) and focus staying on the clicked link all verified. Two fixes: React's <title> loses to index.html's static <title>; non-admins do have Console 'My recent activity'.

### `shell-09` No notification surface: no global toast region, no inbox, no pending-work badges on navigation

`medium` · `gap` · effort `L` · corrected

- **Evidence:** grep -niE 'notification|inbox|unread|bell' over components/layout, components/command and routes/home.tsx: 0 hits; the only toast is local state salesToast (LeadTable.tsx:123). RouteNav.tsx:75-90 renders icon and label only. The prototype icon set already ships 'bell' (Prototype.html:1008).
- **Problem:** Assignments, approvals awaiting a decision, finished long-running Genie answers and the nightly Cotality refresh all happen silently, so users must poll pages; feedback for save, copy and export is inconsistent because no shell-level toast exists.
- **Recommendation:** Start with the shell toast region (role=status, stacked, undo-capable) adopted by every mutation; it is the cheap, high-frequency win. Feed the bell from /api/audit/my-events. Back nav badges with a new audit-free count endpoint; polling GET /leads would write a VIEW_LEADS audit row per poll. In-app only, never outreach. Additive to the prototype topbar.
- **Tech:** Base UI 1.8 Toast and Popover (headless, npm verified) or the Popover API; TanStack Query refetchInterval; existing Lakebase audit endpoints; optional FastAPI SSE later.
- **Verifier:** Verified: only LeadTable's local salesToast and .bulk-actions__toast exist; no bell/inbox in the shell; bell icon is in Icon.tsx. Home's since-last-login card and Console recent activity partly cover 'what changed'. Badge polling must avoid audited reads (GET /leads writes VIEW_LEADS).
- **Constraint conflict:** Badge polling of audited read endpoints would inflate the audit trail (backend/api/leads.py:602); needs a dedicated count endpoint. Bell and badges are additive to the prototype topbar.

### `shell-10` Shared-element View Transitions plus a masked-URL borrower peek sheet would make navigation feel native

`medium` · `wow` · effort `L` · corrected

- **Evidence:** components.css:5012-5018 is the only route motion: a 4px fade replayed by re-keying (app.tsx:68). grep 'viewTransition|startViewTransition|view-transition' in src: 0. Installed react 19.2.8 exports no ViewTransition (node check); React 19.3.0 stabilised it on 2026-09-09 (react.dev, fetched).
- **Problem:** Row-to-dossier, KPI-to-drawer and map-drill changes are hard cuts behind a generic fade, so the spatial continuity that makes Linear and Vercel feel expensive is absent although every target browser now supports it.
- **Recommendation:** Phase it. First: bump React to 19.3 after a React Compiler/happy-dom regression pass and wrap the routed subtree in ViewTransition; BrowserRouter already navigates inside startTransition, so Declarative mode suffices. Give only the opened row a view-transition-name (rows are virtualized). Treat addTransitionType direction as progressive enhancement. The masked peek sheet needs Data mode (shell-05) or a manual backgroundLocation pattern; schedule it separately. Honour reduced motion.
- **Tech:** Same-document View Transitions: Baseline Newly available since 2025-10-14 (Chrome 111, Safari 18, Firefox 144; web.dev verified). React 19.3 ViewTransition/addTransitionType stable; Router mask stable since 7.15, Data mode.
- **Verifier:** React 19.3.0 (2026-09-09) adds ViewTransition; installed 19.2.8 lacks it; Baseline date confirmed on web.dev. But Router mask is Data-mode only, LeadTable rows are virtualized, and Firefox 144 shipped without transition types. With the peek sheet this is L.
- **Constraint conflict:** Router mask is Data-mode only (react-router CHANGELOG: 'Add support for <Link unstable_mask> in Data Mode'), so the peek sheet depends on shell-05. Firefox 144 shipped same-document view transitions without transition types (web.dev), so directional types must degrade gracefully.

### `shell-v1` Expired session or unreachable backend has no recovery surface: only an amber pill with a hover tooltip

`medium` · `gap` · effort `S` · verifier-added

- **Evidence:** HealthProvider.tsx:45-57 computeDegraded ignores status 'unreachable'; DegradedBanner.tsx:81-93 returns null unless a dependency is 'down'; Topbar.tsx:49-64 renders only an amber 'Unreachable' pill whose re-authenticate advice lives in a hover title. grep 'session expired|sign in again|reauth' in src: 0.
- **Problem:** When the Databricks Apps session lapses or the backend is unreachable, every page shows its own error block while the shell's only explanation is a tooltip on a small pill; nobody is told that a reload restores access.
- **Recommendation:** After two consecutive unreachable probes while navigator.onLine, render the existing degraded-banner BEM with 'Connection lost; your session may have expired' and a Reload button preserving the URL; pause query retries meanwhile. Add a Playwright case that aborts /api/* and asserts the banner.
- **Tech:** HealthProvider snapshot; DegradedBanner BEM; navigator.onLine; location.reload; TanStack Query onlineManager.

### `shell-v2` Preloaded routes still flash the RouteFallback skeleton on first visit, so idle and hover preloading buys no perceived speed

`medium` · `defect` · effort `S` · verifier-added

- **Evidence:** Scratch-build probe: after idle and hover preload had fetched the analytics and glossary chunks, a MutationObserver still saw RouteFallback mount on the first click of each (0 on revisit). lazyPreload.ts:18 returns bare React.lazy; app.tsx:68-69 mounts a fresh Suspense per pathname.
- **Problem:** React.lazy suspends once even for a resolved import, and the keyed wrapper gives it a new Suspense boundary, so the skeleton paints anyway. The preloading investment buys no perceived speed on first visits.
- **Recommendation:** In lazyWithPreload, cache the resolved module and return a wrapper that renders it synchronously once loaded, falling back to React.lazy otherwise; or drop key={pathname} so the router's startTransition holds the previous screen. Pin with a test asserting no RouteFallback after preload.
- **Tech:** React.lazy semantics; startTransition already applied by BrowserRouter (react-router 8 default); existing lazyPreload.test.ts.

**Overflow (titles only, not verified)**

- State inventory, URL: lead-queue filters (~25 params), analytics view and filters, segment filters, portfolio-builder filters, offer campaign_id, admin hash anchors, and borrower and asset ids in the path.
- State inventory, memory only: home map drill level, table sort, selection and expanded row, evidence drawer, Genie open state, lastBorrowerId, proof and math panels, and showEvidence/showConfidence.
- State inventory, browser storage: localStorage holds mip.theme, mip.accent, mip.density, mip.consoleOpen, Genie window size and position, pinned insights and bulkApprove.lastCancelled. The Genie conversation id and turns are in browser storage, not the URL.
- lastBorrowerId is memory-only (AppContext.tsx:185). After a reload, the 'Borrower 360' and 'Offer' nav chips fall back to empty index pages.
- The Console toggles showEvidence and showConfidence are not persisted (AppContext.tsx:173-174). They also let a user hide evidence chips, which is questionable in a governance-first product.
- The Rail spends a 72px column on one live item. The brand link and M0 both go to '/'. Rail.tsx:39-40 contains dead logic (pathname !== '/__unused'). The admin gear has no active state.
- M1-M4 placeholders use role=presentation with aria-disabled and a native title (Rail.tsx:66-72). They are not focusable and have no styled tooltip. A roadmap hover card would show the platform vision honestly without building the modules.
- Every shell tooltip is a native title attribute (7 in Topbar, 4 in Rail). These are slow, unstyled and unreachable by keyboard. One tooltip primitive would fix it, using the Popover API with CSS anchor positioning or Base UI Tooltip.
- When borrower search fails, the command palette shows both 'No ... match' and 'Borrower search is temporarily unavailable' at once (shot dark__state__command-palette-query).
- The Topbar Genie toggle and the palette 'open-genie' command do not close the Console; only Console.tsx:370 does. Both right-edge panels can therefore stack at their default positions.
- The 404 page offers only two links (not-found.tsx:31-40). It could add palette-backed 'did you mean' suggestions and recent pages.
- At 1280x720 the Lead Queue shows one data row above the fold: topbar 56px, sticky nav 57px, hero about 150px and filter panel about 215px (shot dark__size-1280x720__lead-queue). Collapsing the hero on scroll and compacting the filter bar would help.
- There is no inline theme bootstrap. AppContext.tsx:222-226 sets data-theme in an effect, so light-theme users get a dark first paint. The fix needs a CSP-hashed inline script. The theme-color meta is also static.
- .app-shell uses height:100vh and width:100vw (components.css:24-25). Prefer 100dvh and 100% to avoid scrollbar-width overflow on Windows.
- React 19.2 Activity is unused. Genie and the Console unmount on close (AppShell.tsx:191-217). Activity mode=hidden would keep their state and let them pre-render when idle.
- useParams is untyped and relies on id! assertions (borrower-360.tsx:85). A typed path helper would remove them.
- There is no e2e coverage for Back/Forward, scroll restoration or per-route titles. A grep for goBack and toHaveTitle in tests/e2e finds only one generic title assertion (module0.spec.ts:121).
- The session query is declared three times with copy-pasted options (RouteNav.tsx:42, AppContext.tsx:191, app.tsx:48). Extract a useSession() hook that also exposes can_approve.
- There are no useTransition, startTransition or useDeferredValue calls anywhere in src, so filter and search typing cannot stay responsive under slow renders.

**Limitations:** This was a read-only code and screenshot audit. I did not run the app, because there was no dev server, the machine was under heavy shared load, and the default ports were off-limits.

- **Behavioural findings not reproduced:** landing position after navigating from a scrolled queue, Back-button loss of sort and expanded row, the glossary hash miss, the stale-chunk white screen after a redeploy, and the Console-over-Genie stacking. All are derived from the code; none was seen live.
- **Console clipping:** observed in the fixture-backed production-bundle screenshots in both themes, and explained from the CSS. It was not measured in a live DOM.
- **Screenshot coverage:** Chromium only, device scale factor 1, animations disabled, admin session only. There is no capture of a non-admin session, a Genie answer state, both right-edge panels open together, hover or focus states, or any route motion.
- **Navigation latency:** no real measurement or RUM data was reviewed.
- **Prior audits:** apart from button-and-interaction-audit.md, prior audits were keyword-scanned rather than read end to end. An earlier audit could mention one of these items in wording my searches missed.
- **Verified facts:** browser support and release status were checked through the web.dev and react.dev posts, npm view, and the installed react-router 8.3.0 docs and changelog.
- **Assumptions:** React Compiler 1.0 compatibility with React 19.3 was not verified. The claim that deploys replace old hashed assets assumes Vite's default emptyOutDir; frontend/dist was never read.

---

## Dense data and table UX (Lead Queue, LeadTable, filters) (`tables`) — grade C+

**Auditor:** The engineering is strong: virtualization with expandable rows, mutation safety, ARIA semantics, and URL-driven server filters. As a data grid it trails Linear, Attio and AG Grid. The Approve column is off-screen at 1440x900, about five rows are visible, and sort is client-only over a 500-row window. It has no column management, row cursor, saved views or audited export.

**Verifier on the grade:** C+ is fair, perhaps a notch harsh. Nearly all evidence reproduced, and the hero Approve control sitting off-screen at the 1440x900 contract viewport with about five rows visible justifies a sub-B grade. B- is defensible because the footer already discloses the 500-row cap, list reads are audited with rendered borrower ids, and the repository already supports min-score/rate-spread filters. Against that, the auditor missed a query-key bug that can show the wrong cohort for city drill-downs and an unscoped single-key approve hotkey.

**Strengths (keep these)**

- **Virtualization with expandable rows is implemented correctly** — LeadTable.tsx:154-174 and 1075-1085: one <tbody> per row measured via rowVirtualizer.measureElement, estimateSize adds the expanded-preview height, getItemKey is the stable borrower_id, spacer tbodys are aria-hidden, and aria-rowcount/aria-rowindex shift for the expanded row (1002, 1083-1085).
- **Mutation safety is unusually rigorous for an audited workflow** — LeadTable.tsx:187-192 synchronous in-flight latches stop double audit rows; 614-666 AbortController on unmount with a sessionStorage partial-result flash; 626-674 only provably-uncommitted failures are re-selected for retry; 688-695 deterministic focus restoration after bulk runs.
- **Filters are URL-addressable and pushed down to the server with honest of-N framing** — lead-queue.tsx:180-201 writes every filter into search params; 219-244 forwards them to /api/leads; LeadTable.tsx:1248-1258 footer states 'Showing N of total matching filters, capped at N' from X-Total-Matching / X-Truncated-At headers (api.ts:1286-1289).
- **CSV content is hygienic and carries provenance** — LeadTable.csv.ts:6-9 neutralises formula injection (=,+,-,@) and quotes correctly; 64-72 fail-closed eligibility/consent/DNC gate; 73-80 header block records generated_at, filters, suppression policy, refreshed_at and rules_version.
- **Table semantics and numeric typography follow best practice** — LeadTable.tsx:841-859 puts aria-sort on the <th> columnheader with a named sort button; components.css:1237 and 1274 give numeric cells mono tabular-nums and right alignment; ConfidenceMeter.tsx:27-33 uses role=meter with value text; <kbd> keycaps at LeadTable.tsx:888; hover/expand deliberately avoid audited dossier reads (modernization-todo.md:130).


### `tables-01` Approve/Reject column renders off-screen at 1440x900; its sticky pin was deleted

`high` · `defect` · effort `M` · corrected

- **Evidence:** components.css:1309-1323 column widths total 1,564px. Rendering the real CSS in headless Chromium measured the table at 1,618px inside a 1,318px scrollport, with Approval at x=1454-1618. Commit 74f8eda1 deleted `position: sticky; right: 0`. components.css:1324-1330 keeps the orphaned opaque background.
- **Problem:** At 1440x900 the Score, Signal and Approval columns sit outside the scroll region (Primary offer too with the Console open). RowPreview also omits the prototype's ApprovalBanner, so no Approve control is visible without horizontal scrolling inside a 520px box.
- **Recommendation:** Lead with prototype parity: add ApprovalBanner to LeadRowPreview (Prototype.html:1981) wired to approveLead/setPendingReject; the CSS comment at components.css:1249-1256 already assumes Approve lives there. Then restore the end-pin on the Approval cell/header, and in the same commit fix route_performance.spec.ts: `document.elementsFromPoint` lists occluded elements (only overflow-clipped ones drop), which is why 74f8eda1 had to delete the pin while adding visibleAtCenter. Use topmost `elementFromPoint` or skip position:sticky cells. Add a Playwright toBeInViewport check on lead-approve-* at 1440x900 with Console closed and open. Treat the pin as a fallback; column presets (tables-05) that fit 1,318px are the durable fix.
- **Tech:** CSS sticky table cells (Baseline widely available); logical inset properties; Playwright toBeInViewport. No dependency.
- **Verifier:** Widths sum 1,564px against a 1,318px scrollport; 74f8eda1 removed the pin; RowPreview lacks ApprovalBanner: verified. But elementsFromPoint also returns occluded elements, so the /lead-queue overlap canary re-reds if sticky returns. Approve remains reachable via bulk bar and A hotkey, so high, not critical.
- **Constraint conflict:** None. ApprovalBanner in the expanded row is prototype parity; the inline Approve column is a documented 2026-04-22 deviation (LeadTable.tsx:52-57).

### `tables-02` Sort and select operate on a 500-row client window; cap is disclosed but sort scope is not; no server sort or pagination

`high` · `gap` · effort `L` · corrected

- **Evidence:** backend/schemas/lead_query.py:27-28 sets limit 500 (max 5,000). backend/api/leads.py:198 accepts only `limit` (grep sort|offset|cursor: none). LeadTable.tsx:134-144 sorts the loaded array client-side. lead-queue.tsx:219-244 never passes limit. databricks_leads.py:67 orders `rank_overall, borrower_id`.
- **Problem:** Sorting by Equity or Rate reorders only the 500 top-ranked rows, not the thousands that match, and nothing says so. There is no pagination or load-more. Borrowers past 500 are unreachable unless filters are narrowed.
- **Recommendation:** Ship now (S): footer copy 'sorted within the loaded 500' when sortKey != rank, plus a Rank reset control (toggleSort('rank') exists at LeadTable.tsx:822 but no header reaches it). Then (L): whitelisted `sort`/`dir` limited to warehouse columns (score, equity, rate spread, confidence) with a keyset cursor over (sort column, rank_overall, borrower_id). Keep Lakebase-hydrated keys (relationship, assignment, outreach) as filters or 'within loaded rows' sorts. lead-queue uses useWarmingUpRetry, not useQuery, so build a warming-up-aware infinite variant. Every page still writes its VIEW_LEADS audit row and X-Total-Matching header.
- **Tech:** TanStack Query 5 useInfiniteQuery and @tanstack/react-virtual (both installed); keyset pagination in Databricks SQL against gold tables.
- **Verifier:** No sort/offset/cursor on /api/leads and client-only sort verified. Footer already states 'of N total matching, capped at 500' (LeadTable.tsx:1248-1257); only sort scope is undisclosed. Relationship, assignment and outreach sort keys are Lakebase-hydrated after the warehouse read, so they cannot be keyset-sorted.

### `tables-03` No keyboard row cursor: no J/K, no Enter-to-open, no auto-advance after approve

`high` · `gap` · effort `M` · corrected

- **Evidence:** LeadTable.tsx:753-786 handles only A, R and Shift+A, and A/R require an expanded row. grep for ArrowDown, 'j', roving, scrollToIndex in LeadTable*.tsx: no hits. LeadTableRow.tsx:80-285 exposes five to seven tab stops per row. approveLead (LeadTable.tsx:290-293) never advances.
- **Problem:** A triage queue with no row cursor: keyboard users Tab through up to seven stops per borrower, and after every approval must re-target the next row by mouse. No J/K flow, no shortcut sheet.
- **Recommendation:** Add an active-row cursor on the native table (no role=grid rewrite): J/K or arrows move it through the virtualizer's scrollToIndex, Enter expands, X selects, A/R act on the cursor row then advance to the next pending row. Bind these keys only while focus is inside `.tbl-wrap` and no dialog, drawer or listbox is open; add aria-keyshortcuts, a `?` sheet, a Console off-switch, and Cmd-K entries. Keep in-row controls natively tabbable and add a 'skip table' link instead of roving tabindex. Extend the 331-line hotkeys test file accordingly.
- **Tech:** WAI-ARIA APG roving-tabindex/grid pattern; @tanstack/react-virtual scrollToIndex; aria-keyshortcuts. No new dependency.
- **Verifier:** Verified: only A/R/Shift+A, expanded row required, no arrows/J/K/scrollToIndex, five to seven tab stops per row. Correction: hotkeys are window-level with only an input guard, so more single-key shortcuts need focus scoping (WCAG 2.1.4); a role=grid/roving rewrite is unnecessary risk.

### `tables-04` About five rows visible: 86px stacked-chip rows in a 520px scroller; density tokens are masked by content height

`high` · `upgrade` · effort `M` · corrected

- **Evidence:** LeadTable.constants.ts:6 estimates 86px rows. components.css:1267-1268 caps `.tbl-wrap` at 520px. components.css:1303 top-aligns cells holding `.chip-stack` columns (LeadTableRow.tsx:120-207). tokens.css:285-296 density only changes `--row-h`; grep `data-density` in components.css: 0 hits. Prototype rows: 44px, 11 columns (Module 0 Prototype.html:2013-2023).
- **Problem:** Roughly five borrowers are visible at once on a 900px screen. An expanded preview (360px estimate) consumes most of that. The Console density toggle has no effect on the hero table. The prototype contract is a dense single-line grid.
- **Recommendation:** Return to one-line cells at `--row-h` (44px, 36px compact): one primary chip per column with `+n` overflow into the evidence hover-card, but DNC, Suppressed and Owner-unresolved chips stay visible in-row because they are compliance signals. Move timestamps, the lifecycle-advance control and the Log button into the row preview. Update LEAD_ROW_ESTIMATE_PX and LeadTableRow.render tests. A `calc(100dvh - offset)` scroller is a declared prototype deviation (prototype uses fixed 480px); floor it at 480px and say so in the commit.
- **Tech:** CSS dvh units (Baseline widely available), container queries, existing `--row-h` density tokens. No dependency.
- **Verifier:** 86px estimate, 520px cap, chip-stack cells and the prototype's 44px, 11-column rows verified. But the prototype itself fixes the scroller at maxHeight 480 (Prototype.html:2009), so viewport sizing is a departure. `.tbl td` does consume --row-h; tall content masks it.
- **Constraint conflict:** Viewport-sized scroller departs from the prototype's fixed table max-height (Prototype.html:2009) and must be called out. Collapsing DNC/suppression chips into '+n' would weaken a compliance signal.

### `tables-06` Filter UX is a wall of 16 single-select dropdowns; no ranges, facets or multi-select; FilterSelect has listbox a11y defects

`high` · `gap` · effort `L` · corrected

- **Evidence:** lead-queue.tsx:551-659 renders 16 single-select FilterSelects. Hero chips are read-only (465-470); only segment chips remove (531-540). lead_query.py exposes no score/rate/LTV range params (grep min_score|min_rate|ltv: none). FilterSelect.tsx:54-72 lacks typeahead/Home/End; 96-115 no aria-activedescendant. components.css:4962-4969 no flip.
- **Problem:** Sixteen dropdown triggers wrap across several rows above the table. There is no multi-select, no search in long lists, no facet counts and no numeric ranges. Screen readers hear nothing while arrowing options, and Tab leaves the menu open.
- **Recommendation:** Split. Now (S-M): repair FilterSelect in place: option ids plus aria-activedescendant, Home/End, typeahead, close on blur/Tab, flip near the viewport edge. It serves lead-queue, analytics, segment-intelligence and portfolio-builder. Next (L): keep the prototype's core pills visible, move the rest behind a 'More filters' popover with removable active chips in `.filter` BEM; expose the repository's existing min-score and rate-spread filters as public params with range inputs; add a GROUP BY facets endpoint for counts. Position with Base UI (JS) or CSS anchor positioning only behind @supports with a JS flip fallback.
- **Tech:** @base-ui/react 1.8.0 Combobox/Popover/Slider (headless, verified on npm), or native Popover API + CSS anchor positioning (Baseline 2026: Firefox 147+, Safari 26).
- **Verifier:** Sixteen FilterSelects and the listbox defects (no aria-activedescendant, Home/End, typeahead, blur-close, flip) verified. Inaccurate: the repo already filters min_opportunity_score and min_rate_spread_bps (databricks_leads.py:140-141) and min_equity_pct is public. The prototype contract is a FilterSelect filter-row.
- **Constraint conflict:** A '+ Filter' builder departs from the prototype's `.filter-row` of FilterSelect pills (Prototype.html:1772-1778); must be declared. CSS anchor positioning is only Baseline newly available (Firefox 147 Jan 2026, Safari 26; absent from Firefox ESR 140), so progressive enhancement only.

### `tables-08` CSV export writes no audit event of its own, ignores selection and sort, and mislabels its row count

`high` · `gap` · effort `M` · corrected

- **Evidence:** exportCsv (LeadTable.tsx:806-819) serialises the loaded `leads` prop client-side: not `sortedLeads`, not `selectedIds`, max 500 rows. The label counts `leads.length` (899) although LeadTable.csv.ts:67-72 drops ineligible rows. grep -i export across backend/api/*.py finds no export route or audit event.
- **Problem:** A product whose promise ends in 'audit' lets users download borrower lists with no ledger entry. The export silently ignores selection and sort and caps at 500. It can announce '500 leads' while writing zero rows.
- **Recommendation:** Step 1 (M): a server-owned `POST /api/leads/export-receipt` that writes LEAD_EXPORT to mip_app.action_audit (actor from the edge header, filter fingerprint, exported row count, SHA-256 of the CSV, scope = selected/loaded) before the download starts; POST /api/audit/event is admin-only so the client cannot self-report. Export `sortedLeads`, or the selection when one exists, and label the button with the post-eligibility count. Step 2 (owner decision): server-streamed full-cohort export through the same eligibility gate, row-capped, role-gated and rationale-required.
- **Tech:** FastAPI StreamingResponse; existing AuditStore; optional showSaveFilePicker progressive enhancement (Chromium-only) with anchor-download fallback.
- **Verifier:** Verified: exportCsv serialises `leads` not sortedLeads/selection, label counts pre-gate rows, no export route or audit event. Nuance: the list read is already audited with rendered_borrower_ids (leads.py:594-604), and code comments deliberately decline a server export (LeadTable.tsx:798-805).
- **Constraint conflict:** Streaming 'All matching' widens egress from the 500 on-screen rows to the whole cohort and reverses an explicit in-code decision not to add a server export; owner decision, not a default.

### `tables-v1` Lead Queue query key omits the city filter, so city drill-downs can show the wrong cohort

`high` · `defect` · effort `S` · verifier-added

- **Evidence:** lead-queue.tsx:229 passes `cities: cityFilters` to the fetcher and :253 lists it in deps, but the explicit queryKey (:268-289) omits it; useWarmingUpRetry.ts:118 ignores deps when queryKey is supplied. buildLeadQueuePath (USChoroplethMap.utils.ts:216-220) emits `cities` without `state`.
- **Problem:** `/lead-queue?cities=CHICAGO~IL` shares one cache entry with the national queue and every other city. Within the 30s staleTime the table shows the wrong cohort under a 'cities = Chicago' chip; later it flashes wrong rows before refetching.
- **Recommendation:** Add `cityFilters.join(',')` to the queryKey; better, build the key from the same params object passed to api.leadsPage so they cannot drift. Add a lead-queue test that changes only `cities` and asserts a second fetch with different rows.
- **Tech:** TanStack Query 5 query-key hygiene. No dependency.

### `tables-v2` Single-key A approves from anywhere on the page: window-level hotkey with no focus scope or off switch

`high` · `defect` · effort `S` · verifier-added

- **Evidence:** LeadTable.tsx:752-791 binds A/R on `window`; isEditableTarget (LeadTable.logic.ts:25-30) exempts only INPUT, TEXTAREA, SELECT and contentEditable. No focus scope, dialog/drawer check, confirmation or off switch; the hotkeys test covers only the editable case (:316).
- **Problem:** With a row expanded, pressing A while focus sits on a filter button, the evidence drawer or Genie panel chrome approves a borrower and writes an audit row. Fails WCAG 2.1.4 Level A; J/K/X or listbox typeahead would widen it.
- **Recommendation:** Handle row shortcuts only while focus is inside `.tbl-wrap` and no dialog, drawer or listbox is open. Add aria-keyshortcuts and a Console toggle persisted like density. Extend LeadTable.hotkeys.test with a focused-button-outside-table case and a drawer-open case that must not approve.
- **Tech:** WCAG 2.1.4 Character Key Shortcuts (Level A); focus-scoped keydown; aria-keyshortcuts. No dependency.

### `tables-05` No column show/hide or presets: every persona gets 15 columns that overflow 1440px; LeadTable opts out of React Compiler because of the virtualizer and a render-time ref

`medium` · `gap` · effort `XL` · corrected

- **Evidence:** LeadTable.tsx:1004-1020 hard-codes a 15-column colgroup and 1038-1050 the header. grep columnVisibility|resiz|reorder|pinned in LeadTable*/lead-queue*: no hits. Sort is single-column, two-state, with no rank header (LeadTable.tsx:821-835), so default order is unrecoverable. `'use no memo'` at LeadTable.tsx:76.
- **Problem:** No show/hide, resize, reorder, pin or multi-sort; every persona gets the same 15 columns. Table mechanics, six async mutation handlers and 22 useState hooks live in one component that opts out of React Compiler.
- **Recommendation:** Stage it. First (M, no dependency): one typed 15-entry column-definition array driving colgroup, header and cells; a visibility set; two presets, 'Review' (the prototype's 11 columns, which fit 1,318px) and 'Sales ops', persisted per user; a Rank reset. Isolate useVirtualizer and the latest-handler ref into child components/hooks so LeadTable can drop 'use no memo'; move approve/assign/disposition into useMutation hooks. Adopting @tanstack/react-table v9 for resize, reorder, pinning and multi-sort is an owner decision: a seven-week-old major release against roughly 1,800 lines of LeadTable tests, for features no buyer persona has asked for.
- **Tech:** @tanstack/react-table 9.2.4 (npm latest, verified 2026-09-21; v9 is React Compiler-safe and tree-shakable). It is headless, so prototype BEM is untouched.
- **Verifier:** No column management and 'use no memo' at :76 verified; react-table 9.2.4 is npm latest (9.0.0 stable 2026-08-04, compiler-compatible per TanStack docs). But the opt-out stems from useVirtualizer (:151-154) and a ref written during render; Table v9 alone won't lift it.
- **Constraint conflict:** A column menu is additive to the prototype (not in design_files); acceptable if rendered with existing `.tbl`/`.filter-menu` BEM and declared.

### `tables-07` Bulk operations: no shift-range or select-all-matching, phantom selection after filter change, no progress or cancel

`medium` · `gap` · effort `M` · corrected

- **Evidence:** toggleSelect (LeadTable.tsx:396-403) ignores shiftKey. toggleSelectAll (562-571) covers loaded rows only. selectedIds never resets when `leads` changes (table unkeyed, lead-queue.tsx:725). Bulk approve shows only 'Approving…' (1199). Each lead costs two POSTs under a 120/min mutation budget (backend/config/settings.py:518).
- **Problem:** There is no shift-click range and no 'select all N matching'. A stale selection count survives filter changes. Approving a few hundred leads runs for minutes at about 60 leads/min, with no progress, cancel, bulk reject or hold.
- **Recommendation:** Client-side only (M): shift-range selection; prune selectedIds to current lead ids whenever `leads` changes; a determinate `k of N` <progress> with a Cancel that stops un-started chunks via the existing AbortController (in-flight outcomes stay 'confirm in Recent activity', as today); bulk Reject/Hold with one shared reason code; an ETA derived from the 120/min mutation budget. Leave 'Select all N matching' and a server batch endpoint as an owner decision: every approval must keep its own governed draft proof (generation id + response hash) and audit row.
- **Tech:** Native <progress>; AbortController; TanStack Table v9 shift-range selection if tables-05 lands; FastAPI batch route inside one Lakebase transaction.
- **Verifier:** toggleSelect ignores shiftKey, select-all covers loaded rows, no progress or cancel, two POSTs per approve: verified. Phantom selection is label-only; approve and assign intersect with current ids (:474, :598). Server batch contradicts the documented per-borrower draft-proof design (:573-576).
- **Constraint conflict:** Approving by filter fingerprint through a server batch approves borrowers no human saw and bypasses the per-borrower draft proof; conflicts with the 'human approval always required' posture unless the owner explicitly accepts it.

### `tables-09` No saved views, recent filters or copy-link; sort state never reaches the URL

`medium` · `gap` · effort `L` · corrected

- **Evidence:** grep -i 'saved.?view|recentFilter' in frontend/src: no hits. Sort and expanded row live in useState (LeadTable.tsx:114-116), outside the URL. grep clipboard in lead-queue.tsx and LeadTable*.tsx: none, though Portfolio Builder has share-URL. backend/api/workspace.py:167-302 persists only saved leads and drafts.
- **Problem:** Filters are URL-addressable, but users cannot name, save, pin or share a queue view, and sort never survives reload. Daily queues like 'stale approved >7d' must be rebuilt from 16 dropdowns each morning.
- **Recommendation:** Phase 1 (M, no infrastructure): system view tabs that are plain URL presets built from existing params (assigned_to, approval_status, aged_days), `sort`/`dir` in the URL through the existing useSearchParams helpers, and a 'Copy link' button reusing portfolio-builder's clipboard handler (portfolio-builder.tsx:283-291). Phase 2 (L): user-saved views (filters, sort, column preset, density) in Lakebase workspace state via an idempotent migration run by scripts/deploy.sh, surfaced in Cmd-K. No new dependency.
- **Tech:** Existing /api/workspace + Lakebase migration; react-router 8 useSearchParams or nuqs 2.10 typed URL state (verified on npm).
- **Verifier:** Verified: no saved-view code, sort/expanded in useState, no clipboard in lead-queue, workspace API holds only leads and drafts. Effort understated: Lakebase migration, schema, tabs, Cmd-K and tests is L. nuqs is unnecessary; useSearchParams helpers already exist.

### `tables-10` Audit explorer exposes three free-text filters while the API already supports actor, date range and correlation

`medium` · `gap` · effort `M` · corrected

- **Evidence:** AdminAuditExplorer.tsx:207-244 offers three free-text inputs (placeholders `outreach.approve`, `APPROVE`). api.ts:2041-2053 and backend/api/audit.py:225-233 already accept actor, since, until, subject_clip, correlation_id. Rollups group by action|actor|event_type (audit.py:305-308). Filters sit in useState (42-49); entity id is plain text (460).
- **Problem:** Compliance users must memorise exact action strings. There is no date range, actor filter, total count, export or shareable URL, and borrower IDs do not link to Borrower 360. The backend already supports most of this.
- **Recommendation:** Feed the Action and Event-type comboboxes from the server's audit event-type registry (or a small admin-only distinct-values endpoint), not the seven-type rollups. Add an Actor field and date presets wired to since/until; add correlation_id to api.auditEventPage; move filters into the URL; link `B-` entities to Borrower 360 and correlation ids to a filtered view. The page is snapshot-cursor paginated, so 'total matching' needs a separate COUNT. Audited CSV/JSON export should reuse the tables-08 receipt pattern.
- **Tech:** Existing /api/audit/events/page params and /api/audit/rollups groupBy; same headless combobox as tables-06. No new infrastructure.
- **Verifier:** Three free-text inputs and useState filters verified; backend accepts actor/since/until/correlation_id. But api.ts auditEventPage lacks correlation_id, and /audit/rollups is hard-limited to seven workflow event types (audit.py:320-324), so comboboxes fed from it would omit VIEW_LEADS, Genie and suppression events.

**Overflow (titles only, not verified)**

- Side-peek panel (Space to peek, stays open while J/K moves) as an alternative to the 360px inline expansion that consumes most of the 520px scroller. This departs from the prototype's inline `.tbl__expand`, so it is an owner decision.
- No copy affordance for borrower_id or property ref in LeadTable. The IDs sit inside a <button>, so text cannot be selected (LeadTableRow.tsx:94-113). The audit explorer already has copy buttons.
- Warm-up and error states still mount an empty LeadTable reading 'Showing 0 ranked borrowers' (lead-queue.tsx:293 `loading` definition and 719-735).
- No-result state is one muted sentence (lead-queue.tsx:710-717). It gives no per-filter 'remove X to see N' guidance and no illustration or CTA.
- `'use no memo'` on LeadTable (LeadTable.tsx:76) plus inline arrow props means every keystroke in the reject or bulk rationale inputs re-renders all mounted rows. Move panel state into the panel components.
- Skeleton draws 9 columns x 6 rows at min-width 920px while the table has 15 columns at ~1,600px (lead-queue.skeleton.tsx:20-22; components.css:1373). This causes a layout jump on load.
- The Signal column is sortable but shows bars only, with no number. Turning showConfidence off leaves an empty column and header (ConfidenceMeter.tsx:17-18; LeadTable.tsx:1049).
- ASSIGNED filter lists raw emails instead of display labels (lead-queue.tsx:648). Hero chips print raw tokens like `approval = approved` (434-437) and cap at 3 with a non-interactive '+N filters' chip (439-470).
- Bulk-bar assignee is a native <select> (LeadTable.tsx:1144-1155) beside custom FilterSelect dropdowns, so one surface carries two dropdown idioms.
- Status feedback uses inline `.table-success/.table-error` strips (LeadTable.tsx:988-992, 1240-1247) instead of a shared toast system. There is no Undo for assign or disposition.
- Row state is split across AppContext approvals, local salesOverrides and the query cache (LeadTable.tsx:125-133) instead of `useMutation` with optimistic cache updates. Overrides are lost on remount until refetch.
- Column headers have no definitions or tooltips for 'Rate Δ (bps)', 'Signal' or 'Last touch', although a glossary route exists.
- No row hover quick-actions. `<tr onClick>` is mouse-only (LeadTableRow.tsx:74-78), with the keyboard path via the borrower button only.
- FilterSelect never scrolls the focused option into view inside its 280px max-height menu (FilterSelect.tsx:61-64; components.css:4968). Long state and lender lists lose the keyboard cursor.
- Export filename `mip-leads-YYYY-MM-DD.csv` (LeadTable.tsx:813-814) has no time or filter slug, so same-day exports overwrite each other in Downloads.

**Limitations:** - **Screenshots:** the fixture-screenshot agent had produced no PNGs or manifest by the time I finished. ui-shots held only harness files and a discovery.json full of 500s, so I did no visual review of the rendered Lead Queue or Audit Explorer in either theme.
- **Headless measurement:** to verify tables-01 I rendered a static 15-column table using the repo's real tokens.css and components.css in headless Chromium over file://, with no port bound. That measures column geometry exactly, but it is not the live React render.
- **Row height:** the 86px row height and 360px preview figures are the repo's own virtualizer estimates (LeadTable.constants.ts), not measured rows.
- **Not run:** I ran no test suites, no build, and no live-app or Databricks checks.
- **Not measured:** re-render cost under 'use no memo', and bulk-approve throughput beyond what the configured 120/min mutation budget implies.
- **Version and support checks:**
  - @tanstack/react-table 9.2.4, @base-ui/react 1.8.0 and nuqs 2.10.1 were verified with `npm view` on 2026-09-21.
  - CSS anchor-positioning Baseline status (Firefox 147, Safari 26) was verified by web search.
  - A `scroll-state()` support search was inconclusive, so no recommendation depends on it.
- **Stale screenshots:** the committed screenshots under docs/validation/screenshots date from 2026-04-22 and were not used.

---

## Data visualization and geography (`dataviz`) — grade C+

**Auditor:** The engineering underneath is disciplined: a server-binned scatter, URL-backed filters, honest-null disclosures and token-only CSS. The visible dataviz is well short of Stripe/Hex/Linear grade: the declared hero geography is a 51-polygon state map that becomes a tile grid after one click, the choropleth ramp and legend fail computed encoding checks in both themes, the flagship funnel collapses to a hairline at real magnitudes, and the Genie answer charts have no axes or tooltips.

**Verifier on the grade:** Fair, at most half a grade harsh: every visible defect reproduced (24-tile grid, 0.029 ramp step and 1.60:1 light-theme wash, 152/4/3/3/3 Sankey, axis-less Genie line chart), so C+ to B- is the honest band. The auditor did inflate impact (8 of 10 'high'), missed that the funnel's conversions span non-nested cuts, and cited a branch whose map and CSS are already split on origin/main.

**Strengths (keep these)**

- **Equity x rate-spread scatter is architected correctly for scale and privacy** — frontend/src/routes/analytics.equity-scatter.tsx:251-316 renders server-side density bins; raw borrower points load only after zooming into a cell, capped with honest returned-vs-matching copy (:336-346). Roving tabindex with arrow/Home/End traversal (:394-411). 24px hit areas via ::before (components.css:4713-4717). DOM budget MAX_SCATTER_POINTS=1200 (analytics.lib.ts:84). Score-band hues pass the CVD validator (deltaE 11.5 protan).
- **Filter and refresh discipline matches best practice** — One URL-backed filter row sits above every chart (frontend/src/routes/analytics.tsx:66-99, 245-305). keepPreviousData plus the `stable-refresh-region is-updating` hold-previous-render pattern replaces skeleton flash on refetch (analytics.charts.tsx:65-79). Deep links and multi-select were verified in docs/audits/analytics-endpoint-audit.md v3/v4.
- **Honest-data posture is built into the map** — Unknown values render as an em dash, never a fixture (USChoroplethMap.tsx:27-31, 364-385). The ZIP drill discloses hidden ZIPs and borrowers without a ZIP (:815-840). The tooltip states contactable vs addressable before the click (USChoroplethMapTooltip.tsx:68-77). Out-of-footprint states still get an explanatory hover (:582-598).
- **Lean geometry pipeline with no runtime projection library** — frontend/src/components/mortgage/USStateMapData.ts:6-20 lazy-imports topojson-client plus the pre-projected us-atlas states-albers-10m.json (82 KB on disk) into its own chunk, memoised in one promise and shared with GenieMapChart. 51 SVG paths has no performance ceiling; the brief's 3,200-county concern is moot because the county level was removed (USChoroplethMap.tsx:49-60).
- **Motion is restrained and accessibility-gated** — One-time entrances are keyed on structure, not volatile counts, via useFirstAppearance (analytics.charts.tsx:202-205; KpiCard.tsx:80-81). Every animation is disabled under prefers-reduced-motion (components.css:3596-3598, 3764-3766, 4529-4534). No number-rolling tickers (KpiCard.tsx:14-19).


### `dataviz-01` Hero geography stops being a map after one click: ZIP level is a 24-tile grid with no zoom or pan

`high` · `gap` · effort `L` · corrected

- **Evidence:** frontend/src/components/mortgage/USChoroplethMap.tsx:35 `ZIP_TILE_CAP = 24`; :741-812 renders ZIPs as a sorted `.zip-tiles` button grid, not geometry; :49-60 records the county rung removed. grep wheel|zoom|pinch in the component: none. Screenshot dark__state__map-zip-drill.png: 12 tiles in an empty 520px stage.
- **Problem:** The declared hero surface is a 51-polygon state map; drilling loses all spatial context (adjacent ZIPs, metro clusters), hides every ZIP past the densest 24, and offers no zoom, pan, or labels. The prototype draws ZIPs as `.map-region` polygons.
- **Recommendation:** Ship mapshaper-simplified per-state ZCTA TopoJSON, committed and generated by a tools/ script (ZCTAs are national-only since 2020, so assign to states via the Census relationship file), under frontend/public/geo/zcta/, lazy-fetched on drill. Fit the viewBox to the populated ZCTAs' extent rather than the whole state, because live coverage is a fraction of each state's ZIPs and a statewide view would repeat the 'mostly unfilled polygons' failure that killed the county rung; draw unpopulated neighbours as muted context. List data ZIPs that have no ZCTA polygon in the existing reconcile note. Keep tiles as the fallback when a state file is absent and as a list toggle. Implement pan/zoom on the SVG viewBox; drop the MapLibre/PMTiles option. On origin/main the map is already split (USChoroplethMapZipLevel.tsx, useChoroplethLiveFacts.ts), so build there, not on the 1,044-line branch copy.
- **Tech:** SVG + installed topojson-client; Census ZCTA boundaries; optional d3-zoom 3.0.0. MapLibre GL JS 6.10 + PMTiles 4.5 (npm-verified) only for a basemap: self-hosted ESM worker via setWorkerUrl.
- **Verifier:** Confirmed ZIP_TILE_CAP=24, the tile grid, no zoom/pan, and prototype L1802-1835 drawing ZIP polygons. But live ZIP coverage is partial (IL 212 ZIPs; 677 rollup rows), ZIP is not ZCTA, and a PMTiles basemap cannot meet the 10 MB per-file cap or connect-src 'self'.
- **Constraint conflict:** The optional MapLibre + PMTiles basemap is infeasible: a US basemap archive exceeds the Databricks Apps 10 MB per-file limit and external tile hosts are blocked by connect-src 'self'. Committing roughly 15-25 MB of all-state geometry (needed because coverage must follow the share dynamically) is an owner decision.

### `dataviz-02` Choropleth ramp, classes and legend fail basic encoding checks in both themes

`high` · `defect` · effort `M` · corrected

- **Evidence:** frontend/src/design-system/components.css:3754 paints `.has-data.lvl-1` at 24% accent, :3756 lvl-2 at 30%, legend swatch :3894 shows 15%. Validator on painted ramps: light deltaL 0.009/0.038/0.036, top class 1.63:1 on white; dark lvl-1/2 deltaL 0.03. USChoroplethMap.tsx:983-986 legend says only Lower/Higher.
- **Problem:** Two of four classes are indistinguishable, the legend swatch mismatches the map, light theme is a pale wash, and rank quantiles over six states (USChoroplethMap.utils.ts:91-106) always paint the top two alike whatever their magnitudes. No numeric breaks; no state labels.
- **Recommendation:** In-contract first: make painted lvl-1 and the legend lvl-1 swatch the same step, re-space the four accent mixes so every step clears about 0.06 OKLab L in both themes, and give light theme a data-safe accent value (the repo's --accent-ink is the precedent). Also reconcile the ZIP level: tiles use a 6/12/20/30% alpha ramp (components.css:4002-4005) that the shared legend never shows. Then additive: print real break values in .map-legend__range while keeping the Lower/Higher words, switch to a continuous sqrt scale below about 12 populated units (the 6-state quantile always paints the top two alike), and label populated states. Skip the hover change; hover is already stroke-only in effect. Record the ramp change as an accessibility deviation citing Prototype L863-866 and L1864-1871.
- **Tech:** CSS oklch() and color-mix() (Baseline 2023), relative color syntax (Baseline 2024); d3-scale 4.0.2 scaleSqrt/scaleQuantize for breaks; SVG text labels at path centroids.
- **Verifier:** Reproduced in OKLab: dark lvl-1/lvl-2 deltaL 0.029; light theme under the default data-accent=bright tops out at 1.60:1. Legend 15% vs painted 24% confirmed. Hover fill swap is dead CSS (later lvl rules win). Ramp and Lower/Higher legend are prototype contract.
- **Constraint conflict:** Replacing the accent-mixed 15/30/50/70 ramp and the Lower/Higher legend departs from design_files/Module 0 Prototype.html L863-866 and L1864-1871. Allowed only as a documented accessibility deviation; the original recommendation did not say so.

### `dataviz-03` Activation funnel Sankey collapses to a hairline at real portfolio magnitudes

`high` · `defect` · effort `M` · corrected

- **Evidence:** frontend/src/routes/analytics.lib.ts:435-444 scales node height linearly to the max stage with `minBarH = 3`. Live counts in docs/audits/analytics-endpoint-audit.md (5.16M, 135.52K, 4.35K, 23, 3) give 152, 4, 3, 3, 3 px. Fixture screenshot already shows stages 3-5 as slivers inside a 400px card.
- **Problem:** The flagship executive chart spends its area on one bar; stages two to five render 3-4px, so height stops encoding anything and the conversion story lives in 10px labels. Pipeline Metrics bars repeat the same linear scale.
- **Recommendation:** Re-form as equal-height stage columns where each stage is a share of ADDRESSABLE (the only guaranteed superset), drawn on a disclosed log or sqrt scale, with the absolute count as the hero figure and share of addressable beneath. Show a stage-to-stage conversion only where SQL guarantees nesting (Approved to Actioned), or first add genuinely nested counts to gold_funnel_snapshot_daily through the deploy script and chart those. Drop the proposed '97% fail the refi-economics screen' style annotation unless the nested counts exist. Apply the same scale fix to the Pipeline Metrics bars. Effort becomes L if the gold table changes.
- **Tech:** Pure SVG inside buildFunnelSankeyModel (analytics.lib.ts:426); d3-scale 4.0.2 scaleLog only if the log option is chosen. No new runtime dependency required.
- **Verifier:** Reproduced 152/4/3/3/3 from maxBarH 152 and minBarH 3. But gold_funnel_snapshot_daily.sql:84-91 computes stages as independent predicates, so a kept-versus-dropped-from-previous-stage funnel would assert nesting the data does not have.

### `dataviz-04` Map bypasses the app's resilience and URL-state patterns: silent zero, filter desync, no deep links

`high` · `defect` · effort `M` · confirmed

- **Evidence:** frontend/src/components/mortgage/USChoroplethMap.tsx:208-228 raw useEffect fetch; `.catch(() => setLiveStateFacts({}))`, so :415-421/:959 print 'Borrowers in selection 0' (degraded-home screenshot). No useWarmingUpRetry/useQuery (grep: none). routes/segment-intelligence.tsx:279 keeps mapSelection in useState; :854 'Clear geography' never resets the map's internal level (:112-128).
- **Problem:** On a cold warehouse the hero map shows a blank country and a confident zero; after getJson's three attempts it never retries. Clearing geography un-filters the table while the map stays drilled. Drilled views cannot be shared by URL.
- **Recommendation:** Move state/ZIP/overlay rollups onto useWarmingUpRetry with queryKeys (shared cache between Home and Segments), render WarmingUpBlock inside `.map-stage`, and show an em dash not 0 when unknown. Make the map controlled: `selection` prop plus `?geo_state=TX&zip=` search params, mirroring analytics.tsx:66-99, so Clear geography, back/forward and shared links all agree.
- **Tech:** @tanstack/react-query 5 (installed) through the repo's useWarmingUpRetry; react-router 8 useSearchParams; existing WarmingUpBlock component. No new dependency.
- **Verifier:** Verified raw useEffect fetch, catch sets {}, legend prints totalCount 0, no useWarmingUpRetry, Clear geography resets only parent state. Also mapStatus announces 'Geography rollups loaded.' after a failed fetch. On origin/main this logic sits in useChoroplethLiveFacts.ts, which eases the fix.

### `dataviz-05` Genie answer line and bar charts lack axes, units, hover and drill; the line chart truncates silently

`high` · `defect` · effort `M` · corrected

- **Evidence:** frontend/src/components/mortgage/GenieAnswerCharts.tsx:128-157 line chart has no y-axis, gridlines, values or hover, and `data.slice(0, 24)` truncates silently (bar chart discloses, :119-123). Bars format with toLocaleString/toFixed (:111-113) while tables use formatCell's currency (GenieAnswer.logic.ts:422-424). Fixed viewBoxes scale 11px text with container width (:63-66).
- **Problem:** The AI answer is the demo climax, yet its line chart cannot be read for any value, dollar measures lose their unit, the state map has no hover or numeric legend, and no chart mark drills into the lead queue.
- **Recommendation:** Fix standalone rather than waiting for the chart kit: y-axis with nice ticks, formatCell-based value formatting so money keeps its unit, a hover readout, a disclosed 24-point truncation matching the bar chart's note, and text sized by container instead of a scaling viewBox. Make bar and state marks links through the existing genieCellHref so a drill carries the answer cohort and the same addressable-versus-contactable disclosure the map tooltip uses; do not hand-build /lead-queue?state=, which lands on a much smaller contactable count. Charts remain a presentation of Genie's own governed rows (live-first). Observable Plot stays an owner decision.
- **Tech:** Shared SVG kit; optional @observablehq/plot 0.6.17 (npm-verified; emits inline styles, permitted by the current `style-src 'unsafe-inline'`) as its own lazy chunk.
- **Verifier:** Line chart confirmed axis-less, hover-less, silently sliced to 24; bar values bypass formatCell. Overstated: the map has a numeric top-6 legend and table cells already drill through lib/genieCellLinks.ts. M only holds if not blocked on the L-sized kit.
- **Constraint conflict:** Observable Plot option: its default look is not the prototype's and must be restyled to tokens, and its d3 dependency weight must pass tools/check_frontend_budgets.mjs. Owner decision, lazy chunk only.

### `dataviz-08` 'Why now' is never visualized: no portfolio time series, and the FRED rate feed is never charted

`high` · `wow` · effort `L` · corrected

- **Evidence:** frontend/src/routes/analytics.tsx:227 lede promises 'portfolio trends', yet the only time axis is evidence events (analytics.sections.tsx:571). jobs/fred_rates_ingest.py:1,64 lands MORTGAGE30US weekly since 2021 in silver.market_rates_weekly; grep market_rates|MORTGAGE30 in frontend/src finds only evidence-source labels; grep in backend/api and repositories: none. gold.funnel_snapshot_daily is daily.
- **Problem:** The product question is 'who, why now, what offer', but the macro driver of 'now' (market rate against the book's note rates) and the funnel's movement over time are invisible. Executives get a snapshot, not a story.
- **Recommendation:** Lead with the certain half: weekly MORTGAGE30US against the book's note-rate band (median and IQR precomputed in a gold table built by scripts/deploy.sh, never a 5M-row percentile per request). Add the in-the-money series beneath only once funnel_snapshot_daily holds enough dates; until then label it 'history since <first snapshot>'. One GET /api/v1/analytics/rate-window on the TTL-cached repository with WarmingUpBlock for the degraded state and an EvidenceChip citing silver.market_rates_weekly. Two stacked panels on one x-axis, never dual-axis. Skip brush-to-zoom in v1.
- **Tech:** Shared SVG kit (d3-scale scaleTime, d3-shape line/area); ~300 weekly points is trivial for SVG, so uPlot 1.6.32 is unnecessary. FastAPI + existing databricks_analytics repository.
- **Verifier:** Confirmed MORTGAGE30US lands weekly since 2021 with a committed seed and no frontend or API surface charts it. Narrowed: Home KPI cards already draw 7-point funnel_snapshot_daily sparklines, and snapshot history may be too short for the second panel.

### `dataviz-v1` Funnel Sankey prints stage-to-stage 'conversion' across independent population cuts

`high` · `defect` · effort `M` · verifier-added

- **Evidence:** sql/transformations/gold_funnel_snapshot_daily.sql:84-91 counts in_the_money, high_opportunity and approved as independent SUM(CASE) predicates. analytics.lib.ts:452 divides each by the previous stage; analytics.charts.tsx:255 announces '% from previous stage'; analytics.sections.tsx:101 says 'Narrowing path'. docs/audits/analytics-endpoint-audit.md:146-167 already ruled them independent cuts.
- **Problem:** The percentages are ratios of unrelated counts, not conversions: high-opportunity borrowers need not be in the money, and approved borrowers need not be high-opportunity. An evidence-first product is showing a derived number no table supports.
- **Recommendation:** Either add genuinely nested counts to gold_funnel_snapshot_daily (itm; itm AND high-opportunity; approved within that; actioned) through the deploy script and chart those, or relabel every stage as share of addressable and keep a conversion label only for Approved to Actioned, which SQL nests. Update the aria-label and panel note to match.
- **Tech:** Databricks SQL gold table plus the existing analytics repository; pure SVG in buildFunnelSankeyModel; no new dependency.

### `dataviz-06` Distribution charts plot but do not say anything: histograms drawn as polylines, no thresholds marked

`medium` · `upgrade` · effort `M` · corrected

- **Evidence:** frontend/src/routes/analytics.sections.tsx:120-126 and :211-218 feed `score_distribution` and `rate_spread_histogram` bins to LineChart (analytics.charts.tsx:389, one polyline). No threshold marks, though lib/opportunityScore.ts:20 pins HIGH_OPPORTUNITY_THRESHOLD = 75 and segment copy gates on 75 bps / 15% equity. Screenshot: seven points joined by straight lines.
- **Problem:** Binned counts joined by a line imply values between bins and hide bin width. The business thresholds that define 'in the money' and 'high opportunity' are invisible, so viewers cannot see how much of the book sits above them.
- **Recommendation:** Render both as histograms with a labelled threshold rule and bars at or past the threshold in accent. Import the score threshold from lib/opportunityScore.ts, but take min_spread_bps and min_equity_pct from the API payload (fn_in_the_money receives them from the application layer) so a lender-tuned rule cannot drift from the chart. The '4,351 borrowers score 75+' annotation must come from the governed stage count, not from re-summing bins. Add the same guides to the scatter overview using the existing .analytics-scatter__guide class.
- **Tech:** Hand-rolled SVG rects on the shared kit; thresholds imported from lib/opportunityScore.ts so chart and scoring can never drift. No dependency.
- **Verifier:** Confirmed both distributions feed a polyline and `.analytics-scatter__guide` is unused. Bins floor to 5 points and 25 bps, so 75 sits on an edge. But spread and equity thresholds are application-supplied parameters, not constants in lib/opportunityScore.ts. Upgrade, not high.

### `dataviz-07` No shared chart foundation: awkward ticks, fixed heights, three tooltip idioms, zero data-change transitions

`medium` · `upgrade` · effort `L` · corrected

- **Evidence:** frontend/src/routes/analytics.lib.ts:199-208 makeTicks divides min-max evenly, so the Executive y-axis reads 24.1K/18.08K/12.05K/6.03K (screenshot). Tooltips: custom in LineChart, none in DailyEvidenceLineChart (analytics.charts.tsx:445-517), native `title` in scatter (analytics.equity-scatter.tsx:298,465). grep ResizeObserver|ViewTransition in src: none; `.analytics-bars__fill` has no transition (components.css:4433-4440).
- **Problem:** Each chart re-implements scales and chrome, so quality varies panel to panel and filter changes snap instead of animating. Awkward tick values are the fastest tell of a home-made chart to a buyer used to Stripe or Hex.
- **Recommendation:** Step 1 (S): replace makeTicks with a 1-2-5 nice-ticks function, no dependency, and add a CSS transition on .analytics-bars__fill width. Step 2 (L): extract components/charts/ with ChartFrame, one shared Tooltip and a table twin; adopt d3-scale 4.0.2 only if time or log scales are needed, checked against the frontend byte budget. ViewTransition needs a React 19.2.8 to 19.3.0 bump and startTransition around filter updates; treat it as progressive enhancement and prefer per-mark CSS transitions for bars, since a view transition cross-fades snapshots rather than morphing marks. Keep hand-rolled SVG, BEM and tokens.
- **Tech:** d3-scale 4.0.2, d3-array 3.2.4, d3-shape (ESM, tree-shaken); or @visx/scale+axis 4.0.0 (React 19 peer verified). React 19.3 ViewTransition stable 2026-09-09; same-document View Transitions Baseline since 2025-10-14.
- **Verifier:** makeTicks even division, three tooltip idioms, no bar transition confirmed. React 19.3.0 (2026-09-09) did stabilise ViewTransition, but the app pins 19.2.8. Charts already resize through preserveAspectRatio none plus HTML ticks, so ResizeObserver is not the gap. Refactor value is medium.

### `dataviz-09` No data-viz palette: data colour is the user-switchable UI accent, and the segment palette fails validation

`medium` · `defect` · effort `M` · corrected

- **Evidence:** frontend/src/design-system/tokens.css:36,40: `--seg-itm` #5CE1E6 vs `--seg-equity` #66C5FF score deltaE 9.5 normal-vision (validator floor 15); `--seg-equity` equals `--accent` (:183). Lines, bars, Sankey and map all fill from `--accent` (components.css:4666, 4439, 4475, 3754-3758), which `[data-accent="red"]` (tokens.css:247) turns #FF3621. grep `--viz-|--chart-` in tokens.css: none.
- **Problem:** Two headline segments are near-twins in colour, one segment is literally the interactive accent, and a Console accent switch repaints every chart and the choropleth red, colliding with the danger semantics sparklines use. Light-theme accent lines sit near 1.9:1.
- **Recommendation:** Smallest defensible deviation first: add a per-theme-and-accent --accent-data token following the existing --accent-ink precedent (tokens.css:272-275) and point chart strokes, bar fills, Sankey and the map ramp at it, so light theme and the red accent stop producing 1.9:1 or danger-coloured data marks. Re-step only --seg-itm and --seg-equity until they clear deltaE 15. Pin both with a Vitest palette test. A full --viz-cat / --viz-seq layer that ignores the Console accent switch is an owner decision, because accent-tinted data marks are prototype behaviour.
- **Tech:** CSS custom properties with oklch() (Baseline 2023); OKLab deltaE plus CVD simulation in a Vitest unit test; optional SVG pattern textures for forced-colors and print.css.
- **Verifier:** Reproduced OKLab deltaE 9.46; --seg-equity equals dark --accent; default data-accent=bright overrides the light accent, giving 1.91:1 strokes on white. But these hexes, the accent-mixed map and [data-accent] switching are prototype contract (index.html:34-38,164); --accent-ink is an existing precedent.
- **Constraint conflict:** Changes prototype token values and decouples data marks from the prototype's [data-accent] behaviour. Permitted only as a documented accessibility deviation with owner sign-off.

### `dataviz-10` Charts and map are opaque to keyboard and screen-reader users

`medium` · `gap` · effort `M` · confirmed

- **Evidence:** frontend/src/routes/analytics.charts.tsx:358,467 wrap charts in role="img" with labels like 'Borrowers by Opportunity score' and an aria-hidden hover layer (:395-398); grep figure|figcaption|desc|aria-describedby across chart files: none. USChoroplethMap.tsx:577-580: 51 paths each tabIndex=0 with aria-label = state name only; no focus tooltip (grep onFocus: none).
- **Problem:** A screen-reader user learns a chart exists but none of its values; a keyboard user tabs through 51 states and never sees the hover card. The scatter already solves this with roving tabindex, so the app is inconsistent.
- **Recommendation:** Give ChartFrame a 'View as table' toggle rendering the same rows through the existing DataTable, plus a generated one-sentence summary as aria-describedby. On the map: roving tabindex with arrow-key traversal limited to populated states, value in each aria-label, show `.map-tip` on focus, and Esc to go up a level. Reuse handleScatterMarkerNavigation (analytics.equity-scatter.tsx:394-411).
- **Tech:** WAI-ARIA roving tabindex and aria-describedby; native table; @axe-core/playwright (already a devDependency) to pin the behaviour in e2e.
- **Verifier:** Verified role=img wrappers with aria-hidden hover, no figure/desc/aria-describedby in chart files, 51 tabIndex=0 paths labelled by name only, no onFocus or Esc handler. Sankey nodes and Genie tables are already accessible, so scope is the distribution charts, daily line and map.

### `dataviz-v2` Evidence-per-day line plots dates on an ordinal index axis with no hover

`low` · `defect` · effort `S` · verifier-added

- **Evidence:** frontend/src/routes/analytics.charts.tsx:451-452 positions each point at idx/(rows.length-1); ticks come from categoricalTickIndexes (:460). backend/services/repositories/databricks_analytics.py:498-507 groups by TO_DATE(timestamp) with no calendar fill, so days without events are absent rather than zero.
- **Problem:** Any missing day compresses the x-axis and the polyline draws a straight segment across the gap, so slope and spacing misstate the trend. No hover readout exists to recover the real dates or values.
- **Recommendation:** Position points by date, break the line or plot zero where days are missing, and reuse the LineChart hover readout. Fold this into the shared chart kit's time axis so the proposed rate-window panel inherits it.
- **Tech:** Plain TypeScript date arithmetic in SVG, or d3-scale scaleTime if the chart kit adopts it.

**Overflow (titles only, not verified)**

- Analytics > Geography tab contains no map at all: two bar lists plus a ZIP table (analytics.sections.tsx:146-194); state bars navigate away to /lead-queue instead of cross-filtering the tab via the existing `states=` param.
- Segment Intelligence stacks the map BELOW the ranked table in a single-column grid (components.css:4843-4845), so clicking a state changes a table that is off-screen at 1440x900; consider side-by-side or a sticky mini-map.
- KPI delta slot misused on Analytics: green up-arrow on non-trend captions such as 'Offer path assigned' (analytics.sections.tsx:93-94); Analytics KPI cards pass no `source` (no evidence chip, contrary to the 'every KPI' contract) and no `trend` sparkline.
- Sparkline is aria-hidden, auto min-max y-scaling exaggerates tiny changes, and it has no endpoint dot or hover scrub (Sparkline.tsx:31-33, 63).
- Map tooltip has no vertical clamp (USChoroplethMapTooltip.tsx:33-37 with translate -100% at components.css:3797), so it clips for northern states when the map is near the viewport top.
- Scatter zoom viewport is local state (analytics.equity-scatter.tsx:69): not deep-linkable; zoom is single-cell only with no brush and no step-back, only Reset.
- DailyEvidence chart sums away signal_type (analytics.lib.ts:261-265) and draws zero-filled batch days as a line with no hover; stacked bars by top signals plus Other is the honest form.
- Approval funnel and Sales ops tabs contain zero charts: the per-loan-officer table (analytics.approval-funnel.tsx:210-262) wants inline stacked micro-bars, and the outcomes sentence (analytics.sales-ops.tsx:254) wants a segmented bar.
- Borrower 360 has no visual encoding of the refi-economics check (spread vs 75 bps, equity vs 15%): add a bullet/threshold gauge and a note-rate vs market-rate mini timeline; TriggerTimeline is an unscaled list with relative dates only (TriggerTimeline.tsx:17-31).
- Wow candidates for the map: bivariate choropleth (borrower volume x average opportunity score), per-segment small-multiple maps, compare-two-states mode, and a national inset mini-map while drilled.
- 'Exact figures' ScopeChip truncates to 'Exact ...' beside the long provenance note in Pipeline Metrics (both theme screenshots; `.chip__label` ellipsis at components.css:584-588).
- frontend/public/us-counties.json (770 KB) is no longer fetched by any frontend code (grep frontend/src: none); it ships publicly only because backend/services/county_names.py:18 reads it. Move it out of public/.
- FunnelSankey and GenieBarChart set px font sizes inside scaling viewBoxes (components.css:4478-4493; GenieAnswerCharts.tsx:80,106), so label size drifts with container width.
- Map library owner decision: MapLibre GL JS 6.10 + PMTiles 4.5 is feasible under the current CSP only with a self-hosted ESM worker (setWorkerUrl) and an explicit worker-src 'self'; Starlette 1.3.1 serves Range requests, but the Databricks Apps 10 MB per-file cap rules out a bundled national archive (UC Volume + Range proxy needed). deck.gl 9.4 is unjustified: point-level coordinates should not cross the wire.

**Limitations:** Read-only and static: I did not run the app, a build, or tests, so hover/render cost, animation feel and real bundle deltas for d3 modules are unmeasured (size figures are estimates). Screenshots are fixture-driven: only the Executive analytics tab was populated, every other analytics tab was captured in its 503 state, no Genie answer state exists, and ZIP tile counts rendered as dashes because of a fixture mismatch, so the Genie-chart, Geography/Economics/Segments/Signals-tab and scatter findings rest on code reading, not pixels. Live magnitudes for the Sankey math come from docs/audits/analytics-endpoint-audit.md (May 2026), not a current query. Palette results come from the dataviz skill's OKLab validator run on token hex values and on color-mix results I computed in sRGB (the CSS mixes in oklab, so painted values differ slightly; the ordering of failures should hold). ZCTA file sizes per state were not generated or measured. Web-verified: npm versions (maplibre-gl 6.10.0, pmtiles 4.5.0, @observablehq/plot 0.6.17, @visx/xychart 4.0.0 peers, uplot 1.6.32, echarts 6.1.0, d3-scale 4.0.2, deck.gl 9.4.0), MapLibre 6 strict-CSP worker guidance, Databricks Apps 10 MB file cap, Starlette Range support, React 19.3 ViewTransition stable, and View Transitions Baseline date; not verified: exact gzip cost of the recommended d3 modules. Machine load was very high (a repo-wide grep timed out at 120 s), so searches were scoped to frontend/src, backend and tools.

---

## Conversational AI: Genie and agent UX (`genie`) — grade C+

**Auditor:** Governance, proof and capability honesty are A-grade, and the progress rail reflects real Genie status rather than a timer. The conversational mechanics trail the 2026 bar. There is no stop, regenerate or edit. Genie gets no page context. Refusals dead-end. Tables truncate silently. The hero deep-research turn also waits about two minutes behind an 'Answer ready' label inside one blocking POST.

**Verifier on the grade:** Fair, at most marginally harsh. All ten findings reproduce at the cited lines. A B- is arguable because history, feedback, pins, the live SQL preview and the shared thread store already exist. C+ still holds because the hero deep turn waits up to 200s behind a mislabelled 'Answer ready', closing the panel or pressing Esc kills the running turn, and refusals dead-end.

**Strengths (keep these)**

- **Progress is driven by real backend stage events, not a client timer** — backend/services/genie_progress.py:59-73 maps the Genie Conversation status enum to a server-owned stage vocabulary. Lines 217-224 disclose mid-flight SQL only when the trust policy would also disclose it. GenieProgress.tsx:96-111 keeps the rail monotonic, and public process steps render live.
- **Governed actions use a real human-approval pattern** — GenieAnswerActions.tsx:72-100 requires Run then Confirm, with cohort preview chips (ZIPs, states, segments, bound borrower count). GenieChat.tsx:339-341 echoes the audit event id. Tokens are HMAC-bound to actor, cohort and question per docs/audits/ai-genie-safety-audit.md section 7.
- **Honest capability labelling; no Agent Framework or MLflow over-claiming** — routes/ask-genie.growth-run-card.tsx:90-117 labels execution mode and trace kind from the response, with governance chips 'Not provisioned', 'Not attached' and 'Roadmap'. GenieAnswer.tsx:167-182 keeps the native-visualization marker neutral and never renders a chart from it.
- **Floating panel ergonomics are already strong** — useGenieWindow.ts provides 8-direction pointer resize, arrow-key resize, drag-to-undock, edge snap, re-dock, persisted size and position, and viewport clamping. GenieChat.tsx:385-392 uses correct non-modal dialog semantics with focus return. lib/genieConversationStore.ts shares one tab-scoped thread between the panel and /ask-genie.
- **Summary-first sectioned answers with deep proof and drill-through** — GenieAnswer.sections.tsx:186-228 renders the verified Summary, then titled sections that each carry their own rows and chart plan. GenieAnswerProof.tsx shows trust, rows, latency, freshness, filters, known gaps, SQL and Catalog Explorer links. lib/genieCellLinks.ts links row cells to Borrower 360 and Lead Queue using the answer's own cohort filters.


### `genie-01` Deep-research and verification run inside one blocking POST while the rail claims 'Answer ready'

`critical` · `defect` · effort `XL` · corrected

- **Evidence:** frontend/src/lib/genieAsk.ts:113-123 stops polling at Genie `terminal`, then awaits one `genieComplete` POST. backend/services/repositories/databricks_genie.py:345-352 runs the 7-10-turn sweep inside it; databricks_genie_sweep.py:128-133 records the ~300s gateway 504; backend/services/genie_progress.py:66 labels that whole wait 'Answer ready — verifying and formatting'.
- **Problem:** On the hero deep question the rail completes in ~20s, then shows 'Answer ready' for 90-200s with only a ticker. No stage events, no partial sections, no ETA; overruns become a gateway 504. Single turns hide guard scans there too.
- **Recommendation:** Phase 0 (S): the submit response returns `deep` and a typical duration, and the rail relabels the wait 'Deep research: running governed sub-analyses' instead of 'Answer ready'. Phase 1: turn completion into a server-side job behind the existing 1.5s poll, with server-owned stage events. Reveal a section only after it passes its own output-policy check and the section floor is met, or keep it as labelled partial research. Keep the audit write and action-token issuance single-shot and idempotent. Expire orphaned jobs on app restart. Never stream unverified tokens.
- **Tech:** FastAPI background job plus Lakebase job table (migration reachable from deploy.sh); keep the 1.5s short-poll, Databricks community's accepted async-polling pattern (verified, 2026-03 answer); SSE only after an ingress spike.
- **Verifier:** Verified genieAsk.ts:113-123, databricks_genie.py:345-352, the sweep budget comments :128-133 and progress label :66; no deep/ETA copy exists in the frontend. But the sweep aborts below a 3-section floor (sweep.py:716), so streamed sections could vanish, and job-ifying the audited completion tail is XL.
- **Constraint conflict:** Incremental sections must not bypass the section floor or the rows-before-prose output policy. The job table needs a Lakebase migration reachable from deploy.sh.

### `genie-02` Closing the panel or pressing Esc anywhere silently destroys the in-flight turn and the question

`high` · `defect` · effort `M` · corrected

- **Evidence:** frontend/src/components/layout/AppShell.tsx:217 unmounts GenieChat when closed; GenieChat.tsx:98-103 aborts the turn on unmount and :181-183 never persists an in-flight question. GenieChat.tsx:192-196 and frontend/src/hooks/useFocusTrap.ts:47-51,82 both bind window Escape without coordination.
- **Problem:** Turns last 20-200s and the panel covers the page, so users close it. Question, progress and answer vanish silently; the submitted Genie message is orphaned. One Esc in the proof or evidence drawer also closes the panel.
- **Recommendation:** Mount GenieChat on first open and never unmount it, so closing no longer aborts the turn. The prototype CSS `.genie:not(.is-open)` and the component's own `is-hidden` FAB already support this; drop AppShell's duplicate FAB. Add a running ring and an 'answer ready' badge on `.genie__fab` as a documented BEM extension. Add one dismiss stack shared by useFocusTrap consumers and the panel so only the top layer consumes Escape; CloseWatcher stays a progressive enhancement. Add a module store only if turns must survive leaving /ask-genie.
- **Tech:** React 19 useSyncExternalStore; CloseWatcher (Chrome 126, Firefox 149, no Safari; verified, not Baseline); FAB badge is a documented BEM extension of the prototype's `.genie__fab`.
- **Verifier:** AppShell.tsx:217 conditional mount, the unmount abort at GenieChat.tsx:98-103, the in-flight persist gate :181-183, and two uncoordinated window Escape listeners all verified. CloseWatcher shipped in Firefox 149; Safari has none. components.css:2519 already supports a mounted-but-closed panel, so keep-mounted beats a new store.

### `genie-03` No Stop, Regenerate, Edit-and-resend or recall; controls lock for up to five minutes

`high` · `gap` · effort `L` · corrected

- **Evidence:** Grep for stop|regenerat|ArrowUp in GenieChat.tsx, routes/ask-genie.answer-panel.tsx, GenieAnswer.tsx, GenieProgress.tsx: no hits; backend/api/genie.py has no cancel route. GenieChat.tsx:279,297 no-op while `typing`; answer-panel.tsx:296 disables Ask; GenieChat.tsx:245-268 renders failures as a bubble without Retry.
- **Problem:** Users cannot stop a mistyped 200s deep question; New thread and History are locked meanwhile; floating-panel errors dead-end; nothing can be regenerated or edited. Every benchmark assistant offers these.
- **Recommendation:** Ship the client-only slice first (S-M). Stop aborts, bumps the generation, restores the question to the composer and marks the bubble 'Stopped'. Add Retry on panel error bubbles, ArrowUp recall, and Edit that reloads the composer. Regenerate re-asks as a new Genie turn, because a Genie thread cannot rewrite its history. Add the audited server cancel flag only after genie-01's job exists. Unify the two lifecycles into one hook last.
- **Tech:** Existing AbortController (GenieChat.tsx:226); new audited POST /api/genie/message/cancel; shared hook replaces the duplicated controllers in GenieChat.tsx and routes/ask-genie.tsx:232-281.
- **Verifier:** Grep confirms no stop/regenerate/ArrowUp. backend/api/genie.py has seven routes and none cancels. GenieChat.tsx:279,297 no-op while typing. The route has Retry (answer-panel.tsx:324); the panel does not. The surfaces use different lifecycles (useWarmingUpRetry vs direct), and server cancel needs genie-01's job, so effort is L.

### `genie-04` Genie is blind to the page: empty context, identical starters everywhere, no 'Ask about this' entry points

`high` · `gap` · effort `L` · corrected

- **Evidence:** frontend/src/lib/api.ts:1813-1818 posts `{context: {}}`; backend/api/genie.py:268-276 discards the payload and returns `load_sample_questions()[:4]`. backend/services/genie_message_policy.py:22-24 accepts only question and conversation_id. AppContext.tsx:76 exposes `setGenieOpen(boolean)`; command/commandActions.ts:51-52 only opens the panel.
- **Problem:** The 'reachable from every page' panel knows nothing about the segment, state, county or borrower on screen. Starters never change, and no KPI, segment card, map region or lead row can launch a grounded question.
- **Recommendation:** Phase 1 (M, frontend only): `openGenie({prompt})` prefills the visible composer from curated per-route templates in reviewed vocabulary, so the prompt of record is exactly what the user sends and the guard scans. Add 'Ask Genie about this' entries, per-route starters and a palette 'Ask Genie: <text>' command. Phase 2 (L): an enum context model rendered server-side into a scope sentence that is guard-scanned, hashed and audited with the prompt, with a battery test over every state and county name. Avoid Ctrl/Cmd-J.
- **Tech:** react-router 8 useLocation/useSearchParams; Pydantic enum context model; per-route starters curated in genie/sample_questions.md; no new dependency.
- **Verifier:** api.ts:1813-1818 posts an empty context, genie.py:268-276 discards it, the request model takes only question and conversation_id, and grep found no prompt-carrying opener. Server-added scope text alters the token-verified prompt of record and must pass place-name-sensitive guards. Ctrl+J is browser Downloads on Windows.
- **Constraint conflict:** A server-composed scope sentence changes the token-verified prompt of record and must pass the fail-closed prompt guards. Place names are a documented guard false-positive source.

### `genie-05` Refusals dead-end: the reason arrives only as a free-text proof gap, there is no compliant rephrase, the question is cleared, and there is no false-positive report

`high` · `gap` · effort `M` · corrected

- **Evidence:** backend/services/genie_deterministic.py:177-194 builds refusals with no follow-ups or reason field (reason reaches only audit, :277). GenieAnswer.tsx:138-143 suppresses follow-ups and :365 hides feedback on untrusted sources; GenieChat.tsx:64 collapses all to 'Governed refusal'. docs/genie-deep-research-talk-track.md:293-295 says reasonable-question refusals are bugs to report.
- **Problem:** A refused lender sees a paragraph and a warning chip; the question is already cleared. Nothing offers a compliant rewording, links the reviewed vocabulary, or captures the false positive the team wants reported.
- **Recommendation:** Add a coarse `refusal_reason` enum limited to the families the refusal copy already reveals, so it does not become a guard oracle. Render a refusal card per reason: rephrase chips that tests pre-validate through protected_prompt_match, 'Edit question' restoring the prompt, and a /glossary link. 'This was legitimate' writes reason, question_hash and actor. Capturing the prompt text for triage is an owner decision.
- **Tech:** Pydantic Literal enum; curated rephrase bank per reason (no LLM authoring, no guard weakening); reuse backend/api/genie_feedback_routes.py idempotent pattern with question_hash only.
- **Verifier:** Six _refused_genie_response sites carry no follow-ups; feedback is hidden at GenieAnswer.tsx:365; there is one 'Governed refusal' label; talk track :293-295 verified. However, proof.known_data_gaps does carry a reason sentence, and outreach/source-gap/footprint paths attach sample follow-ups. Hash-only reports give counts, not triage cases.
- **Constraint conflict:** Persisting refused prompt text conflicts with the posture that refused prompts are never round-tripped or stored. Hash-only reporting complies but yields counts, not cases.

### `genie-06` Answer data is silently truncated and cannot be copied, exported, expanded or re-charted

`high` · `defect` · effort `L` · corrected

- **Evidence:** GenieAnswer.logic.ts:8-9 caps tables at 10 rows, 4 columns; GenieAnswer.sections.tsx:54 drops extra columns unannounced, :152-154 shows inert '+N more rows'. GenieAnswerCharts.tsx:119-122 promises 'full N rows in the table below'. GenieAnswerProof.tsx:110-114 SQL has no copy. Grep clipboard|csv|download in Genie files: no hits.
- **Problem:** An evidence-first product hides columns silently and makes a false promise about the table. Users cannot copy SQL or the answer, download rows, sort, expand, or toggle chart and table. These are baseline in Hex, Databricks Genie and ChatGPT.
- **Recommendation:** Slice 1 (S): fix the chart caption to the real cap, disclose hidden columns by name, and add Copy SQL and Copy answer. Slice 2 (M): add 'Show all rows/columns', virtualized only on the route, and an audited CSV download that reuses csvEscape from LeadTable.csv.ts for formula-injection safety. Defer sortable headers and chart-type switching. `.genie-answer__toolbar` is a documented BEM extension.
- **Tech:** navigator.clipboard.writeText (Baseline); Blob CSV download; existing @tanstack/react-virtual 3.13; `.genie-answer__toolbar` as a documented BEM extension; export event through the existing audit store.
- **Verifier:** Verified: caps at logic.ts:8-9, the silent column slice at sections.tsx:54, the inert '+N more rows', the false chart caption (MAX_BARS 12 vs table 10), no SQL copy, and an empty clipboard/csv grep. LeadTable.csv.ts already has injection-safe CSV escaping to reuse. The full toolbar plus sort and chart switching is L.

### `genie-10` Clickable proof for every number: link verified claims in Genie's prose to the rows that support them

`high` · `wow` · effort `XL` · corrected

- **Evidence:** backend/services/repositories/databricks_genie_numeric.py:107-138 already parses each numeric claim in Genie's prose and tests it against support values built from the rows (:264-330), but returns only the unsupported ones. GenieAnswer.markdown.tsx renders numbers as plain text; GenieAnswerProof.tsx lists SQL, not per-claim support.
- **Problem:** The product's unique asset, every figure checked against its rows before display, is invisible. Users get a 'trusted' chip; they cannot see which cell proves which number.
- **Recommendation:** Phase 1 (M): the verifier returns the verified and total claim counts; show an 'N of N figures verified' badge and list each figure with its derivation in the proof drawer. Phase 2: refactor the support values into a provenance map (cell, row count, sum, share) and resolve ambiguous matches by a fixed priority. The UI locates each figure by its raw token in the shipped text, not by offsets. Highlight direct cell matches only. Withheld figures stay withheld.
- **Tech:** Pydantic claims model on GenieMessageResponse; span-aware rendering in the markdown layer; reuse the existing evidence hover-card; no new dependency.
- **Verifier:** numeric.py:107-138 returns only unsupported labels. Support is a set[float] of tolerance variants (:264-330) with no provenance, so claims[] needs a set-to-map refactor across six call sites in a fail-closed module. Offsets drift after rewrites, and genie-07's library swap would complicate span rendering.

### `genie-07` Hand-rolled markdown renderer collapses numbered lists and prints headings and tables raw

`medium` · `defect` · effort `S` · corrected

- **Evidence:** GenieAnswer.markdown.tsx:106 parses only `**bold**` and backticks; :149 recognises only -, *, • bullets; :162-166 joins any other line into the previous paragraph, so '1. … 2. …' becomes run-on text. No heading, table, italic or link handling; GenieAnswer.markdown.test.tsx pins only bold headings and the source footnote.
- **Problem:** Narratives are model-authored and neither genie/instructions.md nor the sweep prompts restrict format, so ordered lists, `###` headings and pipe tables reach users as run-on text with literal symbols on the hero surface.
- **Recommendation:** First (S): extend the existing 200-line parser with ordered lists and ATX headings mapped to the `.genie-md-*` classes, and add tests for both. Tables should keep coming from governed rows, not prose. A library swap (markdown-to-jsx or react-markdown + remark-gfm, raw HTML off) is M and an owner decision given the nine-dependency posture. It would also complicate genie-10's claim-span rendering.
- **Tech:** markdown-to-jsx 9.10.3 (npm-verified, published 2026-09-15) or react-markdown 10.1.0 + remark-gfm 4.0.1 (npm-verified). Not streamdown 2.6, whose styled output conflicts with the BEM contract.
- **Verifier:** Parser verified: '1. a\n2. b' merges into one paragraph, and there is no heading or table handling. npm versions confirmed (markdown-to-jsx 9.10.3, react-markdown 10.1.0, remark-gfm 4.0.1). But sweep synthesis prompts do forbid headings (sweep.py:414,435,551), and a 30-line parser extension fixes it without a new dependency.

### `genie-08` Long answers fight the reader: forced scroll to the bottom, no section navigation, slow long threads

`medium` · `defect` · effort `M` · confirmed

- **Evidence:** GenieChat.tsx:174-176 sets scrollTop=scrollHeight on every message/typing change. GenieAnswer.sections.tsx:214-220 renders section titles as `<p>` with no outline or collapse. routes/ask-genie.answer-panel.tsx:384-391 renders all stored turns; talk track :66-67 warns 'a long transcript slows the page'. Grep content-visibility in components.css: no hits.
- **Problem:** A summary-first, 7-10-section answer lands scrolled to its end, and users reading earlier turns get yanked. Sections cannot be skimmed or collapsed; presenters are told to clear the thread for speed.
- **Recommendation:** Anchor the new question at the top of the scroller and auto-follow only when the user is near the bottom. Make section titles real headings with a sticky outline and collapsible bodies; collapse earlier turns to question plus summary. Apply content-visibility:auto with contain-intrinsic-size to off-screen turns.
- **Tech:** scrollIntoView({block:'start'}); `<details>` disclosure; content-visibility (Chrome 85, Firefox 125, Safari 18; Baseline Newly available, verified); h3/h4 semantics for screen-reader navigation.
- **Verifier:** Verified: the forced scroll at GenieChat.tsx:174-176, `<p>` section titles (sections.tsx:202,214), all stored turns rendered, the talk-track warning :66-67, and no content-visibility in src. content-visibility is Baseline 2024. The forced scroll is panel-only; the route renders newest-first. The store caps at 20 turns.

### `genie-09` The /ask-genie route buries the Genie composer under the agent console, and agent runs show no progress

`medium` · `upgrade` · effort `M` · corrected

- **Evidence:** frontend/src/routes/ask-genie.tsx:526-798 renders the Growth Agent surface (objective box, four sibling CTAs :553-590, workflow cards, custom builder, watchlists) before AskGenieAnswerPanel at :800. Run feedback is a button label ('Planning…' :561, 'Running…' :678). `latestGrowthRun` (:93) keeps one run, cleared on each action.
- **Problem:** Nav says Ask Genie; the page opens on an agent console whose 'Plan reviewed workflow', 'Compose plan' and 'Execute plan' differ opaquely, pushing the question box to roughly the 900px fold. Agent runs show no step progress and keep no history.
- **Recommendation:** Lead with the conversation: put the composer and thread first and move the workflows to an 'Automate' tab via a search param. This is an owner decision, since the route is titled 'Mortgage growth co-pilot'. Collapse the four CTAs into Plan, review card, Execute. Add a GET runs endpoint over the growth-agent ledger for history. Show an honest indeterminate state while running. Add step-by-step progress only if the server emits real step events through genie-01's job and poll.
- **Tech:** Search-param tab state (react-router 8); reuse `.growth-agent-timeline` with pending states; GET runs endpoint over backend/services/growth_agent_ledger_sql.py.
- **Verifier:** The growth-agent surface at ask-genie.tsx:526 precedes AskGenieAnswerPanel (:800); four CTAs at :553-590; feedback is label-only; growth_agent.py has no runs-list route. The prototype defines no /ask-genie page, so there is no contract conflict. Agent runs are single blocking POSTs, so a step-by-step timeline needs real server events.

### `genie-v1` Elapsed-seconds ticker sits inside the role=status live region, so the whole progress card is re-announced every second

`medium` · `defect` · effort `S` · verifier-added

- **Evidence:** GenieProgress.tsx:116-125 wraps ElapsedTicker (1s setInterval, :55-66) inside `role="status" aria-live="polite"`. role=status implies aria-atomic=true, and the same region holds the stage list, trace and the SQL `<details>` (:152-168), open by default on the route.
- **Problem:** For 20-200 seconds, screen-reader users get the progress region, including generated SQL on the route, re-queued every second. Real stage changes are drowned out.
- **Recommendation:** Move the ticker outside the live region, or mark it aria-hidden and expose elapsed time through an sr-only update throttled to about 30 seconds. Keep only stage-label changes inside the status region, and render the trace and SQL preview outside it. Add a test asserting the ticker is not a live-region descendant.
- **Tech:** ARIA live-region semantics (role=status implies aria-atomic); WCAG 4.1.3 Status Messages; no dependency.

### `genie-v2` Floating-panel composer and follow-up chips stay live mid-turn: a second ask silently aborts the running turn and leaves an unanswered bubble

`medium` · `defect` · effort `S` · verifier-added

- **Evidence:** GenieChat.tsx:614-631: the form submit and Ask button have no `typing` guard. :225 aborts the prior controller and :246 swallows it silently. `onFollowUp={ask}` (:537) is also live mid-turn. The route guards this (answer-panel.tsx:264,296).
- **Problem:** In the panel, Enter or a follow-up chip during a 200s deep turn silently destroys it and orphans the Genie message. Q1 stays on screen with no answer or 'stopped' marker. Panel and route behave differently.
- **Recommendation:** While a turn is in flight, require an explicit Stop before a new ask, or queue the next question. Disable follow-up chips while a turn runs. Mark a superseded question bubble 'Stopped'. Apply one rule to both surfaces and pin it with a GenieChat test. This pairs naturally with the genie-03 Stop control.
- **Tech:** Existing generation counter and AbortController in GenieChat; no new dependency.

**Overflow (titles only, not verified)**

- Composer power features: the floating panel uses a single-line <input> (GenieChat.tsx:625) for ~50-word deep questions. Upgrade to an auto-growing textarea with `field-sizing: content` (Baseline Newly available 2026-06, Firefox 152, verified). This departs from the prototype's `.genie__input input` and is justified by deep-research prompts. Add slash commands, @-mentions of reviewed segments/geographies/campaigns, and a prompt library.
- History menu (GenieHistoryMenu.tsx:91-124) has no search, rename, delete or date grouping. Its role=menu has no arrow-key navigation and no outside-click dismiss. Esc closes the whole panel instead of the menu.
- Pinned insights show a frozen figure with no as-of date: `pinnedAt` is stored (lib/pinnedInsights.ts:24) but never rendered (PinnedInsights.tsx:31-40). They also have no re-run, no open-thread link, and live only in localStorage (max 6).
- The floating panel has no pop-out or 'Open in Ask Genie' control. /ask-genie has no conversation deep link (no search-param handling in routes/ask-genie.tsx).
- Feedback is binary and hidden on refusals (GenieAnswerFeedback.tsx, GenieAnswer.tsx:365). Add closed-vocabulary thumbs-down reason codes, which are PII-safe.
- The conversation controller is implemented twice and has already diverged: GenieChat.tsx ask/newConversation/loadSession/runAction vs routes/ask-genie.tsx:232-281 and 485-512. NON_PERSISTABLE_SOURCES is re-declared at ask-genie.tsx:59-65.
- The Genie line chart has no y-axis, gridlines or hover values (GenieAnswerCharts.tsx:147-156). Bar and line charts have no tooltips, and the bar caption hard-codes fontSize literals.
- `genieComplete` has no client-side timeout. The 5-minute ceiling covers only the poll loop (lib/genieAsk.ts:27,88,123).
- The 'Native chart · Beta' chip (GenieAnswer.tsx:172-182) is a dead-end marker for business users. Move it into the proof drawer.
- The evidence drawer (z-index 41, modal scrim) opens over the floating panel (z-index 30, components.css:2051,2510). Consider auto-docking the panel to the left while a drawer is open.
- On /ask-genie the floating panel and the route can run simultaneous turns into one thread store (AppShell.tsx:217 mounts the panel on every route).
- Spike SSE or streamed fetch through the Databricks Apps ingress. Starlette 1.3.1's GZipMiddleware skips event-stream, but the BaseHTTPMiddleware stack and the ingress idle timeout are unverified, so stay polling-first until proven.
- The proof drawer shows a single raw-ms latency figure (GenieAnswerProof.tsx:37-40). A per-stage timing breakdown (Genie, warehouse, verification, rewrite) would explain slow turns.
- Section titles and 'Summary' are <p> elements (GenieAnswer.sections.tsx:202,214), so screen-reader users cannot navigate a ten-section answer by heading.

**Limitations:** No /ask-genie or floating-panel screenshots existed: the capture agent produced only /analytics shots, and both manifests show '/' as FAILED. Layout judgements, including the composer sitting near the 900px fold, are therefore estimated from DOM order and CSS min-heights, which is why genie-09 is medium confidence.

- **No live turn observed.** All timings (20-25s single turn, 90-200s sweep, ~300s gateway 504) come from the repo's own comments and talk track.
- **Databricks Apps ingress streaming is unverified.** The community accepted answer (community.databricks.com td-p/149966, 2026-03-08) cites ~30s idle limits and recommends async polling. Thread td-p/140801 reports a 120s stream freeze. The repo measured a 504 at ~300s. These conflict, so genie-01 stays polling-first.
- **Server-side cancel is unconfirmed.** I did not check whether the Genie Conversation API exposes one; genie-03 assumes at minimum an app-side cancel flag.
- **Bundle cost of the markdown libraries was not measured**, because install and build were out of bounds. Versions were checked with `npm view`.
- **Web-verified platform status:** field-sizing (Firefox 152, 2026-06), CloseWatcher (no Safari) and content-visibility. Sources disagree on whether content-visibility's Baseline date is 2024-09 or 2025-09; either way it is shipped in all engines.
- **No tests were run.** The double-Escape and unmount-abort defects were derived from code reading only.

---

## Accessibility (WCAG 2.2 AA and procurement readiness) (`a11y`) — grade C+

**Auditor:** Real investment shows (semantic virtualised lead table, skip links, reduced-motion, target-size gate, dual-theme axe), but the app would not pass a third-party WCAG 2.2 AA audit today: static page titles, single-heading pages, SR-silent filter dropdowns, mouse-only map data, light-theme focus ring at 1.91:1 and single-key approve shortcut are Level A/AA failures. The gates run nightly only, on default page states, so none of this is caught, and there is no ACR/VPAT.

**Verifier on the grade:** C+ is fair. All ten findings reproduced at the cited lines, and the auditor under-counted one Level A failure (the mouse-only state picker in Portfolio Builder). It also under-credited the PR-time Vitest ARIA tests and the existing --accent-ink token, which roughly cancels out. The main weakness is systematically optimistic effort: six of ten were sized too small for 1,000+ line components on an expired file-size allowlist.

**Strengths (keep these)**

- **Lead table semantics are genuinely best-in-class** — frontend/src/components/mortgage/LeadTable.tsx:842-858 puts aria-sort on the th (not the button); :993-1003 labelled focusable scroll region with aria-rowcount, rows carry aria-rowindex under virtualisation; indeterminate select-all at :1023-1035; keyboard row expansion pinned by frontend/tests/e2e/accessibility_procurement.spec.ts:205-264.
- **Reduced motion is covered end to end** — frontend/src/design-system/components.css:45-53 clamps every animation/transition under prefers-reduced-motion; JS entrances go through frontend/src/lib/usePrefersReducedMotion.ts; accessibility_procurement.spec.ts:266-304 asserts computed durations <= 1ms on shell, drawer and Genie panel.
- **Shell landmarks, skip links and nav state are correct** — frontend/src/components/layout/AppShell.tsx:163-174 skip links to a tabIndex=-1 main; conditional console skip link; Rail.tsx:42,56 and RouteNav.tsx:74 give labelled nav landmarks with aria-current; PageShell.tsx:46 guarantees one h1 per route.
- **Several widgets already follow the ARIA APG exactly** — EvidenceDrawer.tsx:265-282,325-350 tabs with roving tabindex, arrows, Home/End and aria-controls; CommandPalette.tsx:222-238 combobox with aria-activedescendant; analytics.equity-scatter.tsx:297-301,464 roving tabindex with full data in each point label; ConfidenceMeter.tsx:25-30 role=meter; ScoreBadge.tsx:14 text plus label so score is never colour-only.
- **Contrast and target size are treated as token-level contracts** — tokens.css:171-176,211-214 document deliberate --text-3 divergences from the prototype for AA; :261-275 --accent-ink per theme/accent; 24px floor tokenised (components.css:102-103,579,630,649) and enforced across six routes with a documented geographic-shape exemption (accessibility_procurement.spec.ts:72-108,182-195). Dark default theme passed every text pair I computed.


### `a11y-01` Light theme fails contrast on focus ring, selected evidence tab, warning text and listbox cursor

`high` · `defect` · effort `M` · corrected

- **Evidence:** frontend/src/design-system/tokens.css:345-349 focus ring = --accent; light + default bright accent is #66C5FF on #FFF, 1.91:1. components.css:2241 active drawer tab 1.75:1 (confirmed in light drawer screenshot). :1103, :1497, :2654 --signal-warning text 2.15:1. :3043 --text-4 metadata 2.3-2.9:1. :2140, :5002 option highlight 1.09-1.35:1.
- **Problem:** In light theme the keyboard focus ring, selected evidence tab, warning copy and listbox cursor are near-invisible. Non-default accents are worse: light+teal chip text 1.15:1, dark+navy links 2.06:1, red primary CTA 3.62:1. Axe never exercises these states.
- **Recommendation:** Point :focus-visible and .drawer__tab.is-active at the existing --accent-ink and add a dark+navy --accent-ink override; add --signal-warning-ink (prototype precedent: design_files/index.html:416 uses #B45309) and retire the per-selector light overrides; restore outlines on the three inputs; land the Vitest token gate over 2 themes x 4 accents. Put new CSS outside components.css (size allowlist expired 2026-09-15).
- **Tech:** CSS custom properties; two-layer :focus-visible ring (outline plus box-shadow); Vitest 4 token test. All Baseline widely available. Script: scratchpad/a11y/contrast.mjs (392 pairs, WCAG + APCA).
- **Verifier:** Hand-computed 1.91:1 (#66C5FF on #FFF), 2.15:1 warning, 3.62:1 red CTA, 2.06:1 dark+navy; components.css:2241 uses --accent. But --accent-ink (tokens.css:272-275) already exists for exactly this and is used at :2142 and :5004, so a new --focus-ring token is redundant.
- **Constraint conflict:** Changes the prototype focus-ring colour (design_files/index.html:211) in light theme; permitted as an accessibility deviation but must be called out in the commit with the prototype line.

### `a11y-02` Filter dropdowns are silent to screen readers; tabs and topbar search break the APG

`high` · `defect` · effort `M` · corrected

- **Evidence:** frontend/src/components/ui/FilterSelect.tsx:82-115: trigger + role=listbox with no aria-controls, aria-activedescendant or option ids; Space closes without selecting; 31 call sites (lead-queue 16, segment-intelligence 11). frontend/src/routes/analytics.tsx:230-243 and frontend/src/components/mortgage/BorrowerProofDrawer.tsx:99-112 tabs lack arrow keys and aria-controls. frontend/src/components/layout/Topbar.tsx:305-330 results close on input blur.
- **Problem:** Screen-reader users hear nothing while arrowing the filters that drive segment and queue building; keyboard users cannot reach topbar search results. Each widget type has one correct and one broken hand-rolled implementation, so behaviour varies by page.
- **Recommendation:** Extract the repo's working patterns into components/ui (focus-managed Listbox from MultiFilterSelect, arrow-key Tabs from EvidenceDrawer, combobox from CommandPalette) and reuse them in FilterSelect, StateMultiSelect, analytics and proof tabs, and topbar search; pin each with Vitest keyboard tests. Treat react-aria-components 1.21.1 (npm-verified) as an owner decision because of bundle cost.
- **Tech:** react-aria-components 1.21.1 (npm-verified 2026-09-21; stable, unstyled, tree-shakeable); alternative @base-ui/react 1.8.0. Headless only, so no departure from design_files.
- **Verifier:** FilterSelect.tsx:82-115 reproduces (no option ids or activedescendant; Space closes), 31 call sites counted, tabs lack arrow keys, search closes on blur. But working in-repo patterns exist (MultiFilterSelect analytics.sections.tsx:396-540, EvidenceDrawer.tsx:269, CommandPalette), so a new dependency is optional.
- **Constraint conflict:** Topbar ships in the initial chunk; tools/check_frontend_budgets.mjs gates initial JS at 126 KiB gzip with ~5% headroom and requires a named, measured bump. RAC pulls react-aria, react-stately and @internationalized/*.

### `a11y-03` Every product page has one heading, the same document title and silent route changes

`high` · `defect` · effort `M` · confirmed

- **Evidence:** grep '<h[2-6]' finds 0 in all eight product routes; 70 section titles are <div className="h-4"> (e.g. frontend/src/routes/portfolio-builder.governance.tsx:134). grep 'document.title|<title' in src: only an SVG; frontend/index.html:7 static title. frontend/src/app.tsx:65-69 re-keys routes without focus move or announcement.
- **Problem:** Every product page exposes a single heading, an identical tab title and silent navigation, so heading navigation, history, bookmarks and tab switching all degrade. Fails WCAG 2.4.2 (A) and 1.3.1 (A); weak on 2.4.6.
- **Recommendation:** Render React 19 native <title> from PageShell ('Lead Queue · Mortgage Intelligence Platform'); on pathname change focus the h1 and write the page name to a persistent sr-only polite region; add a SurfaceTitle component that emits h2/h3 while keeping the .h-4 class, then codemod the 70 divs.
- **Tech:** React 19 document-metadata hoisting (stable; repo is on 19.2.8), react-router useLocation. No new dependency, no visual change.
- **Verifier:** Zero h2-h6 in the eight product routes (only analytics and glossary have them); 62 div.h-4 found versus 70 claimed; index.html:7 static title, no document.title; app.tsx re-key has no focus or announcement. Checked react-dom 19.2.8 source: it adopts the static title element.

### `a11y-04` Hero choropleth: 51 tab stops, name-only labels, data in a mouse-only tooltip, no table view

`high` · `defect` · effort `L` · corrected

- **Evidence:** frontend/src/components/mortgage/USChoroplethMap.tsx:572-581: each of 51 state paths is role=button tabIndex=0 with aria-label={loc.name} only; counts, score and top segment exist only in the onMouseEnter tooltip (:588-624; grep onFocus: none). :762-771 ZIP tile is <button role="listitem">. No table alternative.
- **Problem:** The hero geography drill-down gives keyboard and screen-reader users state names without any data, costs 51 Tab presses to cross, and strips the button role from ZIP tiles. Fill tier is colour-only. Fails 1.1.1, 2.1.1, 1.4.1.
- **Recommendation:** Phase it. Slice 1 (S): data-rich aria-labels (count, avg score, top segment), tooltip on focus positioned from getBoundingClientRect, ZIP buttons wrapped in li. Slice 2: roving tabindex for states and ZIP tiles reusing the equity-scatter pattern. Slice 3: 'View as table' toggle in a new USChoroplethMapTable.tsx so the map file shrinks rather than grows.
- **Tech:** ARIA APG roving tabindex (pattern already at frontend/src/routes/analytics.equity-scatter.tsx:301), native table, optional SVG pattern fills. All Baseline; no library needed.
- **Verifier:** USChoroplethMap.tsx:572-581 reproduces: 51 role=button tabIndex=0 paths, aria-label is name only, no onFocus, no table, ZIP button role=listitem. But the file is 1,044 lines on an expired size allowlist with 16 tests; roving focus plus a new sortable table view is not M.

### `a11y-05` No axe or a11y-lint gate on pull requests; nightly axe covers default page states only

`high` · `gap` · effort `L` · corrected

- **Evidence:** frontend/tests/e2e/accessibility.spec.ts:32 skips unless E2E_LIVE; :95 tags omit wcag22aa and best-practice (installed axe 4.11.3: 36 of 104 rules never run, including target-size and aria-allowed-role); :102-104 fails only serious/critical; default page state only. grep 'a11y|axe' in .github/workflows/ci.yml: none. frontend/eslint.config.js has no jsx-a11y.
- **Problem:** No accessibility check runs on a pull request, and nightly axe never opens a drawer, palette, Genie panel, filter menu, expanded row or non-default accent. Every defect in this report passes the current gate.
- **Recommendation:** Add an axe spec to the existing fixture-backed PR Playwright job (ci.yml:296-297): start with 4 core routes x 2 themes x drawer, palette, Genie and filter-open states, then widen to accents. Add wcag22aa; keep best-practice advisory rather than failing. Bump @axe-core/playwright to 4.13.0. oxlint jsx-a11y and aria snapshots as follow-on slices.
- **Tech:** @axe-core/playwright 4.13.0, oxlint 1.85.0 (npm-verified 2026-09-21); eslint-plugin-jsx-a11y 6.10.2 peers stop at ESLint 9, repo runs ESLint 10. Playwright aria snapshots stable since 1.49.
- **Verifier:** Verified: spec skips without E2E_LIVE; installed axe 4.11.3 runs 68 of 104 rules. But 35 of the 36 skipped are best-practice, AAA, obsolete or experimental; only target-size is AA and has a custom gate. Vitest ARIA tests run on PRs. Versions npm-verified.

### `a11y-08` No Accessibility Conformance Report (VPAT) for lender and Section 508 procurement

`high` · `gap` · effort `L` · corrected

- **Evidence:** grep -i 'vpat|conformance report|EN 301 549' over docs/ and README.md finds no document; only passing Section 508 mentions (docs/audits/cross-browser-responsive-audit.md:215, docs/audits/performance-scale-audit.md:700). That audit itself states lender RFPs cite WCAG 2.2 AA as a hard requirement.
- **Problem:** Lender and Section 508 procurement asks for an Accessibility Conformance Report. The product has tests but no criterion-by-criterion statement, no assistive-technology test record, no accessibility statement or contact, so vendor questionnaires stall.
- **Recommendation:** After the Level A fixes land, publish a WCAG-edition VPAT 2.5 ACR first (move to INT with 508 and EN 301 549 only when a federal or EU buyer asks), rated honestly with 'Partially Supports' and a dated roadmap. Record NVDA+Chrome and VoiceOver+Safari passes; JAWS needs a licence. Add an accessibility contact and link it from Glossary.
- **Tech:** ITI VPAT 2.5 INT (confirm current edition before publishing); manual AT passes plus axe evidence from a11y-05.
- **Verifier:** grep over docs/ and README finds no VPAT, ACR, EN 301 549 or accessibility statement; the cross-browser audit (:215) cites RFP demand. VPAT 2.5 web-verified current. But an honest ACR needs manual AT passes over ~55 criteria on 11 routes: L, not M.

### `a11y-v1` Portfolio Builder state picker is mouse-only (keyboard users cannot choose states)

`high` · `defect` · effort `S` · verifier-added

- **Evidence:** frontend/src/routes/portfolio-builder.components.tsx:34-75: StateMultiSelect options are li role=option with onClick only; no tabIndex or onKeyDown anywhere in the file (grep), no Escape or outside-click close. Rendered at portfolio-builder.tsx:453. A keyboard-complete MultiFilterSelect exists at analytics.sections.tsx:396-540.
- **Problem:** Keyboard and screen-reader users cannot choose states in step one of the product flow. This is a WCAG 2.1.1 Level A block, not an APG nicety; the auditor mentioned the file only as a later conversion target.
- **Recommendation:** Move MultiFilterSelect out of analytics.sections.tsx into components/ui and replace StateMultiSelect with it (same .filter/.filter-menu BEM, same value/onChange shape). Add a Vitest keyboard test: ArrowDown opens, Space toggles a state, Escape returns focus to the trigger.
- **Tech:** Existing in-repo focus-managed listbox; React 19; Vitest with happy-dom. No new dependency, no visual change.

### `a11y-06` Genie progress spams screen readers every second while the answer itself may never be announced

`medium` · `defect` · effort `M` · corrected

- **Evidence:** frontend/src/components/mortgage/GenieProgress.tsx:116-120 sets role=status aria-live on the whole block, which contains a 1-second ElapsedTicker (:55-66), six stage labels, trace and SQL. frontend/src/components/mortgage/GenieAnswer.tsx:214-216 mounts a status region already populated. GenieChat.tsx:521 transcript has no role=log.
- **Problem:** During a 10-60 second Genie turn a screen reader can re-read the full progress block every second (status is implicitly atomic), while the answer may go unannounced because live regions inserted with content are unreliably spoken.
- **Recommendation:** One persistent sr-only polite announcer per Genie surface (floating panel and /ask-genie) carrying only stage transitions and 'Answer ready: <metric>'. Remove role=status from the GenieProgress wrapper and GenieAnswer's mount-time region. Do not add role=log to the rich transcript. Pin live-region mutation count in Vitest; confirm with one NVDA and one VoiceOver pass.
- **Tech:** WAI-ARIA live regions (role=status, role=log), Baseline. No dependency. Needs one NVDA and one VoiceOver pass to confirm behaviour.
- **Verifier:** GenieProgress.tsx:116-120 role=status wraps the 1s ticker, stages, trace and SQL; GenieAnswer.tsx:214 mounts pre-populated; no role=log. Confirmed. But role=log on bubbles holding tables and SQL would read whole answers, and two surfaces plus a pinned test make this M.

### `a11y-07` One Escape closes every layer; modals are aria-modal only with a hand-rolled trap and no inert

`medium` · `upgrade` · effort `L` · corrected

- **Evidence:** frontend/src/hooks/useFocusTrap.ts:47-52,82, frontend/src/components/mortgage/GenieChat.tsx:192-199 and FilterSelect.tsx:36-43 each bind window-level Escape with no layer check; evidence chips render inside Genie (GenieChat.tsx:578). grep 'inert|<dialog|showModal' in src: no usage; five aria-modal dialogs rely on the attribute alone. useFocusTrap.ts:19-23 keeps hidden descendants.
- **Problem:** One Escape closes every open layer: the evidence drawer and the Genie panel beneath it, or a filter menu plus Genie. Background content stays reachable to virtual cursors, and Cmd-K over an open drawer leaves two traps competing for focus.
- **Recommendation:** Step 1 (M): a shared dismiss stack so only the topmost layer handles Escape, plus inert on the app shell while a modal is open. Step 2: migrate drawers and palette to <dialog>.showModal(), portalling hover-cards and tooltips into the dialog; entry slide via @starting-style, exit slide as Chromium-only enhancement or delayed close(); move trap tests to Playwright. closedby stays unused (Safari still preview-only).
- **Tech:** <dialog>/showModal and inert: Baseline widely available. @starting-style, transition-behavior: Baseline 2024. closedby is not Baseline (Safari pending; web-verified 2026-09), so keep the scrim click handler.
- **Verifier:** Three window-level Escape listeners without layer checks and five aria-modal dialogs without inert confirmed. But showModal makes document.body portals inert (EvidenceHoverCard.tsx:106, map tooltip), the exit slide needs CSS overlay (Chromium-only, web-verified), and happy-dom cannot test top layer. Effort is L.

### `a11y-09` Single-key A / R / slash shortcuts are global with no off switch; A approves outreach

`medium` · `defect` · effort `M` · corrected

- **Evidence:** frontend/src/components/mortgage/LeadTable.tsx:753-791 binds window-level 'a' (approve, writes the audit row), 'r' and Shift+A behind only an editable-target guard; frontend/src/components/layout/Topbar.tsx:198-204 binds '/'. grep -i 'hotkey|shortcut' in Console.tsx and AppContext.tsx: no off switch or remap.
- **Problem:** WCAG 2.1.4 (Level A) requires single-character shortcuts to be disableable, remappable or focus-scoped. A speech-input user dictating can fire 'a' and approve outreach on the expanded row with no confirmation, which touches the human-approval gate.
- **Recommendation:** Ship the cheapest 2.1.4-conformant fix first: a Console 'Single-key shortcuts' toggle (persisted AppContext preference) gating LeadTable a/r/Shift+A and Topbar '/'. Then add an inline confirm for keyboard approve mirroring pendingReject, and a '?' shortcuts sheet. Move hotkey logic into a new hook file rather than growing LeadTable.tsx; update LeadTable.hotkeys.test.
- **Tech:** React onKeyDown scoped to the existing role=region wrapper; AppContext preference. No dependency.
- **Verifier:** LeadTable.tsx:753-791 window-level a/r/Shift+A confirmed; approveLead drafts and approves with no confirm; no off switch found. But LeadTable is 1,261 lines on an expired size allowlist, 7 hotkey tests pin the window listener, and region-scoping breaks click-row-then-A. Effort M.

### `a11y-10` No forced-colors, prefers-contrast, color-scheme or OS theme support

`medium` · `gap` · effort `S` · corrected

- **Evidence:** grep 'forced-colors|prefers-contrast|forced-color-adjust|color-scheme' over frontend/src/design-system/*.css, frontend/src/routes/*.css and frontend/index.html: zero matches. Listbox cursor is background-only (components.css:2140, :5002); confidence bars and status dots are background-only (:1177-1181, :279). frontend/src/components/AppContext.tsx:164 defaults dark regardless of OS.
- **Problem:** In Windows High Contrast the active palette or filter option, confidence meters and status dots vanish because backgrounds are forced. Native selects, scrollbars and autofill render light inside the dark theme. OS contrast and scheme preferences are ignored.
- **Recommendation:** Add color-scheme per [data-theme]; a forced-colors block (Highlight outline on .is-active/.is-focused options; forced-color-adjust: none with system colours on meters, dots, legend and map tiers); prefers-contrast: more token overrides. Put it in a new design-system file, not components.css. Keep dark as the first-visit default; OS seeding is an owner decision. Test with Playwright emulateMedia forcedColors.
- **Tech:** color-scheme, forced-colors, prefers-contrast, CSS system colours: all Baseline widely available. Playwright emulateMedia({ forcedColors: 'active' }) for tests.
- **Verifier:** Zero matches for forced-colors, prefers-contrast or color-scheme in src and index.html; 11 native selects get light UA chrome in dark theme; compact ConfidenceMeter is bars-only. Confirmed. But OS theme seeding overrides the prototype's deliberate data-theme=dark default.
- **Constraint conflict:** Seeding the first-visit theme from prefers-color-scheme departs from the prototype default (design_files/index.html:2 and Module 0 Prototype.html:2, data-theme="dark").

### `a11y-v2` Sticky table header and bars with no scroll-padding can hide the focused control (WCAG 2.2 SC 2.4.11)

`medium` · `defect` · effort `S` · verifier-added

- **Evidence:** frontend/src/design-system/components.css:1217-1218 .tbl thead th is position: sticky; top: 0 inside .tbl-wrap (overflow: auto, :1267-1271); .route-nav (:322) and .bulk-actions (:1462-1472) are also sticky. grep scroll-padding|scroll-margin across frontend/src: zero matches.
- **Problem:** Shift+Tab up the Lead Queue scrolls the focused row control to the scroller's top edge, under the sticky header, hiding focus entirely. Likely WCAG 2.2 SC 2.4.11 (AA) failure; inferred from CSS, not runtime-verified.
- **Recommendation:** Set scroll-padding-block-start on .tbl-wrap to the header height and scroll-padding on the main scroller for .route-nav and .bulk-actions, using tokens. Add a Playwright check in the fixture-backed PR job: Shift+Tab through rows and assert the focused element's rect is not covered by thead.
- **Tech:** CSS scroll-padding (Baseline widely available); Playwright boundingBox assertion. No dependency.

**Overflow (titles only, not verified)**

- Evidence hover card fails WCAG 1.4.13: pointer-events:none so it is not hoverable, no Escape dismiss, aria-hidden (components.css:689, components/EvidenceHoverCard.tsx:110,142); move to popover + CSS anchor positioning (Baseline newly available Jan 2026, web-verified).
- Focus Not Obscured (2.4.11): zero scroll-padding/scroll-margin in components.css; sticky thead (:1218), sticky bulk-actions bar (:1470) and the fixed 420x640 non-modal Genie panel (:2498-2510) can fully cover focused row controls such as the right-edge Approval column.
- 118 of 289 font-size declarations are <=11px (19 at 9-10px) and all but one are px-based, so the browser font-size preference is ignored; move the scale to rem and set a 12px floor for non-decorative text.
- Reflow is untested below 390px: WCAG 1.4.10 needs 320x256 CSS px (1280 at 400% zoom); .app-shell is 100vh/100vw overflow:hidden (components.css:24-26); add a 320x256 Playwright case to responsive.spec.ts.
- Genie panel drag-to-move has no keyboard or single-pointer alternative (WCAG 2.5.7 / 2.1.1); resize has a keyboard path only (GenieChat.tsx:417-458).
- CommandPalette: option buttons are natively tabbable so Tab walks every option inside the trap, and role=status nodes sit inside role=listbox (CommandPalette.tsx:242-247, 296-301, 336-341); no result-count announcement.
- Non-text contrast: control borders --line-2 compute to 1.46:1 (dark) and 1.26:1 (light); inputs and filter chips on white cards rely on them alone (WCAG 1.4.11).
- Focus restore is lost when the trigger has been virtualised away: useFocusTrap.ts:85 only checks document.contains; fall back to the table region or h1.
- Accent combinations are not theme-scoped: [data-accent] chip-text overrides win over [data-theme=light] (tokens.css:246-253), giving 1.15:1 (teal) and 1.48:1 (red) evidence chips in light theme; .sr-skip-link uses --bg-1 on --accent (1.91:1 light+bright).
- Line charts expose only an axis-name label with no data summary or table toggle (routes/analytics.charts.tsx:358, 467); sparkline is aria-hidden (acceptable) but trend direction is not in the KPI label.
- LeadTable nits: empty <th> (LeadTable.tsx:1037), aria-pressed duplicated alongside aria-sort (:851), no caption or aria-label on the table element itself.
- GenieProgress ElapsedTicker puts aria-label on a roleless span (GenieProgress.tsx:63), which is prohibited and ignored by most screen readers.
- @axe-core/playwright is pinned at 4.11.2; 4.13.0 is current (npm-verified).

**Limitations:** Static review only: I could not boot the app (live Databricks credentials required, default ports reserved) so no axe run, no NVDA/JAWS/VoiceOver pass, and no in-browser check of 200%/400% zoom, forced-colors rendering or actual tab order; the live-region and Escape-stacking findings are derived from code reading, not observed with assistive technology. Contrast numbers are computed from tokens.css with sRGB alpha compositing (392 pairs, 2 themes x 4 accents) and then cross-checked against components.css for light-theme overrides; gradients, color-mix(in oklab) blends and backdrop blur were approximated, and rendered pixels were not sampled. Fixture screenshots (light evidence drawer, command palette, lead queue, segments) were used only to confirm visual contrast of the active drawer tab and placeholder; the manifest was still being written when I started and I did not review every PNG. I did not inspect nightly workflow run results, so I cannot say whether the nightly axe job is currently green. I did not open all 183 tsx files: Borrower 360, Offer Orchestrator, Admin and Portfolio Builder form labelling/error association (WCAG 3.3.x) were not audited in depth. Library versions were verified with npm view on 2026-09-21; dialog closedby and CSS anchor positioning status were verified by web search; the current VPAT template edition was not verified.

---

## Loading, error, empty, degraded and feedback states (`states`) — grade C+

**Auditor:** The transient-failure handling is strong: typed 503 reasons, the warm-up block, debounced health polling, keep-previous-data refresh regions and the Genie stage rail. But basics a Linear- or Stripe-class app takes for granted are missing. There are no error boundaries, no session-expiry or offline handling, no notification layer, no unsaved-work guard and no frontend error reporting.

**Verifier on the grade:** C+ is fair, at most a notch harsh. Every cited gap reproduced (no error boundaries, no session/offline handling, no navigation guard, no notification layer), but the auditor under-credited existing work (bulk rationale gate, abort-ambiguity handling, failed-preload reset, visibility-aware health polling) and missed a false-empty Lead Queue during warm-up, so the net stays at the C+/B- boundary.

**Strengths (keep these)**

- **Typed transient-failure protocol from the 503 body through to the UI** — frontend/src/lib/api.ts:438-443 defines the reason codes, and frontend/src/lib/retryPlan.ts:23-88 maps each one to a retry cadence (30s pacing for breaker_open, immediate stop for retries_exhausted). frontend/src/components/ui/WarmingUpBlock.tsx:80-118 renders dependency-specific copy, an attempt counter and the correlation id in a neutral chip, not a red error.
- **Health polling is carefully engineered** — frontend/src/components/HealthProvider.tsx:129-180 debounces the down-to-up transition so the banner does not flap, :206-226 pauses the poll when the tab is hidden, and :70-75 slows the poll for a deployment-shaped degraded status. frontend/src/components/layout/Topbar.tsx:45-66 ensures a failed probe never shows as Live.
- **Stale-while-revalidate is done well where it exists** — keepPreviousData plus .stable-refresh-region shows an 'updating' pill over the retained data instead of a new skeleton. See frontend/src/routes/lead-queue.tsx:290-298 and 719-724, frontend/src/routes/analytics.charts.tsx:65-79, and frontend/src/design-system/components.css:4230-4275.
- **The Genie long-wait and empty states are at the bar** — frontend/src/components/mortgage/GenieProgress.tsx:29-124 shows server-owned stage labels on an ordered lifecycle rail, stage dots that never un-complete, and an elapsed-seconds ticker. The Genie empty state (frontend/src/components/mortgage/GenieChat.tsx:598-604) has an icon, a title, and copy.
- **Writes are protected against double-submit and partial failure** — frontend/src/components/mortgage/LeadTable.tsx:237-250 and 582-595 use synchronous ref latches. LeadTable.tsx:652-666 recovers a partial bulk result on the next mount and restores focus. frontend/src/routes/portfolio-builder.tsx:334-345 reuses an idempotent request id across retries. frontend/src/routes/offer-orchestrator.panels.tsx:521-544 and 590-593 confirm in-page before a regenerate or channel switch discards a dirty draft.


### `states-01` No error boundary anywhere: a render throw or a stale chunk after a redeploy blanks the whole app

`critical` · `defect` · effort `M` · corrected

- **Evidence:** grep -rniE 'error.?boundary|componentDidCatch|getDerivedStateFromError|preloadError|unhandledrejection' over frontend/src returns nothing. frontend/src/main.tsx:13 is a bare createRoot. frontend/src/app.tsx:69 and frontend/src/components/layout/AppShell.tsx:179,216 are Suspense without boundaries. backend/main.py:834 returns 404 for retired hashed chunks. frontend/src/lib/rum.ts:3-10 has no error metric.
- **Problem:** A throw in any route, the Genie panel or the Console unmounts the React root, leaving a blank page with no recovery and no report. Every deploy strands open tabs on missing chunks, and React.lazy caches the rejection, so re-rendering never recovers.
- **Recommendation:** Keep the three-level react-error-boundary plan and the one-shot vite:preloadError reload (event verified present in installed Vite 8.0.16). Chunk-load failures must offer Reload, not Retry, because React.lazy caches the rejection. Drop useQueryErrorResetBoundary: queries here return errors and never throw. Add js_error to the metric allowlist in backend/schemas/telemetry.py and scrub messages with the existing RUM id regexes. Build the fallback from existing tokens/BEM as a declared prototype extension (the prototype has no error-page pattern).
- **Tech:** react-error-boundary 6.1.6 (npm-verified, React 19 peer). React 19 createRoot error callbacks (stable). Vite vite:preloadError event (stable since 4.4).
- **Verifier:** Reproduced: zero boundary/handler grep hits, bare createRoot, Suspense-only shells, /assets 404 at main.py:834, no RUM error metric. Nuance: lazyWithPreload resets failed preloads and idle-preloads four routes, narrowing stale-chunk exposure. Effort is M: new dependency, backend telemetry allowlist, fallback CSS, tests.

### `states-02` Session expiry, an unreachable backend and offline are nearly silent; the worst outage gets the quietest signal

`high` · `defect` · effort `M` · corrected

- **Evidence:** frontend/src/lib/api.ts:825 fetch has no redirect or content-type handling, and :880 res.json() sits outside the try. :993 maps a failed probe to status 'unreachable', which frontend/src/components/mortgage/DegradedBanner.tsx:81-93 ignores. Only the amber pill tooltip at Topbar.tsx:56-66 remains. grep 401|navigator.onLine|onlineManager|fetchStatus in src: no handling.
- **Problem:** When the Databricks Apps session lapses or the network drops, users get 'Failed to fetch' or a JSON SyntaxError beside a useless Retry. Offline, they get perpetual skeletons because TanStack pauses queries. There is no banner and no re-auth path.
- **Recommendation:** First capture the real expired-session response from the Databricks Apps proxy (302, 401 or HTML 200) before coding. If using redirect:'manual', confirm with a second /api/health probe before declaring session expiry, because FastAPI's default trailing-slash 307s (redirect_slashes is not disabled in backend/) also surface as opaqueredirect. The rest stands: 401/HTML-body classification as session_expired, one blocking re-auth dialog that preserves the URL, unreachable and offline .degraded-banner variants, and no skeletons while fetchStatus is 'paused'.
- **Tech:** fetch redirect:'manual' (Baseline widely available). TanStack Query v5 onlineManager and QueryCache onError. Native dialog element (Baseline widely available).
- **Verifier:** Reproduced: fetch has no redirect/content-type handling, res.json() outside try (api.ts:880), health maps to 'unreachable' (:993), DegradedBanner reads only dependencies/breakers, no onLine/onlineManager/401 handling. Topbar.tsx comment records a real expired-session incident. Recommendation needs a redirect caveat.

### `states-03` The degraded banner promises automatic recovery that only the Home KPIs deliver

`high` · `defect` · effort `M` · corrected

- **Evidence:** frontend/src/components/mortgage/DegradedBanner.tsx:182 promises 'Live data will resume automatically. This page refreshes every 3 seconds', but only the health poll does that. The sole recovery refetch is frontend/src/routes/home.tsx:108-111; grep refetchQueries|resetQueries returns nothing. home.tsx:72-73 admits the 30s retry budget is shorter than a 30-60s cold start.
- **Problem:** On Lead Queue, Borrower 360, Segments, Analytics and Offer, a cold start outlasts the retry budget. Panels turn red under a 'Reconnecting' banner and stay red after recovery until someone clicks Retry.
- **Recommendation:** On the degraded-to-healthy edge in HealthProvider call refetchQueries({ type: 'active', predicate }) limited to errors that are retryable ApiErrors for the recovered dependency, so unmounted audited reads and 403/422 failures are not re-fired. Then delete the Home-only effect, keep warming copy instead of the red callout while the banner's dependency matches the error's, and make the banner copy truthful (it is the health poll, not the page, that refreshes every 3 seconds).
- **Tech:** TanStack Query v5 refetchQueries with a predicate, plus the existing HealthProvider context. No new dependency.
- **Verifier:** Reproduced: banner copy at DegradedBanner.tsx:182, sole recovery effect at home.tsx:108-111, no refetchQueries anywhere; useWarmingUpRetry stops after six attempts. An unfiltered refetchQueries also refetches unmounted errored queries, writing VIEW_* audit rows. The all-route callout change makes this M.

### `states-05` No unsaved-changes guard on route leave or tab close

`high` · `gap` · effort `L` · corrected

- **Evidence:** grep beforeunload|useBlocker|usePrompt|isDirty in frontend/src returns zero hits. frontend/src/routes/offer-orchestrator.tsx:382 computes draftDirty. frontend/src/routes/portfolio-builder.tsx:136 campaignSetup and :108 uncommitted filters are plain useState. frontend/src/main.tsx:16 mounts the declarative BrowserRouter, and installed react-router 8.3.0 (dist/development/lib/hooks.js:1248) shows useBlocker needs a data router.
- **Problem:** Editing an outreach draft or campaign copy, then clicking the rail, Cmd-K or a Genie link, or closing the tab, discards the work silently. In-page guards exist for regenerate and channel switch, but navigation has none.
- **Recommendation:** Migrate minimally: createBrowserRouter([{ path: '*', element: <App/> }]) keeps the existing <Routes> tree and enables useBlocker. Make useUnsavedGuard tolerate non-data routers or move the six offer-orchestrator/portfolio-builder test files to createMemoryRouter. Style the confirm with .approval tokens; beforeunload only while dirty. Key any sessionStorage stash to generation_id and the provenance tokens so restored edits never pair with stale governed proof, and register the key in actorScopedBrowserState so it clears on actor change.
- **Tech:** React Router 8 data-router useBlocker (stable, verified in installed 8.3.0) and beforeunload (Baseline widely available). The data router also unlocks errorElement, useNavigation and viewTransition.
- **Verifier:** Reproduced: no beforeunload/useBlocker hits; draftDirty at offer-orchestrator.tsx:382; campaignSetup is a 16-field useState; main.tsx uses BrowserRouter; installed react-router 8.3.0 useBlocker calls useDataRouterContext. But 41 test files use MemoryRouter, where useBlocker throws. Effort is L.

### `states-06` Single-row queue approve certifies outreach copy the approver never sees (bulk has a rationale gate but shows no draft sample)

`high` · `gap` · effort `L` · corrected

- **Evidence:** frontend/src/components/mortgage/LeadTable.tsx:255-257 fetches a governed draft inside approveLead, and :271-279 immediately approves its subject, body and hash. LeadRowPreview.tsx:77 shows only the offer rationale (grep draft|subject|body finds nothing else). The bulk path (:634) repeats this per id. Reject opens LeadRejectPanel with a reason and rationale.
- **Problem:** The audit row certifies that a human approved specific outreach copy that was never rendered to them. The friction is inverted: the 'no' needs a form, while the compliance-bearing 'yes' needs nothing, not even a cancel window.
- **Recommendation:** Generate the draft on explicit intent, not on row expand: the first Approve click (or A) opens a review panel in the expanded row showing subject, body, disclosure chip and evidence chips; a second confirm submits that exact generation_id. For bulk, keep the existing required-rationale gate and add count-by-offer plus a few sampled drafts. LeadTable.tsx is 1,261 lines with seven test files including hotkeys. Owner decision: this changes approval posture and triage speed.
- **Tech:** Existing api.draftOutreach with a TanStack useQuery prefetch on row expand. No new dependency.
- **Verifier:** Single approve confirmed: LeadTable.tsx:255-279 drafts then approves unseen; A hotkey at :780; LeadRowPreview shows no draft. But bulk already requires a shared rationale (:604-607, :1128), and /draft writes a DRAFT_OUTREACH audit row, so prefetch-on-expand inflates audit evidence.
- **Constraint conflict:** Prefetching drafts on row expand would write a DRAFT_OUTREACH audit row per expand (backend/api/outreach.py docstring and insert), inflating governance evidence the app elsewhere protects. Changes the approval posture, so it is an owner decision.

### `states-04` Raw transport strings reach buyers, and each route hand-rolls its own error surface

`medium` · `defect` · effort `L` · corrected

- **Evidence:** frontend/src/routes/lead-queue.filters.ts:400 renders `Couldn't load leads: ${error.message}`, and frontend/src/routes/analytics.charts.tsx:97 prints error.message. frontend/src/lib/api.ts:843,867 make that '500 Internal Server Error' or 'Failed to fetch'. frontend/src/routes/portfolio-builder.tsx:350 is `catch {`, so :562 shows generic copy. The only four-state wrapper, LoadState (analytics.charts.tsx:56), is route-local.
- **Problem:** Business buyers read browser and HTTP jargon. Permission, outage and duplicate-name failures are indistinguishable on save. Error layout differs per route (status-callout, surface--danger, PageShell swap), so Retry and the correlation id appear inconsistently.
- **Recommendation:** Ship in two slices. Slice 1 (M): lib/describeApiError.ts mapping status, reason and dependency to title, body and action with a copyable correlation id, and pass the caught ApiError through portfolio save so 403, 409 and outage get distinct copy. Slice 2 (L): promote LoadState to components/ui/AsyncState.tsx (adding an empty slot) and migrate routes one per commit, starting with Lead Queue, Home and Analytics.
- **Tech:** Internal refactor using a TypeScript discriminated union. Pairs with the react-error-boundary FallbackComponent. No new dependency.
- **Verifier:** Reproduced: lead-queue.filters.ts:400 and analytics.charts.tsx:97 print error.message; api.ts falls back to status text or 'Failed to fetch'; save uses a bare catch; components/ui has no shared async-state; correlation id shows only in WarmingUpBlock. Visible only on failure: medium. Eleven-route adoption is L.

### `states-07` No notification layer and no undo; success and failure feedback is scattered across five ad hoc patterns

`medium` · `gap` · effort `L` · corrected

- **Evidence:** find -iname '*toast*' in frontend/src returns none, and there is no .toast in components.css or design_files/index.html. Feedback is ad hoc: LeadTable.tsx:123 salesToast, :209 bulkToast, :176 single-string approvalError rendered at the table foot (:1240). portfolio-builder.tsx:497-500 mutates the button label, and offer-orchestrator.tsx:788 uses routingConfirm. grep useMutation|useOptimistic returns none.
- **Problem:** Success and failure feedback lives in five places and styles. It can sit below the fold, it overwrites itself during bulk failures, and it never offers Undo or a link to the audit event just written.
- **Recommendation:** Slice 1 (M): an app-shell Toaster with BEM .toast from existing tokens, popover='manual' with a fixed-position fallback where unsupported, and existing success/failure messages routed through it with 'View audit event' links. Offer Undo only for assignment, as a compensating audited write, never for approve or reject. Slice 2 (L): migrate writes to useMutation incrementally, preserving LeadTable's synchronous in-flight latches and tagged outcomes.
- **Tech:** Popover API popover='manual' (Baseline Newly available, verified 2025-01-27) with @starting-style, or headless sonner 2.0.8 unstyled (npm-verified). TanStack useMutation and MutationCache.
- **Verifier:** Reproduced: no toast files, no .toast in CSS or design_files, five ad hoc feedback patterns, zero useMutation. Popover API Baseline January 2025 verified on MDN; sonner 2.0.8 verified on npm. Migrating every write (LeadTable latches, tagged outcomes) to useMutation makes this L.
- **Constraint conflict:** Departs from the prototype, which defines no transient-feedback pattern; acceptable only as a declared extension built from the existing token and BEM vocabulary, not sonner's default look.

### `states-08` Long waits lack progress, time and a way out: bulk approve, Genie turns, 429 cooldowns and warm-up

`medium` · `gap` · effort `M` · corrected

- **Evidence:** frontend/src/components/mortgage/LeadTable.tsx:1199 shows only 'Approving…' while :628 loops chunks of 3 (LeadTable.constants.ts:5), with no count or cancel. grep -i 'cancel|stop' in ask-genie.tsx, GenieChat.tsx and GenieProgress.tsx finds no user control. frontend/src/lib/api.ts:831 sleeps the full Retry-After silently, :844-852 drops retryAfterMs, and :504 excludes 429 from the warming UI.
- **Problem:** A 60-lead bulk approve is twenty silent round-trips. A Genie turn cannot be stopped. A rate-limited read freezes on a skeleton, then says 'retry after the indicated delay' with no delay shown. WarmingUpBlock counts attempts, not time.
- **Recommendation:** Bulk: determinate progress plus a cooperative Cancel that stops before the next chunk and lets in-flight POSTs settle; do not abort them, since the code treats aborted approvals as ambiguous. Genie: Stop via askAbortRef in GenieChat and queryClient.cancelQueries on /ask-genie (it is a useQuery there), labelled as 'stop waiting' because no server-side cancel exists. Carry retryAfterMs on ApiError and render a countdown; add elapsed and next-retry time to WarmingUpBlock.
- **Tech:** Native progress element and AbortController (Baseline widely available). The existing GenieProgress ElapsedTicker. The backend already sends retry_after_seconds (backend/services/backpressure.py:263-270).
- **Verifier:** Reproduced: 'Approving…' only (LeadTable.tsx:1199), chunks of 3, no Genie stop control, Retry-After slept silently (api.ts:831) and dropped from ApiError, attempt-only WarmingUpBlock. But aborting in-flight approve POSTs is documented as audit-ambiguous (R5-21), and four surfaces make this M.

### `states-09` Views go stale without saying so: no fetched-at stamp, no refresh control, no change signal for a multi-approver queue

`medium` · `gap` · effort `M` · corrected

- **Evidence:** frontend/src/lib/queryClient.ts:18 disables focus refetch globally because reads write VIEW_* audit rows. grep refetchInterval finds only DataOperationsPanel.tsx:187; grep dataUpdatedAt finds none. The only manual refresh is portfolio-builder.governance.tsx:143. Home's chip (frontend/src/routes/home.tsx:141) shows the gold-table refresh time, not the fetch time.
- **Problem:** A Lead Queue left open for an hour looks live while other approvers change it. Nothing says when the view was fetched, and there is no refresh control short of a browser reload.
- **Recommendation:** Keep 'Fetched Nm ago · Refresh' from dataUpdatedAt in surface__hdr with Intl.RelativeTimeFormat. For the change signal, add a dedicated authenticated, non-audited, short-TTL-cached endpoint (for example /api/v1/leads/queue-version returning max approval updated_at), polled about every 60s only while Lead Queue is mounted and the tab is visible. Show a 'Queue changed, refresh' pill rather than auto-refetching audited reads.
- **Tech:** TanStack Query dataUpdatedAt. Intl.RelativeTimeFormat (Baseline widely available). One added field on the existing FastAPI health payload.
- **Verifier:** Reproduced: focus refetch disabled (queryClient.ts:18), one refetchInterval (admin panel), no dataUpdatedAt, one Refresh button, Home chip is gold-table time. But /api/health is deliberately minimal and probe-cheap (health.py R6-09 docstring); tenant approval state does not belong there.

### `states-10` Skeleton craft: no show-delay or minimum duration, generic placeholders for full dashboards, the KPI row unmounted during warm-up, paint-bound shimmer

`medium` · `upgrade` · effort `M` · corrected

- **Evidence:** frontend/src/components/ui/Skeleton.tsx:29-47 has no show-delay or minimum duration (grep useSpinDelay|showDelay|minDuration: none). analytics.charts.tsx:40-54 and app.tsx:25-38 use three generic lines for whole dashboards. lead-queue.skeleton.tsx:20 draws six 14px rows versus ~68px real rows (fixture screenshot). home.tsx:211,217 unmount the KPI row during warm-up. components.css:4948 animates background-position.
- **Problem:** Fast cached responses flash a skeleton for a frame. Slow ones swap a 120px placeholder for a 1,400px dashboard, shifting layout that the app's own CLS telemetry records. The shimmer is paint-bound and unsynchronized across blocks.
- **Recommendation:** Prioritise shaped skeletons that reuse the real grid templates and row heights, and keep the KPI row mounted in its loading state during warm-up. Reserve space with min-height or aspect-ratio, not contain-intrinsic-size. Move the shimmer to a transform-animated ::after sweep within the .skeleton vocabulary. Add the 150ms show-delay only where uncached responses are routinely sub-200ms; cached queries already skip the skeleton.
- **Tech:** A plain React hook (spin-delay pattern). CSS transform animation (compositor-only). contain-intrinsic-size to reserve space (Baseline 2024). Class names stay within the prototype's .skeleton vocabulary.
- **Verifier:** Reproduced: Skeleton has no delay logic, generic three-line fallbacks, 14px skeleton bars versus LEAD_ROW_ESTIMATE_PX=86, KPI row gated on !previewWarming, shimmer animates background-position. Corrections: TanStack cache hits render synchronously (no flash); contain-intrinsic-size is Baseline 2023 and inert without size containment (MDN).

### `states-v1` Lead Queue renders a false-empty table ('Showing 0 ranked borrowers') during warm-up and after load errors

`medium` · `defect` · effort `S` · verifier-added

- **Evidence:** lead-queue.tsx:293 sets loading=false once warmingUp or error exists; :719 then renders LeadTable with empty leads; LeadTable.tsx:1249 prints 'Showing 0 ranked borrowers' with full header chrome. WarmingUpBlock.tsx:64-79 returns null when the banner covers the dependency, leaving only the empty table.
- **Problem:** During a cold start or failed load, the queue shows a normal-looking table claiming zero ranked borrowers. A buyer reads a false zero as data, contradicting the app's honest-state posture.
- **Recommendation:** Render LeadTable only when leadsData is non-null. While warming or errored with no data, keep LeadQueueTableSkeleton or the warming copy in the table slot, even when DegradedBanner suppresses WarmingUpBlock. Add a lead-queue test asserting the table and its footer are absent in both states.
- **Tech:** Conditional render change in frontend/src/routes/lead-queue.tsx plus one Vitest case; no new dependency.

### `states-v2` Empty states are bare muted one-liners with no cause, no next action and leftover table chrome

`medium` · `gap` · effort `M` · verifier-added

- **Evidence:** lead-queue.tsx:710-717 renders 'No leads match this filter.' as plain .muted text while full LeadTable chrome mounts below. analytics.charts.tsx:136,532 print 'No rows returned.' components/ui holds only FilterSelect, Skeleton, WarmingUpBlock; design_files/index.html defines no empty pattern.
- **Problem:** Zero-result views do not distinguish filters-exclude-everything, day-zero and coverage gaps, and offer no inline recovery. They read as unfinished beside the rest of the product.
- **Recommendation:** Add components/ui/EmptyState.tsx with BEM .empty built from existing tokens (declared prototype extension): icon, one-sentence cause, and a primary action such as Clear filters, widen geography or Ask Genie about the current filters. Hide table chrome at zero rows. Adopt on Lead Queue, Analytics and Segment Intelligence first.
- **Tech:** Internal React component plus existing design tokens; no new dependency.

**Overflow (titles only, not verified)**

- Empty states do not teach the next action. Analytics shows 'No rows returned.' (frontend/src/routes/analytics.charts.tsx:136,532), and Lead Queue shows plain muted text with no Clear-filters CTA (frontend/src/routes/lead-queue.tsx:710-717). There is no shared EmptyState primitive; the Genie empty state is the only designed one.
- frontend/index.html ships an empty #root and no noscript, so boot is blank until the ~396 KiB initial JS runs. Inline an app-shell skeleton under a CSP hash. This overlaps the performance dimension.
- React 19 async primitives are unused: grep useTransition|useOptimistic|useActionState|useDeferredValue over frontend/src returns nothing. Filter changes and tab switches are good candidates for transitions.
- The comment at frontend/src/lib/lazyPreload.ts:24-29 says the real path can retry after a failed chunk. React.lazy caches the rejection, so only a reload recovers. Fix the comment along with the vite:preloadError handler.
- The rate_limited branch at frontend/src/lib/retryPlan.ts:55-66 is unreachable from the hook, because isWarmingUpError requires status 503 (frontend/src/lib/api.ts:504).
- WarmingUpBlock replaces its cadence copy with a raw 'correlation_id: …' string for business users (frontend/src/components/ui/WarmingUpBlock.tsx:112-114). Move it behind a Details disclosure with a copy button.
- DegradedBanner has no collapse or dismiss, no 'since' time and no 'what still works' note. Dependency naming is inconsistent: the banner says 'analytics warehouse' while WarmingUpBlock says 'Warehouse' and 'Lakebase'.
- Form validation feedback is weak. LeadRejectPanel disables submit with no inline reason (frontend/src/components/mortgage/LeadTableDecisionPanels.tsx:70), as does the save-build form. The 500- and 80-character fields have no counters.
- 'Reset draft' removes a saved draft with no confirm (frontend/src/routes/offer-orchestrator.panels.tsx:639), while Regenerate does confirm.
- The DegradedBanner standalone fetcher (frontend/src/components/mortgage/DegradedBanner.tsx:51-62, 101-149) is test scaffolding shipped in production. It fabricates 'warehouse and lakebase down' on any non-2xx. Move it to test support.
- Bulk approve keeps only the last per-row error string (frontend/src/components/mortgage/LeadTable.tsx:300-304), so the per-row failure reasons are lost.
- AdminRouteGate silently redirects to '/' when the session query errors (frontend/src/app.tsx:54-55, retry:false), so a network blip reads as 'no access' with no message.

**Limitations:** This was static analysis only; I did not run the app, tests or builds. The screenshot set (35 PNGs) has no loading, error, degraded or warm-up captures, so state visuals were judged from JSX and CSS. I viewed only the lead-queue and not-found fixture shots. The Databricks Apps proxy's response on session expiry (302 to login, 401, or an HTML 200) could not be verified live, and a web search was inconclusive. states-02 holds for all three, because none is handled. CLS and skeleton-flash durations were not measured. I read home, lead-queue, borrower-360, analytics, portfolio-builder and offer-orchestrator state handling in detail. Segment-intelligence, asset, admin-config and the Console were only grepped. Library versions were verified with npm view on 2026-09-21. Popover API Baseline status was verified via web.dev. The useBlocker data-router requirement was verified in the installed react-router 8.3.0 source.

---

## End-to-end workflow UX and product strategy (`flow`) — grade C+

**Auditor:** Governance plumbing, honest-state design and the Borrower 360 story are strong. The flow breaks where it matters most: Score and Approve are off-screen at the contracted 1440 viewport, Home never answers its own question, approval can be blind, the audit trail is admin-only and buried, and nothing supports daily use.

**Verifier on the grade:** Slightly harsh: all ten findings reproduce, but three were overstated (Approve and score are one row-expand or hotkey away, the entity tabs are not dead ends, and last-login deltas, saved leads/drafts/campaigns and watchlists already give some daily pull), so B- is fairer, with blind approval, the role-blind Approve UI and the admin-only audit trail as the real drags. Tech currency checked: npm view confirmed @tanstack/react-table 9.2.4, nuqs 2.10.1, sonner 2.0.8, react 19.3.0 (2026-09-09); sources: https://react.dev/blog/2026/09/09/react-19-3 and https://web.dev/blog/same-document-view-transitions-are-now-baseline-newly-available

**Strengths (keep these)**

- **Borrower 360 'The story' is a best-in-class explain moment** — frontend/src/routes/borrower-360.tsx plus components/mortgage/BorrowerStoryCard.tsx and lib/borrowerStory.ts give a one-paragraph narrative with per-claim verified chips (rate, spread, equity, lien). It sits beside the refi-economics check, primary offer, trigger timeline and a Show proof drawer (dark__borrower-360-detail__full.png). The r5 audit checked every figure against the dossier.
- **Typed, versioned URL handoff contracts between steps** — frontend/src/lib/campaignPrefill.ts:1-40 mirrors backend/schemas/campaign_prefill.py for the geo-drill to campaign handoff. segment-intelligence.tsx:559-578 builds a Lead Queue href carrying segments, mode, geography and criteria. routes/analytics.tsx:66-97 drives tab, filters and window entirely from search params. Genie opens a governed cohort_id queue.
- **Honest-state design that builds trust instead of hiding gaps** — home.tsx:44-58 day-zero callout and 170-196 keep 'warehouse warming' distinct from a red error. Segment cards show 'first snapshot, deltas pending'. KPI cards carry step-change trend notes. Pending Cotality feeds are disclosed. The ROI projector refuses to substitute a fallback conversion rate (portfolio-builder.components.tsx:344-347).
- **Approval write-path is hardened end to end** — ApprovalBanner.tsx:52-67 has a synchronous double-submit latch. LeadTable.tsx:271-287 binds draft generation id, response hash and source_refreshed_at into the approve payload. Bulk approve requires a shared rationale (603-609). backend/api/outreach.py:1662 checks approver RBAC and takes the actor from the edge identity only.
- **Sales-ops loop and power-user affordances already exist** — Assignment, round-robin distribute, call dispositions and an aging filter live in the queue (LeadTable.tsx:1160-1176). LO assignment and a follow-up reminder are captured at approval (offer-orchestrator.tsx:741-775). ActivationLoopPanel appears after approval. The Cmd-K palette and A/R hotkeys work. Saved leads and drafts persist server-side through api.workspace (AppContext.tsx:200-204).


### `flow-01` Lead Queue hides Score, Signal and Approve off-screen at the contracted 1440x900 viewport

`high` · `defect` · effort `M` · corrected

- **Evidence:** frontend/src/design-system/components.css:1309-1323 fixes 15 column widths totalling 1,564px under `inline-size:max-content` (1296). components/mortgage/LeadTable.tsx:1047-1050 orders Score, Signal, Approval last. Screenshots dark/light__lead-queue__viewport (1440) clip at 'PRIMA…'; the 1920 shot shows all three. `.tbl-wrap` caps height at 520px (1268).
- **Problem:** At the contracted 1440x900 viewport the score, signal meter and Approve/Reject buttons sit off-screen right. The hero plus a 16-filter panel push the table to y≈645, so under four rows show. 'Ranked borrowers' displays neither rank nor action.
- **Recommendation:** Keep the prototype's column order (design_files/Module 0 Prototype.html:2013-2023 ends NBO, Score, Confidence, action). Default-hide the four app-added columns (Relationship, Assigned, Outreach, Last touch; 444px) behind a column-visibility preset with a 'Sales ops' view, giving about 1,120px, which fits the roughly 1,320px main column with Console closed. Pin Approval with position:sticky; inset-inline-end:0 for Console-open (about 1,020px). Collapse filters into an active-chip bar plus an 'All filters' popover and replace the 520px cap with a flex-fill scroller. Use plain React state for visibility; do not adopt @tanstack/react-table for this slice.
- **Tech:** CSS sticky columns (Baseline widely available); @tanstack/react-table 9.2.4 headless column visibility/order/pinning (npm-verified 2026-09-21) beside the existing @tanstack/react-virtual; native Popover API for the filter sheet.
- **Verifier:** Summed the 15 column widths (1,564px) and viewed the 1440/1920 captures: Score, Signal, Approval clip at 1440. Row-expand still shows score and Approve, so high, not critical. Score-first departs from prototype order; TanStack Table means rewriting a 1,261-line, 8-test component.
- **Constraint conflict:** Moving Score to the first column departs from the prototype table order (design_files/Module 0 Prototype.html:2013-2023); the corrected fix preserves that order.

### `flow-02` Approve UI ignores the server's can_approve flag and never shows who is signed in

`high` · `defect` · effort `M` · corrected

- **Evidence:** backend/api/session.py:13,20 returns `can_approve`. frontend/src/types/workspace.ts:37-39 types only `can_access_admin`. grep `can_approve|canApprove` over frontend/src returned 0 hits. backend/api/outreach.py:1662 `require_approver` rejects everyone outside the allowlist. grep `avatar|email|profile` in components/layout/Topbar.tsx found no identity element.
- **Problem:** Every persona sees live Approve buttons, A/R hotkeys, bulk approve and the 'Human approval required' banner. Non-approvers learn they lack rights only from a 403. Nobody sees whose name the audit row will carry.
- **Recommendation:** Slice it. S: add can_approve and the actor's own email (from X-Forwarded-Email) to /session and the frontend SessionResponse; hide or disable Approve, bulk approve and the A/R hotkeys for non-approvers before any draft call is made; print 'Approving as ...' in ApprovalBanner. M, owner decision: an audit-logged 'Request approval' Lakebase record added through jobs/lakebase_migrate.py. The topbar identity chip is an additive departure from the prototype topbar; call it out in the commit.
- **Tech:** Existing FastAPI /session endpoint and TanStack Query `['session','access']` cache; request table added through jobs/lakebase_migrate.py to keep deploy zero-click. No new library.
- **Verifier:** session.py returns can_approve; workspace.ts omits it; grep can_approve in frontend/src = 0; Topbar has no identity. Worse: /outreach/draft is not approver-gated, so a non-approver's click writes a DRAFT_OUTREACH audit row before the 403. 'Request approval' adds a table and endpoint: M.
- **Constraint conflict:** The prototype topbar has no identity/avatar element (grep avatar in design_files matches only .genie__avatar); the identity chip is an additive departure that must be justified in the commit.

### `flow-03` Approval can be blind: row, hotkey and bulk approve never show the copy being approved, and give no receipt

`high` · `gap` · effort `M` · corrected

- **Evidence:** frontend/src/components/mortgage/LeadTable.tsx:255-289: row Approve and hotkey A call `api.draftOutreach()` then immediately `api.approve()` with that `draft_body`; the copy is never rendered. Bulk (581-609) asks only a rationale. grep `audit_event_id` in LeadTable.tsx = 0. routes/offer-orchestrator.tsx:809 prints `audit: {id}` as muted 11px text, not a link.
- **Problem:** The human gate can be a rubber stamp. An approver can approve borrower-facing copy, disclosure and channel they never saw, in one keystroke, with no confirmation, no receipt and no path to the audit row. A compliance reviewer will notice.
- **Recommendation:** As written, with three changes: reuse the existing bulkToast/aria-live pattern for the receipt instead of adding sonner; make the review sheet state the channel explicitly because the queue hard-codes 'email' (LeadTable.tsx:257, 283); have hotkey A open the sheet with Enter to confirm, and update LeadTable.hotkeys.test plus the approve e2e specs in the same slice.
- **Tech:** Native `<dialog>` (Baseline widely available) with the existing hooks/useFocusTrap.ts; receipt via the existing aria-live region or sonner 2.0.8 (npm-verified); approve already returns audit_event_id, so no backend change.
- **Verifier:** LeadTable.tsx:255-289 drafts then approves without rendering the copy; hotkey A (777-780) does the same; grep audit_event_id = 0; offer-orchestrator.tsx:809 is plain text. Bulk does show an ok/fail toast, just no audit id. Queue hard-codes channel 'email'. sonner is a styled kit.
- **Constraint conflict:** sonner ships its own visual style; under the design contract only an unstyled/headless use is acceptable, and the existing in-house toast already covers it.

### `flow-04` The audit trail is admin-only, buried, not deep-linkable, and absent from the borrower dossier

`high` · `gap` · effort `L` · corrected

- **Evidence:** frontend/src/app.tsx:83 gates the audit explorer behind AdminRouteGate; non-admins get only the Console's self-scoped 'My recent activity' (components/layout/Console.tsx:93-95). routes/admin-config.tsx:466 mounts the explorer last on a 3,512px page. components/admin/AdminAuditExplorer.tsx:42-47 holds filters in useState, free-text codes; no export, actor or date filter (grep `download|Blob|actor|date`).
- **Problem:** 'Audit' is the promise's final verb, yet a non-admin approver or growth lead cannot see a decision trail. Nobody can deep-link to an event, and a borrower's dossier shows no decision history. Rows read 'APPROVE_OUTREACH / APPROVE_OUTREACH'.
- **Recommendation:** Phase 1 (M): expose the actor/since/until filters the backend already supports (backend/api/audit.py:176,183-184), URL-sync them with the existing useSearchParams pattern rather than adding nuqs, humanise event labels, add CSV via LeadTable.csv.ts, and link every audit_event_id. Phase 2 (M): a borrower-scoped decision-history endpoint and timeline on Borrower 360 and Offer. Owner decision: whether non-admins see other actors' emails or only a role label; keep the full explorer admin-gated until decided.
- **Tech:** nuqs 2.10.1 typed search params (peer react-router ^8, npm-verified 2026-09-21) or plain useSearchParams; existing /api/audit/events filters and /borrowers/{id}/lifecycle; CSV reuse of LeadTable.csv.ts.
- **Verifier:** app.tsx gates /admin-config; Console shows only my-events; AdminAuditExplorer keeps filters in useState with no export. Borrower 360 and Offer render no decision history (lifecycle feeds status only). Backend /audit/events already accepts actor, since, until. Endpoint plus route plus CSV plus timelines is L.
- **Constraint conflict:** Non-admin audit visibility moves an intentional RBAC boundary (home.tsx comment: 'Audit exploration is admin-only'; /audit/events is admin-gated); needs an owner governance decision, not just UI work.

### `flow-05` Home asks 'who, why now, what offer' but answers with four population counts and three competing primary CTAs

`high` · `gap` · effort `M` · confirmed

- **Evidence:** frontend/src/routes/home.tsx:132 titles the page with the question, then renders four count KPIs (224-263) and PortfolioSummaryCard, whose prose (lib/portfolioStory.ts:99-129) restates those four numbers. Three `btn--primary` links (149, 293, 314); two target /portfolio-builder. TopLeadsQuickPick is mounted only on index empties (borrower-360.tsx:129).
- **Problem:** The first 900px show no borrower, no trigger and no offer, only population counts under jargon labels ('Refi economics screen', 'Primary offer paths'). An unaccompanied buyer cannot see the value in 60 seconds, and three primary buttons compete.
- **Recommendation:** Replace 'Your book today' with an answer band. WHO: the top five ranked borrowers (score, city, segment chip). WHY NOW: the leading triggers since last login (rate move, new listings, maturing liens). WHAT OFFER: an offer-mix bar. Each deep-links to a filtered queue. Keep one primary CTA, 'Review today's top leads'; demote Build a portfolio.
- **Tech:** Existing api.leads(limit 5), homeSummary highlights and offer counts from gold; hand-rolled SVG bar on prototype tokens; React 19.3 stable `<ViewTransition>` (released 2026-09-09, web-verified) for card-to-dossier continuity.
- **Verifier:** Viewed the 1440 Home capture and home.tsx: last-login count deltas, four count KPIs, a prose card restating them, approval banner; no borrower, trigger or offer; three btn--primary (149, 293, 314). The prototype's own main screen carries BorrowerTable, so the fix aligns. React 19.3.0 verified; app is on 19.2.8.

### `flow-06` Evidence drawer is a data-catalog panel, not proof of the number the user clicked

`high` · `gap` · effort `L` · corrected

- **Evidence:** frontend/src/components/AppContext.tsx:35-53: DrawerSource has no value, definition, filter, SQL or as-of field, so a KPI opens a static descriptor. components/mortgage/EvidenceDrawer.tsx:232 fetches metadata only when `canAccessAdmin`; 256-257 sets freshness 'restricted' otherwise. Copy at 583 ('Compact semantics: Overview de-duplicates this governed family's…') and 620 ('Sanitized signals').
- **Problem:** A sceptic who clicks '89,553' never sees that number again, its plain-English rule, the filters applied, the query or an as-of time. Non-admin buyers see freshness 'restricted'. The drawer proves tables exist, not that this number is right.
- **Recommendation:** Phase 1 (M): pass value, a one-sentence definition, predicate chips and as_of into setDrawer from KpiCard; lead the drawer with 'How we got 89,553'; add a non-admin freshness endpoint returning refreshed_at and row count only; move manifest semantics under an 'Under the hood' tab. Phase 2 (M): reproduce SQL emitted by the backend from the governed query that produced the number, never composed client-side. Catalog Explorer links already exist; keep them.
- **Tech:** Reuse BorrowerProofDrawer's reproduce-SQL pattern; add a non-admin `/api/assets/{key}/freshness` returning refreshed_at and row count only; no new library.
- **Verifier:** DrawerSource (AppContext.tsx:35-53) has no value, definition, filter or SQL; the drawer capture shows tables and COUNT(*), never 89,553; metadata fetch is admin-only (232) so freshness reads 'restricted'. But Catalog Explorer links already exist (EvidenceDrawer.tsx:66-73), and per-KPI reproduce SQL needs backend emission.

### `flow-08` No habit loop: no saved views, no notification inbox, watchlists hard-coded 'scheduler paused', pins in localStorage, no notes or goals

`high` · `gap` · effort `XL` · corrected

- **Evidence:** grep `savedView|saved_view|saved view` over frontend/src = 0. routes/lead-queue.tsx:479-481 offers 16 URL-only filters. routes/ask-genie.saved-monitors.tsx:42 hard-codes ' · scheduler paused'; databricks.yml:980 ships the monitor job `pause_status: PAUSED`. grep `notification|inbox|unread` matches only Slack/Teams draft types. lib/pinnedInsights.ts:27,57 stores pins in localStorage. No comment or goal code found.
- **Problem:** Nothing pulls a user back tomorrow: no named views ('My CA recapture queue'), no alert when a watchlist moves, no inbox, no note or @-mention on a borrower, no target. The Sales Manager audit deferred saved views to 'Module 1'.
- **Recommendation:** Slice by value. First (M): saved views as name plus URL params through the existing workspace API, shown as Queue tabs; the queue already round-trips all 16 filters via the URL. Second (S): render real scheduler state from the monitor API instead of the hard-coded string. Third (L): notification inbox for internal staff alerts only. Defer borrower notes and @mentions as an owner decision because new free-text about borrowers widens the PII and fair-lending scrub surface.
- **Tech:** New mip_app tables via jobs/lakebase_migrate.py (zero-click deploy); TanStack Query refetch-on-focus polling; existing mip_growth_agent_monitor_scheduler job; native Popover API (Baseline Newly available 2025) for the inbox.
- **Verifier:** grep savedView = 0; ' · scheduler paused' is hard-coded (ask-genie.saved-monitors.tsx:42); job ships PAUSED by design (databricks.yml:980, cost-control comment); pins use localStorage; no inbox or notes. Saved leads, drafts and campaigns do exist. Three Lakebase primitives plus inbox plus mentions is XL.
- **Constraint conflict:** CLAUDE.md: 'Do not overbuild Modules 1-4 before Module 0 is stable'; docs/audits/persona-walkthroughs/04-sales-manager.md:280 defers named saved views to Module 1. Free-text borrower notes add a new surface the fail-closed PII guards must cover.

### `flow-v1` Home's 'Approval queue' count is the whole-book refi screen, but its CTA opens a contactable-only queue

`high` · `defect` · effort `S` · verifier-added

- **Evidence:** frontend/src/routes/home.tsx:24 requests the preview with marketing_eligibility 'Any'; :113 sets queued = high_intent_leads; :285-293 prints it and links to /lead-queue?segment=itm. lead-queue.tsx:141 defaults to 'Eligible only'. SegmentCard.tsx:90-106 already states contactable; Home does not.
- **Problem:** The banner named 'Approval queue' quotes the whole-book screen, then lands on a contactability-filtered queue. On live gold the two differed about 23x (74,335 vs 3,217, measured 2026-08-10). The first click of the demo shows disagreeing numbers.
- **Recommendation:** State both numbers on the banner, as SegmentCard does: 'N contactable of M passing the screen', with N from an 'Eligible only' preview so it equals the queue's total-matching footer. Rename the banner or count genuinely pending approvals. Add an e2e assertion that the banner number equals the queue footer.
- **Tech:** Existing /api/portfolio/preview with marketing_eligibility 'Eligible only' and the X-Total-Matching header; no new library.

### `flow-07` Ten flat nav chips with no grouping, no pending-work badges and no sense of position in the build-to-audit flow

`medium` · `upgrade` · effort `M` · corrected

- **Evidence:** frontend/src/components/layout/RouteNav.tsx:24-33,54-76 renders ten flat chips. 'Borrower 360' and 'Offer' open 'Choose a borrower…' empties (routes/borrower-360.tsx:102; offer-orchestrator.route-ui.tsx:41). segment-intelligence.tsx:871-882 embeds a second LeadTable plus the Home map. Portfolio, Segments and Leads each carry 11-16 near-identical filters. `route-nav` is absent from design_files (grep = 0).
- **Problem:** Navigation mirrors the pipeline's tables, not the user's jobs. Three pages are 'filters + ranked table' variants, two tabs dead-end until a borrower is chosen, no tab shows pending work, and nothing shows position in build → approve → audit.
- **Recommendation:** Keep every contracted route and URL. Group the chip strip visually by job (Today, Target, Work, Ask, Measure, Govern) using the same .route-nav BEM; show Borrower 360 and Offer as breadcrumbed detail under Work only once lastBorrowerId exists; add count badges for pending approvals and stale approved from existing endpoints. Treat merging Portfolio and Segments into one audience builder as a separate owner decision, outside this slice.
- **Tech:** react-router 8 nested routes with `<Outlet>`; badges from existing /api/portfolio/preview counts and /api/sales/aging; prototype `.filter` chip BEM retained.
- **Verifier:** RouteNav renders ten flat chips with no badges; route-nav is absent from design_files. But the entity tabs are not dead ends: they redirect to lastBorrowerId and their index pages mount TopLeadsQuickPick plus a CTA. Merging Portfolio and Segments conflicts with the eight contracted routes.
- **Constraint conflict:** Merging Portfolio and Segments or dropping tabs conflicts with CLAUDE.md's 'All eight routes are navigable' completion criterion and with the e2e specs that pin route headings.

### `flow-09` Segments' secondary filters and map drill are not in the URL, dossiers carry no queue context, and two buttons open one drawer

`medium` · `defect` · effort `M` · corrected

- **Evidence:** frontend/src/routes/segment-intelligence.tsx:251-254 holds 11 filters in useState; only four lender keys are read from the URL (255-265), onChange handlers (712-777) never write it, and map drill is local (279-283). grep `location.state|returnTo|useLocation` in borrower-360, offer-orchestrator and LeadRowPreview = 0. borrower-360.tsx:527 and 569 both call setProofOpen(true).
- **Problem:** Refresh, Back or a shared link on Segments drops seven filters and the map drill. A dossier offers no 'back to my filtered queue' and no next/previous borrower, so a 50-lead list costs 50 round-trips. Two buttons open one drawer.
- **Recommendation:** As written, but extend the file's existing useSearchParams and segmentSearchParamsForState helpers instead of adding nuqs for one route, and treat <ViewTransition> as optional polish behind the React 19.3 bump (the app is on 19.2.8; Firefox's implementation lacks view-transition types, so keep it progressive enhancement).
- **Tech:** nuqs 2.10.1 (npm-verified); queryClient.prefetchQuery; React Router Link `viewTransition` prop with React 19.3 stable `<ViewTransition>`; same-document view transitions Baseline Newly available since October 2025 (web-verified).
- **Verifier:** Segments DOES URL-sync selected segments and mode both ways (segment-intelligence.tsx:249-250, 519-523); the seven secondary chip filters and map drill are useState only, and four lender keys are read but never written. No queue context in dossiers (grep = 0); both buttons call setProofOpen (527, 569).

### `flow-10` Copy is written for auditors, not buyers, and /ask-genie buries the ask box beneath a workflow console

`medium` · `upgrade` · effort `M` · corrected

- **Evidence:** frontend/src/routes/ask-genie.tsx:523 lede ends '…automation is identified only when the configured capability is ready'. Primary workflow buttons at 555, 672 and 749 precede 'Ask a question', which starts near y=1190 (dark__ask-genie__full.png). lead-queue.tsx:481: 'without hand-editing the URL'. segment-intelligence.tsx:600-601: 'de-duplicated OR cohort'. portfolio-builder.governance.tsx:58: 'Server treatment preflight'.
- **Problem:** Hero copy describes plumbing and claim boundaries rather than outcomes. The page named Ask Genie opens on a workflow console with the question box below the fold. It is accurate, but it reads as engineering notes rather than product.
- **Recommendation:** As written, plus: update the ten e2e specs that pin current headings and ledes (module0, real_data, accessibility, responsive, cross_browser_matrix, route_performance, layout-stability, growth_agent_live, buyer_wow_live, lineage_explorer) in the same slice, and keep the capability caveats reachable in the proof panel so the 'do not claim unproven integrations' rule still holds.
- **Tech:** Existing GlossaryTerm and lib/mortgageGlossary.ts; a vitest 4 copy-lint over route ledes with a banned-token list (preflight, de-duplicated, semantics, hand-editing); no new dependency.
- **Verifier:** All quoted strings reproduce at the cited lines; AskGenieAnswerPanel mounts at ask-genie.tsx:801 after the Growth Agent console, confirmed in the 1440 capture. But ten e2e specs pin these headings and ledes and the route is 852 lines, so S is naive. Floating Genie softens impact.

### `flow-v2` Approve and reject are terminal: no revoke, undo or correction path in the UI or API

`medium` · `gap` · effort `M` · verifier-added

- **Evidence:** backend/api/outreach.py exposes only POST /draft (1536), /approve (1628) and /reject (1965); grep revoke|rescind|unapprove over backend/api = 0. LeadTable.logic.ts:134-136 treats approved, rejected and hold as terminal. LeadTable.tsx:777-780 approves on a bare 'A' keypress.
- **Problem:** A mis-keyed or mistaken approval cannot be corrected by the approver; the decision stays in the governed queue and funnel. One-keystroke approve with no confirmation makes that error likelier.
- **Recommendation:** Add an approver-only, rationale-required 'Revoke approval' action, allowed only while outreach_status is none or queued. It appends a revoke event to action_audit (never deletes the approve row), returns the lifecycle to pending and triggers the existing lifecycle sync. Surface it from the Approved chip and the approval receipt.
- **Tech:** New FastAPI POST /api/outreach/revoke reusing require_approver, request_id idempotency and the audit store; no new library or infrastructure.

**Overflow (titles only, not verified)**

- Portfolio Builder's default build lands in a role=alert 'Save blocked' state (routes/portfolio-builder.governance.tsx:41-42,77). Make it a contacts-vs-10,000 budget meter with narrowing suggestions.
- The ROI projector renders 14 tiles, six reading 'Not qualified' or 'Insufficient denominators' on any new tenant (portfolio-builder.components.tsx:302-332). It needs progressive disclosure and a single empty state.
- There is no first-run onboarding, guided sample path or contextual help beyond the Glossary (grep onboarding|tour|coachmark = 0). driver.js 1.8.0 (npm-verified) is a lightweight candidate.
- There is no role-based Home. SessionResponse carries no role, so all four personas land on the same page and the same CTA.
- Offer Orchestrator shows two Approve CTAs (offer-orchestrator.tsx:611 and 780), unstyled native selects (744-774) and a white subject input at the trust moment.
- Degraded Home drops the KPI row entirely, and the map reports 'Borrowers in selection 0' instead of an unknown state (dark__state__degraded-warehouse-down__home.png).
- Number format differs by page: Home shows '89,553' and Analytics shows '89.55K' for the same measure.
- Property lookup is duplicated in the Console (Console.tsx:195) and at the foot of Lead Queue (lead-queue.tsx:741).
- Console panel content clips at its right edge at 1440 in both themes (state__console-open-home screenshots). This truncates 'My recent activity', the only non-admin audit surface.
- TopLeadsQuickPick chips read 'Owner 48291 · 94' while every other surface uses 'B-48291' (TopLeadsQuickPick.tsx:52).
- Home KPIs still lack a period selector and a snapshot export or share. Head of Growth walkthrough P2-G9 and P2-G10 were never remediated.
- Audit explorer rows print action and event type as the same raw code twice (admin-config-populated__full.png).
- Analytics 'Pipeline Metrics' body copy carries table names and sync caveats (routes/analytics.sections.tsx:76-83). Move them to a proof tooltip.
- There is no goals or progress tracking (weekly approvals target, campaign pacing) anywhere in the product.
- Segment cards have no per-card action such as 'Send to queue' or 'Start campaign'. The page has no primary CTA at all (segment-intelligence.tsx:602-613, 861).
- 'Your book today' restates the four KPI cards in prose directly beneath them (home.tsx:271-273), using about 170px of above-the-fold space.
- The left rail spends 72px of permanent width on one active module and four disabled roadmap placeholders (Rail.tsx:27-33). This is prototype-mandated; consider an owner-approved collapse.

**Limitations:** I did not run the app, so these were not measured on the live app:
- Click counts and time-to-first-insight come from code paths and fixture screenshots only.
- Live latency, warm-up sequence and real-data density were not observed.
- The non-admin experience was judged from code (canAccessAdmin gates) because every screenshot used an admin session.

Screenshot coverage gaps:
- No Genie answer state exists in the screenshot set. Answer cards, follow-ups, pinning, the proof panel and the Genie-to-queue cohort handoff were judged from code and the r5 audit only.
- The Borrower proof/math drawer, approve and reject flows, toasts, the bulk-approve bar, hover cards and the campaign-prefill banner were not captured. I reviewed them in source.
- Analytics sub-tabs other than Executive were captured only in a degraded state.

Evidence I did not fully establish:
- The Lead Queue 1440 overflow shows in both theme screenshots and in the declared 1,564px of column widths. I did not isolate why the Relationship column renders wider than its declared 96px.
- I did not re-read the prototype HTML in full. I relied on CLAUDE.md, a grep showing `route-nav` absent from design_files, and the RouteNav.tsx header comment.
- 02-vp-mortgage-lending.md and 03-marketing-leader.md were read at outline level plus remediation tables, not line by line.

Version and support claims were verified on 2026-09-21. npm view gave nuqs 2.10.1 (peer react-router ^8), driver.js 1.8.0, @tanstack/react-table 9.2.4 and sonner 2.0.8. WebSearch confirmed React 19.3 stable `<ViewTransition>` and same-document view transitions as Baseline Newly available.

Sources:
- https://react.dev/blog/2026/09/09/react-19-3
- https://web.dev/blog/same-document-view-transitions-are-now-baseline-newly-available
- https://reactrouter.com/how-to/view-transitions

---

## Motion, micro-interactions and perceived polish (`motion`) — grade C+

**Auditor:** The motion culture is sound: reduced-motion coverage, one-shot entrances, 16 of 19 keyframes compositor-only, and ambient effects deliberately removed. Against a Linear or Stripe bar it still falls short. No overlay has a working exit animation, there is exactly one `:active` rule in the CSS, there are no view or shared-element transitions, an undefined easing token silently disables three transitions, and the flagship sparkline draw completes in about 60ms of its 700ms.

Inventory of `components.css`:
- 19 `@keyframes`.
- 40 `transition:` declarations, 9 of them `transition: all`.
- Non-compositor properties transitioned: `padding-right` on `.main`, `filter` (x2), `left` (x2), and `box-shadow`.

**Verifier on the grade:** Fair to slightly harsh. The inventory (19 keyframes, 40 transitions, nine `transition: all`, one `:active`, undefined `--ease-standard`, ~50-106ms sparkline draw) all reproduced exactly, but nearly every defect is an S-size CSS fix on sound foundations (tokens, reduced-motion coverage, one-shot gating), and three findings were overstated (one evidence chip per lead row, prototype-verbatim row pointer, 'all day' shimmer), so B- is closer than C+.

**Strengths (keep these)**

- **Disciplined reduced-motion coverage** — components.css:45-53 sets a global 0.01ms animation/transition clamp. There are also 19 per-rule `prefers-reduced-motion` blocks, including the hover-lift suppression at :793-795. useRevealOnScroll.ts:18-25 reveals immediately under reduced motion, and tokens.css:322-326 clears the body transition.
- **Motion restraint is an explicit, documented team value** — tokens.css:328-333 records that the ambient halo animations were removed after measured GPU lag. KpiCard.tsx:14-19 and modernization-todo.md:672-675 record the declined count-up. useFirstAppearance.ts keeps a module-level seen-set so KPI and Sankey entrances fire once per page load, keyed on the label rather than the value (KpiCard.tsx:81-82).
- **Keyframes are overwhelmingly compositor-only** — 16 of the 19 `@keyframes` in components.css animate only opacity and/or transform (route-in :5015, cmdk-panel-in :2172, map-level-in :3592, seg-emanate :5214, approve-burst :5241 and others). The three exceptions are topbar-heartbeat (box-shadow), skeleton-shimmer (background-position) and spark-draw (stroke-dashoffset).
- **Stagger and drag details show real craft** — The ZIP tile stagger is index-driven and capped: USChoroplethMap.tsx:769 uses `Math.min(tileIndex, 24)` and components.css:3991 multiplies it by 16ms, for a 384ms maximum. The Genie panel deliberately keeps width/height out of its transition so resize-drag tracks the pointer (components.css:2511-2517).
- **Rich, correct cursor vocabulary** — components.css uses nwse/ns/ew/nesw-resize on the Genie resize handles, grab/grabbing on the Genie header (:2609-2612), zoom-in, and help on glossary terms (:2419). `.seg-card` is deliberately `cursor: default`, with the pointer reserved for the real `.seg-card__select` button (:893-897).


### `motion-01` Overlay exits are broken or absent: drawer and Genie panel vanish instantly; palette, menus, hover card and proof drawer hard-cut

`high` · `defect` · effort `M` · corrected

- **Evidence:** components.css:2050 transitions only `transform`; :2054 `.drawer:not(.is-open){visibility:hidden}` and :2519 (Genie) flip instantly, hiding the 360ms slide-out. Prototype index.html:665-676 has no visibility rule. CommandPalette.tsx:197 returns null; FilterSelect.tsx:96 and GenieAnswer.tsx:284-292 mount conditionally; `.tweaks` toggles display:none (:4040-4043).
- **Problem:** Every overlay closes with a hard cut; the Genie proof drawer and Console also open with none. Three drawers behave three different ways. The scrim fades while the panel pops, which reads as a glitch on the most-used evidence interaction.
- **Recommendation:** Drawer and Genie: add `visibility 0s linear var(--dur-slow)` to the base transition and `transition-delay:0s` on `.is-open` (also `transition-delay:0s !important` in the reduced-motion reset). Console (`.tweaks`, always mounted): `@starting-style` + `transition-behavior: allow-discrete` on display. React-unmounted overlays (palette, filter menu, hover card, Genie proof drawer in GenieAnswer.tsx): `@starting-style` gives the entry only; for the exit add a small `usePresence` hook (stay mounted until transitionend; instant under reduced motion and jsdom so existing unmount tests hold) or, after the React 19.3 bump, `<ViewTransition exit>` with the close wrapped in startTransition. Release focus traps when the exit starts. Exits about 0.6x the enter duration with an ease-in token.
- **Tech:** CSS `@starting-style` + `transition-behavior: allow-discrete` (Baseline Newly available Aug 2024: Chrome 121, Safari 17.5, Firefox 129; verified by web search). The visibility-delay fix works in every browser.
- **Verifier:** Reproduced: components.css:2050-2055 and :2519-2524 flip visibility instantly; prototype index.html:665-676 has no such rule; palette, filter menu and Genie proof drawer unmount. But `@starting-style` + allow-discrete cannot animate a React unmount (entry only); those overlays need a presence hook or must stay mounted.
- **Constraint conflict:** Animating the Console open/close is additive to the prototype, which hard-cuts `.tweaks` via display none/block (Module 0 Prototype.html:904-906). State it in the commit. The drawer/Genie fix restores prototype behaviour.

### `motion-03` No View Transitions: every navigation is a blank-then-fade remount with no shared-element continuity

`high` · `upgrade` · effort `L` · corrected

- **Evidence:** app.tsx:68 `key={pathname}` remounts each route; components.css:5012-5018 fades new content from opacity 0. grep `viewTransition|ViewTransition|startViewTransition|view-transition` in frontend/src: 0 hits. main.tsx:16 uses BrowserRouter; node_modules/react-router/dist/development/lib/dom/lib.js:163-164 already wraps navigations in `React.startTransition`. package.json pins react 19.2.8.
- **Problem:** Old content is cut and the new page fades in from nothing, so persistent context (borrower ID, segment, KPI) teleports. Theme switching relies on piecemeal colour transitions over ten selectors (components.css:29-44) with mixed timings.
- **Recommendation:** Phase 1 (M): bump react/react-dom to exactly 19.3.0 (12 days old; run every gate), wrap the routed subtree in `<ViewTransition>`, hoist `<Suspense>` above the keyed element so the old page holds while a lazy chunk loads, keep `route-in` as the fallback where `document.startViewTransition` is missing, honour reduced motion, and wrap `setTheme` in `document.startViewTransition` + flushSync. Phase 2 (M-L): named shares for the lead-row borrower ID to the Borrower 360 title and segment card to segment header. Drop the KPI-to-drawer pair: EvidenceDrawer never unmounts, so use motion-01's CSS slide there. Re-baseline the Playwright visual and layout-stability specs.
- **Tech:** React 19.3 `<ViewTransition>` stable since 2026-09-09 (verified on react.dev). Same-document View Transitions Baseline Newly available Oct 2025 (Firefox 144). React Router docs confirm `viewTransition` is unavailable in Declarative mode.
- **Verifier:** Reproduced app.tsx:68, route-in, zero view-transition hits, BrowserRouter startTransition (lib.js:163-164). npm: react 19.3.0 is `latest`, published 2026-09-09; react.dev blog confirms stable ViewTransition. But EvidenceDrawer is always mounted (no share pair), Suspense sits inside the keyed div, and full scope is L.
- **Constraint conflict:** No SSR or framework change needed (client-only API). Deleting `route-in` outright would remove all route motion in browsers without View Transitions; keep it as the fallback.

### `motion-02` No pressed state anywhere: one `:active` rule in 6,630 lines of CSS

`medium` · `gap` · effort `S` · corrected

- **Evidence:** grep `:active` across components.css, tokens.css, analytics.scatter.css: one hit, `.genie__resize:active` (components.css:2555). Scripted scan of every `<button className>` in non-test TSX: 28 of 29 base classes lack `:active`; `.switch`, `.sw`, `.cmdk__row`, `.trusted-asset` also lack `:hover`. design_files prototypes: zero `:active`.
- **Problem:** Clicks on Approve, filters, chips, tabs, rail items and rows give no press feedback until the network answers. Linear, Stripe and Vercel acknowledge pointer-down within a frame; this UI feels inert by comparison.
- **Recommendation:** Add a pressed layer: `.btn:active{translate:0 1px}` plus a pressed background. `--accent-pressed` does not exist: define it in every theme and accent block of tokens.css (lines 184, 229, 247-258) or derive it with color-mix from `--accent-hover`. `scale(.98)` for chips/cards via `.seg-card:has(.seg-card__select:active)`, a 60-80ms `--dur-instant`, none under reduced motion. Cover `.filter`, `.evidence-chip`, `.topbar__icon-btn`, `.rail__item`, `.drawer__tab`, `.switch` and interactive `.tbl` rows. Add a components.test.ts assertion. Additive to the prototype (it defines none); say so in the commit.
- **Tech:** Plain CSS `:active` with individual transform properties (`translate`, `scale`; Baseline widely available) and `:has()` (Baseline 2023). No dependency.
- **Verifier:** Reproduced: the only `:active` rule is `.genie__resize:active` (components.css:2555); design_files have zero. Overstated: Approve swaps to 'Approving...' on click, `.cmdk__row` hover exists via JS `.is-active` (CommandPalette.tsx:345), and `--accent-pressed` is not defined in tokens.css. A polish gap, not high.
- **Constraint conflict:** Additive to the design contract: the prototypes define no `:active` state. Acceptable if called out in the commit message.

### `motion-04` Undefined `--ease-standard` token silently disables transitions; the motion token scale is thin and bypassed by magic numbers

`medium` · `defect` · effort `S` · confirmed

- **Evidence:** components.css:204, :1743, :1798 use `var(--ease-standard)`; grep for its definition across frontend/src and design_files: none. tokens.css:106-109 defines three durations and one easing. Literals bypass them: 700ms (:831), 1.2s x4, 1.4s (:4948), 1.6s (:2024), `ease` (:602), `ease-in-out` x4.
- **Problem:** Those three declarations are invalid at computed-value time, so the Cmd-K keycap, last-login numbers and claim chips snap. One ease-out curve serves enters, exits and infinite loops. There is no spring, exit curve or stagger token.
- **Recommendation:** Define `--ease-out` (the current curve), `--ease-in`, `--ease-in-out`, `--ease-spring: linear(...)`, `--dur-instant: 80ms`, `--dur-exit` and `--stagger-step`; alias `--ease-standard`. Replace the literals. Extend design-system/components.test.ts to assert every `var(--x)` resolves. It would also catch the undefined `--shadow-1` (:4253) and `--stroke` (:3965).
- **Tech:** CSS `linear()` easing for springs (Baseline since Dec 2023, now widely available; verified by web search). A Vitest token-resolution test; no new dependency.
- **Verifier:** Reproduced. My own scan of all four CSS files, excluding the 15 custom properties set from TSX, found exactly three undefined no-fallback tokens: --ease-standard (3 uses), --shadow-1, --stroke. tokens.css:106-109 matches. Visible effect is small; the CI token-resolution guard is the real value.

### `motion-05` Layout and paint properties are animated on the largest surfaces

`medium` · `defect` · effort `M` · corrected

- **Evidence:** components.css:414 `.main{transition:padding-right}` on the `container-type:inline-size` root (:419); Console itself pops via display:none (:4040). :4233-4236 animates `filter` on refresh regions, declared only on `.is-updating`. :4948 shimmer animates `background-position` (63 Skeleton sites). :306-308 heartbeat keyframes `box-shadow` infinitely. Nine `transition: all`.
- **Problem:** Opening the Console relayouts the page and re-evaluates the container-query bands every frame, across the map and the virtualized table. The saturate filter snaps off. Shimmer and heartbeat repaint on the main thread all day.
- **Recommendation:** Do the cheap, certain fixes first: move the `filter` transition onto the base `.stable-refresh-region` rule (or an opacity veil), replace `transition: all` with explicit property lists, move switch knobs with `translate`, drop `will-change: background-position` and `will-change: box-shadow`. Then take a DevTools Performance trace of the Console toggle on /lead-queue before touching the gutter; if frames drop or container-query bands flip mid-tween, snap the gutter. The shimmer and heartbeat pseudo-element rewrites are optional. Do not slide the Console unless the owner accepts that prototype departure.
- **Tech:** Compositor-only properties (transform, opacity, translate). Drop the ineffective `will-change: background-position` and `will-change: box-shadow`. No library.
- **Verifier:** All cited lines reproduce (:414/:419/:442, :4233-4236, :4948, :302-308, :4097, :6191; nine `transition: all`). But nothing was measured: shimmer runs only while loading and the heartbeat is a 6px dot, so 'all day' is overstated. Several targets are verbatim prototype CSS.
- **Constraint conflict:** The prototype itself uses `transition: all` (.kpi, .genie, .sw) and hard-cuts `.tweaks` (Module 0 Prototype.html:904-906). Replacing those and sliding the Console are departures from the design contract; benign, but each must be named in the commit.

### `motion-06` One-time motion is not one-time: Reveal replays on every visit, the approve burst fires on stale approvals, and unrevealed content prints blank

`medium` · `defect` · effort `S` · corrected

- **Evidence:** useRevealOnScroll.ts:13-41 observes on every mount with no `useFirstAppearance` gate; app.tsx:68 remounts per pathname; borrower-360.tsx:467 wraps the Trigger timeline (below the 900px fold in the screenshot). components.css:5259 permanent `will-change`. print.css has no `.reveal-on-scroll` rule. offer-orchestrator.tsx:802-806 mounts `.burst` from durable status.
- **Problem:** The most-visited dossier hides its trigger timeline at opacity 0 and re-fades it over 360ms for every borrower. Printing before scrolling yields a blank evidence block. The green approval celebration replays for approvals made days ago.
- **Recommendation:** Gate Reveal with `useFirstAppearance(key)` so later mounts render already visible; do not use `animation-timeline: view()`, which is scroll-linked and would replay. Remove the permanent `will-change`. Add `.reveal-on-scroll{opacity:1 !important;transform:none !important}` to print.css. Render `.burst` only when the approve mutation succeeded in this session (a local flag), never from durable `approval_status`.
- **Tech:** The existing `useFirstAppearance` hook. Scroll-driven animations as progressive enhancement (Chrome 115+, Safari 26+, Firefox still behind a flag; verified by web search).
- **Verifier:** Reproduced: lib/useRevealOnScroll.ts has no session gate, print.css has no reveal rule, offer-orchestrator.tsx:368-372 and :802 derive `.burst` from durable status. But `animation-timeline: view()` is scroll-linked and replays on every scroll-back, contradicting one-shot; Firefox still ships it behind a flag.

### `motion-08` Value and structure changes snap: row expand, bars, counts, tabs, toasts and pending buttons

`medium` · `gap` · effort `L` · corrected

- **Evidence:** LeadTableRow.tsx:290 conditionally mounts `tr.tbl__expand`; :91 swaps `down`/`chevright` icons. components.css:4433-4436 bar `width:var(--bar-pct)` without transition; :2228-2241 tabs have none. LeadTable.tsx:123,426 'toast' is inline text removed after 4s. LeadTableRow.tsx:269 swaps label to `Approving…`; grep `spinner|@keyframes spin`: 0.
- **Problem:** Filter changes, expansions and approvals all resolve as hard cuts, so users lose track of what changed. There is no shared toast region and no loading-button primitive. Lead Queue approvals get no success feedback, while the Offer page does.
- **Recommendation:** Split into three slices. (1) CSS-only: rotate one chevron, fade expand content with `@starting-style` (opacity only, so virtualizer row heights are untouched), animate bars by translating a full-width fill inside the overflow-hidden track (scaleX distorts the rounded end), add a tab background transition. (2) A `Button loading` prop (fixed width, spinner, aria-busy) and one shared aria-live toast region replacing LeadTable's inline text. (3) After motion-03: number cross-fade on change via `<ViewTransition update>`. Skip `@number-flow/react`: rolling digits is the declined ticker. Reuse `.burst` on in-session row approval only.
- **Tech:** React 19.3 ViewTransition; optional `@number-flow/react` 0.6.2 (React 19 peer, verified via npm view); CSS `@starting-style` (Baseline 2024).
- **Verifier:** Every cited line reproduces; no spinner, toast region or Button loading prop exists. But NumberFlow is rolling digits, which components.css:816-818 explicitly declined; scaleX squashes the fill's rounded end; and it touches the 1,261-line LeadTable with roughly 20 test files.
- **Constraint conflict:** `@number-flow/react` conflicts with a recorded owner decision (components.css:816-818: 'Deliberately NOT a number-rolling count-up ... not a casino ticker'). All additions are additive to the prototype.

### `motion-v1` No scroll reset or restoration on navigation: the persistent `.main` scroller keeps its offset across routes

`medium` · `defect` · effort `S` · verifier-added

- **Evidence:** AppShell.tsx:174 `<main className="main">` persists outside the keyed route div (app.tsx:68); components.css:412 `overflow-y:auto`. grep `scrollTo|scrollTop|ScrollRestoration` in frontend/src: only GenieChat.tsx:175. BrowserRouter (main.tsx:16) has no ScrollRestoration, which is Data-mode only.
- **Problem:** Opening a borrower from a scrolled Lead Queue can land mid-page when the route chunk is already preloaded, and Back never returns to the row you left. Any future view transition would snapshot the wrong offset.
- **Recommendation:** Add a `useMainScrollMemory` hook in AppShell: in a layout effect keyed on `location.key`, set `main.scrollTop = 0` on PUSH/REPLACE and restore a saved offset on POP from a session Map. Land it before the View Transition work so snapshots capture the final offset. Add a Playwright assertion.
- **Tech:** useLayoutEffect + `useNavigationType` from react-router (works in Declarative mode). No dependency.

### `motion-v2` Genie panel jump-scrolls to the end of every new answer, so the Summary-first composition lands off-screen; chat messages have no entrance

`medium` · `defect` · effort `S` · verifier-added

- **Evidence:** GenieChat.tsx:174-176 sets `bodyRef.current.scrollTop = scrollHeight` on every msgs/typing/open change. components.css:2498-2503 caps the panel at 640px. components.css:2662-2680 `.genie__msg` has no animation; none of the 19 keyframes targets chat messages.
- **Problem:** Multi-section answers with charts exceed the panel, so users land at the follow-up chips and must scroll up to find the Summary. Charts that render late shift the end after the jump. Answers appear as a hard cut after the typing dots.
- **Recommendation:** Stick to bottom only while typing. When an AI message lands, scroll its top into view (`scrollIntoView({block:'start'})`, smooth unless reduced motion). Add a one-shot fade/translate entrance for newly appended `.genie__msg` only, never for restored transcripts. Additive to the prototype; say so in the commit.
- **Tech:** scrollIntoView plus one CSS keyframe using existing `--dur-base`/`--ease` tokens; `overflow-anchor` for late chart growth.

### `motion-07` The sparkline draw-in finishes in about 50-100ms of its 700ms, and the area fill pops in

`low` · `defect` · effort `S` · corrected

- **Evidence:** components.css:829-831: `stroke-dasharray:220; stroke-dashoffset:220; animation: spark-draw 700ms var(--ease) 80ms`. Sparkline.tsx:24 defaults to 64x20, so the path is ~64-124px long. Solving cubic-bezier(.2,.8,.2,1) numerically, the line is fully drawn after 51-106ms. The area path (Sparkline.tsx:71) has no animation.
- **Problem:** The flagship KPI entrance reads as a blink, not a left-to-right draw. The remaining ~600ms animates an invisible dash gap while the gradient fill appears instantly underneath.
- **Recommendation:** Set `pathLength={1}` on the stroke path and use `stroke-dasharray:1; stroke-dashoffset:1`, so progress maps 1:1 to the visible line. Use a gentler ease-in-out token for draws. Fade the area fill over the same window. Update KpiCard.entrance.test.tsx to assert the attribute.
- **Tech:** The SVG `pathLength` attribute (universally supported) plus CSS. No dependency.
- **Verifier:** Math re-derived numerically: with dasharray 220 and cubic-bezier(.2,.8,.2,1), a 64-124px path is fully drawn at 50.5-105.8ms of 700ms; the area path has no animation. The pathLength fix is sound. Impact inflated: a 64x20px, once-per-session flourish.

### `motion-09` Hover affordance mismatches: KPI cards lift without being clickable, static schema and expand rows inherit the prototype's row pointer, proof-tab hover equals active

`low` · `defect` · effort `S` · corrected

- **Evidence:** components.css:788-792 `.kpi:hover` lifts 2px with `--kpi-glow`, but KpiCard.tsx:103 is a plain div without handlers; prototype index.html:455 only changes border-color. components.css:1233-1234 gives every `.tbl tbody tr` pointer and hover, including static schema rows (routes/asset.tsx:180). :2407 `.proof-tab:hover` equals `.is-active`.
- **Problem:** The UI promises clicks that do nothing, and departs from the prototype to do it. Hovering a proof tab makes it indistinguishable from the selected tab.
- **Recommendation:** KPI: either revert `.kpi:hover` to the prototype's border-only change (index.html:455), or add a stretched evidence button using the existing `.seg-card__select` pattern; a whole-card button would nest the EvidenceChip button. Tables: keep the prototype default and add an opt-out `.tbl--static` modifier for routes/asset.tsx, plus `tr.tbl__expand:hover{cursor:default;background:var(--bg-1)}`. Give `.proof-tab:hover` a lighter treatment than `.is-active`.
- **Tech:** CSS only. The `.tbl--interactive` BEM modifier is additive to the prototype vocabulary.
- **Verifier:** KPI div with lift/glow, asset.tsx static rows and proof-tab hover=active reproduce. But `.tbl tbody tr{cursor:pointer}` is verbatim prototype index.html:617, the KPI lift was deliberate (commit 8b9efea8 'wow-factor motion'), and a whole-KPI button would nest the EvidenceChip button.
- **Constraint conflict:** The proposed `.tbl--interactive` opt-in would flip a rule copied verbatim from the prototype (index.html:617-618). An additive opt-out modifier keeps the design contract intact.

### `motion-10` Evidence hover card: 110ms show delay, no exit fade, estimated-height flip

`low` · `upgrade` · effort `S` · corrected

- **Evidence:** EvidenceHoverCard.tsx:19 `SHOW_DELAY_MS = 110`; :20 `ESTIMATED_CARD_H = 132` decides above/below; :87-95 hides on any scroll or resize; :104-106 unmounts with no exit. Every EvidenceChip attaches it (Primitives.tsx:101). grep `anchor-name|position-anchor|popover` in the CSS: 0 hits.
- **Problem:** Sweeping the pointer across the 15-column queue pops cards almost immediately and repeatedly. By the 50th day this is noise. Placement can mis-flip when the content exceeds the height estimate.
- **Recommendation:** S-size fix inside EvidenceHoverCard.tsx: raise the first-open delay to about 300-400ms, skip the delay when another card closed within 300ms (module-level timestamp), add an 80ms fade-out through a closing state, and measure the rendered card height in a layout effect before choosing above or below. CSS anchor positioning (Chrome 125, Safari 26, Firefox 147; Baseline newly available) is optional progressive enhancement with the JS path as fallback. Skip `popover="hint"` until Safari ships it; if used, feature-detect in JS.
- **Tech:** CSS Anchor Positioning: Chrome 125+, Safari 18.2+ (`@position-try` needs 18.4+), Firefox 147+ (verified by web search). Treat it as progressive enhancement.
- **Verifier:** Constants and unmount reproduce. Overstated: LeadTableRow.tsx renders one EvidenceChip (line 241), not chips across 15 columns. Anchor positioning is Safari 26 (not 18.2) and Firefox 147; `popover=hint` has no Safari support and cannot be detected with `@supports`.

**Overflow (titles only, not verified)**

- Map drill-down has no spatial continuity: `.map-levels` keyed remount plays only an opacity/scale(0.985) settle (components.css:3584-3595). A viewBox zoom-to-state tween, gated on reduced motion, would be a wow upgrade.
- SegmentCard emanate (1.2s) plays on the initial mount of an already-selected card: `useState(selected ? 1 : 0)` at SegmentCard.tsx:81 makes `emanateKey > 0` at :150, so it replays on every route re-entry with a selected segment.
- The KPI entrance replays on every loading-to-loaded cycle within one mount: `isFirst` stays true and the value node is recreated (KpiCard.tsx:82, 89-103). KpiCard.entrance.test.tsx:46-52 covers only the first cycle.
- `usePrefersReducedMotion` is dead code: grep finds 0 importers, and no SMIL `<animate>` remains in frontend/src.
- Reduced motion is a global 0.01ms kill switch (components.css:45-53); no reduced-motion alternative exists (e.g. an opacity-only fade replacing the drawer slide).
- Undefined tokens outside motion: `var(--shadow-1)` at components.css:4253 (box-shadow silently dropped) and `var(--stroke)` at :3965.
- Sankey stagger is hard-coded to nth-of-type(1..5) (components.css:4520-4524); a 6th or later ribbon gets no delay. Use an index custom property like the ZIP tiles.
- Timing is not distance-proportional: the Genie panel takes 360ms for a 12px move (components.css:2515-2517), while the palette takes 200ms for 8px (:2084).
- The route Suspense fallback to content swap is a hard cut: the `route-in` animation is consumed by the wrapper before lazy content arrives (app.tsx:68-69).
- Genie chat messages and answer sections append with no enter transition and no progressive text reveal; none of the 19 keyframes targets `.genie__msg` or `.genie-answer`.
- Skeleton.tsx:5-6 says the shimmer is keyed off `--dur-slow`, but components.css:4948 uses a literal 1.4s; each skeleton runs an unsynchronised sweep.
- No disabled styling for non-`.btn` controls: only 4 disabled rules exist in the CSS, yet `.filter`, `.topbar__icon-btn`, pager buttons and Console buttons are disabled in TSX (AdminAuditExplorer.tsx:361,373; Console.tsx:212).
- Density switching animates padding on `.kpi`, `.seg-card`, `.btn` and `.filter` (via `transition: all`) while tables snap, so the density flip is incoherent.

**Limitations:** This was static analysis only. I did not run the app, record a DevTools performance trace, or observe any animation in motion. The Playwright MCP server failed to connect, and the orchestrator's screenshots are static fixture renders, which I used only to place the Trigger timeline below the 900px fold.

Not measured, inferred from code:
- The frame cost of the `.main` padding-right transition and of the skeleton and heartbeat repaints comes from property semantics and container-query structure.
- The blank-print claim for `.reveal-on-scroll` comes from CSS and IntersectionObserver semantics, not a reproduced print. IntersectionObserver does not fire for print layout, and print.css has no reset.
- The sparkline timing is a numeric solve of the easing curve against the geometric path-length bounds of a 64x20 viewBox, not a visual capture.

Not tested:
- React 19.3 `<ViewTransition>` against React Compiler 1.0.0, react-router 8.3.0 Declarative mode, the virtualized table, or the strict CSP.
- Firefox, which historically mishandled `allow-discrete` on `display` and needs a check in the E2E browser matrix.

Browser-support and release-status claims were verified by web search and fetch on 2026-09-21 (react.dev 19.3 blog, reactrouter.com view-transitions how-to, web.dev/MDN summaries) and by npm view.

The live Databricks deployment was not inspected.

---

## Responsive behaviour, theming, density, print, i18n and formatting (`responsive`) — grade C+

**Auditor:** The responsive engineering (named container queries, an 8-anchor 1280-3840 Playwright matrix, zero raw hex in component CSS) is genuinely above average, but the theming layer has defects visible today: the Console clips its own controls at 1440x900, five of eight theme x accent combinations fail contrast, there is a first-paint theme flash with no OS-preference support, and the light-theme hero map loses its encoding. Number/date formatting is fragmented across 5+ parallel formatters, print is shallow and partly dead code, and there is no white-label or stage mode - well short of a Linear/Stripe bar.

**Verifier on the grade:** Slightly harsh: every cited defect reproduced, but most sit off the default path (light theme, non-default accents, Console-open), and the foundation is strong (container-query bands, 8-anchor plus 3-engine/device Playwright matrix, two-theme axe suite, tested lib/time and lib/formatters, zero raw hex), while white-label and stage mode are wishlist rather than deficits. B- is fairer; the Console clipping at 1440x900 and the unreported Console/Genie collision keep it below B.

**Strengths (keep these)**

- **Container-query reflow backed by a real viewport matrix** — frontend/src/design-system/components.css:419-420 names `.main` an inline-size container and 4863-4926 define six bands, so Console-open vs closed reflows without viewport guesses; frontend/tests/e2e/responsive.spec.ts:66-80 asserts KPI/segment/layout column counts at 1280, 1366, 1440, 1600, 1920, 2560, 3440 and 3840, plus 390px phone cases. Ultra-wide caps are deliberate (1920 content, 2400 hero map, 1600 tables, components.css:433-436, 5416-5432).
- **Token discipline is excellent** — grep over components.css finds 0 raw hex and 0 `rgba(` literals; grep over src/**/*.tsx finds 0 inline hex colours. Deliberate prototype divergences carry inline rationale (frontend/src/design-system/tokens.css:171-176, 211-214). This is the precondition that makes runtime theming and white-label cheap.
- **The right formatting foundations exist** — frontend/src/lib/time.ts:21-39 pins naive SQL timestamps to UTC and every formatter attaches a short zone name; Console.tsx:245-251 renders `<time dateTime title>`; frontend/src/lib/formatters.ts:30-36 (signedBps, no '+-422' or '-0'), 55-63 (rangeLabel collapse) and 76-86 (K-to-M boundary after rounding) show careful edge-case thinking. The problem is adoption, not design.
- **Honest, tested degrade story below the desktop target** — components.css:5293-5296 states mobile-first is out of scope rather than faking it; the Console becomes a bottom sheet under 1280 (5329-5343); console-layout.spec.ts:159-244 pins the sheet geometry and the Genie panel's 16px gutters at 390 and 320px; cross_browser_matrix.spec.ts runs Chromium/Firefox/WebKit plus Pixel 7, iPhone 15 and iPad Pro 11.
- **Preference persistence is safe and motion preferences are respected** — AppContext.tsx:96-105 validates stored values against an allow-list inside try/catch (private-mode safe); about 20 `prefers-reduced-motion` blocks in components.css; GPU-heavy ambient backgrounds were removed for ultra-wide/high-DPI performance with the reasoning recorded (tokens.css:328-333).


### `responsive-01` Console clips its own controls at 1440x900 (grid blowout inside the 300px panel)

`high` · `defect` · effort `S` · corrected

- **Evidence:** components.css:4051-4054 `.tweaks__body{display:grid}` has no `minmax(0,1fr)` track; PropertyLookupPanel.tsx:110-122 header carries a nowrap chip. Screenshot dark__state__console-open-home.png: 'Light', 'Compact' and lookup fields clipped at 1440x900. console-layout.spec.ts:134-135 asserts only vertical scroll.
- **Problem:** The Console, a first-class shell element and the only home of Density and Accent, overflows horizontally inside its panel; half of each segmented control sits off-panel behind a sideways scroll.
- **Recommendation:** Restore prototype parity: `.tweaks__body{display:flex;flex-direction:column}` (design_files/index.html:913), or keep grid with `grid-template-columns:minmax(0,1fr)`. Scope `.tweak-row label` to direct children so it stops restyling PropertyLookupPanel field labels. For `compact`: wrap the header chip, single-column `.field-grid`, `.form-input{inline-size:100%;min-inline-size:0}`. Assert `.tweaks__body` scrollWidth<=clientWidth in console-layout.spec.ts at 1440, 1280 and 390.
- **Tech:** CSS Grid minmax(0,1fr) (universal); `container-type:inline-size` on `.tweaks` for the compact lookup (container queries, Baseline widely available).
- **Verifier:** Reproduced: screenshot shows Light/Compact clipped; .tweaks__body is an auto-track grid (components.css:4051) holding a ~430px min-content lookup. But Density/Accent also live in admin-config.tsx:490-536; prototype index.html:913 uses flex-column; `.tweak-row label` (4055) also leaks into lookup field labels.

### `responsive-02` Theme x accent token matrix fails contrast in five of eight user-selectable combinations

`high` · `defect` · effort `M` · corrected

- **Evidence:** tokens.css:254 `[data-accent]` follows and overrides the light block's accent (228-231), so light renders dark-tuned colours: focus ring (346) 1.91:1; red/teal `--chip-text` (248,252) 1.48/1.15:1 on evidence chips; dark+navy `--accent-ink` 2.06:1; components.css:1899 'Not a live offer' disclaimer banner 1.29:1. Ratios computed.
- **Problem:** Light theme is patched per component (20 `[data-theme=light]` overrides) instead of at token level, so new components re-break it; the compliance disclaimer and evidence chips become unreadable in selectable combinations.
- **Recommendation:** Fix at token level with the pattern already started at tokens.css:273-275: add `[data-theme=light][data-accent=X]` blocks for `--accent` and `--chip-*`, light values for `--status-*-ink`, a dark+navy `--accent-ink`, and point `:focus-visible` at `--accent-ink`. Keep dark hex verbatim for prototype parity; light-dark()/oklch derivation is optional later. Extend accessibility.spec.ts's THEMES loop with accents and an opened offer-mock modal, then delete the per-component overrides.
- **Tech:** light-dark() (Baseline newly available May 2024, widely available 2026-11-13, verified); relative color syntax (Baseline 2024); contrast-color() (Baseline newly available 2026-04-10, verified; progressive). Token names unchanged.
- **Verifier:** Reproduced: [data-accent] (tokens.css:246-259) outranks the light block by source order; I recomputed 1.91, 1.48, 2.06, 1.29; counted 20 light overrides. But a two-theme axe matrix already exists in accessibility.spec.ts:73-202, and the prototype (index.html:164-178) carries the same cascade bug.
- **Constraint conflict:** Deriving accents via oklch() would change prototype-specified hex values (design_files/index.html:164-178); keep dark values verbatim and call out the light-theme departure in the commit.

### `responsive-04` The same metric renders with different precision, sign and unit across routes

`high` · `defect` · effort `L` · corrected

- **Evidence:** analytics.sections.tsx:92 renders addressable via `fmt()` as '89.55K'; Home KpiCard.tsx:49 gives '89,553'. Rate spread: formatters.ts:30 signed int vs borrowerStory.ts:96 and analytics.sections.tsx:187 raw floats vs portfolio-builder.components.tsx:304 `toFixed(1)`. Five compact-USD formatters (formatters.ts:76, portfolio-builder.logic.ts:531, analytics.lib.ts:106, EvidenceDrawer.tsx:156, admin-config.tsx:95).
- **Problem:** `fmtCurrency` yields '$-4.41M', `formatUsdCompact` yields '$1000K' at 999,600, axis ticks read 24.1K/18.08K; 87 bare `toLocaleString()` calls follow browser locale while currency is pinned en-US, so separators can disagree on one screen.
- **Recommendation:** Extend the existing lib/formatters.ts (do not add a parallel lib/format.ts): module-cached en-US Intl.NumberFormat instances for count, compact (one fraction digit), usd, usdCompact, percent and bps with signDisplay; fix `currency()` constructing a formatter per call. Fold in analytics.lib fmt/fmtCurrency, formatUsdCompact and EvidenceDrawer formatNumber. Add the ESLint no-restricted-syntax ban with a shrinking per-file allowlist, since lint runs --max-warnings 0.
- **Tech:** Intl.NumberFormat notation:'compact', signDisplay, style:'percent'/'unit' (Baseline widely available); ESLint 10 no-restricted-syntax; cached formatters avoid per-cell construction.
- **Verifier:** Reproduced: fmt() compact 2dp (analytics.lib.ts:77-104) vs KpiCard toLocaleString; exactly 87 bare toLocaleString and 43 toFixed; `$1000K` and `$-4.41M` follow from the code. But lib/formatters.ts already is the tested home; EvidenceDrawer:156 and admin-config:95 are not compact-USD. 130 call sites is L.

### `responsive-03` No first-paint theme bootstrap, no color-scheme, no OS-preference following

`medium` · `defect` · effort `S` · corrected

- **Evidence:** AppContext.tsx:222-226 sets `data-theme` in a post-mount `useEffect`; index.html:6 static `theme-color`, no bootstrap script; tokens.css:317 body transitions background. Grep `prefers-color-scheme|color-scheme|matchMedia` over src+index.html: only reduced-motion hits. Ten native `<select>` plus LeadTableDecisionPanels.tsx:153 `datetime-local`.
- **Problem:** Cold load paints white, then dark, then cross-fades to light for light-theme users; the OS preference is ignored; native select popups and the date picker render light-scheme inside the dark UI.
- **Recommendation:** Ship `public/theme-boot.js` (classic blocking script, CSP-safe) setting data-theme/accent/density AND data-console from localStorage before paint; add `color-scheme` per `[data-theme]` plus the meta tag; add `color-scheme:light` inside print.css's @media print, which relies on Canvas/CanvasText; update theme-color at runtime. Treat a System option and OS-following default as an owner decision; keep dark as the default.
- **Tech:** color-scheme and prefers-color-scheme (Baseline widely available); a classic blocking script from 'self' keeps the strict CSP intact, no nonce or hash needed.
- **Verifier:** Confirmed: data-theme set in a post-mount effect (AppContext.tsx:222-226), no boot script, zero color-scheme hits, CSP script-src 'self' (main.py:489), dist-root files are served. Impact inflated: dark is default so only light-theme users flash. color-scheme:dark would break print.css's Canvas/CanvasText.
- **Constraint conflict:** Prototype Console has a two-state Dark/Light control and defaults to dark (design_files/index.html:2); a System option or OS-following default is an additive departure needing owner sign-off.

### `responsive-05` Light-theme choropleth ramp has almost no dynamic range on the hero geography surface

`medium` · `defect` · effort `S` · corrected

- **Evidence:** components.css:3754-3758 builds the ramp as `color-mix(var(--accent) 15-70%, var(--bg-3))`. In light theme with the default bright accent the four levels compute to about 1.01, 1.09, 1.22, 1.37:1 against empty states (#E4EDF4, sRGB approximation); dark spans 1.37-4.25:1. Visible in light__home__full.png.
- **Problem:** In light theme level 1 is indistinguishable from 'no data' and the whole ramp carries less range than a single dark-theme step, so the drill-down map stops communicating coverage.
- **Recommendation:** Keep the prototype formula in dark. In light only, mix `--accent-ink` (already navy-family per accent) into `--map-region-fill` at roughly 18/38/60/85% in oklab so the accent relationship survives; reuse the same tokens for legend swatches and zip-tile levels. Pin adjacent-step and max-vs-empty contrast in design-system/components.test.ts and cite the prototype lines in the commit.
- **Tech:** oklch() colour space plus light-dark() tokens (Baseline); no library required; legend swatches reuse the same tokens.
- **Verifier:** Formula confirmed at components.css:3754-3758 and light__home__full.png shows a pale ramp; with accent #66C5FF on --bg-3 #F0F5F9 the top level is ~1.35:1 vs empty. But the ramp is verbatim prototype CSS (index.html:863-866), so an accent-independent ramp departs from the contract.
- **Constraint conflict:** Departs from the prototype ramp (design_files/index.html:863-866) in light theme; permitted as an accessibility fix only with the commit call-out CLAUDE.md requires.

### `responsive-06` Breakpoint defects: orphaned fourth KPI at 1280 and Console-open 1440; controls vanish between 721-1023px (zoom)

`medium` · `defect` · effort `S` · confirmed

- **Evidence:** components.css:4895-4899 forces 3 KPI columns for 961-1280px containers; Home has 4 cards, so 1280x720 (responsive.spec.ts:66 pins kpiCols:3) and Console-open 1440 orphan one (console-open screenshot). components.css:5297-5310 hides `.topbar__actions` under 1024 while `.genie__fab` shows only at <=720 (2718).
- **Problem:** Common laptop and Console-open layouts show a lone KPI on its own row; at 1440 with 150% zoom, 1920 at 200% or iPad portrait, Genie, Console, theme and system status have no pointer entry, contradicting 'Genie reachable from every page'.
- **Recommendation:** Make `.kpi-row` intrinsic with `repeat(auto-fit,minmax(min(15rem,100%),1fr))`, or switch 4 cards to 2x2 via `:has(> :nth-child(4):last-child)`. Below 1024 keep the three icon buttons (hide only the pills) or collapse them into an overflow menu, and show `.genie__fab` under 1024. Add a 960x600 zoom-equivalent Playwright case.
- **Tech:** CSS grid auto-fit/minmax; :has() quantity queries (Baseline since Dec 2023, already used 48 times here); existing `main` container bands retained.
- **Verifier:** Reproduced: 961-1280 band forces 3 KPI columns (components.css:4895-4899), Home and Portfolio have 4 cards, console-open screenshot shows the orphan; topbar actions hidden <1024 (5297-5310) while .genie__fab shows only <=720 (2718). Prefer the :has() option: auto-fit lets the N-stage funnel row exceed 4-across.

### `responsive-07` Date and time rendering is fragmented and seven sites bypass the lib/time contract

`medium` · `defect` · effort `M` · confirmed

- **Evidence:** lib/time.ts:14-15 mandates its parser, yet LeadTable.logic.ts:183-193 and borrower-360.tsx:55-65 duplicate a `new Date(value)` formatter with no zone; formatRefreshed.ts:27, PortfolioSummaryCard.tsx:19, admin-config.tsx:668 also bypass. analytics.sections.tsx:92 prints raw '2026-07-14'; TriggerTimeline.tsx:24-27 hand-rolls 'mo ago'. Grep `RelativeTimeFormat`: 0.
- **Problem:** Screens mix 'Apr 23', 'Apr 23, 8:00 AM EDT', 'Jul 14, 2026', raw ISO and '5MO AGO'; zone-less naive timestamps risk the local-time shift lib/time was written to prevent; stale snapshots show no year.
- **Recommendation:** Extend lib/time.ts with `formatDate`, `formatDateTimeShort`, `formatRelative` (cached `Intl.RelativeTimeFormat`, numeric:'auto') and a `<Timestamp>` component rendering `<time dateTime title={absolute UTC}>`; show relative age on freshness chips and include the year when older than 11 months; migrate the seven bypass sites; ban `toLocaleDateString` via ESLint.
- **Tech:** Intl.RelativeTimeFormat and Intl.DateTimeFormat dateStyle/timeStyle (Baseline widely available); Temporal not required.
- **Verifier:** All cited sites open as described: duplicate zone-less formatDateTimeShort (LeadTable.logic.ts:183, borrower-360.tsx:55), new Date bypasses in formatRefreshed.ts:27, PortfolioSummaryCard.tsx:19, admin-config.tsx:668, hand-rolled relativeWhen (TriggerTimeline.tsx:17), raw snapshot_date at analytics.sections.tsx:92; zero RelativeTimeFormat hits. offer-orchestrator.tsx:793 is an eighth.

### `responsive-08` Print path is undiscoverable, partly dead CSS, clips tables and carries no provenance

`medium` · `gap` · effort `M` · corrected

- **Evidence:** Grep `window.print|beforeprint` in src: 0 hits. print.css:100-108 linearizes `.grid-2/.layout-2/.cols-2` and 120-123 `.confidence`; none exist in components.css (real: `.layoutA-grid`, `.kpi-row`, `.conf__bars`). components.css:1267-1270 `.tbl-wrap{max-height:520px;overflow:auto}` and virtualization above 120 rows (LeadTable.constants.ts:9) are not reset.
- **Problem:** Real grids stay multi-column on paper, tables clip to the scroll window, pastel dark-theme segment colours land on white, pages carry no lender, actor, timestamp or page number, and no test exercises print media.
- **Recommendation:** Start with Borrower 360 and the audit log (non-virtualized): an Export PDF button calling window.print(), fixed selectors (`.layoutA-grid`, `.kpi-row`, `.seg-grid`, `.conf`, `.genie__fab`, `.tbl-wrap{max-height:none;overflow:visible}`), paper-safe `--seg-*`, and @page margin boxes as progressive enhancement. For the virtualized Lead Queue use `flushSync` in beforeprint to de-virtualize. Write an audit event per export. Snapshot with emulateMedia({media:'print'}).
- **Tech:** @page margin boxes (Chrome/Edge 131+, verified; graceful no-op elsewhere); print-color-adjust (Baseline); Playwright emulateMedia and page.pdf.
- **Verifier:** Confirmed: zero window.print/beforeprint/print-emulation hits; .grid-2/.layout-2/.cols-2/.confidence/.fab have 0 matches in components.css; .tbl-wrap max-height 520 (1267) is unreset. Chrome 131 margin boxes verified by search. Missing: beforeprint de-virtualization needs flushSync, and exports should write an audit event.

### `responsive-09` Density is three tokens deep and never reaches the Lead Queue; no presentation/stage scale exists

`medium` · `upgrade` · effort `L` · corrected

- **Evidence:** tokens.css:284-298 density changes three tokens; `--row-h` is consumed once (components.css:1228) while lead rows measure about 86px (LeadTable.constants.ts:6); `.btn`/`.filter` paddings are literals (503-505, 3500-3502). responsive.spec.ts:132-153 asserts only the attribute. Kiosk mode 'has no code' (docs/modernization-todo.md:977).
- **Problem:** Compact barely changes the surface density exists for; type and control heights never scale, and with px font tokens (tokens.css:56-65) there is no larger-type projector mode and browser font-size preferences are ignored.
- **Recommendation:** Split it. Stage mode first (S): `:root[data-mode=stage]{zoom:1.15}` (CSS zoom, Baseline since May 2024) plus hidden route-nav/Console, toggled from Cmd-K and `?stage=1`; verify container bands in the 8-anchor matrix and land finding 06 first. Density scale second (L): add `--control-h`, `--cell-pad-y`, `--chip-h` per density and single-line compact lead rows with estimateSize from density. Defer the rem conversion.
- **Tech:** CSS custom properties with rem scaling on `:root`; attribute selectors as baseline (style() container queries optional, Firefox pending). Additive to the prototype's data-density; no class renames.
- **Verifier:** Evidence reproduces (three tokens, --row-h consumed once at components.css:1228, 86px row estimate, kiosk deferred at modernization-todo.md:977). But this equals prototype parity (index.html:180-186), and with px tokens plus 38 literal px font-sizes a root font-size stage mode is inert until a full rem conversion.
- **Constraint conflict:** Additive to the prototype's three-token density, but kiosk/stage mode was deliberately deferred by the owner (docs/modernization-todo.md:977), so it is an owner decision.

### `responsive-10` White-label runtime theming: per-lender brand colour, logo and product label from config

`medium` · `wow` · effort `L` · corrected

- **Evidence:** ConfigOptions exposes only `lender_name` (types.ts:645); accents are four fixed swatches (Console.tsx:19) whose soft/hover/chip values are hand-computed rgba per accent (tokens.css:246-259); Entrada marks are hard-wired in Rail.tsx:44 and PageShell.tsx:61. Enabler: zero raw hex in components.css and TSX.
- **Problem:** For a product sold to lenders, per-tenant branding stops at a text pill; adding a lender colour means hand-tuning about seven derived values per theme and re-auditing contrast by hand.
- **Recommendation:** Owner decision. If approved: add a validated `brand` block to /api/config/options from deploy.sh env (accent hex, label); derive accent tokens with oklch(from ...)/color-mix and a JS-clamped AA ink (contrast-color() only as enhancement); keep the four prototype swatches as default. Serve the logo from a same-origin backend endpoint (CSP img-src 'self'), keep the Entrada x Cotality x Databricks lockup, and reserve lender branding for real tenants.
- **Tech:** Relative color syntax (Baseline 2024), color-mix (Baseline widely available), contrast-color() (Baseline newly available 2026-04, verified; JS-computed ink fallback). Extends the prototype's [data-accent] mechanism.
- **Verifier:** Mostly reproduces: ConfigOptions (actually types/geo.ts:1, not types.ts:645) exposes only lender_name; four fixed swatches; Entrada marks hard-wired; zero raw hex verified. But this is speculative sales value, not a UX deficit, and it conflicts with the sample-lender naming rule and the prototype palette.
- **Constraint conflict:** CLAUDE.md forbids real customer names in the demo (sample lender Summit Mortgage) and the design contract fixes the accent palette to the prototype's four swatches; CSP img-src 'self' requires a same-origin logo reachable from deploy.sh.

### `responsive-v1` Console and floating Genie panel collide on the same right-edge anchor when both are open

`medium` · `defect` · effort `S` · verifier-added

- **Evidence:** components.css:4032-4039 `.tweaks{inset:72px 16px 16px auto;width:300px;z-index:50}`; 2498-2503 `.genie{bottom/right:16px;width:420px;z-index:30}`; Topbar.tsx:401 toggles Genie without closing Console; every `[data-console]` rule (442, 5339, 5371) targets only `.main`.
- **Problem:** With both first-class panels open at 1440x900 the full-height Console overlays the right 300px of the 420px docked Genie panel, hiding answers and the send control; the prototype's short Console never reached Genie, the app's full-height one does.
- **Recommendation:** Add `[data-console="open"] .genie:not(.is-undocked){right:calc(300px + var(--sp-4)*2)}` (340px at >=1920; offset bottom instead in the <1280 bottom-sheet band), clamp persisted undocked positions the same way, and add a bounding-box non-intersection assertion for Console x Genie x Evidence drawer in console-layout.spec.ts.
- **Tech:** Plain CSS attribute selectors on the existing data-console hook; Playwright boundingBox assertions in the existing fixture-intercepted spec.

### `responsive-v2` No visual-regression baseline guards the theme x accent x density x Console matrix

`medium` · `gap` · effort `M` · verifier-added

- **Evidence:** grep `toHaveScreenshot|toMatchSnapshot` across frontend/tests: 0 hits; responsive.spec.ts:132-153 asserts attributes and column counts only; console-layout.spec.ts:25 already serves route-intercepted fixtures, so deterministic captures are feasible; package.json has no Storybook or visual tooling.
- **Problem:** Findings 01, 02 and 05 are pixel-visible at the design-target viewport yet every suite is green; nothing protects theming and layout from regressing again after they are fixed.
- **Recommendation:** Add a fixture-intercepted Playwright `toHaveScreenshot` suite (Chromium, 1440x900 and 1280x720): Home, Lead Queue, Borrower 360 x dark/light x Console open/closed, plus one accent sweep on a chip/KPI/map specimen; disable animations, mask timestamps, commit baselines, run in PR CI. Fixtures stay test-only; no runtime mock.
- **Tech:** Playwright 1.59 toHaveScreenshot (animations:'disabled', mask, maxDiffPixelRatio); existing page.route fixtures.

### `responsive-v3` No forced-colors (Windows High Contrast) support; state encoded by background only disappears

`low` · `gap` · effort `S` · verifier-added

- **Evidence:** grep `forced-colors|prefers-contrast` in design-system/*.css: 0 hits. State is background-only in `.conf__bar.on` (components.css:1177-1181), `.tweak-row .switch.on` (4099-4103) and `.segmented button.is-active` (4080); accessibility_procurement.spec.ts emulates only reducedMotion (267).
- **Problem:** Under forced colors, backgrounds are forced to Canvas, so signal meters, toggle states and active segments vanish; enterprise lender procurement (VPAT/508) reviews commonly test this mode.
- **Recommendation:** Add one `@media (forced-colors: active)` block: system-colour pairs (Highlight/HighlightText, CanvasText borders) for active/on states, and `forced-color-adjust:none` only on meters, score chips and map legend with checked pairs. Cover with `page.emulateMedia({forcedColors:'active'})` in accessibility_procurement.spec.ts.
- **Tech:** forced-colors media query and forced-color-adjust (Baseline widely available); Playwright forcedColors emulation.

**Overflow (titles only, not verified)**

- Ultra-wide (>=2200px): content centres in a 1920px column with idle gutters; auto-dock Console/Genie as a real grid track instead of the fixed panel plus `padding-right:324px/360px` magic numbers (components.css:441, 5375-5380)
- No `forced-colors` or `prefers-contrast` support anywhere (grep over all src CSS: 0 hits); signal meters and score chips rely on background colour and vanish in Windows High Contrast
- `text-wrap: balance/pretty` unused (0 hits): hero and sub-head orphans such as the lone 'R rejects.' line on Lead Queue; balance is Baseline 2024, pretty is Chrome 117+/Safari 26 (Firefox pending, verified)
- Hero narrative renders an ASCII ' -- ' instead of an em dash on the first screen (frontend/src/lib/portfolioStory.ts:106)
- Lead Queue: 15 nowrap columns overflow at 1440x900 so Primary offer, Score, Signal and Approval start off-screen (LeadTable.tsx:1038-1050); no sticky identity/approval columns, no column-priority hiding or column chooser
- Preferences are device-local only: not roamed via Lakebase workspace state, no cross-tab `storage` sync for theme (only pinnedInsights.ts:94 listens), `showEvidence`/`showConfidence` not persisted (AppContext.tsx:173-174), theme keys survive an actor change on a shared booth machine (actorScopedBrowserState.ts:5)
- 38 literal px font-sizes in components.css despite the 'no inline pixel values' rule, plus px `--fs-*` tokens
- Console bottom sheet uses `50vh` (components.css:5336-5341) rather than `dvh`; mobile Safari URL bar overlaps the sheet
- Analytics axis ticks are not 'nice' and mix decimals on one axis (24.1K / 18.08K from COMPACT_FORMAT maximumFractionDigits:2, analytics.lib.ts:77-80)
- `<html lang="en">` and all copy hard-coded with no message catalog; acceptable for US-only, blocks Spanish-language or Canadian lenders later
- No e2e coverage for `colorScheme` emulation, print media, the theme x accent matrix, or density geometry (grep `emulateMedia` in tests: one reducedMotion hit only)
- `Intl.NumberFormat style:'percent'` never used; percent precision differs by site (formatters.ts:13 one decimal, analytics.lib.ts:499 adaptive, portfolioStory.ts:91 adaptive inverse, admin-config.tsx:92 three decimals)

**Limitations:** Read-only audit: I did not run the app, Playwright, a build or any browser, so viewport behaviour at 1280x720, 1366, 1920, 2560, tablet, phone, browser zoom and print preview is inferred from CSS and tests; the only rendered evidence is the other agent's fixture screenshots at 1440x900 (dark and light, default bright accent) - no shots exist for other accents, other viewports, zoom or print. The Console overflow (responsive-01) rests on one screenshot plus CSS reasoning, not a live scrollWidth measurement. Contrast ratios were computed from token hex values with straight sRGB alpha/mix blending; the code uses `color-mix(in oklab)`, so map-ramp figures are approximations (direction is confirmed visually in light__home__full.png). I did not verify the wire format of the timestamps feeding the formatters that bypass lib/time (naive vs offset-bearing), so the timezone-shift risk in responsive-07 is medium confidence while the fragmentation itself is certain. Firefox/Safari rendering was not checked. Platform status verified by WebSearch on 2026-09-21: light-dark() (web-features explorer, widely available 2026-11-13), contrast-color() (newly available 2026-04-10), @page margin boxes (Chrome 131, developer.chrome.com/blog/print-margins), relative color syntax (Baseline 2024), text-wrap pretty/balance support; :has() and Intl API Baseline status are stated from knowledge, not re-verified. frontend/dist was not read, per instructions.

---

## Frontend quality infrastructure for UI excellence (`quality`) — grade C

**Auditor:** The backend-grade rigor (OpenAPI wire gate, bundle budget gate, mock-leak bans, unusually thoughtful behavioral e2e canaries, very clean TS) is real, but the UI-specific safety net is mostly absent or dormant: zero screenshot assertions, 3 of ~138 Playwright tests run on PRs, the only workflow that runs axe/responsive/perf last succeeded 97 days ago, there is no error boundary or client exception reporting, no component workbench, hand-written API types have already drifted, and RUM ships disabled with non-standard math and no dashboard or alert. Against a Linear/Stripe/Vercel bar that is a C.

**Verifier on the grade:** Slightly harsh but defensible: every cited gap reproduced (no error boundary, zero screenshot assertions, 3 of 138 Playwright tests on PRs, live suite last green 2026-06-16), yet the auditor under-credits 137 Vitest files, the CSS containment contract tests, and a procurement a11y spec that already enforces the WCAG 2.2 target-size floor, focus order and reduced motion. C+ is fairer than C; nothing here supports a B.

**Strengths (keep these)**

- **OpenAPI wire-contract gate already exists, so type codegen is a one-step add** — tests/fixtures/openapi_baseline.json (811 KB, 211 schemas, 189 paths) is regenerated deterministically by tools/regen_openapi_baseline.py and tests/unit/test_openapi_contract.py fails on both breaking and unsnapshotted additive drift.
- **Bundle budget gate runs on every PR with a written headroom policy** — ci.yml:105 runs tools/check_frontend_budgets.mjs: eight gates (initial JS/CSS raw+gzip, total JS, largest lazy chunk, exact font count of 14, font bytes) with dated, feature-named re-baseline comments.
- **Behavioral e2e canaries of unusual quality** — route_performance.spec.ts:278-352 proves hover/idle prefetch never reads audited borrower/lead/evidence APIs; :233-276 proves QueryClient dedupe; layout-stability.spec.ts (779 lines) asserts anchored geometry across async refreshes; accessibility.spec.ts:135 runs axe in both themes; procurement spec covers target size, focus order, reduced motion.
- **Mock-leak and native-dialog bans are lint-enforced** — frontend/eslint.config.js:66-94 bans src/mocks imports from production code (no silent mock fallback); lines 52-62 ban prompt/alert/confirm repo-wide with remediation messages.
- **Very clean source hygiene without tooling** — Grep of non-test frontend/src: 0 @ts-ignore/@ts-expect-error, 0 `as any`, 2 eslint-disable, 0 hex/rgb literals in TS/TSX, 25 inline style sites; heuristic unused-export scan found only 4. Prototype token divergences are all commented (tokens.css:27-29, 171, 211). rum.ts:29-47 strips borrower/CLIP/UUID ids before send.


### `quality-01` No error boundary, no client exception capture, no stale-chunk recovery: any render error blanks the whole app silently

`critical` · `defect` · effort `M` · corrected

- **Evidence:** frontend/src/main.tsx:13 calls createRoot() with no onUncaughtError/onCaughtError options; frontend/src/app.tsx:69 wraps Routes in Suspense only. Grep of frontend/src for ErrorBoundary|componentDidCatch|getDerivedStateFromError|onerror|unhandledrejection|vite:preloadError returns nothing; lib/lazyPreload.ts:11-14 rethrows chunk-load failures.
- **Problem:** Any render exception, or a lazy-chunk 404 in a tab left open across a deploy (hashed immutable assets), unmounts the entire React root: blank page, no recovery, and no report reaches anyone. This contradicts the 'fails visibly, degraded-state UI' contract.
- **Recommendation:** Ship in two steps. Step 1 (S): shell-level plus per-route boundary (a 30-line class or react-error-boundary) rendering the existing degraded-state callout with retry, and one guarded reload on vite:preloadError. Step 2 (M): React 19 root error callbacks and window error/unhandledrejection listeners posting to a rate-limited client-error endpoint that runs messages and routes through the existing PII/route sanitizers (error text can carry borrower ids and query strings). Emit hidden sourcemaps kept as a CI artifact per git_sha; without them production component stacks are minified and unreadable.
- **Tech:** react-error-boundary 6.1.6 (peers React ^19, verified via npm view); React 19 createRoot onUncaughtError/onCaughtError/onRecoverableError (stable); Vite `vite:preloadError` event (stable).
- **Verifier:** Reproduced: main.tsx:13 bare createRoot, app.tsx:69 Suspense only, zero grep hits for boundaries or error listeners, lazyPreload rethrows; index.html is no-store while hashed assets are immutable, so stale-tab chunk 404s are real. Effort understated: sanitized endpoint, OpenAPI baseline, sourcemaps.

### `quality-02` Visual regression coverage is zero: three orphaned baselines and no toHaveScreenshot call anywhere

`high` · `defect` · effort `M` · confirmed

- **Evidence:** Grep toHaveScreenshot|toMatchSnapshot|toMatchAriaSnapshot over frontend/tests and frontend/src: zero hits. Commit 15619b66 (2026-05-12) deleted all three assertions from demo_visual.spec.ts, yet the three PNGs and playwright.config.ts:37 snapshotPathTemplate remain. The spec is E2E_LIVE-only (line 4).
- **Problem:** A product whose contract is pixel parity with design_files has no pixel gate on 11 routes, two themes, density/accent axes or any overlay state, while all component CSS lives in one 6,630-line file where any edit can regress distant screens.
- **Recommendation:** Build a fixture-backed Playwright VRT project: every route x dark/light plus drawer, Cmd-K, Genie panel, expanded row, degraded and empty states. Determinism: reducedMotion 'reduce', page.clock.setFixedTime, animations 'disabled', masked canvas, document.fonts.ready, Linux baselines generated in the pinned Playwright Docker image. Upload the HTML diff report on every PR.
- **Tech:** Playwright toHaveScreenshot + page.clock (stable; 1.63.0 current, repo pins 1.59.1). Owner decision: Argos 7.6 / Chromatic 18.9 need a token, which ci.yml:8 forbids in PR CI.
- **Verifier:** Reproduced: zero toHaveScreenshot hits; three orphaned PNGs tracked under demo_visual.spec.ts-snapshots; commit 15619b66 removed the three assertions; components.css is 6,630 lines; the only visual net today is regex tests over CSS source. Effort M holds only after shared fixtures (quality-09) exist.

### `quality-03` UI quality gates do not gate: 3 of ~138 Playwright tests run on PRs and the on-demand suite last passed 97 days ago

`high` · `defect` · effort `M` · corrected

- **Evidence:** .github/workflows/ci.yml:296-298 runs 3 of 138 Playwright tests on PRs. nightly.yml:24-25 is workflow_dispatch-only; `gh run list` shows its last success 2026-06-16 (latest attempt 2026-07-29 cancelled). Nine specs (2,401 lines, including offline-capable module0/home_summary/console-layout) are named in no workflow.
- **Problem:** Axe, responsive, cross-browser, layout and route-performance suites have not executed in CI for 97 days, across the 256 to 396 KiB shell growth and the Genie thread redesign. Twenty-one fixture tests needing no credentials run nowhere.
- **Recommendation:** Add a credential-free e2e-fixture PR job on one runner (build, vite preview, workers=4) running module0, home_summary pinned mode, console-layout, geo_campaign_prefill, lineage_explorer and genie_proof_layout; budget time to repair stale assertions because these specs have not executed in CI. Make axe/responsive/layout dual-mode after the shared fixture layer (quality-09) lands. Keep live validation on demand, but add a release-checklist gate that blocks promotion when the last green live run is older than N days; auto-triggering it is an owner decision.
- **Tech:** Playwright projects + --shard + blob-report merge (stable); GitHub Actions workflow_run trigger; no new dependencies.
- **Verifier:** Reproduced: ci.yml runs three tests of 138 counted; nine specs (2,401 lines) appear in no workflow; gh shows last live success 2026-06-16, 07-29 cancelled. Cross-runner sharding is overkill, and the auto-live trigger contradicts nightly.yml's documented cost posture.
- **Constraint conflict:** nightly.yml:3-7 deliberately keeps live validation unscheduled to avoid Databricks meter burn; auto-running it after deploys is an owner cost decision, and the local piecewise deploy path would not fire a workflow_run trigger anyway.

### `quality-04` Hand-written API types have drifted from the backend; codegen from the committed OpenAPI baseline never landed

`high` · `defect` · effort `L` · corrected

- **Evidence:** Diffed 119 interfaces against tests/fixtures/openapi_baseline.json. types.ts:373 `ltv: number` vs backend/schemas/lead.py:248 `int | None` (borrower-360.tsx:379 prints `${b.ltv}%`); lead.py:249 ltv_basis_is_unreliable missing; types/workspace.ts:37 lacks required can_approve (backend/api/session.py:13); types/genie.ts:127 non-null vs nullable.
- **Problem:** 1,758 hand-written type lines have already drifted: a null LTV renders 'null%', and the unreliable-LTV caveat and approver capability never reach the UI. The api-contract audit called codegen 'optional polish'; it never landed.
- **Recommendation:** First (S, immediately): render an explicit unknown when ltv is null and surface the ltv_basis_is_unreliable caveat in Borrower 360. Then (M): generate types from tests/fixtures/openapi_baseline.json with @hey-api/openapi-ts pinned exact into frontend/src/api/generated, re-export legacy names from types.ts, and add a regenerate-and-git-diff CI step. Caveat: Pydantic defaults make 64 of Borrower360's 87 properties optional in the schema, so plan a response-side 'required' transform or the generated types will be looser than the wire. Typing lib/api.ts (2,195 lines) per operation is a separate L migration.
- **Tech:** @hey-api/openapi-ts 0.99.0 (peers TypeScript >=6; pre-1.0, pin exact). openapi-typescript 7.13.0 peers typescript ^5.x and conflicts with the repo's TS 6.0.3 (both verified via npm view).
- **Verifier:** Reproduced every drift: lead.py:248 ltv int|None vs types.ts number; borrower-360.tsx:379 prints `${b.ltv}%` whenever avm_value>0, so 'null%' is reachable; can_approve unused in frontend; last_activity_at nullable. Baseline has 200 of 201 typed operations. Full api.ts typing is L.

### `quality-05` Axe scans never cover open overlays, ignore moderate violations, and run only on demand

`medium` · `gap` · effort `M` · corrected

- **Evidence:** AxeBuilder appears once (frontend/tests/e2e/accessibility.spec.ts:94), tags wcag2a to wcag21aa only (line 95), gates serious/critical and console.logs moderate (102-118). ROUTES (63-71) omits /glossary, asset detail, not-found. No scan runs with EvidenceDrawer, Cmd-K, Genie panel or expanded rows open. Live-only (line 32).
- **Problem:** Accessibility is scanned only on page-load states of seven routes, only on demand. Overlays, where focus-trap, aria-modal and contrast defects concentrate, are never scanned; WCAG 2.2 rules are not enabled despite procurement target-size claims.
- **Recommendation:** Extract expectAxeClean(page, label) with wcag22aa added; in the existing live spec, scan again after opening EvidenceDrawer, Cmd-K, the Genie panel and an expanded lead row, and add /glossary, asset detail and not-found. Gate moderate violations with a dated allowlist instead of console.log. Move the helper into the PR fixture job once quality-03/09 exist. Add Storybook a11y only if quality-06 is adopted.
- **Tech:** @axe-core/playwright 4.13.0 (repo 4.11.2); axe `wcag22aa` tag (stable since axe 4.8); @storybook/addon-a11y 10.6.0. vitest-axe 0.1.0 unpublished since 2025-01: avoid.
- **Verifier:** Core true: one AxeBuilder site, wcag21aa tags, no overlay scans, live-only. Overstated: the spec also scans deep-linked Borrower 360, Offer and Admin in both themes, and accessibility_procurement.spec.ts already hand-checks the WCAG 2.2 24px target floor, focus order, names, reduced motion.

### `quality-06` No component workbench: Storybook is referenced in CLAUDE.md and ESLint but does not exist

`medium` · `gap` · effort `L` · corrected

- **Evidence:** git ls-files grep for storybook|.stories.|.mdx: none, yet CLAUDE.md and frontend/eslint.config.js:75 exempt `*.stories` files. frontend/src/test/setup.ts is a one-line placeholder; 69 test files hand-roll createRoot/act and 42 repeat IS_REACT_ACT_ENVIRONMENT. analytics.tabs.a11y.test.tsx:5-8 documents a happy-dom accessible-name artifact.
- **Problem:** A 6,630-line hand-built design system has no living catalogue. Degraded, breaker-open, Genie-withheld, zero-row, long-copy and approval states are reachable only by live accident, so they are designed and reviewed blind.
- **Recommendation:** Do the cheap part now: a shared render/act helper in src/test/setup.ts (S) to retire the 42 repeated act flags. Treat Storybook 10 (react-vite, Vitest 4.1.x) as an owner decision sequenced after the shared fixture layer and the VRT project; if adopted keep it dev-only, reuse the same scenario fixtures, and publish the static build as a PR artifact. If declined, remove the Storybook wording from CLAUDE.md and the ESLint exemption.
- **Tech:** storybook + @storybook/react-vite 10.6.0 (peers Vite ^8, React ^19); @storybook/addon-vitest 10.6.0 peers vitest ^3||^4, so stay on Vitest 4.1.x (5.0.1 shipped 2026-09-15). Ladle 5.1.1 stale; Histoire still beta.
- **Verifier:** Reproduced: no Storybook files tracked, eslint.config.js:79 exempts *.stories, setup.ts is a one-line placeholder, 67 tests hand-roll createRoot, 42 repeat the act flag; versions verified. Impact inflated: benefit overlaps quality-02/09 and adds a second build pipeline for a small team.

### `quality-07` RUM ships disabled, computes non-standard Web Vitals, and nothing aggregates or alerts on it

`medium` · `gap` · effort `L` · corrected

- **Evidence:** settings.py:495 mip_rum_enabled=False; .env.example:282 =0. lib/rum.ts:142-151 enqueues every LCP candidate, 161-176 sums CLS across the SPA session, 194 reports max event duration as INP. backend/api/telemetry.py:46 only logs. docs/observability.md: no RUM dashboard or alert (grep rum|vital|alert).
- **Problem:** Field performance is invisible: RUM is off by default, its LCP/CLS/INP math diverges from the Core Web Vitals definitions, it carries no element or long-animation-frame attribution, and nothing aggregates or alerts on it.
- **Recommendation:** Step 1 (S): replace the hand-rolled observers with the lazily imported web-vitals attribution build, tag git_sha, and report once per page lifecycle (LCP/INP work in Safari 26.2+/Firefox 144+, CLS stays Chromium-only, so this is progressive by construction). Step 2 (S): enable in the dev target; the customer default is an owner/privacy decision. Step 3 (L): pick a sink that does not keep the serverless warehouse warm (Lakebase table synced to UC, or the existing OTLP export), then declare the Lakeview dashboard and bundle `alerts` resource, provisioned through deploy.sh.
- **Tech:** web-vitals 6.2.2 (onINP/onLCP/onCLS with LoAF attribution; full in Chromium, partial in Safari/Firefox); Databricks Lakeview dashboards + SQL alerts via bundle (GA).
- **Verifier:** Reproduced: settings.py:495 defaults False; rum.ts enqueues every LCP candidate, sums CLS across the session, reports max event duration as INP, and double-reports on visibilitychange plus pagehide; telemetry.py only logs. Sink, dashboard and alerts make this L, not M.
- **Constraint conflict:** Per-batch INSERTs through the serverless SQL warehouse would keep it warm (cost); enabling telemetry by default in customer deployments is an owner/privacy decision; every new table, dashboard and alert must be declared in the bundle and reachable from scripts/deploy.sh.

### `quality-08` Performance budget is a rising change-detector; no lab Web Vitals and no size delta visible to reviewers

`medium` · `upgrade` · effort `M` · corrected

- **Evidence:** tools/check_frontend_budgets.mjs:15 sets each gate to 'current actual + ~5%'; lines 46-52 re-baseline initial JS from 256.6 to 396.4 KiB. route_performance.spec.ts:22-28 budgets loadEventEnd at 4-7 s, live-only. ci.yml:105 prints the report to the log; no step summary or PR comment.
- **Problem:** The budget ratchets upward with every feature, so it detects change rather than enforcing a target; +54% shell growth passed. There are no lab LCP/INP/CLS assertions and reviewers never see size deltas.
- **Recommendation:** Keep the ratchet as a change detector but add absolute targets with a dated waiver file; write a base-vs-head per-chunk delta table to $GITHUB_STEP_SUMMARY and upload a treemap artifact (rollup-plugin-visualizer 7.1 supports rolldown) instead of a PR comment. Add the CPU-throttled lab vitals spec inside the fixture PR job once it exists. Note that reaching roughly 100 KiB gzip initial JS (119.85 today) requires moving the 48-asset evidence registry out of the shell chunk.
- **Tech:** Playwright CDPSession Emulation.setCPUThrottlingRate + web-vitals 6.2.2; actions/github-script; sonda 0.14 or rollup-plugin-visualizer 7.1 treemap artifact. @lhci/cli 0.15.1 unpublished since 2025-06: skip.
- **Verifier:** Reproduced: written policy is 'current actual + ~5%'; initial JS re-baselined 256.60 to 396.40 KiB; route budgets are 4-7 s loadEventEnd, live-only; ci.yml:105 only logs. Sticky PR comments need pull-requests:write and fail on forks, against ci.yml's fork-parity policy.
- **Constraint conflict:** ci.yml:8-9 requires fork PRs to behave identically with no secrets; a GITHUB_TOKEN sticky comment needs pull-requests: write and fails on fork PRs.

### `quality-09` E2E API mocks are hand-rolled per spec and never validated against the OpenAPI contract

`medium` · `upgrade` · effort `M` · corrected

- **Evidence:** Per-spec hand-rolled page.route mocks: module0 12, geo_campaign_prefill 11, home_summary 10, lineage_explorer 9, genie_proof_layout 9, layout-stability.spec.ts:119/:318. A config/options payload is pasted into 8 specs. src/mocks/fixtureData.ts (164 lines) has two importers. No fixture is validated against openapi_baseline.json.
- **Problem:** Six divergent fake backends mean fixture UIs can pass while the real contract has moved, and every new visual, a11y or story state must re-invent its API. It is the main blocker to PR-time UI coverage.
- **Recommendation:** Build one scenario fixture library (JSON per API domain plus degraded, empty, breaker-open and Genie-withheld overrides) under frontend/src/mocks, typed from the generated OpenAPI types and consumed through a thin page.route adapter. Validate every fixture in pytest against the real Pydantic response models, which needs no new dependency and cannot drift from the backend. Add msw only if Storybook is adopted; leave the existing vi.mock unit tests alone. Keep the ESLint production-import ban and the mock-leak test.
- **Tech:** msw 2.15.0 (stable); @msw/playwright 0.6.7 (pre-1.0; a 20-line page.route adapter is the fallback); msw-storybook-addon 3.0.3. Service worker only in Storybook, so the app CSP is untouched.
- **Verifier:** Reproduced: page.route counts match (module0 12, geo 11, home_summary 10, lineage 9, genie_proof 9); config/options mocked in nine specs; helpers.ts has two helpers; fixtureData.ts has two importers. MSW is optional: 39 Vitest files already mock lib/api with vi.mock.

### `quality-10` Lint depth: e2e specs unchecked, untyped ESLint rules, overdue compiler TODO, eight 'use no memo' opt-outs, colors-only CSS lint

`medium` · `upgrade` · effort `L` · corrected

- **Evidence:** eslint.config.js:10 and tsconfig.json:19 cover src only: 24 e2e specs are never type-checked or linted. Line 33 uses untyped `recommended`; line 43 disables set-state-in-effect (TODO dated 2026-07-15). Eight 'use no memo' opt-outs include LeadTable.tsx:76 and all analytics files. lint_css_literals.mjs:7 checks colors only.
- **Problem:** Floating promises, React anti-patterns and token bypasses (45 raw px spacing/font declarations in components.css) pass lint; the React Compiler silently skips the heaviest interactive surfaces and nothing tracks compiled coverage.
- **Recommendation:** Sequence it: (1) S: add a tests tsconfig and lint tests/e2e so the 23 specs are type-checked; (2) M: enable only no-floating-promises and no-misused-promises via projectService, triage, then widen toward recommendedTypeChecked; (3) M: stylelint selector-class-pattern derived from the prototype BEM grammar with an explicit exception list (strict-value rules are secondary, token discipline is already good); (4) S: React Compiler logger reporting compiled/skipped counts in CI, retiring the pragmas together with the set-state-in-effect TODO in the query-layer migration.
- **Tech:** typescript-eslint 8.70.1 (peers ESLint ^10, TS <6.1); @eslint-react/eslint-plugin 5.20; stylelint 17.15.0; babel-plugin-react-compiler `logger` option. eslint-plugin-jsx-a11y 6.10.2 peers ESLint <=9: incompatible, skip.
- **Verifier:** Reproduced: ESLint and tsconfig cover src only, untyped recommended rules, overdue 2026-07-15 TODO, eight 'use no memo' sites, colors-only CSS lint. 23 specs, not 24; about 42 raw-px declarations in 6,630 lines, so strict-value lint is low-yield. Five workstreams under --max-warnings 0 is L.

### `quality-v1` No shared e2e hygiene fixture: uncaught page errors, console errors and CSP violations do not fail tests

`medium` · `gap` · effort `S` · verifier-added

- **Evidence:** grep page.on('console'|'pageerror') hits only module0.spec.ts:109 and growth_agent_live.spec.ts:85-90; securitypolicyviolation has zero hits; tests/e2e/helpers.ts is 28 lines with two helpers. The PR job serves vite preview, which never sends the CSP defined at backend/main.py:485-496.
- **Problem:** In 21 of 23 specs an uncaught exception, console error or CSP-blocked resource passes silently, and the strict production CSP is never exercised before deploy.
- **Recommendation:** Export a shared `test` via test.extend with an auto fixture collecting pageerror, console.error, securitypolicyviolation and failed same-origin requests, asserting none after each test (small allowlist). Serve the PR fixture build with the production CSP via Vite preview.headers, pinned to the Python constant by a unit test.
- **Tech:** Playwright test.extend auto fixtures; Vite preview.headers; DOM securitypolicyviolation event (Baseline widely available).

### `quality-v2` No dependency-update automation; exact pins are already drifting behind current releases

`medium` · `gap` · effort `S` · verifier-added

- **Evidence:** No .github/dependabot.yml or renovate.json (.github holds only workflows). package.json pins exact versions; npm view on 2026-09-21: Playwright 1.59.1 vs 1.63.0, Vite 8.0.16 vs 8.3.0, React 19.2.8 vs 19.3.0, ESLint 10.2.1 vs 10.11.0, typescript-eslint 8.59.0 vs 8.70.1, Vitest 4.1.4 vs 5.0.1.
- **Problem:** Staying current depends on manual sweeps; updates arrive only when npm audit reddens CI, so tooling and browser-engine fixes lag by months.
- **Recommendation:** Commit a Renovate or Dependabot config scoped to frontend/: weekly grouped PRs (React, Vite/Vitest, lint, Playwright), lockfile maintenance, and majors such as TypeScript 7 and Vitest 5 held for manual approval (typescript-eslint peers TS <6.1; addon-vitest peers Vitest ^4). PRs ride the existing credential-free CI; no automerge until UI checks are required.
- **Tech:** Renovate or GitHub Dependabot version updates with groups; config file only, no runtime dependency.

**Overflow (titles only, not verified)**

- No machine-checked parity between design_files/index.html tokens (87 custom props) and tokens.css (124): add a small parity test first; DTCG JSON + Style Dictionary 5.5.5 only if Figma becomes a token source (owner decision). Current divergences are all comment-documented.
- No Dependabot/Renovate (.github holds only workflows): Playwright 1.59.1 vs 1.63.0, @axe-core/playwright 4.11.2 vs 4.13.0, happy-dom 20.10.3 vs 20.14.5, typescript-eslint 8.59 vs 8.70.1, vitest 4.1.4 vs 4.1.11 drift unattended.
- Frontend unit coverage is unmeasured (backend has --cov-fail-under=83; vite.config.ts test block has no coverage config and @vitest/coverage-v8 is not installed): add a ratcheted floor.
- Vitest projects split (node / happy-dom / browser): 72 per-file happy-dom pragmas today; Vitest 4 browser mode with toMatchScreenshot suits layout- and a11y-tree-sensitive components.
- Playwright ARIA snapshots (toMatchAriaSnapshot) as a cheap deterministic structure-regression gate for shell, nav, drawers and tables.
- PR design-review bot: sticky comment with before/after screenshot grid per changed route, built from the VRT job artifacts.
- 22 waitForTimeout sleeps in e2e (13 in layout-stability.spec.ts): replace with web-first assertions or page.clock.
- playwright.config.ts:38-39 workers:1 / fullyParallel:false also throttles fixture specs that share no live state: split into projects.
- docs/testing.md (43 lines) is stale: no spec matrix, no env-flag reference (E2E_LIVE, E2E_LAYOUT_MOCK, GENIE_PROOF_LAYOUT_MOCKED, ECONOMICS_SCATTER_MOCKED, E2E_BROWSER_MATRIX), no snapshot-update procedure; still lists 'Genie fallback'.
- knip 6.37 for unused exports/deps: heuristic scan found only 4 unused (e.g. AppShell.tsx useIsHealthDegraded, types.ts OfferType) and 7 test-only exports, so low priority.
- Live Playwright failure artifacts are disabled by design (nightly.yml:977-981): add a redacted-trace mode so live failures are debuggable.
- tsconfig.json has strict:true but not noUncheckedIndexedAccess or exactOptionalPropertyTypes.

**Limitations:** Read-only audit: I did not run Vitest, Playwright or a build, so suite runtime and flake rate are unmeasured (only whole-workflow CI wall-clock of about 11 minutes from `gh run list`). frontend/dist was never read; bundle numbers come from the dated comments in tools/check_frontend_budgets.mjs. The types-vs-OpenAPI diff is a name-matched regex heuristic (119 of 133 interfaces; inheritance resolved; enum/union narrowing and optional-vs-default not analysed), so the true drift count is probably higher than the six confirmed by hand. The unused-export scan is a token-count heuristic, not knip. Branch-protection required checks were not inspected. Tool versions and peer ranges were verified today with `npm view`; Vitest browser-mode/visual-regression and Storybook 10 addon-vitest behaviour were verified via web search of official docs, but Vitest 5.0.1 (released 2026-09-15, after my knowledge cutoff) features were not reviewed beyond npm metadata. I viewed one fixture screenshot (home, dark) only to confirm fixture rendering is feasible; a sibling agent's 768-line scratch harness produced 104 deterministic shots across all routes and both themes this session, which supports the M effort estimate for quality-02 but is not repo evidence.

---

## API-to-UI delivery path and perceived latency (`delivery`) — grade B-

**Auditor:** Resilience semantics, static-asset delivery and audit-safe client caching are genuinely strong. But the delivery model is still fetch-on-render with an in-memory-only client cache, hard-expiry server caches, a polling Genie transport and a fixed top-500 lead list; none of the 2026 "feels instant" techniques (render-as-you-fetch, persisted cache, server SWR, streaming, keyset pagination) are in place, and a routine serverless warehouse resume is presented to the user as an outage.

**Verifier on the grade:** Slightly harsh; B is fairer. Every structural gap the auditor names is real. But three of its high-impact claims were inflated on inspection. The hero collapse occurs only on real 503s, not routine resumes. The 628 KB lead payload is 45 KB gzipped. Chunks already preload on hover and idle, and Genie already shows stage, SQL and an elapsed ticker. The existing single-flight, stale-if-error and reason-aware retry design is also stronger than the summary credits.

**Strengths (keep these)**

- **Static delivery is state of the art for the Databricks Apps hosting model** — backend/main.py:825-842 negotiates build-time precompressed .br/.gz hashed assets (brotli q11 / gzip 9 per main.py:535-539 comment) with Vary: Accept-Encoding; main.py:515-519 stamps public, max-age=31536000, immutable on /assets; main.py:897 keeps index.html no-store so a deploy never pins a stale hash.
- **Cold-start / overload protocol is unusually complete end to end** — Structured retryable 503/429 bodies with reason codes; frontend/src/lib/retryPlan.ts:23-80 picks a cadence per reason (warming_up, breaker_open, rate_limited, dependency_saturated); api.ts:816-838 honours Retry-After with jitter; resilience.py:478-538 gives single-flight + stale_if_error on the server TTL cache.
- **Audit-safe query defaults, deliberately reasoned** — queryClient.ts:14-18 disables focus refetch because reads write VIEW_* audit rows; queryKeys.ts:44-78 invalidates operational families with refetchType 'none'; AppShell.tsx:133-140 purges QueryClient, app state, browser storage and memory caches when health.actor_cache_key changes (shared-machine safety).
- **Health polling done right** — HealthProvider.tsx:206-293: one shared poll per document, paused while the tab is hidden, adaptive 8 s / 3 s cadence (shouldPollFast :70-75), down-to-up debounce (:129-180); server probes are SWR-cached (health_probes.py:20-24) and anonymous liveness callers never touch billable dependencies (api/health.py:31-34).
- **Intent/idle code preloading and bounded payload patterns already exist** — RouteNav.tsx:82-83 preloads route chunks on hover/focus; AppShell.tsx:208-209 warms the Genie chunk from the FAB; routePreloaders.ts:31-38 idle-preloads the four likeliest routes; api.ts:1006-1027 analytics scatter uses server viewport capping with an honest N-of-M; Console.tsx:92 uses cursor-based useInfiniteQuery; lib/rum.ts collects LCP/INP/CLS/route_change/api_call.


### `delivery-06` Server caches are hard-expiry: someone pays full warehouse latency every 2-5 minutes for daily-refreshed data

`high` · `upgrade` · effort `M` · corrected

- **Evidence:** settings.py:504 preview TTL 120 s, :456 others 300 s, :468 re-warm interval 0. Hot reads use hard-expiry TTLCache.get_or_set (databricks_portfolio.py:950, databricks_geo.py:204, databricks_analytics.py:543, api/config.py:108). StaleWhileRevalidateCache (resilience.py:624) is instantiated only at health_probes.py:22.
- **Problem:** Gold tables refresh about daily, yet every 2-5 minutes one user pays the full warehouse round trip (1.4-5.6 s cold, performance-scale audit section 3) on hero KPIs while single-flight followers block behind them.
- **Recommendation:** Serve hot gold aggregates stale-while-revalidate (soft TTL = today's TTL, hard cap ~24 h, snapshot id learned on each refresh and folded into the key). Use a dedicated refresh executor: the 3-worker mip-swr pool also runs the health probes, and starving it past the 10 s hard TTL flips health to down. Keep Lakebase-derived counts (approved_count, in_outreach_count) out of the long-lived value or invalidate the preview key on approve/outreach writes. Refresh stays request-triggered so idle workspaces still auto-stop.
- **Tech:** Existing StaleWhileRevalidateCache.get_or_refresh; snapshot-versioned cache keys; no new dependency.
- **Verifier:** All cited call sites use hard-expiry get_or_set; the SWR cache is health-only. Two flaws: mip-swr has three workers shared with the three health probes (resilience.py:594), and preview embeds live approval counts with no mutation invalidation anywhere.

### `delivery-01` Routine serverless resume trips the red Degraded pill and Reconnecting banner (3 s binary health budget); the Home hero collapses only on real retryable 503s

`medium` · `defect` · effort `M` · corrected

- **Evidence:** home.tsx:211,217,271 unmount LastLoginSummary, .kpi-row and PortfolioSummaryCard while previewWarming; WarmingUpBlock.tsx:65-77 returns null under the banner. settings.py:566-568: health waits 3 s then reports down; HealthProvider.tsx:185 holds down 5 s more. WarmingUpBlock.tsx:32 says ~30 s. databricks.yml:270-272: serverless, auto_stop 10.
- **Problem:** The first visit after ten idle minutes shows a red Degraded pill, a Reconnecting banner and a hero missing ~500 px (ui-shots degraded-warehouse-down__home); content then pops in. A documented 2-6 s serverless resume reads as an incident.
- **Recommendation:** Add a `resuming` dependency state sourced from the warehouse-state API (STOPPED/STARTING) so a wake shows a calm 'Waking warehouse - Ns' pill with the existing ElapsedTicker; keep red Degraded for breaker-open/failed probes. Separately, in the true 503 path keep .kpi-row, LastLoginSummary and PortfolioSummaryCard mounted as skeletons instead of unmounting (home.tsx:211-273). Fix the 30-60 s copy for serverless. Ship together with delivery-v1, because moving health off SELECT 1 removes today's accidental keep-warm.
- **Tech:** GET /api/2.0/sql/warehouses/{id} state field; existing Skeleton and ElapsedTicker components; CSS min-block-size reservation. Serverless resume of 2-6 s verified on Databricks answers, Sept 2026.
- **Verifier:** Health path reproduces: 3 s budget returns None, reported down (resilience.py:742-751). But warmingUp is set only on retryable 503s and the SQL client waits 30 s, so a routine resume keeps KPI skeletons mounted. The screenshot harness simulated breaker-open 503s.

### `delivery-02` Lead Queue is a fixed top-500-by-score snapshot: sorting is client-only and leads beyond 500 are unreachable

`medium` · `gap` · effort `L` · corrected

- **Evidence:** lead_query.py:27-28 default 500, max 5000; grep offset|cursor in backend/api/leads.py: none; lead-queue.tsx never sends limit; LeadTable.tsx:1255 prints 'capped at'; LeadTable.tsx:134 sorts client-side; leads.py:485-492 runs list, count, hydrate serially; types.ts:118-124,169 declare fields no component reads.
- **Problem:** Users cannot reach lead 501+, and sorting by equity reorders only the top 500 by score, not the cohort. First paint waits on ~628 KB of JSON (performance-scale audit) for about twelve visible rows, including eight never-read fields per row.
- **Recommendation:** Phase 1 (M): add a server `sort` parameter so a column sort refetches the true top 500 for that order, and run list and count concurrently. Phase 2 (L): keyset cursor on (sort_col, borrower_id) with useInfiniteQuery feeding the existing virtualizer. Define audit semantics first: one VIEW_LEADS row per cohort view with the cursor in its payload, not one per page; keep the cohort identity proof whole-cohort; bulk-approve, select-all and CSV must state whether they act on loaded rows or the cohort. Drop the payload-size argument.
- **Tech:** TanStack Query v5 useInfiniteQuery and @tanstack/react-virtual (both installed, stable); SQL keyset pagination; FastAPI response_model_exclude_none.
- **Verifier:** Cap, client-only sort, no cursor and serial list/count reproduce. But performance-scale-audit.md:419-420 shows 628 KB is 45 KB gzipped, and :124-125 shows 100 rows ran slower than 500, so the first-page speed benefit is unsupported.
- **Constraint conflict:** Audit log must stay meaningful: per-page fetches would multiply VIEW_LEADS rows, and the Growth Agent handoff identity proof (leads.py:471-482) is computed over the whole cohort.

### `delivery-03` Route data starts only after the lazy route chunk mounts; chunks are preloaded on intent but data never is

`medium` · `upgrade` · effort `M` · corrected

- **Evidence:** main.tsx:16 BrowserRouter; app.tsx:69-71 Suspense + lazy Routes; home.tsx:85-89 preview query fires on mount; USStateMapData.ts:7-9 imports geometry after that. grep prefetchQuery|ensureQueryData|createBrowserRouter|useNavigation in frontend/src: zero hits. routePreloaders.ts:31-38 idle-preloads four chunks, never data.
- **Problem:** Every first paint is HTML, then a ~120 KB-gzip entry, then the route chunk, then the API, then map geometry: four serial levels. Hero KPIs wait on a chunk they do not depend on.
- **Recommendation:** Get the win without a router migration: in main.tsx, before render, call preloadRouteForPath(location.pathname) and queryClient.prefetchQuery for that route's non-audited hero queries (homePreview, homeSummary, geo state rollups) plus loadUsaStateMap. Extend RouteNav hover/focus to prefetch non-audited aggregates (segments, analytics). Governed reads (leads, borrower) still start at mount so exactly one VIEW_* row is written. Treat createBrowserRouter/loader migration (L) as an optional owner decision justified by pending-navigation UI, not latency.
- **Tech:** react-router 8 data mode (createBrowserRouter, lazy, loader, useNavigation; stable since 6.4, no SSR required); TanStack Query v5 ensureQueryData/prefetchQuery.
- **Verifier:** Zero data-prefetch hits confirmed; react-router 8.3.0 exports createBrowserRouter. But RouteNav.tsx:82-83 already hover-preloads chunks, four routes idle-preload, chunks are immutable-cached, and map geometry loads parallel to the API, not after it.

### `delivery-04` Genie complete phase is an opaque blocking POST after a 1.5 s poll loop; no streaming transport exists

`medium` · `upgrade` · effort `L` · corrected

- **Evidence:** genieAsk.ts:21 polls every 1,500 ms (loop :90-121), then one opaque POST complete (backend/api/genie.py:616-621). load-baseline.md:79: Genie p50 12 s, p95 15 s. grep StreamingResponse|EventSource|text/event-stream in backend and frontend/src: none. fastapi.sse imports cleanly in the repo venv (FastAPI 0.136.1).
- **Problem:** The showcase surface advances in ~2 s steps, then sits silent on 'Format' while guards, verification and any rewrite run. No streaming transport exists anywhere in the app.
- **Recommendation:** Add an SSE turn stream whose main value is named post-guard stages during complete (verify, cross-check, rewrite, format); guards still evaluate the whole answer before any prose is sent. Poll Genie server-side at 1 s, within Databricks' 1-5 s guidance. Do not hold a genie concurrency slot for the stream's lifetime (backpressure.py classifies /api/genie as a slot holder). Verify streaming and client-disconnect through the four BaseHTTPMiddleware layers or convert them to pure ASGI first. Keep polling as fallback; section-by-section reveal can be client-side.
- **Tech:** fastapi.sse.EventSourceResponse (FastAPI >=0.135; 15 s keep-alive); Starlette 1.3.1 GZip skips event-stream (verified); fetch + ReadableStream client. Genie Agent-mode SSE API is Public Preview (docs verified).
- **Verifier:** Poll loop, blocking complete and absent streaming reproduce; fastapi.sse imports on 0.136.1 and Starlette 1.3.1 GZip skips event-stream. But onProgress already renders stage, SQL and an elapsed ticker, and Databricks documents 1-5 s Genie polling, so 500 ms is out of guidance.

### `delivery-05` No persisted query cache: every reload, new tab and next-morning visit starts from empty skeletons

`medium` · `gap` · effort `M` · corrected

- **Evidence:** queryClient.ts:12-13: in-memory only, 30 s stale, 5 min gc. grep persistQueryClient|PersistQueryClientProvider|query-persist in frontend: none. Actor scoping already exists: AppShell.tsx:134 clears state when health.actor_cache_key changes; actorScopedBrowserState.ts:5-12 is the key registry.
- **Problem:** Every reload, new tab or next-morning visit repaints from empty skeletons, and during a warehouse resume there is nothing to show, although yesterday's aggregates are still the current gold snapshot.
- **Recommendation:** PersistQueryClientProvider with an allow-list of non-PII tenant aggregates only. sessionStorage covers reload, tab restore and duplicate tab; covering next-morning needs localStorage and is an owner decision against the shared-booth posture. Pin both persist packages to 5.100.10 or bump react-query in lockstep. Do not render restored data until the stored actor_cache_key matches the live one (it arrives with /health after mount). Show the payload's data_refreshed_at as an 'as of' chip; buster = build hash; maxAge 24 h.
- **Tech:** @tanstack/react-query-persist-client and query-sync-storage-persister 5.103.2 (npm view, Sept 2026; stable v5 API).
- **Verifier:** No persister anywhere; actor clearing exists. But sessionStorage is per-tab, so it cannot fix the stated new-tab or next-morning cases, only reload. persist-client 5.103.2 peers on react-query ^5.103.2 while the repo pins 5.100.10 (npm view).
- **Constraint conflict:** Shared-machine actor isolation (actorScopedBrowserState.ts) argues against localStorage; choosing it is an owner decision. Restored data is real cached UC data, not a mock, but must be labelled with its snapshot time.

### `delivery-07` Lender label and Admin tab swap in after first paint because a settings constant rides on the warehouse-backed options call

`medium` · `upgrade` · effort `S` · corrected

- **Evidence:** Five shell calls per load: AppContext.tsx:193 session, :202 workspace, :170 config/options; FootprintProvider.tsx:144 footprint; HealthProvider.tsx:274 health (ui-shots api-log shows them on every route). AppContext.tsx:171 renders 'Configured lender' until options resolves. index.html:3-32 has no preload hint.
- **Problem:** Shell identity data starts only after the entry bundle executes, crosses the five-layer middleware stack five times, and the lender label and Admin tab visibly swap in after first paint.
- **Recommendation:** Do not aggregate. Add lender_name and rum_enabled to the zero-dependency /api/v1/session response, read the label from it, and start it from index.html with link rel=preload as=fetch crossorigin (connect-src 'self' allows it; a missed match only costs one duplicate request). Leave options, footprint and workspace as separate parallel calls so a warehouse or Lakebase 503 never blocks shell identity.
- **Tech:** rel=preload as=fetch (Baseline widely available); TanStack setQueryData; FastAPI aggregate router reusing existing services.
- **Verifier:** Five calls and the placeholder label confirmed. But session.py is zero-dependency and lender_name is a settings constant, while options and footprint are warehouse-backed (1.6-2.5 s cold in the perf audit). One bootstrap would gate identity on the warehouse and inherit its 503s.

### `delivery-08` Offer Orchestrator bypasses the query cache (duplicate dossier fetch + duplicate audit row); Lead Queue runs export-only calls on every mount

`medium` · `defect` · effort `L` · corrected

- **Evidence:** offer-orchestrator.tsx:162-166 Promise.all inside a raw effect, cached in a module Map (offer-orchestrator.cache.ts:27). borrower-360.tsx:85 caches the same dossier under queryKeys.borrower and links to the offer at :551; borrowers.py:175 writes VIEW_BORROWER per GET. lead-queue.tsx:339,351 fire preview({}) and admin/rules on mount for CSV stamps.
- **Problem:** The commonest click, Dossier to Offer, refetches a dossier already in cache and writes a duplicate VIEW_BORROWER audit row. Every Lead Queue visit runs a whole-book KPI aggregation that only an export would use.
- **Recommendation:** Ship separately. S: fetch export metadata on Export click (or return X-Data-Refreshed-At on /leads) and drop the mount-time preview({}) and adminRules calls; preload borrower-360 and offer chunks on row expand. L: port the orchestrator to useQueries on shared keys and delete BORROWER_CACHE, preserving the snapshot-reconcile fresh=true retry. Before reusing the cached dossier, get compliance sign-off or write an explicit lightweight VIEW_OFFER audit event so the approval surface still records what the approver saw.
- **Tech:** TanStack Query v5 useQueries/queryOptions (installed); existing lazyWithPreload helper.
- **Verifier:** Raw-effect Promise.all, module Map, shared dossier key and the mount-time preview({}) and adminRules calls reproduce. But borrowers.py:171-173 records what the approver saw, so dropping that row on the approval surface is a governance call. The 842-line route plus 627-line test is not M.
- **Constraint conflict:** Audit log must stay: sharing the cached dossier removes a VIEW_BORROWER row on the approval surface, which Governance section 4 (borrowers.py:171-173) treats as evidence of what the approver saw.

### `delivery-09` Read-only POST preview is budgeted as a Lakebase mutation; dependency slots (24+16+6) exceed the 40-thread pool that also serves health, session and static files

`medium` · `defect` · effort `M` · corrected

- **Evidence:** backpressure.py:111-115 classifies every POST as mutation + lakebase before the warehouse list (:127), so POST /portfolio/preview draws 16 Lakebase slots. 74 sync handlers, 0 async; anyio limiter 40 (checked in venv; grep total_tokens: none) equals 24+16 semaphores. runtime.py:67 uvicorn defaults; resilience.py:508 followers block 30 s.
- **Problem:** On a cold cache, simultaneous users fill both semaphores with blocked single-flight followers, which exhausts all 40 worker threads, stalls /health, /session and static files, and returns 429s. load-baseline.md:21-25 measured warm only.
- **Recommendation:** S now: classify read-only POSTs (portfolio/preview, offers/recommend, campaign-recommendation) as warehouse reads after confirming they make no Lakebase write; raise the anyio limiter to ~100 in lifespan; make /session async. M later: release the dependency slot while waiting on a single-flight leader, and add a cold-cache 30-user Locust profile. That profile needs a real warehouse, so run it operator-side, and measure before investing in slot release.
- **Tech:** anyio.to_thread.current_default_thread_limiter().total_tokens; existing tools/load_test Locust harness.
- **Verifier:** POST-before-warehouse classification, default 40-token limiter, uvicorn defaults and 30 s follower wait reproduce. Arithmetic is worse than stated: 24+16+6 genie slots = 46 > 40, and all 100 routes are sync. Slot release across middleware and cache layers is not S. Exhaustion itself is unmeasured.

### `delivery-10` API JSON carries no Cache-Control: default every /api response to private, no-store

`medium` · `defect` · effort `S` · corrected

- **Evidence:** grep Cache-Control|no-store|ETag across backend: only api/audit.py:128 sets 'private, no-store'; main.py:515-519 stamps /assets only; main.py:897 covers index.html. Borrower, leads and workspace JSON carry no cache directive, and no JSON endpoint emits ETag or answers If-None-Match.
- **Problem:** PII responses rely on browser defaults on the shared booth machines the code defends elsewhere (actorScopedBrowserState.ts:7-9), while cacheable non-PII aggregates never earn a 304 or a browser-cache hit.
- **Recommendation:** In SecurityHeadersMiddleware setdefault Cache-Control: private, no-store for every /api/ path (audit.py:128 already does this for one route). Drop the ETag/304/max-age/stale-while-revalidate half: payloads are 1-2 KB, TanStack plus delivery-05/06 cover repeat-read latency, the browser HTTP cache cannot be cleared when actor_cache_key changes, and Safari support for stale-while-revalidate is unconfirmed.
- **Tech:** RFC 9111 validators and RFC 5861 stale-while-revalidate (Chromium, Firefox; confirm Safari); Starlette 304 responses.
- **Verifier:** Header grep reproduces; api.ts sets no fetch cache mode. But aggregates are 1-2 KB (perf audit :97-99), so 304s save nothing, and URL-keyed browser caching cannot be cleared on actor change or see audit writes. Keep only the no-store default.
- **Constraint conflict:** Browser max-age caching of API reads conflicts with actor-scoped state clearing on shared machines and would bypass any endpoint that writes VIEW_* audit rows.

### `delivery-v1` Health poll's SELECT 1 is an accidental warehouse keep-warm: any visible tab blocks auto-stop

`medium` · `gap` · effort `M` · verifier-added

- **Evidence:** health_probes.py:57 probes with SELECT 1 on the warehouse; probe soft TTL 2 s (:20). HealthProvider.tsx:182-183 polls every 8 s while the tab is visible (:207). databricks.yml:272 auto_stop_mins 10. settings.py:462-468 disables lead re-warm for idle cost.
- **Problem:** Any visible tab queries the warehouse every 8 s, so it never auto-stops, an unplanned cost, and cold starts hit only the first visitor. Moving health to the state API (delivery-01) silently removes this keep-warm.
- **Recommendation:** Probe warehouse health through the warehouse-state API, not SQL. Make keep-warm an explicit, configurable policy: ping only while a tab is visible and the user was active in the last N minutes, off by default outside staffed hours. Ship with delivery-01 so cold-start frequency is a chosen trade-off, wired through deploy.sh env.
- **Tech:** Databricks SQL Warehouses GET state; existing visibilitychange handling; one settings flag. Assumes any query resets the idle timer (standard behaviour, not live-verified).

### `delivery-v3` No Server-Timing or API resource timing in RUM: slow paints cannot be attributed to cache, warehouse or Lakebase

`medium` · `gap` · effort `S` · verifier-added

- **Evidence:** grep Server-Timing across backend and frontend/src: none. rum.ts:153,169,203 observes LCP, layout-shift and event timing only, no 'resource' entries for /api. TTLCache already emits hit/miss events server-side (resilience.py:456-466). load-baseline.md:21-25 covers warm load only.
- **Problem:** Per real session the team cannot tell whether a slow Home was a cache miss, a warehouse resume or Lakebase hydration, so gains from delivery-03, 05 and 06 cannot be proven and regressions go unseen.
- **Recommendation:** Emit a Server-Timing header from middleware fed by a ContextVar collector (cache;desc=hit|miss, warehouse;dur, lakebase;dur, total). In rum.ts observe 'resource' entries under /api/ and forward serverTiming plus duration, sampled, with route-templated paths so no borrower id leaves the browser.
- **Tech:** Server-Timing and PerformanceResourceTiming.serverTiming (Chromium, Firefox, Safari 16.4+); existing /telemetry/rum endpoint.

### `delivery-v2` Inner fetch retry is reason-blind: three sub-second retries into an open breaker before any degraded UI

`low` · `defect` · effort `S` · verifier-added

- **Evidence:** api.ts:816-836 retries any retryable 503/429 three times at 200-400 ms, ignoring body.reason. retryPlan.ts:31-52 says breaker_open should wait 30 s and retries_exhausted should stop. queryClient.ts:19-35 retries again on top; postJson (api.ts:904) uses the same loop.
- **Problem:** Each failing query sends three requests before TanStack's reason-aware plan runs, delaying the first warming or error paint by up to a second and tripling traffic into an open breaker and the actor's rate bucket.
- **Recommendation:** Make _fetchWithRetry consult the parsed reason: no inner retry for breaker_open or retries_exhausted, since TanStack owns those; keep the inner loop for 429 with Retry-After. Review inner retries on non-idempotent POSTs such as approve and restrict them to requests carrying an idempotency key.
- **Tech:** Existing retryPlan.ts and the reason field already parsed by _parseRetryableBody; no dependency.

**Overflow (titles only, not verified)**

- Approve click is two serial round trips (draftOutreach p50 0.6 s then approve p50 1.5 s; LeadTable.tsx:255-271, load-baseline.md:76-77): compose draft+approve server-side or pre-draft on row expand; keep the pessimistic approved state (governance), but show a row-level 'audit write pending' state via React 19 useOptimistic.
- Databricks SQL client opens a new TCP+TLS connection per statement via urllib.request.urlopen (databricks_sql.py:267); same pattern twice in genie_client.py. Use a pooled keep-alive httpx.Client (httpx 0.28.1 already in uv.lock).
- segment-intelligence and lead-queue cache the identical default GET /api/leads under different key prefixes ('segment-intelligence' vs 'lead-queue'; segment-intelligence.tsx:403, lead-queue.tsx:268) so the ~628 KB page is fetched twice on Segments -> Leads.
- Authenticated health poll runs SELECT 1 on the warehouse (health_probes.py:58) with a 10 s hard TTL, so any visible tab prevents the 10-minute auto-stop (databricks.yml:272): cost vs cold-start trade-off is implicit; a warehouse-state probe would decouple them.
- No Server-Timing header on API responses (grep: none), so RUM api_call cannot attribute proxy vs app vs warehouse vs cache-hit time; emit it from CorrelationIdMiddleware.
- Dynamic JSON is gzip-6 only (main.py:539; brotli/zstd modules not installed): brotli q4-5 would cut ~15-20% but is low priority once pagination/projection lands.
- GenieChat calls api.genieStart raw (GenieChat.tsx:111) while /ask-genie uses the query cache (ask-genie.tsx:123): two starts per session, each drawing the 30/min genie bucket and a genie concurrency slot.
- HealthProvider polls outside TanStack Query and sets a new object every 8 s (HealthProvider.tsx:240-242), re-rendering every useHealth consumer; compare-before-set or move to useQuery with structural sharing and refetchInterval.
- Degraded map legend renders 'Borrowers in selection 0' while state-rollups is 503 (ui-shots degraded-warehouse-down__home): an unknown should render as an em dash, not zero.
- In-page links and the command palette do not use preloadRouteForPath (only RouteNav.tsx:82-83 does); borrower-360 and offer-orchestrator chunks are absent from the idle preload list (routePreloaders.ts:31-38).
- 103 Early Hints / HTTP/3: not actionable here (uvicorn has no early-hints support, index.html is 1.8 KB static, proxy behaviour undocumented); the preload-as-fetch hint in delivery-07 captures the achievable win. Recommend not pursuing.
- Single uvicorn worker with GIL-bound Pydantic validation + JSON + gzip of 500x70-field lead pages (runtime.py:67): caching the serialized, compressed bytes of the default lead page would remove that CPU from the hot path without breaking the process-local cache assumption.
- POST retries: _fetchWithRetry retries POST/PATCH/DELETE on retryable 503/429 (api.ts:816-838, postJson :895-917) without an idempotency key on most mutations; worth a contract review (owner: API-contract dimension).

**Limitations:** Read-only, no live app access (no Databricks CLI/deploy, default ports untouched), so there are no fresh wire-level measurements: all latency and payload numbers are quoted from the May 2026 audits and docs/load-baseline.md, and may have drifted. No build was run and frontend/dist was not read, so the emitted modulepreload set and chunk graph are inferred from source. Databricks Apps proxy behaviour for SSE/WebSocket/103 Early Hints/HTTP-3 could not be confirmed from official docs; I found only community reports of a roughly 120 s request cap and idle timeouts, plus the earlier audit's captured 'HTTP/2 200' response, so delivery-04 is medium confidence and needs a one-hour spike on the dev app. The cold-cache concurrency finding (delivery-09) comes from code reading plus a local check of the anyio limiter (40) and handler census (74 sync / 0 async via grep), not from a load test. Verified on the web or locally: serverless SQL warehouse 2-6 s start (Databricks answers), FastAPI native SSE since 0.135 and importable in the repo venv at 0.136.1, Starlette 1.3.1 GZip excluding text/event-stream, Genie Agent-mode SSE API in Public Preview, TanStack persist packages at 5.103.2 via npm view. Not verified: Safari support for stale-while-revalidate, Databricks Apps compute size/vCPU count. Screenshots were fixture-driven and used only for layout (hero collapse in the degraded state), never for data claims. The 'fields never read' list was derived by whole-word grep over frontend/src excluding tests, mocks, types and api.ts; a dynamic key iteration would escape that search, though LeadTable.csv.ts enumerates its columns explicitly.

---

## Coverage critic: surfaces and modalities the 16 dimensions missed (`critic`) — grade C+

**Auditor:** The surfaces nobody audited are honest and careful about data, but they fall well short of Linear/Stripe-class craft. There is no form system, no help layer, tooltips are native title attributes, and the role-aware journeys dead-end. Three of the defects sit directly on the approve/prove/execute trust path: the clipped subject line, the evidence 403, and the unreviewed plan execution.

**Verifier on the grade:** Slightly harsh; B- is fairer. All twelve findings reproduce at file level, but four overstate harm: plan execution is read-only and halts at approval gates, drawer proof still works for non-admins, glossary hover definitions and shortcut hints already exist, and the icon set is prototype-verbatim. The auditor also missed per-route document titles, session-expiry handling and campaign-draft loss.

**Strengths (keep these)**

- **Cards never state a number they have not read** — frontend/src/routes/analytics.sales-ops.tsx:52-63 and :125-134: CardValue renders a skeleton while loading and an em-dash on error, never a zero. The comment explains why a false 'STALE APPROVED 0' would mislead a manager.
- **Narrative numbers are provable, not decorative** — frontend/src/components/mortgage/LastLoginSummary.tsx:29-57 splits the server sentence on validated value tokens, so every figure becomes an evidence-drawer button. :156-163 labels Genie rephrasing as 'Genie-phrased · deterministic numbers'.
- **Approval control is hardened against double submission** — frontend/src/components/mortgage/ApprovalBanner.tsx:52-67: a synchronous in-flight latch plus caller isSubmitting. Both buttons disable and the region sets aria-busy.
- **Admin refuses fake editability** — backend/api/admin.py:295-313: PUT /rules returns 410 with a rationale that a process-local override 'made the Admin surface look editable without changing a single borrower score'. frontend/src/routes/admin-config.tsx:296-323 shows the active governed version instead of inventing a diff.
- **Deliberate, honest naming of the signal meter** — frontend/src/components/mortgage/ConfidenceMeter.tsx:6-8 and :22-31: role=meter with aria-valuenow. The label is 'Signal' because it is a deterministic average, 'not a statistical confidence interval'.


### `critic-02` Approval screen shows the outreach subject in an unstyled 20-character native input, clipping the copy being certified

`high` · `defect` · effort `M` · corrected

- **Evidence:** frontend/src/routes/offer-orchestrator.panels.tsx:477-486: a readOnly <input> with no className inside label.field. components.css defines .field__label (:5524) but no .field or .field input rule (grep). Screenshot dark__offer-orchestrator-detail-populated__full.png shows a white box reading 'A quick review of your mor'.
- **Problem:** On the one screen where a human certifies borrower-facing copy, the subject is clipped to about 24 characters in a browser-default white field. The body sits in a disabled textarea with a resize grip.
- **Recommendation:** Hotfix first (S): render the subject as wrapping text, or give the input the form-input class at full width. Then (M) build the read-only email frame and SMS bubble with segment count, keeping the data-testids and the 'exact audited copy' label, and converting the six value-based test assertions to text assertions.
- **Tech:** Semantic HTML (article, dl) with existing tokens; no library. If inputs remain, CSS field-sizing: content (Baseline newly available June 2026, verified by web search).
- **Verifier:** Reproduced in code and the dark screenshot: unstyled readOnly input at panels.tsx:478-485, no .field input rule, no color-scheme, subject clipped at 'A quick review of your mor'. field-sizing Baseline June 2026 verified. 23 test references, six value-based, so the full preview is M.

### `critic-03` Evidence drawer's primary action dead-ends for non-admin buyers, and source freshness is hidden from them

`high` · `defect` · effort `M` · corrected

- **Evidence:** frontend/src/components/mortgage/EvidenceDrawer.tsx:641-646 renders 'View asset details' without the canAccessAdmin check used at :232. backend/api/assets.py:29 requires AdminDep. frontend/src/routes/asset.tsx:83-95 answers 'Admin access required' with only 'Return home'. EvidenceDrawer.tsx:175 labels non-admin freshness 'Admin-only freshness'.
- **Problem:** The buyer personas are not admins. Their proof journey ends on a 403 page, and they cannot see how fresh the evidence behind a score is. Sibling admin links are already gated (offer-orchestrator.panels.tsx:607), so this one is inconsistent.
- **Recommendation:** Step 1 (S): gate 'View asset details' on canAccessAdmin exactly as the metadata query is gated at :232, and give the asset 403 state a way back to the evidence. Step 2 (M, owner and governance decision): an AuthenticatedActorDep asset-summary endpoint exposing only display name, description, freshness band and last refresh, with no DDL, tags or lineage internals.
- **Tech:** New GET asset-summary endpoint under AuthenticatedActorDep (the pattern in backend/api/lineage.py:25); reuse the AssetFreshness type; TanStack Query key beside assetMetadata.
- **Verifier:** All four sites verified: ungated link at EvidenceDrawer.tsx:641, AdminDep at assets.py:29, 403 dead-end at asset.tsx:83-95, 'Admin-only freshness' label. Drawer proof and lineage still work for non-admins. Admin-only metadata was deliberate (:186), so exposing freshness is an owner decision.

### `critic-01` Growth Agent 'Execute plan' runs a freshly composed plan the user never saw; the reviewed plan is discarded

`medium` · `defect` · effort `M` · corrected

- **Evidence:** frontend/src/routes/ask-genie.tsx:380-388 clears composePlan and posts {objective, execute:true}. backend/api/growth_agent_compose_routes.py:56-92 recomposes via the Supervisor, then calls execute_plan(). backend/schemas/agent_plan.py:31-39 ComposePlanRequest has no plan_id. Compose and Execute are sibling buttons (ask-genie.tsx:573-589).
- **Problem:** Review-then-run is an illusion. Clicking Execute after reviewing a composed plan triggers a second LLM composition and executes that one. Steps stay registry-validated and approval-gated, but the human never saw what ran.
- **Recommendation:** Bind Execute to the reviewed plan. Cheapest safe path: the client posts back the reviewed ComposedPlan plus its hash; the server re-runs build_validated_plan (the same registry boundary applied to model output) and executes only that, returning 409 on mismatch. A Lakebase plan table is optional; if added, ship the migration through scripts/deploy.sh. Move Execute into ComposePlanCard, relabel or remove the sibling one-shot button, and show a step diff on recompose.
- **Tech:** FastAPI plus a Lakebase migration (mip_app agent plan table) reachable from scripts/deploy.sh; TanStack useMutation; existing ComposePlanResponse.plan_id field.
- **Verifier:** Reproduced: ask-genie.tsx:380-386 clears the plan and re-posts only the objective; the route recomposes, then executes; no plan_id on the request. But execute_plan runs read-only tools and halts at any gates_run step (agent_tools.py:108), so harm is trust, not writes.

### `critic-04` No form system: silent clamping, no inline validation, no units, and read-only fields look editable

`medium` · `gap` · effort `M` · corrected

- **Evidence:** frontend/src/routes/portfolio-builder.logic.ts:108-119 clamps on blur, and portfolio-builder.tsx:723-726 commits with no message. portfolio-builder.campaign-setup.tsx:211-214 readOnly fields share .form-input. components.css:6022-6033 has outline:none and no :read-only, :disabled or :invalid rule. grep aria-invalid finds one hit (AdminAuditExplorer.tsx:218).
- **Problem:** Typing 75 into Holdout silently becomes 50. Budget and cost fields carry no $ or % adornment. Four empty read-only copy boxes invite typing that does nothing. Errors are never tied to their fields.
- **Recommendation:** Build the Field primitive and land it in campaign setup first (M); roll out to the other 16 files afterwards (L overall). Skip react-hook-form unless validation grows; the app has nine runtime dependencies. Restore a visible focus ring, because .form-input outline:none overrides the global :focus-visible rule in tokens.css:345. Render read-only copy as text and announce clamps ('Capped at 50%').
- **Tech:** :user-invalid (Baseline since 2023); field-sizing: content (Baseline newly available June 2026, verified); Intl.NumberFormat; optionally react-hook-form 7.88.0 (npm-verified) for campaign setup only.
- **Verifier:** Verified clamp-on-blur with no message, readOnly fields sharing .form-input, outline:none, no :read-only/:disabled/:invalid rules, one aria-invalid hit. The label does say 'Holdout % (0-50)'. 39 native controls across 17 files, so scope the primitive to campaign setup first. npm versions verified.

### `critic-05` Vendor demo scaffolding ships inside the customer product

`medium` · `defect` · effort `M` · corrected

- **Evidence:** frontend/src/components/admin/BuyerReadinessPanel.tsx:175-196,259 says 'Do not claim HITRUST' and 'Safe claims: ...'. components/admin/CapabilityPanel.tsx:91 says 'DAIS-2026 stack'. routes/offer-orchestrator.tsx:622-632 has an ungated 'Preview borrower view' that opens a PROTOTYPE-watermarked Module 1 mock beside Approve. components/layout/Rail.tsx:63-76 has four dead M1-M4 slots.
- **Problem:** A lender's admin reads coaching written for the vendor's presenter, and an approver finds a mock of an unbuilt feature next to the most consequential button. This contradicts the 'polished enterprise product' posture.
- **Recommendation:** As written, plus: register the flag in the deploy payload builder and the .env.local template so scripts/deploy.sh carries it (empty values are dropped silently), default off. Keep the honest-claims content available in presenter mode, since CLAUDE.md forbids overclaiming. Hiding the M1-M4 rail slots remains an owner decision because they are in the prototype.
- **Tech:** Settings flag (for example MIP_PRESENTER_MODE) in app.yaml and the .env.local template, surfaced through /api/config/options; no new infrastructure.
- **Verifier:** All four sites verified verbatim. BuyerReadinessPanel and CapabilityPanel sit behind the admin guard, so exposure is admin-only; the PROTOTYPE borrower mock is reachable by any approver. No presenter flag exists (grep). Flag plus settings, app.yaml, deploy payload, config endpoint and tests is M.

### `critic-06` Assignment lifecycle control reads as a status, and the outcome picker has no cancel

`medium` · `defect` · effort `M` · corrected

- **Evidence:** frontend/src/components/mortgage/AssignmentLifecycleAdvance.tsx:80-89: a ghost button labelled with the next state ('Approved', 'Actioned'), rendered under the status chip (LeadTableRow.tsx:156-170). At :101-114 the outcome buttons fire immediately with no cancel, confirm or undo. There are three outcomes (:24-28) against five ledger categories (analytics.sales-ops.tsx:254).
- **Problem:** In the Assigned-to cell, a 'Contact drafted' chip followed by an 'Approved' button looks like two statuses. A mis-click advances a governed, audited lifecycle. The outcome picker cannot be backed out, and officer outcomes do not map to the manager's ledger.
- **Recommendation:** Use a verb-first label ('Mark approved') with a compact stepper showing position. Give the outcome picker Cancel, Esc and a confirm step. Keep the three server outcomes; do not borrow the LOS ledger vocabulary. Prefer explicit confirm over a delayed-commit undo for an audited write. Popover plus anchor positioning (Baseline January 2026) with a fixed-position fallback.
- **Tech:** Popover API (popover=auto, Baseline in all engines) plus CSS anchor positioning (supported across engines in 2026 per web search; fixed-position fallback); TanStack useMutation with a delayed commit.
- **Verifier:** Verified the next-state ghost label and the no-cancel outcome buttons. The 'five ledger categories' claim is a misread: that card is the imported, read-only LOS outcome ledger (sales-ops.tsx:259); officer outcomes are a separate three-value server Literal (loan_officer.py:40).

### `critic-07` No help menu, onboarding, glossary search or support path (glossary terms already have hover definitions)

`medium` · `gap` · effort `M` · corrected

- **Evidence:** grep -riE 'onboard|tour|what.s new|keyboard shortcuts' over frontend/src finds comments only. routes/glossary.tsx (107 lines) has no search input. components/command/commandActions.ts:46 indexes the glossary route and none of the 35 entries. components/GlossaryTerm.tsx:20-25 is a Link to /glossary. No support link exists (grep mailto|support).
- **Problem:** A new loan officer gets no first-run guidance and cannot discover A/R, slash or Cmd-K. They cannot search definitions, and they lose their place when they click a term. There is no route to support.
- **Recommendation:** Drop the GlossaryTerm popover rewrite; extend the existing tip beyond its 11 call sites. Add a topbar Help menu with a '?' shortcut sheet, a glossary search input, palette-indexed glossary entries, and a support contact from config. Persist a dismissible first-run checklist per actor through the existing workspace-state API.
- **Tech:** Popover API plus CSS anchor positioning; the existing CommandPalette action registry; Lakebase workspace state (backend/api/workspace.py) for checklist persistence.
- **Verifier:** GlossaryTerm already shows a hover/focus definition tooltip (GlossaryTerm.tsx:27, components.css:2420); Cmd-K is hinted in the topbar and A/R at LeadTable.tsx:888. Confirmed: no glossary search (page measured 5,622px), palette indexes only the route, no help, support or onboarding.

### `critic-08` About 80 native title attributes are the tooltip system; compliance facts and icon-button names live in them

`medium` · `upgrade` · effort `M` · confirmed

- **Evidence:** A scripted count over frontend/src/**/*.tsx finds 52 DOM title= plus 30 passed through Chip, Link, Button and EvidenceChip. There is one role="tooltip" (GlossaryTerm.tsx:27). LeadTableRow.tsx:135,143 put the DNC and eligibility source in a title on a non-focusable Chip. Topbar.tsx:393-412 has icon-only buttons.
- **Problem:** Native titles appear after about a second in OS chrome outside the design system. They never show on keyboard focus or touch and cannot display shortcuts. Facts a compliance reviewer needs are effectively mouse-only.
- **Recommendation:** Ship one Tooltip primitive: hover and focus, short open delay with skip-delay between neighbours, Esc to dismiss, a kbd slot for shortcuts, token-styled. Move the DNC and eligibility source into visible row detail. Ban raw title= in JSX with an ESLint no-restricted-syntax rule.
- **Tech:** @base-ui/react 1.8.0 Tooltip (unstyled, npm-verified) or native popover plus anchor positioning. popover=hint is not Baseline (no Safari, verified), so use it only as an enhancement.
- **Verifier:** My script reproduces 52 DOM title attributes plus about 30 through Chip, Link, EvidenceChip and Button; one role=tooltip. popover=hint lacks Safari (verified); @base-ui/react 1.8.0 verified. Consider generalising the existing portal-based EvidenceHoverCard instead of adding a dependency.

### `critic-09` Administration is a 3,500px scroll of nine read-only panels with no section navigation, and 'Run' launches production jobs in one click

`medium` · `gap` · effort `M` · corrected

- **Evidence:** frontend/src/routes/admin-config.tsx:240-466 stacks nine panels, and :159-177 handles only #offer-rules, #audit and #data-operations. Screenshot dark__admin-config-populated__full.png is 1440x3512. components/admin/DataOperationsPanel.tsx:230-235 hard-codes confirm:true and reason:'operator_refresh' behind a ghost Run button (:340-355).
- **Problem:** Operators hunt by scrolling, and the audit explorer sits about 2,300px down. A gold refresh that rewrites scores starts from a small unguarded button, and the server's confirm and reason fields are filled in for the user.
- **Recommendation:** Add a sticky section nav with scroll-spy (or nested routes) and a status summary row on top. Put Run behind a native dialog naming the job, last run and downstream effect, with a required reason chosen from the four server enum values. Free-text reasons need a schema change and an audit-redaction review first.
- **Tech:** React Router nested routes or IntersectionObserver scroll-spy; native dialog.showModal(); the existing reason field on the admin run-operation request.
- **Verifier:** Nine stacked panels, three handled hashes, the 3,512px screenshot and hard-coded confirm:true / reason all verified. But the server reason is a closed four-value Literal (backend/schemas/admin.py:111), so free text would 422; a per-job cooldown already exists.

### `critic-10` Sales manager view is four static cards: fixed windows, top-three officers, no workload view

`medium` · `gap` · effort `L` · corrected

- **Evidence:** frontend/src/routes/analytics.sales-ops.tsx:66-68 hard-codes yesterday and week-to-date. :220 slices conversion to three officers, and :162-165 shows capacity only as a roster-total chip. grep type="date" finds one hit, the disposition follow-up (LeadTableDecisionPanels.tsx:153). Analytics' only window is 7/30/90 days for Signals (analytics.tsx:97-98).
- **Problem:** The Sales Manager is a named buyer. Yet they cannot pick a period or compare it with the previous one. They cannot see each officer's capacity against assigned and worked leads, and they cannot act from this view.
- **Recommendation:** As written, plus a new per-officer assigned/open aggregate in the sales store, because SalesTeamMember only carries capacity_per_day; conversion rows already supply worked counts. Effort stays L.
- **Tech:** Existing /api/sales/conversion, aging, team and distributeLeads (frontend/src/lib/api.ts:1584); URLSearchParams state; hand-rolled SVG bars consistent with the current charts.
- **Verifier:** Verified fixed yesterday/week windows, slice(0,3) and the roster-total capacity chip; /sales/conversion already takes from/to. The view does link to a filtered stale queue (:192). Conversion rows carry worked counts, but no endpoint returns assigned-open per officer.

### `critic-11` One concept carries six names, and the glossary teaches the term the rest of the UI scrubs

`medium` · `defect` · effort `M` · corrected

- **Evidence:** home.tsx:235 'Refi economics screen'; borrower-360.tsx:485,490 'Refi economics check' and 'Passes refi screen'; lead-queue.filters.ts:38 'Refi economics'; mortgageGlossary.ts:133-135 'In-the-money'; genieAnswerLanguage.ts:5-21 rewrites Genie's 'in-the-money'. One screen has both 'Approve' (offer-orchestrator.tsx:620) and 'Approve outreach' (ApprovalBanner.tsx:43).
- **Problem:** Users cannot tell whether these are one metric or several, and they search the glossary for a label the screens never use. Two labels for the same approval action blur the gate.
- **Recommendation:** Create a typed lexicon module with canonical label, short label, glossary id and banned variants. Consume it in KPI labels, filters, the glossary and the Genie rewrite. Add an ESLint rule that flags banned variants in JSX strings. Decide the canonical term with compliance.
- **Tech:** TypeScript const map plus ESLint no-restricted-syntax; no runtime dependency.
- **Verifier:** All six labels and both approve labels verified. The glossary does alias 'refi economics' (mortgageGlossary.ts:135). The prototype and CLAUDE.md use 'In the Money', so the canonical choice touches the design contract. Copy is pinned in tests; lexicon, lint rule and rewiring is M.
- **Constraint conflict:** The prototype vocabulary is 'In the Money' (Module 0 Prototype.html, --seg-itm token, CLAUDE.md domain rules). Canonicalising on 'Refi economics' is a declared departure that needs owner and compliance sign-off.

### `critic-v1` Every route shares one document title; tabs, history entries and bookmarks are indistinguishable

`medium` · `gap` · effort `S` · verifier-added

- **Evidence:** frontend/index.html:7 holds the only <title>. grep document.title|useDocumentTitle over frontend/src: none. PageShell.tsx:46 receives a title on 17 routes but only renders an h1. My Playwright probe read 'Mortgage Intelligence Platform' on /admin-config, /glossary, /lead-queue and /analytics.
- **Problem:** Officers keep several borrowers and queues open in tabs, and every tab, history entry and bookmark reads the same. Screen-reader users get no page-change cue, which fails WCAG 2.4.2 Page Titled.
- **Recommendation:** Render a React 19 <title> inside PageShell whenever title is a string, formatted as 'Lead Queue · Mortgage Intelligence Platform'; include the masked borrower id on Borrower 360 and Offer routes. Add a polite live region in AppShell that announces the new title after each navigation.
- **Tech:** React 19 native document-metadata hoisting (<title> rendered inside components); no library.

### `critic-v2` No session-expiry path: a 401 or proxy login redirect looks like a dependency outage

`medium` · `gap` · effort `M` · verifier-added

- **Evidence:** backend/services/rbac.py:174 raises 401 'authenticated identity required'. grep 'status === 401' over frontend/src: zero hits (403 has four). frontend/src/lib/api.ts:855-868 wraps a failed fetch as a generic network ApiError; getJson (:871-881) calls res.json() on any 2xx, including an HTML login page.
- **Problem:** Loan officers keep the app open all day behind Databricks Apps OAuth. When the session lapses, panels show retry or degraded states that blame the warehouse, and an in-progress approval has no sign-in recovery.
- **Recommendation:** Classify 401, opaque redirects and non-JSON 2xx bodies in the API client as reason 'session_expired' and stop retrying them. Show one shell-level banner, 'Your session ended. Reload to sign in', that preserves the current URL. Verify the proxy's real expiry response on the dev workspace before shipping.
- **Tech:** fetch redirect:'manual' or a content-type check; TanStack QueryCache global onError; the existing HealthProvider degraded-banner pattern.

### `critic-v3` Campaign setup is unsaved in-memory state that any in-page link silently discards

`medium` · `gap` · effort `S` · verifier-added

- **Evidence:** frontend/src/routes/portfolio-builder.tsx:136 keeps campaignSetup in useState; app.tsx:68 re-keys the route on pathname. The setup panel itself links away (portfolio-builder.campaign-setup.tsx:249), as do the CTAs at portfolio-builder.tsx:750,758. grep beforeunload|useBlocker|unsaved over frontend/src: none.
- **Problem:** Holdout, budget, channel costs, send window and an applied recommendation vanish when the user follows a link or opens the queue, with no warning. Filters survive in the URL, so the loss looks random.
- **Recommendation:** Persist the draft setup per actor in sessionStorage through the existing actor-scoped registry (lib/actorScopedBrowserState.ts). Restore it on return with a 'Draft restored' chip and a Reset action. With the declarative BrowserRouter (main.tsx:16), prefer restore over navigation blocking; add beforeunload only while the draft is dirty.
- **Tech:** sessionStorage plus ACTOR_SCOPED_SESSION_STORAGE_KEYS; optionally Lakebase workspace state (backend/api/workspace.py) for cross-device drafts.

### `critic-12` Icon set renders sub-pixel strokes at its most common sizes and reuses glyphs for unrelated meanings

`low` · `upgrade` · effort `S` · corrected

- **Evidence:** frontend/src/components/Icon.tsx:78 fixes strokeWidth 1.6 on a 24 viewBox. 79 of 177 <Icon size> usages are 9-12px (grep), which gives 0.6-0.8px strokes. Icon.tsx:21 and :50 (filter, audit) are near-identical three-bar glyphs, and :30,:32 close equals cross. sparkle is used 25 times for five concepts.
- **Problem:** Small icons look faint beside Geist text, and eleven sizes are in use. The audit icon is indistinguishable from filter. Sparkle no longer signals AI because it signals everything.
- **Recommendation:** Keep the prototype glyphs, sizes and the 1.6 default. Add a minimum rendered stroke only at sizes of 12 and below, and a distinct audit glyph, both as declared departures in the commit message. Reserve sparkle for Genie output. Copy individual ISC path data rather than adding lucide-react as a dependency.
- **Tech:** The same idea as lucide's absoluteStrokeWidth; lucide-react 1.47.0 (ISC, npm-verified) as a per-icon glyph source with matching 24px round-cap grammar.
- **Verifier:** Icon.tsx is a verbatim copy of the prototype Icon (Module 0 Prototype.html:986-1031): same 1.6 stroke, same duplicate filter/audit and close/cross glyphs, and the prototype also uses sizes 9-12. Counts (79 of 177, sparkle 25) reproduce. On 2x displays strokes are not sub-pixel.
- **Constraint conflict:** Departs from the prototype icon spec (fixed 1.6 stroke, sizes 9/10/11/15, shared glyphs). Restricting sizes to 12/14/16/20 contradicts the prototype's own usage; any change must cite the prototype line per CLAUDE.md.

**Overflow (titles only, not verified)**

- Type scale drifted smaller than the prototype: var(--fs-11) is the most-used size in components.css (90 uses vs 44 for fs-13; prototype index.html 11 vs 12). 19 rules use literal 9-10px (prototype: 3), including the compliance note .field__sub (components.css:5526).
- borrower-360.tsx:315-317 hard-codes 'Summit demo synthetic' / 'Summit Mortgage demo tenant' instead of the configured lender name (multi-tenant copy leak).
- Lead Queue borrower IDs are not links: reaching a dossier requires expanding the row first (LeadRowPreview.tsx:121-131), so there is no Cmd/Ctrl-click to open in a new tab.
- Growth Agent 'State scope' is a free-text comma list (ask-genie.tsx:595-612) while Portfolio Builder has a state picker. The console exposes nine action buttons with overlapping verbs (Plan, Compose, Execute, Run, Run custom, Save x4).
- Glossary hero CTA is always 'Back to leads' regardless of origin (glossary.tsx:38-41), and every entry repeats its category chip under a header that already names it (:85).
- Vendor brand appears three times on Home (rail mark, mid-page wordmark, footer) while the lender is a chip; fold this into the white-label work.
- ActivationLoopPanel speaks implementer language ('dry-run outbox row for implementation proof only', ActivationLoopPanel.tsx:199-203) and shows a raw activation UUID as the receipt (:208). There is no preview of the payload the CRM will receive and no approved-to-staged-to-delivered timeline.
- CSV export header says 'confidence' (LeadTable.csv.ts:33) while the UI says 'Signal'. 'Signal' also names the trigger type in Analytics (analytics.sections.tsx:341).
- Offer Orchestrator repeats the 'Primary offer' card and the borrower flags already shown on Borrower 360. Eight to nine 'No ... / Not ...' chips dominate the dossier (BorrowerTruthFlags).
- DataOperationsPanel launch errors render as muted 12px text with role=status, visually identical to help text (DataOperationsPanel.tsx:270-277).
- Borrower 360 warm-up and admin loading copy exposes infrastructure ('Probing Lakebase...', 'Databricks SQL warehouses auto-suspend when idle') to business users (borrower-360.tsx:141, admin-config.tsx:364,415).

**Limitations:** - **Method:** This was a read-only code audit plus fixture screenshots. The app was not run. The Playwright MCP server failed to connect, so there was no live keyboard-only walkthrough, no non-admin session and no screen-reader pass.
- **critic-03:** Verified from code paths only (AdminDep on the API, no gate on the drawer link). It was not observed as a non-admin user.
- **critic-01:** Inferred from the compose route re-calling the Supervisor. Two differing compositions were not observed live.
- **critic-06:** Server-side legality of lifecycle transitions was not inspected.
- **Theme coverage:** Only dark-theme screenshots were reviewed for these surfaces.
- **Version and Baseline claims:** Library versions were checked with npm view. Browser-support claims were checked by web search, not against caniuse raw data. The anchor-positioning results conflicted slightly on Firefox version numbers.
- **Surfaces not reached:**
  - DataEstatePanel internals
  - PropertyLookupPanel
  - a full read of segment-intelligence.tsx
  - ask-genie growth-run-card, saved-monitors and growth-agent-drafts
  - analytics.approval-funnel.tsx
  - GenieHistoryMenu, GenieAnswerFeedback and GenieAnswerActions
  - CampaignPrefillBanner and campaignPrefill
  - PinnedInsights and TopLeadsQuickPick
  - portfolio-builder.saved-campaigns and governance
  - brand/Entrada
  - print.css
  - the populated asset-detail state

---

## Over-the-top proposals — judged and ranked

**Judge's theme:** The differentiator is not more AI or more motion; it is making the governed arithmetic this product already performs visible and interactive: drag the rate and watch the book recount through the same UC functions (Rate Lever), see why a lead is a 94 with a recompute seal and deterministic margins (score spine), and watch an approval close into the real Lakebase ledger row (Decision receipt, plus a Triage deck that finally shows approvers the copy they are approving). Those share one story a sceptical Head of Growth and a Databricks field engineer will both remember (every number is reproducible, every decision is on the record), and none needs a new dependency, a framework change or a departure from the prototype beyond documented BEM extensions. Sequencing caveats: the proposals' file:line references came from a working tree 25 commits behind origin/main (908b13c9), where LeadTable.tsx, USChoroplethMap.tsx, sales_state.py and api.ts were split, so landing sites above are re-anchored on main; every bundle schedule ships PAUSED, so anything depending on a scheduled job (snooze wake, watchlist briefings, rate watch) is dormant on a default deploy; and two verified defects deserve fixing regardless of ranking: queue-row approve never renders the governed draft it approves (useLeadApprovalActions.ts:150-185), and 'score_balanced' is written to the audit trail while allocation is a plain modulo (sales_state_writes.py:181, :230).


### `wow-stage-2` Score anatomy: five-component score spine with a recompute seal (merges wow-ai-1's bar)

`build-now` · wow 8 · feasibility 9 · effort `M` · lens: wow-stage

- **Concept:** Merged with wow-ai-1's bar. A five-segment spine beside the score in the dossier header and row preview, fed by the existing /proof payload; each segment opens its evidence. Seal reads 'Recomputed from lead_scores components: matches', shown only when proof.trusted; otherwise list known_data_gaps. Cut 'vs next-ranked' from v1; queue-row spines wait for a lead_population projection change.
- **Where it lands:** New frontend/src/components/mortgage/ScoreSpine.tsx (.score-spine__seg, documented as a BEM extension echoing the prototype's .conf__bars, design_files/index.html:556-565); mount at frontend/src/routes/borrower-360.tsx:286 and frontend/src/components/mortgage/LeadRowPreview.tsx:112; enable the proof query on dossier mount (shares queryKeys.borrowerProof with BorrowerProofDrawer.tsx:49-54). Queue rows (LeadTableRow.tsx:244) are a later slice: the list reads gold.lead_population (backend/services/repositories/databricks_leads.py:63), which has no sub-score columns.
- **Tech:** Plain SVG/CSS on existing tokens; TanStack Query shared key; no new dependency. React 19.3 <ViewTransition> is genuinely stable (react 19.3.0 published 2026-09-09 on npm; react.dev blog confirms) but treat the morph as optional polish in its own bump commit; nothing should depend on it.
- **Risk:** Seal must never show when proof.trusted is false. 'Unity Catalog recomputed' overclaims: arithmetic is Python parity over UC rows. Five hues need text labels. One extra warehouse query per dossier.
- **Already exists?** No — Partly. Payload and long-form maths exist: backend/schemas/proof.py:32-39 (weight, weighted_points); live recomputation and mismatch gaps at backend/services/repositories/databricks_borrower_proof.py:346-425 (origin/main); rendered only inside frontend/src/components/mortgage/BorrowerProofDrawer.tsx:167-200 behind the drawer (query enabled only on open, :49-54). No spine anywhere: ScoreBadge.tsx:8-20 is dot + number; grep for spine|anatomy|breakdown in frontend/src returns nothing. Delta = the visual, the seal, the prefetch.

### `wow-stage-1` The Rate Lever: live rate-scenario scrubber on the geography hero (merges wow-ai-2's dial)

`build-now` · wow 9.5 · feasibility 7 · effort `L` · lens: wow-stage

- **Concept:** Merged with wow-ai-2's dial. Third 'Map coloring' mode: a slider moves the 30-year par ±100 bps; map, legend total and a plain-English headline recount from a precomputed state × scenario-step gold rollup. Compute each step by re-running fn_rate_spread and fn_in_the_money, not by shifting rounded bins. Fixed 'Scenario, not a forecast' label; cohort handoff is phase two.
- **Where it lands:** origin/main: frontend/src/components/mortgage/USChoroplethMap.tsx:578 (third toggle), USChoroplethMapLegend.tsx:36 (slider host), new RateScenarioControl.tsx + rateScenario.logic.ts; surfaces via frontend/src/routes/home.tsx:308. Backend: GET /api/v1/geo/rate-sensitivity beside backend/api/geo.py:120; new sql/transformations/gold_rate_sensitivity_rollup.sql + DDL, declared after gold_state_top_segment in databricks.yml (:754) so deploy.sh provisions it. Follow gold_state_top_segment's registration pattern (about 12 production files: geo api/schema/repo/sql, databricks_sql_helpers.py allowlist, protocols.py, DDL, capture_refresh_timestamp.sql).
- **Tech:** Native input[type=range] + React useDeferredValue; fifty state paths recolour fine through React, so skip the outside-React custom-property trick. CSS @property count tween is Baseline Newly available (2024-07) with instant fallback. No new dependency. The slider is a documented extension beyond the prototype, built from tokens.
- **Risk:** bround half-even makes bin-shifting inexact for odd steps (eighth-point coupons land on .5 bps). Gated/NULL-rate rows must be excluded. Misread as forecast. Cohort handoff needs a new max-spread bound.
- **Already exists?** No — No. grep scenario|what-if|rate.?sensitiv over frontend/src, backend, sql hits only the ROI projector (frontend/src/routes/portfolio-builder.components.tsx, backend/schemas/portfolio.py). Inputs exist: borrower_360 carries market_rate_fraction and min_spread_bps_applied (sql/transformations/gold_borrower_360.sql:472-493, :787, :1273); the map already has a two-button 'Map coloring' group (USChoroplethMap.tsx:578 on main). Live spread domain is p50 0 / p99 122 bps against a 75 bps default threshold (backend/schemas/genie_numeric_filters.py:32-37), so a rate move visibly changes the book. The prototype has no slider (grep of design_files).

### `wow-stage-3` Decision receipt: the approval moment ends in the real Lakebase ledger row

`build-now` · wow 8 · feasibility 8 · effort `M` · lens: wow-stage

- **Concept:** After the approve write returns, read the ledger row back and assemble a Decision receipt: evidence ids, recomputed score (matches), selected offer branch, exact copy hash, approver, audit id, request id, timestamp. Printable. Open it as a drawer or ?receipt= view from the audit explorer rather than a twelfth route. Ship CSS stagger first; ViewTransition later.
- **Where it lands:** New frontend/src/components/mortgage/DecisionReceipt.tsx replacing offer-orchestrator.tsx:803-811; reused by the queue approve path (useLeadApprovalActions.ts:121) and Borrower 360. New actor-or-admin scoped GET /api/v1/audit/receipt/{audit_id} beside backend/api/audit.py:115, modelled on my-events scoping. Print through frontend/src/design-system/print.css. Deep link as a search param on the existing audit explorer (components/admin/AdminAuditExplorer.tsx) to stay inside the eleven-route contract.
- **Tech:** TanStack useMutation onSuccess read-back; CSS animation-delay stagger with prefers-reduced-motion rendering the finished receipt. React 19.3 <ViewTransition> shared element is optional later polish (needs 19.2.8 to 19.3.0 plus an @types/react bump; same-document View Transitions are Baseline Newly available since Firefox 144).
- **Risk:** Never animate before the read-back confirms the row; failed writes show failure. Endpoint must be actor-or-admin scoped and return only public-safe audit fields. Keep under 1.2 s and skippable.
- **Already exists?** No — No. Approval success is a .burst chip plus 'audit: <uuid>' in 11px muted mono (frontend/src/routes/offer-orchestrator.tsx:803-811 on main); grep 'receipt' over frontend/src and backend returns nothing. Inputs exist: audit request_id / correlation_id / evidence_ids (backend/schemas/audit.py:32-42), draft response_hash (frontend/src/components/mortgage/useLeadApprovalActions.ts:173), proof offer_branches (backend/schemas/proof.py:43-48). No single-event read endpoint: backend/api/audit.py exposes my-events (:115), events (:170), events/page (:219), rollups (:301) only.

### `wow-ai-3` Delta Explainer: every 'since your last login' number opens its own attribution

`build-next` · wow 7.5 · feasibility 7.5 · effort `M` · lens: wow-ai

- **Concept:** Each 'since your last login' number opens a contribution waterfall inside the EvidenceDrawer: which states gained or lost borrowers between the baseline snapshot and today, the par-rate print on both dates, and any offer-rules change. State bars read the _ALL segment rows. Each bar links to that cohort. Copy says 'coincided with', never 'caused'.
- **Where it lands:** New GET /api/v1/home/summary/attribution beside backend/api/home.py:27; new backend/services/home_attribution.py reading mip.gold.funnel_snapshot_daily + mip.silver.market_rates_weekly; DeltaExplainer panel opened from LastLoginSummary.tsx SummaryNumber inside the existing EvidenceDrawer; waterfall as hand-rolled SVG beside the primitives in frontend/src/routes/analytics.charts.tsx.
- **Tech:** Hand-rolled SVG waterfall; TanStack Query keyed by measure + baseline date. No new infrastructure, no new dependency.
- **Risk:** Bundle schedules ship PAUSED (databricks.yml:878), so snapshot history may have gaps on a fresh workspace: verify live first and show the nearest-snapshot note. Accounting, not causation.
- **Already exists?** No — No. SummaryNumber opens a generic source drawer only (frontend/src/components/mortgage/LastLoginSummary.tsx:59-84 on main). The needed grain already exists: mip.gold.funnel_snapshot_daily is (snapshot_date, state, segment_code) with '_ALL' rollups, MERGEd per scoring refresh (sql/transformations/gold_funnel_snapshot_daily.sql:4-17), and is already read by backend/api/geo.py and backend/services/repositories/databricks_analytics.py:232.

### `wow-ai-1` 'What would change this?' deterministic margins (delta after merging the bar into wow-stage-2)

`build-next` · wow 8 · feasibility 7 · effort `M` · lens: wow-ai

- **Concept:** Bar merged into wow-stage-2; this is the delta. Beside the spine show deterministic margins: 'clears the refi screen by 13 bps; drops out if 30-year par exceeds 5.00%' and the equity level where the offer branch flips. Phase one uses thresholds already on the row; defer the SQL sub-score port. Label: marketing prioritisation, not a credit decision.
- **Where it lands:** backend/schemas/proof.py (margins list on BorrowerProof); backend/services/repositories/databricks_borrower_proof.py _build_borrower_proof; backend/services/scoring.py next_best_offer for branch perturbation; rendered under ScoreSpine in frontend/src/routes/borrower-360.tsx and the Primary offer card in routes/offer-orchestrator.panels.tsx; long form stays in BorrowerProofDrawer.tsx.
- **Tech:** Pure Python over the reviewed scoring functions, pinned by new golden fixtures; no model, no dependency. Phase 2 sub-score what-ifs need a Python port of the SQL subscores CTE (gold_borrower_360.sql:984-1028): defer, SQL/Python drift risk.
- **Risk:** Margins can read as adverse-action reason codes: keep the 'not a credit decision' label. Perturb only economic inputs. Must agree with the Rate Lever's threshold arithmetic or the two contradict.
- **Already exists?** No — Bar: no (see wow-stage-2). Margins: no; BorrowerProof has no margins field (backend/schemas/proof.py:70-95). Inputs already sit on the row: rate_spread_bps, market_rate_fraction, min_spread_bps_applied (sql/transformations/gold_borrower_360.sql:1115-1116, :1273); offer recomputation already runs in _build_borrower_proof (backend/services/repositories/databricks_borrower_proof.py:352).

### `wow-power-1` Triage Mode: one-at-a-time approval deck that shows the governed draft before approve

`build-next` · wow 8.5 · feasibility 6.5 · effort `L` · lens: wow-power

- **Concept:** Today's row approve generates the governed draft and approves it without ever showing the copy. Triage fixes that: a one-borrower deck inside /lead-queue (?mode=triage, not a new route) showing why-now, offer, evidence and the draft; A arms only once the draft has rendered. Decisions commit under one bulk_id and rationale. Cut cross-tab marks from v1.
- **Where it lands:** New components/mortgage/TriageDeck.tsx + TriageTray.tsx mounted from frontend/src/routes/lead-queue.tsx; reuse useLeadApprovalActions but pass the displayed draft's generation_id / response_hash into approve instead of re-drafting at commit; card body reuses LeadRowPreview.tsx; entry in components/command/commandActions.ts. Slice 0 is build-now on its own: show the draft in the expanded row before the A key arms.
- **Tech:** React 19.2 <Activity> (stable; repo on 19.2.8) keeps the virtualised queue mounted, but the @tanstack/react-virtual list must re-measure on reveal; useOptimistic for the tray; TanStack prefetchQuery for the next dossiers (GET only). No new dependency. Sequence after the keymap registry (wow-power-4).
- **Risk:** Every card viewed is an audited draft generation, including skips. Commit must bind to the draft shown and handle a stale draft proof. Rubber-stamping optics: record time-per-decision.
- **Already exists?** No — Partly. No triage surface (grep triage|useOptimistic|<Activity over frontend/src: none). The extraction the proposal asks for is already done on main: approveLead and bulkApprove live in frontend/src/components/mortgage/useLeadApprovalActions.ts:121 and :346, hotkeys in useLeadTableHotkeys.ts. Confirmed gap: approveLead calls api.draftOutreach then api.approve with draft_subject/draft_body and the copy is never rendered (useLeadApprovalActions.ts:150-185); LeadRowPreview.tsx contains no draft.

### `wow-power-6` Filter omnibox: type the cohort, see the count before Enter (merges wow-ai-6)

`build-next` · wow 7 · feasibility 7 · effort `M` · lens: wow-power

- **Concept:** Merged with wow-ai-6. Press F and type 'TX IL heloc unassigned aged>7'. A client-side closed-vocabulary parser maps tokens onto the existing URL filter params, shows each chip with the phrase it came from, lists unrecognised words, and shows the live count before Enter. Unrecognised text offers the existing guarded Genie hand-off. The dropdown row stays. No new guard surface.
- **Where it lands:** New frontend/src/lib/filterGrammar.ts (pure; pinned to the option arrays in routes/lead-queue.filters.ts and lib/uspsStates.ts) + components/mortgage/FilterOmnibox.tsx in the filter surface header of routes/lead-queue.tsx, then segment-intelligence; thin GET /api/v1/leads/count over the existing repository count (shared with wow-power-2); 'Filter leads' group in components/command/CommandPalette.tsx.
- **Tech:** Hand-written tokenizer; WAI-ARIA APG combobox; TanStack Query keepPreviousData, 200 ms debounce, AbortSignal cancellation; counts through the short-TTL cache in backend/services/resilience.py. No LLM, no library.
- **Risk:** Two-letter words collide with states (IN, OR, ME): require uppercase or confirmation. Count must use the queue's contactable-only default. Each count is a warehouse query; expect 1-2 s, not instant.
- **Already exists?** No — No. grep omnibox|filterGrammar over frontend/src: none. The vocabulary to pin against exists (frontend/src/routes/lead-queue.filters.ts:15-35); true counts are already emitted as X-Total-Matching (backend/api/leads.py:495). CommandPalette has no filter group (components/command/commandActions.ts:12-14 has route and command targets only).

### `wow-power-5` Distribution Board: capacity-aware assignment with a dry-run preview (and an honesty fix)

`build-next` · wow 6.5 · feasibility 7.5 · effort `M` · lens: wow-power

- **Concept:** First fix an honesty defect: 'score_balanced' is recorded in the audit trail but allocation is a plain modulo, and capacity_per_day and region are never read. Implement a deterministic greedy fill, add dry_run to the same endpoint, and show a preview board: per-officer load against capacity, proposed additions, summed score. Commit re-validates. Cut arrow-key moves from v1.
- **Where it lands:** backend/services/sales_state_writes.py:158 distribute(); DistributeLeadsRequest in backend/schemas/sales.py:145-150 (dry_run); new frontend/src/components/mortgage/DistributionBoard.tsx opened from LeadTableBulkActions.tsx:105 and routes/analytics.sales-ops.tsx. The label fix (stop sending or recording 'score_balanced' until it is real) is a build-now one-liner.
- **Tech:** CSS grid bars on existing tokens; TanStack useMutation dry-run then commit under the same request_id idempotency (advisory lock already in place); allocation unit-pinned under tests/unit. No library.
- **Risk:** Preview and commit can diverge under concurrent assignment: re-validate and show the difference. Keep region matching at state grain. Allocation rules must be explainable or the board reads as favouritism.
- **Already exists?** No — Board: no. Defect verified on main: backend/services/sales_state_writes.py:181 and :230 assign assignees[idx % len(assignees)] for every strategy; the comment at :213 says the caller score-orders, but backend/api/sales.py:180-222 passes borrower_ids straight through; frontend/src/components/mortgage/useLeadSalesActions.ts:142 sends 'score_balanced' whenever many leads go to one officer, and that label is what the audit row records. capacity_per_day / region are selected (backend/services/sales_state_core.py:60, :93) and never consumed.

### `wow-stage-4` 'Crossed the line': per-borrower note rate vs the FRED market series

`build-next` · wow 8 · feasibility 6 · effort `L` · lens: wow-stage

- **Concept:** In the dossier, draw the fixed note rate against the weekly FRED 30-year series, shade the spread, draw the lender's threshold and mark the first week it was crossed: 'In the money since <week>'. Fixed-rate liens only; adjustable or clamped rates get an empty state. Hand-roll the SVG; skip d3. Series starts 2021 unless the FRED window is extended.
- **Where it lands:** New frontend/src/components/mortgage/SpreadHistoryChart.tsx beside 'Refi economics check' (routes/borrower-360.tsx:485) and above the Trigger timeline (:471); series + crossed week added to the lazy /proof payload (backend/services/repositories/databricks_borrower_proof.py); sql/transformations/gold_borrower_dossier.sql gains first_pos_date and first_pos_rate_type; optionally extend the FRED cosd window and the committed seed together.
- **Tech:** Hand-rolled SVG: about 280 weekly points needs no d3-scale / d3-shape (both mature at 4.0.2 / 3.2.0 on npm, but unnecessary and they count against tools/check_frontend_budgets.mjs); crossing week computed server-side with fn_rate_spread semantics; SVG pathLength draw-in.
- **Risk:** A flat line is wrong for adjustable-rate loans. The historical threshold is today's rule applied backwards: say so. Must share fn_rate_spread's market-rate definition. Seed-sourced weeks labelled. Needs a text alternative.
- **Already exists?** No — No. No FRED series endpoint or chart in frontend/src or backend/api (grep market_rates_weekly|fred hits only labels, drawer sources and admin job status). Gaps the proposal missed: FRED ingest and seed start at 2021-01 (jobs/fred_rates_ingest.py:64 cosd=2021-01-01; data/seeds/fred_mortgage30us_seed.csv first row 2021-01-07, 276 rows); origination date is not in the dossier contract (only first_pos_age_months, sql/ddl/003_gold_tables.sql:251; first_pos_date stops at gold_borrower_360.sql:165); rate type exists only in silver (sql/transformations/silver_lien_current.sql:85).

### `wow-power-3` Follow-ups Due inbox, then Snooze with wake-on-signal

`build-next` · wow 7 · feasibility 6.5 · effort `L` · lens: wow-power

- **Concept:** Two follow-up dates are captured and shown nowhere: callback_at on dispositions and follow_up_at on approvals. Phase one is a 'Due' inbox (overdue, today, this week) on Home and as a queue lens, with no new table. Phase two adds Snooze with wake-on-signal, but bundle schedules ship paused, so wake must also run from the admin refresh.
- **Where it lands:** Phase 1: GET /api/v1/sales/followups in backend/api/sales.py unioning dispositions.callback_at and approvals.follow_up_at; new frontend/src/components/mortgage/FollowUpInbox.tsx on routes/home.tsx beside PinnedInsights (:207). Phase 2: mip_app.lead_snoozes in lakebase/schema.sql + jobs/lakebase_migrate.py; wake step reading mip.gold.evidence_events in the lifecycle sync job; POST /leads/{id}/snooze.
- **Tech:** Lakebase partial index on (actor_email, wake_at) WHERE woken_at IS NULL; Intl.RelativeTimeFormat through lib/time; TanStack Query. In-app only: no push, email or Badging API, which keeps the no-automatic-outreach posture clean.
- **Risk:** Wake must respect suppression and eligible_recontact_at. Lifecycle sync job has had fossil tasks: change it only through deploy.sh. Wake value depends on how often the Cotality share refreshes.
- **Already exists?** No — No inbox or snooze (grep snooze|followup over frontend/src: none). callback_at is written (backend/api/sales.py:258; backend/schemas/sales.py:199-241) and reaches the frontend only as a request field (useLeadSalesActions.ts:174) and CSV export. A second captured date the proposal missed: approval follow_up_at is stored (backend/api/outreach.py:363-471; backend/schemas/offer.py:380, :451) and shown once as a transient chip (frontend/src/routes/offer-orchestrator.tsx:788-797).

### `wow-stage-5` Signal stack: where several Cotality signals fire on the same borrower

`build-next` · wow 6.5 · feasibility 7 · effort `M` · lens: wow-stage

- **Concept:** Lead with one sentence: 'N borrowers fire three or more signals at once', click-through to that intersection in Lead Queue. The UpSet strip sits beneath as secondary detail. Restrict to the six core segments so at most 63 exact combinations make inclusive counts exact; thirteen codes capped at 64 rows would not be. Carry addressable and contactable counts.
- **Where it lands:** frontend/src/routes/segment-intelligence.tsx between the SegmentCard row and the Any/All bar; new components/mortgage/SignalStack.tsx + signalStack.logic.ts; GET /api/v1/segments/combinations beside backend/api/segments.py:88; new sql/transformations/gold_segment_combination_rollup.sql declared after gold_state_top_segment (databricks.yml:754). Click-through reuses segment_codes + segment_mode=all.
- **Tech:** Pure SVG/CSS grid in BEM; inclusive counts summed client-side from exact-combination rows; table alternative for accessibility; optional CSS @property tween. No library.
- **Risk:** UpSet is unfamiliar to executives. The known ~23x addressable-versus-contactable gap will make column counts disagree with the queue unless both are shown. New gold table carries a ~12-file registration cost.
- **Already exists?** No — No. grep upset|combination|overlap over frontend/src and backend/api/segments.py finds only the Any/All copy (frontend/src/routes/segment-intelligence.tsx:515, :600, :677). SegmentCode has 13 values (frontend/src/types.ts:5-21), so the proposal's 'at most 64 exact-combination rows' is exact only for the six core codes; a top-64 cap over 13 codes would make the client-side inclusive counts a lower bound, not a count.

### `wow-power-4` Keyboard grammar: one scoped keymap feeding handlers, a ? overlay, hints and Cmd-K verbs

`build-next` · wow 5.5 · feasibility 8 · effort `M` · lens: wow-power

- **Concept:** One scoped keymap registry feeding handlers, a ? overlay filtered to the page, button hints and Cmd-K verbs on the current selection ('Approve 12 selected'). Migrate the five shortcut listeners; leave FilterSelect's Escape and useFocusTrap component-local. Per-user single-key toggle for WCAG 2.1.4. Build it before Triage, which depends on it.
- **Where it lands:** New frontend/src/lib/keymap.ts + components/command/ShortcutOverlay.tsx; migrate the listeners above; extend CommandTarget with a 'verb' kind fed by a selection context published from LeadTable; toggle persisted through backend/api/workspace.py. Reuses existing kbd and .cmdk__* BEM.
- **Tech:** tinykeys 4.0.0 (npm-verified 2026-09-21, about 1 KB, supports sequences) or an in-house matcher of similar size; TanStack Hotkeys is 0.11 alpha (npm-verified): do not adopt. aria-keyshortcuts on bound controls.
- **Risk:** Chords collide with screen-reader and browser keys: scope strictly, keep everything switchable off. Cmd-K verbs must call the same guarded handlers, never a parallel approval path.
- **Already exists?** No — No registry or overlay. Seven independent window keydown listeners verified on main: components/command/CommandPalette.tsx:95, components/layout/Topbar.tsx:206, components/mortgage/GenieChat.tsx:199, components/mortgage/useLeadTableHotkeys.ts:37 (moved out of LeadTable.tsx by the file-size split), components/ui/FilterSelect.tsx:43, hooks/useFocusTrap.ts:82, routes/analytics.sections.tsx:389. CommandTarget has only 'route' and 'command' kinds (components/command/commandActions.ts:12-14).

### `wow-power-2` 'More like this': pattern cohorts from a single decision

`build-next` · wow 6 · feasibility 7 · effort `M` · lens: wow-power

- **Concept:** After a decision, offer 'N more like this' from reviewed attributes only (segment, offer code, state, relationship, score band), with the true count and the cohort's ranges. Actions: open as a Triage deck, shortlist or assign. Drop batch approve: approving thirty on three sampled drafts contradicts Triage's every-draft-in-view defence. Build after Triage.
- **Where it lands:** New frontend/src/lib/leadPattern.ts (pure, unit-pinned) + components/mortgage/SimilarCohortPanel.tsx; entry beside 'Build offer' in LeadRowPreview.tsx and the Primary offer card in routes/borrower-360.tsx; backend/api/leads.py exposes min_score / max_score and the shared GET /leads/count (also needed by wow-power-6).
- **Tech:** Deterministic rule over reviewed filter vocabulary, deliberately not embeddings or an LLM; TanStack Query keyed by the pattern. No new library.
- **Risk:** Similarity must never use sub-state geography or demographic proxies. 'One decision becomes thirty' is the sentence a compliance buyer fears: keep each approval individual.
- **Already exists?** No — No. Building blocks exist: the lead repository already accepts min_opportunity_score and min_rate_spread_bps through cohort replay (backend/api/leads.py:283-298, :393-396) and X-Total-Matching gives true counts (:495). There is no public min/max score query parameter and no /leads/count route.

### `wow-ai-4` Watchlists that brief you: run-over-run diffs and new-entrant cohorts on Home

`build-next` · wow 7 · feasibility 5.5 · effort `L` · lens: wow-ai

- **Concept:** Each saved watchlist becomes a Home briefing row: actionable count, change since the previous run, average-score change, sparkline, run age. 'Review new entrants' opens a replayable cohort. Replace the hard-coded 'scheduler paused' string with a capability check. Needs a monitor_id on growth_agent_runs first; without it there is no reliable previous run to diff against.
- **Where it lands:** lakebase/schema.sql + jobs/lakebase_migrate.py (nullable monitor_id on growth_agent_runs; later growth_agent_run_members mirroring genie_cohort_members); backend/services/growth_agent_ledger_sql.py (LAG over runs per monitor); backend/services/lead_cohort_replay.py; new frontend/src/components/mortgage/WatchlistBriefings.tsx in routes/home.tsx; scheduler state from backend/services/capabilities.py.
- **Tech:** Lakebase Postgres window query; existing Sparkline.tsx; existing bundle job mip_growth_agent_monitor_scheduler surfaced through a real capability check. No new dependency.
- **Risk:** With the scheduler paused by default the briefing is stale on every fresh deploy: show run age and the paused state honestly. Membership snapshots grow: cap and expire. One card, never toasts.
- **Already exists?** No — No Home watchlist surface (grep watchlist|monitor in frontend/src/routes/home.tsx: none). 'scheduler paused' is hard-coded at frontend/src/routes/ask-genie.saved-monitors.tsx:42. Runs store counts and averages (lakebase/schema.sql:2395-2400) but carry no monitor_id (schema.sql:2376-2412); monitors hold last_run_id only (backend/services/growth_agent_ledger_sql.py:85-92), so the claimed 'Phase 1 needs no schema change' does not hold. The scheduler ships PAUSED (databricks.yml:969-980), as does every bundle schedule (:870-878).

### `wow-ai-5` Living Genie tiles: pin an answer and keep it alive

`prototype-first` · wow 8 · feasibility 4.5 · effort `L` · lens: wow-ai

- **Concept:** 'Pin as live tile' stores the Genie conversation, message and attachment ids; refresh re-executes Genie's own SQL through the Genie API, re-runs trust policy and guards fail-closed, else shows the dated snapshot. Prototype first: refresh must be asynchronous, Lakebase-cached, once per gold refresh, never on Home's critical path. Tiles show rows, never the stale narrative.
- **Where it lands:** backend/services/genie_client.py (execute_attachment_query beside the attachment fetch at :428); POST/GET /genie/tiles and /genie/tiles/{id}/refresh in the genie API package; new mip_app.genie_tiles in lakebase/schema.sql via jobs/lakebase_migrate.py; frontend/src/lib/pinnedInsights.ts becomes a server store with local fallback; PinnedInsights.tsx renders GenieAnswerCharts per tile.
- **Tech:** Genie 'Execute message attachment SQL query' endpoint (listed in the Databricks API reference and the CLI as execute-message-attachment-query; verified 2026-09-21; only valid while the message is EXECUTING_QUERY or COMPLETED); TanStack Query staleTime tied to the gold refresh stamp; existing retry and circuit breaker in backend/services/resilience.py.
- **Risk:** The answer-side guard scan is already superlinear per answer; six tiles multiply it. Conversation retention and the calling identity are unmeasured. Every refresh is warehouse spend. Must not become a dashboard builder.
- **Already exists?** No — Static pins exist: frontend/src/lib/pinnedInsights.ts (actor-scoped localStorage, MAX_PINS 6, 220-char summary, trusted-source denylist) and components/mortgage/PinnedInsights.tsx on Home (routes/home.tsx:207). query_attachment_id is already captured (backend/services/genie_client.py:112, :367-369); no execute-query call exists anywhere in backend (grep). Delta = server store + live re-execution + guards on refresh.

### `wow-ai-2` Rate-trigger watchlist ('Watch this level'), delta after merging the dial into wow-stage-1

`build-next` · wow 6 · feasibility 5 · effort `M` · lens: wow-ai

- **Concept:** Dial merged into wow-stage-1; this is the delta. 'Watch this level' saves a reviewed watchlist that flags when the weekly FRED print crosses the chosen par rate and creates a review draft only. Sequence after the Rate Lever and after the scheduler capability check, because every bundle schedule ships paused. Use the lever's exact step grid, not 12.5-bps buckets.
- **Where it lands:** backend/services/growth_agent_workflows.py (new reviewed workflow rate_trigger_watch); lakebase/schema.sql workflow_id CHECK + migration through jobs/lakebase_migrate.py; backend/services/audit_metadata_policy.py registration; 'Watch this level' button inside RateScenarioControl from wow-stage-1.
- **Tech:** Plain comparison of the latest mip.silver.market_rates_weekly row against the saved level inside the existing monitor runner; draft-only notifications; committed FRED seed covers first boot. No library.
- **Risk:** Dormant on a default deploy because schedules ship paused. A CHECK-constraint migration on an append-only ledger needs care. Must never imply a rate forecast or alter stored scores.
- **Already exists?** No — Dial: no (see wow-stage-1). Watch: no rate-trigger workflow; the workflow_id CHECK lists eight reviewed workflows (lakebase/schema.sql:2380-2390). Watchlist and scheduler plumbing exists (backend/services/growth_agent_workflows.py:186-205; databricks.yml:969-980, PAUSED). The ROI projector's baseline/manual toggle is a conversion scenario, not a rate scenario.

### `wow-stage-6` Boardroom mode with an in-app Stage check and synced presenter notes

`reject` · wow 4 · feasibility 6 · effort `L` · lens: wow-stage

- **Concept:** Reject as proposed: presenter notes, beat rail and a second window are sales tooling inside a customer product, the audience never sees them, and a 1.2× type scale will overflow the 1440×900 layout (browser zoom already does this). Salvage one piece later: an admin 'Readiness check' that runs the rehearsal checklist in-app and reports real degraded states.
- **Where it lands:** Salvage only (effort S-M, build-next): a 'Run readiness check' action in frontend/src/routes/admin-config.tsx composing /api/v1/health, /api/v1/data-estate (public_demo_masking) and the existing capability panels, replacing the curl steps in docs/module0-rehearsal-checklist.md.
- **Tech:** BroadcastChannel (Baseline widely available), Screen Wake Lock (newly available 2024-05) and Fullscreen are not blocked by the Permissions-Policy at backend/main.py:509-511 (geolocation, camera, microphone only), but none is needed for the salvage. Document Picture-in-Picture is not in Safari.
- **Risk:** Shipping talk-track claim boundaries inside the customer bundle is a packaging smell. A warm Genie turn is a real audited, billable query: keep it explicit and human-triggered.
- **Already exists?** No — Partly. Readiness data already exists in Admin: frontend/src/components/admin/PlatformCapabilitiesPanel.tsx, BuyerReadinessPanel.tsx, DataOperationsPanel.tsx, plus startup warm hooks (backend/main.py:135-191). No presenter or stage mode (grep presenter|boardroom|BroadcastChannel|wakeLock: comments only). Health probes deliberately do not warm billable dependencies (backend/api/health.py:33). Only data-density / data-console attributes exist (AppContext.tsx:226, :239; tokens.css:289-297).

### `wow-ai-6` Intent bar: server-side sentence-to-filters parse (delta after merging into wow-power-6)

`reject` · wow 3 · feasibility 4 · effort `M` · lens: wow-ai

- **Concept:** Merged into wow-power-6, which keeps the chip-with-source-phrase idea and the Cmd-K entry. Reject the rest: a server-side parse endpoint that reuses protected_prompt_match creates another guard surface over terse filter text, where those Genie-tuned guards have a record of accidental refusals. A closed client grammar cannot set an unreviewed filter, so the guard adds risk without protection.
- **Where it lands:** None as a separate item; chip provenance and the 'Filter leads' Cmd-K group land with wow-power-6 (frontend/src/lib/filterGrammar.ts, components/command/CommandPalette.tsx). Free text reaches a guard only through the existing 'Ask Genie instead' hand-off.
- **Tech:** Server-side Python grammar was proposed; the proposer rightly rejected Chrome's on-device Prompt API (bypasses the server guard, Chrome and hardware gated).
- **Risk:** Reusing prompt guards on filter text invites false refusals and a new surface to maintain. If ever built server-side, derive vocabulary from one registry shared with the dropdowns.
- **Already exists?** No — Helpers exist for Genie prompts, not for the queue: segments_from_prompt and segment_mode_from_prompt (backend/schemas/growth_agent_segment_intent.py:136, :195), city/state parsing (backend/schemas/genie_geo_filters.py:101), protected_prompt_match (backend/services/genie_message_policy.py:137). No /leads/filters/parse route exists.
