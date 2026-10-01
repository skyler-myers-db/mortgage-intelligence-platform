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
 */
import { isPublicLenderRef } from '../lib/lenderFilters';
import type { LeadTableCampaignHandoff } from '../components/mortgage/LeadTable.types';
import {
  GROWTH_AGENT_PROOF_PARAMS,
  LEAD_TABLE_PLACE_PARAMS,
  LEAD_TABLE_VIEW_PARAM,
  parsePortfolioCriteria,
} from './lead-queue.filters';
import { NON_GEO_FILTER_GROUPS, URL_FILTER_KEYS } from './portfolio-builder.logic';

type PortfolioBuilderKey = (typeof URL_FILTER_KEYS)[number];

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

function pbOptions(key: PortfolioBuilderKey): readonly string[] | null {
  return NON_GEO_FILTER_GROUPS.find((group) => group.key === key)?.options ?? null;
}

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
  for (const key of URL_FILTER_KEYS) {
    const effective = criteria[key];
    const spelled = effective === undefined ? undefined : PB_SPELLING[effective] ?? effective;
    const options = pbOptions(key);
    const carries = spelled !== undefined && (key === 'target_lender_ref' || options === null || options.includes(spelled));
    params.set(key, carries ? spelled : QUEUE_NO_OP[key]);
    if (spelled !== undefined && !carries) listNotCarried(key.replace(/_/g, ' '));
    // A lender the queue could not verify was dropped from its own filter
    // too; say so rather than widening it silently.
    const raw = searchParams.get(key)?.trim();
    if (key === 'target_lender_ref' && raw && raw !== 'All' && !isPublicLenderRef(raw, allowedLenderRefs)) {
      listNotCarried('target lender');
    }
  }
  const handled = new Set<string>([...URL_FILTER_KEYS, 'states', 'state']);
  for (const [key, value] of searchParams) {
    if (handled.has(key) || NEVER_LISTED.has(key) || value.trim() === '') continue;
    // A queue-only portfolio filter counts only when the queue applies it
    // (a no-op or unknown value filters nothing there either).
    if (QUEUE_ONLY_PORTFOLIO_KEYS.has(key) && criteria[key] === undefined) continue;
    listNotCarried(NOT_CARRIED_LABELS[key] ?? key);
  }
  return { href: `/portfolio-builder?${params.toString()}`, notCarried };
}
