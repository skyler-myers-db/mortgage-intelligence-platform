import { useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link, useSearchParams } from 'react-router-dom';
import { api, type LeadsPageResult } from '../lib/api';
import { useConfigOptionsQuery } from '../lib/configOptionsQuery';
import { useWarmingUpRetry } from '../lib/useWarmingUpRetry';
import type { PortfolioPreview, SalesAgingLead, SalesConversionResponse, SalesOutcomeSummaryResponse, SalesStandupResponse, SalesTeamMember } from '../types';
import { PageShell } from '../components/layout/PageShell';
import { LeadTable } from '../components/mortgage/LeadTable';
import { Chip } from '../components/Primitives';
import { WarmingUpBlock } from '../components/ui/WarmingUpBlock';
import { FilterSelect } from '../components/ui/FilterSelect';
import { useFootprint } from '../components/FootprintProvider';
import { queryKeys } from '../lib/queryKeys';
import { LENDER_RELATIONSHIP_OPTIONS } from '../lib/lenderFilters';
import { LeadQueueTableSkeleton } from './lead-queue.skeleton';
import {
  AGING_FILTER_OPTIONS,
  APPROVAL_FILTER_OPTIONS,
  CONTACTABILITY_FILTER_OPTIONS,
  CONSENT_FILTER_OPTIONS,
  FUNNEL_STAGE_LABELS,
  OWNER_LINK_FILTER_OPTIONS,
  OUTREACH_FILTER_OPTIONS,
  PRODUCT_FILTER_OPTIONS,
  PURCHASE_INTENT_FILTER_OPTIONS,
  RECENCY_FILTER_OPTIONS,
  SEGMENT_FILTER_OPTIONS,
  SEGMENT_OPTION_TO_CODE,
  approvalFilterDisplayValue,
  buildLeadQueueExportFilters,
  formatLeadQueueLoadError,
  isNoOpPortfolioValue,
  outreachFilterDisplayValue,
  parseBorrowerIds,
  parseCsvParam,
  parseFunnelStage,
  parsePortfolioCriteria,
  parseSegmentCodes,
  parseTargetLenderRef,
  portfolioFilterEntries,
  segmentDisplayLabel,
  segmentFilterDisplayValue,
} from './lead-queue.filters';

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

function optionsWithCurrentValue(options: readonly string[], value: string): string[] {
  if (options.includes(value)) return [...options];
  if (options.length === 0) return [value];
  return [options[0], value, ...options.slice(1)];
}

interface AdminRulesSummary {
  offer_rules_version?: string | null;
}

export default function LeadQueue() {
  const [searchParams, setSearchParams] = useSearchParams();
  const filtersActive = searchParams.toString().length > 0;
  const footprint = useFootprint();
  const segment = parseSegmentCodes(searchParams.get('segment'))[0];
  const segmentCodes = useMemo(
    () => parseSegmentCodes(searchParams.get('segment_codes')),
    [searchParams],
  );
  const segmentMode = searchParams.get('segment_mode')?.trim().toLowerCase() === 'all' ? 'all' : 'any';
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
  const countyFilters = useMemo(
    () => parseCsvParam(searchParams.get('counties'), /^\d{5}$/, 50),
    [searchParams],
  );
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
  const ownerLinkFilter = portfolioCriteria?.owner_link ?? 'All';
  const purchaseIntentFilter = portfolioCriteria?.purchase_intent ?? 'All';
  const contactabilityFilter = portfolioCriteria?.marketing_eligibility ?? 'Eligible only';
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
    outcomes: SalesOutcomeSummaryResponse | null;
    outcomesError: string | null;
  }>({
    queryKey: queryKeys.salesOps(),
    queryFn: async ({ signal }) => {
      const [agingRows, standupRows, conversionRows] = await Promise.all([
        api.salesAging(7, 100, signal),
        api.salesStandup(yesterday, signal),
        api.salesConversion(weekStart, today, 'lo', signal),
      ]);
      const outcomeResult = await api.salesOutcomeSummary(weekStart, today, signal)
        .then((data) => ({ data, error: null as string | null }))
        .catch((error: unknown) => ({
          data: null,
          error: error instanceof Error ? error.message : 'Outcome summary unavailable',
        }));
      return {
        staleLeads: agingRows,
        standup: standupRows,
        conversion: conversionRows,
        outcomes: outcomeResult.data,
        outcomesError: outcomeResult.error,
      };
    },
    staleTime: 30_000,
  });
  const salesTeam = salesTeamQuery.data ?? [];
  const staleLeads = salesOpsQuery.data?.staleLeads ?? [];
  const standup = salesOpsQuery.data?.standup ?? null;
  const conversion = salesOpsQuery.data?.conversion ?? null;
  const outcomes = salesOpsQuery.data?.outcomes ?? null;
  const outcomesError = salesOpsQuery.data?.outcomesError ?? null;
  const connectedOutcomeSources = outcomes?.source_statuses
    .filter((source) => source.source_system !== 'manual_import' && source.status === 'connected')
    ?? [];
  const dryRunOutcomeSources = outcomes?.source_statuses
    .filter((source) => source.source_system !== 'manual_import' && source.status === 'dry_run')
    ?? [];
  const dryRunOutcomeCount = dryRunOutcomeSources.reduce(
    (sum, source) => sum + (Number(source.outcome_count) || 0),
    0,
  );
  const hasDryRunOutcomeRows = dryRunOutcomeCount > 0;
  const salesTeamError = salesTeamQuery.error instanceof Error ? salesTeamQuery.error.message : null;
  const salesOpsError = salesOpsQuery.error instanceof Error ? salesOpsQuery.error.message : null;
  const segmentFilter = segmentFilterDisplayValue(segment, segmentCodes, segmentMode);
  const segmentFilterOptions = optionsWithCurrentValue(SEGMENT_FILTER_OPTIONS, segmentFilter);
  const stateFilterDisplay = stateFilter
    ?? (stateFilters.length === 1
      ? stateFilters[0]
      : stateFilters.length > 1 ? `${stateFilters.length} states selected` : 'All states');
  const stateFilterOptions = optionsWithCurrentValue(stateOptions, stateFilterDisplay);
  const outcomeDistribution = outcomes
    ? [
      { label: 'submitted', value: outcomes.applications_submitted },
      { label: 'funded', value: outcomes.closed_funded },
      { label: 'lost elsewhere', value: outcomes.lost_to_competitor },
      { label: 'withdrawn', value: outcomes.withdrawn },
      { label: 'not qualified', value: outcomes.not_qualified },
    ]
    : [];

  const updateParam = (key: string, value: string | null) => {
    const next = new URLSearchParams(searchParams);
    if (key === 'state') {
      ['states', 'zips', 'zip', 'county', 'counties', 'borrower_ids', 'cohort_id'].forEach((k) => next.delete(k));
    }
    if (value === null || isNoOpPortfolioValue(key, value)) next.delete(key);
    else next.set(key, value);
    if (key === 'segment') {
      next.delete('segment_codes');
      next.delete('segment_mode');
    }
    setSearchParams(next);
  };

  const updateWorkflowParam = (key: 'approval_status' | 'outreach_status', value: string | null) => {
    const next = new URLSearchParams(searchParams);
    if (key === 'approval_status' && funnelStage === 'approved') next.delete('funnel_stage');
    if (key === 'outreach_status' && funnelStage === 'actioned') next.delete('funnel_stage');
    if (value === null || value === '') next.delete(key);
    else next.set(key, value);
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
    isFetching: leadsFetching,
  } = useWarmingUpRetry<LeadsPageResult>(
    (signal) => api.leadsPage(
      segment,
      signal,
      {
        state: stateFilter,
        zip: zipFilter,
        county: countyFilter,
        counties: countyFilters,
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
      countyFilters.join(','),
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
        countyFilters.join(','),
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
      keepPreviousData: true,
    },
  );
  const loading = leadsData === null && warmingUp === null && error === null;
  const queueRefetchWarming = leadsData !== null && warmingUp !== null;
  const queueUpdating = leadsData !== null && (leadsFetching || queueRefetchWarming);
  const queueStatusLabel = queueRefetchWarming
    ? `${warmingUp.label} (${warmingUp.attempt}/${warmingUp.maxAttempts})`
    : 'updating';
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
  const exportContext = {
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
      countyFilters,
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
  const heroFiltersActive = Boolean(
    segment
      || segmentCodes.length > 0
      || stateFilter
      || zipFilter
      || stateFilters.length > 0
      || zipFilters.length > 0
      || borrowerIdFilters.length > 0
      || countyFilter
      || countyFilters.length > 0
      || targetLenderRef
      || portfolioCriteria
      || cohortId
      || funnelStage
      || approvalStatus !== 'any'
      || outreachStatus !== 'any'
      || assignedTo
      || agedDays,
  );
  const heroFilterChips: Array<{ key: string; variant: 'neutral' | 'success'; label: string }> = [];
  if (segment) heroFilterChips.push({ key: 'segment', variant: 'neutral', label: `segment = ${segmentDisplayLabel(segment)}` });
  if (segmentCodes.length > 0) {
    heroFilterChips.push({
      key: 'segments',
      variant: 'neutral',
      label: `segments = ${segmentCodes.map(segmentDisplayLabel).join(', ')} (${segmentMode === 'all' ? 'all selected' : 'any selected'})`,
    });
  }
  if (stateFilter) heroFilterChips.push({ key: 'state', variant: 'neutral', label: `state = ${stateFilter}` });
  if (zipFilter) heroFilterChips.push({ key: 'zip', variant: 'neutral', label: `zip = ${zipFilter}` });
  if (stateFilters.length > 0) heroFilterChips.push({ key: 'states', variant: 'neutral', label: `states = ${stateFilters.join(', ')}` });
  if (zipFilters.length > 0) heroFilterChips.push({ key: 'zips', variant: 'neutral', label: `zips = ${zipFilters.length} selected` });
  if (borrowerIdFilters.length > 0) heroFilterChips.push({ key: 'borrowers', variant: 'neutral', label: `borrowers = ${borrowerIdFilters.length} selected` });
  if (countyFilter) heroFilterChips.push({ key: 'county', variant: 'neutral', label: `county = ${countyFilter}` });
  if (countyFilters.length > 0) heroFilterChips.push({ key: 'counties', variant: 'neutral', label: `counties = ${countyFilters.length} selected` });
  if (targetLenderRef) heroFilterChips.push({ key: 'lender', variant: 'neutral', label: `lender = ${targetLenderRef}` });
  if (funnelStage) heroFilterChips.push({ key: 'stage', variant: 'success', label: `stage = ${FUNNEL_STAGE_LABELS[funnelStage]}` });
  for (const filter of portfolioFilters) {
    heroFilterChips.push({ key: `portfolio-${filter.key}`, variant: 'neutral', label: `${filter.label} = ${filter.value}` });
  }
  if (approvalStatus !== 'any') heroFilterChips.push({ key: 'approval', variant: 'neutral', label: `approval = ${approvalStatus}` });
  if (outreachStatus !== 'any') heroFilterChips.push({ key: 'outreach', variant: 'neutral', label: `outreach = ${outreachStatus}` });
  if (assignedTo) heroFilterChips.push({ key: 'assigned', variant: 'neutral', label: `assigned = ${assignedTo}` });
  if (agedDays) heroFilterChips.push({ key: 'aged', variant: 'neutral', label: `aged > ${agedDays}d` });
  if (cohortId) heroFilterChips.push({ key: 'cohort', variant: 'success', label: 'Genie cohort' });
  const visibleHeroFilterChips = heroFilterChips.slice(0, 3);
  const hiddenHeroFilterCount = Math.max(heroFilterChips.length - visibleHeroFilterChips.length, 0);
  const scopeFiltersActive = Boolean(
    funnelStage
      || zipFilter
      || countyFilter
      || countyFilters.length > 0
      || stateFilter
      || stateFilters.length > 0
      || zipFilters.length > 0
      || borrowerIdFilters.length > 0,
  );

  return (
    <PageShell
      eyebrow="Lead Queue"
      title="Ranked borrowers"
      lede="Click a row to expand the borrower preview. Approve, reject, assign to LOs, log call outcomes, or open Borrower 360 for the full dossier. Keyboard: while the expanded row is still pending, A approves and R rejects."
      heroRight={
        <div
          className={`page-filter-chips ${heroFiltersActive ? '' : 'is-empty'}`}
          aria-hidden={!heroFiltersActive || undefined}
        >
          {heroFiltersActive ? (
          <>
            {visibleHeroFilterChips.map((chip) => (
              <Chip key={chip.key} variant={chip.variant}>
                {chip.label}
              </Chip>
            ))}
            {hiddenHeroFilterCount > 0 && <Chip variant="neutral">+{hiddenHeroFilterCount} filters</Chip>}
          </>
          ) : null}
        </div>
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
            disabled={!filtersActive}
            aria-disabled={!filtersActive}
            onClick={() => setSearchParams(new URLSearchParams())}
          >
            Clear filters
          </button>
        </div>
        <div className="surface__body">
          <div
            className={`lead-queue-scope ${scopeFiltersActive ? '' : 'is-empty'}`}
            aria-label="Active analytics drilldown filters"
            aria-hidden={!scopeFiltersActive || undefined}
          >
            {scopeFiltersActive ? (
              <>
              {funnelStage && (
                <span className="lead-queue-scope__pill">Stage: {FUNNEL_STAGE_LABELS[funnelStage]}</span>
              )}
              {stateFilter && <span className="lead-queue-scope__pill">State: {stateFilter}</span>}
              {zipFilter && <span className="lead-queue-scope__pill">ZIP: {zipFilter}</span>}
              {countyFilter && <span className="lead-queue-scope__pill">County FIPS: {countyFilter}</span>}
              {countyFilters.length > 0 && <span className="lead-queue-scope__pill">Counties: {countyFilters.join(', ')}</span>}
              {stateFilters.length > 0 && <span className="lead-queue-scope__pill">States: {stateFilters.join(', ')}</span>}
              {zipFilters.length > 0 && <span className="lead-queue-scope__pill">ZIPs: {zipFilters.join(', ')}</span>}
              {borrowerIdFilters.length > 0 && <span className="lead-queue-scope__pill">Borrowers: {borrowerIdFilters.length}</span>}
              {/* Re-audit #3 P3 (2026-06-12): the map tile counts MARKETABLE
                  borrowers; this queue ranks the scored, contactable subset.
                  Without the caption the handoff reads as a numbers jump
                  (e.g. 30,833 on the ZIP tile → 1,379 ranked rows). */}
              <span className="lead-queue-scope__note muted fs-11">
                Ranked leads are the scored, marketing-eligible subset of this
                geography&apos;s marketable borrowers — intentionally smaller than
                the map tile&apos;s population count.
              </span>
              </>
            ) : null}
          </div>
          <div className="filter-row filter-row--lead-queue">
            <FilterSelect
              label="STATE"
              value={stateFilterDisplay}
              options={stateFilterOptions}
              onChange={(v) => {
                if (v === stateFilterDisplay && stateFilters.length > 0) return;
                updateParam('state', v === 'All states' ? null : v);
              }}
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
              label="OWNER LINK"
              value={ownerLinkFilter}
              options={[...OWNER_LINK_FILTER_OPTIONS]}
              onChange={(v) => updateParam('owner_link', v)}
            />
            <FilterSelect
              label="PURCHASE INTENT"
              value={purchaseIntentFilter}
              options={[...PURCHASE_INTENT_FILTER_OPTIONS]}
              onChange={(v) => updateParam('purchase_intent', v)}
            />
            <FilterSelect
              label="SEGMENT"
              value={segmentFilter}
              options={segmentFilterOptions}
              onChange={(v) => {
                if (v === segmentFilter && segmentCodes.length > 0) return;
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
                v === 'Eligible only' ? null : v,
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
              value={approvalFilterDisplayValue(approvalStatus, funnelStage)}
              options={[...APPROVAL_FILTER_OPTIONS]}
              onChange={(v) => updateWorkflowParam('approval_status', v === 'Any approval' ? null : v.toLowerCase())}
            />
            <FilterSelect
              label="OUTREACH"
              value={outreachFilterDisplayValue(outreachStatus, funnelStage)}
              options={[...OUTREACH_FILTER_OPTIONS]}
              onChange={(v) => updateWorkflowParam('outreach_status', v === 'Any outreach' ? null : v.toLowerCase())}
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
              Shift capacity, stale approvals, yesterday's activity, and imported customer-system outcomes.
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
            <div className="sales-ops-card">
              <div className="eyebrow">
                {hasDryRunOutcomeRows ? 'Outcome ledger · includes dry run' : 'Closed-loop outcomes'}
              </div>
              <div className="kpi__value">{outcomesError ? '--' : (outcomes?.closed_funded ?? 0).toLocaleString()}</div>
              <div className="muted fs-12">
                {outcomesError
                  ? 'Customer-system outcome counts are unavailable.'
                  : `${(outcomes?.applications_submitted ?? 0).toLocaleString()} submitted · ${(outcomes?.lost_to_competitor ?? 0).toLocaleString()} lost elsewhere · ${(outcomes?.withdrawn ?? 0).toLocaleString()} withdrawn · ${(outcomes?.not_qualified ?? 0).toLocaleString()} not qualified this week.`}
              </div>
              <div className="muted fs-12 mt-1">
                Imported, read-only outcome ledger; this card does not write back to customer systems.
              </div>
              {hasDryRunOutcomeRows ? (
                <div className="muted fs-12 mt-1">
                  Dry-run connector rows are included for reconciliation only; connected feeds are the live customer-system counts.
                </div>
              ) : null}
              {outcomesError ? (
                <div className="muted fs-12 mt-2">
                  Outcome summary unavailable: {outcomesError}
                </div>
              ) : null}
              {!outcomesError && (outcomes?.total_outcomes ?? 0) > 0 ? (
                <div className="sales-ops-list mt-2">
                  {outcomeDistribution.map((row) => (
                    <div key={row.label} className="split-row">
                      <span className="mono fs-12">{row.label}</span>
                      <span className="mono num">{row.value.toLocaleString()}</span>
                    </div>
                  ))}
                  {(outcomes?.top_competitors ?? []).slice(0, 2).map((row) => (
                    <div key={row.competitor_lender_label} className="split-row">
                      <span className="mono fs-12">{row.competitor_lender_label}</span>
                      <span className="mono num">{row.lost_to_competitor}</span>
                    </div>
                  ))}
                </div>
              ) : !outcomesError ? (
                <div className="muted fs-12 mt-2">
                  {connectedOutcomeSources.length > 0
                    ? 'Connected outcome feeds are live; no funded or lost loans have been reported this week.'
                    : dryRunOutcomeSources.length > 0
                      ? 'Outcome feeds are configured in dry run; no customer-system outcomes are being counted as live yet.'
                    : 'Customer CRM/LOS/POS outcome feeds are not configured yet; manual imports remain available for governed backfill.'}
                </div>
              ) : null}
            </div>
          </div>
        </div>
      </div>
      {warmingUp && leadsData === null && (
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
        <div
          className={`stable-refresh-region stable-refresh-region--table ${queueUpdating ? 'is-updating' : ''}`}
          aria-busy={queueUpdating}
          data-status={queueStatusLabel}
        >
          <LeadTable
            leads={visibleLeads}
            totalMatching={leadsData?.totalMatching ?? null}
            truncatedAt={leadsData?.truncatedAt ?? null}
            exportContext={exportContext}
            salesTeam={salesTeam}
          />
        </div>
      )}
    </PageShell>
  );
}
