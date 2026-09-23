import { describe, expect, it } from 'vitest';
import { breadcrumbTrail } from '../../lib/breadcrumbs';

/**
 * The Topbar crumb used to come from its own hand-kept table (`currentCrumb`,
 * retired by audit shell-08 / shell-04). The trail now derives from the route
 * registry; these pins keep the old guarantees on the new source: nested
 * detail routes keep their parent's name and unknown routes read as not found.
 */
const labels = (pathname: string) => breadcrumbTrail(pathname, null).map((crumb) => crumb.label);

describe('topbar breadcrumb trail', () => {
  it('keeps known nested data-estate asset routes out of the Not Found fallback', () => {
    expect(labels('/data-estate/assets/borrower_360')).toEqual(['Data estate', 'Governed asset']);
    expect(labels('/data-estate/assets/lead_population')).toEqual(['Data estate', 'Governed asset']);
  });

  it('names slash-delimited borrower and offer detail routes by their masked id under the queue', () => {
    expect(labels('/borrower-360/B-0123456789ABC')).toEqual(['Lead Queue', 'B-0123456789ABC']);
    expect(labels('/offer-orchestrator/B-0123456789ABC')).toEqual(['Lead Queue', 'B-0123456789ABC', 'Offer Orchestrator']);
    // A param that is not a masked id is never echoed into the crumb.
    expect(labels('/borrower-360/B-48291')).toEqual(['Borrower 360']);
    expect(labels('/offer-orchestrator/B-48291')).toEqual(['Offer Orchestrator']);
  });

  it('labels genuinely unknown routes as not found', () => {
    expect(labels('/this-route-does-not-exist')).toEqual(['Page not found']);
    expect(labels('/data-estate/assets-bad')).toEqual(['Page not found']);
    expect(labels('/borrower-360ish')).toEqual(['Page not found']);
    expect(labels('/offer-orchestrator-v2')).toEqual(['Page not found']);
  });

  it('names top-level routes by the registry page name', () => {
    expect(labels('/')).toEqual(['Home']);
    expect(labels('/lead-queue')).toEqual(['Lead Queue']);
    expect(labels('/segment-intelligence')).toEqual(['Segment Intelligence']);
  });
});
