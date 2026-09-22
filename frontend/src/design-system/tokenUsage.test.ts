/**
 * Source-level contracts between tokens.css and the component partials:
 *
 *  - css-v1 / a11y-01: every visible focus ring in the partials consumes the
 *    shared `--focus-ring-*` tokens, so the ring is one system and its
 *    colour is the AA-tuned `--accent-ink` in every theme x accent.
 *  - motion-04: every `var(--dur-*)` / `var(--ease-*)` the partials reference
 *    is defined, so no transition is voided at computed-value time
 *    (`--ease-standard` was referenced three times and never defined).
 *  - visual-03 / css-02: `color-scheme` is declared per theme and forced
 *    light for print, where print.css relies on Canvas / CanvasText.
 */
import { describe, expect, it } from 'vitest';
import { designCss } from '../test/designCss';
import { TokenCascade, readTokensCss } from '../test/tokenCascade';

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

describe('motion tokens resolve (motion-04)', () => {
  it('defines --ease-standard as an alias of the base curve', () => {
    const cascade = new TokenCascade(tokens, { theme: 'dark', accent: 'bright' });
    expect(cascade.raw('--ease-standard')).toBe('var(--ease)');
    expect(cascade.resolve('--ease-standard')).toBe(cascade.resolve('--ease'));
  });

  it('resolves every duration and easing token the partials reference', () => {
    const cascade = new TokenCascade(tokens, { theme: 'dark', accent: 'bright' });
    const referenced = new Set<string>();
    for (const match of components.matchAll(/var\((--(?:dur|ease)(?:-[a-z0-9]+)*)\)/g)) referenced.add(match[1]);
    expect(referenced.has('--ease-standard'), 'the partials still reference the alias').toBe(true);
    const unresolved = [...referenced].filter((name) => cascade.raw(name) === undefined);
    expect(unresolved, 'referenced in a partial but never defined in tokens.css').toEqual([]);
  });
});

describe('native control scheme (visual-03 / css-02)', () => {
  it('declares color-scheme for both themes and forces light in print', () => {
    expect(tokens).toMatch(/:root,\s*\[data-theme="dark"\]\s*\{\s*color-scheme:\s*dark;\s*\}/);
    expect(tokens).toMatch(/\[data-theme="light"\]\s*\{\s*color-scheme:\s*light;\s*\}/);
    expect(tokens).toMatch(/@media print\s*\{[^}]*color-scheme:\s*light;/s);
  });
});
