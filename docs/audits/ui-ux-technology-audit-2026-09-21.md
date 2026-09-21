# UI/UX, Strategy and Frontend Technology Audit — 2026-09-21

Scope: every UI/UX surface, the design system, the frontend stack, and the API-to-UI delivery path of Module 0. Question asked: is it the latest and greatest everywhere, where is the room to upgrade, and what would take it over the top visually and in performance.

Full evidence for every item below (file:line, verifier notes, constraint conflicts) is in the companion register: [ui-ux-technology-audit-2026-09-21-findings.md](ui-ux-technology-audit-2026-09-21-findings.md). Finding ids such as `runtime-02` refer to it.

## 1. Verdict

**The toolchain is state of the art. The product is not yet using it, and the money screens have defects a buyer will see.**

- Versions are not the problem. React 19.2 + React Compiler, React Router 8, Vite 8 / Rolldown, TypeScript 6, TanStack Query 5, nine production dependencies, zero `any`, zero literal colours in component CSS. Only minor bumps are available (React 19.3, Router 8.4, Query 5.103).
- Adoption is the problem. Zero hits in `frontend/src` for `ViewTransition`, `useTransition`, `useOptimistic`, `Activity`, `useMutation`, `color-scheme`, `@layer`, `@starting-style`, anchor positioning, or any error boundary. A React 19 app that behaves like a React 17 app.
- Craft is the other problem. At the contracted 1440x900 viewport the Lead Queue hides Score and Approve off-screen, the Console clips its own controls, native inputs render white in the dark theme, and the approval gate sits below the fold.
- Overall grade against a Linear / Stripe / Hex bar: **C+ to B-**. Auditors gave 5 x B-, 10 x C+, 1 x C. The adversarial verifiers judged about half of those a notch harsh, and none generous. Governance, honesty-of-state, static delivery, and token discipline are genuinely A-grade and are the reason this is fixable quickly.

## 2. Method and caveats

- 39 agents: fixture-rendered screenshots of every route (104 PNGs, 1440x900, dark and light), 16 dimension auditors, one adversarial verifier per dimension, a coverage critic, and a three-lens proposal panel with a feasibility judge. Read-only; nothing in the repo was changed.
- 209 verified findings. Verifiers refuted none outright but **corrected 153 of 172** (narrowed claims, downgraded impact, raised effort, flagged constraint conflicts). Auditor effort estimates were systematically optimistic; the register carries the corrected ones.
- The audit read branch `claude/genie-guard-literals`, 25 commits behind `origin/main`. The file-size split pass has already landed on `main` (`components.css` is 14 partials, `api.ts` 112 lines, `LeadTable.tsx` 567, `USChoroplethMap.tsx` 677), so monolith-size findings are already resolved and are omitted here. Five headline defects were re-verified directly against `origin/main` after the run: no error boundary, no `color-scheme`, the lead-queue query key, the Console grid rule, and the immutable header on asset 404s. All five reproduce on `main`.
- Not done: no live app session, no real screen reader, no device profiling. The built-in browser and Playwright MCP were unavailable, so visuals come from a production build with route-intercepted fixtures. No Genie answer state and no Analytics sub-tab other than Executive was captured. Performance impacts are unprofiled unless stated.

## 3. Scorecard

| Dimension | Grade | Verifier's view | The one-line reason |
|---|---|---|---|
| Technology stack and build-vs-buy | B- | fair | Current toolchain, almost none of its capabilities used; no error boundary |
| Bundle and asset delivery | B- | B is fairer | Best-practice static serving; unconfigured build, no vendor chunks, no hints |
| Runtime rendering and state | C+ | B- defensible | Compiler emits nothing for every heavy screen; wrong-cohort cache key |
| Design system and modern CSS | B- | fair | Disciplined 2023-era CSS; theme x accent matrix ships illegible combos |
| Visual design and prototype parity | B- | fair, slightly generous | Strong foundation; main work surfaces fail at 1440x900 |
| Shell, navigation, routing | C+ | fair | No boundary, clipped Console, static title, no scroll handling, no wayfinding |
| Lead Queue and tables | C+ | fair | Approve off-screen, ~5 rows visible, client-only sort on a 500-row window |
| Data visualization and geography | C+ | fair | Hero map becomes a tile grid on drill; ramp and legend fail encoding checks |
| Genie and agent UX | C+ | fair | A-grade governance; turn dies on close; 90-200 s behind "Answer ready" |
| Accessibility | C+ | fair | Several Level A failures; no a11y gate on PRs; no ACR/VPAT |
| Loading, error, degraded states | C+ | fair | Excellent 503 protocol; no boundaries, session-expiry, offline, or toasts |
| Workflow and product strategy | C+ | B- is fairer | Approval can be blind; Home does not answer its own question |
| Motion and micro-interactions | C+ | B- is closer | Sound restraint; every overlay exit is a hard cut; one `:active` rule |
| Responsive, theming, formatting | C+ | B- is fairer | Good container queries; 5 of 8 theme x accent combos fail contrast |
| Frontend quality infrastructure | C | C+ is fairer | Zero screenshot assertions; 3 of ~138 Playwright tests run on PRs |
| API-to-UI delivery | B- | B is fairer | Strong resilience; hard-expiry caches, no persisted cache, polling Genie |

## 4. What is already best in class (do not regress)

- **Supply chain and types**: 9 production dependencies, exact pins, integrity hashes, zero `any`, OpenAPI wire-contract gate, mock-leak lint bans.
- **Static delivery**: build-time brotli-11 / gzip-9, immutable hashed assets, strict CSP, every route lazy with idle and hover preloading.
- **Resilience protocol**: typed 503 reasons end to end, warm-up block, single-flight, stale-if-error, debounced visibility-aware health polling, double-submit protection on writes.
- **Governance UX**: progress rail driven by real backend stage events, honest capability labelling, cards never state a number they have not read, approval write path hardened and idempotent.
- **Design discipline**: prototype BEM vocabulary essentially complete, zero literal colours in components with a lint gate, named container queries backed by an 8-anchor viewport matrix, end-to-end reduced-motion coverage, compositor-only keyframes (16 of 19).
- **Specific surfaces**: command palette and evidence drawer are at the bar; Borrower 360 "The story" is a best-in-class explain moment; the lead table's ARIA semantics and virtualisation with expandable rows are correct; the equity scatter is server-binned and privacy-safe.

## 5. Is it the latest and greatest? Layer by layer

| Layer | Today | Verdict | Action |
|---|---|---|---|
| React | 19.2.8, Compiler on | Current minus one minor. `<ViewTransition>` went stable in 19.3.0 on 2026-09-09 | Bump to 19.3 after a short soak; adopt `<ViewTransition>`, `<Activity>`, `useMutation`-backed pending UI |
| React Compiler | 1.0.0 | **Emits nothing for 25 of 98 component files**, including LeadTable, the map, GenieChat, ask-genie, offer-orchestrator, portfolio-builder (38 CompileErrors, 22 of them `try/finally`; 8 `use no memo`) | One-line hoist compiles the map today; add a logger-based coverage gate; extract `try/finally` handlers (`runtime-03`) |
| Router | 8.3.0 declarative | Fine. Data mode is optional, not required | Remove `key={pathname}` remount; minimal `createBrowserRouter([{path:'*'}])` wrapper only if `useBlocker` is wanted (`states-05`) |
| Data layer | TanStack Query 5.100 | Reads are good. **Zero `useMutation`**, 12 effect-driven fetches, no persisted cache | Migrate writes (approve stays pessimistic), move the map onto `useQuery`, add an actor-scoped persister (`runtime-06`, `delivery-05`) |
| Build | Vite 8, no `build` section | Unconfigured | Vendor chunk groups, non-dot manifest, closure-based brotli budget, per-route CSS (`bundle-03`, `bundle-08`) |
| TypeScript | 6.0.3 | Strict ceiling is free: 0 errors under seven extra flags; `noUncheckedIndexedAccess` is 96 production sites. 9,492 lines of Playwright specs are never type-checked | `stack-07` |
| CSS platform | `color-mix(in oklab)`, container queries, `:has()` | 2023-era. Missing `color-scheme`, `@layer`, `light-dark()`, `@starting-style`, anchor positioning, view transitions, `forced-colors`, subgrid, `text-wrap` | `css-02`, `css-05`, `css-06`, `visual-04`; all Baseline except `text-wrap: pretty` (no Firefox) |
| UI primitives | Hand-rolled | **Wrong call for listboxes and dialogs**: five divergent listboxes, three silent to screen readers; six dialogs on a keydown trap; ~80 native `title` tooltips | Extract the repo's one correct listbox into `components/ui`, or Base UI 1.8 (owner decision, presses the initial-JS budget); native `<dialog>` for the five modal surfaces, never the Genie panel |
| Charts | Hand-rolled SVG | **Right call**, but no shared foundation: non-round ticks, three tooltip idioms, Genie line chart has no axis | One `ChartFrame` + `d3-array` ticks / `d3-shape` only. No chart library, no `d3-scale` |
| Map | Hand-rolled SVG, 51 polygons | Right technology, wrong depth | Committed per-state ZCTA TopoJSON with viewBox pan/zoom. **MapLibre + PMTiles is infeasible**: 10 MB per-file Apps limit and the CSP blocks blob workers |
| Fonts | 7 static Geist files, no preload | Dated | `@fontsource-variable` (2 files), preload, metric-matched fallback face (`bundle-05`) |
| Web Vitals | Hand-rolled observers | Not Core Web Vitals; RUM ships disabled; nothing aggregates or alerts | `web-vitals` 6.x attribution build through the existing sanitised beacon (`stack-08`, `quality-07`) |
| API types | ~1,750 hand-written lines | **Already drifted** (null LTV renders `null%`; approver capability never reaches the UI) although an OpenAPI baseline is committed | Generate from `tests/fixtures/openapi_baseline.json`; CI regenerate-and-diff (`quality-04`) |
| Test stack | happy-dom + hand-rolled `createRoot/act` in 67 files | Cannot exercise native dialog, focus, or transitions below Playwright | Vitest browser mode on the installed Playwright; Testing Library for new tests |
| Dependencies | 19 of 26 packages behind (minor); 2 moderate advisories; no update automation | The "zero vulnerabilities" doc claim is now false | Bump vitest now; batch the rest on one human branch; scheduled `npm outdated` + `npm audit` report |

## 6. Fix first: defects on the trust path

These are the items a sceptical buyer or a compliance reviewer hits. Most are small.

| # | Defect | Ids | Effort |
|---|---|---|---|
| 1 | **No error boundary anywhere.** Any render throw, or a lazy chunk 404 in a tab left open across a deploy, unmounts the root to a white page. `React.lazy` caches the rejection, and the 404 itself is stamped `immutable` for a year, so a rollback stays broken. Found independently by five auditors; reproduced in a browser. | `stack-01` `bundle-01` `shell-01` `states-01` `quality-01` | S then M |
| 2 | **Lead Queue serves the wrong cohort.** The query key omits `cities` while the fetcher sends it, and `useWarmingUpRetry` ignores `deps` once a key is passed. Chicago then Springfield from a Genie link shows Chicago rows under a Springfield chip for 30 s. | `runtime-02` `tables-v1` | S |
| 3 | **Pressing `A` approves from anywhere.** Window-level hotkey with no focus scope; with a row expanded it fires from a filter button, the drawer, or Genie chrome, and writes an audit row. WCAG 2.1.4 Level A. | `tables-v2` `a11y-09` | S |
| 4 | **Approval can be blind.** Row, hotkey and bulk approve certify outreach copy the approver never saw; no receipt, no path to the audit row. | `flow-03` `states-06` | M-L, owner decision on friction |
| 5 | **Approve UI ignores `can_approve`** and never shows whose name the audit row will carry; non-approvers learn from a 403. | `flow-02` `shell-06` | S |
| 6 | **Score, Signal and Approve are off-screen at 1440x900.** 15 columns sum to 1,564 px in a 1,318 px wrap; the sticky pin was deleted; 3-5 rows visible because 16 filters and a hero push the table to y=589. | `visual-01` `tables-01` `tables-04` `flow-01` | L |
| 7 | **Approval screen clips the copy being certified** to ~24 characters in an unstyled white native input; the approval gate and LO routing sit 480 px below the fold. | `critic-02` `visual-v1` | S then M |
| 8 | **Console rail is clipped at 1440x900.** `.tweaks__body` grid lacks `minmax(0,1fr)`, rows render 432 px wide in a 300 px panel, and `.tweak-row label` leaks into the property-lookup form. | `visual-02` `shell-02` `responsive-01` | S |
| 9 | **No `color-scheme`.** White 24 px checkboxes down the dark queue, white selects and scrollbars. First paint flashes the wrong theme; no OS-preference option. | `visual-03` `css-02` `responsive-03` | S |
| 10 | **Numbers that disagree.** Home "Approval queue" quotes the whole-book screen but its CTA opens a contactable-only queue (about 23x apart on live gold); the funnel Sankey prints "conversion" between non-nested population cuts; `$-4.41M` and `$1000K` from parallel formatters; `null%` LTV. | `flow-v1` `dataviz-v1` `responsive-04` `quality-04` | S each, L to consolidate formatters |
| 11 | **Closing Genie, pressing Esc anywhere, or navigating kills the in-flight turn** and the question. Turns take 30-200 s. | `runtime-01` `genie-02` `runtime-v2` | M-L |
| 12 | **Deep research waits 90-200 s behind a rail that says "Answer ready"**, inside one blocking POST; overruns become a gateway 504. | `genie-01` | S relabel, XL job-ification |
| 13 | **Contrast failures users can select.** Light focus ring 1.91:1; light + teal chip text 1.15:1; dark + navy links 2.06:1; 5 of 8 theme x accent combinations fail. | `css-01` `css-v1` `a11y-01` `responsive-02` | S-M |
| 14 | **Keyboard and screen-reader blocks at Level A.** Portfolio Builder state picker is mouse-only; three filter listboxes are silent; one static `document.title`; one heading per page; hero map is 51 tab stops with data in a mouse-only tooltip. | `a11y-v1` `a11y-02` `a11y-03` `a11y-04` | S to L |
| 15 | **Evidence drawer's primary action 403s for non-admin buyers**, and source freshness is hidden from them. | `critic-03` | S then M |
| 16 | **CSV export writes no audit event**, ignores selection and sort, and can announce "500 leads" while writing zero rows. | `tables-08` | M |
| 17 | **Session expiry and offline are nearly silent**: "Failed to fetch" beside a useless Retry, or perpetual skeletons. Capture the real Databricks Apps expired-session response before coding. | `states-02` `shell-v1` `critic-v2` | M |
| 18 | **The degraded banner promises recovery only Home delivers**; other routes stay red until someone clicks Retry. Lead Queue also renders "Showing 0 ranked borrowers" during warm-up. | `states-03` `states-v1` | M, S |

## 7. Upgrades by theme

### 7.1 The Lead Queue, as a best-in-class grid

- Keep the prototype column order. Merge the four app-added workflow columns (444 px) into one Status cell, pin Approval with `position: sticky; inset-inline-end: 0`, return to one-line rows at `--row-h` with DNC / Suppressed / Owner-unresolved chips kept in-row as compliance signals. Add `ApprovalBanner` to the row preview (prototype parity).
- Active-row cursor with J/K, Enter, X, A/R acting on the cursor row then advancing; bound only while focus is inside `.tbl-wrap`; `?` sheet; per-user off switch (`tables-03`, `wow-power-4`).
- Filters: repair `FilterSelect` now (option ids, `aria-activedescendant`, Home/End, typeahead, flip); then core pills plus a "More filters" popover with removable chips, range inputs for the min-score and rate-spread filters the repository already supports, facet counts (`tables-06`).
- Say "sorted within the loaded 500" today; then whitelisted server sort with a keyset cursor (`tables-02`, `delivery-02`).
- Column presets per persona, saved views as name + URL params through the existing workspace API, shift-range and select-all-matching, audited export (`tables-05`, `tables-07`, `tables-09`, `flow-08`).
- Do **not** retrofit TanStack Table into the virtualised table; CSS and plain state make it unnecessary.

### 7.2 Navigation that keeps the user's place

- `useMainScroll` keyed on `location.key` (reset on PUSH, restore on POP, `scrollIntoView` for hashes), per-route `document.title` set in an effect (React's hoisted `<title>` loses to the static tag in `index.html`), focus the `h1` and announce the route (`shell-03`, `a11y-03`).
- Linked breadcrumbs with queue context, "3 of 23" pager with J/K, advance to the next lead after a decision (`shell-04`).
- Preloaded routes still flash the skeleton: render resolved modules synchronously, or drop the keyed remount so the router's `startTransition` holds the previous screen (`shell-v2`).
- Sort, expanded row and map drill into typed search params, extending the `lead-queue.filters.ts` pattern (`shell-03`, `dataviz-04`, `flow-09`).
- Identity menu with role chips; a real 403 surface; a shell toast region (`role=status`, undo-capable) replacing five ad hoc feedback patterns (`shell-06`, `shell-09`, `states-07`).

### 7.3 Genie at the 2026 bar

- Mount the panel once and never unmount it; own the in-flight turn in a module store; FAB progress ring and "answer ready" badge; one shared Escape layer stack (`runtime-01`, `genie-02`).
- Stop, Retry, up-arrow recall, Edit-and-resend (client-only first); Regenerate as a new Genie turn (`genie-03`).
- Page context, phase 1 frontend-only: `openGenie({prompt})` prefilled from curated per-route templates in reviewed vocabulary, so the prompt of record is exactly what the guard scans. "Ask Genie about this" on KPIs, segment cards, map regions, lead rows (`genie-04`).
- Refusal card with a coarse reason enum, pre-validated rephrase chips, and hash-only "this was legitimate" reporting (`genie-05`).
- Disclose hidden columns by name, Copy SQL, Copy answer, show-all, audited CSV (`genie-06`). Axes, units, hover and drill-through on Genie charts via the existing `genieCellHref` (`dataviz-05`).
- Turn completion into a server-side job behind the existing poll with server-owned stage events; reveal a section only after it passes its own output-policy check. **Never stream unverified tokens** (`genie-01`).
- Make `/ask-genie` conversation-first with page tabs Ask | Workflows | Saved monitors; the composer is currently at y=1273 (`visual-07`).
- Extend the hand parser for ordered lists, headings and pipe tables (about 60 lines, zero bytes added) rather than adopting a markdown library, and only after capturing live turns (`stack-02`).

### 7.4 Geography, as the hero it is declared to be

- Fix encoding now: equal OKLab steps across the four classes, legend swatch matching the painted class, a data-safe light-theme accent, real break values on the legend, continuous sqrt scale below about 12 populated units, state labels. Record it as an accessibility deviation from the prototype ramp (`dataviz-02`, `responsive-05`).
- Make the map controlled and deep-linkable, move rollups onto `useWarmingUpRetry` with a shared cache, show an em dash rather than a confident zero (`dataviz-04`).
- Replace the ZIP tile grid with committed, mapshaper-simplified per-state ZCTA TopoJSON, lazy-fetched on drill, viewBox fitted to populated ZCTAs, pan and zoom on the SVG. Tiles stay as the accessible list view. Budget 10-25 MB of repo weight (owner decision) (`dataviz-01`, `visual-09`).
- Data-rich `aria-label`s, tooltip on focus, roving tabindex, "View as table" (`a11y-04`).

### 7.5 Feels instant

- Vendor chunk groups so an app edit stops re-downloading about 70 KiB br of unchanged vendor code; prove it with a two-build hash diff (`bundle-03`).
- A tiny hashed boot module that starts session / options / footprint / health fetches before the route chunk arrives (not `preload as=fetch`: Firefox would double-fetch). Never prime audit-writing reads (`bundle-02`, `delivery-03`).
- Variable fonts with preload and a metric-matched fallback; import the 2048x214 wordmark PNG through Vite as an SVG or WebP (`bundle-05`, `bundle-v1`).
- Server stale-while-revalidate for hot gold aggregates on a dedicated refresh executor, keyed by snapshot id; keep Lakebase-derived counts out of the long-lived value (`delivery-06`).
- Actor-scoped persisted query cache for instant revisits, with PII care (`delivery-05`). A routine serverless resume must not read as an outage (`delivery-01`); the health poll's `SELECT 1` must not block warehouse auto-stop (`delivery-v1`).
- Narrow selector hooks over the 34-field `AppContext` for the per-row consumers; ref + rAF for Genie drag and map hover; profile before anything larger (`runtime-05`, `runtime-v1`, `runtime-07`).
- `Server-Timing` plus `web-vitals` attribution so a slow paint can be attributed to cache, warehouse or Lakebase (`delivery-v3`, `runtime-09`).

### 7.6 Visual craft and motion

- Home: KPI values at `--fs-36` (they are currently smaller than the H1), the missing gap between the login summary and the KPI row, the map paired with a side panel above the fold as the prototype does, one primary CTA, and an answer band that actually says WHO (top five borrowers), WHY NOW (leading triggers), WHAT OFFER (offer-mix bar) (`visual-06`, `flow-05`).
- One bordered Geist Mono chip style currently does five jobs. Give the app-added route nav and Analytics tabs their own treatment, render null workflow states as muted text, fix the underlined nav links; leave prototype-contract `.chip` and `.filter__value` alone (`visual-05`).
- Segment cards are 432 px against the prototype's ~195 px: subgrid rows plus a stacked share bar per facet row, keeping one evidence trigger per row (`visual-04`).
- Motion: working exits (`visibility` in the transition list for drawer and Genie; `usePresence` for unmounted overlays), pressed states (there is exactly one `:active` rule), the undefined `--ease-standard` token, an ease-in exit token at about 0.6x enter, sparkline draw that actually takes its 700 ms (`motion-01`, `motion-02`, `motion-04`, `motion-07`).
- View Transitions phase 1 after the 19.3 bump: wrap the routed subtree, hoist Suspense above the keyed element, keep `route-in` as the fallback, wrap `setTheme` in `startViewTransition`. Phase 2: lead-row borrower id to the Borrower 360 title, segment card to segment header; names assigned at click time because rows are virtualised; under 250 ms (`motion-03`, `css-10`).
- `--z-*` scale for 17 magic z-indexes, one focus-ring token consumed by one rule, `forced-colors` and `prefers-contrast` blocks, a `--chart-*` data palette independent of the user-switchable UI accent (`css-08`, `css-06`, `dataviz-09`).

### 7.7 The safety net UI excellence needs

- Promote this audit's fixture harness into `visual.fixture.spec.ts`: `toHaveScreenshot` for 11 routes x 2 themes x Console open plus drawer, palette, Genie, expanded row, degraded and empty states; Linux baselines from the pinned Playwright image. Today there are zero screenshot assertions and three orphaned May baselines (`quality-02`, `visual-10`).
- A credential-free e2e-fixture PR job. Today 3 of ~138 Playwright tests run on PRs and the live suite last passed 97 days ago, across the 256 to 396 KiB shell growth (`quality-03`).
- Axe on PRs over open-overlay states and the theme x accent matrix; a Vitest token-contrast test (`a11y-05`, `css-01`).
- A shared e2e hygiene fixture so uncaught page errors, console errors and CSP violations fail tests (`quality-v1`).
- Client exception capture through the closed, PII-screened RUM schema: error name, sanitised route and boundary id only, never the message (`quality-01`).
- Storybook is referenced in CLAUDE.md and ESLint but does not exist: build it or remove the references (`quality-06`).
- Publish a WCAG-edition VPAT 2.5 ACR once the Level A fixes land (`a11y-08`).

## 8. Over the top: signature additions

The judge's conclusion: **the differentiator is not more AI or more motion. It is making the governed arithmetic this product already performs visible and interactive.** None of the top items needs a new dependency, a framework change, or a prototype departure beyond documented BEM extensions.

| Rank | Addition | Verdict | Effort | Why it lands |
|---|---|---|---|---|
| 1 | **Score anatomy spine with a recompute seal.** Five-segment spine beside the score, fed by the existing `/proof` payload; each segment opens its evidence; seal shown only when `proof.trusted`. | build now | M | "Why is this lead a 94" answered in one glance, provably |
| 2 | **The Rate Lever.** A slider moves the 30-year par +/-100 bps; map, legend total and a plain-English headline recount from a precomputed state x scenario gold rollup built by re-running `fn_rate_spread` and `fn_in_the_money`. Fixed "Scenario, not a forecast" label. | build now | L | The demo moment: drag the rate, watch the book recount through the same UC functions |
| 3 | **Decision receipt.** After the approve write returns, read the ledger row back: evidence ids, recomputed score, offer branch, copy hash, approver, audit id, timestamp. Printable. | build now | M | The approval moment closes into the real Lakebase row |
| 4 | **Delta Explainer.** Every "since your last login" number opens a contribution waterfall. Copy says "coincided with", never "caused". | build next | M | Turns a delta into a story |
| 5 | **"What would change this?" deterministic margins.** "Clears the refi screen by 13 bps; drops out if par exceeds 5.00%." Labelled marketing prioritisation, not a credit decision. | build next | M | Counterfactuals without a model |
| 6 | **Triage Mode.** One-borrower deck in `/lead-queue?mode=triage` that shows the governed draft before `A` arms. Also fixes blind approval. | build next | L | Speed and defensibility in one surface |
| 7 | **Filter omnibox.** Press F, type `TX IL heloc unassigned aged>7`; a closed client grammar maps tokens to existing URL params with a live count. No LLM, no new guard surface. | build next | M | Power-user speed |
| 8 | **Distribution Board**, preceded by an honesty fix: `score_balanced` is written to the audit trail while allocation is a plain modulo and capacity is never read. | build next | M | Fixes a real audit-truth defect |
| 9 | **"Crossed the line".** Note rate against the weekly FRED series with the threshold-crossing week marked. Fixed-rate liens only. | build next | L | "Why now" per borrower |
| 10 | **Follow-ups Due inbox.** `callback_at` and `follow_up_at` are captured and shown nowhere. | build next | L | First real habit loop |
| 11-14 | Signal stack, keyboard grammar, "More like this", watchlist briefings on Home | build next | M-L | |
| 15 | Living Genie tiles | prototype first | L | Guard-scan cost and conversation retention unmeasured |
| - | Boardroom mode; server-side intent-bar parse | **reject** | | Sales tooling inside a customer product; a new guard surface with no protection |

Related from the dimension audits: a **"Why now" rate-window chart** on Analytics (weekly MORTGAGE30US against the book's note-rate band from a deploy-built gold table, two stacked panels, never dual-axis) (`dataviz-08`), and **clickable proof for every number** in Genie prose, starting with an "N of N figures verified" badge (`genie-10`).

Note that every bundle schedule ships paused, so anything that depends on a scheduled job (snooze wake, watchlist briefings, rate watch) is dormant on a default deploy and must say so honestly.

## 9. Sequenced roadmap

**Wave 0 — stop the bleeding (about one week, almost all S).**
Error boundaries + `vite:preloadError` one-shot reload + `no-store` on non-200 `/assets`; lead-queue query key built from the request object; scope the A/R hotkeys; `can_approve` gating and "Approving as"; Console grid and label scoping with a `scrollWidth` assertion; `color-scheme` + external `theme-boot.js`; focus ring onto `--accent-ink` and the theme x accent token overrides with a contrast test; full-width subject field; state both numbers on the Home approval banner; relabel Sankey stages as share of addressable; `null` LTV renders unknown; drawer and Genie exit transitions; `--ease-standard`; nav underline; scroll reset, per-route title and focus; shared Escape stack; keep Genie mounted and relabel the deep-research wait; gate "View asset details"; false-empty queue during warm-up; one-line map compiler hoist; bump vitest.

**Wave 1 — the money screen and the trust path (two to three weeks).**
Lead Queue layout, row cursor and keymap registry, `FilterSelect` repair and shared listbox; approve-review sheet and Decision receipt; Offer Orchestrator sticky action bar; breadcrumbs, pager and return-to-results; export audit receipt; formatter consolidation with a lint ban; segment card subgrid; Home answer band; `/ask-genie` conversation-first; Genie stop / retry / edit and prefilled page context; refusal card; map encoding fix and controlled URL state; mouse-only state picker.

**Wave 2 — feels instant, and a safety net (three to four weeks).**
Fixture VRT, axe and e2e-fixture PR jobs, hygiene fixture; OpenAPI type codegen; Compiler coverage gate and `try/finally` extraction; `useMutation` migration and the map on `useQuery`; vendor chunks, closure-based brotli budget, variable fonts, boot module; server SWR caches; persisted query cache; resume-is-not-an-outage; `web-vitals` attribution and `Server-Timing`; `@layer` and route CSS; React 19.3 and View Transitions phase 1; `usePresence`, pressed states, toast region; session-expiry surface; unsaved-changes guard; client error telemetry.

**Wave 3 — signature (four to eight weeks).**
Score spine, Rate Lever, ZCTA polygon drill, "Why now" rate window, Genie job-ification with staged sections and clickable proof, Triage Mode, Delta Explainer, filter omnibox, shared-element transitions, saved views and Follow-ups Due, ACR/VPAT.

## 10. Owner decisions this audit cannot make

1. Shared in-house listbox versus Base UI 1.8 / React Aria Components in the shell (initial-JS budget has about 5% headroom).
2. Approve-review friction: showing the draft before approve changes triage speed and approval posture. Note that prefetching drafts on row expand would write a `DRAFT_OUTREACH` audit row per expand.
3. Non-admin visibility of the audit trail and of asset freshness (moves an intentional RBAC boundary).
4. Full-cohort server export (reverses an in-code decision; widens egress beyond the 500 on-screen rows).
5. 10-25 MB of committed ZCTA geometry for polygon drill.
6. Capturing refused prompt text for false-positive triage, versus hash-only counts.
7. Dependency update signal: scheduled report versus Renovate with dashboard approval, given the no-bot-branch contract.
8. Auto-triggering the live e2e suite (Databricks meter cost), versus a release gate on the age of the last green run.
9. Data-router migration; borrower notes and @mentions (new free-text PII surface); white-label runtime theming.
10. The list of declared prototype deviations: sticky Approval and a viewport-sized table scroller, topbar identity chip, animated Console, the choropleth ramp, a "More filters" popover, shared-element motion.

## 11. Recommendations the verifiers struck down

Do not spend time on these; each was proposed by an auditor and rejected or narrowed on inspection.

- MapLibre GL + PMTiles (10 MB per-file Apps limit; CSP blocks blob workers; external tile hosts blocked by `connect-src 'self'`).
- Retrofitting `@tanstack/react-table` into the virtualised LeadTable; `d3-scale`; `sonner`; Style Dictionary / DTCG; Observable Plot as a default.
- `eslint-plugin-jsx-a11y` on ESLint 10 (peer range stops at 9, last published 2024-10).
- `showModal()` on the Genie panel (breaks the first-class non-modal contract).
- Optimistic "approved" state (the Lakebase audit row is the truth; approve stays pessimistic).
- `preload as=fetch` for API reads; the inline async-CSS `onload` trick (CSP); `content-visibility` without a profile; CSS `@property` count-up for KPIs.
- `useDeferredValue` for Lead Queue filters (they are selects to URL to server query).
- A data-router rewrite to fix a scroll bug.
- Streaming unverified Genie tokens; a server-side sentence-to-filters parse that reuses the prompt guards.
