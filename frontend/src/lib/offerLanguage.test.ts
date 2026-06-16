import { describe, expect, it } from 'vitest';
import { offerBorrowerBenefit, offerDisplayLabel, offerRationale, offerShortDescription } from './offerLanguage';

describe('offerLanguage', () => {
  it('turns internal purchase labels into borrower-readable wording', () => {
    expect(offerDisplayLabel('purchase', 'Purchase Mortgage')).toBe('Next-home purchase loan');
    expect(offerDisplayLabel(null, 'Purchase Mortgage')).toBe('Next-home purchase loan');
    expect(offerShortDescription('purchase')).toContain('active listing');
    expect(offerRationale('purchase')).toContain('financing the next home');
    expect(offerBorrowerBenefit('purchase')).toContain('next-home financing');
  });

  it('does not surface legacy internal rationale phrasing', () => {
    const legacy = 'Public-record signals in BELLWOOD, IL point to your current mortgage with another servicer, which may make Purchase Mortgage timely. The home is actively listed -- a purchase mortgage on the next home is the right offer.';

    expect(offerRationale('purchase', legacy)).toBe(
      'The property is listed for sale, so the useful conversation is likely about financing the next home before closing.',
    );
    expect(offerRationale(null, 'This is the right offer from the algorithm.')).not.toMatch(/right offer|algorithm/i);
  });

  it('keeps nurture visibly non-actionable', () => {
    expect(offerDisplayLabel('nurture')).toBe('Monitor for later');
    expect(offerShortDescription('nurture')).toContain('No strong trigger');
  });
});
