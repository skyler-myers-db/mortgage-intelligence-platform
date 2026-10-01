/**
 * Data-ink contract (2026-09-21 audit dataviz-09 / visual-08; 2026-09-30
 * ruling D-dataviz-geo-c1). Single-series data marks paint --accent-data,
 * which is FIXED per theme (#66C5FF dark, #014E80 light) and never follows
 * [data-accent]: the red accent painted data in the danger family and teal
 * made it equal --seg-itm. It clears WCAG 1.4.11 (3:1 for graphics) on the
 * card surfaces in every theme x accent pair; the raw accent drew 1.91:1
 * lines on white with the default `bright` accent.
 *
 * Resolved over the real token cascade (no browser). The painted proof is
 * tests/e2e/fixture/charts.fixture.spec.ts.
 */
import { describe, expect, it } from 'vitest';
// @ts-expect-error Frontend app types intentionally exclude Node globals; this
// test reads source text under Vitest only.
import { readFileSync } from 'node:fs';
// @ts-expect-error see node:fs note above.
import { join } from 'node:path';
import { designCss } from '../test/designCss';
import { featureStylesheets } from '../test/featureCss';
import { TokenCascade, contrast, hex, readTokensCss } from '../test/tokenCascade';

declare const process: { cwd(): string };

const tokens = readTokensCss();
const THEMES = ['dark', 'light'] as const;
const ACCENTS = ['bright', 'navy', 'red', 'teal'] as const;
const SURFACES = ['--bg-1', '--bg-2'] as const;
const GRAPHIC_AA = 3;
/** The fixed data ink per theme (the default accent's own values). */
const DATA_INK = { dark: '#66C5FF', light: '#014E80' } as const;

/** Selectors whose fill or stroke is a single-series data mark. */
const DATA_MARK_SELECTORS = [
  '.analytics-line-chart polyline',
  '.analytics-chart__hover-dot',
  '.analytics-bars__fill',
  '.funnel-sankey__bar',
  '.genie-line__path',
  '.genie-line__point',
  '.genie-bars__bar',
  '.chart-hist__bar--past',
];

/** TSX sites that paint a data mark inline. The Genie line and bar charts
 *  paint from GenieAnswerCharts.css now (dataviz-05), listed above. */
const TSX_DATA_MARK_FILES = [
  'src/routes/analytics.charts.tsx',
  'src/components/mortgage/Sparkline.tsx',
];

const BARE_ACCENT = /var\(\s*--accent\s*[,)]/;

describe('--accent-data (dataviz-09, in contract)', () => {
  for (const theme of THEMES) {
    for (const accent of ACCENTS) {
      it(`${theme} + ${accent}: is the fixed ${DATA_INK[theme]} and never reads the accent`, () => {
        const cascade = new TokenCascade(tokens, { theme, accent });
        expect(hex(cascade.color('--accent-data'))).toBe(DATA_INK[theme]);
        expect(cascade.raw('--accent-data') ?? '').not.toMatch(/--accent/);
      });
    }
  }

  for (const theme of THEMES) {
    for (const accent of ACCENTS) {
      it(`${theme} + ${accent}: data ink, muted bins and the rule ink clear 3:1 on the card surfaces`, () => {
        const cascade = new TokenCascade(tokens, { theme, accent });
        for (const surface of SURFACES) {
          const bg = cascade.color(surface);
          for (const ink of ['--accent-data', '--text-3', '--text-2']) {
            const ratio = contrast(cascade.color(ink), bg);
            expect(ratio, `${ink} ${hex(cascade.color(ink))} on ${surface} ${hex(bg)}: ${ratio.toFixed(2)}:1`).toBeGreaterThanOrEqual(GRAPHIC_AA);
          }
        }
      });
    }
  }
});

describe('data marks never paint the bare accent', () => {
  const sheets = [{ file: 'design-system/components.css (partials)', css: designCss() }, ...featureStylesheets()];
  const rules = sheets.flatMap(({ file, css }) =>
    topLevelRulesDeep(css).map((rule) => ({ file, selectors: rule.selector.split(',').map((part) => part.trim().replace(/\s+/g, ' ')), block: rule.block })),
  );

  it('finds every listed data-mark selector', () => {
    const present = new Set(rules.flatMap((rule) => rule.selectors));
    expect(DATA_MARK_SELECTORS.filter((selector) => !present.has(selector))).toEqual([]);
  });

  it('paints each listed data mark with --accent-data, never var(--accent)', () => {
    const offenders = rules
      .filter((rule) => rule.selectors.some((selector) => DATA_MARK_SELECTORS.some((mark) => selector.endsWith(mark))))
      .filter((rule) => BARE_ACCENT.test(rule.block))
      .map((rule) => `${rule.file}: ${rule.selectors.join(', ')}`);
    expect(offenders).toEqual([]);
  });

  it('draws the rate window data ink from --accent-data in both themes', () => {
    const inks = rules.flatMap((rule) => [...rule.block.matchAll(/--rate-window-data-ink:\s*([^;]+);/g)].map((match) => match[1].trim()));
    expect(inks.length).toBe(2);
    expect(new Set(inks)).toEqual(new Set(['var(--accent-data)']));
  });

  it('keeps the bare accent out of the TSX data-mark sites', () => {
    for (const file of TSX_DATA_MARK_FILES) {
      const source = readFileSync(join(process.cwd(), file), 'utf8') as string;
      expect(BARE_ACCENT.test(source), `${file} paints var(--accent)`).toBe(false);
      expect(source, `${file} paints --accent-data`).toContain('var(--accent-data)');
    }
  });
});

/** Innermost `selector { block }` pairs, whatever at-rule wraps them, comments dropped. */
function topLevelRulesDeep(css: string): Array<{ selector: string; block: string }> {
  const text = css.replace(/\/\*[\s\S]*?\*\//g, '');
  return [...text.matchAll(/([^{}]+)\{([^{}]*)\}/g)].map((match) => ({ selector: match[1].trim(), block: match[2] }));
}
