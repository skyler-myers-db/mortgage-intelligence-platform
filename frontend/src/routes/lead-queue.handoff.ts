/**
 * "Build a campaign from these filters" (audit tables-07 / tables-02,
 * D-approval-flow-a3; deviation:bulk-scope-handoff): the Lead Queue's
 * filters as a Portfolio Builder URL. There is no "select all N matching"
 * and no server batch decision: a whole cohort is worked as a campaign,
 * where Portfolio Builder previews it and every borrower still gets an
 * individual decision.
 *
 * Pure (no I/O). Fail-honest: every Portfolio Builder filter key is written
 * (the queue's value in Portfolio Builder's spelling, or the queue's no-op
 * value, so Portfolio Builder's own narrower defaults never apply
 * silently), and every queue param Portfolio Builder cannot carry is named
 * in `notCarried`, an unknown one by its raw name. The place and view
 * params (sort, dir, row, view) are not filters and are never listed.
 *
 * Portfolio Builder's keys and options are mirrored here, not imported:
 * importing portfolio-builder.logic split it into a 2.8 KiB br chunk that
 * joined the Lead Queue route's closure. The parity test in
 * lead-queue.handoff.test.ts pins the mirror to portfolio-builder.logic.
 */
import { isPublicLenderRef, LENDER_RELATIONSHIP_OPTIONS } from '../lib/lenderFilters';
import type { LeadTableCampaignHandoff } from '../components/mortgage/LeadTable.types';
import {
  GROWTH_AGENT_PROOF_PARAMS,
  LEAD_TABLE_PLACE_PARAMS,
  LEAD_TABLE_VIEW_PARAM,
  parsePortfolioCriteria,
} from './lead-queue.filters';

/** Portfolio Builder's URL filter keys (URL_FILTER_KEYS), in its order. */
export const PB_FILTER_KEYS = [
  'occupancy',
  'lien_status',
  'lender_relationship',
  'target_lender_ref',
  'owner_link',
  'purchase_intent',
  'product',
  'min_equity_pct_label',
  'marketing_eligibility',
  'consent_status',
  'recency',
] as const;

type PortfolioBuilderKey = (typeof PB_FILTER_KEYS)[number];

/**
 * Portfolio Builder's options per key (NON_GEO_FILTER_GROUPS); null where
 * it has no fixed list (the target lender is verified against the
 * tenant's lenders instead).
 */
export const PB_FILTER_OPTIONS: Readonly<Record<PortfolioBuilderKey, readonly string[] | null>> = {
  occupancy: ['Owner-occupied', 'Non-owner-occupied', 'All'],
  lien_status: ['Open 1st lien', 'Open HELOC', 'Free & clear', 'Any'],
  lender_relationship: LENDER_RELATIONSHIP_OPTIONS,
  target_lender_ref: null,
  owner_link: ['All', 'Single-property owner', 'Multi-property (2-4)', 'Portfolio investor (5+)'],
  purchase_intent: ['All', 'Listed for sale', 'HELOC intent', 'Both'],
  product: ['All products', 'Refi', 'HELOC', 'Cash-out', 'Purchase', 'Retention'],
  min_equity_pct_label: ['≥ 15%', '≥ 25%', '≥ 40%', 'Any'],
  marketing_eligibility: ['Eligible only', 'Any', 'Suppressed only'],
  consent_status: ['Any', 'Opt-in', 'Opt-out', 'Unknown'],
  recency: ['Any', 'Untouched 30d', 'Untouched 60d', 'Untouched 90d'],
};

/** The queue's "no filter" value per Portfolio Builder key. */
const QUEUE_NO_OP: Record<PortfolioBuilderKey, string> = {
  occupancy: 'All',
  lien_status: 'Any',
  lender_relationship: 'All',
  target_lender_ref: 'All',
  owner_link: 'All',
  purchase_intent: 'All',
  product: 'All products',
  min_equity_pct_label: 'Any',
  marketing_eligibility: 'Eligible only',
  consent_status: 'Any',
  recency: 'Any',
};

/** The queue's alternate spellings, as Portfolio Builder spells them. */
const PB_SPELLING: Readonly<Record<string, string>> = {
  '>= 15%': '≥ 15%',
  '>= 25%': '≥ 25%',
  '>= 40%': '≥ 40%',
  'Open first lien': 'Open 1st lien',
  'Free and clear': 'Free & clear',
};

/** Queue params Portfolio Builder cannot carry, by what they filter. */
const NOT_CARRIED_LABELS: Readonly<Record<string, string>> = {
  loan_product: 'product type',
  origination_channel: 'origination channel',
  approval_status: 'approval',
  outreach_status: 'outreach',
  assigned_to: 'assignee',
  aged_days: 'aging',
  funnel_stage: 'funnel stage',
  segment: 'segments',
  segment_codes: 'segments',
  segment_mode: 'segments',
  zip: 'ZIPs',
  zips: 'ZIPs',
  county: 'counties',
  counties: 'counties',
  cities: 'cities',
  borrower_ids: 'borrower list',
  cohort_id: 'cohort',
  campaign_id: 'campaign binding',
  variant_name: 'campaign binding',
  ...Object.fromEntries(GROWTH_AGENT_PROOF_PARAMS.map((param) => [param, 'Growth Agent proof'])),
};

/** Portfolio filters the queue reads that Portfolio Builder has no control for. */
const QUEUE_ONLY_PORTFOLIO_KEYS: ReadonlySet<string> = new Set(['loan_product', 'origination_channel']);

/** Not filters: where the reader is in the table, and its column preset. */
const NEVER_LISTED: ReadonlySet<string> = new Set([...LEAD_TABLE_PLACE_PARAMS, LEAD_TABLE_VIEW_PARAM]);

function queueStates(searchParams: URLSearchParams): string[] {
  const raw = searchParams.get('states') ?? searchParams.get('state') ?? '';
  const codes = raw.split(',').map((code) => code.trim().toUpperCase()).filter((code) => /^[A-Z]{2}$/.test(code));
  return [...new Set(codes)];
}

export function buildPortfolioBuilderUrlFromQueue(
  searchParams: URLSearchParams,
  allowedLenderRefs: readonly string[],
): LeadTableCampaignHandoff {
  const criteria = parsePortfolioCriteria(searchParams, allowedLenderRefs) ?? {};
  const params = new URLSearchParams();
  const notCarried: string[] = [];
  const listNotCarried = (label: string) => {
    if (!notCarried.includes(label)) notCarried.push(label);
  };
  const states = queueStates(searchParams);
  if (states.length > 0) params.set('states', states.join(','));
  for (const key of PB_FILTER_KEYS) {
    const effective = criteria[key];
    const spelled = effective === undefined ? undefined : PB_SPELLING[effective] ?? effective;
    const options = PB_FILTER_OPTIONS[key];
    const carries = spelled !== undefined && (options === null || options.includes(spelled));
    params.set(key, carries ? spelled : QUEUE_NO_OP[key]);
    if (spelled !== undefined && !carries) listNotCarried(key.replace(/_/g, ' '));
    // A lender the queue could not verify was dropped from its own filter
    // too; say so rather than widening it silently.
    const raw = searchParams.get(key)?.trim();
    if (key === 'target_lender_ref' && raw && raw !== 'All' && !isPublicLenderRef(raw, allowedLenderRefs)) {
      listNotCarried('target lender');
    }
  }
  const handled = new Set<string>([...PB_FILTER_KEYS, 'states', 'state']);
  for (const [key, value] of searchParams) {
    if (handled.has(key) || NEVER_LISTED.has(key) || value.trim() === '') continue;
    // A queue-only portfolio filter counts only when the queue applies it
    // (a no-op or unknown value filters nothing there either).
    if (QUEUE_ONLY_PORTFOLIO_KEYS.has(key) && criteria[key] === undefined) continue;
    listNotCarried(NOT_CARRIED_LABELS[key] ?? key);
  }
  return { href: `/portfolio-builder?${params.toString()}`, notCarried };
}
