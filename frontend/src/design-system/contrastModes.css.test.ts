/**
 * Source contracts for the three OS contrast modes (2026-09-21 audit css-06,
 * a11y-10, responsive-v3). The rendered proofs are
 * tests/e2e/fixture/css-hygiene.modes.fixture.spec.ts.
 *
 *  - forced-colors: active. Interactive state uses system colour pairs;
 *    `forced-color-adjust: none` is reserved for non-text data marks (and the
 *    switch knob), each edged in a system colour; every colour a forced-colors
 *    block writes is a system keyword (or, on a data mark, its ramp / segment
 *    token).
 *  - prefers-contrast: more. Raises exactly --text-3, --text-4 and
 *    --line-1..3 per theme and the ring width, to measured targets.
 *  - prefers-reduced-transparency: reduce. Every frosted surface and scrim
 *    that declares a backdrop-filter drops it.
 */
import { describe, expect, it } from 'vitest';
import { designCss } from '../test/designCss';
import { featureStylesheets } from '../test/featureCss';
import { TokenCascade, contrast, mediaBlocks, over, readTokensCss, topLevelRules } from '../test/tokenCascade';

const FORCED = '\\(forced-colors:\\s*active\\)';
const MORE_CONTRAST = '\\(prefers-contrast:\\s*more\\)';
const REDUCED_TRANSPARENCY = '\\(prefers-reduced-transparency:\\s*reduce\\)';

const tokens = readTokensCss();
const sheets = [{ file: 'design-system/components.css (partials)', css: designCss() }, ...featureStylesheets()];

const normalize = (selector: string) => selector.trim().replace(/\s+/g, ' ');

/** `{ selector, property, value }` for every declaration inside one kind of media block. */
function mediaDeclarations(query: string) {
  return sheets.flatMap(({ file, css }) =>
    mediaBlocks(css, query).flatMap((block) =>
      topLevelRules(block).flatMap((rule) =>
        rule.block
          .split(';')
          .map((part) => part.trim())
          .filter((part) => part.includes(':'))
          .map((part) => {
            const colon = part.indexOf(':');
            return {
              file,
              selector: normalize(rule.selector),
              property: part.slice(0, colon).trim(),
              value: part.slice(colon + 1).trim(),
            };
          }),
      ),
    ),
  );
}

/** Custom properties per selector of the rules inside one media query of tokens.css. */
function tokenOverrides(query: string): Map<string, string[]> {
  const found = new Map<string, string[]>();
  for (const block of mediaBlocks(tokens, query)) {
    for (const rule of topLevelRules(block)) {
      const names = rule.block
        .split(';')
        .map((part) => part.trim())
        .filter((part) => part.startsWith('--'))
        .map((part) => part.slice(0, part.indexOf(':')).trim());
      found.set(normalize(rule.selector), [...(found.get(normalize(rule.selector)) ?? []), ...names]);
    }
  }
  return found;
}

/** The css with every `@media <query> { ... }` block removed. */
function withoutMedia(css: string, query: string): string {
  const text = css.replace(/\/\*[\s\S]*?\*\//g, '');
  let out = '';
  let from = 0;
  for (const match of text.matchAll(new RegExp(`@media\\s+${query}\\s*\\{`, 'g'))) {
    const start = match.index ?? 0;
    let depth = 1;
    let j = start + match[0].length;
    while (j < text.length && depth > 0) {
      if (text[j] === '{') depth += 1;
      else if (text[j] === '}') depth -= 1;
      j += 1;
    }
    out += text.slice(from, start);
    from = j;
  }
  return out + text.slice(from);
}

const SYSTEM_COLORS = new Set([
  'Canvas', 'CanvasText', 'Highlight', 'HighlightText', 'LinkText', 'VisitedText', 'ActiveText',
  'ButtonFace', 'ButtonText', 'ButtonBorder', 'Field', 'FieldText', 'GrayText', 'Mark', 'MarkText',
  'AccentColor', 'AccentColorText', 'SelectedItem', 'SelectedItemText',
]);
const COLOR_PROPERTY =
  /^(?:color|fill|stroke|box-shadow|background(?:-color)?|outline(?:-color)?|border(?:-(?:top|right|bottom|left|block|inline)(?:-(?:start|end))?)?(?:-color)?)$/;
const NON_COLOR_TOKEN = /^(?:none|solid|dashed|dotted|double|0|-?\d*\.?\d+(?:px|rem|em)|var\(--focus-ring-width\))$/;

/** Non-text data marks allowed to opt out of forcing (brief item 8b, plus the switch knob). */
const DATA_MARKS = [
  '.switch.switch::after',
  '.conf__bar',
  '.map-region.map-region',
  '.map-legend__bar span',
  '.zip-tile',
  '.map-label',
  '.topbar__pill .dot.dot',
  '.seg-card__facet-bar',
];

describe('forced-colors: active (css-06 / a11y-10 / responsive-v3)', () => {
  const declarations = mediaDeclarations(FORCED);

  it('opts only non-text data marks and the switch knob out of forcing', () => {
    const optedOut = declarations
      .filter((d) => d.property === 'forced-color-adjust' && d.value === 'none')
      .flatMap((d) => d.selector.split(/,(?![^(]*\))/).map((part) => part.trim()));
    expect(optedOut.length).toBeGreaterThan(0);
    expect(optedOut.filter((selector) => !DATA_MARKS.includes(selector)), 'forced-color-adjust: none off the data-mark list').toEqual([]);
    expect(new Set(optedOut)).toEqual(new Set(DATA_MARKS));
  });

  it('writes only system colours inside forced-colors blocks', () => {
    const offenders = declarations
      .filter((d) => COLOR_PROPERTY.test(d.property))
      .flatMap((d) =>
        d.value
          .split(/\s+(?![^(]*\))/)
          .filter((token) => !NON_COLOR_TOKEN.test(token) && !SYSTEM_COLORS.has(token))
          .map((token) => `${d.file}: ${d.selector} { ${d.property}: ${d.value} } -> ${token}`),
      );
    expect(offenders, 'use a system colour keyword').toEqual([]);
  });

  it('paints every active / selected / on state with the Highlight pair', () => {
    const highlighted = declarations
      .filter((d) => d.property === 'background-color' && d.value === 'Highlight')
      .flatMap((d) => d.selector.split(/,(?![^(]*\))/).map((part) => part.trim()));
    expect(highlighted).toEqual(
      expect.arrayContaining([
        '.segmented button.is-active',
        '.switch.switch.on',
        '.filter.is-active',
        '.rail__item.is-active',
        '.topbar__icon-btn.is-active',
        '.drawer__tab.is-active',
        '.proof-tab.is-active',
        '.cmdk__row.is-active',
        '.filter-menu__item:is(.is-focused, .is-selected)',
        '.topbar__search-result.is-active',
      ]),
    );
    const rule = declarations.filter((d) => d.selector.includes('.drawer__tab.is-active'));
    expect(Object.fromEntries(rule.map((d) => [d.property, d.value]))).toEqual({
      'background-color': 'Highlight',
      color: 'HighlightText',
      'border-color': 'Highlight',
    });
  });

  it('keeps the score band as a border style, since score chips carry text', () => {
    const style = (selector: string) => declarations.find((d) => d.selector === selector && d.property === 'border-style')?.value;
    expect([style('.score--high'), style('.score--med'), style('.score--low')]).toEqual(['solid', 'dashed', 'dotted']);
    expect(declarations.some((d) => d.selector.includes('.score') && d.property === 'forced-color-adjust')).toBe(false);
  });

  it('routes every ring through the Highlight focus token', () => {
    expect([...tokenOverrides(FORCED)]).toEqual([[':root', ['--focus-ring-color']]]);
    expect(mediaBlocks(tokens, FORCED).join('')).toMatch(/--focus-ring-color:\s*Highlight;/);
  });
});

describe('prefers-contrast: more (css-06 / a11y-10)', () => {
  const INKS_AND_LINES = ['--text-3', '--text-4', '--line-1', '--line-2', '--line-3'];
  const boosted = tokens + '\n' + mediaBlocks(tokens, MORE_CONTRAST).join('\n');

  it('overrides exactly the tertiary inks and the hairlines per theme, plus the ring width', () => {
    const overrides = tokenOverrides(MORE_CONTRAST);
    expect([...overrides.keys()]).toEqual([':root, [data-theme="dark"]', '[data-theme="light"]', ':root']);
    expect(overrides.get(':root, [data-theme="dark"]')?.sort()).toEqual([...INKS_AND_LINES].sort());
    expect(overrides.get('[data-theme="light"]')?.sort()).toEqual([...INKS_AND_LINES].sort());
    expect(overrides.get(':root')).toEqual(['--focus-ring-width']);
  });

  for (const theme of ['dark', 'light'] as const) {
    it(`reaches the contrast targets in the ${theme} theme, each stronger than its default`, () => {
      const base = new TokenCascade(tokens, { theme, accent: 'bright' });
      const more = new TokenCascade(boosted, { theme, accent: 'bright' });
      const surfaces = ['--bg-0', '--bg-1', '--bg-2', '--bg-3'].map((name) => more.color(name));
      for (const surface of surfaces) {
        expect(contrast(more.color('--text-3'), surface), `--text-3 on ${JSON.stringify(surface)}`).toBeGreaterThanOrEqual(7);
        expect(contrast(more.color('--text-4'), surface), `--text-4 on ${JSON.stringify(surface)}`).toBeGreaterThanOrEqual(4.5);
      }
      for (const name of ['--line-2', '--line-3']) {
        for (const surface of ['--bg-1', '--bg-2'].map((bg) => more.color(bg))) {
          const line = over(more.color(name), surface);
          expect(contrast(line, surface), `${name} on ${JSON.stringify(surface)}`).toBeGreaterThanOrEqual(3);
        }
      }
      const bg1 = more.color('--bg-1');
      for (const name of INKS_AND_LINES) {
        const stronger = contrast(over(more.color(name), bg1), bg1);
        const before = contrast(over(base.color(name), bg1), bg1);
        expect(stronger, `${name} is stronger than its default`).toBeGreaterThan(before);
      }
      expect(more.raw('--focus-ring-width')).toBe('3px');
      expect(base.raw('--focus-ring-width')).toBe('2px');
    });
  }
});

describe('prefers-reduced-transparency: reduce (responsive-v3)', () => {
  it('drops the blur on every selector that declares a backdrop-filter', () => {
    const blurred = sheets.flatMap(({ file, css }) =>
      topLevelRulesDeep(withoutMedia(css, REDUCED_TRANSPARENCY))
        .filter((rule) => /backdrop-filter:\s*(?!none)/.test(rule.block))
        .flatMap((rule) => rule.selector.split(',').map((part) => ({ file, selector: normalize(part) }))),
    );
    expect(blurred.length).toBeGreaterThanOrEqual(8);
    const cleared = new Set(
      mediaDeclarations(REDUCED_TRANSPARENCY)
        .filter((d) => d.property === 'backdrop-filter' && d.value === 'none')
        .flatMap((d) => d.selector.split(',').map((part) => part.trim())),
    );
    const missing = blurred.filter((site) => !cleared.has(site.selector)).map((site) => `${site.file}: ${site.selector}`);
    expect(missing, 'a new blur site needs a reduced-transparency reset').toEqual([]);
  });

  it('makes the frosted bars opaque, not just unblurred', () => {
    const opaque = mediaDeclarations(REDUCED_TRANSPARENCY)
      .filter((d) => d.property === 'background' && d.value === 'var(--bg-1)')
      .flatMap((d) => d.selector.split(',').map((part) => part.trim()));
    expect(opaque.sort()).toEqual(['.map-legend', '.offer-action-bar', '.route-nav', '.topbar']);
  });
});

/** Innermost `selector { block }` pairs, whatever at-rule wraps them. */
function topLevelRulesDeep(css: string): Array<{ selector: string; block: string }> {
  return [...css.matchAll(/([^{}]+)\{([^{}]*)\}/g)].map((match) => ({ selector: match[1].trim(), block: match[2] }));
}
