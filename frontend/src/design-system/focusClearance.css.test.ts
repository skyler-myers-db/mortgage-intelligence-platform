// @ts-expect-error Frontend app types intentionally exclude Node globals; this
// test reads the stylesheet entry under Vitest only.
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { designCss } from '../test/designCss';

declare const process: { cwd(): string };
import { featureStylesheets } from '../test/featureCss';

/**
 * The Lead Queue lane's CSS pins (wave 4b; never components.test.ts):
 *
 *   - Focus Not Obscured (audit a11y-v2, WCAG 2.2 SC 2.4.11, technique
 *     C43): what `.main` holds outside the nav clears the sticky route nav
 *     (scroll-margin) by its measured block while it is docked (report
 *     12.4 #5: `.route-nav[data-docked]`, useRouteNavDock), except
 *     the ranked-borrower table, whose focus targets carry their own
 *     scroll-margin at the block start (the larger of the nav's clearance
 *     and the sticky thead's), and whose scroller clears the pinned
 *     Approval column with scroll-padding at the inline end, cancelled on
 *     the pin's own controls. Rendered proof: the focus-obscured walk in
 *     tests/e2e/fixture/lead-queue.fixture.spec.ts. The in-place filter
 *     listboxes are left out of the nav's margin, and every :has() stays on
 *     `.main` itself (cost proof: focus-clearance-cost.fixture.spec.ts).
 *   - The row-expand motion (audit motion-08 slice 1, queue part): one
 *     chevron that rotates, an opacity-only fade in, both on --dur-fast /
 *     --ease and off under prefers-reduced-motion.
 */

const stripComments = (text: string): string => text.replace(/\/\*[\s\S]*?\*\//g, '');
const components = (): string => stripComments(designCss());

function leadTableCss(): string {
  const sheet = featureStylesheets().find((entry) => entry.file === 'src/components/mortgage/LeadTable.css');
  expect(sheet, 'LeadTable.css is a feature stylesheet').toBeDefined();
  return stripComments(sheet!.css);
}

/** The body of the first rule whose selector list is exactly `selectors` (in order, any whitespace). */
function ruleFor(css: string, selectors: readonly string[]): string {
  const rule = [...css.matchAll(/([^{}]+)\{([^{}]*)\}/g)]
    .find((match) => match[1].split(',').map((part) => part.trim()).join('|') === selectors.join('|'));
  expect(rule, `${selectors.join(', ')} is declared`).toBeDefined();
  return rule![2];
}

/** The body of the first rule whose selector list is exactly `selector`. */
function block(css: string, selector: string): string {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const match = new RegExp(`(?:^|[}\\n])\\s*${escaped}\\s*\\{([^}]*)\\}`).exec(css);
  expect(match, `${selector} is declared`).not.toBeNull();
  return match![1];
}

const RING = String.raw`\+\s*var\(--focus-ring-width\)\s*\+\s*var\(--focus-ring-offset\)`;
/** The pinned column plus the ring, as LeadTable.css spells it. */
const PIN_CLEAR = String.raw`var\(--tbl-pin-inline-size\)\s*${RING}`;

function focusClearancePartial(): string {
  return stripComments(readFileSync(`${process.cwd()}/src/design-system/components/38-focus-clearance.css`, 'utf8') as string);
}

/** A selector with every parenthesized argument removed: its compounds and combinators only. */
function topLevel(selector: string): string {
  let text = selector;
  for (let previous = ''; previous !== text;) {
    previous = text;
    text = text.replace(/\([^()]*\)/g, '');
  }
  return text;
}
/** routes/ask-genie.css's condition for its own nav clearance: the Ask tab (with its docked composer) shows. */
const ASK_TAB = String.raw`section\[role="tabpanel"\]:not\(\[hidden\]\) \.genie-composer`;
/** The same condition, short (the shell sheet's gates): a composer outside any hidden tabpanel. */
const ASK_TAB_SHORT = String.raw`\.genie-composer:not\(\[hidden\] \*\)`;

describe('focus clearance (a11y-v2)', () => {
  it('imports 38-focus-clearance.css exactly once, right after 37', () => {
    const entry = readFileSync(`${process.cwd()}/src/design-system/components.css`, 'utf8') as string;
    const imports = entry.split('\n').filter((line) => line.startsWith('@import'));
    const at37 = imports.findIndex((line) => line.includes('37-topbar-status.css'));
    expect(imports.filter((line) => line.includes('38-focus-clearance.css'))).toHaveLength(1);
    expect(imports[at37 + 1]).toBe('@import "./components/38-focus-clearance.css";');
  });

  it('the ranked-borrower scroller sets the block clearance: the larger of the route nav\'s and the thead\'s, never as scroll-padding', () => {
    const wrap = block(leadTableCss(), '.tbl-wrap:has(> .lead-table__table)');
    // The measured size (useTableScrollClearance): one row until measured.
    expect(wrap).toMatch(/--tbl-head-block-size:\s*var\(--row-h\);/);
    // --nav-clear is `.main`'s (38-focus-clearance.css, ring included); absent
    // (no docked nav, the Ask tab, a short viewport), the thead's alone.
    expect(wrap).toMatch(new RegExp(
      String.raw`--tbl-focus-clear:\s*max\(\s*var\(--nav-clear,\s*0px\),\s*calc\(\s*var\(--tbl-head-block-size\)\s*${RING}\s*\)\s*\);`,
    ));
    // A block-start scroll-padding on the table cleared the thead only, never
    // the nav (38 leaves the table's contents out).
    expect(leadTableCss()).not.toMatch(/scroll-padding(?:-block|-top|:)/);
  });

  it('every focus target in the table carries that clearance at its block start: row controls, the expanded row and its review', () => {
    const targets = ruleFor(leadTableCss(), [
      '.lead-table__table tr[data-borrower-row] *',
      '.lead-table__table tr.tbl__expand',
      '.lead-table__table tr.tbl__expand *',
    ]);
    expect(targets).toMatch(/^\s*scroll-margin-block-start:\s*var\(--tbl-focus-clear\);\s*$/);
  });

  it('the J / K cursor row carries it too, plus --sp-2 of air at each end', () => {
    const row = block(leadTableCss(), '.lead-table__table tr[data-borrower-row]');
    expect(row).toMatch(/^\s*scroll-margin-block:\s*calc\(\s*var\(--tbl-focus-clear\)\s*\+\s*var\(--sp-2\)\s*\)\s+var\(--sp-2\);\s*$/);
  });

  it('the thead size is registered NON-inherited, so its measured write re-styles the scroller alone', () => {
    const registration = block(leadTableCss(), '@property --tbl-head-block-size');
    expect(registration).toMatch(/syntax:\s*'<length>';/);
    expect(registration).toMatch(/inherits:\s*false;/);
    expect(registration).toMatch(/initial-value:\s*0px;/);
    // The clearance the rows inherit computes to a length, so a thead write
    // that leaves it unchanged re-styles no row.
    const clear = block(leadTableCss(), '@property --tbl-focus-clear');
    expect(clear).toMatch(/syntax:\s*'<length>';/);
    expect(clear).toMatch(/inherits:\s*true;/);
  });

  it('the ranked-borrower scroller clears the pinned Approval column at its inline end, by that column\'s declared width', () => {
    const pin = block(leadTableCss(), '.tbl-wrap:has(> .lead-table__table)');
    const declared = /\.lead-table__col-approval\s*\{\s*width:\s*(\d+px);/.exec(stripComments(readFileSync(`${process.cwd()}/src/design-system/components/03-score-and-table.css`, 'utf8') as string))?.[1];
    expect(declared, 'the Approval column declares its width (table-layout: fixed)').toBeTruthy();
    expect(pin).toMatch(new RegExp(String.raw`--tbl-pin-inline-size:\s*${declared};`));
    expect(pin).toMatch(new RegExp(String.raw`scroll-padding-inline-end:\s*calc\(\s*${PIN_CLEAR}\s*\);`));
  });

  it('moves the pin\'s own controls back by that padding, so a focus on one never scrolls the table sideways', () => {
    const inPin = block(leadTableCss(), '.lead-table__table .tbl-cell--approval *');
    expect(inPin).toMatch(new RegExp(
      String.raw`scroll-margin-inline:\s*calc\(\s*${PIN_CLEAR}\s*\)\s*calc\(\s*-1\s*\*\s*\(\s*${PIN_CLEAR}\s*\)\s*\);`,
    ));
  });

  it('keeps the shell stylesheet\'s `.tbl-wrap` as it was (the clearance ships in the lazy sheet)', () => {
    expect(block(components(), '.tbl-wrap')).not.toMatch(/scroll-padding|--tbl-/);
  });

  it('everything in .main outside the nav clears the docked route nav by its measured block, under no media query', () => {
    const css = components();
    // `.main` carries the clearance only while its nav (a direct child) is
    // docked, never while /ask-genie's Ask tab clears the nav itself (below);
    // a universal rule with zero specificity (:where, so a route's own
    // scroll-margin still wins) reads it.
    const rules = new RegExp(String.raw`(?:^|\})\s*`
      + String.raw`\.main:has\(> \.route-nav\[data-docked\]\):not\(:has\(${ASK_TAB_SHORT}\)\)\s*\{([^}]*)\}\s*`
      + String.raw`:where\(\.main :not\(([^)]*)\)\)\s*\{([^}]*)\}`).exec(css);
    expect(rules, 'both rules, top level and adjacent').not.toBeNull();
    // The measured nav block (useRouteNavDock's --route-nav-block), plus the ring.
    expect(rules![1]).toMatch(new RegExp(String.raw`^\s*--nav-clear:\s*calc\(\s*var\(--route-nav-block\)\s*${RING}\s*\);\s*$`));
    expect(rules![2].split(',').map((part) => part.trim())).toEqual(
      expect.arrayContaining(['.route-nav *', '.tbl-wrap *', '.filter-menu *', '.lead-approve-dialog *']),
    );
    // No fallback: a `.main` without the clearance leaves the margin at the registered 0px.
    expect(rules![3]).toMatch(/^\s*scroll-margin-block-start:\s*var\(--nav-clear\);\s*$/);
    // No 40rem height query docks the nav or wraps the clearance any more.
    expect(focusClearancePartial()).not.toMatch(/@media/);
    expect(css).not.toMatch(/@media\s*\(min-height:\s*40rem\)\s*\{\s*(?:\.route-nav|\.main:has\(|:where\(\.main)/);
    // Never scroll-padding on `.main` for the nav: it shrank the view for the
    // nav's own links, so a click on one scrolled `.main` to its top first.
    expect(css).not.toMatch(/\.main:has\(> \.route-nav[^{]*\{[^}]*scroll-padding/);
    expect(css.match(/\.main:has\(> \.route-nav\[data-docked\]\)/g)).toHaveLength(1);
  });

  it('registers the measured block NON-inherited and the clearance inherited, both <length> at 0px', () => {
    const css = components();
    const registered = /@property --route-nav-block\s*\{([^}]*)\}/.exec(css)?.[1] ?? '';
    expect(registered).toMatch(/syntax:\s*'<length>';/);
    expect(registered).toMatch(/inherits:\s*false;/);
    expect(registered).toMatch(/initial-value:\s*0px;/);
    const clear = /@property --nav-clear\s*\{([^}]*)\}/.exec(css)?.[1] ?? '';
    expect(clear).toMatch(/syntax:\s*'<length>';/);
    expect(clear).toMatch(/inherits:\s*true;/);
    expect(clear).toMatch(/initial-value:\s*0px;/);
  });

  it('sticks the Administration section nav at the route nav\'s measured block while docked, at .main\'s top while not', () => {
    const sheet = featureStylesheets().find((entry) => entry.file === 'src/components/admin/AdminSectionNav.css');
    expect(sheet, 'AdminSectionNav.css is a feature stylesheet').toBeDefined();
    // Sticky under its own (unchanged) 40rem query; the offset is the route nav's.
    const media = /@media \(min-height: 40rem\)\s*\{([\s\S]*)\}\s*$/.exec(stripComments(sheet!.css))?.[1] ?? '';
    const nav = block(media, '.admin-section-nav');
    expect(nav).toMatch(/position:\s*sticky;/);
    expect(nav).toMatch(new RegExp(
      String.raw`inset-block-start:\s*max\(\s*0px,\s*calc\(\s*var\(--nav-clear\)\s*-\s*var\(--focus-ring-width\)\s*-\s*var\(--focus-ring-offset\)\s*\)\s*\);`,
    ));
    // No hand-written one-line nav (the 57px expression) is left to drift.
    expect(stripComments(sheet!.css)).not.toMatch(/var\(--sp-3\)\s*\*\s*2\s*\+\s*var\(--sp-8\)/);
  });

  it('keeps every :has() on `.main` itself, never in an ancestor compound', () => {
    // `.main:has(...) *` made Chromium re-style the whole `.main` subtree on
    // every node inserted into it (every virtual-window shift of the queue).
    const selectors = [...focusClearancePartial().matchAll(/([^{}]+)\{/g)]
      .map((match) => match[1].trim())
      .filter((selector) => !selector.startsWith('@'));
    const withHas = selectors.filter((selector) => selector.includes(':has('));
    expect(withHas, 'non-vacuity: the partial gates on :has()').not.toEqual([]);
    for (const selector of withHas) {
      expect(topLevel(selector), selector).toMatch(/^\.main(?::[a-z-]+)+$/);
    }
    expect(selectors.filter((selector) => /\s/.test(topLevel(selector))), 'the universal rule sits in :where()').toEqual([]);
  });

  it('leaves the top-layer approval review dialog, its own scroller, out of the nav\'s margin', () => {
    // a11y-v2 residual: the nav is not over a top-layer dialog, so the margin
    // only over-scrolled the dialog when a control already in its view took focus.
    expect(focusClearancePartial()).toMatch(/:where\(\.main :not\([^)]*\.lead-approve-dialog \*[^)]*\)\)/);
  });

  it('gives the sticky header\'s controls the table clearance only while the table sits at its block start', () => {
    const css = leadTableCss();
    expect(css).toMatch(/\.tbl-wrap:has\(> \.lead-table__table\)\s*\{\s*container-type:\s*scroll-state;\s*\}/);
    expect(css).toMatch(new RegExp(
      String.raw`@container not scroll-state\(scrollable: top\)\s*\{\s*\.lead-table__table thead \*\s*\{\s*scroll-margin-block-start:\s*var\(--tbl-focus-clear\);\s*\}\s*\}`,
    ));
    // Never unconditional: a margin with the table scrolled moved it back to "reveal" the stuck header.
    const outsideQueries = css.replace(/@container[^{]*\{[^{}]*\{[^}]*\}\s*\}/g, '');
    expect(outsideQueries).not.toMatch(/(?:^|[}\n])\s*\.lead-table__table thead[^{]*\{[^}]*scroll-margin/);
  });

  it('leaves the in-place filter listboxes out of the nav\'s margin', () => {
    // FilterSelect / MultiFilterSelect scroll their active option into view
    // on hover: the margin rolled a scrolled menu back under the pointer.
    expect(focusClearancePartial()).toMatch(/:where\(\.main :not\([^)]*\.filter-menu \*[^)]*\)\)/);
  });

  // The rendered proofs that the two conditions agree (the Ask tab clears the
  // nav once; the Workflows tab keeps this rule) are in lead-queue.fixture.spec.ts.
  it('leaves /ask-genie to its own .main scroll-padding while its Ask tab shows', () => {
    const genie = featureStylesheets().find((entry) => entry.file === 'src/routes/ask-genie.css');
    expect(genie?.css).toMatch(new RegExp(String.raw`\.main:has\(${ASK_TAB}\)\s*\{\s*scroll-padding-block-start`));
  });
});

describe('row-expand motion (motion-08 slice 1, queue part)', () => {
  it('turns one chevron a quarter on the expanded row, on --dur-fast / --ease', () => {
    const css = leadTableCss();
    expect(block(css, '.lead-table__chevron')).toMatch(/transition:\s*rotate\s+var\(--dur-fast\)\s+var\(--ease\);/);
    expect(block(css, '.lead-table__table tr.is-expanded .lead-table__chevron')).toMatch(/rotate:\s*90deg;/);
  });

  it('fades the expanded preview in with opacity only: no height, so row measurements stay put', () => {
    const css = leadTableCss();
    expect(block(css, '.lead-table__table .tbl__expand-inner')).toMatch(/^\s*transition:\s*opacity\s+var\(--dur-fast\)\s+var\(--ease\);\s*$/);
    const starting = /@starting-style\s*\{\s*\.lead-table__table \.tbl__expand-inner\s*\{([^}]*)\}\s*\}/.exec(css);
    expect(starting, '@starting-style entry').not.toBeNull();
    expect(starting![1].trim()).toBe('opacity: 0;');
  });

  it('switches both transitions off under prefers-reduced-motion', () => {
    const reduced = /@media\s*\(prefers-reduced-motion:\s*reduce\)\s*\{\s*\.lead-table__chevron,\s*\.lead-table__table \.tbl__expand-inner\s*\{\s*transition:\s*none;\s*\}\s*\}/;
    expect(leadTableCss()).toMatch(reduced);
  });
});
