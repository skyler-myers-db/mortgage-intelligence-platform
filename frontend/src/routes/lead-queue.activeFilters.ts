import type { LeadFunnelStage } from '../lib/api';
import { FUNNEL_STAGE_LABELS, LEAD_TABLE_VIEW_PARAM } from './lead-queue.filters';

/**
 * Active NON-core Lead Queue filters, rendered as removable `.filter` chips in
 * the hero while their pills sit collapsed behind "More filters" (audit
 * tables-06). The core pills (state, segment, relationship, product,
 * approval) stay visible in the filter row, so they are not repeated here.
 * Each chip names the URL params it removes; values come from the parsed,
 * allowlisted state, never from the raw query string.
 */
export interface LeadQueueFilterChip {
  key: string;
  /** The pill label the value belongs to ("OWNER LINK"). */
  label: string;
  value: string;
  /** Query params dropped when the chip is removed. */
  params: readonly string[];
}

/** Portfolio-criteria keys that have a pill behind "More filters" or only arrive by deep link. */
const PORTFOLIO_CHIP_LABELS: Readonly<Record<string, string>> = {
  occupancy: 'OCCUPANCY',
  lien_status: 'LIEN STATUS',
  loan_product: 'PRODUCT TYPE',
  origination_channel: 'CHANNEL',
  min_equity_pct_label: 'EQUITY',
  owner_link: 'OWNER LINK',
  purchase_intent: 'PURCHASE INTENT',
  marketing_eligibility: 'CONTACTABILITY',
  consent_status: 'CONSENT',
  recency: 'RECENCY',
};

/** The non-core pills the "More filters" panel holds, for its active count. */
export const MORE_FILTER_PARAMS: readonly string[] = [
  'target_lender_ref',
  'owner_link',
  'purchase_intent',
  'loan_product',
  'origination_channel',
  'marketing_eligibility',
  'consent_status',
  'recency',
  'outreach_status',
  'assigned_to',
  'aged_days',
];

export interface LeadQueueActiveFilterInput {
  targetLenderRef?: string;
  portfolioCriteria?: Record<string, string>;
  outreachStatus: string;
  assignedTo?: string;
  agedDays: number | null;
  zipFilter?: string;
  zipFilters: readonly string[];
  cityFilters: readonly string[];
  countyFilter?: string;
  countyFilters: readonly string[];
  borrowerIdFilters: readonly string[];
  cohortId?: string;
  funnelStage?: LeadFunnelStage;
}

function titleCase(value: string): string {
  return value.split('_').map((part) => part.charAt(0).toUpperCase() + part.slice(1)).join(' ');
}

export function leadQueueActiveFilterChips(input: LeadQueueActiveFilterInput): LeadQueueFilterChip[] {
  const chips: LeadQueueFilterChip[] = [];
  const push = (key: string, label: string, value: string, params: readonly string[] = [key]) => {
    chips.push({ key, label, value, params });
  };
  if (input.targetLenderRef) push('target_lender_ref', 'TARGET LIEN HOLDER', input.targetLenderRef);
  for (const [key, value] of Object.entries(input.portfolioCriteria ?? {})) {
    const label = PORTFOLIO_CHIP_LABELS[key];
    if (label) push(key, label, value);
  }
  if (input.outreachStatus !== 'any') push('outreach_status', 'OUTREACH', titleCase(input.outreachStatus));
  if (input.assignedTo) push('assigned_to', 'ASSIGNED', input.assignedTo);
  if (input.agedDays) push('aged_days', 'AGING', `Aged >${input.agedDays}d`);
  if (input.funnelStage) push('funnel_stage', 'STAGE', FUNNEL_STAGE_LABELS[input.funnelStage]);
  if (input.zipFilter) push('zip', 'ZIP', input.zipFilter);
  if (input.zipFilters.length > 0) push('zips', 'ZIPS', `${input.zipFilters.length} selected`);
  // Spell city pairs out: `CHICAGO~IL` names the state each city resolved in.
  if (input.cityFilters.length > 0) push('cities', 'CITIES', input.cityFilters.join(', '));
  if (input.countyFilter) push('county', 'COUNTY', input.countyFilter);
  if (input.countyFilters.length > 0) push('counties', 'COUNTIES', `${input.countyFilters.length} selected`);
  if (input.borrowerIdFilters.length > 0) push('borrower_ids', 'BORROWERS', `${input.borrowerIdFilters.length} selected`);
  if (input.cohortId) push('cohort_id', 'COHORT', 'Genie cohort');
  return chips;
}

/** Count of active filters whose pills sit behind "More filters". */
export function moreFiltersActiveCount(chips: readonly LeadQueueFilterChip[]): number {
  return chips.filter((chip) => chip.params.some((param) => MORE_FILTER_PARAMS.includes(param))).length;
}

export function searchParamsWithoutFilter(
  searchParams: URLSearchParams,
  params: readonly string[],
): URLSearchParams {
  const next = new URLSearchParams(searchParams);
  for (const param of params) next.delete(param);
  return next;
}

/** True when the URL carries anything but the column preset. */
export function hasLeadQueueFilters(searchParams: URLSearchParams): boolean {
  return [...searchParams.keys()].some((key) => key !== LEAD_TABLE_VIEW_PARAM);
}

/** Clear all: drop every filter and deep-link param, keep the column preset. */
export function searchParamsCleared(searchParams: URLSearchParams): URLSearchParams {
  const next = new URLSearchParams();
  const view = searchParams.get(LEAD_TABLE_VIEW_PARAM);
  if (view) next.set(LEAD_TABLE_VIEW_PARAM, view);
  return next;
}
