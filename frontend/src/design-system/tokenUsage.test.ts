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
 *  - a11y-01: no partial paints text or a glyph with the amber fill hue
 *    (`color: var(--signal-warning)`, 2.15:1 on the light surfaces); warning
 *    copy and amber icons consume `--signal-warning-ink`, the active
 *    evidence-drawer tab consumes `--accent-ink`, not `--accent`, and the
 *    three text inputs no longer switch the shared focus ring off.
 *  - responsive-02: the light theme is carried by the tokens (light
 *    `--status-*-ink`, `--signal-*`), not by `[data-theme="light"]` rules in
 *    the partials that shadow them per selector (twelve were retired).
 *  - css-01 (print): every property a `[data-theme][data-accent]` compound
 *    sets is reset inside `@media print` to print.css's monochrome value,
 *    because the (0,2,0) compounds outrank print.css's (0,1,0) remap.
 */
import { describe, expect, it } from 'vitest';
import { designCss } from '../test/designCss';
import { TokenCascade, mediaBlocks, readPrintCss, readTokensCss, topLevelRules } from '../test/tokenCascade';

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

/** Custom-property declarations of one rule block, by name. */
function customProperties(block: string): Map<string, string> {
  const found = new Map<string, string>();
  for (const part of block.split(';').map((p) => p.trim())) {
    if (!part.startsWith('--')) continue;
    const colon = part.indexOf(':');
    found.set(part.slice(0, colon).trim(), part.slice(colon + 1).trim());
  }
  return found;
}

describe('print keeps the accent family monochrome (css-01)', () => {
  // print.css remaps the accent family at (0,1,0) and never sets
  // --accent-ink; a (0,2,0) theme x accent compound outranks it, so dark +
  // navy printed accent-ink #66C5FF on white paper (1.91:1) until the reset.
  const COMPOUND = /^\[data-theme="[a-z]+"\]\[data-accent="[a-z]+"\]$/;
  const compoundProperties = new Set(
    topLevelRules(tokens)
      .filter((rule) => COMPOUND.test(rule.selector))
      .flatMap((rule) => [...customProperties(rule.block).keys()]),
  );
  const resets = mediaBlocks(tokens, 'print')
    .flatMap((block) => topLevelRules(block))
    .filter((rule) => rule.selector === '[data-theme][data-accent]');

  it('resets every property a theme x accent compound sets, inside @media print', () => {
    expect([...compoundProperties]).toEqual(expect.arrayContaining(['--accent', '--accent-ink', '--chip-text']));
    expect(resets, 'one [data-theme][data-accent] block inside @media print').toHaveLength(1);
    const reset = customProperties(resets[0].block);
    expect([...compoundProperties].filter((name) => !reset.has(name)), 'set by a compound, not reset for print').toEqual([]);
  });

  it('resets them to the values print.css remaps them to', () => {
    const printRemap = new Map(
      mediaBlocks(readPrintCss(), 'print')
        .flatMap((block) => topLevelRules(block))
        .filter((rule) => rule.selector.includes(':root'))
        .flatMap((rule) => [...customProperties(rule.block)]),
    );
    expect(printRemap.get('--accent')).toBe('CanvasText');
    for (const [name, value] of customProperties(resets[0]?.block ?? '')) {
      // print.css has no --accent-ink; it prints like --accent.
      expect(value, name).toBe(printRemap.get(name) ?? printRemap.get('--accent'));
    }
  });
});

/** `selector { block }` pairs of the expanded component CSS (nested blocks are not modelled; none carry `color:`). */
function rules(css: string): Array<{ selector: string; block: string }> {
  const text = css.replace(/\/\*[\s\S]*?\*\//g, '');
  return [...text.matchAll(/([^{}]+)\{([^{}]*)\}/g)].map((match) => ({
    selector: match[1].trim().replace(/\s+/g, ' '),
    block: match[2],
  }));
}

describe('SVG focus strokes use the ring colour (css-v1)', () => {
  // `outline` is unreliable on SVG <g>, so the funnel Sankey nodes draw
  // their focus ring as a stroke; it painted --accent (1.9:1 on white in
  // light + bright). The map region pairs its token outline with a --text-1
  // edge, which is a second cue, not the ring.
  it('paints every :focus-visible stroke with --focus-ring-color or a text token', () => {
    const strokes = rules(components)
      .filter((rule) => rule.selector.includes(':focus-visible'))
      .flatMap((rule) =>
        [...rule.block.matchAll(/(?<![-\w])stroke:\s*([^;]+);/g)].map((m) => ({ selector: rule.selector, value: m[1].trim() })),
      );
    expect(strokes.map((s) => s.selector)).toContain('.funnel-sankey__node:focus-visible .funnel-sankey__bar');
    for (const stroke of strokes) {
      expect(stroke.value, stroke.selector).toMatch(/^var\(--(?:focus-ring-color|text-[1-2])\)$/);
    }
    const sankey = strokes.find((s) => s.selector === '.funnel-sankey__node:focus-visible .funnel-sankey__bar');
    expect(sankey?.value).toBe('var(--focus-ring-color)');
  });
});

describe('warning copy and glyphs use the ink token (a11y-01)', () => {
  it('never paints text or a glyph with the amber fill hue', () => {
    // `color:` only: background, border, box-shadow and color-mix() tints keep
    // the prototype amber. The lookbehind excludes border-color / background-color.
    const offenders = rules(components)
      .filter((rule) => /(?<![-\w])color:\s*var\(--signal-warning\)/.test(rule.block))
      .map((rule) => rule.selector);
    expect(offenders, 'use var(--signal-warning-ink) for text and icon glyphs').toEqual([]);
  });

  it('consumes --signal-warning-ink at every warning text and glyph site', () => {
    const consumers = rules(components)
      .filter((rule) => /(?<![-\w])color:\s*var\(--signal-warning-ink\)/.test(rule.block))
      .map((rule) => rule.selector);
    expect(consumers).toEqual(
      expect.arrayContaining([
        '.topbar__search-status--error',
        '.seg-card__meta--pending',
        '.bulk-actions__toast--warn',
        '.genie-history__state--error',
        '.genie-proof__gap',
        '.approval__ico',
        '.degraded-banner__ico',
        '.audit__ico.amber',
      ]),
    );
  });

  it('paints the active evidence-drawer tab with --accent-ink', () => {
    const active = rules(components).find((rule) => rule.selector === '.drawer__tab.is-active');
    expect(active?.block).toMatch(/(?<![-\w])color:\s*var\(--accent-ink\)/);
  });
});

describe('text inputs keep the shared focus ring (a11y-01)', () => {
  // These rules outrank the global `:focus-visible` (tokens.css), so an
  // `outline: none` in them removed the ring and left only the prototype's
  // 1px `--accent` border swap (design_files/index.html:770-772), 1.9:1 in
  // light + bright and invisible under forced colours.
  const TEXT_INPUTS = ['.genie__input input', '.admin-filter-input', '.form-input'];

  it('never switches the outline off on a text-entry control', () => {
    const all = rules(components);
    for (const selector of TEXT_INPUTS) {
      const own = all.filter((rule) => rule.selector.split(',').map((part) => part.trim()).includes(selector));
      expect(own.length, `${selector} is still styled in the partials`).toBeGreaterThan(0);
      const off = own.filter((rule) => /(?<![-\w])outline:\s*(?:none|0)(?![\w.%])/.test(rule.block));
      expect(off.map((rule) => rule.selector), `${selector} must keep the global focus ring`).toEqual([]);
    }
  });
});

describe('light theme is carried by tokens, not per-selector overrides (responsive-02)', () => {
  it('has no [data-theme="light"] rule that repaints a status or brand hue', () => {
    // These shadowed the token-level light inks (e.g. .chip--warning navy
    // where tokens.css and the prototype, design_files/index.html:416, say
    // #B45309). A light-only rule may still remap to --text-* / --accent-ink.
    const offenders = rules(components)
      .filter((rule) => /\[data-theme="light"\]/.test(rule.selector))
      .filter((rule) => /(?<![-\w])color:\s*var\(--(?:signal-|status-|entrada-)/.test(rule.block))
      .map((rule) => rule.selector);
    expect(offenders, 'move the light value into tokens.css instead').toEqual([]);
  });

  it('keeps the remaining light-only colour remaps on the text and accent-ink tokens', () => {
    const remaining = rules(components)
      .filter((rule) => /\[data-theme="light"\]/.test(rule.selector))
      .flatMap((rule) => [...rule.block.matchAll(/(?<![-\w])color:\s*([^;]+);/g)].map((m) => m[1].trim()));
    expect(remaining.length).toBeGreaterThan(0);
    for (const value of remaining) expect(value).toMatch(/^var\(--(?:text-[1-4]|accent-ink)\)$/);
  });
});
