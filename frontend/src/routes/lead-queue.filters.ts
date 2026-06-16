import { ApiError, type LeadFunnelStage } from '../lib/api';
import { isPublicLenderRef, LENDER_RELATIONSHIP_OPTIONS } from '../lib/lenderFilters';
import type { SegmentCode } from '../types';

export const SEGMENT_CODES = new Set<SegmentCode>(['itm', 'listed', 'permit', 'investor', 'equity', 'retention']);
export const SEGMENT_CODE_LABELS: Record<SegmentCode, string> = {
  itm: 'Prime Refi Candidates',
  listed: 'Listed for Sale',
  permit: 'HELOC Intent',
  investor: 'Investor / Multi-Property',
  equity: 'Home Equity Candidate',
  retention: 'Retention Risk',
};
export const SEGMENT_FILTER_OPTIONS = [
  'All segments',
  SEGMENT_CODE_LABELS.itm,
  SEGMENT_CODE_LABELS.listed,
  SEGMENT_CODE_LABELS.permit,
  SEGMENT_CODE_LABELS.investor,
  SEGMENT_CODE_LABELS.equity,
  SEGMENT_CODE_LABELS.retention,
] as const;
export const SEGMENT_OPTION_TO_CODE: Record<string, SegmentCode | null> = {
  'All segments': null,
  [SEGMENT_CODE_LABELS.itm]: 'itm',
  [SEGMENT_CODE_LABELS.listed]: 'listed',
  [SEGMENT_CODE_LABELS.permit]: 'permit',
  [SEGMENT_CODE_LABELS.investor]: 'investor',
  [SEGMENT_CODE_LABELS.equity]: 'equity',
  [SEGMENT_CODE_LABELS.retention]: 'retention',
};
export const PRODUCT_FILTER_OPTIONS = ['All products', 'Refi', 'HELOC', 'Cash-out', 'Purchase', 'Retention'] as const;
export const CONTACTABILITY_FILTER_OPTIONS = ['Eligible only', 'Any', 'Suppressed only'] as const;
export const CONSENT_FILTER_OPTIONS = ['Any', 'Opt-in', 'Opt-out', 'Unknown'] as const;
export const RECENCY_FILTER_OPTIONS = ['Any', 'Untouched 30d', 'Untouched 60d', 'Untouched 90d'] as const;
export const APPROVAL_FILTER_OPTIONS = ['Any approval', 'Approved', 'Pending', 'Rejected', 'Hold'] as const;
export const OUTREACH_FILTER_OPTIONS = ['Any outreach', 'None', 'Queued', 'Actioned', 'Sent', 'Bounced', 'Replied'] as const;
export const AGING_FILTER_OPTIONS = ['Any age', 'Aged >7d', 'Aged >14d', 'Aged >30d'] as const;
export const FUNNEL_STAGE_LABELS: Record<LeadFunnelStage, string> = {
  addressable: 'Addressable',
  in_the_money: 'Refi economics',
  high_opportunity: 'Opportunity score 75+',
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
    const code = value.trim() as SegmentCode;
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

export function segmentDisplayLabel(code: SegmentCode): string {
  return SEGMENT_CODE_LABELS[code];
}

export function segmentFilterDisplayValue(segment?: SegmentCode, segmentCodes: SegmentCode[] = []): string {
  if (segment) return segmentDisplayLabel(segment);
  if (segmentCodes.length === 1) return segmentDisplayLabel(segmentCodes[0]);
  if (segmentCodes.length > 1) return `${segmentCodes.length} segments selected`;
  return 'All segments';
}

const PORTFOLIO_FILTER_VALUE_SETS: Partial<Record<PortfolioFilterKey, Set<string>>> = {
  occupancy: new Set(['Owner-occupied', 'Non-owner-occupied', 'All']),
  lien_status: new Set(['Any', 'Open 1st lien', 'Open first lien', 'Open HELOC', 'Free & clear', 'Free and clear']),
  lender_relationship: new Set([...LENDER_RELATIONSHIP_OPTIONS, 'Competitor']),
  product: new Set(['All products', 'Refi', 'HELOC', 'Cash-out', 'Purchase', 'Retention']),
  min_equity_pct_label: new Set(['Any', '>= 15%', '>= 25%', '>= 40%', '≥ 15%', '≥ 25%', '≥ 40%']),
  owner_link: new Set(['All', 'Single-property owner', 'Multi-property (2-4)', 'Portfolio investor (5+)']),
  purchase_intent: new Set(['All', 'Listed for sale', 'HELOC intent', 'Both']),
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
    const value = key === 'purchase_intent'
      ? normalizePurchaseIntent(raw[key]?.trim() ?? '')
      : raw[key]?.trim();
    if (!value) continue;
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
  const rendered = params.toString();
  return rendered.length > 0 ? rendered : 'none';
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
