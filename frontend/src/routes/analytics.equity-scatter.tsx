// React Compiler memo caches add enough code here to breach the strict bundle
// budget. Keep this route module explicit, like its analytics siblings.
'use no memo';

import { useState, type CSSProperties, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { EvidenceChip } from '../components/Primitives';
import { api, type AnalyticsQueryOptions } from '../lib/api';
import { DRAWER_SOURCES } from '../lib/drawerSources';
import { scoreBand } from '../lib/opportunityScore';
import { queryKeys } from '../lib/queryKeys';
import { useWarmingUpRetry } from '../lib/useWarmingUpRetry';
import type {
  EquitySpreadOverview,
  EquitySpreadPointsResponse,
  EquitySpreadViewport,
} from '../types';
import { LoadState } from './analytics.charts';
import {
  MAX_SCATTER_POINTS,
  binCellRect,
  binDensityAlpha,
  binZoomViewport,
  fmt,
  formatAxisTick,
  makeTicks,
  overviewScatterLayout,
  pct,
  scatterPosition,
  zoomScatterLayout,
  type ScatterLayout,
} from './analytics.lib';

/**
 * S7 economics scatter (equity × rate spread).
 *
 * Overview mode renders the server's DENSITY BINS from
 * mip.gold.equity_spread_points — no raw borrower rows cross the wire until
 * the user zooms. Selecting a cell loads the REAL borrowers inside that
 * window from /analytics/economics/points, capped server-side, with an
 * honest "showing N of M" line. Dots are colored by the canonical S1 score
 * bands (scoreBand → .score--high/med/low) and deep-link to Borrower 360,
 * where the draft-only outreach composer is the terminal action.
 */
export function EquitySpreadScatter({
  overview,
  filters,
  filterCriteria,
}: {
  overview: EquitySpreadOverview;
  filters: AnalyticsQueryOptions;
  filterCriteria: ReadonlyArray<string | number>;
}) {
  const [viewport, setViewport] = useState<EquitySpreadViewport | null>(null);
  const zoomCriteria = viewport
    ? [...filterCriteria, viewport.equity_min, viewport.equity_max, viewport.spread_min, viewport.spread_max]
    : [...filterCriteria];
  const points = useWarmingUpRetry<EquitySpreadPointsResponse>(
    (signal) => api.analyticsEconomicsPoints(signal, filters, viewport ?? undefined),
    ['analytics', 'economics-points', ...zoomCriteria],
    {
      enabled: viewport !== null,
      queryKey: queryKeys.analytics('economics-points', zoomCriteria),
      keepPreviousData: true,
      staleTime: 60_000,
    },
  );

  return (
    <section className="surface analytics-section" data-testid="equity-spread-scatter">
      <div className="surface__hdr surface__hdr--split">
        <div className="surface__hdr-main">
          <h2 className="h-3">Equity vs Rate Spread</h2>
          <EvidenceChip
            source={{
              ...DRAWER_SOURCES.equitySpreadPoints,
              updatedAt: overview.refreshed_at ?? undefined,
            }}
          >
            {overview.source_table}
          </EvidenceChip>
        </div>
        {viewport && (
          <button type="button" className="btn btn--sm" onClick={() => setViewport(null)}>
            Reset zoom
          </button>
        )}
      </div>
      <div className="surface__body analytics-chart-panel analytics-chart-panel--scatter">
        {viewport === null ? (
          <EquitySpreadBinsView overview={overview} onZoom={setViewport} />
        ) : (
          <LoadState query={points} title="Borrower points">
            {(payload) => <EquitySpreadPointsView payload={payload} />}
          </LoadState>
        )}
      </div>
    </section>
  );
}

function ScatterFrame({
  layout,
  xTicks,
  yTicks,
  overlay,
  meta,
}: {
  layout: ScatterLayout;
  xTicks: number[];
  yTicks: number[];
  overlay: ReactNode;
  meta: ReactNode;
}) {
  return (
    <div className="analytics-scatter-wrap">
      <div className="analytics-chart__plot">
        <div className="analytics-chart__y-ticks" aria-hidden="true">
          {[...yTicks].reverse().map((tick) => (
            <span
              key={tick}
              className="analytics-chart__tick analytics-chart__tick--y"
              style={{ '--tick-pos': `${100 - pct(tick - layout.yMin, layout.yRange)}%` } as CSSProperties}
            >
              {formatAxisTick(tick)}
            </span>
          ))}
        </div>
        <div className="analytics-chart__canvas">
          <svg viewBox="0 0 100 100" preserveAspectRatio="none" className="analytics-scatter" role="img" aria-label="Equity versus rate spread grid">
            {yTicks.map((tick) => (
              <line
                key={`y-${tick}`}
                x1="0"
                x2="100"
                y1={100 - pct(tick - layout.yMin, layout.yRange)}
                y2={100 - pct(tick - layout.yMin, layout.yRange)}
                className="analytics-chart__grid"
                vectorEffect="non-scaling-stroke"
              />
            ))}
            {xTicks.map((tick) => (
              <line
                key={`x-${tick}`}
                x1={pct(tick - layout.xMin, layout.xRange)}
                x2={pct(tick - layout.xMin, layout.xRange)}
                y1="0"
                y2="100"
                className="analytics-chart__grid"
                vectorEffect="non-scaling-stroke"
              />
            ))}
          </svg>
          {overlay}
          <div className="analytics-chart__x-ticks" aria-hidden="true">
            {xTicks.map((tick) => (
              <span
                key={tick}
                className="analytics-chart__tick analytics-chart__tick--x"
                style={{ '--tick-pos': `${pct(tick - layout.xMin, layout.xRange)}%` } as CSSProperties}
              >
                {formatAxisTick(tick)}
              </span>
            ))}
          </div>
        </div>
      </div>
      <div className="analytics-chart__axis analytics-chart__axis--x">Equity percent</div>
      <div className="analytics-chart__axis analytics-chart__axis--y">Rate spread bps</div>
      {meta}
    </div>
  );
}

export function EquitySpreadBinsView({
  overview,
  onZoom,
}: {
  overview: EquitySpreadOverview;
  onZoom: (viewport: EquitySpreadViewport) => void;
}) {
  if (overview.bins.length === 0) {
    return <div className="analytics-empty">No borrower points returned.</div>;
  }
  const layout = overviewScatterLayout(overview);
  const maxCount = Math.max(1, ...overview.bins.map((bin) => bin.borrower_count));
  return (
    <ScatterFrame
      layout={layout}
      xTicks={makeTicks(overview.equity_domain_min, overview.equity_domain_max)}
      yTicks={makeTicks(overview.spread_domain_min, overview.spread_domain_max)}
      overlay={
        <div className="analytics-scatter__points" aria-label="Borrower density cells">
          {overview.bins.map((bin) => {
            const rect = binCellRect(bin, overview);
            const band = scoreBand(bin.mean_opportunity_score);
            return (
              <button
                key={`${bin.equity_bin_pct}:${bin.spread_bin_bps}`}
                type="button"
                className={`analytics-scatter__bin score--${band}`}
                style={{
                  '--bin-x': `${rect.xPct}%`,
                  '--bin-y': `${rect.yPct}%`,
                  '--bin-w': `${rect.wPct}%`,
                  '--bin-h': `${rect.hPct}%`,
                  '--bin-alpha': binDensityAlpha(bin.borrower_count, maxCount),
                } as CSSProperties}
                onClick={() => onZoom(binZoomViewport(bin, overview))}
                aria-label={`${fmt(bin.borrower_count)} borrowers near ${bin.equity_bin_pct}% equity and ${bin.spread_bin_bps} bps spread, mean score ${bin.mean_opportunity_score}. Zoom in to load real borrowers.`}
                title={`${fmt(bin.borrower_count)} borrowers · mean score ${bin.mean_opportunity_score} · ${fmt(bin.in_the_money_borrowers)} in the money`}
              />
            );
          })}
        </div>
      }
      meta={
        <p className="analytics-scatter-meta muted fs-12" data-testid="scatter-meta">
          {fmt(overview.total_borrowers)} borrowers, binned server-side. Select a cell to load the real
          borrowers inside it.
        </p>
      }
    />
  );
}

export function EquitySpreadPointsView({ payload }: { payload: EquitySpreadPointsResponse }) {
  const layout = zoomScatterLayout(payload.viewport);
  // The server orders by opportunity score DESC and caps at point_cap; keep
  // the DOM responsive by plotting at most MAX_SCATTER_POINTS of those and
  // saying so — the meta line never pretends the plot is the population.
  const plotted = payload.points.slice(0, MAX_SCATTER_POINTS);
  const meta = (
    <p className="analytics-scatter-meta muted fs-12" data-testid="scatter-meta">
      Showing {fmt(payload.showing)} of {fmt(payload.total_matching)} borrowers in this window
      {payload.truncated ? ` (server cap ${fmt(payload.point_cap)})` : ''}
      {plotted.length < payload.points.length
        ? `; plotting the top ${fmt(plotted.length)} by opportunity score`
        : ''}
      . Dots open Borrower 360.
    </p>
  );
  if (payload.points.length === 0) {
    return (
      <div className="analytics-empty" data-testid="scatter-meta">
        No borrowers in this window. Showing 0 of 0 — reset the zoom to return to the overview.
      </div>
    );
  }
  return (
    <ScatterFrame
      layout={layout}
      xTicks={makeTicks(payload.viewport.equity_min, payload.viewport.equity_max)}
      yTicks={makeTicks(payload.viewport.spread_min, payload.viewport.spread_max)}
      overlay={
        <div className="analytics-scatter__points" aria-label="Borrower drilldown points">
          {plotted.map((point) => {
            const position = scatterPosition(point.equity_pct, point.rate_spread_bps, layout);
            const band = point.score_band ?? scoreBand(point.opportunity_score);
            return (
              <Link
                key={point.borrower_id}
                className={`analytics-scatter__dot analytics-scatter__dot--band score--${band}`}
                to={`/borrower-360/${encodeURIComponent(point.borrower_id)}`}
                style={{
                  '--dot-x': `${position.xPct}%`,
                  '--dot-y': `${position.yPct}%`,
                } as CSSProperties}
                aria-label={`${point.display_name}: ${point.equity_pct}% equity, ${point.rate_spread_bps} bps spread, score ${point.opportunity_score} (${band}). Open Borrower 360.`}
                title={`${point.display_name} · ${point.segment} · ${point.state} · ${point.equity_pct}% equity · ${point.rate_spread_bps} bps · score ${point.opportunity_score}`}
              />
            );
          })}
        </div>
      }
      meta={meta}
    />
  );
}
