# Prototype deviation register

The single register of every declared deviation from the design contract in
`design_files/`. CLAUDE.md asks that a warranted deviation be called out in
its commit with the prototype line it departs from; this file is where that
call-out lives after the commit, and `tests/unit/test_prototype_deviations_register.py`
enforces it.

## Conventions

- **One row per deviation.** `id` is kebab-case and unique across every
  section of this file (Adopted, Not adopted and Pending rows).
- **Prototype cite.** Starts with `design_files/`, with `:line` (or
  `:first-last`) where the prototype has a line to point at.
- **Code token.** Every code path an Adopted row names carries the literal
  comment token `deviation:<id>` inside a comment. A code cell lists repo
  paths, optionally `path::selector-or-symbol`, separated by `<br>` or `;`.
- **Pinning test.** A repo path, `—` (Not adopted only), or
  `pending: <path>` for a pin another lane will land.
- **Ratchet.** Marker phrases in `frontend/src/**/*.{ts,tsx,css}` (declared /
  additive deviation, departure or extension; "deviation from", "departure
  from"; "app-added") count as registered when a `deviation:<id>` token for a
  row in this file sits within 5 lines. Unregistered hits per file are pinned
  in `tests/fixtures/prototype_deviation_baseline.json`, which only shrinks:
  register a marker (row + token) and lower its file's count in the same
  commit; a new unregistered marker fails the test.
- **Pending rows.** A lane that adds a deviation appends its row under
  `## Pending rows` with the same eight columns plus a trailing `lane`
  column (`w5-<key>`). A pending row needs a `design_files/` cite and an id
  unique across the file, but no code token until the integrator promotes it
  to Adopted or Not adopted.

## Adopted

| id | deviation | prototype cite | class | code | pinning test | finding ids | ruling date |
| --- | --- | --- | --- | --- | --- | --- | --- |
| console-motion | The Console animates open (200 ms `--dur-base` / `--ease`) and closed (120 ms `--dur-fast` / `--ease-exit`) through `@starting-style` and an allow-discrete `display`, instead of the prototype's display hard cut. | design_files/Module 0 Prototype.html:904-906 | product | frontend/src/design-system/components/09-console-and-layout.css::.tweaks | frontend/src/design-system/components.test.ts | motion-01; motion-05; runtime-10; css-09 | 2026-09-30 |
| console-gutter-snap | The `.main` right gutter reserved for the open Console snaps (no transition), so a tween never crosses the `@container main` band mid-animation. | design_files/index.html:330-334 | performance | frontend/src/design-system/components/01-app-shell.css::[data-console="open"] .main | frontend/src/design-system/components.test.ts | motion-05; css-09 | 2026-09-30 |
| route-theme-view-transitions | Route changes and theme switches run View Transitions (route fade, theme cross-fade) at or under 250 ms; the prototype has no route or theme motion. | design_files/Module 0 Prototype.html:1234-1236 | product | frontend/src/app.transitions.css | frontend/src/app.transitions.css.test.ts | stack-04; motion-03; runtime-10; css-10; shell-10 | 2026-09-30 |
| route-pending-line | A held navigation draws a 2px `--accent-ink` line under the route nav after `--dur-base`; the prototype has no pending-navigation indicator. | design_files/Module 0 Prototype.html:1206-1243 | product | frontend/src/app.transitions.css::mip-route-pending<br>frontend/src/app.tsx::RouteTransition | frontend/src/app.transitions.css.test.ts | shell-05 | 2026-09-30 |
| data-ink-fixed | Single-series data marks (sparklines, Sankey, chart lines and bars, the rate window, Genie charts, the geography ramp hue) paint `--accent-data`, fixed per theme (#66C5FF dark, #014E80 light) and never following `[data-accent]`; the prototype drew them in the switchable accent. | design_files/index.html:34-38; design_files/index.html:164-176 | accessibility | frontend/src/design-system/tokens.css::--accent-data | frontend/src/design-system/dataVizContrast.test.ts | dataviz-09; visual-08 | 2026-09-30 |
| choropleth-ramp | The geography ramp mixes the fixed data hue into the empty step at equal OKLab distances (20/40/60/80%, light top step 85%) with a >= 4.5:1 ink per step, instead of the prototype's 15/30/50/70% accent mix. | design_files/Module 0 Prototype.html:863-866; design_files/Module 0 Prototype.html:1864-1871 | accessibility | frontend/src/design-system/tokens.css::--map-ramp-* | frontend/src/components/mortgage/USChoroplethMap.ramp.test.ts | dataviz-02; responsive-05 | 2026-09-30 |
| cta-fill-dark-red | Solid CTAs (`.btn--primary`, `.sr-skip-link`) fill with `--accent-fill` under `--accent-contrast` text: the accent everywhere except dark + red, which fills #D92D1A (4.84:1) and hovers darker to #C8281A (5.56:1) instead of the prototype's #FF3621 / #FF5A47 (3.62:1 / 3.09:1 under white). The skip link moves from `--bg-1` text on `--accent` to the same pair. | design_files/index.html:164-166; design_files/index.html:388-392 | accessibility | frontend/src/design-system/tokens.css::--accent-fill<br>frontend/src/design-system/components/01-app-shell.css::.btn--primary | frontend/src/design-system/tokenContrast.test.ts | a11y-01 | 2026-09-30 |
| segment-palette | The segment palette is validated per theme: dark keeps four core prototype hexes and re-steps equity to #2985DF and retention to #3FD073 (the prototype itm / equity pair measured OKLab dE 9.5 and equity was the data ink); light gets its own 13 values (the prototype never drew segments on white). The Home offer-mix bar stripes refi + HELOC in the refi and HELOC hues, separates slices with a 2px surface gap and keeps its hues in forced colours. | design_files/index.html:33-39 | accessibility | frontend/src/design-system/tokens.css::--seg-*<br>frontend/src/components/mortgage/HomeAnswerBand.css::.offer-mix | frontend/src/design-system/segmentPalette.test.ts | dataviz-09 | 2026-09-30 |
| theme-system-option | The Console and Administration theme control adds a System option (follows prefers-color-scheme live, dark when the platform cannot say) to the prototype's two-state Dark / Light control. Nothing chosen boots dark, the prototype default; only an explicit pick is stored, with its choice marker. | design_files/index.html:2 | accessibility | frontend/src/lib/themePreference.ts::DEFAULT_THEME_PREFERENCE | frontend/src/lib/themePreference.test.ts | css-02; responsive-03; a11y-10 | 2026-09-30 |

## Not adopted

| id | deviation | prototype cite | class | code | pinning test | finding ids | ruling date |
| --- | --- | --- | --- | --- | --- | --- | --- |
| topbar-bell | No notification bell in the topbar: the prototype topbar has none (its `bell` glyph at :1008 is never placed), and the Follow-ups rail item carries the inbox instead. | design_files/Module 0 Prototype.html:1206-1243 | product | — | pending: frontend/src/components/layout/FollowUpsRailItem.test.tsx | shell-09 | 2026-09-30 |
| nav-count-badges | No count badges on the route nav: the prototype rail items carry none, and a count that is not the governed total would contradict the Lead Queue. | design_files/Module 0 Prototype.html:1188-1204 | product | — | pending: frontend/src/components/layout/RoleNavigation.test.tsx | flow-07 | 2026-09-30 |
| borrower-peek-sheet | No masked-URL borrower peek sheet. The expand-in-place row preview is the prototype's only dossier preview; QueuePager, breadcrumbs, useMainScroll and useLeadTableScroll already keep the operator's place; a sheet would be a fourth right-edge overlay; and a second dossier would have to re-carry evidence, pessimistic approval and exactly one VIEW_BORROWER audit row per open. | design_files/Module 0 Prototype.html:1888-2096 | product | frontend/src/components/mortgage/LeadRowPreview.tsx | tests/unit/test_prototype_deviations_register.py | shell-10; stack-04; runtime-10; css-10 | 2026-09-30 |
| console-reflow-transition | No View Transition on the Console toggle: the gutter snaps (console-gutter-snap) and the panel keeps its own fade, so the page is never snapshotted to animate a reflow. | design_files/Module 0 Prototype.html:904-906 | performance | — | — | motion-01; runtime-10 | 2026-09-30 |
| data-router-loaders | The data router is not migrated to loaders / actions / lazy route objects. Borrower and lead reads write one VIEW_* audit row per deliberate open, and a loader re-runs on every revalidation; a cold warehouse would hold the navigation instead of painting the route's warm-up state; data-before-mount already ships as prefetchRouteData. | design_files/Module 0 Prototype.html:2097 | architecture | frontend/src/appRouter.tsx::appRouteObjects | frontend/src/appRouter.test.tsx | shell-05; shell-v2; stack-04 | 2026-09-30 |
| borrower-notes-mentions | No free-text borrower notes or @mentions. (i) Free text about consumers creates fair-lending / UDAAP evidence that the existing screens (the name-shape / PII scrub plus the protected-class and unsupported-qualification banks `audit_metadata_validation._sanitize_metadata` runs) cannot catch by paraphrase; (ii) the lender CRM is the narrative system of record (docs/audits/persona-walkthroughs/04-sales-manager.md:189); (iii) @mentions need a staff directory and a delivery channel and duplicate structured routing. "Un-erasable" applies only to the existing append-only ledgers. | design_files/Module 0 Prototype.html:1888-2096 | safety | — | tests/unit/test_no_free_text_borrower_notes.py | flow-08 | 2026-09-30 |

## Pending rows (merged by the integrator)

| id | deviation | prototype cite | class | code | pinning test | finding ids | ruling date | lane |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
