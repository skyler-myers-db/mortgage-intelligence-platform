import { describe, expect, it } from 'vitest';
// @ts-expect-error Frontend app types intentionally exclude Node globals; this
// unit test reads the design-system CSS text under Vitest only.
import { readFileSync } from 'node:fs';
// @ts-expect-error see node:fs note above.
import { join } from 'node:path';
import { designCss } from '../test/designCss';

declare const process: { cwd(): string };

const partial = (): string =>
  readFileSync(
    join(process.cwd(), 'src', 'design-system', 'components', '16-genie-fab-status.css'),
    'utf8',
  );

/** Declarations only: comments cite prototype line numbers and pixel sizes. */
const declarations = (): string => partial().replace(/\/\*[\s\S]*?\*\//g, '');

describe('Genie launcher status partial (audit 2026-09-21 runtime-01 / genie-02)', () => {
  it('is part of the shipped component cascade', () => {
    expect(designCss()).toContain('.genie__fab.is-genie-running::after');
  });

  it('styles both launchers for both states', () => {
    const css = declarations();
    for (const launcher of ['.genie__fab', '.topbar__icon-btn']) {
      expect(css).toContain(`${launcher}.is-genie-running::after`);
      expect(css).toContain(`${launcher}.is-genie-ready::after`);
    }
    // The topbar toggle is not positioned by the prototype; the pseudo-element
    // needs a containing block there. `.genie__fab` is already `fixed`.
    expect(css).toMatch(/\.topbar__icon-btn\.is-genie-ready\s*\{\s*position:\s*relative;/);
  });

  it('stops both animations under prefers-reduced-motion', () => {
    const css = declarations();
    const reduced = css.match(/@media \(prefers-reduced-motion: reduce\)\s*\{([\s\S]*?)\n\}/);
    expect(reduced).not.toBeNull();
    const block = reduced![1];
    for (const selector of [
      '.genie__fab.is-genie-running::after',
      '.topbar__icon-btn.is-genie-running::after',
      '.genie__fab.is-genie-ready::after',
      '.topbar__icon-btn.is-genie-ready::after',
    ]) {
      expect(block).toContain(selector);
    }
    expect(block).toMatch(/animation:\s*none;/);
  });

  it('uses design tokens only: no raw pixel lengths and no color literals', () => {
    const css = declarations();
    expect(css).not.toMatch(/\d+px/);
    expect(css).not.toMatch(/#[0-9a-fA-F]{3,8}\b|rgba?\(/);
  });

  it('keeps a landed answer off the panel header when scrolled to its start (motion-v2)', () => {
    expect(declarations()).toMatch(/\.genie__msg\s*\{\s*scroll-margin-block-start:\s*var\(--sp-3\);/);
  });
});
