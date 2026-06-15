import { describe, expect, it } from 'vitest';
import { currentCrumb } from './Topbar';

describe('currentCrumb', () => {
  it('keeps known nested data-estate asset routes out of the Not Found fallback', () => {
    expect(currentCrumb('/data-estate/assets/borrower_360')).toBe('Data Estate');
    expect(currentCrumb('/data-estate/assets/lead_population')).toBe('Data Estate');
  });

  it('keeps slash-delimited borrower and offer detail routes on their parent crumbs', () => {
    expect(currentCrumb('/borrower-360/B-48291')).toBe('Borrower 360');
    expect(currentCrumb('/offer-orchestrator/B-48291')).toBe('Offer Orchestrator');
  });

  it('labels genuinely unknown routes as not found', () => {
    expect(currentCrumb('/this-route-does-not-exist')).toBe('Not Found');
    expect(currentCrumb('/data-estate/assets-bad')).toBe('Not Found');
    expect(currentCrumb('/borrower-360ish')).toBe('Not Found');
    expect(currentCrumb('/offer-orchestrator-v2')).toBe('Not Found');
  });
});
