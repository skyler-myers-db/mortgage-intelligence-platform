/**
 * @vitest-environment happy-dom
 *
 * WHAT TO OFFER (Home answer band) at the component grain: the bar draws
 * primary offer paths only, states its total with the "Primary offer paths"
 * KPI's own number, folds offers past the legend rows into one "Also" line,
 * and says honestly when a book has no primary offer path at all.
 */

// @ts-expect-error Frontend app types intentionally exclude Node globals; the
// stylesheet contract below reads the lazy sheet's text under Vitest only.
import { readFileSync } from 'node:fs';
// @ts-expect-error see node:fs note above.
import { join } from 'node:path';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { PortfolioPreview } from '../../types';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock('../AppContext', () => ({
  useApp: () => ({ setDrawer: vi.fn(), showEvidence: true }),
}));

import { OfferMixBar } from './OfferMixBar';

type OfferMix = NonNullable<PortfolioPreview['offer_mix']>;

declare const process: { cwd(): string };

const ACTIONABLE: OfferMix = [
  { offer_code: 'refi', borrower_count: 55_871 },
  { offer_code: 'purchase', borrower_count: 41_220 },
  { offer_code: 'refi_plus_heloc', borrower_count: 18_904 },
  { offer_code: 'investor', borrower_count: 3_311 },
  { offer_code: 'cash_out', borrower_count: 2_164 },
  { offer_code: 'retention', borrower_count: 912 },
  { offer_code: 'heloc', borrower_count: 7 },
];
const OFFER_PATHS = ACTIONABLE.reduce((sum, row) => sum + row.borrower_count, 0);

function preview(offerMix: OfferMix, offersRecommended: number | null = OFFER_PATHS): PortfolioPreview {
  return {
    marketable_population: 5_156_184,
    high_intent_leads: 74_775,
    top_tier_opportunities: 38_912,
    offers_recommended: offersRecommended,
    offer_mix: offerMix,
  } as PortfolioPreview;
}

describe('OfferMixBar', () => {
  let root: Root;
  let container: HTMLElement;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  const render = (value: PortfolioPreview | null) =>
    act(() =>
      root.render(
        <MemoryRouter>
          <OfferMixBar preview={value} />
        </MemoryRouter>,
      ),
    );
  const note = () => container.querySelector('.home-answer__note')?.textContent ?? '';

  it('draws primary offer paths only and states the KPI total plus the Monitor-for-later count', () => {
    render(preview([{ offer_code: 'nurture', borrower_count: 4_620_000 }, ...ACTIONABLE]));
    const segments = Array.from(container.querySelectorAll('.offer-mix__seg')).map((el) => el.getAttribute('data-offer'));
    expect(segments).not.toContain('nurture');
    expect(segments).toHaveLength(ACTIONABLE.length);
    expect(note()).toContain(`Share of the ${OFFER_PATHS.toLocaleString()} borrowers with a primary offer path`);
    expect(note()).toContain('4,620,000 more are on Monitor for later');
    expect(container.textContent).not.toMatch(/Monitor for later\s*\d/);
  });

  it('gives the four largest offers a legend row and the rest one "Also" line, every one a queue link', () => {
    render(preview(ACTIONABLE));
    const rows = Array.from(container.querySelectorAll('.offer-mix__item .offer-mix__label'));
    expect(rows.map((el) => el.textContent)).toEqual([
      'Refinance review',
      'Next-home purchase loan',
      'Refinance + home-equity review',
      'Investor financing review',
    ]);
    const also = Array.from(container.querySelectorAll<HTMLAnchorElement>('.offer-mix__more .offer-mix__more-label'));
    expect(also.map((el) => [el.textContent, el.getAttribute('href')])).toEqual([
      ['Cash-out refinance review', '/lead-queue?product=Cash-out'],
      ['Customer retention review', '/lead-queue?product=Retention'],
      ['Home-equity line review', '/lead-queue?product=HELOC'],
    ]);
    const printed = Array.from(container.querySelectorAll('.offer-mix__pct'));
    expect(printed.reduce((sum, el) => sum + Number(el.getAttribute('data-percent')), 0)).toBe(100);
    expect(printed[printed.length - 1]?.textContent).toBe('<1%');
    // No nurture row in this mix: the note ends at the total.
    expect(note()).toMatch(/primary offer path\. Links open the contactable subset\.$/);
  });

  it('falls back to the drawn total when the preview carries no offers_recommended', () => {
    render(preview(ACTIONABLE.slice(0, 2), null));
    expect(note()).toContain(`Share of the ${(55_871 + 41_220).toLocaleString()} borrowers`);
    expect(container.querySelector('.offer-mix__more')).toBeNull();
  });

  it('says a monitor-only book has no primary offer path instead of drawing a 100% "no offer"', () => {
    render(preview([{ offer_code: 'nurture', borrower_count: 12 }], 0));
    expect(container.querySelector('.offer-mix')).toBeNull();
    expect(container.querySelector('[role="status"]')?.textContent).toBe(
      'No borrower has a primary offer path in this snapshot; 12 are on Monitor for later.',
    );
  });
});

/**
 * The bar's stylesheet (HomeAnswerBand.css, a lazy sheet) under the
 * re-stepped segment palette (D-dataviz-geo-c2): refi + HELOC is the refi and
 * HELOC hues striped together (its own sky hue sat within dE 15 of the new
 * equity blue), slices are separated by a 2px surface gap, and the
 * forced-colours block lives in this sheet so it wins ties with the shell.
 * Rendered proof: home-answer.fixture.spec.ts and css-hygiene.modes.
 */
describe('offer-mix stylesheet contract', () => {
  const css = (readFileSync(join(process.cwd(), 'src/components/mortgage/HomeAnswerBand.css'), 'utf8') as string)
    .replace(/\/\*[\s\S]*?\*\//g, '');

  it('stripes refi + HELOC with both the refi and the HELOC hues', () => {
    const rule = /\.offer-mix__swatch\[data-offer="refi_plus_heloc"\]\s*\{([^}]*)\}/.exec(css)?.[1] ?? '';
    expect(rule).toMatch(/repeating-linear-gradient\(/);
    expect(rule).toContain('var(--seg-itm)');
    expect(rule).toContain('var(--seg-permit)');
    expect(rule).not.toContain('--seg-related-itm');
  });

  it('separates slices with a half-step surface gap', () => {
    expect(css).toMatch(/\.offer-mix__seg \+ \.offer-mix__seg\s*\{\s*box-shadow:\s*inset calc\(var\(--sp-1\) \/ 2\) 0 0 var\(--bg-2\);/);
  });

  it('keeps its forced-colours block in this sheet', () => {
    const forced = /@media \(forced-colors: active\)\s*\{([\s\S]*)\}\s*$/.exec(css)?.[1] ?? '';
    expect(forced).toMatch(/\.offer-mix\s*\{[^}]*forced-color-adjust:\s*none;[^}]*background:\s*Canvas;/);
    expect(forced).toMatch(/\.offer-mix__seg \+ \.offer-mix__seg\s*\{\s*box-shadow:\s*inset calc\(var\(--sp-1\) \/ 2\) 0 0 Canvas;/);
  });
});
