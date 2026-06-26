// React Compiler memo caches add enough code here to breach the strict bundle budget.
// Keep this route explicit: chart-scale derivations use targeted useMemo below.
'use no memo';

import { useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { Icon } from '../components/Icon';
import { PageShell } from '../components/layout/PageShell';
import { FilterSelect } from '../components/ui/FilterSelect';
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
  type AnalyticsTab,
} from './analytics.lib';
import { LoadState } from './analytics.charts';
import {
  EconomicsView,
  ExecutiveView,
  GeographyView,
  MultiFilterSelect,
  SegmentsView,
  SignalsView,
} from './analytics.sections';

// Re-export the symbols imported from './analytics' by analytics.test.tsx so the
// decomposition keeps every existing import path stable.
export {
  buildDailyEvidenceTotals,
  compactScatterRows,
  leadQueueHref,
  leadQueueHrefForFunnelStage,
  normalizeAnalyticsSegmentCodes,
  segmentIntelligenceHref,
} from './analytics.lib';
export { DailyEvidenceLineChart, LineChart, ScatterPlot } from './analytics.charts';

export default function AnalyticsRoute() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [tab, setTab] = useState<AnalyticsTab>('executive');
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
    ['analytics', 'executive', ...baseCriteria],
    { enabled: tab === 'executive', queryKey: queryKeys.analytics('executive', baseCriteria), staleTime: 60_000 },
  );
  const geography = useWarmingUpRetry<GeographyAnalyticsResponse>(
    (signal) => api.analyticsGeography(signal, baseFilters),
    ['analytics', 'geography', ...baseCriteria],
    { enabled: tab === 'geography', queryKey: queryKeys.analytics('geography', baseCriteria), staleTime: 60_000 },
  );
  const economics = useWarmingUpRetry<EconomicsAnalyticsResponse>(
    (signal) => api.analyticsEconomics(signal, baseFilters),
    ['analytics', 'economics', ...baseCriteria],
    { enabled: tab === 'economics', queryKey: queryKeys.analytics('economics', baseCriteria), staleTime: 60_000 },
  );
  const segments = useWarmingUpRetry<SegmentAnalyticsResponse>(
    (signal) => api.analyticsSegments(signal, baseFilters),
    ['analytics', 'segments', ...baseCriteria],
    { enabled: tab === 'segments', queryKey: queryKeys.analytics('segments', baseCriteria), staleTime: 60_000 },
  );
  const signals = useWarmingUpRetry<SignalAnalyticsResponse>(
    (signal) => api.analyticsSignals(signal, signalFilters),
    ['analytics', 'signals', ...signalCriteria],
    { enabled: tab === 'signals', queryKey: queryKeys.analytics('signals', signalCriteria), staleTime: 60_000 },
  );

  return (
    <PageShell
      eyebrow="Analytics"
      title="Analytics"
      lede="Portfolio command center for governed gold and semantic-table trends, geography, economics, segments, and evidence signals."
      heroRight={<Link className="btn btn--primary" to="/ask-genie"><Icon name="sparkle" size={14} /> Ask Genie</Link>}
    >
      <div className="analytics-tabs" role="tablist" aria-label="Analytics views">
        {TABS.map((item) => (
          <button
            key={item.id}
            type="button"
            role="tab"
            aria-selected={tab === item.id}
            className={`filter analytics-tab ${tab === item.id ? 'is-active' : ''}`}
            onClick={() => setTab(item.id)}
          >
            <Icon name={item.icon} size={12} />
            <span className="filter__value">{item.label}</span>
          </button>
        ))}
      </div>
      <div className="filter-row filter-row--spaced analytics-filters" aria-label="Analytics filters">
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
          onClick={() => setSearchParams(new URLSearchParams())}
        >
          Clear filters
        </button>
      </div>

      {tab === 'executive' && (
        <LoadState query={executive} title="Executive analytics">
          {(data) => <ExecutiveView data={data} leadParams={leadParams} />}
        </LoadState>
      )}
      {tab === 'geography' && (
        <LoadState query={geography} title="Geography analytics">
          {(data) => <GeographyView data={data} leadParams={leadParams} />}
        </LoadState>
      )}
      {tab === 'economics' && (
        <LoadState query={economics} title="Economics analytics">
          {(data) => <EconomicsView data={data} />}
        </LoadState>
      )}
      {tab === 'segments' && (
        <LoadState query={segments} title="Segment analytics">
          {(data) => <SegmentsView data={data} leadParams={leadParams} />}
        </LoadState>
      )}
      {tab === 'signals' && (
        <LoadState query={signals} title="Signal analytics">
          {(data) => <SignalsView data={data} filterParams={{ states, segmentCodes, days, ...leadParams }} />}
        </LoadState>
      )}
    </PageShell>
  );
}
