import { useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useSearchParams } from 'react-router';
import { api, type LeadsPageResult } from '../lib/api';
import { useConfigOptionsQuery } from '../lib/configOptionsQuery';
import { useWarmingUpRetry } from '../lib/useWarmingUpRetry';
import type { PortfolioPreview, SalesTeamMember } from '../types';
import { PageShell } from '../components/layout/PageShell';
import { LeadTable } from '../components/mortgage/LeadTable';
import { PropertyLookupPanel } from '../components/mortgage/PropertyLookupPanel';
import { Chip } from '../components/Primitives';
import { WarmingUpBlock } from '../components/ui/WarmingUpBlock';
import { FilterSelect } from '../components/ui/FilterSelect';
import { useFootprint } from '../components/FootprintProvider';
import { useApp } from '../components/AppContext';
import { queryKeys } from '../lib/queryKeys';
import { LENDER_RELATIONSHIP_OPTIONS } from '../lib/lenderFilters';
import { LeadQueueTableSkeleton } from './lead-queue.skeleton';
import {
  AGING_FILTER_OPTIONS,
  APPROVAL_FILTER_OPTIONS,
  CONTACTABILITY_FILTER_OPTIONS,
  CONSENT_FILTER_OPTIONS,
  FUNNEL_STAGE_LABELS,
  LOAN_PRODUCT_FILTER_OPTIONS,
  ORIGINATION_CHANNEL_FILTER_OPTIONS,
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
  searchParamsAfterSegmentRemoval,
  segmentDisplayLabel,
  segmentFilterChips,
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
  const { canAccessAdmin } = useApp();
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
  const loanProductFilter = portfolioCriteria?.loan_product ?? 'All loan products';
  const originationChannelFilter = portfolioCriteria?.origination_channel ?? 'All channels';
  const ownerLinkFilter = portfolioCriteria?.owner_link ?? 'All';
  const purchaseIntentFilter = portfolioCriteria?.purchase_intent ?? 'All';
  const contactabilityFilter = portfolioCriteria?.marketing_eligibility ?? 'Eligible only';
  const consentFilter = portfolioCriteria?.consent_status ?? 'Any';
  const recencyFilter = portfolioCriteria?.recency ?? 'Any';
  const approvalStatus = (searchParams.get('approval_status') ?? 'any').toLowerCase();
  const outreachStatus = (searchParams.get('outreach_status') ?? 'any').toLowerCase();
  const assignedTo = (searchParams.get('assigned_to') ?? '').trim() || undefined;
  const agedDays = Number(searchParams.get('aged_days') ?? '') || null;
  const growthAgentProofKey = [
    'growth_agent_run_id',
    'actionable_total',
    'actionable_cohort_fingerprint',
    'actionable_snapshot_id',
    'tool_result_hash',
    'growth_handoff',
  ].map((key) => searchParams.get(key) ?? '').join('|');
  // Sales team feeds the ASSIGNED filter and LeadTable's assign actions. The
  // Sales ops snapshot that also used it moved to the Analytics "Sales ops" tab.
  const salesTeamQuery = useQuery<SalesTeamMember[]>({
    queryKey: queryKeys.salesTeam(),
    queryFn: ({ signal }) => api.salesTeam(signal).then((team) => team.filter((member) => member.role === 'loan_officer')),
    staleTime: 60_000,
  });
  const salesTeam = salesTeamQuery.data ?? [];
  const salesTeamError = salesTeamQuery.error instanceof Error ? salesTeamQuery.error.message : null;
  const segmentFilter = segmentFilterDisplayValue(segment, segmentCodes, segmentMode);
  const segmentFilterOptions = optionsWithCurrentValue(SEGMENT_FILTER_OPTIONS, segmentFilter);
  // S8: one removable chip per active segment. Removing a chip rewrites the
  // segment URL params, which re-runs the composed predicate server-side —
  // the ranked rows and the X-Total-Matching count both recompute in UC.
  const segmentChips = segmentFilterChips(segment, segmentCodes);
  const removeSegmentChip = (code: (typeof segmentChips)[number]['code']) => {
    setSearchParams(searchParamsAfterSegmentRemoval(searchParams, code));
  };
  const stateFilterDisplay = stateFilter
    ?? (stateFilters.length === 1
      ? stateFilters[0]
      : stateFilters.length > 1 ? `${stateFilters.length} states selected` : 'All states');
  const stateFilterOptions = optionsWithCurrentValue(stateOptions, stateFilterDisplay);

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
    isPlaceholderData: leadsPlaceholderData,
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
      growthAgentProofKey,
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
        growthAgentProofKey,
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
        { countyFips: countyFilter },
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
    // The offer-rules version stamped on an export comes from an admin-scoped
    // endpoint. A loan officer's visit to this route used to fire it anyway
    // and eat a 403, which the UI swallowed but the browser console did not
    // (2026-08-07 audit H4). Ask only when the session says we may.
    if (!canAccessAdmin) {
      setRulesVersion(null);
      return () => ctrl.abort();
    }
    api
      .adminRules<AdminRulesSummary>(ctrl.signal)
      .then((payload) => setRulesVersion(payload.offer_rules_version ?? null))
      .catch(() => setRulesVersion(null));
    return () => ctrl.abort();
  }, [canAccessAdmin]);

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
            role="group"
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
              {/* 2026-08-07 audit C4: geo drill-ins show EVERY borrower the
                  map counted (no score floor — the map-tile promise), so the
                  old caption claiming a "scored, marketing-eligible subset"
                  described the inverse of the query. State both real numbers
                  from the same identity row instead. */}
              <span className="lead-queue-scope__note muted fs-11">
                {leadsData?.rankedMatching != null
                  && leadsData?.totalMatching != null
                  && leadsData.rankedMatching < leadsData.totalMatching
                  ? `Showing every borrower the map counted for this geography, ranked by opportunity score; ${leadsData.rankedMatching.toLocaleString('en-US')} of ${leadsData.totalMatching.toLocaleString('en-US')} also clear the national queue's score floor.`
                  : 'Showing every borrower the map counted for this geography, ranked by opportunity score.'}
              </span>
              </>
            ) : null}
          </div>
          {segmentChips.length > 0 && (
            <div className="chip-row mb-2" role="group" aria-label="Active segment filters">
              {segmentChips.map((chip) => (
                <Chip
                  key={chip.code}
                  variant="neutral"
                  onRemove={() => removeSegmentChip(chip.code)}
                  removeLabel={`Remove ${chip.label} segment filter`}
                >
                  {chip.label}
                </Chip>
              ))}
              {segmentChips.length > 1 && (
                <span className="muted fs-11">
                  {segmentMode === 'all'
                    ? 'Intersection — every borrower is in all selected segments.'
                    : 'Union — borrowers in any selected segment, de-duplicated.'}
                </span>
              )}
            </div>
          )}
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
              label="PRODUCT TYPE"
              value={loanProductFilter}
              options={[...LOAN_PRODUCT_FILTER_OPTIONS]}
              onChange={(v) => updateParam('loan_product', v === 'All loan products' ? null : v)}
            />
            <FilterSelect
              label="CHANNEL"
              value={originationChannelFilter}
              options={[...ORIGINATION_CHANNEL_FILTER_OPTIONS]}
              onChange={(v) => updateParam('origination_channel', v === 'All channels' ? null : v)}
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
      {/* Assign-degradation stays visible: the Sales ops snapshot moved to the
          Analytics "Sales ops" tab, but the ASSIGNED filter + LeadTable assign
          actions still depend on the sales team, so surface its outage here,
          directly above the ranked-borrowers region. */}
      {salesTeamError && (
        <div role="alert" className="status-callout status-callout--warning mb-grid">
          Sales team unavailable: {salesTeamError} — lead assignment to LOs is degraded until it reconnects.
        </div>
      )}
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
            : segmentChips.length > 1 && segmentMode === 'all'
              ? '0 borrowers sit in every selected segment — a real intersection result from the live query, not an error. Remove a segment chip to widen the cohort.'
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
            growthAgentVerification={leadsPlaceholderData
              ? null
              : leadsData?.growthAgentVerification ?? null}
            exportContext={exportContext}
            salesTeam={salesTeam}
          />
        </div>
      )}
      {/* Address → borrower lookup: a secondary fast path, demoted from the
          hero slot so the operational queue leads. The Console right-rail
          quick action stays the primary lookup entry. */}
      <div className="mb-grid">
        <PropertyLookupPanel />
      </div>
    </PageShell>
  );
}
