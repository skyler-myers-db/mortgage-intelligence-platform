import { useId, useMemo, useState, type CSSProperties, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { Icon } from '../components/Icon';
import { PageShell } from '../components/layout/PageShell';
import { KpiCard } from '../components/mortgage/KpiCard';
import { WarmingUpBlock } from '../components/ui/WarmingUpBlock';
import { api, type LeadFunnelStage } from '../lib/api';
import { queryKeys } from '../lib/queryKeys';
import { useWarmingUpRetry, type UseWarmingUpRetryResult } from '../lib/useWarmingUpRetry';
import type {
  EconomicsAnalyticsResponse,
  EvidenceDailyRow,
  EquitySpreadPoint,
  EvidenceBySignalRow,
  ExecutiveAnalyticsResponse,
  FunnelStage,
  GeographyAnalyticsResponse,
  RateSpreadBucket,
  ScoreBucket,
  SegmentAnalyticsResponse,
  SegmentByStateRow,
  SegmentMetricRow,
  SignalAnalyticsResponse,
  StateAvmValueRow,
  StateOpportunityRow,
  TopBorrowerAnalyticsRow,
  TopSegmentByStateRow,
  TopZipOpportunityRow,
} from '../types';

type AnalyticsTab = 'executive' | 'geography' | 'economics' | 'segments' | 'signals';

const TABS: Array<{ id: AnalyticsTab; label: string; icon: 'flow' | 'map' | 'money' | 'layers' | 'audit' }> = [
  { id: 'executive', label: 'Executive', icon: 'flow' },
  { id: 'geography', label: 'Geography', icon: 'map' },
  { id: 'economics', label: 'Economics', icon: 'money' },
  { id: 'segments', label: 'Segments', icon: 'layers' },
  { id: 'signals', label: 'Signals', icon: 'audit' },
];

const COMPACT_FORMAT = new Intl.NumberFormat('en-US', {
  notation: 'compact',
  maximumFractionDigits: 2,
});
const MAX_SCATTER_POINTS = 1_200;
const SCATTER_SPREAD_BUCKET_BPS = 25;

type PreparedScatterPoint = {
  row: EquitySpreadPoint;
  xPct: number;
  yPct: number;
};

function fmt(n: number | null | undefined): string {
  if (n === null || n === undefined) return '—';
  return COMPACT_FORMAT.format(n);
}

function fmtCurrency(n: number | null | undefined): string {
  if (n === null || n === undefined) return '—';
  return `$${COMPACT_FORMAT.format(n)}`;
}

function borrowerDisplay(row: Pick<TopBorrowerAnalyticsRow, 'display_name' | 'borrower_id'>): string {
  return `${row.display_name} · ${row.borrower_id.slice(-4)}`;
}

function pct(n: number, total: number): number {
  if (!Number.isFinite(n) || !Number.isFinite(total) || total <= 0) return 0;
  return Math.max(0, Math.min(100, (n / total) * 100));
}

export function leadQueueHref(params: Record<string, string | number | null | undefined>): string {
  const qs = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== null && value !== undefined && value !== '') qs.set(key, String(value));
  }
  const encoded = qs.toString();
  return encoded ? `/lead-queue?${encoded}` : '/lead-queue';
}

export function leadQueueHrefForFunnelStage(row: Pick<FunnelStage, 'stage' | 'stage_order'>): string {
  const byOrder: Record<number, LeadFunnelStage> = {
    1: 'addressable',
    2: 'in_the_money',
    3: 'high_opportunity',
    4: 'offer_recommended',
    5: 'approved',
    6: 'actioned',
  };
  const byLabel: Record<string, LeadFunnelStage> = {
    addressable: 'addressable',
    'in the money': 'in_the_money',
    'high opportunity': 'high_opportunity',
    'offer recommended': 'offer_recommended',
    approved: 'approved',
    actioned: 'actioned',
  };
  const stage = byOrder[row.stage_order] ?? byLabel[row.stage.trim().toLowerCase()];
  return leadQueueHref({ funnel_stage: stage ?? 'addressable' });
}

function makeTicks(min: number, max: number, count = 5): number[] {
  if (!Number.isFinite(min) || !Number.isFinite(max)) return [];
  if (max === min) return [min];
  const ticks: number[] = [];
  const steps = Math.max(1, count - 1);
  for (let i = 0; i <= steps; i += 1) {
    ticks.push(min + ((max - min) * i) / steps);
  }
  return ticks;
}

function formatAxisTick(value: number, compact = false): string {
  if (!Number.isFinite(value)) return '';
  const rounded = Math.round(value);
  return compact ? fmt(rounded) : rounded.toLocaleString();
}

function formatShortDate(value: string): string {
  const [year, month, day] = value.split('-').map((part) => Number(part));
  if (!year || !month || !day) return value;
  return new Intl.DateTimeFormat('en-US', {
    month: 'short',
    day: 'numeric',
    timeZone: 'UTC',
  }).format(new Date(Date.UTC(year, month - 1, day)));
}

type DailyEvidenceTotal = {
  event_date: string;
  event_count: number;
};

export function buildDailyEvidenceTotals(rows: EvidenceDailyRow[]): DailyEvidenceTotal[] {
  const byDate = new Map<string, number>();
  for (const row of rows) {
    byDate.set(row.event_date, (byDate.get(row.event_date) ?? 0) + row.event_count);
  }
  return [...byDate.entries()]
    .map(([event_date, event_count]) => ({ event_date, event_count }))
    .sort((a, b) => a.event_date.localeCompare(b.event_date));
}

function categoricalTickIndexes(length: number, maxTicks = 6): number[] {
  if (length <= 0) return [];
  if (length <= maxTicks) return Array.from({ length }, (_, idx) => idx);
  const step = Math.ceil((length - 1) / (maxTicks - 1));
  const indexes = new Set<number>([0, length - 1]);
  for (let idx = step; idx < length - 1; idx += step) indexes.add(idx);
  return [...indexes].sort((a, b) => a - b);
}

function segmentClass(value: string): string {
  const text = value.toLowerCase();
  if (text.includes('equity')) return 'analytics-segment--equity';
  if (text.includes('money') || text === 'itm') return 'analytics-segment--itm';
  if (text.includes('investor')) return 'analytics-segment--investor';
  if (text.includes('listed')) return 'analytics-segment--listed';
  if (text.includes('permit')) return 'analytics-segment--permit';
  if (text.includes('retention')) return 'analytics-segment--retention';
  return 'analytics-segment--none';
}

export function compactScatterRows(rows: EquitySpreadPoint[], limit = MAX_SCATTER_POINTS): EquitySpreadPoint[] {
  const byBucket = new Map<string, EquitySpreadPoint>();
  const ordered = [...rows].sort((a, b) => (
    b.opportunity_score - a.opportunity_score || a.borrower_id.localeCompare(b.borrower_id)
  ));
  for (const row of ordered) {
    const key = [
      Math.round(row.equity_pct),
      Math.round(row.rate_spread_bps / SCATTER_SPREAD_BUCKET_BPS) * SCATTER_SPREAD_BUCKET_BPS,
      row.segment,
    ].join(':');
    if (!byBucket.has(key)) byBucket.set(key, row);
    if (byBucket.size >= limit) break;
  }
  return [...byBucket.values()].sort((a, b) => a.equity_pct - b.equity_pct || a.rate_spread_bps - b.rate_spread_bps);
}

function prepareScatterPoints(rows: EquitySpreadPoint[], minSpread: number, maxSpread: number): PreparedScatterPoint[] {
  const points = compactScatterRows(rows).map((row) => ({
    row,
    xPct: pct(row.equity_pct, 100),
    yPct: 100 - pct(row.rate_spread_bps - minSpread, maxSpread - minSpread),
  }));
  const groups = new Map<string, PreparedScatterPoint[]>();
  for (const point of points) {
    const key = `${point.xPct.toFixed(2)}:${point.yPct.toFixed(2)}`;
    groups.set(key, [...(groups.get(key) ?? []), point]);
  }
  for (const group of groups.values()) {
    if (group.length <= 1) continue;
    const radiusX = Math.min(1.2, 0.35 + group.length * 0.1);
    const radiusY = Math.min(2.8, 0.8 + group.length * 0.22);
    group.forEach((point, idx) => {
      const angle = (Math.PI * 2 * idx) / group.length;
      point.xPct = Math.max(0, Math.min(100, point.xPct + Math.cos(angle) * radiusX));
      point.yPct = Math.max(0, Math.min(100, point.yPct + Math.sin(angle) * radiusY));
    });
  }
  return points;
}

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

function LoadState<T>({
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

function SectionHeader({ title, action }: { title: string; action?: ReactNode }) {
  return (
    <div className="section-hdr analytics-section-hdr">
      <h2 className="h-2">{title}</h2>
      {action}
    </div>
  );
}

function ScopeChip({ children, title }: { children: ReactNode; title?: string }) {
  return (
    <span className="chip chip--neutral analytics-scope-chip" title={title}>
      <span className="chip__label">{children}</span>
    </span>
  );
}

function Bars<T>({
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

function FunnelBars({ stages }: { stages: FunnelStage[] }) {
  return (
    <Bars
      rows={[...stages].sort((a, b) => a.stage_order - b.stage_order)}
      value={(row) => row.borrower_count}
      label={(row) => row.stage}
      href={leadQueueHrefForFunnelStage}
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

function DataTable<T>({
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

function ExecutiveView({ data }: { data: ExecutiveAnalyticsResponse }) {
  return (
    <>
      <div className="kpi-row">
        <KpiCard label="Addressable Borrowers" value={fmt(data.totals.addressable_borrowers)} delta={data.totals.snapshot_date ?? undefined} deltaDir="flat" />
        <KpiCard label="In the Money" value={fmt(data.totals.in_the_money_borrowers)} delta={`${fmt(data.totals.high_opportunity_borrowers)} high score`} deltaDir="up" />
        <KpiCard label="Offers Recommended" value={fmt(data.totals.offer_recommended_borrowers)} delta="Next-best-offer ready" deltaDir="up" />
        <KpiCard label="Approved Outreach" value={fmt(data.totals.approved_borrowers)} delta={`${fmt(data.totals.actioned_borrowers)} actioned`} deltaDir="flat" />
      </div>
      <div className="layoutA-grid analytics-grid">
        <section className="surface">
          <div className="surface__hdr surface__hdr--split">
            <h2 className="h-3">Opportunity Score Distribution</h2>
            <Link className="btn btn--sm" to="/lead-queue">Open queue</Link>
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
            <h2 className="h-3">Pipeline Metrics</h2>
            <ScopeChip>Independent cuts</ScopeChip>
          </div>
          <div className="surface__body">
            <FunnelBars stages={data.stages} />
          </div>
        </section>
      </div>
    </>
  );
}

function GeographyView({ data }: { data: GeographyAnalyticsResponse }) {
  return (
    <>
      <SectionHeader title="Geography" action={<Link className="btn btn--sm" to="/segment-intelligence">Open map</Link>} />
      <div className="layoutA-grid analytics-grid">
        <section className="surface">
          <div className="surface__hdr"><h2 className="h-3">Opportunity by State</h2></div>
          <div className="surface__body">
            <Bars<StateOpportunityRow>
              rows={data.state_opportunities}
              value={(row) => row.in_the_money_borrowers}
              label={(row) => row.state}
              sublabel={(row) => `${fmt(row.borrower_count)} addressable · ${row.mean_opportunity_score} avg score`}
              href={(row) => leadQueueHref({ state: row.state })}
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
              sublabel={(row) => `${fmtCurrency(row.total_equity_usd)} equity`}
              href={(row) => leadQueueHref({ state: row.state })}
            />
          </div>
        </section>
      </div>
      <section className="surface analytics-section">
        <div className="surface__hdr"><h2 className="h-3">Top ZIPs in the Money</h2></div>
        <div className="surface__body">
          <DataTable<TopZipOpportunityRow>
            rows={data.top_zips}
            getKey={(row) => `${row.state}-${row.zip}`}
            columns={[
              { key: 'zip', label: 'ZIP', render: (row) => <Link to={leadQueueHref({ state: row.state, zip: row.zip })}>{row.zip}</Link> },
              { key: 'place', label: 'Market', render: (row) => `${row.city ?? 'Unknown'}, ${row.state}` },
              { key: 'itm', label: 'In the Money', render: (row) => fmt(row.in_the_money_borrowers) },
              { key: 'score', label: 'ITM Avg Score', render: (row) => row.mean_opportunity_score },
              { key: 'spread', label: 'ITM Avg Spread', render: (row) => `${row.mean_rate_spread_bps} bps` },
            ]}
          />
        </div>
      </section>
    </>
  );
}

function EconomicsView({ data }: { data: EconomicsAnalyticsResponse }) {
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
            />
          </div>
        </section>
        <section className="surface">
          <div className="surface__hdr"><h2 className="h-3">Top Borrowers</h2></div>
          <div className="surface__body">
            <DataTable<TopBorrowerAnalyticsRow>
              rows={data.top_borrowers}
              getKey={(row) => row.borrower_id}
              columns={[
                { key: 'borrower', label: 'Borrower', render: (row) => <Link to={`/borrower-360/${row.borrower_id}`}>{borrowerDisplay(row)}</Link> },
                { key: 'score', label: 'Score', render: (row) => row.opportunity_score },
                { key: 'spread', label: 'Spread', render: (row) => `${row.rate_spread_bps} bps` },
                { key: 'offer', label: 'Offer', render: (row) => row.recommended_offer },
              ]}
            />
          </div>
        </section>
      </div>
      <section className="surface analytics-section">
        <div className="surface__hdr"><h2 className="h-3">Equity vs Rate Spread</h2></div>
        <div className="surface__body analytics-chart-panel analytics-chart-panel--scatter">
          <ScatterPlot rows={data.equity_vs_spread} />
        </div>
      </section>
    </>
  );
}

function SegmentsView({ data }: { data: SegmentAnalyticsResponse }) {
  const topStates = data.top_segments_by_state.filter((row) => row.state_rank === 1);
  const scopeLabel = data.scope.label;
  const scopeDescription = data.scope.description;
  return (
    <>
      <section className="surface">
        <div className="surface__hdr surface__hdr--split">
          <h2 className="h-3">Segment Overview</h2>
          <ScopeChip title={scopeDescription}>{scopeLabel}</ScopeChip>
        </div>
        <div className="surface__body">
          <DataTable
            rows={data.overview}
            getKey={(row) => row.segment_code}
            columns={[
              { key: 'segment', label: 'Segment', render: (row) => <Link to={leadQueueHref({ segment_codes: row.segment_code, segment_mode: 'all' })}>{row.name}</Link> },
              { key: 'borrowers', label: 'Borrowers', render: (row) => fmt(row.borrower_count) },
              { key: 'score', label: 'Avg Score', render: (row) => row.mean_opportunity_score },
              { key: 'itm', label: 'In the Money', render: (row) => fmt(row.in_the_money_borrowers) },
              { key: 'approval', label: 'Approval', render: (row) => row.approval_rate === null || row.approval_rate === undefined ? '—' : `${row.approval_rate.toFixed(1)}%` },
            ]}
          />
        </div>
      </section>
      <div className="layoutA-grid analytics-grid">
        <section className="surface">
          <div className="surface__hdr surface__hdr--split">
            <h2 className="h-3">Segment Size</h2>
            <ScopeChip title={scopeDescription}>{scopeLabel}</ScopeChip>
          </div>
          <div className="surface__body">
            <Bars<SegmentMetricRow>
              rows={data.counts}
              value={(row) => row.value}
              label={(row) => row.segment_name}
              href={(row) => leadQueueHref({ segment_codes: row.segment_code, segment_mode: 'all' })}
            />
          </div>
        </section>
        <section className="surface">
          <div className="surface__hdr surface__hdr--split">
            <h2 className="h-3">Mean Opportunity Score</h2>
            <ScopeChip title={scopeDescription}>{scopeLabel}</ScopeChip>
          </div>
          <div className="surface__body">
            <Bars<SegmentMetricRow>
              rows={data.average_scores}
              value={(row) => row.value}
              label={(row) => row.segment_name}
              href={(row) => leadQueueHref({ segment_codes: row.segment_code, segment_mode: 'all' })}
            />
          </div>
        </section>
      </div>
      <section className="surface analytics-section">
        <div className="surface__hdr surface__hdr--split">
          <h2 className="h-3">Leading Segment by State</h2>
          <ScopeChip title={scopeDescription}>{scopeLabel}</ScopeChip>
        </div>
        <div className="surface__body">
          <Bars<TopSegmentByStateRow | SegmentByStateRow>
            rows={topStates}
            value={(row) => row.borrower_count}
            label={(row) => `${row.state} · ${row.segment_name}`}
            href={(row) => leadQueueHref({ state: row.state, segment_codes: row.segment_code, segment_mode: 'all' })}
          />
        </div>
      </section>
    </>
  );
}

function SignalsView({ data }: { data: SignalAnalyticsResponse }) {
  const dailyTotals = useMemo(() => buildDailyEvidenceTotals(data.evidence_daily), [data.evidence_daily]);

  return (
    <>
      <section className="surface">
        <div className="surface__hdr"><h2 className="h-3">Evidence Events Per Day</h2></div>
        <div className="surface__body analytics-chart-panel">
          <DailyEvidenceLineChart rows={dailyTotals} />
        </div>
      </section>
      <section className="surface analytics-section">
        <div className="surface__hdr"><h2 className="h-3">Evidence by Signal Type</h2></div>
        <div className="surface__body">
          <Bars<EvidenceBySignalRow>
            rows={data.evidence_by_signal}
            value={(row) => row.event_count}
            label={(row) => row.signal_type}
            sublabel={(row) => `${row.source_product} · ${row.mean_confidence === null || row.mean_confidence === undefined ? '—' : row.mean_confidence.toFixed(3)} mean confidence`}
          />
        </div>
      </section>
    </>
  );
}

export default function AnalyticsRoute() {
  const [tab, setTab] = useState<AnalyticsTab>('executive');
  const executive = useWarmingUpRetry<ExecutiveAnalyticsResponse>(
    api.analyticsExecutive,
    ['analytics', 'executive'],
    { enabled: tab === 'executive', queryKey: queryKeys.analytics('executive'), staleTime: 60_000 },
  );
  const geography = useWarmingUpRetry<GeographyAnalyticsResponse>(
    api.analyticsGeography,
    ['analytics', 'geography'],
    { enabled: tab === 'geography', queryKey: queryKeys.analytics('geography'), staleTime: 60_000 },
  );
  const economics = useWarmingUpRetry<EconomicsAnalyticsResponse>(
    api.analyticsEconomics,
    ['analytics', 'economics'],
    { enabled: tab === 'economics', queryKey: queryKeys.analytics('economics'), staleTime: 60_000 },
  );
  const segments = useWarmingUpRetry<SegmentAnalyticsResponse>(
    api.analyticsSegments,
    ['analytics', 'segments'],
    { enabled: tab === 'segments', queryKey: queryKeys.analytics('segments'), staleTime: 60_000 },
  );
  const signals = useWarmingUpRetry<SignalAnalyticsResponse>(
    api.analyticsSignals,
    ['analytics', 'signals'],
    { enabled: tab === 'signals', queryKey: queryKeys.analytics('signals'), staleTime: 60_000 },
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

      {tab === 'executive' && (
        <LoadState query={executive} title="Executive analytics">
          {(data) => <ExecutiveView data={data} />}
        </LoadState>
      )}
      {tab === 'geography' && (
        <LoadState query={geography} title="Geography analytics">
          {(data) => <GeographyView data={data} />}
        </LoadState>
      )}
      {tab === 'economics' && (
        <LoadState query={economics} title="Economics analytics">
          {(data) => <EconomicsView data={data} />}
        </LoadState>
      )}
      {tab === 'segments' && (
        <LoadState query={segments} title="Segment analytics">
          {(data) => <SegmentsView data={data} />}
        </LoadState>
      )}
      {tab === 'signals' && (
        <LoadState query={signals} title="Signal analytics">
          {(data) => <SignalsView data={data} />}
        </LoadState>
      )}
    </PageShell>
  );
}
