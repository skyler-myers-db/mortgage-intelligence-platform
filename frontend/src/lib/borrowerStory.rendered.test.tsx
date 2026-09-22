/**
 * @vitest-environment happy-dom
 *
 * Rendered-layer proof for the 2026-09-21 audit finding visual-v2: Borrower
 * 360 "The story" opened with a sentence fragment ("This Chicago, IL
 * homeowner. Carries a 5.75% rate…") for every single-property borrower. The
 * defect was visible in the card's narrative paragraph, so this asserts on
 * that paragraph rather than only on the builder below it.
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { Borrower360 } from '../types';
import { BorrowerStoryCard } from '../components/mortgage/BorrowerStoryCard';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

// The audited dossier: the Chicago single-property borrower from the fixture
// screenshot (5.75% rate, 88 bps above market).
const SINGLE_PROPERTY = {
  borrower_id: 'B-1ABCDEFGHIJK2',
  city: 'Chicago', state: 'IL', zip: '60611',
  segment_codes: ['itm'],
  equity_estimate: 285_000, rate_spread_bps: 88, opportunity_score: 82, confidence: 80,
  recommended_offer: 'Refi to 5/1 ARM', why_now: 'x',
  is_investor: false, related_property_count: 1,
  avm_value: 625_000, current_lien_balance: 340_000, current_rate: 5.75, ltv: 54,
  clip_id: 'clip_x', owner_link_id: 'owner_x',
} as unknown as Borrower360;

describe('BorrowerStoryCard narrative for a single-property borrower', () => {
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

  it('renders one full opening sentence instead of "This … homeowner." + a verbless second sentence', () => {
    act(() => root.render(<BorrowerStoryCard borrower={SINGLE_PROPERTY} />));
    const narrative = container.querySelector('.borrower-story__narrative')?.textContent ?? '';
    expect(narrative).toBe(
      'This Chicago, IL homeowner carries a 5.75% rate, 88 bps above market, with 46% equity on a $340K lien. '
      + 'Primary offer: Refi to 5/1 ARM.',
    );
    expect(narrative).not.toContain('homeowner.');
    expect(narrative).not.toContain('. Carries');
    // Joining the sentences left the grounded-claim chips and the verdict alone.
    expect(container.querySelectorAll('.borrower-story__claim')).toHaveLength(4);
    expect(container.querySelector('.borrower-story__claim--unverified')).toBeNull();
    expect(container.textContent).not.toContain('Some figures could not be verified');
  });
});
