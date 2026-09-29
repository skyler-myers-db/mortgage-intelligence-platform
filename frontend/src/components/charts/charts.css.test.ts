/**
 * Source pins for the chart kit's CSS contracts (2026-09-21 audit stack-06,
 * motion-08). The rendered proofs are tests/e2e/fixture/charts.fixture.spec.ts.
 *
 *  - The shared y-tick column is the plot's height, so a label and its
 *    gridline share one --tick-pos space (dataviz-07) on the kit charts, the
 *    equity scatter and the rate window alike.
 *  - Edge-anchored x ticks: the first label starts at the plot's left edge,
 *    the last ends at its right edge, instead of centring on the edge.
 *  - The analytics bars move by translating a full-width fill inside the
 *    overflow-hidden track: never scaleX (it squashes the rounded end) and
 *    never an animated width.
 */
import { describe, expect, it } from 'vitest';
import { designCss } from '../../test/designCss';
import { featureStylesheets } from '../../test/featureCss';

const chartsCss = featureStylesheets().find((sheet) => sheet.file === 'src/components/charts/charts.css')?.css ?? '';

/** Declarations of the first rule whose selector list is exactly `selector`, as a property map. */
function rule(css: string, selector: string): Record<string, string> {
  const text = css.replace(/\/\*[\s\S]*?\*\//g, '');
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const block = new RegExp(`(?:^|[}\\s])${escaped}\\s*\\{([^}]*)\\}`).exec(text)?.[1] ?? '';
  return Object.fromEntries(
    block
      .split(';')
      .map((part) => part.trim())
      .filter((part) => part.includes(':'))
      .map((part) => [part.slice(0, part.indexOf(':')).trim(), part.slice(part.indexOf(':') + 1).trim()]),
  );
}

describe('chart kit CSS (charts.css)', () => {
  it('is found', () => {
    expect(chartsCss).toContain('.chart-frame');
  });

  it("gives the shared y-tick column the plot's height, not the canvas row's, so each label sits on its gridline", () => {
    const yTicks = rule(designCss(), '.analytics-chart__y-ticks');
    expect(yTicks['align-self']).toBe('start');
    expect(yTicks['block-size']).toBe('var(--analytics-chart-h)');
    expect(yTicks).not.toHaveProperty('min-block-size');
    // One rule for every consumer: the kit sheet no longer scopes its own.
    expect(chartsCss).not.toContain('.analytics-chart__y-ticks');
  });

  it('anchors the first x tick at the left edge and the last at the right edge', () => {
    expect(rule(chartsCss, '.analytics-chart__tick--x.analytics-chart__tick--edge-start')).toEqual({ transform: 'none' });
    expect(rule(chartsCss, '.analytics-chart__tick--x.analytics-chart__tick--edge-end')).toEqual({ transform: 'translateX(-100%)' });
  });
});

describe('analytics bars motion (motion-08)', () => {
  const fill = rule(designCss(), '.analytics-bars__fill');

  it('slides a full-width fill to its share with a translate transition', () => {
    expect(fill.inset).toBe('0');
    expect(fill['inline-size']).toBe('100%');
    expect(fill.translate).toBe('calc(var(--bar-pct, 0%) - 100%) 0');
    expect(fill.transition).toBe('translate var(--dur-base) var(--ease-standard)');
  });

  it('never scales or animates the width, and keeps the clip on the track', () => {
    expect(Object.keys(fill)).not.toEqual(expect.arrayContaining(['width']));
    expect(JSON.stringify(fill)).not.toMatch(/scaleX|scale\(/);
    expect(rule(designCss(), '.analytics-bars__track').overflow).toBe('hidden');
  });
});
