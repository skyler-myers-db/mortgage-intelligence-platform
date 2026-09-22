/**
 * Source-level contracts between tokens.css and the component partials:
 *
 *  - css-v1 / a11y-01: every visible focus ring in the partials consumes the
 *    shared `--focus-ring-*` tokens, so the ring is one system and its
 *    colour is the AA-tuned `--accent-ink` in every theme x accent.
 */
import { describe, expect, it } from 'vitest';
import { designCss } from '../test/designCss';
import { readTokensCss } from '../test/tokenCascade';

const tokens = readTokensCss();
const components = designCss();

describe('focus ring is one system (css-v1 / a11y-01)', () => {
  it('routes the global :focus-visible ring through the focus-ring tokens', () => {
    expect(tokens).toMatch(
      /:focus-visible\s*\{[^}]*outline:\s*var\(--focus-ring-width\)\s+solid\s+var\(--focus-ring-color\);[^}]*outline-offset:\s*var\(--focus-ring-offset\);/s,
    );
  });

  it('never paints a bespoke ring colour in a component partial', () => {
    const bespoke: string[] = [];
    for (const match of components.matchAll(/outline:\s*([^;]*?)\s*;/g)) {
      const value = match[1];
      if (/^(none|0)$/.test(value)) continue;
      if (!/var\(--focus-ring-color\)/.test(value)) bespoke.push(value);
    }
    expect(bespoke).toEqual([]);
  });
});
