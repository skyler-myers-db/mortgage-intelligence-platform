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
 *    the partials or the route stylesheets that shadow them per selector or
 *    through a local custom property (twelve plus the Analytics scatter's
 *    `--scatter-score-*` remap were retired).
 *  - css-01 (print): every property a `[data-theme][data-accent]` compound
 *    sets is reset inside `@media print` to print.css's monochrome value,
 *    because the (0,2,0) compounds outrank print.css's (0,1,0) remap.
 */
// @ts-expect-error Frontend app types intentionally exclude Node globals; this
// test reads the app's TSX sources under Vitest only.
import { readFileSync, readdirSync } from 'node:fs';
// @ts-expect-error see node:fs note above.
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { designCss } from '../test/designCss';
import { featureStylesheets } from '../test/featureCss';
import { TokenCascade, mediaBlocks, readPrintCss, readTokensCss, topLevelRules } from '../test/tokenCascade';

declare const process: { cwd(): string };

const tokens = readTokensCss();
const components = designCss();

function stripCssComments(css: string): string {
  return css.replace(/\/\*[\s\S]*?\*\//g, '');
}

/**
 * Custom properties a non-test .ts/.tsx under src sets: a quoted `'--x'`
 * (an inline `style` key) or `setProperty('--x', ...)`. Test files, the
 * test helpers and the Storybook/test fixtures are not the app.
 */
function tsxCustomPropertySetters(): Set<string> {
  const src = join(process.cwd(), 'src');
  const files = (readdirSync(src, { recursive: true }) as string[])
    .map((entry) => entry.split('\\').join('/'))
    .filter((entry) => /\.tsx?$/.test(entry) && !/\.test\.tsx?$/.test(entry))
    .filter((entry) => !entry.startsWith('test/') && !entry.startsWith('mocks/'));
  const found = new Set<string>();
  for (const file of files) {
    const text = readFileSync(join(src, file), 'utf8') as string;
    for (const match of text.matchAll(/['"`](--[\w-]+)['"`]/g)) found.add(match[1]);
  }
  return found;
}

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

  it('still references the alias from a partial', () => {
    expect(components).toMatch(/var\(--ease-standard\)/);
  });
});

/**
 * css-04 / motion-04: an undefined custom property voids its declaration at
 * computed-value time, silently (`var(--stroke)` dropped the ZIP reconcile
 * divider, `var(--shadow-1)` the refresh pill's shadow, and the
 * `--ease-standard` transitions snapped). Every `var(--x)` in the component
 * partials and the feature sheets must name a property some stylesheet
 * under src declares, or one the app sets from TSX (listed below, each
 * checked against a real setter so a stale entry fails too).
 */
describe('every referenced custom property resolves (css-04 / motion-04)', () => {
  /** Set on an element's inline style from TSX, and only there. */
  const TSX_SET = [
    '--bin-alpha', '--bin-h', '--bin-w', '--bin-x', '--bin-y',
    '--dot-x', '--dot-y', '--facet-share', '--hover-x', '--hover-y', '--tick-pos',
  ];
  /** Read with a fallback on purpose: TSX sets them on some elements only. */
  const TSX_SET_WITH_FALLBACK = [
    '--bar-pct', '--chip-hue', '--filter-menu-space', '--genie-composer-block-size',
    '--genie-route-nav-block-size', '--lead-table-fill-block', '--offer-action-bar-block-size',
    '--offer-action-bar-genie-clearance', '--receipt-i', '--seg-color', '--tile-i', '--ribbon-i',
  ];
  const consumers = [{ file: 'design-system/components.css (partials)', css: components }, ...featureStylesheets()];
  const declared = new Set(
    [tokens, readPrintCss(), components, ...featureStylesheets().map((sheet) => sheet.css)].flatMap((css) =>
      [...stripCssComments(css).matchAll(/(--[\w-]+)\s*:/g)].map((match) => match[1]),
    ),
  );
  const references = consumers.flatMap(({ file, css }) =>
    [...stripCssComments(css).matchAll(/var\(\s*(--[\w-]+)\s*([,)])/g)].map((match) => ({
      file,
      name: match[1],
      fallback: match[2] === ',',
    })),
  );

  it('declares every fallback-free var() somewhere, or sets it from TSX', () => {
    const missing = references
      .filter((ref) => !ref.fallback && !declared.has(ref.name) && !TSX_SET.includes(ref.name))
      .map((ref) => `${ref.file}: ${ref.name}`);
    expect(references.length).toBeGreaterThan(1000);
    expect([...new Set(missing)], 'referenced but never declared: the declaration is void').toEqual([]);
  });

  it('never leans on a fallback for a property nothing declares or sets', () => {
    // `var(--surface-2, var(--bg-1))` always painted the fallback: --surface-2
    // exists nowhere, so the first argument was dead code.
    const known = new Set([...TSX_SET, ...TSX_SET_WITH_FALLBACK]);
    const dead = references
      .filter((ref) => ref.fallback && !declared.has(ref.name) && !known.has(ref.name))
      .map((ref) => `${ref.file}: ${ref.name}`);
    expect([...new Set(dead)], 'use the fallback directly or declare the property').toEqual([]);
  });

  it('lists only properties a non-test source file really sets', () => {
    const setters = tsxCustomPropertySetters();
    const stale = [...TSX_SET, ...TSX_SET_WITH_FALLBACK].filter((name) => !setters.has(name));
    expect(stale, 'no .ts/.tsx under src sets these any more: drop them from the list').toEqual([]);
    const unused = [...TSX_SET, ...TSX_SET_WITH_FALLBACK].filter((name) => !references.some((ref) => ref.name === name));
    expect(unused, 'no stylesheet reads these any more').toEqual([]);
  });
});

/**
 * motion-04: component CSS times and eases motion through the --dur-* /
 * --ease-* / --stagger-* tokens only (1.2s pulses, a 1.4s shimmer, a 16ms
 * stagger, bare `ease` / `ease-in-out` and five hand-written Sankey delays
 * used to bypass them). Two exemptions: the global reduced-motion reset
 * (01-app-shell.css: `0.01ms` / `0s !important`), and a discrete
 * `visibility` transition entry, which may hold `0s` and `linear` (the
 * drawer / Genie exit contract: `visibility 0s linear var(--dur-exit)`).
 */
describe('motion timings come from tokens (motion-04)', () => {
  const MOTION_PROPERTY = /^(?:transition|animation)(?:-[a-z-]+)?$/;
  const TIME_LITERAL = /(?<![\w.-])\d*\.?\d+m?s(?![\w-])/;
  const EASING_LITERAL =
    /(?<![\w-])(?:ease(?:-in-out|-in|-out)?|linear|step-start|step-end)(?![\w(-])|(?<![\w-])(?:cubic-bezier|steps|linear)\(/;
  const REDUCED_MOTION_RESET = '*, *::before, *::after';

  /** Top-level comma split (commas inside var() / calc() stay in their entry). */
  function entries(value: string): string[] {
    const out: string[] = [];
    let depth = 0;
    let start = 0;
    for (let i = 0; i < value.length; i += 1) {
      if (value[i] === '(') depth += 1;
      else if (value[i] === ')') depth -= 1;
      else if (value[i] === ',' && depth === 0) {
        out.push(value.slice(start, i).trim());
        start = i + 1;
      }
    }
    out.push(value.slice(start).trim());
    return out;
  }

  function literalMotion(css: string): string[] {
    const offenders: string[] = [];
    for (const rule of rules(css)) {
      if (rule.selector === REDUCED_MOTION_RESET) continue;
      for (const part of rule.block.split(';')) {
        const colon = part.indexOf(':');
        if (colon === -1) continue;
        const property = part.slice(0, colon).trim();
        if (!MOTION_PROPERTY.test(property)) continue;
        for (const entry of entries(part.slice(colon + 1).trim())) {
          const checked = /^visibility\s/.test(entry)
            ? entry.replace(/(?<![\w.-])0m?s(?![\w-])/g, '').replace(/(?<![\w-])linear(?![\w(-])/g, '')
            : entry;
          if (TIME_LITERAL.test(checked) || EASING_LITERAL.test(checked)) offenders.push(`${rule.selector} { ${property}: ${entry} }`);
        }
      }
    }
    return offenders;
  }

  it('finds a literal duration, delay or easing keyword where one is written', () => {
    expect(literalMotion('.kpi { transition: border-color 200ms var(--ease); }')).toHaveLength(1);
    expect(literalMotion('.x { animation: spin var(--dur-pulse) ease-in-out infinite; }')).toHaveLength(1);
    expect(literalMotion('.x { animation-delay: calc(var(--tile-i) * 16ms); }')).toHaveLength(1);
    expect(literalMotion('.x { transition: opacity var(--dur-fast) linear; }')).toHaveLength(1);
    expect(literalMotion('.x { transition: visibility 200ms linear 0s; }')).toHaveLength(1);
    expect(literalMotion('.x { transition: opacity var(--dur-fast) var(--ease-in-out), visibility 0s linear var(--dur-exit); }')).toEqual([]);
  });

  it('writes no numeric duration or delay and no bare easing keyword in component CSS', () => {
    const sheets = [{ file: 'design-system/components.css (partials)', css: components }, ...featureStylesheets()];
    const offenders = sheets.flatMap(({ file, css }) => literalMotion(css).map((where) => `${file}: ${where}`));
    expect(offenders, 'use a --dur-* / --ease-* / --stagger-* token (tokens.css)').toEqual([]);
  });

  it('keeps the literal-free values the motion scale replaced', () => {
    const cascade = new TokenCascade(tokens, { theme: 'dark', accent: 'bright' });
    expect(cascade.raw('--dur-instant')).toBe('80ms');
    expect(cascade.raw('--dur-pulse')).toBe('1.2s');
    expect(cascade.raw('--dur-shimmer')).toBe('1.4s');
    expect(cascade.raw('--dur-pulse-slow')).toBe('1.6s');
    expect(cascade.raw('--ease-in-out')).toBe('cubic-bezier(0.42, 0, 0.58, 1)');
    expect(cascade.raw('--stagger-step')).toBe('16ms');
    expect(cascade.raw('--stagger-step-lg')).toBe('70ms');
    // --ease / --ease-exit carry the out / in roles; no unconsumed aliases.
    for (const name of ['--ease-out', '--ease-in', '--ease-spring']) expect(cascade.raw(name), name).toBeUndefined();
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
    const reset = customProperties(resets[0]?.block ?? '');
    expect(reset.size, 'the print reset declares the accent family').toBeGreaterThan(0);
    for (const [name, value] of reset) {
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

describe('accent text and glyphs use the ink token (a11y-01)', () => {
  // `--accent` is a fill hue: #66C5FF on white is 1.9:1 in light + bright,
  // and navy #025080 on the dark surfaces is under 3:1 in dark + navy.
  // `--accent-ink` is the AA-tuned accent for text and icon glyphs in every
  // theme x accent (it equals `--accent` in the dark default pairs).
  const sheets = [{ file: 'design-system/components.css (partials)', css: components }, ...featureStylesheets()];

  it('never paints text or a glyph with the accent fill hue', () => {
    const offenders = sheets.flatMap(({ file, css }) =>
      rules(css)
        .filter((rule) => /(?<![-\w])color:\s*var\(--accent\)/.test(rule.block))
        .map((rule) => `${file}: ${rule.selector}`),
    );
    expect(offenders, 'use var(--accent-ink) for text and icon glyphs').toEqual([]);
  });

  it('consumes --accent-ink at the sites that painted the fill hue', () => {
    const consumers = sheets.flatMap(({ css }) =>
      rules(css)
        .filter((rule) => /(?<![-\w])color:\s*var\(--accent-ink\)/.test(rule.block))
        .map((rule) => rule.selector),
    );
    expect(consumers).toEqual(
      expect.arrayContaining([
        '.not-found__icon',
        '.growth-agent-card__icon',
        '.glossary-term:hover,.glossary-term:focus-visible',
        '.analytics-scatter__cluster-more',
        '.analytics-scatter-legend__cluster-count',
      ]),
    );
  });
});

describe('--text-4 is decoration, never readable metadata (a11y-01)', () => {
  // --text-4 is the disabled / decorative ink (2.3-2.9:1 on the surfaces in
  // either theme). The Growth Agent step meta, the palette placeholder and a
  // gated segment card's em-dash count painted it as if it were text-3.
  const DECORATIVE = ['.topbar__crumbs .sep', '.lineage-arrow'];
  const sheets = [{ file: 'design-system/components.css (partials)', css: components }, ...featureStylesheets()];
  const textFour = () =>
    sheets.flatMap(({ file, css }) =>
      rules(css)
        .filter((rule) => /(?<![-\w])color:\s*var\(--text-4\)/.test(rule.block))
        .map((rule) => ({ file, selector: rule.selector })),
    );

  it('paints --text-4 text only on the aria-hidden crumb separator and the lineage arrow glyph', () => {
    expect(textFour().map((site) => site.selector).sort()).toEqual([...DECORATIVE].sort());
  });

  it('never paints a placeholder with --text-4', () => {
    expect(textFour().filter((site) => site.selector.includes('::placeholder'))).toEqual([]);
    const placeholder = rules(components).find((rule) => rule.selector === '.cmdk__input::placeholder');
    expect(placeholder?.block).toMatch(/color:\s*var\(--text-3\)/);
  });

  it('moves the three metadata sites to --text-3', () => {
    for (const selector of ['.growth-agent-step__meta', '.seg-card__count--gated', '.cmdk__input::placeholder']) {
      const own = rules(components).filter((rule) => rule.selector === selector);
      expect(own.map((rule) => rule.block).join(';'), selector).toMatch(/(?<![-\w])color:\s*var\(--text-3\)/);
    }
  });
});

describe('the palette cursor wears the ring while the input is keyboard-focused (a11y-01)', () => {
  it('rings the active command row inside the panel', () => {
    const rule = rules(components).find(
      (candidate) => candidate.selector === '.cmdk__panel:has(.cmdk__input:focus-visible) .cmdk__row.is-active',
    );
    expect(rule?.block).toMatch(/outline:\s*var\(--focus-ring-width\) solid var\(--focus-ring-color\);/);
    expect(rule?.block).toMatch(/outline-offset:\s*calc\(-1 \* var\(--focus-ring-width\)\);/);
  });
});

describe('text inputs keep the shared focus ring (a11y-01)', () => {
  // These rules outrank the global `:focus-visible` (tokens.css), so an
  // `outline: none` in them removed the ring and left only the prototype's
  // 1px `--accent` border swap (design_files/index.html:770-772), 1.9:1 in
  // light + bright and invisible under forced colours.
  // `.admin-filter-input` retired with the audit explorer rewrite, whose
  // filters are `.form-input` fields. css-06 adds the palette input, the
  // Console's (prototype-verbatim, index.html:921) select / text rule, and
  // the Genie panel's keyboard resize handle.
  const TEXT_INPUTS = [
    '.genie__input input',
    '.form-input',
    '.cmdk__input',
    '.tweak-row input[type="text"]',
    '.tweak-row select',
    '.genie__resize:focus-visible',
  ];

  it('never switches the outline off on a text-entry control', () => {
    const all = rules(components);
    for (const selector of TEXT_INPUTS) {
      const own = all.filter((rule) => rule.selector.split(',').map((part) => part.trim()).includes(selector));
      expect(own.length, `${selector} is still styled in the partials`).toBeGreaterThan(0);
      const off = own.filter((rule) => /(?<![-\w])outline:\s*(?:none|0)(?![\w.%])/.test(rule.block));
      expect(off.map((rule) => rule.selector), `${selector} must keep the global focus ring`).toEqual([]);
    }
  });

  it('insets the resize handle ring so the panel corner does not clip it (css-06)', () => {
    const own = rules(components).filter((rule) => rule.selector === '.genie__resize:focus-visible');
    expect(own.map((rule) => rule.block).join(';')).toMatch(/outline-offset:\s*calc\(-1 \* var\(--focus-ring-width\)\);/);
  });
});

describe('the Admin Config switches share the Console switch (css-06)', () => {
  // admin-config.tsx renders `button.switch` straight inside `.admin-row`;
  // only `.tweak-row .switch` and `.campaign-setup__toggle .switch` were
  // styled, so "Show evidence chips" / "Show signal meters" were empty,
  // stateless buttons.
  it('names .admin-row > .switch beside every .tweak-row .switch rule', () => {
    const switchRules = rules(components).filter((rule) => /\.tweak-row \.switch(?![\w-])/.test(rule.selector));
    expect(switchRules.map((rule) => rule.selector)).toEqual([
      '.tweak-row .switch, .admin-row > .switch',
      '.tweak-row .switch::after, .admin-row > .switch::after',
      '.tweak-row .switch.on, .admin-row > .switch.on',
      '.tweak-row .switch.on::after, .admin-row > .switch.on::after',
      '.tweak-row .switch:active, .admin-row > .switch:active',
    ]);
  });
});

describe('light theme is carried by tokens, not per-selector overrides (responsive-02)', () => {
  // The partials plus every stylesheet outside design-system/ (the route
  // sheets), each rule tagged with its file so a failure names it.
  const sheets = [
    { file: 'design-system/components.css (partials)', css: components },
    ...featureStylesheets(),
  ];
  const lightRules = () =>
    sheets.flatMap(({ file, css }) =>
      rules(css)
        .filter((rule) => /\[data-theme="light"\]/.test(rule.selector))
        .map((rule) => ({ ...rule, where: `${file}: ${rule.selector}` })),
    );

  it('reads the route stylesheets too', () => {
    expect(sheets.map((sheet) => sheet.file)).toContain('src/routes/analytics.scatter.css');
  });

  it('has no [data-theme="light"] rule that repaints a status or brand hue', () => {
    // These shadowed the token-level light inks (e.g. .chip--warning navy
    // where tokens.css and the prototype, design_files/index.html:416, say
    // #B45309), directly or through a local custom property (the Analytics
    // scatter set --scatter-score-med to navy, so its medium band disagreed
    // with the Lead Queue's). A light-only rule may still remap to --text-* /
    // --accent-ink, or tint a non-text stroke.
    const offenders = lightRules()
      .filter((rule) => /(?:(?<![-\w])color|--[\w-]+)\s*:\s*var\(--(?:signal-|status-|entrada-)/.test(rule.block))
      .map((rule) => rule.where);
    expect(offenders, 'move the light value into tokens.css instead').toEqual([]);
  });

  it('keeps the remaining light-only colour remaps on the text and accent-ink tokens', () => {
    const remaining = lightRules()
      .flatMap((rule) => [...rule.block.matchAll(/(?<![-\w])color:\s*([^;]+);/g)].map((m) => m[1].trim()));
    expect(remaining.length).toBeGreaterThan(0);
    for (const value of remaining) expect(value).toMatch(/^var\(--(?:text-[1-4]|accent-ink)\)$/);
  });
});
