import { describe, expect, it } from 'vitest';
import { offerBorrowerBenefit, offerDisplayLabel, offerShortDescription } from './offerLanguage';

describe('offerLanguage', () => {
  it('turns internal purchase labels into borrower-readable wording', () => {
    expect(offerDisplayLabel('purchase', 'Purchase Mortgage')).toBe('Next-home purchase loan');
    expect(offerDisplayLabel(null, 'Purchase Mortgage')).toBe('Next-home purchase loan');
    expect(offerShortDescription('purchase')).toContain('active listing');
    expect(offerBorrowerBenefit('purchase')).toContain('next-home financing');
  });

  it('keeps nurture visibly non-actionable', () => {
    expect(offerDisplayLabel('nurture')).toBe('Monitor for later');
    expect(offerShortDescription('nurture')).toContain('No strong trigger');
  });
});
