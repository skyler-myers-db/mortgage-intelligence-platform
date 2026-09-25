import { ApiError, type LeadFunnelStage } from '../lib/api';
import { isPublicLenderRef, LENDER_RELATIONSHIP_OPTIONS } from '../lib/lenderFilters';
import { SEGMENT_DEFINITIONS } from '../lib/segmentMetadata';
import type { SegmentCode } from '../types';
import { HIGH_OPPORTUNITY_KPI_LABEL } from '../lib/opportunityScore';
import type { LeadTableView } from '../components/mortgage/LeadTable.columns';
import type { LeadTableSort, LeadTableSortKey, SortDir } from '../components/mortgage/LeadTable.types';
import { QUEUE_MASKED_ID_RE } from '../lib/queueContext';

// S1.3: codes, labels, and filter options derive from SEGMENT_DEFINITIONS
// (the canonical presentation registry) so a segment added there appears in
// the Lead Queue filter automatically instead of drifting in a second list.
export const SEGMENT_CODES = new Set<SegmentCode>(SEGMENT_DEFINITIONS.map((d) => d.code));
export const SEGMENT_CODE_LABELS: Record<SegmentCode, string> = Object.fromEntries(
  SEGMENT_DEFINITIONS.map((d) => [d.code, d.name]),
) as Record<SegmentCode, string>;
export const SEGMENT_FILTER_OPTIONS: readonly string[] = [
  'All segments',
  ...SEGMENT_DEFINITIONS.map((d) => d.name),
];
export const SEGMENT_OPTION_TO_CODE: Record<string, SegmentCode | null> = {
  'All segments': null,
  ...Object.fromEntries(SEGMENT_DEFINITIONS.map((d) => [d.name, d.code])),
};
export const PRODUCT_FILTER_OPTIONS = ['All products', 'Refi', 'HELOC', 'Cash-out', 'Purchase', 'Retention'] as const;
// S1.6 — borrower loan-product-type and origination-channel dimensions. Values
// are the reviewed display labels the backend accepts as query-param values
// (the API maps them to the lowercase gold tokens). "All ..." is the no-op.
export const LOAN_PRODUCT_FILTER_OPTIONS = ['All loan products', 'Conventional', 'Jumbo', 'FHA', 'VA', 'Other', 'Unknown'] as const;
export const ORIGINATION_CHANNEL_FILTER_OPTIONS = ['All channels', 'Loan officer', 'Digital', 'Branch', 'Call center', 'Unknown'] as const;
export const OWNER_LINK_FILTER_OPTIONS = ['All', 'Single-property owner', 'Multi-property (2-4)', 'Portfolio investor (5+)'] as const;
export const PURCHASE_INTENT_FILTER_OPTIONS = ['All', 'Listed for sale', 'HELOC intent', 'Both'] as const;
export const CONTACTABILITY_FILTER_OPTIONS = ['Eligible only', 'Any', 'Suppressed only'] as const;
export const CONSENT_FILTER_OPTIONS = ['Any', 'Opt-in', 'Opt-out', 'Unknown'] as const;
export const RECENCY_FILTER_OPTIONS = ['Any', 'Untouched 30d', 'Untouched 60d', 'Untouched 90d'] as const;
export const APPROVAL_FILTER_OPTIONS = ['Any approval', 'Approved', 'Pending', 'Rejected', 'Hold'] as const;
export const OUTREACH_FILTER_OPTIONS = ['Any outreach', 'None', 'Queued', 'Actioned', 'Sent', 'Bounced', 'Replied'] as const;
export const AGING_FILTER_OPTIONS = ['Any age', 'Aged >7d', 'Aged >14d', 'Aged >30d'] as const;
export const FUNNEL_STAGE_LABELS: Record<LeadFunnelStage, string> = {
  addressable: 'Addressable',
  in_the_money: 'Refi economics',
  high_opportunity: HIGH_OPPORTUNITY_KPI_LABEL,
  offer_recommended: 'Primary offer selected',
  approved: 'Approved',
  actioned: 'Actioned',
};
const FUNNEL_STAGES = new Set<LeadFunnelStage>(
  Object.keys(FUNNEL_STAGE_LABELS) as LeadFunnelStage[],
);
const PORTFOLIO_FILTER_KEYS = [
  'occupancy',
  'lien_status',
  'lender_relationship',
  'target_lender_ref',
  'product',
  'loan_product',
  'origination_channel',
  'min_equity_pct_label',
  'owner_link',
  'purchase_intent',
  'marketing_eligibility',
  'consent_status',
  'recency',
] as const;
type PortfolioFilterKey = (typeof PORTFOLIO_FILTER_KEYS)[number];

export function parseCsvParam(
  raw: string | null,
  pattern: RegExp,
  max: number,
): string[] {
  if (!raw) return [];
  const out: string[] = [];
  for (const value of raw.split(',')) {
    const trimmed = value.trim().toUpperCase();
    if (!pattern.test(trimmed) || out.includes(trimmed)) continue;
    out.push(trimmed);
    if (out.length >= max) break;
  }
  return out;
}

export function parseSegmentCodes(raw: string | null): SegmentCode[] {
  if (!raw) return [];
  const out: SegmentCode[] = [];
  for (const value of raw.split(',')) {
    const code = value.trim().toLowerCase() as SegmentCode;
    if (!SEGMENT_CODES.has(code) || out.includes(code)) continue;
    out.push(code);
  }
  return out;
}

export function parseBorrowerIds(raw: string | null): string[] {
  if (!raw) return [];
  const out: string[] = [];
  for (const value of raw.split(',')) {
    const borrowerId = value.trim();
    if (!borrowerId.startsWith('B-') || out.includes(borrowerId)) continue;
    out.push(borrowerId);
    if (out.length >= 20) break;
  }
  return out;
}

export function parseFunnelStage(raw: string | null): LeadFunnelStage | undefined {
  const value = raw?.trim() as LeadFunnelStage | undefined;
  return value && FUNNEL_STAGES.has(value) ? value : undefined;
}

export function parseTargetLenderRef(raw: string | null, allowedLenderRefs: readonly string[]): string | undefined {
  const value = raw?.trim();
  if (!value) return undefined;
  if (value === 'All') return undefined;
  return isPublicLenderRef(value, allowedLenderRefs) ? value : undefined;
}

function normalizePurchaseIntent(value: string): string {
  return value === 'Recent permit activity' ? 'HELOC intent' : value;
}

function titleCaseWorkflowStatus(value: string): string {
  return value.split('_').map((part) => part.charAt(0).toUpperCase() + part.slice(1)).join(' ');
}

export function approvalFilterDisplayValue(
  approvalStatus: string,
  funnelStage?: LeadFunnelStage,
): string {
  if (approvalStatus !== 'any') return titleCaseWorkflowStatus(approvalStatus);
  return funnelStage === 'approved' ? 'Approved' : 'Any approval';
}

export function outreachFilterDisplayValue(
  outreachStatus: string,
  funnelStage?: LeadFunnelStage,
): string {
  if (outreachStatus !== 'any') return titleCaseWorkflowStatus(outreachStatus);
  return funnelStage === 'actioned' ? 'Actioned' : 'Any outreach';
}

export function funnelStageDisplayValue(stage: LeadFunnelStage): string {
  return FUNNEL_STAGE_LABELS[stage];
}

export function segmentDisplayLabel(code: SegmentCode | string): string {
  return SEGMENT_CODE_LABELS[code as SegmentCode] ?? 'Unknown segment';
}

export interface SegmentFilterChip {
  code: SegmentCode;
  label: string;
}

/**
 * S8: one removable chip per active segment filter, whether the deep link
 * arrived as a single `?segment=` or a composed `?segment_codes=` selection.
 */
export function segmentFilterChips(
  segment?: SegmentCode,
  segmentCodes: SegmentCode[] = [],
): SegmentFilterChip[] {
  const codes = segmentCodes.length > 0 ? segmentCodes : segment ? [segment] : [];
  return codes.map((code) => ({ code, label: segmentDisplayLabel(code) }));
}

/**
 * S8: recompute the URL state after removing one segment chip. The server
 * re-runs the composed predicate for whatever remains:
 *   - 2+ codes remain → keep `segment_codes` (+ the current `segment_mode`)
 *   - exactly 1 remains → collapse to the single-segment `segment` param
 *   - none remain → drop the segment filter entirely
 * Every other query param is preserved untouched.
 */
export function searchParamsAfterSegmentRemoval(
  searchParams: URLSearchParams,
  code: SegmentCode,
): URLSearchParams {
  const activeSegment = parseSegmentCodes(searchParams.get('segment'))[0];
  const activeCodes = parseSegmentCodes(searchParams.get('segment_codes'));
  const codes = activeCodes.length > 0 ? activeCodes : activeSegment ? [activeSegment] : [];
  const remaining = codes.filter((c) => c !== code);
  const mode = searchParams.get('segment_mode')?.trim().toLowerCase() === 'all' ? 'all' : 'any';
  const next = new URLSearchParams(searchParams);
  next.delete('segment');
  next.delete('segment_codes');
  next.delete('segment_mode');
  if (remaining.length === 1) {
    next.set('segment', remaining[0]);
  } else if (remaining.length > 1) {
    next.set('segment_codes', remaining.join(','));
    next.set('segment_mode', mode);
  }
  return next;
}

export function segmentFilterDisplayValue(
  segment?: SegmentCode,
  segmentCodes: SegmentCode[] = [],
  segmentMode: 'any' | 'all' = 'any',
): string {
  if (segment) return segmentDisplayLabel(segment);
  if (segmentCodes.length === 1) return segmentDisplayLabel(segmentCodes[0]);
  if (segmentCodes.length > 1) {
    return `${segmentCodes.length} segments selected (${segmentMode === 'all' ? 'all selected' : 'any selected'})`;
  }
  return 'All segments';
}

// S1.6 — deep links may carry the backend's lowercase/snake_case tokens
// (e.g. ?loan_product=jumbo, ?origination_channel=loan_officer) instead of
// the display labels. Mirrors _LOAN_PRODUCT_ALIASES / _ORIGINATION_CHANNEL_ALIASES
// in backend/schemas/portfolio.py; keys are lowercased with spaces -> underscores.
const LOAN_PRODUCT_VALUE_ALIASES: Record<string, string> = {
  all: 'All loan products',
  all_loan_products: 'All loan products',
  conventional: 'Conventional',
  jumbo: 'Jumbo',
  fha: 'FHA',
  va: 'VA',
  other: 'Other',
  unknown: 'Unknown',
};
const ORIGINATION_CHANNEL_VALUE_ALIASES: Record<string, string> = {
  all: 'All channels',
  all_channels: 'All channels',
  loan_officer: 'Loan officer',
  digital: 'Digital',
  branch: 'Branch',
  call_center: 'Call center',
  unknown: 'Unknown',
};
const PORTFOLIO_FILTER_VALUE_ALIASES: Partial<Record<PortfolioFilterKey, Record<string, string>>> = {
  loan_product: LOAN_PRODUCT_VALUE_ALIASES,
  origination_channel: ORIGINATION_CHANNEL_VALUE_ALIASES,
};

function resolvePortfolioValueAlias(key: PortfolioFilterKey, value: string): string {
  const aliases = PORTFOLIO_FILTER_VALUE_ALIASES[key];
  return aliases?.[value.toLowerCase().replace(/ /g, '_')] ?? value;
}

const PORTFOLIO_FILTER_VALUE_SETS: Partial<Record<PortfolioFilterKey, Set<string>>> = {
  occupancy: new Set(['Owner-occupied', 'Non-owner-occupied', 'All']),
  lien_status: new Set(['Any', 'Open 1st lien', 'Open first lien', 'Open HELOC', 'Free & clear', 'Free and clear']),
  lender_relationship: new Set([...LENDER_RELATIONSHIP_OPTIONS, 'Competitor']),
  product: new Set(['All products', 'Refi', 'HELOC', 'Cash-out', 'Purchase', 'Retention']),
  loan_product: new Set(LOAN_PRODUCT_FILTER_OPTIONS),
  origination_channel: new Set(ORIGINATION_CHANNEL_FILTER_OPTIONS),
  min_equity_pct_label: new Set(['Any', '>= 15%', '>= 25%', '>= 40%', '≥ 15%', '≥ 25%', '≥ 40%']),
  owner_link: new Set(OWNER_LINK_FILTER_OPTIONS),
  purchase_intent: new Set(PURCHASE_INTENT_FILTER_OPTIONS),
  marketing_eligibility: new Set(['Eligible only', 'Any', 'Suppressed only']),
  consent_status: new Set(['Any', 'Opt-in', 'Opt-out', 'Unknown']),
  recency: new Set(['Any', 'Untouched 30d', 'Untouched 60d', 'Untouched 90d']),
};

export function isNoOpPortfolioValue(key: string, value: string): boolean {
  if (value === '' || value.startsWith('All')) return true;
  if (key === 'marketing_eligibility') return value === 'Eligible only';
  return value === 'Any';
}

function sanitizePortfolioCriteria(
  raw: Record<string, string | undefined>,
  allowedLenderRefs: readonly string[] = [],
): Record<string, string> | undefined {
  const criteria: Record<string, string> = {};
  for (const key of PORTFOLIO_FILTER_KEYS) {
    const trimmed = key === 'purchase_intent'
      ? normalizePurchaseIntent(raw[key]?.trim() ?? '')
      : raw[key]?.trim();
    if (!trimmed) continue;
    const value = resolvePortfolioValueAlias(key, trimmed);
    if (key === 'target_lender_ref' && !isPublicLenderRef(value, allowedLenderRefs)) continue;
    const allowedValues = PORTFOLIO_FILTER_VALUE_SETS[key];
    if (allowedValues && !allowedValues.has(value)) continue;
    if (isNoOpPortfolioValue(key, value)) continue;
    criteria[key] = value;
  }
  return Object.keys(criteria).length > 0 ? criteria : undefined;
}

export function parsePortfolioCriteria(
  sp: URLSearchParams,
  allowedLenderRefs: readonly string[],
): Record<string, string> | undefined {
  return sanitizePortfolioCriteria(
    Object.fromEntries(PORTFOLIO_FILTER_KEYS.map((key) => [key, sp.get(key) ?? undefined])),
    allowedLenderRefs,
  );
}

const PORTFOLIO_FILTER_LABELS: Record<string, string> = {
  occupancy: 'occupancy',
  lien_status: 'lien',
  lender_relationship: 'relationship',
  target_lender_ref: 'lender',
  product: 'product',
  loan_product: 'Product type',
  origination_channel: 'Origination channel',
  min_equity_pct_label: 'equity',
  owner_link: 'owner link',
  purchase_intent: 'purchase intent',
  marketing_eligibility: 'contactability',
  consent_status: 'consent',
  recency: 'recency',
};

export function portfolioFilterEntries(criteria: Record<string, string> | undefined) {
  if (!criteria) return [];
  return Object.entries(criteria).map(([key, value]) => ({
    key,
    label: PORTFOLIO_FILTER_LABELS[key] ?? key.replace(/_/g, ' '),
    value,
  }));
}

export interface LeadQueueExportFiltersInput {
  segment?: SegmentCode;
  segmentCodes?: SegmentCode[];
  segmentMode?: 'any' | 'all';
  stateFilter?: string;
  zipFilter?: string;
  stateFilters?: string[];
  zipFilters?: string[];
  /** `CITY~ST` pairs. Unlisted keys are silently dropped by this
   *  allowlist, so an export of a city cohort would otherwise describe
   *  itself as unfiltered. */
  cityFilters?: string[];
  borrowerIdFilters?: string[];
  countyFilter?: string;
  countyFilters?: string[];
  targetLenderRef?: string;
  targetLenderRefs?: readonly string[];
  portfolioCriteria?: Record<string, string>;
  approvalStatus?: string;
  outreachStatus?: string;
  assignedTo?: string;
  agedDays?: number | null;
  cohortId?: string;
  funnelStage?: LeadFunnelStage;
}

export function buildLeadQueueExportFilters(input: LeadQueueExportFiltersInput): string {
  const rendered = leadQueueFilterParams(input).toString();
  return rendered.length > 0 ? rendered : 'none';
}

/** The allowlisted, sanitized filter grammar, as URL params (export and share). */
function leadQueueFilterParams(input: LeadQueueExportFiltersInput): URLSearchParams {
  const params = new URLSearchParams();
  if (input.segment) params.set('segment', input.segment);
  if (input.segmentCodes?.length) {
    params.set('segment_codes', input.segmentCodes.join(','));
    params.set('segment_mode', input.segmentMode === 'all' ? 'all' : 'any');
  }
  if (input.stateFilter) params.set('state', input.stateFilter);
  if (input.zipFilter) params.set('zip', input.zipFilter);
  if (input.stateFilters?.length) params.set('states', input.stateFilters.join(','));
  if (input.zipFilters?.length) params.set('zips', input.zipFilters.join(','));
  if (input.cityFilters?.length) params.set('cities', input.cityFilters.join(','));
  if (input.borrowerIdFilters?.length) {
    params.set('borrower_ids', input.borrowerIdFilters.join(','));
  }
  if (input.countyFilter && /^\d{5}$/.test(input.countyFilter)) {
    params.set('county', input.countyFilter);
  }
  if (input.countyFilters?.length) {
    params.set('counties', input.countyFilters.join(','));
  }
  if (input.targetLenderRef && isPublicLenderRef(input.targetLenderRef, input.targetLenderRefs ?? [])) {
    params.set('target_lender_ref', input.targetLenderRef);
  }
  if (input.approvalStatus && input.approvalStatus !== 'any') params.set('approval_status', input.approvalStatus);
  if (input.outreachStatus && input.outreachStatus !== 'any') params.set('outreach_status', input.outreachStatus);
  if (input.assignedTo) params.set('assigned_to', input.assignedTo);
  if (input.agedDays) params.set('aged_days', String(input.agedDays));
  if (input.funnelStage) params.set('funnel_stage', input.funnelStage);
  const safePortfolioCriteria = sanitizePortfolioCriteria(input.portfolioCriteria ?? {}, input.targetLenderRefs ?? []);
  for (const key of PORTFOLIO_FILTER_KEYS) {
    const value = safePortfolioCriteria?.[key];
    if (value) params.set(key, value);
  }
  if (input.cohortId && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(input.cohortId)) {
    params.set('cohort_id', input.cohortId);
  }
  return params;
}

/**
 * The six URL params a Growth Agent handoff carries its cohort proof in.
 * They bind the rows to one agent run for THIS session: a shared link never
 * carries them (the recipient's queue is re-verified on its own).
 */
export const GROWTH_AGENT_PROOF_PARAMS = [
  'growth_agent_run_id',
  'actionable_total',
  'actionable_cohort_fingerprint',
  'actionable_snapshot_id',
  'tool_result_hash',
  'growth_handoff',
] as const;

/**
 * `?assigned_to=me`: the "Assigned to me" preset (audit tables-09). Resolved
 * to the signed-in actor's email at request time, so the URL, the history
 * and a copied link never hold an email the preset wrote.
 */
export const ASSIGNED_TO_ME = 'me';

export function isAssignedToMe(value: string | null | undefined): boolean {
  return value?.trim().toLowerCase() === ASSIGNED_TO_ME;
}

/** Every param the queue reads; anything else in a URL is unrecognized. */
const KNOWN_QUEUE_PARAMS: readonly string[] = [
  'segment', 'segment_codes', 'segment_mode', 'state', 'zip', 'states', 'zips', 'cities',
  'borrower_ids', 'county', 'counties', 'approval_status', 'outreach_status', 'assigned_to',
  'aged_days', 'funnel_stage', 'cohort_id', ...PORTFOLIO_FILTER_KEYS,
  'sort', 'dir', 'row', 'view', 'campaign_id', 'variant_name', ...GROWTH_AGENT_PROOF_PARAMS,
];

export interface LeadQueueShare {
  /** `?...` or '' — the query string a copied link carries. */
  search: string;
  /** What the link leaves out, in plain words (for the toast). */
  omitted: string[];
}

/**
 * The query a "Copy link" carries (audit tables-09): the allowlisted,
 * sanitized filter grammar (the export's own), the segment mode, the sort
 * and direction, the column preset, the campaign binding, and `assigned_to`
 * only when it is `me`. Left out: the open row (a place, not a view), an
 * `assigned_to` that holds an email, the Growth Agent proof and unknown
 * params. `filters.assignedTo` is the RAW URL value, never the resolved one.
 */
export function leadQueueShareParams(
  raw: URLSearchParams,
  filters: LeadQueueExportFiltersInput,
): LeadQueueShare {
  const params = leadQueueFilterParams({
    ...filters,
    assignedTo: isAssignedToMe(filters.assignedTo) ? ASSIGNED_TO_ME : undefined,
  });
  const { sort } = parseLeadTablePlace(raw);
  if (sort) {
    params.set('sort', sort.key);
    params.set('dir', sort.dir);
  }
  const view = parseLeadTableView(raw.get(LEAD_TABLE_VIEW_PARAM));
  if (view !== 'default') params.set(LEAD_TABLE_VIEW_PARAM, view);
  for (const key of ['campaign_id', 'variant_name'] as const) {
    const value = raw.get(key)?.trim();
    if (value) params.set(key, value);
  }
  const omitted = new Set<string>();
  let unknown = 0;
  for (const key of new Set(raw.keys())) {
    if (params.has(key)) continue;
    if (key === 'row') omitted.add('the open row');
    else if (key === 'assigned_to' && raw.get(key)?.trim()) omitted.add('the assignee email');
    else if ((GROWTH_AGENT_PROOF_PARAMS as readonly string[]).includes(key)) omitted.add('the Growth Agent proof');
    else if (!KNOWN_QUEUE_PARAMS.includes(key)) unknown += 1;
  }
  if (unknown > 0) omitted.add(unknown === 1 ? '1 unrecognized parameter' : `${unknown} unrecognized parameters`);
  const rendered = params.toString();
  return { search: rendered ? `?${rendered}` : '', omitted: [...omitted] };
}

/**
 * Column preset of the ranked-borrower table (audit tables-05), persisted as
 * `?view=sales-ops`. The Default view is the prototype's columns plus the
 * merged Status cell and never appears in the URL. `view` is a display
 * preference, not a filter: it is not sent to /api/leads, not exported, and
 * survives Clear all.
 */
export const LEAD_TABLE_VIEW_PARAM = 'view';

export function parseLeadTableView(raw: string | null): LeadTableView {
  return raw?.trim().toLowerCase() === 'sales-ops' ? 'sales-ops' : 'default';
}

export function searchParamsWithLeadTableView(
  searchParams: URLSearchParams,
  view: LeadTableView,
): URLSearchParams {
  const next = new URLSearchParams(searchParams);
  if (view === 'default') next.delete(LEAD_TABLE_VIEW_PARAM);
  else next.set(LEAD_TABLE_VIEW_PARAM, view);
  return next;
}

/**
 * The reader's PLACE in the ranked table (audit shell-03, runtime-08,
 * tables-09 phase 1): the client-side sort and the expanded borrower, kept in
 * the URL so Back from a dossier, a reload and "Return to results" land where
 * the reader left. Like `?view=`, these are NOT filters: they never reach
 * `/api/leads`, the leads query key, the Growth Agent proof key or the export
 * filter string (the export states its own `rowOrder`), and they never enable
 * Clear all.
 *
 *   sort  a sortable column key; rank order is the absence of `sort` and is
 *         never written. An unknown value is dropped.
 *   dir   `asc` or `desc` (default `desc`); ignored without `sort`.
 *   row   the expanded borrower's masked id (`B-` + 13); anything else is
 *         dropped. Restoring it only re-opens the in-memory preview: no
 *         borrower, proof or draft request.
 */
export const LEAD_TABLE_PLACE_PARAMS = ['sort', 'dir', 'row'] as const;

const LEAD_TABLE_SORT_KEYS: ReadonlySet<LeadTableSortKey> = new Set<LeadTableSortKey>([
  'relationship',
  'assignment',
  'outreach',
  'equity',
  'rate',
  'score',
  'confidence',
]);

export interface LeadTablePlace {
  sort: LeadTableSort | null;
  row: string | null;
}

function isLeadTableSortKey(value: string): value is LeadTableSortKey {
  return LEAD_TABLE_SORT_KEYS.has(value as LeadTableSortKey);
}

export function parseLeadTablePlace(searchParams: URLSearchParams): LeadTablePlace {
  const rawSort = searchParams.get('sort')?.trim().toLowerCase() ?? '';
  const dir: SortDir = searchParams.get('dir')?.trim().toLowerCase() === 'asc' ? 'asc' : 'desc';
  const rawRow = searchParams.get('row')?.trim() ?? '';
  return {
    sort: isLeadTableSortKey(rawSort) ? { key: rawSort, dir } : null,
    row: QUEUE_MASKED_ID_RE.test(rawRow) ? rawRow : null,
  };
}

/**
 * Write the table place into a copy of `searchParams`; every other param is
 * kept. A patch names only what it changes: `sort: null` returns to rank
 * order (drops `sort` and `dir`), `row: null` collapses.
 */
export function searchParamsWithLeadTablePlace(
  searchParams: URLSearchParams,
  patch: Partial<LeadTablePlace>,
): URLSearchParams {
  const next = new URLSearchParams(searchParams);
  if (patch.sort !== undefined) {
    if (patch.sort === null) {
      next.delete('sort');
      next.delete('dir');
    } else {
      next.set('sort', patch.sort.key);
      next.set('dir', patch.sort.dir);
    }
  }
  if (patch.row !== undefined) {
    if (patch.row !== null && QUEUE_MASKED_ID_RE.test(patch.row)) next.set('row', patch.row);
    else next.delete('row');
  }
  return next;
}

export interface LeadQueueLoadErrorState {
  message: string;
  invalidFilters: boolean;
}

export function formatLeadQueueLoadError(error: unknown): LeadQueueLoadErrorState {
  if (error instanceof ApiError && error.status === 422) {
    const issueText = error.validationIssues.length > 0
      ? error.validationIssues.map((issue) => `${issue.field}: ${issue.message}`).join('; ')
      : error.message;
    return {
      message: `Lead queue filters are invalid. ${issueText}. Clear filters or choose a supported filter value.`,
      invalidFilters: true,
    };
  }
  if (error instanceof Error) {
    return {
      message: `Couldn't load leads: ${error.message}`,
      invalidFilters: false,
    };
  }
  return {
    message: "Couldn't load leads.",
    invalidFilters: false,
  };
}
