/**
 * The CSS test helpers read cascade layers (css-05) and css-07's derived
 * tints: a token carrier inside `@layer tokens { }` still resolves, a
 * conditional block is still skipped, and `color-mix(in oklab, X p%,
 * transparent)` is X at alpha p.
 */
import { describe, expect, it } from 'vitest';
import { designCss, importedPartials, lazyPartials } from './designCss';
import { TokenCascade, parseColor, topLevelRules } from './tokenCascade';

describe('topLevelRules', () => {
  it('reads the rules of a @layer block as top level, in source order', () => {
    const css = '@layer tokens {\n:root { --a: 1; }\n@media print { :root { --a: 2; } }\n}\n.b { color: red; }\n@layer base {\nbody { margin: 0; }\n}';
    expect(topLevelRules(css).map((rule) => rule.selector)).toEqual([':root', '.b', 'body']);
  });

  it('feeds layered carriers to the token cascade', () => {
    const cascade = new TokenCascade(
      '@layer tokens {\n:root { --x: #102030; }\n[data-theme="light"] { --x: #FFFFFF; }\n}',
      { theme: 'light', accent: 'bright' },
    );
    expect(cascade.raw('--x')).toBe('#FFFFFF');
  });
});

describe('parseColor', () => {
  it('reads color-mix(in oklab, X p%, transparent) as X at alpha p', () => {
    expect(parseColor('color-mix(in oklab, #10B981 14%, transparent)')).toEqual({ r: 16, g: 185, b: 129, a: 0.14 });
    expect(parseColor('color-mix(in oklab, rgba(239, 68, 68, 0.5) 30%, transparent)')).toEqual({ r: 239, g: 68, b: 68, a: 0.15 });
  });

  it('resolves var()-supplied colours and percentages before the mix', () => {
    const cascade = new TokenCascade(
      ':root { --base: #EF4444; --step: 12%; --tint: color-mix(in oklab, var(--base) var(--step), transparent); }',
      { theme: 'dark', accent: 'bright' },
    );
    const tint = cascade.color('--tint');
    expect([tint.r, tint.g, tint.b]).toEqual([239, 68, 68]);
    expect(tint.a).toBeCloseTo(0.12, 10);
  });

  it('refuses a mix with any other second colour', () => {
    expect(parseColor('color-mix(in oklab, #10B981 14%, white)')).toBeNull();
  });
});

describe('designCss', () => {
  it('expands every partial components.css imports, then each lazy partial exactly once', () => {
    const text = designCss();
    for (const partial of [...importedPartials(), ...lazyPartials()]) {
      expect(partial).toMatch(/^(\.\/)?components\/\d+-[\w-]+\.css$/);
    }
    expect(text).not.toMatch(/^@import /m);
    expect(new Set([...importedPartials().map((path) => path.replace(/^\.\//, '')), ...lazyPartials()]).size).toBe(
      importedPartials().length + lazyPartials().length,
    );
  });
});
