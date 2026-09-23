/**
 * Shared reference data for the fixture harness: the synthetic footprint and
 * the headline totals every panel has to agree on.
 *
 * Everything here is synthetic. Lender is the sample tenant `Summit Mortgage`
 * (CLAUDE.md naming rules); there are no real names or contact fields.
 *
 * Reconciliation contract (asserted by harness.fixture.spec.ts):
 *   - sum(state.addressable)    === TOTALS.addressable    (KPI, map, analytics funnel)
 *   - sum(state.in_the_money)   === TOTALS.inTheMoney     (KPI, `itm` segment count)
 *   - sum(state.top_tier)       === TOTALS.highOpportunity
 *   - sum(state.contactable)    === TOTALS.contactable    (Lead Queue X-Total-Matching)
 */

/** Gold snapshot instant. Borrower, offer and draft payloads must share it. */
export const SNAPSHOT_AT = '2026-07-14T12:00:00Z';
export const SNAPSHOT_DATE = '2026-07-14';
/** Default frozen clock for fixture pages: three hours after the snapshot. */
export const FIXTURE_NOW = '2026-07-14T15:00:00Z';
export const LENDER_NAME = 'Summit Mortgage';

export interface FixtureState {
  code: string;
  name: string;
  county: string;
  fips: string;
  city: string;
  zip: string;
  addressable: number;
  contactable: number;
  inTheMoney: number;
  topTier: number;
  avgScore: number;
  topSegment: 'itm' | 'equity' | 'listed' | 'retention' | 'permit' | 'investor';
}

export const STATES: readonly FixtureState[] = [
  { code: 'IL', name: 'Illinois', county: 'Cook County', fips: '17031', city: 'Chicago', zip: '60611', addressable: 21480, contactable: 897, inTheMoney: 3080, topTier: 988, avgScore: 84, topSegment: 'itm' },
  { code: 'TX', name: 'Texas', county: 'Harris County', fips: '48201', city: 'Houston', zip: '77002', addressable: 17920, contactable: 748, inTheMoney: 2570, topTier: 824, avgScore: 82, topSegment: 'equity' },
  { code: 'CA', name: 'California', county: 'Los Angeles County', fips: '06037', city: 'Los Angeles', zip: '90012', addressable: 14650, contactable: 612, inTheMoney: 2100, topTier: 674, avgScore: 81, topSegment: 'listed' },
  { code: 'FL', name: 'Florida', county: 'Miami-Dade County', fips: '12086', city: 'Miami', zip: '33130', addressable: 11230, contactable: 469, inTheMoney: 1610, topTier: 517, avgScore: 79, topSegment: 'retention' },
  { code: 'AZ', name: 'Arizona', county: 'Maricopa County', fips: '04013', city: 'Phoenix', zip: '85004', addressable: 9040, contactable: 378, inTheMoney: 1296, topTier: 416, avgScore: 78, topSegment: 'permit' },
  { code: 'WA', name: 'Washington', county: 'King County', fips: '53033', city: 'Seattle', zip: '98101', addressable: 6710, contactable: 280, inTheMoney: 962, topTier: 309, avgScore: 77, topSegment: 'investor' },
  { code: 'CO', name: 'Colorado', county: 'Denver County', fips: '08031', city: 'Denver', zip: '80202', addressable: 4980, contactable: 208, inTheMoney: 714, topTier: 229, avgScore: 76, topSegment: 'itm' },
  { code: 'GA', name: 'Georgia', county: 'Fulton County', fips: '13121', city: 'Atlanta', zip: '30303', addressable: 3543, contactable: 149, inTheMoney: 508, topTier: 163, avgScore: 75, topSegment: 'equity' },
];

export const TOTALS = {
  addressable: 89553,
  contactable: 3741,
  inTheMoney: 12840,
  highOpportunity: 4120,
  offersRecommended: 6250,
  approved: 432,
  actioned: 318,
  /**
   * The same headline measures under the Lead Queue's default contactability
   * (`marketing_eligibility: 'Eligible only'`): the preview applies the
   * eligibility predicate to one statement, so every count shrinks to the
   * contactable subset. `contactableInTheMoney` is what the `itm` segment
   * card and the `/lead-queue?segment=itm` footer report, so Home's
   * "N contactable of M" banner reconciles with the queue it links to.
   */
  contactableInTheMoney: 1286,
  contactableHighOpportunity: 418,
  contactableOffersRecommended: 2610,
} as const;

export function stateByCode(code: string | null | undefined): FixtureState | undefined {
  const wanted = (code ?? '').toUpperCase();
  return STATES.find((state) => state.code === wanted);
}
