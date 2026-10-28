// React Compiler memo caches add enough code here to breach the strict bundle budget.
// Keep this route explicit: chart-scale derivations use targeted useMemo below.
'use no memo';

import { Suspense, useEffect, useMemo } from 'react';
import { Link, useSearchParams } from 'react-router';
import { Icon } from '../components/Icon';
import { PageShell } from '../components/layout/PageShell';
import { FetchedAt } from '../components/ui/FetchedAt';
import { FilterSelect } from '../components/ui/FilterSelect';
import { LazyStaleDataNote } from '../components/ui/StaleDataNote.lazy';
import { MultiFilterSelect } from '../components/ui/MultiFilterSelect';
import { useTabs } from '../components/ui/useTabs';
import '../components/ui/LayoutTabs.css';
import { useFootprint } from '../components/FootprintProvider';
import { api, type AnalyticsQueryOptions } from '../lib/api';
import { useConfigOptionsQuery } from '../lib/configOptionsQuery';
import { queryKeys } from '../lib/queryKeys';
import { useWarmingUpRetry } from '../lib/useWarmingUpRetry';
import { LENDER_RELATIONSHIP_OPTIONS } from '../lib/lenderFilters';
import type {
  EconomicsAnalyticsResponse,
  ExecutiveAnalyticsResponse,
  GeographyAnalyticsResponse,
  SegmentAnalyticsResponse,
  SignalAnalyticsResponse,
} from '../types';
import {
  EVIDENCE_WINDOW_OPTIONS,
  EVIDENCE_WINDOW_TO_DAYS,
  parseCsvParam,
  SEGMENT_MULTI_OPTIONS,
  SIGNAL_MULTI_OPTIONS,
  SIGNAL_TYPE_TO_OPTION,
  TABS,
  normalizeAnalyticsSegmentCodes,
  parseAnalyticsTab,
  type AnalyticsTab,
} from './analytics.lib';
import { LoadState } from './analytics.charts';
import { ANALYTICS_SKELETONS, AnalyticsSkeleton, type AnalyticsSkeletonShape } from './analytics.skeleton';
import { ExecutiveView } from './analytics.executive';
import { lazyView, warmChunk } from './analytics.lazyView';
import { useRateWindowQuery } from './analytics.rate-window';
import {
  AnalyticsEmpty,
  isEmptyExecutive,
  isEmptyGeography,
  isEmptySegments,
  isEmptySignals,
  refreshTab,
  tabFreshness,
  type TabRead,
} from './analytics.tabStates';
import { preloadBestEffort } from '../lib/lazyPreload';
import { useIsOnline } from '../lib/connectivity';

const TAB_IDS: readonly AnalyticsTab[] = TABS.map((item) => item.id);

// Per-tab chunks (2026-09-21 audit bundle-06, W5e item 2b): Executive is the
// default tab and stays in the route chunk; every other tab loads its own
// module, and the Economics scatter loads inside that one. The queries stay
// here, so a tab's read starts beside the route, not after its chunk.
const loadSections = () => import('./analytics.sections');
const GeographyView = lazyView(() => loadSections().then((module) => module.GeographyView));
const EconomicsView = lazyView(() => loadSections().then((module) => module.EconomicsView));
const SegmentsView = lazyView(() => loadSections().then((module) => module.SegmentsView));
const SignalsView = lazyView(() => loadSections().then((module) => module.SignalsView));
const ApprovalFunnelSection = lazyView(() => import('./analytics.approval-funnel').then((module) => module.ApprovalFunnelSection));
const SalesOpsSection = lazyView(() => import('./analytics.sales-ops').then((module) => module.SalesOpsSection));

/** Each tab's chunks (none for Executive): warmed on intent, loaded on selection. */
const TAB_CHUNKS: Partial<Record<AnalyticsTab, () => Promise<unknown>>> = {
  geography: GeographyView.preload,
  // Sections first (the scatter's own lazy object lives there), then the scatter.
  economics: () => EconomicsView.preload().then(() => loadSections()).then((module) => module.preloadEquityScatter()),
  segments: SegmentsView.preload,
  signals: SignalsView.preload,
  'approval-funnel': ApprovalFunnelSection.preload,
  'sales-ops': SalesOpsSection.preload,
};

/** The skeleton a tab shows while its chunk or its first read loads. */
const TAB_SKELETON: Record<AnalyticsTab, { title: string; shape: AnalyticsSkeletonShape }> = {
  executive: { title: 'Executive analytics', shape: ANALYTICS_SKELETONS.executive },
  geography: { title: 'Geography analytics', shape: ANALYTICS_SKELETONS.geography },
  economics: { title: 'Economics analytics', shape: ANALYTICS_SKELETONS.economics },
  segments: { title: 'Segment analytics', shape: ANALYTICS_SKELETONS.segments },
  signals: { title: 'Signal analytics', shape: ANALYTICS_SKELETONS.signals },
  'approval-funnel': { title: 'Approval funnel', shape: ANALYTICS_SKELETONS.approvalFunnel },
  'sales-ops': { title: 'Sales ops snapshot', shape: ANALYTICS_SKELETONS.panel },
};

export default function AnalyticsRoute() {
  const [searchParams, setSearchParams] = useSearchParams();
  // Tab selection is URL-addressable via `?view=`; unknown/absent -> executive.
  const tab = parseAnalyticsTab(searchParams.get('view'));
  const setTab = (next: AnalyticsTab) => {
    const params = new URLSearchParams(searchParams);
    if (next === 'executive') params.delete('view');
    else params.set('view', next);
    setSearchParams(params);
  };
  // APG tabs (audit a11y-02): arrow keys, Home/End, roving tabindex, and a
  // tabpanel the selected tab controls.
  const tabs = useTabs({ tabs: TAB_IDS, selected: tab, onSelect: setTab, idBase: 'analytics' });
  // The selected tab's chunk loads with its read (a deep link or a key press
  // as much as a click), never after the read lands. Offline the read is
  // paused and the fetch could only fail (a chunk error report), so the chunk
  // waits for the network like the read does.
  const online = useIsOnline();
  useEffect(() => {
    const chunk = TAB_CHUNKS[tab];
    if (chunk && online) preloadBestEffort(chunk);
  }, [tab, online]);
  const footprint = useFootprint();
  const configOptionsQuery = useConfigOptionsQuery();
  const targetLenderOptions = useMemo(() => {
    const values = configOptionsQuery.data?.target_lender_refs?.filter(Boolean);
    return values && values.length > 0 ? values : ['All'];
  }, [configOptionsQuery.data?.target_lender_refs]);
  const states = parseCsvParam(searchParams.get('states') ?? searchParams.get('state'))
    .map((value) => value.toUpperCase())
    .filter((value, idx, all) => /^[A-Z]{2}$/.test(value) && all.indexOf(value) === idx);
  const segmentCodes = normalizeAnalyticsSegmentCodes(parseCsvParam(searchParams.get('segment_codes')));
  const lenderRelationship = LENDER_RELATIONSHIP_OPTIONS.includes(searchParams.get('lender_relationship') as (typeof LENDER_RELATIONSHIP_OPTIONS)[number])
    ? searchParams.get('lender_relationship')!
    : 'All';
  const targetLenderFromUrl = searchParams.get('target_lender_ref');
  const targetLenderRef = targetLenderFromUrl
    && targetLenderFromUrl !== 'All'
    && targetLenderOptions.includes(targetLenderFromUrl)
    ? targetLenderFromUrl
    : null;
  const signalTypes = parseCsvParam(searchParams.get('signal_types') ?? searchParams.get('signal_type'))
    .map((value) => value.toLowerCase())
    .filter((value, idx, all) => Boolean(SIGNAL_TYPE_TO_OPTION[value]) && all.indexOf(value) === idx);
  const daysFromUrl = Number(searchParams.get('days') ?? 30);
  const days = [7, 30, 90].includes(daysFromUrl) ? daysFromUrl : 30;

  const stateOptions = useMemo(() => {
    const states = footprint.ready && !footprint.usingFallback
      ? footprint.states.map((item) => item.state_code).sort()
      : ['CA', 'CO', 'FL', 'IL', 'TX', 'WA'];
    return states.map((state) => ({ label: state, value: state }));
  }, [footprint.ready, footprint.states, footprint.usingFallback]);
  const windowValue = Object.entries(EVIDENCE_WINDOW_TO_DAYS).find(([, value]) => value === days)?.[0] ?? 'Last 30 days';
  const baseFilters = useMemo<AnalyticsQueryOptions>(() => ({
    states,
    segmentCodes,
    segmentMode: 'any',
    lenderRelationship: lenderRelationship !== 'All' ? lenderRelationship : null,
    targetLenderRef,
  }), [lenderRelationship, segmentCodes, states, targetLenderRef]);
  const signalFilters = useMemo<AnalyticsQueryOptions>(() => ({
    ...baseFilters,
    signalTypes,
    days,
  }), [baseFilters, days, signalTypes]);
  const leadParams = useMemo(
    () => ({
      lender_relationship: lenderRelationship !== 'All' ? lenderRelationship : null,
      target_lender_ref: targetLenderRef,
    }),
    [lenderRelationship, targetLenderRef],
  );
  const baseCriteria = [
    states.join(',') || 'all',
    segmentCodes.join(',') || 'all',
    lenderRelationship,
    targetLenderRef ?? 'all',
  ];
  const signalCriteria = [...baseCriteria, signalTypes.join(',') || 'all', days];
  const filtersActive = Boolean(
    states.length
    || segmentCodes.length
    || lenderRelationship !== 'All'
    || targetLenderRef
    || signalTypes.length
    || days !== 30,
  );

  const setMultiFilterParam = (key: 'states' | 'segment_codes' | 'signal_types', values: readonly string[]) => {
    const next = new URLSearchParams(searchParams);
    next.delete(key === 'states' ? 'state' : key === 'signal_types' ? 'signal_type' : key);
    if (values.length === 0) {
      next.delete(key);
      if (key === 'segment_codes') next.delete('segment_mode');
    } else {
      next.set(key, values.join(','));
      if (key === 'segment_codes') next.set('segment_mode', 'any');
    }
    setSearchParams(next);
  };

  const setWindowParam = (value: string) => {
    const next = new URLSearchParams(searchParams);
    if (value === 'Last 30 days') {
      next.delete('days');
    } else {
      next.set('days', String(EVIDENCE_WINDOW_TO_DAYS[value as (typeof EVIDENCE_WINDOW_OPTIONS)[number]] ?? 30));
    }
    setSearchParams(next);
  };

  const setSingleFilterParam = (key: 'lender_relationship' | 'target_lender_ref', value: string) => {
    const next = new URLSearchParams(searchParams);
    if (!value || value === 'All') next.delete(key);
    else next.set(key, value);
    setSearchParams(next);
  };

  const executive = useWarmingUpRetry<ExecutiveAnalyticsResponse>(
    (signal) => api.analyticsExecutive(signal, baseFilters),
    {
      enabled: tab === 'executive',
      queryKey: queryKeys.analytics('executive', baseCriteria),
      keepPreviousData: true,
      staleTime: 60_000,
    },
  );
  // Start the Executive tab's rate window beside the executive read, not after it renders.
  const rateWindow = useRateWindowQuery(tab === 'executive');
  const executiveFiltersActive = Boolean(states.length || segmentCodes.length || lenderRelationship !== 'All' || targetLenderRef);
  const geography = useWarmingUpRetry<GeographyAnalyticsResponse>(
    (signal) => api.analyticsGeography(signal, baseFilters),
    {
      enabled: tab === 'geography',
      queryKey: queryKeys.analytics('geography', baseCriteria),
      keepPreviousData: true,
      staleTime: 60_000,
    },
  );
  const economics = useWarmingUpRetry<EconomicsAnalyticsResponse>(
    (signal) => api.analyticsEconomics(signal, baseFilters),
    {
      enabled: tab === 'economics',
      queryKey: queryKeys.analytics('economics', baseCriteria),
      keepPreviousData: true,
      staleTime: 60_000,
    },
  );
  const segments = useWarmingUpRetry<SegmentAnalyticsResponse>(
    (signal) => api.analyticsSegments(signal, baseFilters),
    {
      enabled: tab === 'segments',
      queryKey: queryKeys.analytics('segments', baseCriteria),
      keepPreviousData: true,
      staleTime: 60_000,
    },
  );
  const signals = useWarmingUpRetry<SignalAnalyticsResponse>(
    (signal) => api.analyticsSignals(signal, signalFilters),
    {
      enabled: tab === 'signals',
      queryKey: queryKeys.analytics('signals', signalCriteria),
      keepPreviousData: true,
      staleTime: 60_000,
    },
  );

  // The active data tab's reads (states-09, delivery-05): their fetch age and
  // one Refresh in the hero, and a stale marker over data whose refresh
  // failed. Executive is two reads: the older fetch is the tab's age, and
  // Refresh re-reads both. Approval funnel and Sales ops state their own
  // as-of and period. Every analytics read is audit-free; Refresh is a click.
  const TAB_READS: Partial<Record<AnalyticsTab, readonly TabRead[]>> = {
    executive: [executive, rateWindow],
    geography: [geography],
    economics: [economics],
    segments: [segments],
    signals: [signals],
  };
  const tabReads = TAB_READS[tab] ?? null;
  const freshness = tabReads ? tabFreshness(tabReads) : null;
  // Clear data filters but stay on the current tab.
  const clearFilters = () => {
    const params = new URLSearchParams();
    if (tab !== 'executive') params.set('view', tab);
    setSearchParams(params);
  };
  const emptyTab = (active: boolean, windowDays?: number) => (
    <AnalyticsEmpty filtersActive={active} onClearFilters={clearFilters} windowDays={windowDays} />
  );

  return (
    <PageShell
      eyebrow="Analytics"
      title="Analytics"
      lede="Command center for portfolio trends, geography, economics, segments, and evidence signals."
      heroRight={
        <>
          {/* deviation:analytics-fetched-at: the active tab's own fetch age and
              its one Refresh, before the primary action. */}
          {tabReads && freshness && (
            <FetchedAt
              at={freshness.fetchedAt}
              subject={TAB_SKELETON[tab].title.toLowerCase()}
              isFetching={freshness.isFetching}
              onRefresh={() => refreshTab(tabReads)}
            />
          )}
          <Link className="btn btn--primary" to="/ask-genie"><Icon name="sparkle" size={14} /> Ask Genie</Link>
        </>
      }
    >
      <div className="layout-tabs analytics-tabs" {...tabs.tabListProps} aria-label="Analytics views">
        {TABS.map((item) => (
          <button
            key={item.id}
            {...tabs.tabProps(item.id)}
            className={tab === item.id ? 'is-active' : undefined}
            onPointerEnter={() => warmChunk(TAB_CHUNKS[item.id])}
            onFocus={() => warmChunk(TAB_CHUNKS[item.id])}
          >
            {item.label}
          </button>
        ))}
      </div>
      <div {...tabs.panelProps(tab)}>
      {tab !== 'sales-ops' && tab !== 'approval-funnel' && (
      <div className="filter-row filter-row--spaced analytics-filters" role="group" aria-label="Analytics filters">
        <MultiFilterSelect
          label="State"
          allLabel="All states"
          selected={states}
          options={stateOptions}
          onChange={(value) => setMultiFilterParam('states', value)}
        />
        <MultiFilterSelect
          label="Segment"
          allLabel="All segments"
          selected={segmentCodes}
          options={SEGMENT_MULTI_OPTIONS}
          onChange={(value) => setMultiFilterParam('segment_codes', value)}
        />
        <FilterSelect
          label="Relationship"
          value={lenderRelationship}
          options={[...LENDER_RELATIONSHIP_OPTIONS]}
          onChange={(value) => setSingleFilterParam('lender_relationship', value)}
        />
        <FilterSelect
          label="Target lien holder"
          value={targetLenderRef ?? 'All'}
          options={targetLenderOptions}
          onChange={(value) => setSingleFilterParam('target_lender_ref', value)}
        />
        {tab === 'signals' && (
          <>
            <MultiFilterSelect
              label="Signal"
              allLabel="All signals"
              selected={signalTypes}
              options={SIGNAL_MULTI_OPTIONS}
              onChange={(value) => setMultiFilterParam('signal_types', value)}
            />
            <FilterSelect
              label="Window"
              value={windowValue}
              options={[...EVIDENCE_WINDOW_OPTIONS]}
              onChange={setWindowParam}
            />
          </>
        )}
        <button
          type="button"
          className="btn btn--sm"
          disabled={!filtersActive}
          aria-disabled={!filtersActive}
          onClick={clearFilters}
        >
          Clear filters
        </button>
      </div>
      )}

      {/* Keyed by tab: a ?view= change shows the new tab's skeleton at once
          instead of holding the old tab while the chunk loads. */}
      {/* The data on screen is not current: its refresh failed (states-09). */}
      <LazyStaleDataNote lastGoodAt={freshness?.staleAt ?? null} />
      <Suspense key={tab} fallback={<AnalyticsSkeleton title={TAB_SKELETON[tab].title} shape={TAB_SKELETON[tab].shape} />}>
      {tab === 'executive' && (
        <LoadState
          query={executive}
          title="Executive analytics"
          skeleton={ANALYTICS_SKELETONS.executive}
          isEmpty={isEmptyExecutive}
          empty={emptyTab(executiveFiltersActive)}
        >
          {(data) => <ExecutiveView data={data} leadParams={leadParams} filtersActive={executiveFiltersActive} />}
        </LoadState>
      )}
      {tab === 'geography' && (
        <LoadState
          query={geography}
          title="Geography analytics"
          skeleton={ANALYTICS_SKELETONS.geography}
          isEmpty={isEmptyGeography}
          empty={emptyTab(executiveFiltersActive)}
        >
          {(data) => <GeographyView data={data} leadParams={leadParams} />}
        </LoadState>
      )}
      {tab === 'economics' && (
        <LoadState query={economics} title="Economics analytics" skeleton={ANALYTICS_SKELETONS.economics}>
          {(data) => <EconomicsView data={data} filters={baseFilters} filterCriteria={baseCriteria} />}
        </LoadState>
      )}
      {tab === 'segments' && (
        <LoadState
          query={segments}
          title="Segment analytics"
          skeleton={ANALYTICS_SKELETONS.segments}
          isEmpty={isEmptySegments}
          empty={emptyTab(executiveFiltersActive)}
        >
          {(data) => <SegmentsView data={data} leadParams={leadParams} />}
        </LoadState>
      )}
      {tab === 'signals' && (
        <LoadState
          query={signals}
          title="Signal analytics"
          skeleton={ANALYTICS_SKELETONS.signals}
          isEmpty={isEmptySignals}
          empty={emptyTab(filtersActive, days)}
        >
          {(data) => <SignalsView data={data} filterParams={{ states, segmentCodes, days, ...leadParams }} />}
        </LoadState>
      )}
      {tab === 'approval-funnel' && <ApprovalFunnelSection />}
      {tab === 'sales-ops' && <SalesOpsSection />}
      </Suspense>
      </div>
    </PageShell>
  );
}
