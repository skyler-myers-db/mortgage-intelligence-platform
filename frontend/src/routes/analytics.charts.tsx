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
import { AsyncStatus } from '../components/ui/AsyncState';
import { Tooltip } from '../components/ui/Tooltip';
import { type UseWarmingUpRetryResult } from '../lib/useWarmingUpRetry';
import { useFirstAppearance } from '../lib/useFirstAppearance';
import { CountChart } from '../components/charts/CountChart';
import { utcDayScale } from '../components/charts/scales';
import { fixedAttr } from '../lib/fixedPrecision';
import { formatCompact, formatCount } from '../lib/formatters';
import type { FunnelStage } from '../types';
import { evidenceDate, evidenceSummary, type HistogramModel, type HistogramModelBin } from './analytics.chart-model';
import {
  buildFunnelSankeyModel,
  categoricalTickIndexes,
  funnelStageDisplayLabel,
  formatConversionPct,
  formatShareOfAddressable,
  formatShortDate,
  leadQueueHrefForFunnelStage,
  pct,
  type DailyEvidenceTotal,
  type LenderFilterParams,
} from './analytics.lib';
import { AnalyticsSkeleton, ANALYTICS_SKELETONS, type AnalyticsSkeletonShape } from './analytics.skeleton';

/**
 * An Analytics panel's four states (2026-09-21 audit states-04, states-03,
 * states-v2). Data keeps its stable-refresh region; a first load reserves the
 * loaded shape; warming and failure render the shared AsyncStatus vocabulary
 * (the calm bannered status under a DegradedBanner, whose recovery refetches
 * these reads; never the transport message); `empty` shows only for a
 * settled, measured zero, never on the previous filters' placeholder rows.
 */
export function LoadState<T>({
  query,
  title,
  skeleton = ANALYTICS_SKELETONS.panel,
  isEmpty,
  empty = null,
  children,
}: {
  query: UseWarmingUpRetryResult<T>;
  /** The panel's name, sentence case; also the failure's subject. */
  title: string;
  /** The loaded view's shape, reserved while it loads (analytics.skeleton.tsx). */
  skeleton?: AnalyticsSkeletonShape;
  /** True for a payload with nothing in it. */
  isEmpty?: (data: T) => boolean;
  empty?: ReactNode;
  children: (data: T) => ReactNode;
}) {
  if (query.data) {
    if (isEmpty?.(query.data) && !query.isPlaceholderData && query.error === null && query.warmingUp === null) {
      return <>{empty}</>;
    }
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
  const status = <AsyncStatus query={query} subject={title} compact />;
  // A failure: AsyncFailure's copy and its one Retry.
  if (query.error) return status;
  // Warming: the block (it steps aside under the banner), the slot kept.
  return (
    <>
      {query.warmingUp ? status : null}
      <AnalyticsSkeleton title={title} shape={skeleton} reserve={Boolean(query.warmingUp)} />
    </>
  );
}

export function SectionHeader({ title, action }: { title: string; action?: ReactNode }) {
  return (
    <div className="section-hdr analytics-section-hdr">
      <h2 className="h-2">{title}</h2>
      {action}
    </div>
  );
}

/**
 * A panel's scope chip. Its optional `detail` (for example the source table)
 * is the design-system Tooltip, never a DOM title (critic-08).
 */
export function ScopeChip({ children, detail }: { children: ReactNode; detail?: string }) {
  const chip = (
    <span className="chip chip--neutral analytics-scope-chip">
      <span className="chip__label">{children}</span>
    </span>
  );
  return detail ? <Tooltip content={detail}>{chip}</Tooltip> : chip;
}

/**
 * A non-zero bar shorter than this share of the largest draws at it, so it
 * stays visible; a zero draws no fill (dataviz-03, 2026-09-21 audit). The
 * scale stays linear, and Bars says "not to scale" whenever it floors one.
 * Linear, final (D-dataviz-geo-a, ruled 2026-09-30): a floored bar is drawn
 * outlined, not filled, and says it is not to scale (deviation:funnel-floored-marks).
 */
export const BAR_FLOOR_PCT = 2;

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
  const barPct = (rowValue: number) => (rowValue > 0 ? Math.max(BAR_FLOOR_PCT, pct(rowValue, max)) : 0);
  // Floored: a non-zero bar drawn longer than its linear share.
  const floored = (rowValue: number) => barPct(rowValue) > pct(rowValue, max);
  const bars = (
    <div className="analytics-bars">
      {rows.map((row) => {
        const rowValue = value(row);
        const isFloored = floored(rowValue);
        const node = (
          <>
            <span className="analytics-bars__label">{label(row)}</span>
            <span className="analytics-bars__track" aria-hidden="true">
              <span
                className={isFloored ? 'analytics-bars__fill analytics-bars__fill--floored' : 'analytics-bars__fill'}
                data-to-scale={isFloored ? 'false' : undefined}
                style={{ '--bar-pct': `${barPct(rowValue)}%` } as CSSProperties}
              />
            </span>
            <span className="analytics-bars__value num">{formatCompact(rowValue)}</span>
            {isFloored && <span className="sr-only">, drawn at minimum length, not to scale</span>}
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
  return rows.some((row) => floored(value(row))) ? (
    <>
      {bars}
      <p className="analytics-panel-note" data-testid="bars-scale-note">
        {`Not to scale: outlined bars are under ${BAR_FLOOR_PCT}% of the largest and are drawn at a minimum length. The printed values are exact.`}
      </p>
    </>
  ) : bars;
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
 * activation stages (addressable, In the Money, high-opportunity, approved,
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
 *
 * Scale (dataviz-03, ruled 2026-09-30): linear, final. A sqrt or log scale
 * would draw sizes that contradict the printed shares; floored stages and
 * their ribbons are hatched and say so. See report 12.4 #7.
 * deviation:funnel-floored-marks (the hatch and the outlined bars).
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

  // A ribbon INTO a floored stage is drawn at that stage's minimum thickness.
  const clampedOrders = new Set(model.nodes.filter((node) => node.clamped).map((node) => node.stageOrder));
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
        + `borrowers in the lead queue.${model.notToScale ? ' Hatched stages and the ribbons into them are not to scale.' : ''}`
      }
      preserveAspectRatio="xMidYMid meet"
    >
      <defs>
        <linearGradient id={`fs-${gradientId}`} x1="0" x2="1" y1="0" y2="0">
          <stop offset="0%" stopColor="var(--accent-data)" stopOpacity="0.5" />
          <stop offset="100%" stopColor="var(--seg-itm)" stopOpacity="0.28" />
        </linearGradient>
        {/* The hatch for a floored stage and the ribbon into it; colours
            come from the classes only (10-native-analytics.css). */}
        <pattern id={`fs-floor-${gradientId}`} patternUnits="userSpaceOnUse" width={5} height={5} patternTransform="rotate(45)">
          <rect className="funnel-sankey__hatch-bg" width={5} height={5} />
          <line className="funnel-sankey__hatch-line" x1={0} y1={0} x2={0} y2={5} />
        </pattern>
        <pattern id={`fs-ribbon-floor-${gradientId}`} patternUnits="userSpaceOnUse" width={5} height={5} patternTransform="rotate(45)">
          <rect className="funnel-sankey__hatch-bg funnel-sankey__hatch-bg--ribbon" width={5} height={5} />
          <line className="funnel-sankey__hatch-line funnel-sankey__hatch-line--ribbon" x1={0} y1={0} x2={0} y2={5} />
        </pattern>
      </defs>
      {model.ribbons.map((ribbon, index) => {
        const into = clampedOrders.has(ribbon.toOrder);
        return (
          <path
            key={`${ribbon.fromOrder}-${ribbon.toOrder}`}
            className={into ? 'funnel-sankey__ribbon funnel-sankey__ribbon--floored' : 'funnel-sankey__ribbon'}
            d={ribbon.path}
            fill={`url(#fs-${gradientId})`}
            data-to-scale={into ? 'false' : undefined}
            style={{
              '--ribbon-i': index,
              ...(into ? { '--floor-fill': `url(#fs-ribbon-floor-${gradientId})` } : {}),
            } as CSSProperties}
          />
        );
      })}
      {model.nodes.map((node) => {
        const stageLabel = funnelStageDisplayLabel({ stage: node.stage, stage_order: node.stageOrder });
        // Two kinds of ratio, never confused: every stage's share of the
        // addressable superset, and a conversion ONLY where the model says
        // SQL nests the stage inside a parent (Approved → Actioned).
        const share = node.shareOfAddressable === null
          ? null
          : formatShareOfAddressable(node.count, model.addressableCount);
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
            aria-label={`${stageLabel}: ${[`${formatCount(node.count)} borrowers`, ...lines].join(', ')}${node.clamped ? ' (drawn at minimum size, not to scale)' : ''}. Open in lead queue.`}
            onClick={() => go(node)}
            onKeyDown={onKey(node)}
          >
            <rect
              className={node.clamped ? 'funnel-sankey__bar funnel-sankey__bar--floored' : 'funnel-sankey__bar'}
              data-to-scale={node.clamped ? 'false' : undefined}
              style={node.clamped ? ({ '--floor-fill': `url(#fs-floor-${gradientId})` } as CSSProperties) : undefined}
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
          Not to scale: hatched stages are too small to draw in proportion and are shown at a minimum
          thickness. The printed counts and shares of addressable are exact.
        </p>
      )}
    </>
  );
}

/**
 * Evidence events per day on the chart kit (dataviz-v2, stack-06, dataviz-10).
 * Each day sits at its UTC-day offset along [first, last], never at its index,
 * so a day the rows skip keeps its width instead of compressing the axis. The
 * kit's CountChart gives the 1-2-5 count axis, the edge-anchored first and
 * last date labels, the hover and keyboard readout, and the frame with the
 * summary and the date/events table.
 */
export function DailyEvidenceLineChart({ rows }: { rows: DailyEvidenceTotal[] }) {
  const ordered = [...rows].sort((a, b) => a.event_date.localeCompare(b.event_date));
  const summary = evidenceSummary(ordered);
  if (summary === null) return <div className="analytics-empty">No daily evidence returned.</div>;
  const x = utcDayScale(ordered[0].event_date, ordered[ordered.length - 1].event_date);
  const points = ordered.map((row) => ({ x: x(row.event_date), count: row.event_count, label: formatShortDate(row.event_date) }));
  return (
    <CountChart
      title="Evidence Events Per Day"
      summary={summary}
      table={
        <DataTable<DailyEvidenceTotal>
          rows={ordered}
          getKey={(row) => row.event_date}
          columns={[
            { key: 'date', label: 'Date', render: (row) => evidenceDate(row.event_date) },
            { key: 'events', label: 'Events', render: (row) => formatCount(row.event_count) },
          ]}
        />
      }
      points={points}
      xTicks={categoricalTickIndexes(points.length).map((idx) => points[idx])}
      xLabel="Event date"
      yLabel="Events"
      svgClassName="analytics-line-chart"
      marks={(y) => (
        <polyline
          points={points.map((point) => `${fixedAttr(point.x)},${fixedAttr(y(point.count))}`).join(' ')}
          vectorEffect="non-scaling-stroke"
        />
      )}
    />
  );
}

/**
 * The table twin of a histogram (dataviz-10): the same bins the chart plots,
 * with the threshold partition as a column: Yes from the threshold's bin edge
 * up, Partly for a bin the threshold falls inside. `pastLabel` null: there is
 * no threshold, so there is no column.
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
        ...(pastLabel === null
          ? []
          : [{ key: 'past', label: pastLabel, render: (bin: HistogramModelBin) => (bin.past ? 'Yes' : bin.straddles ? 'Partly' : 'No') }]),
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
