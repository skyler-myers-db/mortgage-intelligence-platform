/**
 * Every route in frontend/src/app.tsx (plus each Analytics tab), with the
 * detail routes pointed at the fixture population. Later lanes iterate this
 * table instead of re-listing routes, so a new route is added in one place.
 *
 * `populated` is a value that only appears when the route rendered FIXTURE
 * DATA (a count, a masked id, a governed asset path). It is deliberately not
 * UI copy, so a lane that rewrites a heading or a label does not break the
 * smoke spec. Routes that are static by design have none.
 */
import { PRIMARY_BORROWER } from './data/borrowers';
import { PRIMARY_ASSET_KEY } from './data/dataEstate';
import { TOTALS } from './data/reference';

export interface FixtureRoute {
  /** Stable slug for test titles and, later, screenshot names. */
  name: string;
  path: string;
  populated?: string | RegExp;
}

export const FIXTURE_ROUTES: readonly FixtureRoute[] = [
  { name: 'home', path: '/', populated: '89,553' },
  // Portfolio Builder previews under its default 'Eligible only' contactability.
  { name: 'portfolio-builder', path: '/portfolio-builder', populated: TOTALS.contactable.toLocaleString('en-US') },
  { name: 'segment-intelligence', path: '/segment-intelligence', populated: '12,840' },
  { name: 'lead-queue', path: '/lead-queue', populated: PRIMARY_BORROWER.borrower_id },
  { name: 'borrower-360-index', path: '/borrower-360' },
  { name: 'borrower-360-detail', path: `/borrower-360/${PRIMARY_BORROWER.borrower_id}`, populated: PRIMARY_BORROWER.clip },
  { name: 'offer-orchestrator-index', path: '/offer-orchestrator' },
  { name: 'offer-orchestrator-detail', path: `/offer-orchestrator/${PRIMARY_BORROWER.borrower_id}`, populated: 'NMLS #000000' },
  { name: 'ask-genie', path: '/ask-genie', populated: 'Daily refi brief' },
  // Analytics abbreviates today (89.55K); accept the full figure too.
  { name: 'analytics-executive', path: '/analytics', populated: /89[.,]55/ },
  { name: 'analytics-geography', path: '/analytics?view=geography' },
  { name: 'analytics-economics', path: '/analytics?view=economics' },
  { name: 'analytics-segments', path: '/analytics?view=segments' },
  { name: 'analytics-signals', path: '/analytics?view=signals' },
  { name: 'analytics-approval-funnel', path: '/analytics?view=approval-funnel' },
  { name: 'analytics-sales-ops', path: '/analytics?view=sales-ops' },
  { name: 'admin-config', path: '/admin-config', populated: 'fixture-v1' },
  { name: 'glossary', path: '/glossary' },
  { name: 'asset-detail', path: `/data-estate/assets/${PRIMARY_ASSET_KEY}`, populated: 'mip.gold.borrower_360' },
  { name: 'not-found', path: '/this-route-does-not-exist' },
];

export const FIXTURE_THEMES = ['dark', 'light'] as const;
