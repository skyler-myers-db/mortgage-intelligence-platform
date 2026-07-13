// React Compiler memo caches add enough code here to breach the strict bundle
// budget. Keep this route module explicit, like its analytics siblings.
'use no memo';

import { useId, useRef, useState, type CSSProperties, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { EvidenceChip } from '../components/Primitives';
import { api, type AnalyticsQueryOptions } from '../lib/api';
import { DRAWER_SOURCES } from '../lib/drawerSources';
import {
  SCORE_BAND_HIGH_MIN,
  SCORE_BAND_MED_MIN,
  scoreBand,
  type ScoreBand,
} from '../lib/opportunityScore';
import { queryKeys } from '../lib/queryKeys';
import { useWarmingUpRetry } from '../lib/useWarmingUpRetry';
import type {
  EquitySpreadCoordinateGroup,
  EquitySpreadOverview,
  EquitySpreadPoint,
  EquitySpreadPointsResponse,
  EquitySpreadViewport,
} from '../types';
import { LoadState } from './analytics.charts';
import './analytics.scatter.css';
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
  scoreScope,
}: {
  layout: ScatterLayout;
  xTicks: number[];
  yTicks: number[];
  overlay: ReactNode;
  meta: ReactNode;
  scoreScope: 'overview' | 'borrower';
}) {
  return (
    <>
      <ScoreBandLegend scope={scoreScope} />
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
    </>
  );
}

const SCORE_BAND_LEGEND: ReadonlyArray<{
  band: ScoreBand;
  label: string;
  range: string;
}> = [
  { band: 'high', label: 'High', range: `${SCORE_BAND_HIGH_MIN}-100` },
  {
    band: 'med',
    label: 'Medium',
    range: `${SCORE_BAND_MED_MIN}-${SCORE_BAND_HIGH_MIN - 1}`,
  },
  { band: 'low', label: 'Low', range: `0-${SCORE_BAND_MED_MIN - 1}` },
];

const CLUSTER_PAGE_SIZE = 50;

export function ScoreBandLegend({ scope }: { scope: 'overview' | 'borrower' }) {
  const scopeLabel = scope === 'overview' ? 'overview cell means' : 'borrower drilldown points';
  return (
    <div
      className="analytics-scatter-legend"
      role="group"
      aria-label={`Opportunity score color legend for ${scopeLabel}`}
    >
      <div className="analytics-scatter-legend__items chip-row">
        {SCORE_BAND_LEGEND.map(({ band, label, range }) => (
          <span key={band} className={`score score--${band} analytics-scatter-legend__band`}>
            <span className="score__dot" aria-hidden="true" />
            {label} {range}
          </span>
        ))}
        {scope === 'borrower' && (
          <span className="analytics-scatter-legend__cluster">
            <span className="analytics-scatter-legend__cluster-count" aria-hidden="true">3</span>
            Exact-coordinate cluster count
          </span>
        )}
      </div>
      <p className="analytics-scatter-legend__scope muted fs-12 flush">
        {scope === 'overview'
          ? 'Color metric: mean opportunity score per overview cell.'
          : 'Colors show individual opportunity scores. A numbered marker uses the highest score in its cluster; its number is the borrower count. Open it for every borrower link.'}
      </p>
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
      scoreScope="overview"
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
  const [openClusterKey, setOpenClusterKey] = useState<string | null>(null);
  const layout = zoomScatterLayout(payload.viewport);
  // The server orders by opportunity score DESC and caps at point_cap; keep
  // the plot responsive by selecting at most MAX_SCATTER_POINTS top-ranked
  // borrowers. Exact-coordinate groups are progressively disclosed when
  // opened so a dense cluster does not add thousands of hidden links to the
  // initial DOM.
  const plotted = payload.points.slice(0, MAX_SCATTER_POINTS);
  const coordinateGroups = groupExactCoordinatePoints(plotted);
  const meta = (
    <p className="analytics-scatter-meta muted fs-12" data-testid="scatter-meta">
      Showing {fmt(payload.showing)} of {fmt(payload.total_matching)} borrowers in this window
      {payload.truncated ? ` (server cap ${fmt(payload.point_cap)})` : ''}
      {plotted.length < payload.points.length
        ? `; plotting the top ${fmt(plotted.length)} by opportunity score across ${fmt(coordinateGroups.length)} coordinate markers`
        : ''}
      . Single points open Borrower 360; numbered clusters reveal every borrower at that coordinate.
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
      scoreScope="borrower"
      overlay={
        <div className="analytics-scatter__points" aria-label="Borrower drilldown points">
          {coordinateGroups.map((group) => (
            group.points.length === 1 ? (
              <ScatterPointLink key={group.key} point={group.points[0]} layout={layout} />
            ) : (
              <ScatterPointCluster
                key={group.key}
                group={group}
                layout={layout}
                open={openClusterKey === group.key}
                onOpenChange={(open) => setOpenClusterKey(open ? group.key : null)}
              />
            )
          ))}
        </div>
      }
      meta={meta}
    />
  );
}

function pointCoordinateKey(point: Pick<EquitySpreadPoint, 'equity_pct' | 'rate_spread_bps'>): string {
  return `${point.equity_pct}\u0000${point.rate_spread_bps}`;
}

export function groupExactCoordinatePoints(
  points: ReadonlyArray<EquitySpreadPoint>,
): EquitySpreadCoordinateGroup[] {
  const groups = new Map<string, EquitySpreadCoordinateGroup>();
  for (const point of points) {
    const key = pointCoordinateKey(point);
    const existing = groups.get(key);
    if (existing) {
      existing.points.push(point);
    } else {
      groups.set(key, {
        key,
        equity_pct: point.equity_pct,
        rate_spread_bps: point.rate_spread_bps,
        points: [point],
      });
    }
  }
  for (const group of groups.values()) {
    group.points.sort((left, right) => (
      left.borrower_id < right.borrower_id ? -1 : left.borrower_id > right.borrower_id ? 1 : 0
    ));
  }
  return [...groups.values()];
}

function ScatterPointLink({ point, layout }: { point: EquitySpreadPoint; layout: ScatterLayout }) {
  const position = scatterPosition(point.equity_pct, point.rate_spread_bps, layout);
  const band = point.score_band ?? scoreBand(point.opportunity_score);
  return (
    <Link
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
}

function ScatterPointCluster({
  group,
  layout,
  open,
  onOpenChange,
}: {
  group: EquitySpreadCoordinateGroup;
  layout: ScatterLayout;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const panelId = useId();
  const [visibleCount, setVisibleCount] = useState(CLUSTER_PAGE_SIZE);
  const markerRef = useRef<HTMLButtonElement>(null);
  const position = scatterPosition(group.equity_pct, group.rate_spread_bps, layout);
  const highestScore = Math.max(...group.points.map((point) => point.opportunity_score));
  const band = scoreBand(highestScore);
  const alignment = position.xPct < 24
    ? 'analytics-scatter__cluster--align-start'
    : position.xPct > 76
      ? 'analytics-scatter__cluster--align-end'
      : '';
  const vertical = position.yPct > 58 ? 'analytics-scatter__cluster--above' : '';
  return (
    <div
      className={`analytics-scatter__cluster ${alignment} ${vertical}`}
      style={{
        '--dot-x': `${position.xPct}%`,
        '--dot-y': `${position.yPct}%`,
      } as CSSProperties}
      onKeyDown={(event) => {
        if (event.key !== 'Escape' || !open) return;
        event.preventDefault();
        onOpenChange(false);
        markerRef.current?.focus();
      }}
    >
      <button
        ref={markerRef}
        type="button"
        className={`analytics-scatter__cluster-marker score--${band}`}
        aria-expanded={open}
        aria-controls={panelId}
        aria-label={`${group.points.length} borrowers at ${group.equity_pct}% equity and ${group.rate_spread_bps} bps spread. Open exact-coordinate cluster.`}
        title={`${group.points.length} borrowers at the same coordinate`}
        onClick={() => onOpenChange(!open)}
      >
        {group.points.length}
      </button>
      <div
        id={panelId}
        className="analytics-scatter__cluster-panel"
        role="group"
        aria-label={`${group.points.length} borrowers at ${group.equity_pct}% equity and ${group.rate_spread_bps} bps spread`}
        hidden={!open}
      >
        <div className="analytics-scatter__cluster-title">
          {group.points.length} borrowers · {group.equity_pct}% equity · {group.rate_spread_bps} bps
        </div>
        {open && <ul className="analytics-scatter__cluster-list">
          {group.points.slice(0, visibleCount).map((point) => {
            const pointBand = point.score_band ?? scoreBand(point.opportunity_score);
            return (
              <li key={point.borrower_id}>
                <Link
                  className="analytics-scatter__cluster-link"
                  to={`/borrower-360/${encodeURIComponent(point.borrower_id)}`}
                  aria-label={`${point.display_name}, ${point.borrower_id}, score ${point.opportunity_score} (${pointBand}). Open Borrower 360.`}
                >
                  <span className={`analytics-scatter__cluster-score score--${pointBand}`} aria-hidden="true" />
                  <span>
                    <strong>{point.display_name}</strong>
                    <span className="mono">{point.borrower_id}</span>
                  </span>
                  <span className="mono num">{point.opportunity_score}</span>
                </Link>
              </li>
            );
          })}
          {visibleCount < group.points.length && (
            <li>
              <button
                type="button"
                className="analytics-scatter__cluster-more"
                onClick={() => setVisibleCount((count) => Math.min(count + CLUSTER_PAGE_SIZE, group.points.length))}
              >
                Show {Math.min(CLUSTER_PAGE_SIZE, group.points.length - visibleCount)} more
              </button>
            </li>
          )}
        </ul>}
      </div>
    </div>
  );
}
