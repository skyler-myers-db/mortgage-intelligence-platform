// React Compiler memo caches add enough code here to breach the strict bundle budget.
// Keep this route explicit: chart-scale derivations use targeted useMemo below.
'use no memo';

import { useMemo } from 'react';
import { HIGH_OPPORTUNITY_SCORE_LABEL } from '../lib/opportunityScore';
import { Link } from 'react-router';
import { GlossaryTerm } from '../components/GlossaryTerm';
import { KpiCard } from '../components/mortgage/KpiCard';
import { friendlyAssetLabel } from '../lib/assetLabels';
import { formatCompact, formatCount, formatFixed, formatUsdCompact, pct, signedBpsLabel } from '../lib/formatters';
import { offerDisplayLabel } from '../lib/offerLanguage';
import { formatDate, formatTimestamp } from '../lib/time';
import { Timestamp } from '../components/ui/Timestamp';
import type {
  EconomicsAnalyticsResponse,
  EvidenceBySignalRow,
  ExecutiveAnalyticsResponse,
  ExecutiveProvenance,
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
  activationFunnelStages,
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
  FunnelBars,
  FunnelSankey,
  LineChart,
  ScopeChip,
  SectionHeader,
} from './analytics.charts';
import { EquitySpreadScatter } from './analytics.equity-scatter';
import { RateWindowSection } from './analytics.rate-window';
import type { AnalyticsQueryOptions } from '../lib/api';

/**
 * Provenance for the executive funnel. Stages 1-4 aggregate borrower_360;
 * Approved and Actioned read the gold lifecycle MIRROR of Lakebase, which the
 * Approval funnel tab reads directly. The mirror can trail by one sync — that
 * is by design, and the two tabs are never reconciled to match. Publishing the
 * sources and the mirror's as-of boundary is what lets someone comparing the
 * tabs see why a number differs instead of guessing which one is broken.
 */
export function ExecutiveProvenanceNote({
  provenance,
  leadParams,
}: {
  provenance: ExecutiveProvenance;
  leadParams: LenderFilterParams;
}) {
  return (
    <p className="analytics-panel-note">
      Stages 1&ndash;4 from <span className="mono">{provenance.population_source}</span>
      {provenance.snapshot_date ? <> (snapshot <Timestamp value={provenance.snapshot_date} format="date" />)</> : null}. Approved and
      Actioned from <span className="mono">{provenance.workflow_source}</span>, the gold mirror of
      Lakebase
      {provenance.lifecycle_synced_at ? <>, synced {formatTimestamp(provenance.lifecycle_synced_at)}</> : null}.
      The <Link to={analyticsHref({ view: 'approval-funnel', ...leadParams })}>Approval funnel</Link> tab
      reads the Lakebase approval tables directly and is exact, so a higher count there means this
      mirror is one sync behind &mdash; by design, not a discrepancy.
    </p>
  );
}

export function ExecutiveView({
  data,
  leadParams,
  filtersActive = false,
}: {
  data: ExecutiveAnalyticsResponse;
  leadParams: LenderFilterParams;
  /** The tab's state / segment / lender filters narrow the KPIs; the rate window says it ignores them. */
  filtersActive?: boolean;
}) {
  return (
    <>
      <div className="kpi-row">
        <KpiCard label="Addressable Borrowers" value={formatCount(data.totals.addressable_borrowers)} delta={data.totals.snapshot_date ? `Snapshot ${formatDate(data.totals.snapshot_date)}` : undefined} deltaDir="flat" />
        <KpiCard label="Refi Economics" value={formatCount(data.totals.in_the_money_borrowers)} delta={`${formatCount(data.totals.high_opportunity_borrowers)} score ${HIGH_OPPORTUNITY_SCORE_LABEL}`} deltaDir="up" />
        <KpiCard label="Primary Offer Paths" value={formatCount(data.totals.offer_recommended_borrowers)} delta="Offer path assigned" deltaDir="up" />
        <KpiCard label="Approved Outreach" value={formatCount(data.totals.approved_borrowers)} delta={`${formatCount(data.totals.actioned_borrowers)} actioned`} deltaDir="flat" />
      </div>
      <RateWindowSection filtersActive={filtersActive} />
      <section className="surface analytics-section">
        <div className="surface__hdr surface__hdr--split">
          <div>
            <h2 className="h-3">Activation funnel</h2>
            <p className="analytics-panel-note">
              Each stage is an independent cut of the addressable book, shown as its
              share of addressable &mdash; not a conversion from the stage before it. A
              high-opportunity borrower need not pass the refi-economics screen, and an
              approved borrower need not be high-opportunity. Only Approved &rarr; Actioned
              is nested, so only it carries a conversion.
              Offer-path coverage remains in Pipeline Metrics because nearly every
              borrower receives a governed branch, including nurture.
            </p>
          </div>
          <Link className="btn btn--sm" to={leadQueueHref(leadParams)}>Open queue</Link>
        </div>
        <div className="surface__body">
          <FunnelSankey stages={activationFunnelStages(data.stages)} leadParams={leadParams} />
        </div>
      </section>
      <div className="layoutA-grid analytics-grid">
        <section className="surface">
          <div className="surface__hdr surface__hdr--split">
            <h2 className="h-3">Opportunity Score Distribution</h2>
            <Link className="btn btn--sm" to={leadQueueHref(leadParams)}>Open queue</Link>
          </div>
          <div className="surface__body analytics-chart-panel">
            <LineChart
              rows={data.score_distribution}
              x={(row) => 'score_bucket' in row ? row.score_bucket : row.spread_bucket_bps}
              y={(row) => row.borrower_count}
              xLabel="Opportunity score"
              yLabel="Borrowers"
            />
          </div>
        </section>
        <section className="surface">
          <div className="surface__hdr surface__hdr--split">
            <div>
              <h2 className="h-3">Pipeline Metrics</h2>
              <ExecutiveProvenanceNote provenance={data.provenance} leadParams={leadParams} />
            </div>
            <ScopeChip>Exact figures</ScopeChip>
          </div>
          <div className="surface__body">
            <FunnelBars stages={data.stages} leadParams={leadParams} />
          </div>
        </section>
      </div>
    </>
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
            <LineChart
              rows={data.rate_spread_histogram}
              x={(row) => 'spread_bucket_bps' in row ? row.spread_bucket_bps : row.score_bucket}
              y={(row) => row.borrower_count}
              xLabel="Spread bps"
              yLabel="Borrowers"
              xUnit="bps"
            />
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
      <EquitySpreadScatter
        overview={data.equity_spread}
        filters={filters}
        filterCriteria={filterCriteria}
      />
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

// MultiFilterSelect moved to components/ui (2026-09-21 audit a11y-v1) so the
// Portfolio Builder state picker shares it; re-exported so analytics imports
// keep resolving here.
export { MultiFilterSelect } from '../components/ui/MultiFilterSelect';

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
