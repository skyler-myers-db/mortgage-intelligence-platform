/**
 * @vitest-environment happy-dom
 *
 * BorrowerStoryCard contract (Buyer-Wow #3): the narrative is hidden until
 * "Tell the story" is clicked; once revealed it shows the prose, the
 * grounded-claim chips, and an honest verified/needs-review verdict.
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

  it('hides the narrative until the button is clicked', () => {
    mount(dossier());
    expect(container.querySelector('[data-testid="borrower-story-body"]')).toBeNull();
    act(() => container.querySelector<HTMLButtonElement>('[data-testid="tell-the-story"]')!.click());
    const body = container.querySelector('[data-testid="borrower-story-body"]');
    expect(body).not.toBeNull();
    expect(body!.textContent).toContain('Chicago, IL investor');
    expect(body!.textContent).toContain('41 properties');
  });

  it('shows the verified verdict and grounded-claim chips for a clean dossier', () => {
    mount(dossier());
    act(() => container.querySelector<HTMLButtonElement>('[data-testid="tell-the-story"]')!.click());
    expect(container.textContent).toContain('Every figure verified against the source dossier');
    expect(container.querySelectorAll('.borrower-story__claim').length).toBeGreaterThanOrEqual(4);
    expect(container.querySelector('.borrower-story__claim--unverified')).toBeNull();
  });

  it('shows the needs-review verdict when a figure cannot be verified', () => {
    mount(dossier({ city: 'Area 51' }));
    act(() => container.querySelector<HTMLButtonElement>('[data-testid="tell-the-story"]')!.click());
    expect(container.textContent).toContain('Some figures could not be verified');
  });
});
