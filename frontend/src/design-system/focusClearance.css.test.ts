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
 *     C43): `.tbl-wrap` clears its sticky thead, the Lead Queue scroller
 *     clears the pinned Approval column, and `.main` clears the sticky route
 *     nav inside the same 40rem condition that docks it. Rendered proof: the
 *     focus-obscured walk in tests/e2e/fixture/lead-queue.fixture.spec.ts.
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

/** The body of the first rule whose selector list is exactly `selector`. */
function block(css: string, selector: string): string {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const match = new RegExp(`(?:^|[}\\n])\\s*${escaped}\\s*\\{([^}]*)\\}`).exec(css);
  expect(match, `${selector} is declared`).not.toBeNull();
  return match![1];
}

const RING = String.raw`\+\s*var\(--focus-ring-width\)\s*\+\s*var\(--focus-ring-offset\)`;

describe('focus clearance (a11y-v2)', () => {
  it('imports 38-focus-clearance.css exactly once, right after 37', () => {
    const entry = readFileSync(`${process.cwd()}/src/design-system/components.css`, 'utf8') as string;
    const imports = entry.split('\n').filter((line) => line.startsWith('@import'));
    const at37 = imports.findIndex((line) => line.includes('37-topbar-status.css'));
    expect(imports.filter((line) => line.includes('38-focus-clearance.css'))).toHaveLength(1);
    expect(imports[at37 + 1]).toBe('@import "./components/38-focus-clearance.css";');
  });

  it('.tbl-wrap clears its sticky thead: the measured size, one row until measured, plus the ring', () => {
    const wrap = block(components(), '.tbl-wrap');
    expect(wrap).toMatch(/--tbl-head-block-size:\s*var\(--row-h\);/);
    expect(wrap).toMatch(new RegExp(String.raw`scroll-padding-block-start:\s*calc\(\s*var\(--tbl-head-block-size\)\s*${RING}\s*\);`));
  });

  it('the Lead Queue scroller clears the pinned Approval column at its inline end', () => {
    const pin = block(leadTableCss(), '.tbl-wrap:has(> .lead-table__table)');
    expect(pin).toMatch(/--tbl-pin-inline-size:\s*0px;/);
    expect(pin).toMatch(new RegExp(String.raw`scroll-padding-inline-end:\s*calc\(\s*var\(--tbl-pin-inline-size\)\s*${RING}\s*\);`));
  });

  it('.main clears the sticky route nav only inside the 40rem condition that docks it', () => {
    const css = components();
    const media = /@media\s*\(min-height:\s*40rem\)\s*\{\s*\.main:has\(\.route-nav\)\s*\{([^}]*)\}\s*\}/.exec(css);
    expect(media, 'the .main rule sits inside @media (min-height: 40rem)').not.toBeNull();
    expect(media![1]).toMatch(/--route-nav-block-size:\s*calc\(var\(--sp-3\)\s*\*\s*2\s*\+\s*var\(--sp-8\)\s*\+\s*1px\);/);
    expect(media![1]).toMatch(new RegExp(String.raw`scroll-padding-block-start:\s*calc\(\s*var\(--route-nav-block-size\)\s*${RING}\s*\);`));
    // The sticky nav is docked by the same condition (01-app-shell.css).
    expect(css).toMatch(/@media\s*\(min-height:\s*40rem\)\s*\{\s*\.route-nav\s*\{[^}]*position:\s*sticky;/);
    // Nowhere else does a rule give `.main:has(.route-nav)` scroll-padding.
    expect(css.match(/\.main:has\(\.route-nav\)/g)).toHaveLength(1);
  });

  it('leaves /ask-genie its own, more specific .main scroll-padding', () => {
    const genie = featureStylesheets().find((entry) => entry.file === 'src/routes/ask-genie.css');
    expect(genie?.css).toMatch(/\.main:has\(section\[role="tabpanel"\]:not\(\[hidden\]\) \.genie-composer\)\s*\{\s*scroll-padding-block-start/);
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
