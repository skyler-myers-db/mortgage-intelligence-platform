/**
 * Lead Queue and Borrower 360 fixtures: the ranked lead page, borrower
 * search, the dossier, its proof drawer and its lifecycle.
 *
 * These are READ fixtures. Writes (approve, reject, assign, save, log a
 * disposition) are deliberately not registered: a test that exercises a write
 * registers its own handler so it owns the response timing and result, which
 * is what a pessimistic-approval assertion needs.
 */
import type {
  Borrower360,
  BorrowerLifecycle,
  BorrowerProof,
  LeadSummary,
  SegmentCode,
} from '../../../../src/types';
import { fixture, json, type FixtureEntry, type FixtureReply, type FixtureRequest } from '../mockApi';
import { LEADS, borrowerById } from './borrowers';
import { SNAPSHOT_AT, TOTALS, stateByCode } from './reference';
import { SEGMENTS } from './segments';

function csv(query: URLSearchParams, key: string): string[] {
  return (query.get(key) ?? '').split(',').map((value) => value.trim()).filter(Boolean);
}

/**
 * Total the real API would report in `X-Total-Matching`. The unfiltered queue
 * is the contactable population, a one-state or one-segment filter reports
 * that slice's contactable count (so it reconciles with the map tile and the
 * segment card), and any narrower filter reports the rows it matched.
 */
function totalMatching(query: URLSearchParams, matched: number): number {
  const states = [...csv(query, 'states'), ...csv(query, 'state')];
  const segments = [...csv(query, 'segment_codes'), ...csv(query, 'segment')];
  const narrowing = ['zip', 'zips', 'cities', 'county', 'counties', 'borrower_ids', 'approval_status', 'outreach_status', 'assigned_to', 'funnel_stage', 'cohort_id', 'target_lender_ref'];
  if (narrowing.some((key) => query.has(key))) return matched;
  if (states.length === 1 && segments.length === 0) return stateByCode(states[0])?.contactable ?? matched;
  if (segments.length === 1 && states.length === 0) {
    return SEGMENTS.find((segment) => segment.code === segments[0])?.contactable ?? matched;
  }
  if (states.length === 0 && segments.length === 0) return TOTALS.contactable;
  return matched;
}

function filterLeads(query: URLSearchParams): LeadSummary[] {
  const states = [...csv(query, 'states'), ...csv(query, 'state')].map((code) => code.toUpperCase());
  const segments = [...csv(query, 'segment_codes'), ...csv(query, 'segment')] as SegmentCode[];
  const requireAll = query.get('segment_mode') === 'all';
  const ids = csv(query, 'borrower_ids');
  const approval = query.get('approval_status');
  return LEADS.filter((lead) => {
    if (states.length > 0 && !states.includes(lead.state)) return false;
    if (ids.length > 0 && !ids.includes(lead.borrower_id)) return false;
    if (approval && lead.approval_status !== approval) return false;
    if (segments.length === 0) return true;
    return requireAll
      ? segments.every((code) => lead.segment_codes.includes(code))
      : segments.some((code) => lead.segment_codes.includes(code));
  });
}

function leadsPage({ query }: FixtureRequest) {
  const matched = filterLeads(query);
  const limit = Number(query.get('limit') ?? 0);
  const rows = limit > 0 ? matched.slice(0, limit) : matched;
  return json<LeadSummary[]>(rows, {
    headers: {
      'X-Total-Matching': String(totalMatching(query, matched.length)),
      'X-Returned-Rows': String(rows.length),
    },
  });
}

function proofFor(borrower: Borrower360): BorrowerProof {
  const why = borrower.why_panel;
  return {
    borrower_id: borrower.borrower_id,
    trusted: true,
    known_data_gaps: ['Building permits share pending'],
    generated_from: 'mip.gold.borrower_360',
    source_refresh_at: SNAPSHOT_AT,
    opportunity_score: borrower.opportunity_score,
    signal_strength: borrower.confidence,
    signal_strength_note: 'Signal strength reflects evidence coverage, not borrower quality.',
    evidence_confidence_note: 'Evidence confidence is the mean of source-row confidence.',
    score_components: [
      { key: 'economic_incentive', label: 'Economic incentive', value: 92, weight: 0.4, weighted_points: 36.8, explanation: 'Rate spread and equity clear the refinance screen.', source_fields: ['rate_spread_bps', 'equity_pct'] },
      { key: 'intent_trigger', label: 'Intent trigger', value: 80, weight: 0.25, weighted_points: 20, explanation: 'Recent market and propensity signals.', source_fields: ['heloc_propensity_score'] },
      { key: 'fit', label: 'Product fit', value: 88, weight: 0.15, weighted_points: 13.2, explanation: 'Occupancy and lien position fit the offer.', source_fields: ['is_owner_occupied'] },
      { key: 'relationship', label: 'Relationship', value: 70, weight: 0.1, weighted_points: 7, explanation: 'First-party relationship depth.', source_fields: ['is_current_customer'] },
      { key: 'evidence', label: 'Evidence coverage', value: 90, weight: 0.1, weighted_points: 9, explanation: 'Three governed evidence rows.', source_fields: ['evidence_ids'], fair_lending_note: 'No protected-class attribute is an input.' },
    ],
    score_formula: { label: 'Opportunity score', expression: 'sum(component value x weight)', result: String(borrower.opportunity_score), source: 'mip.gold.fn_lead_score' },
    signal_strength_formula: { label: 'Signal strength', expression: 'evidence coverage x source confidence', result: String(borrower.confidence), source: 'mip.gold.evidence_events' },
    rate_spread_formula: { label: 'Rate spread', expression: 'lien rate - par rate', result: `${why.rate_spread_bps} bps`, source: 'mip.gold.fn_rate_spread' },
    equity_formula: { label: 'Equity', expression: 'AVM value - lien balance', result: `$${borrower.equity_estimate.toLocaleString('en-US')}`, source: 'mip.gold.borrower_360' },
    ltv_formula: { label: 'LTV', expression: 'lien balance / AVM value', result: `${borrower.ltv}%`, source: 'mip.gold.borrower_360' },
    offer_code: borrower.recommended_offer_code ?? 'refi',
    offer_label: borrower.recommended_offer,
    offer_branches: [
      { code: 'purchase', label: 'Next-home purchase loan', passed: false, selected: false, reason: 'No active listing.' },
      { code: borrower.recommended_offer_code ?? 'refi', label: borrower.recommended_offer, passed: true, selected: true, reason: why.in_the_money_reason },
    ],
    evidence_rows: borrower.evidence_events.map(({ source_table: _table, ...row }) => row),
    source_assets: ['mip.gold.borrower_360', 'mip.gold.evidence_events'],
    reproduce: [
      { title: 'Borrower row', sql: 'SELECT * FROM mip.gold.borrower_360 WHERE borrower_id = :borrower_id', sql_hash: 'a'.repeat(64), note: 'Masked id parameter; no contact fields selected.' },
    ],
  };
}

interface NotFoundBody {
  detail: string;
}

/** Unknown ids answer 404 like the API does; hygiene then fails the test loudly. */
function borrowerOr404<T>(borrowerId: string, build: (borrower: Borrower360) => T): FixtureReply<T | NotFoundBody> {
  const borrower = borrowerById(borrowerId);
  if (!borrower) {
    return json<NotFoundBody>({ detail: `Borrower ${borrowerId} is not in the fixture population.` }, { status: 404 });
  }
  return json<T>(build(borrower));
}

export const leadFixtures: FixtureEntry[] = [
  fixture('GET', '/api/leads', leadsPage),
  fixture('GET', '/api/borrowers/search', ({ query }) => {
    const needle = (query.get('q') ?? '').trim().toLowerCase();
    const hits = LEADS.filter((lead) =>
      [lead.borrower_id, lead.display_name, lead.city, lead.state, lead.zip].some((field) => field.toLowerCase().includes(needle)),
    );
    return json<LeadSummary[]>(hits.slice(0, 8));
  }),
  fixture('GET', '/api/borrowers/:id', ({ params }) => borrowerOr404<Borrower360>(params.id, (borrower) => borrower)),
  fixture('GET', '/api/borrowers/:id/proof', ({ params }) => borrowerOr404<BorrowerProof>(params.id, proofFor)),
  fixture('GET', '/api/borrowers/:id/lifecycle', ({ params }) =>
    borrowerOr404<BorrowerLifecycle>(params.id, (borrower) => ({
      borrower_id: borrower.borrower_id,
      approval_status: borrower.approval_status,
      outreach_status: borrower.outreach_status ?? 'none',
      synced_at: SNAPSHOT_AT,
      assignment: null,
      latest_disposition: null,
    })),
  ),
];
