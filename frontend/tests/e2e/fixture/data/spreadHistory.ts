/**
 * "Crossed the line" fixtures (lane w5-gold-slot-cache, audit wow-stage-4).
 *
 * Nothing registers by default: gold-slot-cache.fixture.spec.ts calls
 * `registerSpreadHistoryBorrowers(mockApi)` per test, which answers
 * GET /api/borrowers/:id with two dossiers coherent with the default
 * rate-window series (data/rateWindow.ts) and every other id as the default
 * population does:
 *
 *   - the PRIMARY borrower: a fixed note rate of 7.15% with the 75 bps screen
 *     (6.40%), a 2019 origination before the series starts, and the
 *     first_itm_week gold would compute for that series: the week after the
 *     last week whose spread was below the screen. Rates carry two decimals,
 *     so (note - rate) * 100 is a whole number of basis points and the
 *     governed BROUND has no tie to break here;
 *   - the SECOND borrower: an ARM first lien (the FIX-only state).
 *
 * `contractSamples()` exports both bodies for the fixture contract. Synthetic
 * only: masked ids, no contact fields.
 */
import type { Borrower360 } from '../../../../src/types';
import type { ContractSample } from '../contractSamples';
import { json, type MockApi } from '../mockApi';
import { BORROWERS, PRIMARY_BORROWER, borrowerById } from './borrowers';
import { RATE_WINDOW_WEEKS } from './rateWindow';

export const SPREAD_NOTE_RATE = 7.15;
export const SPREAD_SCREEN_BPS = 75;
const LATEST = RATE_WINDOW_WEEKS[RATE_WINDOW_WEEKS.length - 1];
const SPREAD_NOW_BPS = Math.round((SPREAD_NOTE_RATE - LATEST.market_rate_pct) * 100);

function inTheMoney(ratePct: number): boolean {
  // Two-decimal percents: the spread is a whole number of basis points.
  return Math.round(SPREAD_NOTE_RATE * 100) - Math.round(ratePct * 100) >= SPREAD_SCREEN_BPS;
}

/** The week gold's crossing CTE would choose for this series (equity clears its threshold). */
function goldFirstItmWeek(): string | null {
  if (!inTheMoney(LATEST.market_rate_pct)) return null;
  let start = RATE_WINDOW_WEEKS[0].week;
  for (const [index, week] of RATE_WINDOW_WEEKS.entries()) {
    if (!inTheMoney(week.market_rate_pct)) start = RATE_WINDOW_WEEKS[index + 1]?.week ?? start;
  }
  return start;
}

export const SPREAD_FIRST_ITM_WEEK = goldFirstItmWeek() as string;

/** The caption the Math tab must read for the primary borrower. */
export const SPREAD_CAPTION = `In the money since the week of ${new Intl.DateTimeFormat('en-US', {
  month: 'short',
  day: 'numeric',
  year: 'numeric',
  timeZone: 'UTC',
}).format(new Date(`${SPREAD_FIRST_ITM_WEEK}T00:00:00Z`))}`;

/**
 * The keys the real dossier always sends that the default population leaves
 * out (registry KNOWN_OMISSIONS), at the server's own defaults, so these two
 * bodies satisfy the fixture contract without growing the omission list.
 */
const SERVER_KEYS = {
  approved_at: null,
  outreach_at: null,
  is_former_customer: false,
  is_competitor_lien: false,
  second_pos_amount: 0,
  has_permit: false,
  listing_status_category: null,
  listing_status_description: null,
  listing_date: null,
  listing_status_date: null,
  listing_price: null,
  listing_days_on_market: null,
  listing_service: null,
  heloc_propensity_score: null,
  heloc_propensity_run_date: null,
  refi_propensity_score: null,
  refi_propensity_run_date: null,
  has_refi_propensity_trigger: false,
  current_lender_ref: null,
  last_touch_at: null,
  eligible_recontact_at: null,
  dnc: false,
  assigned_to_email: null,
  assigned_to_label: null,
  assigned_at: null,
  assignment_expires_at: null,
  assignment_status: null,
  assignment_id: null,
  latest_disposition_outcome: null,
  latest_disposition_at: null,
  latest_callback_at: null,
  aging_days: null,
  ltv_basis_is_unreliable: false,
  situs_cbsa_code: null,
  first_pos_loan_type: null,
  is_absentee: false,
  is_corporate_owner: false,
  has_first_party_relationship: false,
  first_party_relationship_depth: 0,
  first_party_recent_interactions: 0,
  first_party_recent_application: false,
  first_party_synthetic_demo: false,
} satisfies Partial<Borrower360>;

function complete(borrower: Borrower360): Borrower360 {
  return {
    ...SERVER_KEYS,
    current_lien_balance_low: borrower.current_lien_balance,
    current_lien_balance_high: borrower.current_lien_balance,
    ...borrower,
  };
}

export const SPREAD_FIX_BORROWER: Borrower360 = complete({
  ...PRIMARY_BORROWER,
  current_rate: SPREAD_NOTE_RATE,
  rate_spread_bps: SPREAD_NOW_BPS,
  why_panel: {
    ...PRIMARY_BORROWER.why_panel,
    rate_spread_bps: SPREAD_NOW_BPS,
    market_rate: LATEST.market_rate_pct / 100,
    equity_pct: 40,
    in_the_money: true,
    in_the_money_reason: `+${SPREAD_NOW_BPS} bps spread (>= ${SPREAD_SCREEN_BPS}) AND 40% equity (>= 15%)`,
    min_spread_bps: SPREAD_SCREEN_BPS,
    min_equity_pct: 15,
  },
  first_pos_date: '2019-06-12',
  first_pos_rate_type: 'FIX',
  first_itm_week: SPREAD_FIRST_ITM_WEEK,
});

export const SPREAD_ARM_BORROWER: Borrower360 = complete({
  ...BORROWERS[1],
  first_pos_date: '2021-03-15',
  first_pos_rate_type: 'ARM',
  first_itm_week: null,
});

const OVERRIDES: ReadonlyMap<string, Borrower360> = new Map([
  [SPREAD_FIX_BORROWER.borrower_id, SPREAD_FIX_BORROWER],
  [SPREAD_ARM_BORROWER.borrower_id, SPREAD_ARM_BORROWER],
]);

export function registerSpreadHistoryBorrowers(mockApi: MockApi): void {
  mockApi.register<Borrower360 | { detail: string }>('GET', '/api/borrowers/:id', ({ params }) => {
    const borrower = OVERRIDES.get(params.id) ?? borrowerById(params.id);
    if (!borrower) return json({ detail: `Borrower ${params.id} is not in the fixture population.` }, { status: 404 });
    return json<Borrower360>(borrower);
  });
}

/** Every body this module can register, for the fixture contract exporter. */
export function contractSamples(): ContractSample[] {
  return [SPREAD_FIX_BORROWER, SPREAD_ARM_BORROWER].map((borrower, index) => ({
    source: index === 0 ? 'SPREAD_FIX_BORROWER' : 'SPREAD_ARM_BORROWER',
    method: 'GET',
    pattern: '/api/borrowers/:id',
    path: `/api/borrowers/${borrower.borrower_id}`,
    query: '',
    status: 200,
    body: borrower,
  }));
}
