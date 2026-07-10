/**
 * @vitest-environment happy-dom
 *
 * BorrowerStoryCard contract (Buyer-Wow #3): the narrative renders
 * AUTOMATICALLY (no click) and shows the prose, the grounded-claim chips, and
 * an honest verified/needs-review verdict.
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { Borrower360 } from '../../types';
import { BorrowerStoryCard } from './BorrowerStoryCard';

function dossier(overrides: Partial<Borrower360> = {}): Borrower360 {
  return {
    borrower_id: 'B-1ABCDEFGHIJK2',
    city: 'Chicago', state: 'IL', zip: '60617',
    segment_codes: ['investor'],
    equity_estimate: 420_000, rate_spread_bps: 356, opportunity_score: 88, confidence: 80,
    recommended_offer: 'Refinance + HELOC', why_now: 'x',
    is_investor: true, related_property_count: 41,
    avm_value: 600_000, current_lien_balance: 42_000, current_rate: 10.04, ltv: 7,
    clip_id: 'clip_x', owner_link_id: 'owner_x',
    ...overrides,
  } as unknown as Borrower360;
}

describe('BorrowerStoryCard', () => {
  let container: HTMLDivElement;
  let root: Root;
  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });
  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });
  const mount = (b: Borrower360) => act(() => root.render(<BorrowerStoryCard borrower={b} />));

  it('renders the narrative automatically, with no reveal button', () => {
    mount(dossier());
    expect(container.querySelector('[data-testid="tell-the-story"]')).toBeNull();
    const body = container.querySelector('[data-testid="borrower-story-body"]');
    expect(body).not.toBeNull();
    expect(body!.textContent).toContain('Chicago, IL investor');
    expect(body!.textContent).toContain('41 properties');
  });

  it('shows grounded-claim chips and no needs-review caveat for a clean dossier', () => {
    mount(dossier());
    // The per-figure claim checks stay; the ambient "every figure verified…"
    // reassurance was trimmed (2026-07-10 declutter), so only the caveat renders
    // when something can't be verified — and here nothing is unverified.
    expect(container.querySelectorAll('.borrower-story__claim').length).toBeGreaterThanOrEqual(4);
    expect(container.querySelector('.borrower-story__claim--unverified')).toBeNull();
    expect(container.textContent).not.toContain('Every figure verified against the source dossier');
    expect(container.textContent).not.toContain('Some figures could not be verified');
  });

  it('shows the needs-review verdict and an unverified chip when a figure cannot be verified', () => {
    mount(dossier({ city: 'Area 51' }));
    expect(container.textContent).toContain('Some figures could not be verified');
  });
});
