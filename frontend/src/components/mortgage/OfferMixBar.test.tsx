/**
 * @vitest-environment happy-dom
 *
 * WHAT TO OFFER (Home answer band) at the component grain: the bar draws
 * primary offer paths only, states its total with the "Primary offer paths"
 * KPI's own number, folds offers past the legend rows into one "Also" line,
 * and says honestly when a book has no primary offer path at all.
 */

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
