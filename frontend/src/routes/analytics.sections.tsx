// React Compiler memo caches add enough code here to breach the strict bundle budget.
// Keep this route explicit: chart-scale derivations use targeted useMemo below.
'use no memo';

import { Suspense, useMemo } from 'react';
import { Link } from 'react-router';
import { GlossaryTerm } from '../components/GlossaryTerm';
import { Histogram } from '../components/charts/Histogram';
import { friendlyAssetLabel } from '../lib/assetLabels';
import { formatCompact, formatCount, formatFixed, formatUsdCompact, pct, signedBpsLabel } from '../lib/formatters';
import { offerDisplayLabel } from '../lib/offerLanguage';
import type {
  EconomicsAnalyticsResponse,
  EvidenceBySignalRow,
  GeographyAnalyticsResponse,
  SegmentAnalyticsResponse,
  SegmentByStateRow,
  SegmentCode,
  SegmentMetricRow,
  SignalEvidenceExample,
  SignalAnalyticsResponse,
  StateAvmValueRow,
  StateOpportunityRow,
  TopBorrowerAnalyticsRow,
  TopSegmentByStateRow,
  TopZipOpportunityRow,
} from '../types';
import {
  analyticsHref,
  borrowerDisplay,
  buildDailyEvidenceTotals,
  leadQueueHref,
  segmentIntelligenceHref,
  signalLabel,
  type LenderFilterParams,
} from './analytics.lib';
import {
  Bars,
  DailyEvidenceLineChart,
  DataTable,
  HistogramTable,
  ScopeChip,
  SectionHeader,
} from './analytics.charts';
import {
  SPREAD_BUCKET_BPS,
  formatBinRange,
  histogramModel,
  spreadSummary,
  thresholdNotice,
} from './analytics.chart-model';
import { lazyView } from './analytics.lazyView';
import type { AnalyticsQueryOptions } from '../lib/api';

// The scatter is its own chunk (W5e item 2b): Geography, Segments and Signals
// never download it, and Economics loads it beside its read.
const EquitySpreadScatter = lazyView(() => import('./analytics.equity-scatter').then((module) => module.EquitySpreadScatter));

/** Warm the scatter chunk (the Economics tab's own preload). */
export const preloadEquityScatter = (): Promise<unknown> => EquitySpreadScatter.preload();

/** The scatter panel's frame while its chunk loads: the loaded panel's height, so nothing below moves. */
function EquitySpreadScatterPending() {
  return (
    <section className="surface analytics-section" aria-busy="true">
      <div className="surface__hdr">
        <h2 className="h-3">Equity vs Rate Spread</h2>
      </div>
      <div className="surface__body analytics-chart-panel analytics-chart-panel--scatter">
        <span className="skeleton analytics-skeleton__chart" aria-hidden="true" />
      </div>
    </section>
  );
}

const spreadRange = (start: number) => formatBinRange(start, SPREAD_BUCKET_BPS, 'bps');

/**
 * Rate spreads as a histogram with the refresh's governed refi screen
 * (data.thresholds, dataviz-06): no rule and a notice when the refresh
 * carries no single screen.
 */
export function SpreadDistribution({ data }: { data: EconomicsAnalyticsResponse }) {
  const threshold = data.thresholds.min_spread_bps;
  const model = histogramModel(
    data.rate_spread_histogram.map((row) => ({ start: row.spread_bucket_bps, count: row.borrower_count })),
    SPREAD_BUCKET_BPS,
    threshold,
  );
  return (
    <Histogram
      title="Rate Spread Distribution"
      summary={spreadSummary(model, data.thresholds)}
      notice={thresholdNotice(data.thresholds)}
      bins={model.bins}
      binWidth={SPREAD_BUCKET_BPS}
      threshold={threshold}
      ruleLabel={threshold === null ? null : `Refi screen ≥ ${formatCount(threshold)} bps`}
      xLabel="Spread bps"
      yLabel="Borrowers"
      formatRange={spreadRange}
      table={
        <HistogramTable
          model={model}
          rangeLabel="Spread bps"
          pastLabel={threshold === null ? null : 'At or past the screen'}
          formatRange={spreadRange}
        />
      }
    />
  );
}

export function GeographyView({ data, leadParams }: { data: GeographyAnalyticsResponse; leadParams: LenderFilterParams }) {
  return (
    <>
      <SectionHeader title="Geography" action={<Link className="btn btn--sm" to={segmentIntelligenceHref(leadParams)}>Open map</Link>} />
      <div className="layoutA-grid analytics-grid">
        <section className="surface">
          <div className="surface__hdr"><h2 className="h-3">Opportunity by State</h2></div>
          <div className="surface__body">
            <Bars<StateOpportunityRow>
              rows={data.state_opportunities}
              value={(row) => row.in_the_money_borrowers}
              label={(row) => row.state}
              sublabel={(row) => `${formatCompact(row.borrower_count)} addressable · ${row.mean_opportunity_score} avg score`}
              href={(row) => leadQueueHref({ state: row.state, ...leadParams })}
            />
          </div>
        </section>
        <section className="surface">
          <div className="surface__hdr"><h2 className="h-3">AVM Value by State</h2></div>
          <div className="surface__body">
            <Bars<StateAvmValueRow>
              rows={data.state_avm_values}
              value={(row) => row.total_avm_value_usd}
              label={(row) => row.state}
              sublabel={(row) => `${formatUsdCompact(row.total_equity_usd)} equity`}
              href={(row) => leadQueueHref({ state: row.state, ...leadParams })}
            />
          </div>
        </section>
      </div>
      <section className="surface analytics-section">
        <div className="surface__hdr"><h2 className="h-3">Top ZIPs by Refi Economics</h2></div>
        <div className="surface__body">
          <DataTable<TopZipOpportunityRow>
            rows={data.top_zips}
            getKey={(row) => `${row.state}-${row.zip}`}
            columns={[
              { key: 'zip', label: 'ZIP', render: (row) => <Link to={leadQueueHref({ state: row.state, zip: row.zip, ...leadParams })}>{row.zip}</Link> },
              { key: 'place', label: 'Market', render: (row) => `${row.city ?? 'Unknown'}, ${row.state}` },
              { key: 'itm', label: 'Refi economics', render: (row) => formatCount(row.in_the_money_borrowers) },
              { key: 'score', label: 'Avg opportunity score', render: (row) => row.mean_opportunity_score },
              { key: 'spread', label: 'Avg rate spread', render: (row) => signedBpsLabel(row.mean_rate_spread_bps) },
            ]}
          />
        </div>
      </section>
    </>
  );
}

export function EconomicsView({
  data,
  filters,
  filterCriteria,
}: {
  data: EconomicsAnalyticsResponse;
  filters: AnalyticsQueryOptions;
  filterCriteria: ReadonlyArray<string | number>;
}) {
  return (
    <>
      <div className="layoutA-grid analytics-grid analytics-grid--wide-left">
        <section className="surface">
          <div className="surface__hdr"><h2 className="h-3">Rate Spread Distribution</h2></div>
          <div className="surface__body analytics-chart-panel">
            <SpreadDistribution data={data} />
          </div>
        </section>
        <section className="surface">
          <div className="surface__hdr">
            <h2 className="h-3">Top Borrowers</h2>
            {/* Consistency contract (2026-08-06): this panel now ranks with
                the exact governed predicate and ordering the Lead Queue and
                Ask Genie use — one top-10 everywhere. Labels remain the
                masked owner identity plus the borrower id's last 4; an owner
                holding multiple eligible properties can still appear more
                than once because each property loan is its own borrower
                record. */}
            <p className="analytics-panel-note">
              The same governed ranking Ask Genie and the Lead Queue use:
              marketing-eligible, opt-in borrowers ordered by opportunity
              score, then rate-spread economics. Labels show the masked owner
              identity plus the borrower id&apos;s last 4; an owner with
              multiple eligible properties can appear more than once.
            </p>
          </div>
          <div className="surface__body">
            <DataTable<TopBorrowerAnalyticsRow>
              rows={data.top_borrowers}
              getKey={(row) => row.borrower_id}
              columns={[
                { key: 'borrower', label: 'Borrower', render: (row) => <Link to={`/borrower-360/${row.borrower_id}`}>{borrowerDisplay(row)}</Link> },
                { key: 'score', label: 'Score', render: (row) => row.opportunity_score },
                { key: 'spread', label: 'Spread', render: (row) => signedBpsLabel(row.rate_spread_bps) },
                { key: 'offer', label: 'Offer', render: (row) => offerDisplayLabel(null, row.recommended_offer) },
              ]}
            />
          </div>
        </section>
      </div>
      <Suspense fallback={<EquitySpreadScatterPending />}>
        <EquitySpreadScatter
          overview={data.equity_spread}
          filters={filters}
          filterCriteria={filterCriteria}
        />
      </Suspense>
    </>
  );
}

export function SegmentsView({ data, leadParams }: { data: SegmentAnalyticsResponse; leadParams: LenderFilterParams }) {
  const topStates = data.top_segments_by_state.filter((row) => row.state_rank === 1);
  return (
    <>
      <section className="surface">
        <div className="surface__hdr surface__hdr--split">
          <div>
            <h2 className="h-3">Segment Overview</h2>
            {/* One plain-English scope note for the whole tab, replacing the
                repeated "full population · pre-suppression" badges. */}
            <p className="analytics-panel-note">Counts include all borrowers, before contact-eligibility filtering.</p>
          </div>
        </div>
        <div className="surface__body">
          <DataTable
            rows={data.overview}
            getKey={(row) => row.segment_code}
            columns={[
              { key: 'segment', label: 'Segment', render: (row) => <Link to={leadQueueHref({ segment: row.segment_code, ...leadParams })}>{row.name}</Link> },
              { key: 'borrowers', label: 'Borrowers', render: (row) => formatCount(row.borrower_count) },
              { key: 'score', label: 'Avg Score', render: (row) => row.mean_opportunity_score },
              { key: 'itm', label: 'Refi economics', render: (row) => formatCount(row.in_the_money_borrowers) },
              { key: 'approval', label: 'Approval', render: (row) => pct(row.approval_rate) },
            ]}
          />
        </div>
      </section>
      <div className="layoutA-grid analytics-grid">
        <section className="surface">
          <div className="surface__hdr">
            <h2 className="h-3">Segment Size</h2>
          </div>
          <div className="surface__body">
            <Bars<SegmentMetricRow>
              rows={data.counts}
              value={(row) => row.value}
              label={(row) => row.segment_name}
              href={(row) => leadQueueHref({ segment: row.segment_code, ...leadParams })}
            />
          </div>
        </section>
        <section className="surface">
          <div className="surface__hdr">
            <h2 className="h-3">Mean Opportunity Score</h2>
          </div>
          <div className="surface__body">
            <Bars<SegmentMetricRow>
              rows={data.average_scores}
              value={(row) => row.value}
              label={(row) => row.segment_name}
              href={(row) => leadQueueHref({ segment: row.segment_code, ...leadParams })}
            />
          </div>
        </section>
      </div>
      <section className="surface analytics-section">
        <div className="surface__hdr">
          <h2 className="h-3">Leading Segment by State</h2>
        </div>
        <div className="surface__body">
          <Bars<TopSegmentByStateRow | SegmentByStateRow>
            rows={topStates}
            value={(row) => row.borrower_count}
            label={(row) => `${row.state} · ${row.segment_name}`}
            href={(row) => leadQueueHref({ state: row.state, segment: row.segment_code, ...leadParams })}
          />
        </div>
      </section>
    </>
  );
}

function EvidenceExamplesTable({ rows }: { rows: SignalEvidenceExample[] }) {
  return (
    <DataTable<SignalEvidenceExample>
      rows={rows}
      getKey={(row, idx) => `${row.borrower_id}-${row.signal_type}-${row.timestamp}-${idx}`}
      columns={[
        { key: 'borrower', label: 'Borrower', render: (row) => <Link to={`/borrower-360/${row.borrower_id}`}>{borrowerDisplay(row)}</Link> },
        { key: 'signal', label: 'Signal', render: (row) => signalLabel(row.signal_type) },
        { key: 'value', label: 'Value', render: (row) => row.signal_value },
        { key: 'confidence', label: 'Evidence confidence', render: (row) => formatFixed(row.confidence, 3) },
        { key: 'source', label: 'Source', render: (row) => row.source_product },
      ]}
    />
  );
}

export function SignalsView({
  data,
  filterParams,
}: {
  data: SignalAnalyticsResponse;
  filterParams: { states: string[]; segmentCodes: SegmentCode[]; days: number } & LenderFilterParams;
}) {
  const dailyTotals = useMemo(() => buildDailyEvidenceTotals(data.evidence_daily), [data.evidence_daily]);

  return (
    <>
      <section className="surface">
        <div className="surface__hdr surface__hdr--split">
          <div>
            <h2 className="h-3">Evidence Events Per Day</h2>
            <p className="analytics-panel-note">
              Dates from the evidence-events feed; blank days are shown as zero.
              Lien, equity, and owner-graph signals carry their latest Cotality
              refresh-batch date — so those events land in bulk on refresh days —
              while AVM, market-rate, and ownership-transfer events carry true
              source dates.
            </p>
          </div>
          <ScopeChip>{data.evidence_daily.length} signal/date buckets</ScopeChip>
        </div>
        <div className="surface__body analytics-chart-panel">
          <DailyEvidenceLineChart rows={dailyTotals} />
        </div>
      </section>
      <section className="surface analytics-section">
        <div className="surface__hdr surface__hdr--split">
          <div>
            <h2 className="h-3">Evidence by Signal Type</h2>
            <p className="analytics-panel-note">
              Rows count evidence events by signal and Cotality source; each row's
              trailing decimal is the mean <GlossaryTerm term="evidenceConfidence" /> (0–1).
            </p>
          </div>
          <ScopeChip title="mip.gold.evidence_events">Evidence events</ScopeChip>
        </div>
        <div className="surface__body">
          <Bars<EvidenceBySignalRow>
            rows={data.evidence_by_signal}
            value={(row) => row.event_count}
            label={(row) => signalLabel(row.signal_type)}
            sublabel={(row) => `${row.source_product} · ${friendlyAssetLabel(row.source_label ?? row.source_table)} · ${formatFixed(row.mean_confidence, 3)}`}
            href={(row) => analyticsHref({
              states: filterParams.states.join(',') || null,
              segment_codes: filterParams.segmentCodes.join(',') || null,
              lender_relationship: filterParams.lender_relationship,
              target_lender_ref: filterParams.target_lender_ref,
              days: filterParams.days === 30 ? null : filterParams.days,
              signal_types: row.signal_type,
            })}
          />
        </div>
      </section>
      <section className="surface analytics-section">
        <div className="surface__hdr surface__hdr--split">
          <div>
            <h2 className="h-3">Evidence Drilldown</h2>
            <p className="analytics-panel-note">
              Highest evidence-confidence matching rows with borrower links. Each row is one evidence event attached to a borrower dossier.
            </p>
          </div>
          <ScopeChip>Top 25</ScopeChip>
        </div>
        <div className="surface__body">
          <EvidenceExamplesTable rows={data.evidence_examples} />
        </div>
      </section>
    </>
  );
}
