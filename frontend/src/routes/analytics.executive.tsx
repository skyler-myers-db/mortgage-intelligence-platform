// React Compiler memo caches add enough code here to breach the strict bundle budget.
// Keep this route explicit: chart-scale derivations use targeted useMemo below.
'use no memo';

import { HIGH_OPPORTUNITY_SCORE_LABEL, HIGH_OPPORTUNITY_THRESHOLD } from '../lib/opportunityScore';
import { Link } from 'react-router';
import { KpiCard } from '../components/mortgage/KpiCard';
import { Histogram } from '../components/charts/Histogram';
import { formatCount } from '../lib/formatters';
import { formatDate, formatTimestamp } from '../lib/time';
import { Timestamp } from '../components/ui/Timestamp';
import type {
  ExecutiveAnalyticsResponse,
  ExecutiveProvenance,
} from '../types';
import {
  analyticsHref,
  activationFunnelStages,
  leadQueueHref,
  type LenderFilterParams,
} from './analytics.lib';
import {
  FunnelBars,
  FunnelSankey,
  HistogramTable,
  ScopeChip,
} from './analytics.charts';
import {
  SCORE_BUCKET_WIDTH,
  formatBinRange,
  histogramModel,
  scoreSummary,
} from './analytics.chart-model';
import { RateWindowSection } from './analytics.rate-window';

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

const scoreRange = (start: number) => formatBinRange(start, SCORE_BUCKET_WIDTH);

/**
 * Opportunity scores as a histogram (dataviz-06). The rule sits at the pinned
 * high-opportunity threshold, and its label carries the governed
 * high-opportunity count from the executive totals, never re-summed bins.
 */
export function ScoreDistribution({ data }: { data: ExecutiveAnalyticsResponse }) {
  const model = histogramModel(
    data.score_distribution.map((row) => ({ start: row.score_bucket, count: row.borrower_count })),
    SCORE_BUCKET_WIDTH,
    HIGH_OPPORTUNITY_THRESHOLD,
  );
  const pastLabel = `Score ${HIGH_OPPORTUNITY_SCORE_LABEL}`;
  return (
    <Histogram
      title="Opportunity Score Distribution"
      summary={scoreSummary(model, data.totals)}
      bins={model.bins}
      binWidth={SCORE_BUCKET_WIDTH}
      threshold={HIGH_OPPORTUNITY_THRESHOLD}
      ruleLabel={`${HIGH_OPPORTUNITY_SCORE_LABEL} · ${formatCount(data.totals.high_opportunity_borrowers)} borrowers`}
      xLabel="Opportunity score"
      yLabel="Borrowers"
      formatRange={scoreRange}
      table={<HistogramTable model={model} rangeLabel="Opportunity score" pastLabel={pastLabel} formatRange={scoreRange} />}
    />
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
            <ScoreDistribution data={data} />
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
