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
