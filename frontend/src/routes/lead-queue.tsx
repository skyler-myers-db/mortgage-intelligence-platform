import { useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link, useSearchParams } from 'react-router-dom';
import { api, ApiError, type LeadFunnelStage, type LeadsPageResult } from '../lib/api';
import { useConfigOptionsQuery } from '../lib/configOptionsQuery';
import { useWarmingUpRetry } from '../lib/useWarmingUpRetry';
import type { PortfolioPreview, SalesAgingLead, SalesConversionResponse, SalesStandupResponse, SalesTeamMember, SegmentCode } from '../types';
import { PageShell } from '../components/layout/PageShell';
import { LeadTable } from '../components/mortgage/LeadTable';
import { Chip } from '../components/Primitives';
import { WarmingUpBlock } from '../components/ui/WarmingUpBlock';
import { FilterSelect } from '../components/ui/FilterSelect';
import { useFootprint } from '../components/FootprintProvider';
import { Skeleton } from '../components/ui/Skeleton';
import { queryKeys } from '../lib/queryKeys';
import { isPublicLenderRef, LENDER_RELATIONSHIP_OPTIONS } from '../lib/lenderFilters';

/**
 * Lead Queue — deep-dive table route. Full borrower list (filtered by segment
 * URL param if present). Row expand opens the inline dossier preview.
 *
 * Honors `?state=XX`, `?zip=NNNNN`, `?states=IL,TX`, `?zips=60617,75217`,
 * `?borrower_ids=B-...`, `?target_lender_ref=Competitor%20A`,
 * `?cohort_id=<uuid>`,
 * and `?county=FFFFF` query params so the
 * home/segments geography drill can deep-link into a filtered view. State,
 * county, and ZIP filters are pushed down to `/api/leads`, which reads
 * borrower_360 for geo cohorts so the queue preserves the map's counted
 * population.
 */

const SEGMENT_CODES = new Set<SegmentCode>(['itm', 'listed', 'permit', 'investor', 'equity', 'retention']);
const SEGMENT_FILTER_OPTIONS = ['All segments', 'In the Money', 'Investor / Multi-Property', 'Home Equity Candidate', 'Retention Risk'] as const;
const SEGMENT_OPTION_TO_CODE: Record<string, SegmentCode | null> = {
  'All segments': null,
  'In the Money': 'itm',
  'Investor / Multi-Property': 'investor',
  'Home Equity Candidate': 'equity',
  'Retention Risk': 'retention',
};
const PRODUCT_FILTER_OPTIONS = ['All products', 'Refi', 'HELOC', 'Cash-out', 'Purchase', 'Retention'] as const;
const CONTACTABILITY_FILTER_OPTIONS = ['Eligible only', 'Any', 'Suppressed only'] as const;
const CONSENT_FILTER_OPTIONS = ['Any', 'Opt-in', 'Opt-out', 'Unknown'] as const;
const RECENCY_FILTER_OPTIONS = ['Any', 'Untouched 30d', 'Untouched 60d', 'Untouched 90d'] as const;
const APPROVAL_FILTER_OPTIONS = ['Any approval', 'Approved', 'Pending', 'Rejected', 'Hold'] as const;
const OUTREACH_FILTER_OPTIONS = ['Any outreach', 'None', 'Queued', 'Actioned', 'Sent', 'Bounced', 'Replied'] as const;
const AGING_FILTER_OPTIONS = ['Any age', 'Aged >7d', 'Aged >14d', 'Aged >30d'] as const;
const FUNNEL_STAGE_LABELS: Record<LeadFunnelStage, string> = {
  addressable: 'Addressable',
  in_the_money: 'In the Money',
  high_opportunity: 'High Opportunity',
  offer_recommended: 'Offer Recommended',
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

function isoDate(date: Date): string {
  return date.toISOString().slice(0, 10);
}

function relativeIsoDate(days: number): string {
  const date = new Date();
  date.setDate(date.getDate() + days);
  return isoDate(date);
}

function weekStartIsoDate(): string {
  const date = new Date();
  const day = date.getDay();
  const diff = day === 0 ? -6 : 1 - day;
  date.setDate(date.getDate() + diff);
  return isoDate(date);
}

interface AdminRulesSummary {
  offer_rules_version?: string | null;
}

function parseCsvParam(
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

function parseSegmentCodes(raw: string | null): SegmentCode[] {
  if (!raw) return [];
  const out: SegmentCode[] = [];
  for (const value of raw.split(',')) {
    const code = value.trim() as SegmentCode;
    if (!SEGMENT_CODES.has(code) || out.includes(code)) continue;
    out.push(code);
  }
  return out;
}

function parseBorrowerIds(raw: string | null): string[] {
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

function parseFunnelStage(raw: string | null): LeadFunnelStage | undefined {
  const value = raw?.trim() as LeadFunnelStage | undefined;
  return value && FUNNEL_STAGES.has(value) ? value : undefined;
}

function parseTargetLenderRef(raw: string | null, allowedLenderRefs: readonly string[]): string | undefined {
  const value = raw?.trim();
  if (!value) return undefined;
  if (value === 'All') return undefined;
  return isPublicLenderRef(value, allowedLenderRefs) ? value : undefined;
}

const PORTFOLIO_FILTER_VALUE_SETS: Partial<Record<PortfolioFilterKey, Set<string>>> = {
  occupancy: new Set(['Owner-occupied', 'Non-owner-occupied', 'All']),
  lien_status: new Set(['Any', 'Open 1st lien', 'Open first lien', 'Open HELOC', 'Free & clear', 'Free and clear']),
  lender_relationship: new Set([...LENDER_RELATIONSHIP_OPTIONS, 'Competitor']),
  product: new Set(['All products', 'Refi', 'HELOC', 'Cash-out', 'Purchase', 'Retention']),
  min_equity_pct_label: new Set(['Any', '≥ 15%', '≥ 25%', '≥ 40%']),
  owner_link: new Set(['All', 'Single-property owner', 'Multi-property (2-4)', 'Portfolio investor (5+)']),
  purchase_intent: new Set(['All', 'Listed for sale', 'HELOC intent', 'Recent permit activity', 'Both']),
  marketing_eligibility: new Set(['Eligible only', 'Any', 'Suppressed only']),
  consent_status: new Set(['Any', 'Opt-in', 'Opt-out', 'Unknown']),
  recency: new Set(['Any', 'Untouched 30d', 'Untouched 60d', 'Untouched 90d']),
};

function sanitizePortfolioCriteria(
  raw: Record<string, string | undefined>,
  allowedLenderRefs: readonly string[] = [],
): Record<string, string> | undefined {
  const criteria: Record<string, string> = {};
  for (const key of PORTFOLIO_FILTER_KEYS) {
    const value = raw[key]?.trim();
    if (!value) continue;
    if (key === 'target_lender_ref' && !isPublicLenderRef(value, allowedLenderRefs)) continue;
    const allowedValues = PORTFOLIO_FILTER_VALUE_SETS[key];
    if (allowedValues && !allowedValues.has(value)) continue;
    criteria[key] = value;
  }
  return Object.keys(criteria).length > 0 ? criteria : undefined;
}

function parsePortfolioCriteria(
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

function portfolioFilterEntries(criteria: Record<string, string> | undefined) {
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

function LeadQueueTableSkeleton() {
  return (
    <div className="surface lead-queue-skeleton mb-grid" aria-busy="true" role="status">
      <div className="surface__hdr surface__hdr--split">
        <div className="surface__hdr-main">
          <Skeleton width={28} height={28} rounded="md" />
          <div>
            <div className="h-4">Loading ranked borrowers</div>
            <div className="mt-2">
              <span className="muted fs-12">Fetching the current queue with the selected filters.</span>
            </div>
          </div>
        </div>
        <Skeleton width={120} height={32} rounded="md" />
      </div>
      <div className="surface__body">
        <div className="lead-queue-skeleton__table">
          {Array.from({ length: 6 }).map((_, row) => (
            <div key={row} className="lead-queue-skeleton__row">
              {Array.from({ length: 9 }).map((__, col) => (
                <Skeleton
                  key={col}
                  width={col === 0 ? 18 : col === 1 ? 120 : col === 8 ? 84 : '100%'}
                  height={col === 0 ? 18 : 14}
                  rounded={col === 0 ? 'sm' : 'pill'}
                />
              ))}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

export default function LeadQueue() {
  const [searchParams, setSearchParams] = useSearchParams();
  const footprint = useFootprint();
  const segment = (searchParams.get('segment') as SegmentCode | null) ?? undefined;
  const segmentCodes = useMemo(
    () => parseSegmentCodes(searchParams.get('segment_codes')),
    [searchParams],
  );
  const segmentMode = searchParams.get('segment_mode') === 'all' ? 'all' : 'any';
  // 2-char state code (e.g. `?state=IL`) from the home-map deep-link.
  // Uppercased defensively so `/lead-queue?state=il` still works.
  const stateFilter = (searchParams.get('state') ?? '').toUpperCase() || undefined;
  const zipFilter = (searchParams.get('zip') ?? '').trim() || undefined;
  const stateFilters = useMemo(
    () => parseCsvParam(searchParams.get('states'), /^[A-Z]{2}$/, 20),
    [searchParams],
  );
  const zipFilters = useMemo(
    () => parseCsvParam(searchParams.get('zips'), /^\d{5}$/, 50),
    [searchParams],
  );
  const borrowerIdFilters = useMemo(
    () => parseBorrowerIds(searchParams.get('borrower_ids')),
    [searchParams],
  );
  const countyFilter = (searchParams.get('county') ?? '').trim() || undefined;
  const configOptionsQuery = useConfigOptionsQuery();
  const targetLenderOptions = useMemo(() => {
    const values = configOptionsQuery.data?.target_lender_refs?.filter(Boolean);
    return values && values.length > 0 ? values : ['All'];
  }, [configOptionsQuery.data?.target_lender_refs]);
  const targetLenderRef = parseTargetLenderRef(searchParams.get('target_lender_ref'), targetLenderOptions);
  const portfolioCriteria = useMemo(
    () => parsePortfolioCriteria(searchParams, targetLenderOptions),
    [searchParams, targetLenderOptions],
  );
  const portfolioFilters = useMemo(
    () => portfolioFilterEntries(portfolioCriteria),
    [portfolioCriteria],
  );
  const cohortId = (searchParams.get('cohort_id') ?? '').trim() || undefined;
  const funnelStage = parseFunnelStage(searchParams.get('funnel_stage'));
  const stateOptions = useMemo(() => {
    const states = footprint.ready && !footprint.usingFallback
      ? footprint.states.map((s) => s.state_code).sort()
      : [];
    return ['All states', ...states];
  }, [footprint.ready, footprint.states, footprint.usingFallback]);
  const relationshipFilter = portfolioCriteria?.lender_relationship ?? 'All';
  const productFilter = portfolioCriteria?.product ?? 'All products';
  const analyticsScopeActive = Boolean(
    funnelStage || segment || segmentCodes.length > 0 || stateFilter || zipFilter || countyFilter
    || stateFilters.length > 0 || zipFilters.length > 0 || borrowerIdFilters.length > 0,
  );
  const contactabilityFilter = portfolioCriteria?.marketing_eligibility
    ?? (analyticsScopeActive ? 'Any' : 'Eligible only');
  const consentFilter = portfolioCriteria?.consent_status ?? 'Any';
  const recencyFilter = portfolioCriteria?.recency ?? 'Any';
  const approvalStatus = (searchParams.get('approval_status') ?? 'any').toLowerCase();
  const outreachStatus = (searchParams.get('outreach_status') ?? 'any').toLowerCase();
  const assignedTo = (searchParams.get('assigned_to') ?? '').trim() || undefined;
  const agedDays = Number(searchParams.get('aged_days') ?? '') || null;
  const yesterday = relativeIsoDate(-1);
  const weekStart = weekStartIsoDate();
  const today = isoDate(new Date());
  const salesTeamQuery = useQuery<SalesTeamMember[]>({
    queryKey: queryKeys.salesTeam(),
    queryFn: ({ signal }) => api.salesTeam(signal).then((team) => team.filter((member) => member.role === 'loan_officer')),
    staleTime: 60_000,
  });
  const salesOpsQuery = useQuery<{
    staleLeads: SalesAgingLead[];
    standup: SalesStandupResponse;
    conversion: SalesConversionResponse;
  }>({
    queryKey: queryKeys.salesOps(),
    queryFn: async ({ signal }) => {
      const [agingRows, standupRows, conversionRows] = await Promise.all([
        api.salesAging(7, 100, signal),
        api.salesStandup(yesterday, signal),
        api.salesConversion(weekStart, today, 'lo', signal),
      ]);
      return { staleLeads: agingRows, standup: standupRows, conversion: conversionRows };
    },
    staleTime: 30_000,
  });
  const salesTeam = salesTeamQuery.data ?? [];
  const staleLeads = salesOpsQuery.data?.staleLeads ?? [];
  const standup = salesOpsQuery.data?.standup ?? null;
  const conversion = salesOpsQuery.data?.conversion ?? null;
  const salesTeamError = salesTeamQuery.error instanceof Error ? salesTeamQuery.error.message : null;
  const salesOpsError = salesOpsQuery.error instanceof Error ? salesOpsQuery.error.message : null;
  const segmentFilter = segment
    ? (Object.entries(SEGMENT_OPTION_TO_CODE).find(([, code]) => code === segment)?.[0] ?? 'All segments')
    : segmentCodes.length === 1
      ? (Object.entries(SEGMENT_OPTION_TO_CODE).find(([, code]) => code === segmentCodes[0])?.[0] ?? 'All segments')
      : 'All segments';

  const updateParam = (key: string, value: string | null) => {
    const next = new URLSearchParams(searchParams);
    if (key === 'state') {
      ['states', 'zips', 'zip', 'county', 'borrower_ids', 'cohort_id'].forEach((k) => next.delete(k));
    }
    if (value === null || value === '' || value.startsWith('All')) next.delete(key);
    else next.set(key, value);
    if (key === 'segment') {
      next.delete('segment_codes');
      next.delete('segment_mode');
    }
    setSearchParams(next);
  };

  // 2026-05-04 FIX β: pass state + zip to the API so the geo-filtered
  // path on the backend bypasses lead_population's score >= 50 floor
  // and queries borrower_360 directly. The returned rows then match
  // the per-geo addressable counts the map tooltips report. Pre-fix,
  // the FE only filtered client-side against the top-500 from
  // lead_population, so ZIPs whose borrowers didn't make the national
  // top 500 rendered as 0 rows (the "ZIP shows 19 but queue shows 0"
  // bug). Re-runs when state, zip, or segment changes.
  const {
    data: leadsData,
    warmingUp,
    error,
    manualRetry,
  } = useWarmingUpRetry<LeadsPageResult>(
    (signal) => api.leadsPage(
      segment,
      signal,
      {
        state: stateFilter,
        zip: zipFilter,
        county: countyFilter,
        states: stateFilters,
        zips: zipFilters,
        borrowerIds: borrowerIdFilters,
      },
      {
        segmentCodes,
        segmentMode,
        targetLenderRef,
        cohortId,
        funnelStage,
        portfolioCriteria,
        approvalStatus: approvalStatus === 'any' ? 'any' : approvalStatus as 'pending' | 'approved' | 'rejected' | 'hold',
        outreachStatus: outreachStatus === 'any' ? 'any' : outreachStatus as 'none' | 'queued' | 'actioned' | 'sent' | 'bounced' | 'replied',
        assignedTo,
        agedDays,
      },
    ),
    [
      segment,
      stateFilter,
      zipFilter,
      countyFilter,
      stateFilters.join(','),
      zipFilters.join(','),
      borrowerIdFilters.join(','),
      segmentCodes.join(','),
      segmentMode,
      targetLenderRef,
      JSON.stringify(portfolioCriteria ?? {}),
      cohortId,
      funnelStage,
      approvalStatus,
      outreachStatus,
      assignedTo,
      agedDays,
    ],
    {
      queryKey: queryKeys.leads([
        'lead-queue',
        segment ?? '',
        stateFilter ?? '',
        zipFilter ?? '',
        countyFilter ?? '',
        stateFilters.join(','),
        zipFilters.join(','),
        borrowerIdFilters.join(','),
        segmentCodes.join(','),
        segmentMode,
        targetLenderRef ?? '',
        JSON.stringify(portfolioCriteria ?? {}),
        cohortId ?? '',
        funnelStage ?? '',
        approvalStatus,
        outreachStatus,
        assignedTo ?? '',
        agedDays ?? '',
      ]),
    },
  );
  const loading = leadsData === null && warmingUp === null && error === null;
  const loadError = error ? formatLeadQueueLoadError(error) : null;

  // Resolve `?county=FFFFF` → set of ZIPs via /api/geo/zip-rollups for an
  // honest scope chip only. The actual county predicate is now server-side
  // in /api/leads, so a transient rollup failure must not broaden or empty
  // the ranked borrower list.
  const [countyZips, setCountyZips] = useState<Set<string> | null>(null);
  const [exportRefreshedAt, setExportRefreshedAt] = useState<string | null>(null);
  const [rulesVersion, setRulesVersion] = useState<string | null>(null);
  useEffect(() => {
    if (!countyFilter) {
      setCountyZips(null);
      return;
    }
    const ctrl = new AbortController();
    let cancelled = false;
    api
      .zipRollups(
        countyFilter,
        ctrl.signal,
        segmentCodes.length > 0 ? segmentCodes : segment ? [segment] : null,
        segmentMode,
        portfolioCriteria,
      )
      .then((payload) => {
        if (cancelled) return;
        setCountyZips(new Set(payload.rollups.map((r) => r.zip)));
      })
      .catch(() => {
        if (!cancelled) setCountyZips(new Set());
      });
    return () => {
      cancelled = true;
      ctrl.abort();
    };
  }, [countyFilter, portfolioCriteria, segment, segmentCodes, segmentMode]);

  useEffect(() => {
    const ctrl = new AbortController();
    api
      .portfolioPreview({}, ctrl.signal)
      .then((payload: PortfolioPreview) => setExportRefreshedAt(payload.data_refreshed_at ?? null))
      .catch(() => setExportRefreshedAt(null));
    api
      .adminRules<AdminRulesSummary>(ctrl.signal)
      .then((payload) => setRulesVersion(payload.offer_rules_version ?? null))
      .catch(() => setRulesVersion(null));
    return () => ctrl.abort();
  }, []);

  const visibleLeads = useMemo(() => {
    return leadsData?.leads ?? [];
  }, [leadsData]);

  const countyLoading = Boolean(countyFilter) && countyZips === null;
  const exportContext = useMemo(() => {
    return {
      filters: buildLeadQueueExportFilters({
        segment,
        segmentCodes,
        segmentMode,
        stateFilter,
        zipFilter,
        stateFilters,
        zipFilters,
        borrowerIdFilters,
        countyFilter,
        targetLenderRef,
        targetLenderRefs: targetLenderOptions,
        portfolioCriteria,
        approvalStatus: approvalStatus === 'any' ? undefined : approvalStatus,
        outreachStatus: outreachStatus === 'any' ? undefined : outreachStatus,
        assignedTo,
        agedDays,
        cohortId,
        funnelStage,
      }),
      refreshedAt: exportRefreshedAt,
      rulesVersion,
    };
  }, [
    borrowerIdFilters,
    cohortId,
    funnelStage,
    countyFilter,
    exportRefreshedAt,
    portfolioCriteria,
    rulesVersion,
    segment,
    segmentCodes,
    segmentMode,
    stateFilter,
    stateFilters,
    targetLenderRef,
    targetLenderOptions,
    approvalStatus,
    outreachStatus,
    assignedTo,
    agedDays,
    zipFilter,
    zipFilters,
  ]);

  return (
    <PageShell
      eyebrow="Lead Queue"
      title="Ranked borrowers"
      lede="Click a row to expand the borrower preview. Approve, reject, assign to LOs, log call outcomes, or open Borrower 360 for the full dossier. Keyboard: while the expanded row is still pending, A approves and R rejects."
      heroRight={
        segment || segmentCodes.length > 0 || stateFilter || zipFilter || stateFilters.length > 0 || zipFilters.length > 0 || borrowerIdFilters.length > 0 || countyFilter || targetLenderRef || portfolioCriteria || cohortId || funnelStage || approvalStatus !== 'any' || outreachStatus !== 'any' || assignedTo || agedDays ? (
          <>
            {segment && <Chip variant="neutral">segment = {segment}</Chip>}
            {segmentCodes.length > 0 && <Chip variant="neutral">segments = {segmentCodes.join(', ')}</Chip>}
            {stateFilter && <Chip variant="neutral">state = {stateFilter}</Chip>}
            {zipFilter && <Chip variant="neutral">zip = {zipFilter}</Chip>}
            {stateFilters.length > 0 && <Chip variant="neutral">states = {stateFilters.join(', ')}</Chip>}
            {zipFilters.length > 0 && <Chip variant="neutral">zips = {zipFilters.length} selected</Chip>}
            {borrowerIdFilters.length > 0 && <Chip variant="neutral">borrowers = {borrowerIdFilters.length} selected</Chip>}
            {countyFilter && <Chip variant="neutral">county = {countyFilter}</Chip>}
            {targetLenderRef && <Chip variant="neutral">lender = {targetLenderRef}</Chip>}
            {funnelStage && <Chip variant="success">funnel = {FUNNEL_STAGE_LABELS[funnelStage]}</Chip>}
            {portfolioFilters.map((filter) => (
              <Chip key={filter.key} variant="neutral">
                {filter.label} = {filter.value}
              </Chip>
            ))}
            {approvalStatus !== 'any' && <Chip variant="neutral">approval = {approvalStatus}</Chip>}
            {outreachStatus !== 'any' && <Chip variant="neutral">outreach = {outreachStatus}</Chip>}
            {assignedTo && <Chip variant="neutral">assigned = {assignedTo}</Chip>}
            {agedDays && <Chip variant="neutral">aged &gt; {agedDays}d</Chip>}
            {cohortId && <Chip variant="success">Genie cohort</Chip>}
          </>
        ) : undefined
      }
    >
      <div className="surface mb-grid">
        <div className="surface__hdr surface__hdr--split">
          <div className="surface__hdr-main">
            <div className="h-4">Queue filters</div>
            <div className="muted fs-12">
              Narrow the operational queue without hand-editing the URL.
            </div>
          </div>
          <button
            type="button"
            className="btn btn--ghost btn--sm"
            onClick={() => setSearchParams(new URLSearchParams())}
          >
            Clear filters
          </button>
        </div>
        <div className="surface__body">
          {(funnelStage || zipFilter || countyFilter || stateFilter || stateFilters.length > 0 || zipFilters.length > 0 || borrowerIdFilters.length > 0) && (
            <div className="lead-queue-scope" aria-label="Active analytics drilldown filters">
              {funnelStage && (
                <span className="lead-queue-scope__pill">Funnel stage: {FUNNEL_STAGE_LABELS[funnelStage]}</span>
              )}
              {stateFilter && <span className="lead-queue-scope__pill">State: {stateFilter}</span>}
              {zipFilter && <span className="lead-queue-scope__pill">ZIP: {zipFilter}</span>}
              {countyFilter && <span className="lead-queue-scope__pill">County FIPS: {countyFilter}</span>}
              {stateFilters.length > 0 && <span className="lead-queue-scope__pill">States: {stateFilters.join(', ')}</span>}
              {zipFilters.length > 0 && <span className="lead-queue-scope__pill">ZIPs: {zipFilters.join(', ')}</span>}
              {borrowerIdFilters.length > 0 && <span className="lead-queue-scope__pill">Borrowers: {borrowerIdFilters.length}</span>}
              {/* Re-audit #3 P3 (2026-06-12): the map tile counts MARKETABLE
                  borrowers; this queue ranks the scored high-intent subset.
                  Without the caption the handoff reads as a numbers jump
                  (e.g. 30,833 on the ZIP tile → 1,379 ranked rows). */}
              <span className="lead-queue-scope__note muted fs-11">
                Ranked leads are the scored, marketing-eligible subset of this
                geography&apos;s marketable borrowers — intentionally smaller than
                the map tile&apos;s population count.
              </span>
            </div>
          )}
          <div className="filter-row">
            <FilterSelect
              label="STATE"
              value={stateFilter ?? 'All states'}
              options={stateOptions}
              onChange={(v) => updateParam('state', v === 'All states' ? null : v)}
            />
            <FilterSelect
              label="RELATIONSHIP"
              value={relationshipFilter}
              options={[...LENDER_RELATIONSHIP_OPTIONS]}
              onChange={(v) => updateParam('lender_relationship', v)}
            />
            <FilterSelect
              label="TARGET LIEN HOLDER"
              value={targetLenderRef ?? 'All'}
              options={targetLenderOptions}
              onChange={(v) => updateParam('target_lender_ref', v)}
            />
            <FilterSelect
              label="SEGMENT"
              value={segmentFilter}
              options={[...SEGMENT_FILTER_OPTIONS]}
              onChange={(v) => {
                const code = SEGMENT_OPTION_TO_CODE[v];
                updateParam('segment', code);
              }}
            />
            <FilterSelect
              label="PRODUCT"
              value={productFilter}
              options={[...PRODUCT_FILTER_OPTIONS]}
              onChange={(v) => updateParam('product', v)}
            />
            <FilterSelect
              label="CONTACTABILITY"
              value={contactabilityFilter}
              options={[...CONTACTABILITY_FILTER_OPTIONS]}
              onChange={(v) => updateParam(
                'marketing_eligibility',
                analyticsScopeActive && v === 'Any' ? null : v,
              )}
            />
            <FilterSelect
              label="CONSENT"
              value={consentFilter}
              options={[...CONSENT_FILTER_OPTIONS]}
              onChange={(v) => updateParam('consent_status', v)}
            />
            <FilterSelect
              label="RECENCY"
              value={recencyFilter}
              options={[...RECENCY_FILTER_OPTIONS]}
              onChange={(v) => updateParam('recency', v)}
            />
            <FilterSelect
              label="APPROVAL"
              value={approvalStatus === 'any' ? 'Any approval' : approvalStatus[0].toUpperCase() + approvalStatus.slice(1)}
              options={[...APPROVAL_FILTER_OPTIONS]}
              onChange={(v) => updateParam('approval_status', v === 'Any approval' ? null : v.toLowerCase())}
            />
            <FilterSelect
              label="OUTREACH"
              value={outreachStatus === 'any' ? 'Any outreach' : outreachStatus[0].toUpperCase() + outreachStatus.slice(1)}
              options={[...OUTREACH_FILTER_OPTIONS]}
              onChange={(v) => updateParam('outreach_status', v === 'Any outreach' ? null : v.toLowerCase())}
            />
            <FilterSelect
              label="ASSIGNED"
              value={assignedTo ?? 'All LOs'}
              options={['All LOs', ...salesTeam.map((member) => member.email)]}
              onChange={(v) => updateParam('assigned_to', v === 'All LOs' ? null : v)}
            />
            <FilterSelect
              label="AGING"
              value={agedDays ? `Aged >${agedDays}d` : 'Any age'}
              options={[...AGING_FILTER_OPTIONS]}
              onChange={(v) => {
                const match = v.match(/>(\d+)d/);
                updateParam('aged_days', match ? match[1] : null);
              }}
            />
          </div>
        </div>
      </div>
      <div className="surface mb-grid">
        <div className="surface__hdr surface__hdr--split">
          <div className="surface__hdr-main">
            <div className="h-4">Sales ops snapshot</div>
            <div className="muted fs-12">
              Shift capacity, stale approvals, yesterday's activity, and week-to-date LO conversion.
            </div>
          </div>
          <div className="chip-row">
            <Chip variant="neutral">{salesTeam.length} active LOs</Chip>
            <Chip variant="neutral">
              {salesTeam.reduce((sum, member) => sum + member.capacity_per_day, 0)} daily capacity
            </Chip>
            {salesTeamError && <Chip variant="warning">Team unavailable</Chip>}
          </div>
        </div>
        <div className="surface__body">
          {salesTeamError && (
            <div role="alert" className="status-callout status-callout--warning mb-3">
              Sales team unavailable: {salesTeamError}
            </div>
          )}
          {salesOpsError && (
            <div role="alert" className="status-callout status-callout--warning mb-3">
              Sales operations metrics unavailable: {salesOpsError}
            </div>
          )}
          <div className="sales-ops-grid">
            <div className="sales-ops-card">
              <div className="eyebrow">Stale approved</div>
              <div className="kpi__value">{staleLeads.length >= 100 ? '100+' : staleLeads.length.toLocaleString()}</div>
              <div className="muted fs-12">
                Approved over 7 days ago with no LO disposition{staleLeads.length >= 100 ? '; showing first 100.' : '.'}
              </div>
              <div className="chip-row mt-2">
                <Link className="btn btn--default btn--sm" to="/lead-queue?approval_status=approved&outreach_status=queued&aged_days=7">
                  Open stale queue
                </Link>
                {staleLeads.slice(0, 2).map((lead) => (
                  <Link key={lead.borrower_id} className="chip chip--neutral chip--compact" to={`/borrower-360/${lead.borrower_id}`}>
                    {lead.borrower_id} · {lead.age_days}d
                  </Link>
                ))}
              </div>
            </div>
            <div className="sales-ops-card">
              <div className="eyebrow">Yesterday standup</div>
              <div className="kpi__value">{(standup?.calls_logged ?? 0).toLocaleString()}</div>
              <div className="muted fs-12">
                {(standup?.contacts_reached ?? 0).toLocaleString()} reached · {(standup?.callbacks_scheduled ?? 0).toLocaleString()} callbacks · {(standup?.applications_started ?? 0).toLocaleString()} apps.
              </div>
            </div>
            <div className="sales-ops-card">
              <div className="eyebrow">Week-to-date conversion</div>
              <div className="sales-ops-list">
                {(conversion?.rows ?? []).slice(0, 3).map((row) => (
                  <div key={row.group_key} className="split-row">
                    <span className="mono fs-12">{row.group_key}</span>
                    <span className="mono num">{Math.round(row.application_start_rate * 100)}%</span>
                  </div>
                ))}
                {(conversion?.rows ?? []).length === 0 && (
                  <div className="muted fs-12">No LO dispositions logged this week.</div>
                )}
              </div>
            </div>
          </div>
        </div>
      </div>
      {warmingUp && (
        <WarmingUpBlock state={warmingUp} title="Ranked borrowers loading" compact />
      )}
      {loadError && !warmingUp && (
        <div
          role="alert"
          className="status-callout status-callout--danger"
        >
          <span>{loadError.message}</span>
          {loadError.invalidFilters ? (
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              onClick={() => setSearchParams(new URLSearchParams())}
              aria-label="Clear invalid lead queue filters"
            >
              Clear filters
            </button>
          ) : (
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              onClick={manualRetry}
              aria-label="Retry loading leads"
            >
              Retry
            </button>
          )}
        </div>
      )}
      {loading && !loadError && !warmingUp && (
        <LeadQueueTableSkeleton />
      )}
      {countyLoading && !loading && !loadError && !warmingUp && (
        <div className="muted body mb-grid">
          Resolving county ZIPs…
        </div>
      )}
      {!loading && !loadError && !warmingUp && !countyLoading && visibleLeads.length === 0 && (
        <div className="muted body mb-grid">
          {countyFilter && countyZips && countyZips.size === 0
            ? 'No ZIP-level rollup for this county in the current Cotality data coverage.'
            : 'No leads match this filter.'}
        </div>
      )}
      {!loading && (
        <LeadTable
          leads={visibleLeads}
          totalMatching={leadsData?.totalMatching ?? null}
          truncatedAt={leadsData?.truncatedAt ?? null}
          exportContext={exportContext}
          salesTeam={salesTeam}
        />
      )}
    </PageShell>
  );
}
