import { describe, expect, it } from 'vitest';
// @ts-expect-error Frontend app types intentionally exclude Node globals; this
// unit test reads the print stylesheet text under Vitest only.
import { readFileSync } from 'node:fs';
import { designCss } from '../test/designCss';

const printCss = () => readFileSync(
  new URL('./print.css', import.meta.url),
  'utf8',
);

/**
 * Audit motion-06: scroll-revealed blocks sit at opacity 0 until they enter
 * the viewport, so a print taken before scrolling left the trigger timeline
 * and offer details blank on paper. The print sheet must force the revealed
 * state, and it must beat the screen rule's own `opacity: 0` regardless of
 * cascade order, hence `!important`.
 */
describe('print.css reveal contract', () => {
  it('forces scroll-revealed blocks visible inside @media print', () => {
    const css = printCss();
    const printBlock = /@media print\s*\{([\s\S]*)\}\s*$/.exec(css)?.[1] ?? '';

    expect(printBlock).toMatch(/\.reveal-on-scroll\s*\{[^}]*opacity:\s*1\s*!important;/s);
    expect(printBlock).toMatch(/\.reveal-on-scroll\s*\{[^}]*transform:\s*none\s*!important;/s);
  });

  it('overrides the screen sheet, which still starts the block hidden', () => {
    expect(designCss()).toMatch(/\.reveal-on-scroll\s*\{[^}]*opacity:\s*0;/s);
    // No standing compositor layer for a one-shot fade (motion-06).
    expect(designCss()).not.toMatch(/\.reveal-on-scroll\s*\{[^}]*will-change/s);
  });
});

/**
 * responsive-08 (W5d): the print path. The stylesheet had linearized grids
 * that do not exist (`.grid-2`, `.layout-2`, `.cols-2`, `.confidence`,
 * `.fab`) while the real ones stayed multi-column; dark segment ink printed
 * pastel on white; overlays and the consented refusal text could reach
 * paper. Rendered proof: tests/e2e/fixture/print-glossary-sales-manager.
 * fixture.spec.ts (emulateMedia print, both themes).
 */
const stripComments = (css: string) => css.replace(/\/\*[\s\S]*?\*\//g, '');

/** The selector lists of every rule whose declarations include `display: none`. */
function hiddenSelectors(css: string): string[] {
  const hidden: string[] = [];
  for (const match of stripComments(css).matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    if (/display:\s*none/.test(match[2])) hidden.push(...match[1].split(',').map((part) => part.trim()));
  }
  return hidden;
}

describe('print.css paper contract (responsive-08)', () => {
  it('keeps no dead selector: none of these classes exists in the app', () => {
    const css = stripComments(printCss());
    for (const dead of ['grid', 'grid-2', 'grid-3', 'layout-2', 'layout-3', 'cols-2', 'cols-3', 'fab', 'confidence']) {
      expect(css, `.${dead}`).not.toMatch(new RegExp(`\\.${dead}(?![\\w-])`));
    }
  });

  it('remaps every --seg-* token tokens.css declares (a new segment cannot print pastel)', () => {
    const tokens = readFileSync(new URL('./tokens.css', import.meta.url), 'utf8') as string;
    const declared = new Set([...stripComments(tokens).matchAll(/(--seg-[\w-]+)\s*:/g)].map((match) => match[1]));
    const remapped = new Set([...stripComments(printCss()).matchAll(/(--seg-[\w-]+)\s*:\s*CanvasText\s*;/g)].map((match) => match[1]));
    expect(declared.size).toBeGreaterThan(0);
    expect([...declared].filter((name) => !remapped.has(name))).toEqual([]);
  });

  it('hides the overlays, the toasts and the refusal reports on paper', () => {
    const hidden = hiddenSelectors(printCss());
    for (const selector of ['.genie__fab', '.cmdk', '.evidence-hovercard', '.toast-region', '#refusal-reports']) {
      expect(hidden, selector).toContain(selector);
    }
  });

  it('shows the provenance block that the screen keeps hidden, and unclips the tables', () => {
    const css = stripComments(printCss());
    expect(css).toMatch(/:root \.print-provenance\[hidden\]\s*\{[^}]*display:\s*block;/s);
    expect(css).toMatch(/:root \.tbl-wrap,[^{]*:root \.admin-audit-list\s*\{[^}]*overflow:\s*visible;/s);
    expect(css).toMatch(/@bottom-right\s*\{[^}]*counter\(page\)[^}]*counter\(pages\)/s);
  });
});
