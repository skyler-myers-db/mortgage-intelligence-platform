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
import { Link, useNavigate } from 'react-router-dom';
import { Icon } from '../components/Icon';
import { WarmingUpBlock } from '../components/ui/WarmingUpBlock';
import { type UseWarmingUpRetryResult } from '../lib/useWarmingUpRetry';
import { useFirstAppearance } from '../lib/useFirstAppearance';
import type {
  EquitySpreadPoint,
  FunnelStage,
  RateSpreadBucket,
  ScoreBucket,
} from '../types';
import {
  buildFunnelSankeyModel,
  categoricalTickIndexes,
  fmt,
  formatAxisTick,
  formatConversionPct,
  formatShortDate,
  leadQueueHrefForFunnelStage,
  makeTicks,
  pct,
  prepareScatterPoints,
  segmentClass,
  type DailyEvidenceTotal,
  type LenderFilterParams,
} from './analytics.lib';

function LoadingPanel({ title }: { title: string }) {
  return (
    <div className="surface">
      <div className="surface__hdr">
        <div className="surface__icon"><Icon name="db" size={14} /></div>
        <h2 className="h-3">{title}</h2>
      </div>
      <div className="surface__body analytics-state" aria-busy="true">
        <span className="skeleton analytics-state__line" />
        <span className="skeleton analytics-state__line analytics-state__line--wide" />
        <span className="skeleton analytics-state__line" />
      </div>
    </div>
  );
}

export function LoadState<T>({
  query,
  title,
  children,
}: {
  query: UseWarmingUpRetryResult<T>;
  title: string;
  children: (data: T) => ReactNode;
}) {
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
  if (!query.data) return <LoadingPanel title={title} />;
  return children(query.data);
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
            <span className="analytics-bars__value num">{fmt(rowValue)}</span>
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
      label={(row) => row.stage}
      href={(row) => leadQueueHrefForFunnelStage(row, leadParams)}
    />
  );
}

/**
 * FunnelSankey (re-audit Buyer-Wow #5) — the value story at a glance: a
 * flowing pipeline funnel (addressable → in-the-money → high-opportunity →
 * offers → approved → actioned) rendered as connected ribbons that narrow
 * with each stage's drop-off. Pure client-side SVG over the SAME
 * FunnelStage[] the Pipeline Metrics bars use — no new data, fully
 * deterministic. Each stage is a keyboard-focusable link to its slice of
 * the lead queue; the ribbons draw in ONCE on first appearance (gated by
 * useFirstAppearance, disabled under prefers-reduced-motion). The exact
 * figures stay in the Pipeline Metrics bars below.
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

  return (
    <svg
      className={`funnel-sankey${animate ? ' funnel-sankey--enter' : ''}`}
      viewBox={`0 0 ${model.viewWidth} ${model.viewHeight}`}
      role="group"
      aria-label="Pipeline funnel — each stage links to its borrowers in the lead queue"
      preserveAspectRatio="xMidYMid meet"
    >
      <defs>
        <linearGradient id={`fs-${gradientId}`} x1="0" x2="1" y1="0" y2="0">
          <stop offset="0%" stopColor="var(--accent)" stopOpacity="0.5" />
          <stop offset="100%" stopColor="var(--seg-itm)" stopOpacity="0.28" />
        </linearGradient>
      </defs>
      {model.ribbons.map((ribbon) => (
        <path
          key={`${ribbon.fromOrder}-${ribbon.toOrder}`}
          className="funnel-sankey__ribbon"
          d={ribbon.path}
          fill={`url(#fs-${gradientId})`}
        />
      ))}
      {model.nodes.map((node) => {
        const conv = formatConversionPct(node.conversion);
        return (
          <g
            key={node.stageOrder}
            className="funnel-sankey__node"
            role="link"
            tabIndex={0}
            aria-label={`${node.stage}: ${fmt(node.count)} borrowers${conv ? `, ${conv} from previous stage` : ''}. Open in lead queue.`}
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
            <text className="funnel-sankey__count" x={node.xCenter} y={node.yTop - 18} textAnchor="middle">
              {fmt(node.count)}
            </text>
            {conv && (
              <text className="funnel-sankey__conv" x={node.xCenter} y={node.yTop - 5} textAnchor="middle">
                {conv}
              </text>
            )}
            <text className="funnel-sankey__label" x={node.xCenter} y={node.yBottom + 18} textAnchor="middle">
              {node.stage}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

export function LineChart({
  rows,
  x,
  y,
  xLabel,
  yLabel,
}: {
  rows: Array<ScoreBucket | RateSpreadBucket>;
  x: (row: ScoreBucket | RateSpreadBucket) => number;
  y: (row: ScoreBucket | RateSpreadBucket) => number;
  xLabel: string;
  yLabel: string;
}) {
  const clipId = useId();
  const chart = useMemo(() => {
    if (rows.length === 0) return null;
    const xs = rows.map(x);
    const ys = rows.map(y);
    const minX = Math.min(...xs);
    const maxX = Math.max(...xs);
    const maxY = Math.max(1, ...ys);
    const plotY = (value: number) => 92 - (Math.max(0, Math.min(1, value / maxY)) * 84);
    const points = rows.map((row) => {
      const px = maxX === minX ? 50 : ((x(row) - minX) / (maxX - minX)) * 100;
      const py = plotY(y(row));
      return `${px.toFixed(2)},${py.toFixed(2)}`;
    }).join(' ');
    return {
      minX,
      maxX,
      maxY,
      points,
      xTicks: makeTicks(minX, maxX),
      yTicks: makeTicks(0, maxY),
      plotY,
    };
  }, [rows, x, y]);

  if (!chart) return <div className="analytics-empty">No distribution returned.</div>;
  return (
    <div className="analytics-chart" role="img" aria-label={`${yLabel} by ${xLabel}`}>
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
            {chart.xTicks.map((tick) => (
              <span
                key={tick}
                className="analytics-chart__tick analytics-chart__tick--x"
                style={{ '--tick-pos': `${pct(tick - chart.minX, chart.maxX - chart.minX)}%` } as CSSProperties}
              >
                {formatAxisTick(tick)}
              </span>
            ))}
          </div>
        </div>
      </div>
      <div className="analytics-chart__axis analytics-chart__axis--x">{xLabel}</div>
      <div className="analytics-chart__axis analytics-chart__axis--y">{yLabel}</div>
    </div>
  );
}

export function DailyEvidenceLineChart({ rows }: { rows: DailyEvidenceTotal[] }) {
  const clipId = useId();
  const chart = useMemo(() => {
    if (rows.length === 0) return null;
    const maxY = Math.max(1, ...rows.map((row) => row.event_count));
    const plotY = (value: number) => 92 - (Math.max(0, Math.min(1, value / maxY)) * 84);
    const points = rows.map((row, idx) => {
      const px = rows.length === 1 ? 50 : (idx / (rows.length - 1)) * 100;
      const py = plotY(row.event_count);
      return `${px.toFixed(2)},${py.toFixed(2)}`;
    }).join(' ');
    return {
      maxY,
      points,
      yTicks: makeTicks(0, maxY),
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

export function ScatterPlot({ rows }: { rows: EquitySpreadPoint[] }) {
  if (rows.length === 0) return <div className="analytics-empty">No borrower points returned.</div>;
  const minSpread = Math.min(-100, ...rows.map((row) => row.rate_spread_bps));
  const maxSpread = Math.max(400, ...rows.map((row) => row.rate_spread_bps));
  const yTicks = makeTicks(minSpread, maxSpread);
  const xTicks = makeTicks(0, 100);
  const points = prepareScatterPoints(rows, minSpread, maxSpread);
  return (
    <div className="analytics-scatter-wrap">
      <div className="analytics-chart__plot">
        <div className="analytics-chart__y-ticks" aria-hidden="true">
          {[...yTicks].reverse().map((tick) => (
            <span
              key={tick}
              className="analytics-chart__tick analytics-chart__tick--y"
              style={{ '--tick-pos': `${100 - pct(tick - minSpread, maxSpread - minSpread)}%` } as CSSProperties}
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
                y1={100 - pct(tick - minSpread, maxSpread - minSpread)}
                y2={100 - pct(tick - minSpread, maxSpread - minSpread)}
                className="analytics-chart__grid"
                vectorEffect="non-scaling-stroke"
              />
            ))}
            {xTicks.map((tick) => (
              <line
                key={`x-${tick}`}
                x1={tick}
                x2={tick}
                y1="0"
                y2="100"
                className="analytics-chart__grid"
                vectorEffect="non-scaling-stroke"
              />
            ))}
            <line x1="0" y1="50" x2="100" y2="50" className="analytics-scatter__guide" vectorEffect="non-scaling-stroke" />
            <line x1="15" y1="0" x2="15" y2="100" className="analytics-scatter__guide" vectorEffect="non-scaling-stroke" />
          </svg>
          <div className="analytics-scatter__points" aria-label="Borrower drilldown points">
            {points.map(({ row, xPct, yPct }) => (
              <Link
                key={row.borrower_id}
                className={`analytics-scatter__dot ${segmentClass(row.segment)}`}
                to={`/borrower-360/${encodeURIComponent(row.borrower_id)}`}
                style={{
                  '--dot-x': `${xPct}%`,
                  '--dot-y': `${yPct}%`,
                } as CSSProperties}
                aria-label={`${row.display_name}: ${row.equity_pct}% equity, ${row.rate_spread_bps} bps spread, ${row.opportunity_score} score`}
                title={`${row.display_name} · ${row.segment} · ${row.state} · ${row.equity_pct}% equity · ${row.rate_spread_bps} bps`}
              />
            ))}
          </div>
          <div className="analytics-chart__x-ticks" aria-hidden="true">
            {xTicks.map((tick) => (
              <span
                key={tick}
                className="analytics-chart__tick analytics-chart__tick--x"
                style={{ '--tick-pos': `${tick}%` } as CSSProperties}
              >
                {formatAxisTick(tick)}
              </span>
            ))}
          </div>
        </div>
      </div>
      <div className="analytics-chart__axis analytics-chart__axis--x">Equity percent</div>
      <div className="analytics-chart__axis analytics-chart__axis--y">Rate spread bps</div>
    </div>
  );
}

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
