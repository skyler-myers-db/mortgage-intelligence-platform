/**
 * Source contracts for the three OS contrast modes (2026-09-21 audit css-06,
 * a11y-10, responsive-v3). The rendered proofs are
 * tests/e2e/fixture/css-hygiene.modes.fixture.spec.ts.
 *
 *  - forced-colors: active. Interactive states take a Highlight fill and
 *    paint HighlightText on themselves and everything in them, with forcing
 *    off (`forced-color-adjust: none`): Firefox paints no Canvas backplate
 *    behind forced text, so a forced ink sat on Highlight at 1.18:1, and with
 *    forcing on Chromium's backplate hid a HighlightText label. Otherwise
 *    `forced-color-adjust: none` is reserved for non-text data marks (and the
 *    switch knob), each edged in a system colour; every colour a forced-colors
 *    block writes is a system keyword (or, on a data mark, its ramp / segment
 *    token). The painted proofs are css-hygiene.forced-ink.fixture.spec.ts
 *    (Chromium) and forced-colors.firefox.fixture.spec.ts (Firefox, CI).
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

/** The selectors of a selector list, split only at commas outside every parenthesis. */
function selectorList(list: string): string[] {
  const parts: string[] = [];
  let depth = 0;
  let from = 0;
  for (let i = 0; i < list.length; i += 1) {
    if (list[i] === '(') depth += 1;
    else if (list[i] === ')') depth -= 1;
    else if (list[i] === ',' && depth === 0) {
      parts.push(list.slice(from, i).trim());
      from = i + 1;
    }
  }
  return [...parts, list.slice(from).trim()];
}

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
// `inset` and `calc(var(--sp-N) / N)`: the offer-mix slice gap (a box-shadow
// on a data mark, HomeAnswerBand.css; D-dataviz-geo-c2).
const NON_COLOR_TOKEN =
  /^(?:none|solid|dashed|dotted|double|inset|0|-?\d*\.?\d+(?:px|rem|em)|var\(--focus-ring-width\)|calc\(var\(--sp-\d+\) \/ \d+\)|!important)$/;

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
  '.chart-hist__bar',
  '.chart-hist__rule',
  '.analytics-scatter__bin',
  '.analytics-scatter__dot--band',
  // The Home offer-mix bar and legend swatches (D-dataviz-geo-c2): segment
  // hues on a Canvas track edged in CanvasText.
  '.offer-mix',
  '.offer-mix__seg',
  '.offer-mix__swatch',
];

/** The Highlight-filled interactive states that paint the system pair themselves (W5b, Firefox backplate). */
const HIGHLIGHT_STATES = [
  '.segmented.segmented button.is-active',
  '.filter.is-active',
  '.rail__item.is-active',
  '.topbar__icon-btn.is-active',
  '.drawer__tab.is-active',
  '.proof-tab.is-active',
  '.cmdk__row.is-active',
  '.filter-menu__item:is(.is-focused, .is-selected)',
  '.topbar__search-result.is-active',
  '.layout-tabs.layout-tabs button.is-active',
];

describe('forced-colors: active (css-06 / a11y-10 / responsive-v3)', () => {
  const declarations = mediaDeclarations(FORCED);

  it('opts only non-text data marks, the switch knob and the Highlight states out of forcing', () => {
    const optedOut = declarations
      .filter((d) => d.property === 'forced-color-adjust' && d.value === 'none')
      .flatMap((d) => d.selector.split(/,(?![^(]*\))/).map((part) => part.trim()));
    const allowed = [...DATA_MARKS, ...HIGHLIGHT_STATES];
    expect(optedOut.length).toBeGreaterThan(0);
    expect(optedOut.filter((selector) => !allowed.includes(selector)), 'forced-color-adjust: none off the allowed list').toEqual([]);
    expect(new Set(optedOut)).toEqual(new Set(allowed));
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

  it('fills every active / selected / on state with Highlight, with forcing off on the text-bearing ones', () => {
    const highlighted = declarations
      .filter((d) => d.property === 'background-color' && d.value === 'Highlight')
      .flatMap((d) => d.selector.split(/,(?![^(]*\))/).map((part) => part.trim()));
    expect(highlighted).toEqual(
      expect.arrayContaining([
        '.segmented.segmented button.is-active',
        '.switch.switch.on',
        '.filter.is-active',
        '.rail__item.is-active',
        '.topbar__icon-btn.is-active',
        '.drawer__tab.is-active',
        '.proof-tab.is-active',
        '.cmdk__row.is-active',
        '.filter-menu__item:is(.is-focused, .is-selected)',
        '.topbar__search-result.is-active',
        '.layout-tabs.layout-tabs button.is-active',
      ]),
    );
    const rule = declarations.filter((d) => selectorList(d.selector).includes('.drawer__tab.is-active'));
    expect(Object.fromEntries(rule.map((d) => [d.property, d.value]))).toEqual({
      'background-color': 'Highlight',
      'border-color': 'Highlight',
      'forced-color-adjust': 'none',
    });
  });

  // Chromium paints a Canvas backplate behind every forced text run, so a
  // HighlightText label painted as a solid Canvas box (round-2 review);
  // Firefox paints none, so a forced ink sat on Highlight at 1.18:1 (W5a CI).
  // HighlightText text is therefore only right where forcing is off.
  it('inks text HighlightText only inside a state that opts out of forcing', () => {
    const unDoubled = (selector: string) => selector.replace(/(\.[\w-]+)\1(?![\w-])/g, '$1');
    const optedOut = new Set(HIGHLIGHT_STATES.map(unDoubled));
    const inked = declarations.filter((d) => d.property === 'color' && /^HighlightText\b/.test(d.value));
    expect(inked.length).toBeGreaterThan(0);
    const stray = inked
      .flatMap((d) => selectorList(d.selector))
      .flatMap((selector) => {
        const subject = selector.replace(/ (\*:not\(svg \*\)|svg)$/, '');
        const grouped = /^:is\((.*)\)([^()]*)$/.exec(subject);
        return grouped ? selectorList(grouped[1]).map((part) => unDoubled(part + grouped[2])) : [unDoubled(subject)];
      })
      .filter((subject) => !optedOut.has(subject));
    expect(stray, 'a subject inked HighlightText with forcing on').toEqual([]);
  });

  it('inks exactly the Highlight states and everything in them, over any author rule', () => {
    const unDoubled = (selector: string) => selector.replace(/(\.[\w-]+)\1(?![\w-])/g, '$1');
    // A switch is a childless button: its knob is ::after, set by the knob rule.
    const states = declarations
      .filter((d) => d.property === 'background-color' && d.value === 'Highlight')
      .flatMap((d) => selectorList(d.selector).map((part) => unDoubled(part)))
      .filter((state) => state !== '.switch.on' && !state.endsWith('::before'));
    const inkRules = declarations.filter((d) => d.property === 'color' && d.value === 'HighlightText !important');
    expect(inkRules).toHaveLength(1);
    // `:is(A, B).is-active` and `:is(A, B).is-active *:not(svg *)` cover `A.is-active` and `B.is-active`, self and descendants.
    const subjects = selectorList(inkRules[0]?.selector ?? '').map((selector) => {
      const subject = selector.replace(/ \*:not\(svg \*\)$/, '');
      return { subject, descendants: subject !== selector };
    });
    const expand = (subject: string) => {
      const grouped = /^:is\((.*)\)([^()]*)$/.exec(subject);
      return grouped ? selectorList(grouped[1]).map((part) => part + grouped[2]) : [subject];
    };
    const self = subjects.filter((s) => !s.descendants).flatMap((s) => expand(s.subject));
    const inside = subjects.filter((s) => s.descendants).flatMap((s) => expand(s.subject));
    expect(states.length).toBeGreaterThanOrEqual(10);
    expect(new Set(self)).toEqual(new Set(states));
    expect(new Set(inside)).toEqual(new Set(states));
    expect(new Set(states)).toEqual(new Set(HIGHLIGHT_STATES.map(unDoubled)));
  });

  it("returns a nested control's glyph to ButtonText on its forced hover and focus fill", () => {
    // `.filter__remove:hover` / `:focus-visible` fill with --bg-3, which is
    // forced to ButtonFace: a HighlightText cross vanished on it (1:1).
    // The remove button keeps forcing inside the unforced chip, so that fill
    // stays the forced ButtonFace the ButtonText glyph is chosen for.
    const carveOut = declarations.filter((d) => d.selector.includes('.filter__remove'));
    expect(carveOut.map((d) => [d.file, d.selector, d.property, d.value])).toEqual([
      ['design-system/components.css (partials)', '.filter.is-active .filter__remove', 'forced-color-adjust', 'auto'],
      ['src/routes/lead-queue.css', '.filter.is-active .filter__remove:is(:hover, :focus-visible) svg', 'color', 'ButtonText !important'],
    ]);
  });

  it('keeps the route nav current-page indicator in Highlight and paints no line under idle links', () => {
    const nav = declarations.filter((d) => d.selector.startsWith('.route-nav__link'));
    expect(nav.map((d) => [d.selector, d.property, d.value])).toEqual([
      ['.route-nav__link', 'border-block-end-color', 'Canvas'],
      ['.route-nav__link[aria-current="page"]', 'border-block-end-color', 'Highlight'],
    ]);
  });

  it('rings the listbox cursor in HighlightText, since its inset ring sits on the shared fill', () => {
    const cursor = declarations.filter((d) => d.selector === '.filter-menu__item.is-focused');
    expect(cursor.map((d) => [d.property, d.value])).toEqual([['--focus-ring-color', 'HighlightText']]);
  });

  it('keeps the score band as a border style, since score chips carry text', () => {
    const style = (selector: string) => declarations.find((d) => d.selector === selector && d.property === 'border-style')?.value;
    expect([style('.score.score--high'), style('.score.score--med'), style('.score.score--low')]).toEqual(['solid', 'dashed', 'dotted']);
    expect(declarations.some((d) => d.selector.includes('.score') && d.property === 'forced-color-adjust')).toBe(false);
  });

  it('scopes the chip band rules to the chip, so a scatter mark carrying .score--* keeps its own cue', () => {
    // An unscoped `.score--high` also hit the scatter's density cells, dots
    // and cluster markers, which carry the band classes (css-hygiene review).
    const bandSubjects = declarations
      .flatMap((d) => selectorList(d.selector))
      .filter((selector) => /\.score--(?:high|med|low)\b/.test(selector));
    const unscoped = bandSubjects.filter(
      (selector) => !/^\.score\.score--/.test(selector) && !/analytics-scatter__(?:bin|dot--band|cluster-marker)/.test(selector),
    );
    expect(unscoped, 'a band rule outside the chip and the scatter marks').toEqual([]);
  });

  it('gives each scatter band a distinct (fill, edge style, edge width) cue, and the cluster markers an explicit width', () => {
    // The band fills read --scatter-score-*, which the forced block points at
    // system colours; med and low add a distinct edge, high keeps none.
    const panel = Object.fromEntries(
      declarations.filter((d) => d.selector === '.analytics-chart-panel--scatter').map((d) => [d.property, d.value]),
    );
    expect(panel).toEqual({ '--scatter-score-high': 'CanvasText', '--scatter-score-med': 'Canvas', '--scatter-score-low': 'Canvas' });
    const edge = (band: string) =>
      declarations.find((d) => d.selector === `:is(.analytics-scatter__bin, .analytics-scatter__dot--band).score--${band}`)?.value;
    expect([edge('high'), edge('med'), edge('low')]).toEqual([undefined, '2px solid CanvasText', '1px dashed CanvasText']);
    const marker = declarations.filter((d) => d.selector.startsWith('.analytics-scatter__cluster-marker'));
    expect(marker.map((d) => [d.selector, d.property, d.value])).toEqual([
      ['.analytics-scatter__cluster-marker', 'border-width', '2px'],
      ['.analytics-scatter__cluster-marker.score--med', 'border-style', 'dashed'],
      ['.analytics-scatter__cluster-marker.score--low', 'border-style', 'dotted'],
    ]);
  });

  it('draws a confidence bar on and off in different fills AND edge styles', () => {
    const rule = (selector: string) =>
      Object.fromEntries(declarations.filter((d) => d.selector === selector).map((d) => [d.property, d.value]));
    expect(rule('.conf__bar')).toEqual({ 'forced-color-adjust': 'none', 'background-color': 'Canvas', border: '1px dashed GrayText' });
    expect(rule('.conf__bar.conf__bar.on')).toEqual({ 'background-color': 'CanvasText', 'border-style': 'solid', 'border-color': 'CanvasText' });
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
