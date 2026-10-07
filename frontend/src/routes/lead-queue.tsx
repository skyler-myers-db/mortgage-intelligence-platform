import { useMemo, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useLocation, useSearchParams } from 'react-router';
import { api } from '../lib/api';
import { useConfigOptionsQuery } from '../lib/configOptionsQuery';
import { isAbortError } from '../lib/apiTransport';
import { PageShell } from '../components/layout/PageShell';
import { LeadTable, type LeadExportContext } from '../components/mortgage/LeadTable';
import { Chip } from '../components/Primitives';
import { AsyncState } from '../components/ui/AsyncState';
import { DescribedErrorBody } from '../components/ui/DescribedError';
import { lazyModule, useLazyModule } from '../components/mortgage/useLazyModule';
import { FilterSelect } from '../components/ui/FilterSelect';
import { useFootprint } from '../components/FootprintProvider';
import { useApp } from '../components/AppContext';
import { queryKeys } from '../lib/queryKeys';
import { loanOfficersOnly, useSalesRoster } from '../lib/salesRoster';
import { queueFilterLabel, usePublishQueueContext } from '../lib/queueContextPublish';
import { LENDER_RELATIONSHIP_OPTIONS } from '../lib/lenderFilters';
import { LeadQueueTableSkeleton } from './lead-queue.skeleton';
import { serverOrderOf, useLeadQueuePages } from './lead-queue.pages';
import { leadQueueFilterInputFromSearchParams, leadQueueLenderRefs, leadsRequestFromSearchParams } from './lead-queue.request';
import { useLeadQueueFreshness } from './lead-queue.freshness';
import { LeadQueueFilterBar, LeadQueueHeroFilterChips } from './lead-queue.filterBar';
import { LeadQueueViews } from './lead-queue.views';
import { buildPortfolioBuilderUrlFromQueue } from './lead-queue.handoff';
import { copyLink } from '../lib/copyLink';
import {
  hasLeadQueueFilters,
  leadQueueActiveFilterChips,
  leadQueueFilterParams,
  moreFiltersActiveCount,
  searchParamsCleared,
  searchParamsWithoutFilter,
} from './lead-queue.activeFilters';
import {
  AGING_FILTER_OPTIONS,
  APPROVAL_FILTER_OPTIONS,
  CONTACTABILITY_FILTER_OPTIONS,
  CONSENT_FILTER_OPTIONS,
  GROWTH_AGENT_PROOF_PARAMS,
  LEAD_TABLE_VIEW_PARAM,
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
  isAssignedToMe,
  isNoOpPortfolioValue,
  leadQueueShareParams,
  outreachFilterDisplayValue,
  parseLeadTablePlace,
  parseLeadTableView,
  parseTriageMode,
  searchParamsAfterSegmentRemoval,
  searchParamsWithLeadTablePlace,
  searchParamsWithLeadTableView,
  searchParamsWithTriageMode,
  searchWithoutTriageMode,
  segmentFilterChips,
  type LeadQueueExportFiltersInput,
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

/** The ZIPs a county rollup covers (the Fresh shape). Module-level, so the derived Set keeps its identity. */
function selectCountyZips(payload: Awaited<ReturnType<typeof api.zipRollupsWithFreshness>>): ReadonlySet<string> {
  return new Set(payload.data.rollups.map((rollup) => rollup.zip));
}

const RULES_VERSION_STALE_MS = 5 * 60_000;
const EMPTY_STATE = lazyModule(() => import('../components/mortgage/LeadQueueEmptyState'));
const PROPERTY_LOOKUP = lazyModule(() => import('../components/mortgage/PropertyLookupPanel'));
const EXPORT_WAITS_FOR_ROWS = 'Export waits for the rows of the current filters';

export default function LeadQueue() {
  const [searchParams, setSearchParams] = useSearchParams();
  const { search: queueSearch, pathname } = useLocation();
  // `?view=` (column preset) and the table place (`?sort=`, `?dir=`, `?row=`)
  // are display state, not filters: they never enable Clear all.
  const filtersActive = hasLeadQueueFilters(searchParams);
  const footprint = useFootprint();
  const { canAccessAdmin, actorEmail, sessionStatus } = useApp();
  const queryClient = useQueryClient();
  const moreFiltersToggleRef = useRef<HTMLButtonElement | null>(null);
  const configOptionsQuery = useConfigOptionsQuery();
  const targetLenderOptions = useMemo(
    () => leadQueueLenderRefs(configOptionsQuery.data?.target_lender_refs),
    [configOptionsQuery.data?.target_lender_refs],
  );
  // ONE derivation of the URL (lead-queue.request.ts, W5c): the request the
  // queue sends, the export and Copy-link input, the facet counts and the
  // saved views read the same grammar (parity pinned in lead-queue.filterBar.test).
  const input = useMemo(
    () => leadQueueFilterInputFromSearchParams(searchParams, targetLenderOptions),
    [searchParams, targetLenderOptions],
  );
  const {
    segment, segmentCodes, segmentMode, stateFilter, zipFilter, stateFilters, zipFilters, cityFilters,
    borrowerIdFilters, countyFilter, countyFilters, targetLenderRef, portfolioCriteria, cohortId, funnelStage, agedDays,
  } = input;
  const campaignHandoff = useMemo(
    () => buildPortfolioBuilderUrlFromQueue(searchParams, targetLenderOptions),
    [searchParams, targetLenderOptions],
  );
  const tableView = parseLeadTableView(searchParams.get(LEAD_TABLE_VIEW_PARAM));
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
  const approvalStatus = input.approvalStatus ?? 'any';
  const outreachStatus = input.outreachStatus ?? 'any';
  // `?assigned_to=me` (the "Assigned to me" preset) is resolved to the
  // signed-in actor's email here, at request time: the request and the export
  // get the email, the URL never holds one a preset wrote. While the session
  // is loading the leads query waits; a session with no email reads nothing
  // and says so.
  const assignedParam = input.assignedTo;
  const assignedToMe = isAssignedToMe(assignedParam);
  const assignedTo = assignedToMe ? actorEmail ?? undefined : assignedParam;
  const meUnresolved = assignedToMe && !actorEmail;
  const meWithoutEmail = meUnresolved && sessionStatus !== 'loading';
  const growthAgentProofKey = GROWTH_AGENT_PROOF_PARAMS.map((key) => searchParams.get(key) ?? '').join('|');
  // Sales team feeds the ASSIGNED filter and LeadTable's assign actions. The
  // Sales ops snapshot that also used it moved to the Analytics "Sales ops" tab.
  const salesTeamQuery = useSalesRoster(loanOfficersOnly);
  const salesTeam = salesTeamQuery.data ?? [];
  // require_visible_assignee answers 422 for anyone but a listed loan officer.
  const actorIsListedLo = Boolean(actorEmail) && salesTeam.some(
    (member) => member.email.toLowerCase() === actorEmail?.toLowerCase(),
  );
  const salesTeamError = salesTeamQuery.error && !isAbortError(salesTeamQuery.error) ? salesTeamQuery.error : null;
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

  // State, ZIP and every other filter go to /api/leads (geo cohorts read
  // borrower_360, so the queue keeps the map's counted population). ONE
  // request object feeds the fetch and the key (lib/leadsQuery, audit
  // runtime-02). The paged view (lead-queue.pages.ts, D-audit-reads-a): page
  // 0 per view, one page per Load next, no passive re-read. The server sorts
  // by the URL's ?sort= when it is a warehouse column, else by rank. The
  // Growth Agent proof (read from window.location) rides in the key; an
  // unresolved "Assigned to me" adds a sentinel, reads nothing and never
  // carries a placeholder (w3-queue-place review).
  const leadsRequest = useMemo(
    () => leadsRequestFromSearchParams(searchParams, targetLenderOptions, actorEmail),
    [searchParams, targetLenderOptions, actorEmail],
  );
  const place = parseLeadTablePlace(searchParams);
  const order = serverOrderOf(place.sort);
  const pages = useLeadQueuePages({
    request: leadsRequest,
    order,
    implicitInputs: meUnresolved ? [growthAgentProofKey, 'assigned_to=me:unresolved'] : [growthAgentProofKey],
    enabled: !meUnresolved,
    keepPrevious: !meUnresolved,
  });
  const leadsQueryState = meUnresolved ? { ...pages, data: null } : pages;
  const {
    data: leadsData,
    warmingUp,
    error,
    isFetching: leadsFetching,
    isPlaceholderData: leadsPlaceholderData,
  } = leadsQueryState;
  // Audit states-v1 / states-v2 (2026-09-21): LeadTable mounts ONLY for a
  // payload with rows. Warming, a load error and a measured zero each have
  // their own surface (AsyncState below), so no "Showing 0 ranked
  // borrowers" chrome ever reads as data. TanStack keeps `failureReason`
  // after the final retry, so `warmingUp` outlives the retry loop; `error` is
  // set only once it has given up.
  const warming = error === null ? warmingUp : null;
  const hasQueue = leadsData !== null;
  const queueRefetchWarming = hasQueue && warming !== null;
  const queueUpdating = hasQueue && (leadsFetching || queueRefetchWarming);
  const queueStatusLabel = queueRefetchWarming
    ? `${warming.label} (${warming.attempt}/${warming.maxAttempts})`
    : 'updating';
  // The measured zero's EmptyState (and its Ask Genie template) loads only
  // when a zero is on screen: none of it rides the natural load.
  const measuredZero = hasQueue && !leadsPlaceholderData && error === null && warming === null
    && leadsData.leads.length === 0;
  const emptyModule = useLazyModule(EMPTY_STATE, measuredZero).module;
  // The below-the-fold address lookup loads right after the queue, off its
  // natural-load closure (the Console's copy is the primary entry).
  const lookupModule = useLazyModule(PROPERTY_LOOKUP, true).module;
  // "Fetched 3m ago · Refresh" / "Queue updated · Refresh" (audit states-09):
  // an audit-free version poll; Refresh is the one explicit re-read.
  const freshness = useLeadQueueFreshness({
    enabled: hasQueue && !leadsPlaceholderData && !meUnresolved,
    dataUpdatedAt: leadsQueryState.dataUpdatedAt,
    isFetching: leadsFetching,
    onRefresh: pages.manualRetry,
  });

  // Resolve `?county=FFFFF` → set of ZIPs via /api/geo/zip-rollups for an
  // honest scope chip only. The actual county predicate is server-side in
  // /api/leads, so a failed rollup must not broaden or empty the ranked
  // list, and must not claim the county has no coverage either (runtime-06):
  // it is a non-audited aggregate read on the query layer.
  const countyCohort = segmentCodes.length > 0 ? segmentCodes : segment ? [segment] : null;
  const countyZipsQuery = useQuery({
    queryKey: queryKeys.geoCountyZipRollups(countyFilter ?? '', [countyCohort, segmentMode, portfolioCriteria]),
    queryFn: ({ signal }) => api.zipRollupsWithFreshness(
      { countyFips: countyFilter ?? '' },
      signal,
      countyCohort,
      segmentMode,
      portfolioCriteria,
    ),
    enabled: Boolean(countyFilter),
    select: selectCountyZips,
  });
  // Only a rollup that ANSWERED may claim the county has no ZIP coverage.
  const countyZips = countyFilter && countyZipsQuery.isSuccess ? countyZipsQuery.data : null;

  const visibleLeads = useMemo(() => {
    return leadsData?.leads ?? [];
  }, [leadsData]);

  // The reader's place (audit shell-03 / runtime-08): sort and expanded row
  // live in the URL, never in the leads request. `?row=` counts only when it
  // names a row of the SETTLED list (placeholder rows belong to the previous
  // filters); otherwise it is ignored here and the next place write drops it.
  // Restoring it re-opens the in-memory preview only: no borrower, proof or
  // draft request.
  const settledRowIds = leadsData && !leadsPlaceholderData
    ? new Set(visibleLeads.map((lead) => lead.borrower_id))
    : null;
  const committedRow = place.row !== null && settledRowIds?.has(place.row) ? place.row : null;
  // An expand or collapse the table asked for, until its URL write commits.
  // The data router applies setSearchParams in a transition, so for a moment
  // `?row=` still names the previous row; the table's keyboard flow reading
  // that (A pressed in the same task as the Enter that expanded the row)
  // opened the review as a dialog instead of in the row. The request counts
  // only while the search params are the very object it was made against:
  // once any URL write commits (this one, Back, a filter) it is spent, so a
  // later return to the same URL can never resurrect it.
  const [requestedRow, setRequestedRow] = useState<{ row: string | null; against: URLSearchParams } | null>(null);
  const expandedRow = requestedRow !== null && requestedRow.against === searchParams ? requestedRow.row : committedRow;

  const countyLoading = Boolean(countyFilter) && countyZipsQuery.isPending;
  // Export provenance (audit delivery-08): the rows' own refresh time from
  // X-Data-Refreshed-At, and the rules version read on the Export click only,
  // for an actor who may read admin rules (GET /api/admin/rules writes no
  // audit row). Nothing export-only is fetched on mount.
  const filterInput: LeadQueueExportFiltersInput = { ...input, assignedTo };
  const exportContext: LeadExportContext = {
    filters: buildLeadQueueExportFilters(filterInput),
    refreshedAt: leadsData?.dataRefreshedAt ?? null,
    // The export's 4 s bound signal is deliberately not threaded into this
    // read: GET /api/admin/rules writes no audit row, and a read that
    // outlives the bound still fills the cache for the next export.
    resolveRulesVersion: canAccessAdmin
      ? () => queryClient
        .fetchQuery({
          queryKey: queryKeys.adminRules(),
          queryFn: ({ signal }) => api.adminRules<AdminRulesSummary>(signal),
          staleTime: RULES_VERSION_STALE_MS,
        })
        .then((rules) => rules.offer_rules_version ?? null)
      : undefined,
    // Placeholder rows belong to the previous filters (keepPreviousData).
    exportBlockedReason: leadsPlaceholderData ? EXPORT_WAITS_FOR_ROWS : null,
  };
  // Audit tables-06: the hero lists the active NON-core filters as removable
  // chips (their pills sit collapsed behind "More filters"); core filters
  // stay visible as highlighted pills in the filter row.
  const activeFilterChips = leadQueueActiveFilterChips({
    targetLenderRef,
    portfolioCriteria,
    outreachStatus,
    // The URL value: `me` reads "Me", never the resolved email.
    assignedTo: assignedParam,
    agedDays,
    zipFilter,
    zipFilters,
    cityFilters,
    countyFilter,
    countyFilters,
    borrowerIdFilters,
    cohortId,
    funnelStage,
  });
  const moreActiveCount = moreFiltersActiveCount(activeFilterChips);
  // Audit shell-04: publish this exact queue (URL search, filter summary,
  // ranked masked ids) for the dossier breadcrumbs and pager. Settled rows
  // only: placeholder rows still belong to the previous filters.
  usePublishQueueContext(leadsData && !leadsPlaceholderData ? {
    search: searchWithoutTriageMode(queueSearch), // dossier crumbs return to the table
    label: queueFilterLabel([
      stateFilterDisplay !== 'All states' && stateFilterDisplay,
      segmentFilter !== 'All segments' && segmentFilter,
      activeFilterChips.length > 0 && `+${activeFilterChips.length} ${activeFilterChips.length === 1 ? 'filter' : 'filters'}`,
    ]),
    ids: visibleLeads.map((lead) => lead.borrower_id),
  } : null);
  const clearAllFilters = () => setSearchParams(searchParamsCleared(searchParams));
  // Copy link (audit tables-09): this origin and path plus the shareable
  // params only. Never the address bar: that holds the open row, maybe an
  // assignee email and a Growth Agent proof bound to this session.
  const copyQueueLink = () => {
    const share = leadQueueShareParams(searchParams, { ...filterInput, assignedTo: assignedParam });
    void copyLink(`${window.location.origin}${pathname}${share.search}`, {
      success: 'Queue link copied',
      successDetail: share.omitted.length > 0
        ? `Left out: ${share.omitted.join(', ')}.`
        : 'Anyone with access opens this queue view.',
      failure: 'Copy failed',
      // Never "copy the address bar": it holds exactly what this link leaves out.
      failureDetail: 'The browser blocked clipboard access. Try again rather than sharing the address bar: '
        + 'it can hold the open row and private filters.',
    });
  };
  const scopeFiltersActive = Boolean(
    funnelStage
      || zipFilter
      || countyFilter
      || countyFilters.length > 0
      || stateFilter
      || stateFilters.length > 0
      || zipFilters.length > 0
      || cityFilters.length > 0
      || borrowerIdFilters.length > 0,
  );

  return (
    <PageShell
      eyebrow="Lead Queue"
      title="Ranked borrowers"
      lede="Expand a row, then approve or reject. Decisions are audited; nothing is sent automatically."
      heroRight={
        // The presets and Copy link sit in the hero's action slot, stacked
        // over the active-filter chips: a row of their own above the filter
        // bar pushed the 480px table scroller below the fold at 1440x900.
        <div className="lead-queue-hero">
          <LeadQueueViews
            searchParams={searchParams}
            showAssignedToMe={actorIsListedLo}
            onCopyLink={copyQueueLink}
          />
          <LeadQueueHeroFilterChips
            chips={activeFilterChips}
            onRemove={(chip) => setSearchParams(searchParamsWithoutFilter(searchParams, chip.params))}
            focusFallbackRef={moreFiltersToggleRef}
          />
        </div>
      }
    >
      <div className="surface mb-grid">
        <div className="surface__body">
          <div
            className={`lead-queue-scope ${scopeFiltersActive ? '' : 'is-empty'}`}
            role="group"
            aria-label="Active analytics drilldown filters"
            aria-hidden={!scopeFiltersActive || undefined}
          >
            {scopeFiltersActive ? (
              <>
              {/* A value the hero already shows as a removable chip (stage,
                  ZIP, county, cities, borrowers) is not repeated here; the
                  strip keeps the states and the full ZIP / county lists the
                  hero chips only count. */}
              {stateFilter && <span className="lead-queue-scope__pill">State: {stateFilter}</span>}
              {countyFilters.length > 0 && <span className="lead-queue-scope__pill">Counties: {countyFilters.join(', ')}</span>}
              {stateFilters.length > 0 && <span className="lead-queue-scope__pill">States: {stateFilters.join(', ')}</span>}
              {zipFilters.length > 0 && <span className="lead-queue-scope__pill">ZIPs: {zipFilters.join(', ')}</span>}
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
          <LeadQueueFilterBar
            filtersActive={filtersActive}
            onClearAll={clearAllFilters}
            moreToggleRef={moreFiltersToggleRef}
            moreActiveCount={moreActiveCount}
            core={(
              <>
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
                  label="RELATIONSHIP"
                  value={relationshipFilter}
                  options={[...LENDER_RELATIONSHIP_OPTIONS]}
                  onChange={(v) => updateParam('lender_relationship', v)}
                />
                <FilterSelect
                  label="PRODUCT"
                  value={productFilter}
                  options={[...PRODUCT_FILTER_OPTIONS]}
                  onChange={(v) => updateParam('product', v)}
                />
                <FilterSelect
                  label="APPROVAL"
                  value={approvalFilterDisplayValue(approvalStatus, funnelStage)}
                  options={[...APPROVAL_FILTER_OPTIONS]}
                  onChange={(v) => updateWorkflowParam('approval_status', v === 'Any approval' ? null : v.toLowerCase())}
                />
              </>
            )}
            more={(
              <>
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
                  label="OUTREACH"
                  value={outreachFilterDisplayValue(outreachStatus, funnelStage)}
                  options={[...OUTREACH_FILTER_OPTIONS]}
                  onChange={(v) => updateWorkflowParam('outreach_status', v === 'Any outreach' ? null : v.toLowerCase())}
                />
                <FilterSelect
                  label="ASSIGNED"
                  value={assignedToMe ? 'Me' : assignedParam ?? 'All LOs'}
                  options={['All LOs', ...(assignedToMe ? ['Me'] : []), ...salesTeam.map((member) => member.email)]}
                  onChange={(v) => updateParam('assigned_to', v === 'All LOs' ? null : v === 'Me' ? 'me' : v)}
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
              </>
            )}
          />
        </div>
      </div>
      {/* Assign-degradation stays visible: the Sales ops snapshot moved to the
          Analytics "Sales ops" tab, but the ASSIGNED filter + LeadTable assign
          actions still depend on the sales team, so surface its outage here,
          directly above the ranked-borrowers region. */}
      {salesTeamError && (
        <div role="alert" className="status-callout status-callout--warning mb-grid">
          {/* The buyer-safe vocabulary, never the transport message (audit states-04). */}
          Sales team unavailable:{' '}
          <DescribedErrorBody error={salesTeamError} subject="the sales team" />{' '}
          Lead assignment to LOs is degraded until it reconnects.
        </div>
      )}
      {meWithoutEmail && (
        <div role="alert" className="status-callout status-callout--warning" data-testid="lead-queue-me-unresolved">
          <span>The Assigned to me view needs your signed-in email, and this session has none, so no leads were read.</span>
          <button
            type="button"
            className="btn btn--ghost btn--sm"
            onClick={clearAllFilters}
            aria-label="Clear the Assigned to me filter"
          >
            Clear filters
          </button>
        </div>
      )}
      {/* One surface per state (audit states-04 / states-v2): the skeleton
          holds the slot through a first load and a warm-up (the banner can
          suppress the warming block), a failure says what happened in the
          shared vocabulary, a measured zero says why, and LeadTable mounts
          only for rows. */}
      {!meWithoutEmail && (
        <AsyncState
          query={leadsQueryState}
          subject="Ranked borrowers"
          loading={<LeadQueueTableSkeleton />}
          // A Growth Agent handoff is never "empty": its cohort proof and
          // verification status render in LeadTable's status chips, and a
          // verified cohort whose rows the filters hide must still show them.
          isEmpty={(page) => page.leads.length === 0 && !page.growthAgentVerification}
          empty={countyLoading ? (
            <div className="muted body mb-grid">Resolving county ZIPs…</div>
          ) : emptyModule && (
            <emptyModule.LeadQueueEmptyState
              filterParams={leadQueueFilterParams(searchParams)}
              countyNoCoverage={Boolean(countyFilter && countyZips && countyZips.size === 0)}
              intersection={segmentChips.length > 1 && segmentMode === 'all'}
              onClearFilters={clearAllFilters}
            />
          )}
          onClearFilters={clearAllFilters}
        >
          {(page) => (
            <>
              {countyLoading && !warming && (
                <div className="muted body mb-grid">
                  Resolving county ZIPs…
                </div>
              )}
              <div
                className={`stable-refresh-region stable-refresh-region--table ${queueUpdating ? 'is-updating' : ''}`}
                aria-busy={queueUpdating}
                data-status={queueStatusLabel}
              >
                <LeadTable
                  leads={visibleLeads}
                  totalMatching={page.totalMatching ?? null}
                  truncatedAt={page.truncatedAt ?? null}
                  growthAgentVerification={leadsPlaceholderData
                    ? null
                    : page.growthAgentVerification ?? null}
                  paging={pages.paging}
                  sortScope={order.sort === 'rank' && place.sort ? 'loaded' : 'server'}
                  exportContext={exportContext}
                  salesTeam={salesTeam}
                  view={tableView}
                  onViewChange={(next) => setSearchParams(searchParamsWithLeadTableView(searchParams, next))}
                  fillHeight
                  restoreScroll
                  headerStatus={freshness}
                  campaignHandoff={campaignHandoff}
                  // D-approval-flow-a2: entering is a new entry; leaving replaces it (Back never re-enters).
                  triage={{
                    mode: parseTriageMode(searchParams.get('mode')),
                    onModeChange: (mode, row) => setSearchParams(
                      searchParamsWithTriageMode(searchParams, mode, row), { replace: mode === null },
                    ),
                  }}
                  // A sort (and Reset to rank) is a new history entry; expand and
                  // collapse replace the current one, so Back leaves the queue.
                  sort={place.sort}
                  onSortChange={(next) => setSearchParams(
                    searchParamsWithLeadTablePlace(searchParams, { sort: next, row: expandedRow }),
                  )}
                  expandedId={expandedRow}
                  onExpandedChange={(borrowerId) => {
                    setRequestedRow({ row: borrowerId, against: searchParams });
                    setSearchParams(
                      searchParamsWithLeadTablePlace(searchParams, { row: borrowerId }),
                      { replace: true },
                    );
                  }}
                />
              </div>
            </>
          )}
        </AsyncState>
      )}
      {/* Address → borrower lookup: a secondary fast path, demoted from the
          hero slot so the operational queue leads. The Console right-rail
          quick action stays the primary lookup entry. */}
      {lookupModule && (
        <div className="mb-grid">
          <lookupModule.PropertyLookupPanel />
        </div>
      )}
    </PageShell>
  );
}
