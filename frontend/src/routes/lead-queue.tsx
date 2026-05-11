import { useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { api } from '../lib/api';
import { useWarmingUpRetry } from '../lib/useWarmingUpRetry';
import type { LeadSummary, PortfolioPreview, SegmentCode } from '../types';
import { PageShell } from '../components/layout/PageShell';
import { LeadTable } from '../components/mortgage/LeadTable';
import { Chip } from '../components/Primitives';
import { WarmingUpBlock } from '../components/ui/WarmingUpBlock';

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
const PUBLIC_LENDER_REF_RE = /^(All|Summit Mortgage|Competitor ([A-Z]|Other))$/;
const PORTFOLIO_FILTER_KEYS = [
  'occupancy',
  'lien_status',
  'lender_relationship',
  'target_lender_ref',
  'product',
  'min_equity_pct_label',
  'owner_link',
  'purchase_intent',
] as const;
type PortfolioFilterKey = (typeof PORTFOLIO_FILTER_KEYS)[number];

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

function parseTargetLenderRef(raw: string | null): string | undefined {
  const value = raw?.trim();
  if (!value) return undefined;
  if (value === 'All') return undefined;
  return PUBLIC_LENDER_REF_RE.test(value) ? value : undefined;
}

const PORTFOLIO_FILTER_VALUE_SETS: Partial<Record<PortfolioFilterKey, Set<string>>> = {
  occupancy: new Set(['Owner-occupied', 'Non-owner-occupied', 'All']),
  lien_status: new Set(['Any', 'Open 1st lien', 'Open first lien', 'Open HELOC', 'Free & clear', 'Free and clear']),
  lender_relationship: new Set(['All', 'Current customer', 'Former customer', 'Competitor customer', 'Competitor']),
  product: new Set(['All products', 'Refi', 'HELOC', 'Cash-out', 'Purchase', 'Retention']),
  min_equity_pct_label: new Set(['Any', '≥ 15%', '≥ 25%', '≥ 40%']),
  owner_link: new Set(['All', 'Single-property owner', 'Multi-property (2-4)', 'Portfolio investor (5+)']),
  purchase_intent: new Set(['All', 'Listed for sale', 'Recent permit activity', 'Both']),
};

function sanitizePortfolioCriteria(raw: Record<string, string | undefined>): Record<string, string> | undefined {
  const criteria: Record<string, string> = {};
  for (const key of PORTFOLIO_FILTER_KEYS) {
    const value = raw[key]?.trim();
    if (!value) continue;
    if (key === 'target_lender_ref' && !PUBLIC_LENDER_REF_RE.test(value)) continue;
    const allowedValues = PORTFOLIO_FILTER_VALUE_SETS[key];
    if (allowedValues && !allowedValues.has(value)) continue;
    criteria[key] = value;
  }
  return Object.keys(criteria).length > 0 ? criteria : undefined;
}

function parsePortfolioCriteria(sp: URLSearchParams): Record<string, string> | undefined {
  return sanitizePortfolioCriteria(
    Object.fromEntries(PORTFOLIO_FILTER_KEYS.map((key) => [key, sp.get(key) ?? undefined])),
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
  portfolioCriteria?: Record<string, string>;
  cohortId?: string;
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
  if (input.targetLenderRef) params.set('target_lender_ref', input.targetLenderRef);
  const safePortfolioCriteria = sanitizePortfolioCriteria(input.portfolioCriteria ?? {});
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

export default function LeadQueue() {
  const [searchParams] = useSearchParams();
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
  const targetLenderRef = parseTargetLenderRef(searchParams.get('target_lender_ref'));
  const portfolioCriteria = useMemo(
    () => parsePortfolioCriteria(searchParams),
    [searchParams],
  );
  const portfolioFilters = useMemo(
    () => portfolioFilterEntries(portfolioCriteria),
    [portfolioCriteria],
  );
  const cohortId = (searchParams.get('cohort_id') ?? '').trim() || undefined;

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
  } = useWarmingUpRetry<LeadSummary[]>(
    (signal) => api.leads(
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
        portfolioCriteria,
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
    ],
  );
  const loading = leadsData === null && warmingUp === null && error === null;
  const loadError = error
    ? error instanceof Error
      ? `Couldn't load leads: ${error.message}`
      : "Couldn't load leads."
    : null;

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
        segmentCodes.length > 0 ? segmentCodes : null,
        segmentMode,
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
  }, [countyFilter, segmentCodes, segmentMode]);

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
    return leadsData ?? [];
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
        portfolioCriteria,
        cohortId,
      }),
      refreshedAt: exportRefreshedAt,
      rulesVersion,
    };
  }, [
    borrowerIdFilters,
    cohortId,
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
    zipFilter,
    zipFilters,
  ]);

  return (
    <PageShell
      eyebrow="Lead Queue"
      title="Ranked borrowers"
      lede="Click a row to expand the borrower preview. Approve or reject inline, or open Borrower 360 for the full dossier. Keyboard: A approves, R rejects the expanded row."
      heroRight={
        segment || segmentCodes.length > 0 || stateFilter || zipFilter || stateFilters.length > 0 || zipFilters.length > 0 || borrowerIdFilters.length > 0 || countyFilter || targetLenderRef || portfolioCriteria || cohortId ? (
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
            {portfolioFilters.map((filter) => (
              <Chip key={filter.key} variant="neutral">
                {filter.label} = {filter.value}
              </Chip>
            ))}
            {cohortId && <Chip variant="success">Genie cohort</Chip>}
          </>
        ) : undefined
      }
    >
      {warmingUp && (
        <WarmingUpBlock state={warmingUp} title="Ranked borrowers loading" compact />
      )}
      {loadError && !warmingUp && (
        <div
          role="alert"
          className="status-callout status-callout--danger"
        >
          <span>{loadError}</span>
          <button
            type="button"
            className="btn btn--ghost btn--sm"
            onClick={manualRetry}
            aria-label="Retry loading leads"
          >
            Retry
          </button>
        </div>
      )}
      {loading && !loadError && !warmingUp && (
        <div className="muted body mb-grid">
          Loading leads…
        </div>
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
      <LeadTable leads={visibleLeads} exportContext={exportContext} />
    </PageShell>
  );
}
