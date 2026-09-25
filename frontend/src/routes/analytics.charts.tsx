// React Compiler memo caches add enough code here to breach the strict bundle budget.
// Keep this route explicit: chart-scale derivations use targeted useMemo below.
'use no memo';

import {
  useId,
  useMemo,
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
} from 'react';
import { Link, useNavigate } from 'react-router';
import { Icon } from '../components/Icon';
import { WarmingUpBlock } from '../components/ui/WarmingUpBlock';
import { type UseWarmingUpRetryResult } from '../lib/useWarmingUpRetry';
import { useFirstAppearance } from '../lib/useFirstAppearance';
import { niceTicks } from '../lib/chartTicks';
import { fixedAttr } from '../lib/fixedPrecision';
import { formatCompact, formatCount } from '../lib/formatters';
import type { FunnelStage } from '../types';
import type { HistogramModel, HistogramModelBin } from './analytics.chart-model';
import {
  buildFunnelSankeyModel,
  categoricalTickIndexes,
  funnelStageDisplayLabel,
  formatAxisTick,
  formatConversionPct,
  formatShortDate,
  leadQueueHrefForFunnelStage,
  pct,
  type DailyEvidenceTotal,
  type LenderFilterParams,
} from './analytics.lib';
import { AnalyticsSkeleton, ANALYTICS_SKELETONS, type AnalyticsSkeletonShape } from './analytics.skeleton';

export function LoadState<T>({
  query,
  title,
  skeleton = ANALYTICS_SKELETONS.panel,
  children,
}: {
  query: UseWarmingUpRetryResult<T>;
  title: string;
  /** The loaded view's shape, reserved while it loads (analytics.skeleton.tsx). */
  skeleton?: AnalyticsSkeletonShape;
  children: (data: T) => ReactNode;
}) {
  if (query.data) {
    const updating = query.isFetching || Boolean(query.warmingUp);
    return (
      <div
        className={`stable-refresh-region ${updating ? 'is-updating' : ''}`}
        aria-busy={updating ? true : undefined}
      >
        {updating && (
          <span className="stable-refresh-status" role="status" aria-live="polite">
            Updating {title.toLowerCase()}
          </span>
        )}
        {children(query.data)}
      </div>
    );
  }
  if (query.warmingUp) {
    return <WarmingUpBlock state={query.warmingUp} title={title} compact />;
  }
  if (query.error) {
    return (
      <div className="surface surface--danger">
        <div className="surface__hdr surface__hdr--split">
          <div className="surface__hdr-main">
            <div className="surface__icon"><Icon name="info" size={14} /></div>
            <h2 className="h-3">{title}</h2>
          </div>
          <button type="button" className="btn btn--sm" onClick={query.manualRetry}>
            Retry
          </button>
        </div>
        <div className="surface__body body">
          {query.error.message || 'Analytics are unavailable.'}
        </div>
      </div>
    );
  }
  return <AnalyticsSkeleton title={title} shape={skeleton} />;
}

export function SectionHeader({ title, action }: { title: string; action?: ReactNode }) {
  return (
    <div className="section-hdr analytics-section-hdr">
      <h2 className="h-2">{title}</h2>
      {action}
    </div>
  );
}

export function ScopeChip({ children, title }: { children: ReactNode; title?: string }) {
  return (
    <span className="chip chip--neutral analytics-scope-chip" title={title}>
      <span className="chip__label">{children}</span>
    </span>
  );
}

export function Bars<T>({
  rows,
  value,
  label,
  sublabel,
  href,
}: {
  rows: T[];
  value: (row: T) => number;
  label: (row: T) => string;
  sublabel?: (row: T) => string;
  href?: (row: T) => string;
}) {
  const max = Math.max(1, ...rows.map(value));
  if (rows.length === 0) return <div className="analytics-empty">No rows returned.</div>;
  return (
    <div className="analytics-bars">
      {rows.map((row) => {
        const rowValue = value(row);
        const node = (
          <>
            <span className="analytics-bars__label">{label(row)}</span>
            <span className="analytics-bars__track" aria-hidden="true">
              <span
                className="analytics-bars__fill"
                style={{ '--bar-pct': `${pct(rowValue, max)}%` } as CSSProperties}
              />
            </span>
            <span className="analytics-bars__value num">{formatCompact(rowValue)}</span>
            {sublabel && <span className="analytics-bars__sub">{sublabel(row)}</span>}
          </>
        );
        const key = `${label(row)}-${rowValue}`;
        return href ? (
          <Link key={key} className="analytics-bars__row analytics-bars__row--link" to={href(row)}>
            {node}
          </Link>
        ) : (
          <div key={key} className="analytics-bars__row">
            {node}
          </div>
        );
      })}
    </div>
  );
}

export function FunnelBars({ stages, leadParams = {} }: { stages: FunnelStage[]; leadParams?: LenderFilterParams }) {
  return (
    <Bars
      rows={[...stages].sort((a, b) => a.stage_order - b.stage_order)}
      value={(row) => row.borrower_count}
      label={(row) => funnelStageDisplayLabel(row)}
      href={(row) => leadQueueHrefForFunnelStage(row, leadParams)}
    />
  );
}

/**
 * FunnelSankey (re-audit Buyer-Wow #5) — the value story at a glance: the
 * activation stages (addressable, refi economics, high-opportunity, approved,
 * actioned) rendered as connected ribbons. Pure client-side SVG over the SAME
 * FunnelStage[] the Pipeline Metrics bars use — no new data, fully
 * deterministic. Each stage is a keyboard-focusable link to its slice of
 * the lead queue; the ribbons draw in ONCE on first appearance (gated by
 * useFirstAppearance, disabled under prefers-reduced-motion). The exact
 * figures stay in the Pipeline Metrics bars below.
 *
 * Labels (2026-09-21 audit, dataviz-v1 + dataviz-03): the stages are
 * independent cuts of the addressable book, NOT a kept-versus-dropped
 * pipeline, so each prints its share of ADDRESSABLE and only the one
 * SQL-nested pair (Approved → Actioned) prints a conversion — see
 * NESTED_FUNNEL_STAGE_PAIRS in analytics.lib.ts. Small stages draw at a
 * minimum visible thickness on the unchanged linear scale, and the chart
 * says "not to scale" whenever it does that.
 */
export function FunnelSankey({
  stages,
  leadParams = {},
}: {
  stages: FunnelStage[];
  leadParams?: LenderFilterParams;
}) {
  const navigate = useNavigate();
  const gradientId = useId().replace(/:/g, '');
  const model = useMemo(() => buildFunnelSankeyModel(stages), [stages]);
  const total = useMemo(() => stages.reduce((sum, s) => sum + Math.max(0, s.borrower_count), 0), [stages]);
  // One-time entrance keyed on the funnel's STRUCTURE (stage identity), not the
  // volatile counts — so a live data refresh never re-keys and replays the
  // draw-in mid-walkthrough (same class of fix as the KpiCard entrance, D5).
  const animate = useFirstAppearance(`sankey:${model.nodes.map((n) => n.stage).join(',')}`);

  if (model.nodes.length === 0 || total === 0) {
    return (
      <div className="funnel-sankey funnel-sankey--empty muted fs-12" role="status">
        Pipeline funnel appears once the gold tables are populated.
      </div>
    );
  }

  const go = (node: (typeof model.nodes)[number]) =>
    navigate(leadQueueHrefForFunnelStage({ stage: node.stage, stage_order: node.stageOrder }, leadParams));
  const onKey = (node: (typeof model.nodes)[number]) => (e: ReactKeyboardEvent<SVGGElement>) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      go(node);
    }
  };

  const chart = (
    <svg
      className={`funnel-sankey${animate ? ' funnel-sankey--enter' : ''}`}
      viewBox={`0 0 ${model.viewWidth} ${model.viewHeight}`}
      role="group"
      aria-label={
        'Activation funnel — each stage is its share of the addressable population, not a conversion '
        + 'from the stage before it; only Approved to Actioned is nested. Each stage links to its '
        + `borrowers in the lead queue.${model.notToScale ? ' Ribbon thickness is not to scale for the smaller stages.' : ''}`
      }
      preserveAspectRatio="xMidYMid meet"
    >
      <defs>
        <linearGradient id={`fs-${gradientId}`} x1="0" x2="1" y1="0" y2="0">
          <stop offset="0%" stopColor="var(--accent-data)" stopOpacity="0.5" />
          <stop offset="100%" stopColor="var(--seg-itm)" stopOpacity="0.28" />
        </linearGradient>
      </defs>
      {model.ribbons.map((ribbon, index) => (
        <path
          key={`${ribbon.fromOrder}-${ribbon.toOrder}`}
          className="funnel-sankey__ribbon"
          d={ribbon.path}
          fill={`url(#fs-${gradientId})`}
          style={{ '--ribbon-i': index } as CSSProperties}
        />
      ))}
      {model.nodes.map((node) => {
        const stageLabel = funnelStageDisplayLabel({ stage: node.stage, stage_order: node.stageOrder });
        // Two kinds of ratio, never confused: every stage's share of the
        // addressable superset, and a conversion ONLY where the model says
        // SQL nests the stage inside a parent (Approved → Actioned).
        const sharePct = formatConversionPct(node.shareOfAddressable);
        const share = sharePct ? `${sharePct} of addressable` : null;
        const convPct = node.nestedIn ? formatConversionPct(node.conversion) : null;
        const conv = convPct && node.nestedIn
          ? `${convPct} of ${funnelStageDisplayLabel(node.nestedIn).toLowerCase()}`
          : null;
        // Stack above the bar, nearest line last: count, share, conversion.
        const lines = [share, conv].filter((line): line is string => line !== null);
        return (
          <g
            key={node.stageOrder}
            className="funnel-sankey__node"
            role="link"
            tabIndex={0}
            aria-label={`${stageLabel}: ${[`${formatCount(node.count)} borrowers`, ...lines].join(', ')}. Open in lead queue.`}
            onClick={() => go(node)}
            onKeyDown={onKey(node)}
          >
            <rect
              className="funnel-sankey__bar"
              x={node.xCenter - 8}
              y={node.yTop}
              width={16}
              height={node.height}
              rx={3}
            />
            <text
              className="funnel-sankey__count"
              x={node.xCenter}
              y={node.yTop - 18 - Math.max(0, lines.length - 1) * 13}
              textAnchor="middle"
            >
              {formatCompact(node.count)}
            </text>
            {lines.map((line, idx) => (
              <text
                key={line}
                className="funnel-sankey__conv"
                data-ratio={line === share ? 'share-of-addressable' : 'nested-conversion'}
                x={node.xCenter}
                y={node.yTop - 5 - (lines.length - 1 - idx) * 13}
                textAnchor="middle"
              >
                {line}
              </text>
            ))}
            <text className="funnel-sankey__label" x={node.xCenter} y={node.yBottom + 18} textAnchor="middle">
              {stageLabel}
            </text>
          </g>
        );
      })}
    </svg>
  );

  return (
    <>
      {chart}
      {model.notToScale && (
        <p className="analytics-panel-note" data-testid="funnel-sankey-scale-note">
          Not to scale: stages too small to see in proportion are drawn at a minimum ribbon thickness.
          The printed counts and shares of addressable are exact.
        </p>
      )}
    </>
  );
}

export function DailyEvidenceLineChart({ rows }: { rows: DailyEvidenceTotal[] }) {
  const clipId = useId();
  const chart = useMemo(() => {
    if (rows.length === 0) return null;
    const yTicks = niceTicks(0, Math.max(0, ...rows.map((row) => row.event_count)), 5, { integer: true });
    const maxY = yTicks[yTicks.length - 1];
    const plotY = (value: number) => 92 - (Math.max(0, Math.min(1, value / maxY)) * 84);
    const points = rows.map((row, idx) => {
      const px = rows.length === 1 ? 50 : (idx / (rows.length - 1)) * 100;
      const py = plotY(row.event_count);
      return `${fixedAttr(px)},${fixedAttr(py)}`;
    }).join(' ');
    return {
      maxY,
      points,
      yTicks,
      xTicks: categoricalTickIndexes(rows.length),
      plotY,
    };
  }, [rows]);

  if (!chart) return <div className="analytics-empty">No daily evidence returned.</div>;
  return (
    <div className="analytics-chart" role="img" aria-label="Evidence events by date">
      <div className="analytics-chart__plot">
        <div className="analytics-chart__y-ticks" aria-hidden="true">
          {[...chart.yTicks].reverse().map((tick) => (
            <span
              key={tick}
              className="analytics-chart__tick analytics-chart__tick--y"
              style={{ '--tick-pos': `${chart.plotY(tick)}%` } as CSSProperties}
            >
              {formatAxisTick(tick, true)}
            </span>
          ))}
        </div>
        <div className="analytics-chart__canvas">
          <svg viewBox="0 0 100 100" preserveAspectRatio="none" className="analytics-line-chart">
            <defs>
              <clipPath id={clipId}>
                <rect x="0" y="0" width="100" height="100" />
              </clipPath>
            </defs>
            {chart.yTicks.map((tick) => (
              <line
                key={`y-${tick}`}
                x1="0"
                x2="100"
                y1={chart.plotY(tick)}
                y2={chart.plotY(tick)}
                className="analytics-chart__grid"
                vectorEffect="non-scaling-stroke"
              />
            ))}
            <polyline points={chart.points} clipPath={`url(#${clipId})`} vectorEffect="non-scaling-stroke" />
          </svg>
          <div className="analytics-chart__x-ticks" aria-hidden="true">
            {chart.xTicks.map((idx) => (
              <span
                key={rows[idx].event_date}
                className="analytics-chart__tick analytics-chart__tick--x"
                style={{ '--tick-pos': `${rows.length === 1 ? 50 : (idx / (rows.length - 1)) * 100}%` } as CSSProperties}
              >
                {formatShortDate(rows[idx].event_date)}
              </span>
            ))}
          </div>
        </div>
      </div>
      <div className="analytics-chart__axis analytics-chart__axis--x">Event date</div>
      <div className="analytics-chart__axis analytics-chart__axis--y">Events</div>
    </div>
  );
}

/**
 * The table twin of a histogram (dataviz-10): the same bins the chart plots,
 * with the threshold partition as a column. `pastLabel` null: no threshold.
 */
export function HistogramTable({
  model,
  rangeLabel,
  pastLabel,
  formatRange,
}: {
  model: HistogramModel;
  rangeLabel: string;
  pastLabel: string | null;
  formatRange: (start: number) => string;
}) {
  return (
    <DataTable<HistogramModelBin>
      rows={model.bins}
      getKey={(bin) => String(bin.start)}
      columns={[
        { key: 'range', label: rangeLabel, render: (bin) => formatRange(bin.start) },
        { key: 'borrowers', label: 'Borrowers', render: (bin) => formatCount(bin.count) },
        { key: 'past', label: pastLabel ?? 'At or past the screen', render: (bin) => (pastLabel === null ? '—' : bin.past ? 'Yes' : 'No') },
      ]}
    />
  );
}

// The equity × spread scatter moved to analytics.equity-scatter.tsx (S7):
// the overview is server-side density bins and the zoom loads capped real
// points, so the old raw-row ScatterPlot renderer was retired with it.

export function DataTable<T>({
  columns,
  rows,
  getKey,
}: {
  columns: Array<{ key: string; label: string; render: (row: T) => ReactNode }>;
  rows: T[];
  getKey: (row: T, idx: number) => string;
}) {
  if (rows.length === 0) return <div className="analytics-empty">No rows returned.</div>;
  return (
    <div className="analytics-table-wrap">
      <table className="analytics-table">
        <thead>
          <tr>
            {columns.map((column) => <th key={column.key}>{column.label}</th>)}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, idx) => (
            <tr key={getKey(row, idx)}>
              {columns.map((column) => <td key={column.key}>{column.render(row)}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
