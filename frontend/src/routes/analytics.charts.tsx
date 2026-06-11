// React Compiler memo caches add enough code here to breach the strict bundle budget.
// Keep this route explicit: chart-scale derivations use targeted useMemo below.
'use no memo';

import { useId, useMemo, type CSSProperties, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { Icon } from '../components/Icon';
import { WarmingUpBlock } from '../components/ui/WarmingUpBlock';
import { type UseWarmingUpRetryResult } from '../lib/useWarmingUpRetry';
import type {
  EquitySpreadPoint,
  FunnelStage,
  RateSpreadBucket,
  ScoreBucket,
} from '../types';
import {
  categoricalTickIndexes,
  fmt,
  formatAxisTick,
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
