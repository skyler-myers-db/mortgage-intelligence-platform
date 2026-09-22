import { describe, expect, it } from 'vitest';
// @ts-expect-error Frontend app types intentionally exclude Node globals; this
// unit test reads the design-system CSS text under Vitest only.
import { readdirSync, readFileSync } from 'node:fs';
// @ts-expect-error see node:fs note above.
import { join } from 'node:path';
// @ts-expect-error CSS lint helper is an ESM Node script used by lint/tests only.
import { findCssLiteralViolations } from '../../../tools/lint_css_literals.mjs';
import { designCss } from '../test/designCss';

declare const process: { cwd(): string };

const tokensCss = () => readFileSync(
  new URL('./tokens.css', import.meta.url),
  'utf8',
);

describe('layout containment contracts', () => {
  it('keeps component CSS free of hard-coded color literals', () => {
    expect(findCssLiteralViolations()).toEqual([]);
  });

  it('keeps topbar borrower search visibly actionable', () => {
    const css = designCss();

    expect(css).toMatch(/\.topbar\s*\{[^}]*display:\s*grid;/s);
    expect(css).toMatch(
      /\.topbar\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\) minmax\(0,\s*32rem\) minmax\(0,\s*1fr\);/s,
    );
    expect(css).toMatch(/\.topbar__search\s*\{[^}]*grid-column:\s*2;/s);
    expect(css).toMatch(/\.topbar__search\s*\{[^}]*inline-size:\s*min\(32rem,\s*100%\);/s);
    expect(css).toContain('.topbar__actions');
    expect(css).toContain('.topbar__search-results');
    expect(css).toContain('.topbar__search-status');
    expect(css).toMatch(/\.topbar__search-results\s*\{[^}]*z-index:\s*var\(--z-search\);/s);
  });

  /**
   * 2026-08-07 audit M1: the end-justified actions cluster (~375px at
   * max-content) overflowed its 296px grid track and the opaque tenant pill
   * covered the search box's ⌘K badge by 26px at 1440x900, on every route.
   * Measured after the fix: tracks 392/512/392, search centred with 0px
   * overlap, tenant pill back to one line.
   */
  it('keeps the topbar actions cluster inside its own grid track', () => {
    const css = designCss();

    expect(css).toMatch(/\.topbar__actions\s*\{[^}]*justify-self:\s*end;/s);
    expect(css).toMatch(/\.topbar__actions\s*\{[^}]*max-inline-size:\s*100%;/s);
    // A pill is one line high; a long tenant name ellipsizes, never wraps.
    expect(css).toMatch(/\.topbar__pill\s*\{[^}]*white-space:\s*nowrap;/s);
    expect(css).toMatch(/\.topbar__pill-tenant\s*\{[^}]*text-overflow:\s*ellipsis;/s);
    // Below the breakpoint the third track is content-sized instead, so the
    // cluster still can't be overlapped when the side tracks get tight.
    expect(css).toMatch(
      /@media \(max-width:\s*88rem\)\s*\{[\s\S]*?\.topbar\s*\{[^}]*grid-template-columns:[^;]*auto;/s,
    );
  });

  it('prevents lead-table chips from compressing into neighboring cells', () => {
    const css = designCss();

    expect(css).toMatch(/\.tbl-wrap\s*\{[^}]*overflow:\s*auto;/s);
    expect(css).toMatch(/\.lead-table__table\s*\{[^}]*inline-size:\s*max-content;/s);
    expect(css).toMatch(/\.chip__label\s*\{[^}]*text-overflow:\s*ellipsis;/s);
    expect(css).toMatch(/\.lead-table__segments\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\);/s);
  });

  it('keeps small interactive chips at the WCAG 2.2 AA touch-target floor', () => {
    const css = designCss();

    expect(css).toMatch(/\.evidence-chip\s*\{[^}]*min-block-size:\s*calc\(var\(--sp-6\) \+ var\(--sp-1\)\);/s);
    expect(css).toMatch(/\.chip--compact\s*\{[^}]*min-block-size:\s*var\(--sp-6\);/s);
    expect(css).toMatch(/\.tbl__sort\s*\{[^}]*min-block-size:\s*var\(--sp-6\);/s);
  });

  it('lets evidence drawer signal rows wrap long source and value text', () => {
    const css = designCss();

    // fit-content(50%) caps the value column so a long value can never
    // squeeze the label/source column into mid-word wraps or visual
    // collision (operator report 2026-06-11, "Configured tenant lens" row).
    expect(css).toMatch(/\.lineage-node--signal\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\) fit-content\(50%\);/s);
    expect(css).toMatch(/\.lineage-node__name\s*\{[^}]*overflow-wrap:\s*anywhere;/s);
    expect(css).toMatch(/\.lineage-node__value\s*\{[^}]*overflow-wrap:\s*anywhere;/s);
  });

  it('keeps data-estate lanes from starving asset labels when the console rail opens', () => {
    const css = designCss();

    // 2026-06-11 audit P2-1 (live repro: 65px label column, 5-6 line
    // mid-word wraps with the Console rail open). Lanes wrap to fewer
    // columns instead of compressing below a readable floor, and the
    // asset row is flex-with-wrap so a wide meta (the "demo synthetic"
    // GOVERNANCE chip, which must never be ellipsized) drops to its own
    // right-aligned line instead of overflowing into the next lane
    // (overflow probe: chips painted 68-81px past the lane edge).
    expect(css).toMatch(/\.data-estate__grid\s*\{[^}]*grid-template-columns:\s*repeat\(auto-fit,\s*minmax\(13\.5rem,\s*1fr\)\);/s);
    expect(css).toMatch(/\.data-estate__asset\s*\{[^}]*flex-wrap:\s*wrap;/s);
    expect(css).toMatch(/\.data-estate__asset-main\s*\{[^}]*flex:\s*1 1 7\.5rem;/s);
    expect(css).toMatch(/\.data-estate__asset-meta\s*\{[^}]*flex-wrap:\s*wrap;/s);
  });

  it('keeps the expanded lead-row actions inside the scrollport when the console is open', () => {
    const css = designCss();

    // Re-audit #3 P3 + #4 (live: with the Console open at ~1413px the
    // colSpan-15 expanded preview spanned the table's full scroll width,
    // pushing Approve/Open/Build off-canvas behind a horizontal scroll).
    // The inner block sticks to the scrollport's left edge and caps at the
    // main container width so the actions stay visible; the grid collapses
    // via container queries at 1280/960. Pinned so the fix can't silently
    // regress (re-audit #4 nit: this rule had no test).
    expect(css).toMatch(/\.tbl__expand-inner--lead\s*\{[^}]*position:\s*sticky;/s);
    expect(css).toMatch(/\.tbl__expand-inner--lead\s*\{[^}]*left:\s*0;/s);
    expect(css).toMatch(/\.tbl__expand-inner--lead\s*\{[^}]*max-inline-size:\s*calc\(100cqw/s);
    expect(css).toMatch(/@container main \(max-width: 960px\)\s*\{[^}]*\.tbl__expand-inner--lead\s*\{\s*grid-template-columns:\s*1fr;/s);
  });

  it('makes proof affordance rows visibly interactive without layout shifts', () => {
    const css = designCss();

    expect(css).toContain('.data-estate__lane-proof');
    expect(css).toMatch(/\.data-estate__asset\s*\{[^}]*display:\s*flex;/s);
    expect(css).toMatch(/\.data-estate__asset\s*\{[^}]*cursor:\s*pointer;/s);
    expect(css).toContain('.trusted-asset--button');
    expect(css).toMatch(/\.trusted-asset--button\s*\{[^}]*width:\s*100%;/s);
    expect(css).toContain('.trusted-asset--button.is-active');
  });

  it('shows Ask Genie sample chips inside the composer grid', () => {
    const css = designCss();

    expect(css).toContain('.genie-composer__samples');
    expect(css).toMatch(/\.genie-composer__samples\s*\{[^}]*display:\s*grid;/s);
    expect(css).toMatch(/\.genie-composer__samples\s*\{[^}]*grid-template-columns:\s*repeat\(2,\s*minmax\(0,\s*1fr\)\);/s);
  });

  it('layers the ⌘K command palette above the Genie FAB and dims with a scrim (re-audit #4 #1)', () => {
    const css = designCss();
    // The palette sits at the top of the overlay scale (--z-palette, 1000),
    // above the Genie FAB (--z-genie-fab, 29) and the map tip (--z-map-tip, 900).
    expect(css).toMatch(/\.cmdk\s*\{[^}]*z-index:\s*var\(--z-palette\);/s);
    expect(css).toMatch(/\.cmdk\s*\{[^}]*background:\s*var\(--surface-scrim\);/s);
    expect(css).toContain('.cmdk__row.is-active');
    // Entrance animation is disabled under reduced motion.
    expect(css).toMatch(/@media \(prefers-reduced-motion: reduce\)\s*\{[^}]*\.cmdk[\s\S]*?animation:\s*none;/s);
  });

  it('portals the evidence hover-card with fixed coords and never lets it steal the click (re-audit #4 #8)', () => {
    const css = designCss();
    expect(css).toMatch(/\.evidence-hovercard\s*\{[^}]*position:\s*fixed;/s);
    // pointer-events:none means the chip click underneath always wins.
    expect(css).toMatch(/\.evidence-hovercard\s*\{[^}]*pointer-events:\s*none;/s);
    expect(css).toContain('.evidence-hovercard--above');
    expect(css).toMatch(/@media \(prefers-reduced-motion: reduce\)\s*\{[^}]*\.evidence-hovercard\s*\{\s*animation:\s*none;/s);
  });

  it('gives the KPI one-time entrance + sparkline draw a reduced-motion off-switch (re-audit #4 #2)', () => {
    const css = designCss();
    expect(css).toContain('.kpi__value--enter');
    expect(css).toContain('@keyframes kpi-value-enter');
    expect(css).toMatch(/\.spark__line--draw\s*\{[^}]*stroke-dashoffset:/s);
    // Reduced motion: no entrance, sparkline fully drawn (offset 0).
    expect(css).toMatch(/@media \(prefers-reduced-motion: reduce\)\s*\{[\s\S]*?\.spark__line--draw\s*\{[^}]*stroke-dashoffset:\s*0;/s);
  });

  /**
   * Audit motion-07: a dash of 220 on a ~64-124px path drew the line in the
   * first 50-100ms of its window. The stroke path sets pathLength="1"
   * (Sparkline.tsx), so the dash and offset must be exactly 1 and the draw
   * must run on the shared draw tokens; the area fill fades in over the same
   * window and reduced motion shows both fully.
   */
  it('draws the sparkline 1:1 against a normalised path and fades the area over the same window', () => {
    const css = designCss();
    expect(css).toMatch(/\.spark__line--draw\s*\{[^}]*stroke-dasharray:\s*1;/s);
    expect(css).toMatch(/\.spark__line--draw\s*\{[^}]*stroke-dashoffset:\s*1;/s);
    expect(css).toMatch(/\.spark__line--draw\s*\{[^}]*animation:\s*spark-draw var\(--dur-draw\) var\(--ease-draw\)/s);
    expect(css).toMatch(/\.spark__area--fade\s*\{[^}]*opacity:\s*0;/s);
    expect(css).toMatch(/\.spark__area--fade\s*\{[^}]*animation:\s*spark-area-fade var\(--dur-draw\) var\(--ease-draw\)/s);
    expect(css).toMatch(/@keyframes spark-area-fade\s*\{[^}]*to\s*\{\s*opacity:\s*1;/s);
    expect(css).toMatch(/@media \(prefers-reduced-motion: reduce\)\s*\{[\s\S]*?\.spark__area--fade\s*\{[^}]*opacity:\s*1;/s);
    const tokens = tokensCss();
    expect(tokens).toMatch(/--dur-draw:\s*700ms;/);
    expect(tokens).toMatch(/--ease-draw:\s*cubic-bezier\(/);
  });

  it('gives the funnel Sankey a focus-visible affordance and a reduced-motion off-switch (Buyer-Wow #5)', () => {
    const css = designCss();
    expect(css).toContain('.funnel-sankey__node');
    // Keyboard focus is visible on the SVG node (stroke ring + accent label).
    expect(css).toMatch(/\.funnel-sankey__node:focus-visible \.funnel-sankey__bar\s*\{[^}]*stroke:/s);
    // One-time ribbon draw is fully disabled under reduced motion (ribbons
    // visible, no animation) so it never becomes a distracting loop.
    expect(css).toMatch(/@media \(prefers-reduced-motion: reduce\)\s*\{[\s\S]*?\.funnel-sankey--enter \.funnel-sankey__ribbon\s*\{[^}]*animation:\s*none;/s);
    expect(css).toMatch(/@media \(prefers-reduced-motion: reduce\)\s*\{[\s\S]*?\.funnel-sankey--enter \.funnel-sankey__ribbon\s*\{[^}]*opacity:\s*1;/s);
  });

  it('animates map level transitions without collapsing the hero map flex layout (Buyer-Wow #4)', () => {
    const css = designCss();
    // The keyed wrapper MUST be flex-transparent: it takes .map-wrap's flex:1
    // slot AND re-exposes it to the stage inside, so wrapping never collapses
    // the map. This is the load-bearing assertion for the hero surface.
    expect(css).toMatch(/\.map-levels\s*\{[^}]*flex:\s*1 1 0;/s);
    expect(css).toMatch(/\.map-levels\s*\{[^}]*display:\s*flex;/s);
    // Airtight: the child-flex rule body itself (the `.map-levels > ...`
    // selector group through its `{ ... }`) must carry flex:1 1 0 — scoped to
    // the block so it can't satisfy itself against the standalone stage rule.
    expect(css).toMatch(/\.map-levels\s*>\s*\.map-svg-stage,\s*\.map-levels\s*>\s*\.map-stage,\s*\.map-levels\s*>\s*\.zip-tiles\s*\{[^}]*flex:\s*1 1 0;/s);
    expect(css).toContain('@keyframes map-level-in');
    // Both the level transition and the ZIP tile stagger are reduced-motion off.
    expect(css).toMatch(/@media \(prefers-reduced-motion: reduce\)\s*\{[^}]*\.map-levels\s*\{[^}]*animation:\s*none;/s);
    expect(css).toMatch(/\.zip-tile\s*\{[^}]*animation:\s*zip-tile-in/s);
    expect(css).toMatch(/@media \(prefers-reduced-motion: reduce\)\s*\{[^}]*\.zip-tile\s*\{[^}]*animation:\s*none;/s);
    // The tile entrance is opacity-only (a transform would be retained by the
    // `both` fill and clobber the :hover lift — the signoff bug).
    expect(css).toMatch(/@keyframes zip-tile-in\s*\{\s*from\s*\{\s*opacity:\s*0;\s*\}\s*to\s*\{\s*opacity:\s*1;\s*\}\s*\}/s);
  });

  it('colors the borrower-story verified/unverified claim chips by status (Buyer-Wow #3)', () => {
    const css = designCss();
    expect(css).toContain('.borrower-story__narrative');
    expect(css).toMatch(/\.borrower-story__claim\s*\{[^}]*border:\s*1px solid var\(--status-success-line\);/s);
    expect(css).toMatch(/\.borrower-story__claim--unverified\s*\{[^}]*border-color:\s*var\(--status-warning-line\);/s);
    expect(css).toMatch(/\.borrower-story__verdict--ok\s*\{[^}]*color:\s*var\(--status-success-ink\);/s);
    expect(css).toMatch(/\.borrower-story__verdict--warn\s*\{[^}]*color:\s*var\(--status-warning-ink\);/s);
  });

  it('styles the borrower offer prototype mock (watermark + warning banner)', () => {
    const css = designCss();
    expect(css).toContain('.offer-mock__watermark');
    expect(css).toMatch(/\.offer-mock__banner\s*\{[^}]*color:\s*var\(--status-warning-ink\);/s);
    expect(css).toMatch(/\.offer-mock-scrim\s*\{[^}]*background:\s*var\(--surface-scrim\);/s);
  });

  it('styles the portfolio summary card claim chips + verdict ("Your book today")', () => {
    const css = designCss();
    expect(css).toContain('.portfolio-summary__narrative');
    expect(css).toMatch(/\.portfolio-summary__claim\s*\{[^}]*border:\s*1px solid var\(--status-success-line\);/s);
    expect(css).toMatch(/\.portfolio-summary__claim--unverified\s*\{[^}]*border-color:\s*var\(--status-warning-line\);/s);
    expect(css).toMatch(/\.portfolio-summary__verdict--ok\s*\{[^}]*color:\s*var\(--status-success-ink\);/s);
  });

  it('styles the pinned-insights card and the answer pin-row (Buyer-Wow #9)', () => {
    const css = designCss();
    expect(css).toContain('.pinned-insights__list');
    expect(css).toMatch(/\.pinned-insights__unpin:hover\s*\{[^}]*color:\s*var\(--signal-danger\);/s);
    expect(css).toContain('.genie-answer__pin-row');
  });

  it('renders skeleton placeholders for slow lead and data-estate loads', () => {
    const css = designCss();

    expect(css).toContain('.lead-queue-skeleton__row');
    expect(css).toMatch(/\.lead-queue-skeleton__row\s*\{[^}]*grid-template-columns:/s);
    expect(css).toContain('.data-estate__lane-skeleton-main');
    expect(css).toContain('.data-estate__asset--skeleton');
  });

  it('gives the drawer freshness chip distinct loading and error states', () => {
    const css = designCss();

    // Re-audit 2026-06-11: 'checking' and 'fetch failed' wore the
    // unavailable/no-timestamp style. Loading pulses on the accent
    // (motion-reduced safe); error warns.
    expect(css).toMatch(/\.source-freshness--loading::before\s*\{[^}]*background:\s*var\(--accent\);/s);
    expect(css).toMatch(/\.source-freshness--error::before\s*\{[^}]*background:\s*var\(--signal-warning\);/s);
    expect(css).toMatch(/@media \(prefers-reduced-motion:\s*reduce\)\s*\{[\s\S]*?\.source-freshness--loading::before\s*\{\s*animation:\s*none;/s);
  });

  it('keeps long error messages wrapped inside callouts', () => {
    const css = designCss();

    expect(css).toMatch(/\.status-callout--danger\s*\{[^}]*flex-wrap:\s*wrap;/s);
    expect(css).toMatch(/\.status-callout--danger > span\s*\{[^}]*flex:\s*1 1 24rem;/s);
  });

  it('keeps the floating Genie entrypoint out of desktop table/map content', () => {
    const css = designCss();

    expect(css).toMatch(/\.genie__fab\s*\{[^}]*display:\s*none;/s);
    expect(css).toMatch(/@media \(max-width:\s*720px\)\s*\{[\s\S]*?\.genie__fab\s*\{[\s\S]*?display:\s*grid;/s);
  });

  it('keeps theme switches visually coherent across shell surfaces', () => {
    const css = designCss();

    expect(css).toMatch(/\.topbar,[\s\S]*?\.topbar__icon-btn\s*\{[^}]*transition:/s);
    expect(css).toMatch(/@media \(prefers-reduced-motion:\s*reduce\)\s*\{[\s\S]*?\.topbar,[\s\S]*?transition:\s*none;/s);
  });

  it('uses readable accent ink for light-theme active and hover text', () => {
    const css = designCss();
    const tokens = tokensCss();

    expect(tokens).toContain('[data-theme="light"][data-accent="bright"] { --accent-ink: #014E80; }');
    expect(tokens).toContain('[data-theme="light"][data-accent="teal"]   { --accent-ink: #045D62; }');
    expect(tokens).toContain('[data-theme="light"][data-accent="red"]    { --accent-ink: #B42318; }');
    expect(css).toMatch(/\.rail__item\.is-active\s*\{[^}]*color:\s*var\(--accent-ink\);/s);
    expect(css).toMatch(/\.topbar__icon-btn\.is-active\s*\{[^}]*color:\s*var\(--accent-ink\);/s);
    expect(css).toMatch(/\.filter\.is-active\s*\{[^}]*color:\s*var\(--accent-ink\);/s);
    expect(css).toMatch(/\.proof-tab\.is-active,[^{]+\{[^}]*color:\s*var\(--accent-ink\);/s);
    expect(css).toMatch(/\.filter-menu__item\.is-selected\s*\{[^}]*color:\s*var\(--accent-ink\);/s);
    expect(css).toMatch(/\.text-accent\s*\{[^}]*color:\s*var\(--accent-ink\);/s);
    expect(css).toMatch(/\.icon-accent\s*\{[^}]*color:\s*var\(--accent-ink\);/s);
  });

  /**
   * 2026-09-21 audit visual-02 / shell-02 / responsive-01: the Console body
   * was an auto-track grid, so the 432px property-lookup row widened every
   * row past the 300px panel and `.tweak-row label` restyled the lookup's
   * own field labels. The rendered proof is console-layout.fixture.spec.ts;
   * this pins the CSS contract it relies on.
   */
  it('constrains the Console body to one track and scopes its row labels', () => {
    const css = designCss();

    expect(css).toMatch(/\.tweaks__body\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\);/s);
    expect(css).toMatch(/\.tweak-row > label,\s*\.tweak-row > \.row > label\s*\{/s);
    expect(css).not.toMatch(/\n\.tweak-row label\s*\{/);
    // The compact lookup wraps its audit chip and stacks its field grid.
    expect(css).toMatch(/\.property-lookup--compact \.surface__hdr\s*\{[^}]*flex-wrap:\s*wrap;/s);
    expect(css).toMatch(/\.property-lookup--compact \.field-grid\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\);/s);
    expect(css).toMatch(/\.property-lookup--compact \.form-input\s*\{[^}]*inline-size:\s*100%;[^}]*min-inline-size:\s*0;/s);
  });

  /**
   * 2026-09-21 audit responsive-06: the 961-1280 band forced 3 columns and
   * orphaned the fourth KPI (Console open at 1440, or a 1280 laptop). The
   * quantity query lays exactly four cards out 2x2; other counts keep the
   * band's three columns. Rendered proof: console-layout.fixture.spec.ts.
   */
  it('lays four KPIs out 2x2 in the 961-1280px container band', () => {
    const css = designCss();
    expect(css).toMatch(
      /@container main \(min-width: 961px\) and \(max-width: 1280px\)\s*\{\s*\.kpi-row:has\(> :nth-child\(4\):last-child\)\s*\{\s*grid-template-columns:\s*repeat\(2,\s*minmax\(0,\s*1fr\)\);/s,
    );
  });

  /**
   * 2026-09-21 audit css-08: 17 magic z-index values. Every declaration now
   * reads a `--z-*` token; the token values ARE the numbers the sites used
   * before, so this is a pure refactor of the stacking order (the pairs are
   * listed in the commit that introduced the scale).
   */
  it('reads every z-index from the --z-* scale and keeps its numeric values', () => {
    const css = designCss();
    const routeCss = readdirSync(join(process.cwd(), 'src', 'routes'))
      .filter((name: string) => name.endsWith('.css'))
      .map((name: string) => readFileSync(join(process.cwd(), 'src', 'routes', name), 'utf8'))
      .join('\n');
    const stripComments = (text: string) => text.replace(/\/\*[\s\S]*?\*\//g, '');

    const numericLiterals = [...stripComments(`${css}\n${routeCss}`).matchAll(/z-index\s*:\s*(-?\d+)\s*[;}]/g)]
      .map((match) => match[0]);
    expect(numericLiterals, 'a z-index outside tokens.css must read a --z-* token').toEqual([]);

    const declared = [...stripComments(`${css}\n${routeCss}`).matchAll(/z-index\s*:\s*var\(--z-([a-z0-9-]+)\)/g)]
      .map((match) => match[1]);
    expect(declared.length).toBeGreaterThanOrEqual(30);

    const tokens = tokensCss();
    const scale = Object.fromEntries(
      [...tokens.matchAll(/--z-([a-z0-9-]+):\s*(-?\d+);/g)].map((match) => [match[1], Number(match[2])]),
    );
    for (const name of new Set(declared)) {
      expect(scale, `--z-${name} is defined in tokens.css`).toHaveProperty(name);
    }
    expect(scale).toMatchObject({
      below: -1, base: 0, raised: 1, 'raised-2': 2, 'raised-3': 3, 'raised-4': 4,
      sticky: 5, topbar: 10, menu: 20, 'genie-fab': 29, genie: 30,
      'drawer-scrim': 40, drawer: 41, tooltip: 45, console: 50, search: 60,
      'skip-link': 100, 'map-tip': 900, hovercard: 1000, modal: 1000, palette: 1000,
    });
    // The overlay chain the shell depends on, in order.
    expect(scale.genie).toBeGreaterThan(scale['genie-fab']);
    expect(scale.drawer).toBeGreaterThan(scale['drawer-scrim']);
    expect(scale['drawer-scrim']).toBeGreaterThan(scale.genie);
    expect(scale.console).toBeGreaterThan(scale.drawer);
    expect(scale.palette).toBeGreaterThan(scale['map-tip']);
  });

  /**
   * 2026-09-21 audit responsive-v1: Console (--z-console) and the docked Genie
   * panel shared the right-edge anchor, so both open at 1440x900 hid the
   * panel's right 300px and its composer. The docked panel now moves left of
   * the Console by its width; undocked (dragged) panels are left alone and
   * the < 1280 bottom-sheet band drops the sheet under the Genie layer
   * instead. Rendered proof: console-layout.fixture.spec.ts.
   */
  it('moves the docked Genie panel clear of an open Console', () => {
    const css = designCss();
    expect(css).toMatch(/:root\s*\{\s*--console-w:\s*300px;\s*\}/);
    expect(css).toMatch(/\.tweaks\s*\{[^}]*width:\s*var\(--console-w\);/s);
    expect(css).toMatch(/\[data-console="open"\] \.genie:not\(\.is-undocked\)\s*\{\s*right:\s*calc\(var\(--console-w\) \+ var\(--sp-4\) \* 2\);/s);
    expect(css).toMatch(/@media \(max-width: 1279px\)\s*\{[\s\S]*?\.tweaks\s*\{[^}]*z-index:\s*var\(--z-sheet\);/s);
    expect(css).toMatch(/@media \(min-width: 2560px\)\s*\{[\s\S]*?--console-w:\s*340px;/s);
    expect(tokensCss()).toMatch(/--z-sheet:\s*25;/);
  });

  /**
   * 2026-09-21 audit motion-01 / css-03: `visibility` was not in the drawer's
   * or the Genie panel's transition list, so each flipped hidden the instant
   * it closed and its slide / fade never showed. The closed rule now holds
   * visibility for --dur-exit; the open rule flips it at 0s; reduced motion
   * zeroes the delay. The Console gains an @starting-style entry and an
   * allow-discrete display exit (additive to the prototype's hard cut).
   * Rendered proof: console-layout.fixture.spec.ts.
   */
  it('lets the drawer, Genie panel and Console animate out before they hide', () => {
    const css = designCss();
    const tokens = tokensCss();
    expect(tokens).toMatch(/--dur-exit:\s*216ms;/);
    expect(tokens).toMatch(/--ease-exit:\s*cubic-bezier\(/);

    expect(css).toMatch(/\.drawer\s*\{[^}]*visibility:\s*hidden;[^}]*transition:\s*transform var\(--dur-exit\) var\(--ease-exit\),\s*visibility 0s linear var\(--dur-exit\);/s);
    expect(css).toMatch(/\.drawer\.is-open\s*\{[^}]*visibility:\s*visible;[^}]*transition:\s*transform var\(--dur-slow\) var\(--ease\),\s*visibility 0s linear 0s;/s);
    expect(css).not.toMatch(/\.drawer:not\(\.is-open\)/);

    expect(css).toMatch(/\.genie\s*\{[^}]*visibility:\s*hidden;[^}]*visibility 0s linear var\(--dur-exit\);/s);
    expect(css).toMatch(/\.genie\.is-open\s*\{[^}]*visibility:\s*visible;[^}]*visibility 0s linear 0s;/s);
    expect(css).not.toMatch(/\.genie:not\(\.is-open\)/);

    expect(css).toMatch(/\.tweaks\s*\{[^}]*display:\s*none;[^}]*transition:[^}]*display var\(--dur-fast\) allow-discrete;/s);
    expect(css).toMatch(/\.tweaks\.is-open\s*\{[^}]*display:\s*flex;[^}]*display var\(--dur-base\) allow-discrete;/s);
    expect(css).toMatch(/@starting-style\s*\{\s*\.tweaks\.is-open\s*\{\s*opacity:\s*0;/s);

    expect(css).toMatch(/@media \(prefers-reduced-motion: reduce\)\s*\{\s*\*,[\s\S]*?transition-delay:\s*0s !important;/s);
    // Reduced motion drops the visibility hold entirely (a pending 0.01ms
    // transition would still show the panel until the next frame commits).
    expect(css).toMatch(/@media \(prefers-reduced-motion: reduce\)\s*\{[^}]*\.drawer,\s*\.drawer\.is-open\s*\{\s*transition-property:\s*transform;/s);
    expect(css).toMatch(/@media \(prefers-reduced-motion: reduce\)\s*\{[^}]*\.genie,\s*\.genie\.is-open\s*\{\s*transition-property:\s*opacity, transform;/s);
  });

  it('lets segment cards wrap content instead of clipping labels or pending copy', () => {
    const css = designCss();

    expect(css).toMatch(/\.seg-card\s*\{[^}]*min-block-size:\s*184px;/s);
    expect(css).toMatch(/\.seg-card__hdr\s*\{[^}]*min-inline-size:\s*0;/s);
    expect(css).toMatch(/\.seg-card__title\s*\{[^}]*overflow-wrap:\s*anywhere;/s);
    expect(css).toMatch(/\.seg-card__count\s*\{[^}]*overflow-wrap:\s*anywhere;/s);
    expect(css).toMatch(/\.seg-card__sub\s*\{[^}]*overflow-wrap:\s*anywhere;/s);
    expect(css).toMatch(/\.seg-card__meta\s*\{[^}]*flex-wrap:\s*wrap;/s);
    expect(css).toMatch(/\.seg-card__meta-item\s*\{[^}]*overflow-wrap:\s*anywhere;/s);
  });
});
