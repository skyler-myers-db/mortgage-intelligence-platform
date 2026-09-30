# UI/UX, Strategy and Frontend Technology Audit — 2026-09-21

> **Internal validation artifact — not approved for public release.** Multi-agent read-only audit of UI/UX design, strategy and frontend technology, with adversarial verification of every finding. Screenshots referenced here were rendered from synthetic test fixtures, never live borrower data.

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

## 12. Implementation status and the wave-5 register (2026-09-29)

Waves 0 to 4b reached `main` in ten merges, each deployed to dev. This section records what shipped, what wave 4 taught, what wave 5 inherits, and which owner decisions still hold work back. Finding ids refer to the register; an item with no register id says where it came from.

### 12.1 What shipped

| Wave | PR | Main sha | Dev deployment | Headline |
|---|---|---|---|---|
| 0 | #248 | `65d15aa2` (main after #249; #248's own merge is `79b8f219`) | `01f1b6dc` | Crash-proofing, trust-path fixes, Genie survivability, a credential-free e2e harness |
| 0c + 1a | #250 | `42edd4c8` | `01f1b71b` | Theming to AA, rendered-layer proofs, decision receipt, audited export, helpful refusals, Genie controls, "why now" rate window |
| 1b | #251 | `726106a1` | `01f1b755` | Queue layout, shared listbox, Home answer band, segment cards, conversation-first Ask Genie, map encoding |
| 1c | #252 | `ba0df308` | `01f1b7d1` | Queue keyboard and approve review, wayfinding, Offer decision bar, formatting contract, session recovery, unsaved-changes guard and toasts |
| 2 | #253 | `4a967a3d` | `01f1b867` | Build currency and closure budgets, error boundaries and client-error telemetry, warehouse delivery, queue writes on `useMutation`, one Genie turn, CSS hygiene, rendered-layer safety net |
| 3 | #254 | `443fe7d1` | `01f1b8a3` | Score anatomy, Rate Lever, Genie jobs and reading, the queue keeps its place, motion and navigation, fixture contract |
| Rate Lever fix | #255 | `a4b6e04d` | `01f1b8b4` | The Rate Lever reads gold only; warehouse permission refusals fail fast |
| 4 pre-cut | #256 | `fd74aa44` | `01f1b8cb` | Required query keys, real surface headings, one navigation order |
| 4a | #257 | `4df16327` | `01f1b92e` | Playwright 1.63 and an a11y lint ratchet, boot module and identity boundary, native dialogs, error surfaces, Growth Agent plan trust, audited Genie Stop |
| 4b | #258 | `f3474739` | `01f1bc6d` | Lead Queue render cost, row approval banner and focus clearance; Offer and Portfolio on the query layer with a queue pager; a compiled chart kit with 1-2-5 axes and histograms; Genie turns that survive before the first open, and a conversation deep link; missing tables fail fast; legacy specs typechecked, one axe entry point |

### 12.2 Wave 4 outcomes worth knowing

- **Bytes set the cut line, and both batches still went over their caps.** Every frontend lane ran against per-lane byte caps with a fixed cut order; 12.3 names each cut. In wave 4a, error-surfaces added +12.5 KiB br of total JS against a +3.0 cap (its never-cut items alone came to about 9) and overlays +3.44 against +2.0; #257 re-baselined both with per-lane attribution. In wave 4b, w4-workflow added +8.65 against +2.0, mostly React Compiler memo code for the newly compiled Offer Orchestrator and Portfolio Builder routes plus shared mutation and pager chunks (its fix-round cuts took back 0.72), and w4-genie-client added +4.6 against +2.7. The integrator re-baselines the moved route gates at measured + ~5% with per-lane attribution rather than cut shipped behaviour. One lever remains: loading QueuePager only when a queue context exists would take about 1.25 KiB br off the Offer and Borrower 360 closures.
- **The perf gate caught what reviews could not.** Wave 4b's first CI run put the cold Lead Queue at LCP 2960 ms and TBT 1717 ms. The W4a run had measured 1568 and 506.
  - A build of every merge step, then `git bisect run` inside the guilty lane, named the a11y-v2 focus clearance.
  - Its hook wrote the measured thead and pin sizes as ordinary custom properties on the table scroller, and each write re-styled every row.
  - An unregistered custom property derived from them computes to its unresolved text, so it "changed" even when the clearance did not.
  - `@property` registration, a declared pin width (the table is `table-layout: fixed`) and a first measurement in the next frame took the local numbers back to the pre-lane level (TBT 890 → 443 ms at 4x CPU).
  - focus-clearance-cost.fixture.spec.ts now pins it by re-styled element count (1 against 1,744).
  - The same PR fixed a histogram rule label that scrolled its surface sideways with the Console open, found by the VRT surface-overflow check. The label now measures which side of its rule it fits on.
- **Compiler coverage grew, with two exceptions.** Offer Orchestrator, Portfolio Builder, HealthProvider, PropertyLookupPanel, useMainScroll, useGenieWindow and the new chart kit now compile. The LeadTable shell does not: compiled clean it measured 38.44 KiB br against its 38 KiB chunk gate, so it keeps `'use no memo'` with targeted manual memoization. The seven Analytics route modules also keep their `'use no memo'` budget pragmas (`stack-09` item 6 below).
- **Playwright 1.63 found a real Chromium 153 defect; the other gates held.** Chromium 153 rejects an in-flight fetch at the start of the `pagehide` dispatch, before later listeners run, so a reload during a Genie turn recorded a false "could not be reached" failure and never resumed. Wave 4a defers that decision by one macrotask (`lib/genieInFlightTurn.ts:369`); both fixture specs pass without being loosened. Elsewhere in CI, a digest-pinned `postgres:16.15-bookworm` service runs the real-SQL contracts serially; its first run exposed one test-data bug, since fixed, and `KNOWN_UNRUN_DSN_SUITES` is empty. The perf budget stayed gating under Chromium 153 (the documented `continue-on-error` fallback was never armed), and the new Lead Queue interaction budget is report-only (`runtime-09`). oxlint 1.85 runs the jsx-a11y rules against a per-file baseline that can only shrink; `prefer-tag-over-role` is off because its remedy contradicts prototype markup.
- **Genie Stop is app-side.** `POST /api/genie/message/cancel` is audited (`GENIE_TURN_CANCELLED`) and enforced by a database commit point, but Genie's own Conversation may keep the question as context, and the copy says so. A cancelled turn keeps the submit's RUN_GENIE row, so count `metadata->>'action' = 'genie.run_query'`, not `event_type` (docs/observability.md). After a rollback, a status poll of an already-cancelled job returns 500, because the old status enum has no cancelled value.
- **Warehouse behaviour an operator will see.** A missing UC table or view now answers 503 `retries_exhausted` after one attempt. A cold source-readiness failure is no longer negative-cached. The state footprint may be served stale between 240 s and 300 s; the oldest value a Genie guard can see is still 300 s. The lifecycle job ships its 04:00 schedule PAUSED, and the workflow generation moves only for job-mode runs the App submitted and this process observes; scheduled or Jobs-UI runs trail by one soft TTL, and a run not observed to finish within an hour expires with `lifecycle_job_watch_expired`, after which its counts also trail by one soft TTL.

### 12.3 Wave-5 register

What wave 4 cut, deferred or left unowned, checked against the wave-4b tree with all six lanes merged on 2026-09-29. Items wave 4b closes are not listed.

**Lead Queue**
- `tables-07` bulk Reject, the whole item: the reject gate with one shared reason and rationale, the `bulk_rejection` status, kind-aware progress copy (the bulk fallback still says "Approving") and the `outreach.ts` signal; `BulkRunKind` is `'approve'` only. Budget cut 3: the lane was already over every cap before bulk Reject was built (LeadTable chunk 37.63-37.72 KiB br against a 37.6 cap). Grouping bulk rejects under a `bulk_id` needs a backend field OutreachRejectRequest does not have; do it if compliance asks.
- `critic-06` item (d), the compact lifecycle stepper: built, then removed with its CSS and tests by budget cut 2. The verb-first labels, the confirm row and Cancel/Esc shipped.
- `runtime-04` slice 3(b) and the `runtime-03` LeadTable family: the shell stays uncompiled (cut 5, forced by the chunk gate; 12.2). AssignmentLifecycleAdvance, useLeadTableCursor, useLeadTableHotkeys and useLeadApproveReview keep `'use no memo'` as budget trades (cut 4), useLeadCsvExport and LeadBulkApproveReview stay at their base code, and useLeadTableKeyboardFlow keeps its pragma (compiling it cost +1.35 KiB br on the chunk). None of them gains anything while the shell is uncompiled; compiling them, like `tables-05`'s optional LeadTableHeader/Footer split, needs chunk headroom first.
- `a11y-v2` residuals after wave 4b's clearance, which is the one-line nav height (57 px at 1440x900) plus the focus ring. A nav that wraps on a narrow screen is under-cleared: measuring it needs code in `RouteNav.tsx`, which is initial JS, against the lane's +0.1 KiB cap, so the brief deferred it. The sticky header's sort and select-all controls carry no margin: with one, focusing a control while the table was scrolled moved the table back 45 px to "reveal" a header already stuck in view and still left the control under the nav (measured), so a header control focused while `.main` has scrolled the header under the nav stays there. Raised in the third review and not taken: the margin also reaches nested scrollers inside `.main` (the map tile grid, `.lead-queue-scope`, the filter row at 640 px or narrower, the approve-review dialog), where Shift+Tab over-scrolls them by up to 61 px.

**Offer Orchestrator and Portfolio Builder**
- `critic-v3` per-actor campaign draft restore, and `critic-04`'s $ and % adornments, read-only copy fields as readouts and the `:read-only`/`:disabled`/`:user-invalid` styles: built, then removed by budget cuts 1-2 in the workflow fix round. Field stays on the five numeric editors with the announced clamp. Rolling Field out to other native controls touches other lanes' files and waits too.
- `runtime-06`: Offer reads the roster under its own `queryKeys.salesRoster()`, while Lead Queue and Sales ops cache a loan-officer-only list under `queryKeys.salesTeam()`. One shared key needs those two routes, which were outside the lane.
- `shell-04` / `flow-09` follow-up, raised in the workflow lane's approving review after its fix round: from an offer opened through "Open bound offer", J/K, the pager buttons and "Next in queue" go to `offerPath(next)` without `?campaign_id=&variant_name=`, so the next approval is not bound to the campaign. Carry the verified binding through `pathFor` and the Next link, or hide both on bound offers.

**Growth Agent and Genie**
- `critic-01` residual: the step diff on recompose (kept, added and removed rows over tool and canonical-parameter signatures), plus the compose pending copy, changed pairing and scope-changed note. Cut for the `/ask-genie` closure: the full lane measured 63.58 KiB br against the 63 gate, the diff about 0.64. The legacy `execute: true` on `POST /agent/compose` also remains, for about 15 refusal-battery tests. The approving review's follow-ups were not taken: Run uses native `disabled`, so focus drops to `<body>` (move it to "Compose again" on a 409 and to the execution trace on success); editing the objective while a Run is pending hides that run's result although the server still executes it and writes its audit rows (disable the objective while pending); and the command-bar hint sits under the objective, not the buttons, a fix in another lane's 06-genie-chat.css.
- `genie-09` parts 1, 3 and 4, with `flow-08` slice 2: "Save reviewed watchlist" stays on the command bar, because moving it to the result card would re-plan through `/agent/run` and could save a workflow the card does not show; the Workflows tab has no run-history list, although `GET /api/growth-agent/runs` exists (outside the W4a lane's binding scope, parts 1-2 only); step-by-step progress needs server step events that growth runs do not emit. The Workflows tab also still hard-codes " · scheduler paused" (ask-genie.saved-monitors.tsx:42) instead of a `scheduler_state` read from the monitor job; the plan routed it to the same lane, and the job read touches databricks_jobs.py.
- `genie-01` phase 1b, verified sections revealed as each passes its own checks (SSE optional): planned with `genie-10` in a Genie verified reveal lane; SSE waits on the `delivery-04` ingress spike. Wave 4's one Lakebase migration went to the cancel and added only the cancel, record and deep columns.
- `genie-03` Stop residuals: cancelling Genie's own Conversation message (nobody has verified the API supports it); a Stop before the 202 names the job sends no cancel (needs a turn-keyed pre-cancelled row); sweep sub-turns already running on pool threads cannot be interrupted (restructure the sweep); the `ended` outcome cannot tell a job that recorded and then failed from one that never recorded, and "recorded" is returned once `recorded_at` is set, before the RUN_GENIE and session rows exist, so a failed tail makes "Find it in History" wrong (a fourth wire outcome, or an owner copy ruling). Live proof, 2026-09-29 on dev deployment `01f1b92e`: a Stop while a real Genie turn was running answered `cancelled`, a repeat was a no-op, one `GENIE_TURN_CANCELLED` row was written, and the job ended `cancelled` 19 s later with no answer recorded. A wrong question label was refused with 400, and a body carrying the question with 422. Still not run: a live Stop AFTER the audit write (the `recorded` outcome) and the APP_ENV=local capture of one single and one deep turn from the wave-4a operator gates.
- `genie-08` item 1, the section outline for sweeps of four or more sections: the charts brief lists it as a wave-5 remainder (plan item 12, the last cut), since it needs the `/ask-genie` route budget and GenieAnswerReading.css; genie-client had marked it as the charts lane's, so it fell between the two.
- `shell-03` deep-link remainder: the URL does not follow a settled turn that moves to a new conversation, nor a History load from the floating panel while `/ask-genie/<id>` is mounted, and the link cache can go stale after a follow-up and Back. Needs a conversation-switch signal. From the approving review: the link state's Retry has no pending style while `aria-disabled` and a failed retry announces nothing (it needs CSS the lane could not change), and a vote that fails after GenieAnswerFeedback remounts leaves enabled buttons with no error line (store the failure copy in genieAnswerMemory, as the export latch does).
- `genie-04` phase 2 / `states-v2`: an empty queue prefills Ask Genie only for one state and/or one segment, because only reviewed templates may prefill. Intersections, counties, ZIPs, cities, lenders and assignees wait for the closed, battery-tested context model.

**Charts and geography**
- `dataviz-07` ChartTooltip on the equity scatter (cut a) and `dataviz-06` scatter threshold guides, legend line and `.analytics-scatter__guide` stroke on `--text-3` (cut b): cut for the analytics route closure (46 KiB gate). The per-tab lazy step would move about 3 KiB br out of it, but adds 1.1-1.2 KiB br of total JS and needs a `budgets.routes` entry the lane could not add.
- `dataviz-10` map deltas (Escape up one level; roving over populated units only) and `css-03`'s `.map-tip` popover re-parent: no wave-4 lane owned the map files, which go with `runtime-07` (12.4).
- Also outside the charts brief's cut line: the Genie half of `dataviz-05` / `stack-06` (Genie line and bar charts still have no y axis, gridlines, hover readout or drill-through); the rate window onto ChartFrame, with the `.analytics-chart*` chrome moving to the lazy kit sheet (`bundle-06` / `stack-v3`, the wave-5 CSS cascade; it already has a table toggle and nice ticks); `motion-08` slice 2, a loading Button in the overlays lane's files, and slice 3, a value cross-fade that waits on `motion-03`; `css-06` item 4, dropping `border-radius` from the global `:focus-visible` rule (tokens.css:542), a non-additive tokens.css edit for the CSS cascade lane; and data-change View Transitions (`dataviz-07`), which must never start on same-pathname search navigations.
- Fix-round leftovers: a mouse click leaves the index-0 tip visible after pointerleave (`dataviz-07`; the brief specifies that focus shows index 0, and gating it on `:focus-visible` changes that), and a U+2212 minus sign belongs in `lib/formatters` for every surface, not in one chart summary (`responsive-04`; outside the lane's files).

**Overlays, palette and evidence**
- `motion-01` item 2e, presence exits for FilterSelect, MultiFilterSelect, GenieHistoryMenu, IdentityMenu and the sort menu: cut for the overlays lane's total-JS cap (+3.44 KiB br against +2.0). The drawer recovery frame, shortcut sheet and offer mock also still close at once, because their parents, in other lanes' files, unmount them.
- Also cut for the overlays cap: `responsive-07` item 8b (EvidenceHoverCard.tsx:215 and the EvidenceChip aria-label still print the raw `updatedAt` string, not `formatRelative`/`formatDate`), `shell-07` item 4 (hide `.cmdk__empty` while a borrower search runs or has failed) and `bundle-09` item 2 (preload the palette's active row).
- `bundle-04` item 3, a slim evidence-registry index in the shell with the prose (3.92 KiB br) behind the lazy drawer body: EvidenceHoverCard and four dynamic-source builders read registry prose synchronously, so a key-based lookup needs a registry-key field on DrawerSource in AppContext.tsx, another lane's file. Planned with `flow-06`, which rewrites the drawer's content model.
- `critic-08` remainder: a Tooltip primitive generalized from EvidenceHoverCard, the Topbar/Chip/Button `title=` migrations and a `title=` lint ban. The compliance text shipped in the lead-queue lane; the rest is outside its files, and the ban edits eslint.config.js and would flag every lane's files, so it needs a quiet tree.

**Loading, error and freshness states**
- `states-08` item 3 / `states-07`: the 429 countdown covers query surfaces only; approve, reject, assign and bulk-review failures still toast without it. W4a error-surfaces handed the mutation surfaces to W4b lead-queue, which deferred them to wave 5 under the integrator's correction after the W4b batch checker found the hand-off unrecorded.
- `states-09`: FetchedAt runs on Lead Queue and Segments, and the queue version on Lead Queue only, by design. The error-surfaces brief deferred Home, and Analytics was handed to the charts lane, which did not take it. Outreach delivery status and CRM-imported `lead_outcomes` reach the queue through the lifecycle sync job, not the decision ledgers, so the version misses them (a Sales loop item); the `approvals.decided_at` index needs a Lakebase migration, and wave 4's only slot went to `genie-03`'s cancel. False positives remain: an LO's own lifecycle advance or outcome raises "Queue updated", because AssignmentLifecycleAdvance still writes through raw `api` calls that the MutationCache own-write store never sees; a keyed `useMutation` fixes it (handed to W4b lead-queue, not taken). From error-surfaces' approving review: after a remount, a cached pre-write version (gcTime 5 min) can also become the baseline, which leaves a lasting pill for an own write made more than 90 s earlier; ignore readings requested before mount.
- `states-04` / `states-03` / `states-v2`, the AsyncState sweep: Asset (cut 7, its closure at 7.67 of 8 KiB br), Analytics, Portfolio and Offer keep their own states; the last three were the charts and workflow lanes' files. Raw `err.message` still reaches buyers at offer-orchestrator.tsx:51 and :55, portfolio-builder.tsx:209 and AppContext.tsx:322, 406, 428, 459 and 483. AsyncStatus's pending placeholder offers no Retry (+0.1 KiB br, cut for Home's cap).

**Delivery, resilience and field vitals**
- New finding, no register id: `backend/services/admin_rules.py` falls back to `DESCRIBE DETAIL` and `COUNT(*)` probes of `mip.silver.*` and `mip.first_party.*` (descriptors at :66-136, fallback from :338) when `mip.gold.source_readiness` is absent or partial. That breaks the rule that the App reads only gold, ref and audit schemas. `test_app_sql_reads_gold_only.py` misses it: the descriptors carry no `FROM`/`JOIN`, the probe statements interpolate a runtime table name, and the test's ETL-only set is silver and raw, so `first_party` is not checked at all. Seen by the delivery-warehouse lane outside its scope; registered by the integrator.
- `delivery-06` remainder, out of the delivery-warehouse lane's scope: the segment list stays on hard expiry (needs an owner ruling: `test_geo_repository.py` pins that an expired cache must not mask a refresh failure); cache keys carry no gold snapshot id; lifecycle-run observation is per process; only `TABLE_OR_VIEW_NOT_FOUND` fails fast, not `SCHEMA_NOT_FOUND` or `UNRESOLVED_ROUTINE`; and a dedicated non-retryable 503 kind for a missing object would change the client's 503 vocabulary, so `retries_exhausted` stands. The lane's approving review adds that the footprint options in `backend/api/config.py` (:74, :161-163) and the Genie footprint guards (`genie_prompt_guardrails.py:301`) still read the fallback flag and the rows in separate calls, so a background refresh between them can pair a live flag with fallback rows for one request; routing them through `snapshot()` touches a guard and needs an owner call.
- `delivery-09`: `POST /api/genie/start` still counts against the Genie budget, because the integrator's reclassification named only the GET session reads. Slot release under a single-flight leader is built only if the operator's Locust cold-cache profile against a real dev warehouse reproduces slot exhaustion; backpressure.py was W4a genie-server's file.
- `quality-01`: `currentRouteTemplate()` in `lib/clientErrorLog.ts` reads `window.location` (:82-84, used at :107), so an error during a held navigation can be filed under the wrong route. Routed to the field-vitals lane, which the plan moved to wave 5; registered by the integrator. RUM and client-error route templates also differ (`:conversation_id` against `:conversationId`); unifying them must change both streams for every route at once.
- `runtime-09`: the interaction budget stays report-only until three reference-runner medians set its ceilings (needs runner calibration). Steps 2-3 (web-vitals attribution, persisted RUM) go with `stack-08` and `quality-07` in the field-vitals lane, which needs the package.json and Lakebase slots; turning RUM on for customers is an owner and privacy call.

**Test infrastructure, lint and compiler**
- `quality-03` items 1-2 / `quality-09` item 1, part 2b: port module0, home_summary, geo_campaign_prefill, lineage_explorer and genie_proof_layout to fixture specs, and add the `fixture-mobile` project with layout-stability's geometry tests (its `EXECUTIVE_FIXTURE` lacks `provenance.population_source`). Deferred by the PR-2 brief for size.
- `quality-06`: the repo-wide `mount()` codemod (about 210 test files still set their own act flag, and the global flag newly unmasked "not wrapped in act" warnings in six suites) touches every lane's files and needs a quiet tree. Storybook needs an owner ruling: build it, or drop its wording from CLAUDE.md and eslint.config.js.
- `runtime-03` and `stack-09` items 3-6: the Topbar, ActivationLoopPanel and DataOperationsPanel moves compiled and passed their pins, but cost +1.05 KiB br initial (cap +0.6) and +0.58 to +0.93 on route closures (cap +0.35), so they were dropped and their allowlist entries stay; `ask-genie.growth-agent-state.ts` keeps six try/finally errors for the Growth Agent trust lane, which rewrites it. AppContext's workspace saves onto mutations, and its five compiler errors, go with `runtime-05`'s store; pending decisions in ApprovalBanner are optional; the repo-wide `react-hooks/set-state-in-effect` flip, which useLeadTableCursor and useLeadTableKeyboardFlow still fail, waits for lint depth; and item 6 re-measures the seven Analytics and five shell `'use no memo'` opt-outs, documented budget trades, against a re-baselined Analytics budget.
- `a11y-05`: the oxlint jsx-a11y baseline holds 52 hits in 23 files; the fixes land in other lanes' files and move VRT captures and bytes, so the owning lanes burn them down. PR-1's approving review found two gate holes nobody took: the ratchet checks only that oxlint linted at least one file, so a nested `.gitignore` or `frontend/.eslintignore` silently drops a file from the gate (compare `number_of_files` with the tool's own enumeration of frontend/src), and the suppression scan misses a bare rule name (`eslint-disable-next-line alt-text`), which oxlint honours. The axe spec also never scans Offer with the Console open, where axe reports `scrollable-region-focusable` on the Console's `.audit-panel` (seen by the workflow lane outside its files).
- `stack-10`: the remaining non-major updates (React, Vite, vitest, typescript-eslint and others) go in one post-wave-4 dependency batch, since taking them mid-wave would have moved every lane's budget baseline (integrator decision). TypeScript 7 waits on typescript-eslint's peer range; vitest 5 and @babel/core 8 are optional majors; update automation is owner decision #7 (12.4).
- Review leftovers, no register id, taken by no lane: LeadTable.hotkeys.test.tsx still simulates the retired aside/`aria-modal` layers; the lib/api mock gate (an integrator addition after `bde112b0`) still accepts an ApiError-only factory, because tightening it to "must spread" first needs 40 factories converted, and it neither checks an `importActual` specifier nor detects the `vi.mock(import(...))` form; the axe source gate misses direct axe-core injection; the commandActions locale test passes with the defect restored, because Node ICU defaults to en-US; a toast control inside a modal also reaches the dialog portal's React ancestors (latent, since no ancestor handles those events today); and if the entry chunk never loads, the page keeps an `aria-hidden` skeleton with no visible error.

**Accessibility**
- `a11y-06` item 2, `a11y-10` item 4 / `css-06` item 3 and `a11y-08`: NVDA with Chrome and VoiceOver with Safari over both Genie surfaces, and a first recorded Firefox forced-colors pass. They need a person. The NVDA and VoiceOver passes are `a11y-08`'s prerequisite for the ACR, which also needs an owner-supplied accessibility contact; the Firefox pass is the per-release step docs/testing.md now describes, not yet recorded.

**Signature continued**
- Wave-3 review minors (critique #8), no register id, in files no wave-4 lane owned: #130, the comment at 02-chips-kpi-segments.css:109-112 still says the hover-card stylesheet is lazy, but app.tsx imports it into initial CSS; #135, Icon.test.tsx:16 keeps `ALL_ICONS` as a plain `IconName[]`, not a `satisfies` table; Rate Lever #57 and #58, "Rate scenarios could not load." is written twice with different recovery (RateScenarioControl.tsx:44, USChoroplethMapLegend.tsx:206), and rate-lever.fixture.spec.ts:335 and :381 allow any "Failed to load resource" console error. #52, a raw line-height, was fixed in wave 3 (`889e8e96`).
- `wow-stage-1` remainder: scenario rows in the rate-mode hover card, the aria-label suffix, the cohort handoff (needs public spread bounds on GET /leads, from the Filtering lane), the scenario step and mode in the URL, the PR/VI legend-total divergence and the lazy legend's first-activation shift. The map files have no owner until `runtime-07` is decided (12.4).

**Planned before wave 4, not started** (ids only; the plan's recorded reasons stand)
- Filtering and server sort: `tables-02`, `delivery-02`, `tables-06`, `wow-power-6`, then `wow-power-2`; blocked on the VIEW_LEADS ruling (12.4). Sales loop: `states-07` item 1 (assignment Undo, with the decision toast's View receipt and the bulk result banner moving onto the shell toast region), `critic-10`, `wow-power-5` step 2, `wow-power-3`. Saved views, evidence and the audit trail: `tables-09` phase 2 with `tables-05` step 2, `flow-08` slice 1, `flow-06`, `flow-10`, `flow-04`, `tables-10`, `flow-v2`, `responsive-08`.
- Growth Agent, Home and the gold slot: `wow-ai-4`, then `wow-ai-2`; `flow-05` with `wow-ai-3`; `wow-ai-5` (prototype first); `wow-stage-2`, `wow-stage-4`, `wow-stage-5`.
- CSS cascade and lint depth: `stack-v3`, `css-05`, `bundle-06`, `css-07`, `responsive-09` items 1-2 and 4; `stack-07` items 3-5, `quality-10` steps 2-4. Palette and help: `shell-07` items 1-3 and 5, `bundle-09` item 1, `runtime-06`'s shared borrower search, `critic-07`, `wow-power-4`, and a keyboard path to the toast region (wave-3 review #13).
- Not yet in a lane: `runtime-05`, `stack-v2`, `delivery-05`, `quality-08`, `critic-05`'s presenter-mode flag, `critic-09`, `critic-12`, and the wave-3 remainders (`wow-ai-1` margins in the drawer Math tab, `runtime-08` React Activity, `motion-10` map-drill continuity, a `verify_live` probe for rate sensitivity, WebKit and Firefox pagehide and Web Lock coverage, and legacy-created jobs joined async that show "Queued").

### 12.4 Owner decisions still open

Nothing new is built for these; each stays where it is until the owner rules. The OS-seeded theme is live as shipped, and the identity boundary is built fail-closed and waits for review. Smaller rulings sit in 12.3: Storybook (`quality-06`), the segment list's hard expiry and the footprint snapshot (`delivery-06`), the Stop "ended" copy (`genie-03`) and turning RUM on for customers (`runtime-09`).

1. The OpenAPI type generator (`stack-v1`, `quality-04` items 1-2, `quality-09` item 2). `@hey-api/openapi-ts` 0.97.1 and later pulls js-yaml 4.2.0, which carries high advisories. Options: an npm override to js-yaml 4.3.2, a pin to 0.97.0, or wait. Types stay hand-written.
2. VIEW_LEADS semantics for server paging (`tables-02`, `delivery-02`): one audit row per requested page, or one per cohort view with the cursor in the payload. It blocks the filtering and server-sort lane.
3. VIEW_OFFER (`delivery-08`): compliance sign-off to share the dossier read, or a new VIEW_OFFER event. Offer keeps its own snapshot and writes VIEW_BORROWER on every open.
4. The OS-seeded theme (`a11y-10` item 5, `responsive-03`): ratify the departure from design_files/index.html:2, or revert `DEFAULT_THEME_PREFERENCE` from `'system'` to `'dark'` and re-pin theme-boot.js.
5. The route-nav dock: the nav docks only when the viewport is at least 40rem tall, a threshold chosen so the wrapped sticky nav cannot cover the whole scrollport at 400% zoom (WCAG 1.4.10); below it the nav scrolls away with the page. The css-hygiene lane's reviewer noted that a 1366x768 laptop often has a 620-660 px viewport after browser chrome and the taskbar, so some of those users lose the docked nav. The owner ratifies or changes the threshold.
6. `runtime-07`: close it on the recorded numbers (median 3 ms, p95 4-5 ms at 4x CPU), or move map hover out of React state.
7. The section 10 item 10 deviations: the notification bell (`shell-09`), nav badges and groups (`flow-07`), the borrower peek sheet and shared-element morphs (`shell-10`, `motion-03`; also tracked as `stack-04`, `runtime-10` and `css-10`), the animated Console, dark + red CTA contrast (`a11y-01`, three recorded axe violations on `.btn--primary`; the `.sr-skip-link` repaint onto `--accent-contrast` ships with that answer), square-root against linear funnel marks (`dataviz-03`; wave 4 kept linear with a not-to-scale note), the diverging ramp, the "user" glyph, `dataviz-09`'s categorical layer (re-stepping `--seg-itm`/`--seg-equity` and decoupling data colour from `[data-accent]`) and hiding the M1-M4 rail slots (`critic-05`).
8. An identity-owner review of wave-4a delivery item 1, the identity boundary (`genie-02` item 1, `lib/healthTrust.ts`). It is built fail-closed. The same review covers a pre-existing gap: a new tab has no stored actor key, so the previous actor's localStorage pins, `lastBorrowerId` and single-key shortcut preference carry into it until an actor key scopes localStorage.
9. The rest of section 10, still unanswered: #2 approve-review friction (Triage Mode, `wow-power-1`; select-all N matching and server batch decisions, `tables-07`, which also needs #4); #3 non-admin visibility of audit rows and asset freshness (`critic-03`, `flow-04`, `tables-10`, `flow-06` phase 2); #4 full-cohort export; #5 committed ZCTA geometry (`visual-09`, `dataviz-01`); #6 capturing refused prompt text (`genie-05`); #7 update automation (`quality-v2`, `stack-10`); #8 a release gate on the age of the last green live run, or auto-triggering the live suite (`quality-03` item 3); #9 the data router, borrower notes and white-label theming (`shell-05`, `flow-08`, `responsive-10`). #1 is moot: the in-house listbox shipped.
10. Two calls outside section 10: "Request approval" for non-approvers (`flow-02`), and the canonical term for "In the Money", with compliance (`critic-11`).
