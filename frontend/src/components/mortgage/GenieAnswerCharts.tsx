import { useEffect, useState } from 'react';
import { Link } from 'react-router';
import {
  coerceNumber,
  formatCell,
  humanizeKey,
  level,
  normalizeState,
  type ChartRow,
} from './GenieAnswer.logic';
import { loadUsaStateMap } from './USStateMapData';
import type { UsaSvgMap } from './USChoroplethMap.utils';
import { offerDisplayLabel } from '../../lib/offerLanguage';
import { safeSegmentName } from '../../lib/segmentMetadata';
import { borrower360Path } from '../../lib/genieCellLinks';

export function strategySegmentLabel(value: unknown): string | null {
  if (value === null || value === undefined || value === '') return null;
  return safeSegmentName(value) ?? 'Unknown segment';
}

function strategyOfferLabel(row: Record<string, unknown>): string | null {
  const code = row.leading_offer_code ?? row.offer_code ?? row.recommended_offer_code;
  const fallback = row.leading_recommended_offer ?? row.recommended_offer ?? row.product_label;
  if (!code && !fallback) return null;
  return offerDisplayLabel(code ? String(code) : null, fallback ? String(fallback) : null);
}

/**
 * Inline horizontal bar chart. Dependency-free SVG so we don't pull
 * a 100KB+ chart lib. Each bar is sized relative to the max value;
 * negative values are clamped to 0 (real Genie data is counts /
 * scores / dollars -- all >= 0). Truncates to 12 bars to stay
 * readable in the Ask Genie surface; the underlying table still
 * renders below for the full data.
 */
export function GenieBarChart({
  data,
  labelCol,
  valueCol,
}: {
  data: ChartRow[];
  labelCol: string;
  valueCol: string;
}) {
  const MAX_BARS = 12;
  const bars = data.slice(0, MAX_BARS);
  const maxV = Math.max(1, ...bars.map((b) => b.value));
  const rowH = 22;
  const labelW = 140;
  const trackW = 240;
  const valueW = 70;
  const totalW = labelW + trackW + 12 + valueW;
  const totalH = bars.length * rowH + 28;
  return (
    <div className="genie-chart">
      <div className="eyebrow genie-chart__title">
        {humanizeKey(valueCol)} by {humanizeKey(labelCol)}
      </div>
      <svg
        width="100%"
        viewBox={`0 0 ${totalW} ${totalH}`}
        role="img"
        aria-label={`Bar chart: ${humanizeKey(valueCol)} by ${humanizeKey(labelCol)}`}
        className="genie-chart__svg"
      >
        {bars.map((b, i) => {
          const y = i * rowH + 10;
          const w = (b.value / maxV) * trackW;
          return (
            <g key={`${b.label}-${i}`}>
              <text
                x={labelW - 8}
                y={y + rowH / 2}
                textAnchor="end"
                dominantBaseline="middle"
                fontSize={11}
                fill="var(--text-2)"
                fontFamily="var(--font-sans)"
              >
                {b.label.length > 22 ? `${b.label.slice(0, 21)}…` : b.label}
              </text>
              <rect
                x={labelW}
                y={y + 4}
                width={trackW}
                height={rowH - 8}
                fill="var(--bg-3)"
                rx={3}
              />
              <rect
                x={labelW}
                y={y + 4}
                width={Math.max(2, w)}
                height={rowH - 8}
                fill="var(--accent)"
                rx={3}
              />
              <text
                x={labelW + trackW + 8}
                y={y + rowH / 2}
                dominantBaseline="middle"
                fontSize={11}
                fill="var(--text-1)"
                fontFamily="var(--font-mono)"
                fontVariant="tabular-nums"
              >
                {Number.isInteger(b.value)
                  ? b.value.toLocaleString()
                  : b.value.toFixed(2)}
              </text>
            </g>
          );
        })}
      </svg>
      {data.length > MAX_BARS && (
        <div className="genie-chart__more">
          chart shows top {MAX_BARS}; full {data.length} rows in the table below
        </div>
      )}
    </div>
  );
}

export function GenieLineChart({ data, labelCol, valueCol }: { data: ChartRow[]; labelCol: string; valueCol: string }) {
  const points = data.slice(0, 24);
  const maxV = Math.max(1, ...points.map((p) => p.value));
  const minV = Math.min(0, ...points.map((p) => p.value));
  const width = 520;
  const height = 180;
  const span = Math.max(1, maxV - minV);
  const path = points
    .map((p, i) => {
      const x = points.length === 1 ? width / 2 : (i / (points.length - 1)) * width;
      const y = height - ((p.value - minV) / span) * height;
      return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(' ');
  return (
    <div className="genie-chart">
      <div className="eyebrow genie-chart__title">
        {humanizeKey(valueCol)} over {humanizeKey(labelCol)}
      </div>
      <svg className="genie-chart__svg" viewBox={`0 0 ${width} ${height + 36}`} role="img" aria-label={`Line chart: ${humanizeKey(valueCol)} over ${humanizeKey(labelCol)}`}>
        <path d={path} className="genie-line__path" />
        {points.map((p, i) => {
          const x = points.length === 1 ? width / 2 : (i / (points.length - 1)) * width;
          const y = height - ((p.value - minV) / span) * height;
          return <circle key={`${p.label}-${i}`} cx={x} cy={y} r="3" className="genie-line__dot" />;
        })}
        {points[0] && <text x="0" y={height + 24} className="genie-line__axis">{points[0].label}</text>}
        {points[points.length - 1] && <text x={width} y={height + 24} textAnchor="end" className="genie-line__axis">{points[points.length - 1].label}</text>}
      </svg>
    </div>
  );
}

export function GenieMapChart({
  rows,
  x,
  y,
}: {
  rows: Array<Record<string, unknown>>;
  x?: string | null;
  y?: string | null;
}) {
  const [usaMap, setUsaMap] = useState<UsaSvgMap | null>(null);
  useEffect(() => {
    let live = true;
    loadUsaStateMap().then((map) => {
      if (live) setUsaMap(map);
    });
    return () => {
      live = false;
    };
  }, []);
  if (!x || !y) return null;
  const values = new Map<string, number>();
  for (const row of rows) {
    const state = normalizeState(row[x]);
    const value = coerceNumber(row[y]);
    if (!state || value === null) continue;
    values.set(state, value);
  }
  if (values.size === 0) return null;
  const maxV = Math.max(...values.values(), 1);
  if (!usaMap) {
    return (
      <div className="genie-map">
        <div className="eyebrow genie-chart__title">
          {humanizeKey(y)} by {humanizeKey(x)}
        </div>
      </div>
    );
  }
  return (
    <div className="genie-map">
      <div className="eyebrow genie-chart__title">
        {humanizeKey(y)} by {humanizeKey(x)}
      </div>
      <svg viewBox={usaMap.viewBox} className="genie-map__svg" role="img" aria-label={`Map: ${humanizeKey(y)} by state`}>
        {usaMap.locations.map((location) => {
          const code = location.id.toUpperCase();
          const value = values.get(code) ?? 0;
          return (
            <path
              key={location.id}
              d={location.path}
              className={`genie-map__region lvl-${level(value, maxV)} ${value > 0 ? 'has-data' : ''}`}
              aria-label={`${location.name}: ${value.toLocaleString()}`}
            />
          );
        })}
      </svg>
      <div className="genie-map__legend">
        {Array.from(values.entries())
          .sort((a, b) => b[1] - a[1])
          .slice(0, 6)
          .map(([state, value]) => (
            <span key={state} className="genie-map__legend-item">
              <span className={`genie-map__dot lvl-${level(value, maxV)}`} />
              {state} {value.toLocaleString()}
            </span>
          ))}
      </div>
    </div>
  );
}

export function GenieBorrowerList({ rows }: { rows: Array<Record<string, unknown>> }) {
  const borrowers = rows
    .filter((r) => typeof r.borrower_id === 'string')
    .slice(0, 10);
  if (borrowers.length === 0) return null;
  return (
    <div className="genie-board">
      <div className="eyebrow genie-chart__title">Borrower drill-down</div>
      <div className="genie-board__grid">
        {borrowers.map((row) => {
          const id = String(row.borrower_id);
          const score = coerceNumber(row.opportunity_score ?? row.score);
          return (
            // Router <Link>, not a raw <a>: the card used to hard-navigate,
            // dropping the SPA state (and the open Genie panel) on every
            // borrower drill-down.
            <Link key={id} className="genie-board__card" to={borrower360Path(id)}>
              <div className="genie-board__title">{id}</div>
              <div className="genie-board__meta">
                {[row.city, row.state, row.zip].filter(Boolean).join(', ') || 'Open borrower evidence'}
              </div>
              {score !== null && <div className="genie-board__value">{score.toLocaleString()}</div>}
            </Link>
          );
        })}
      </div>
    </div>
  );
}

export function GenieStrategyBoard({
  rows,
  x,
  y,
}: {
  rows: Array<Record<string, unknown>>;
  x?: string | null;
  y?: string | null;
}) {
  const label = x ?? Object.keys(rows[0] ?? {}).find((c) => typeof rows[0]?.[c] === 'string');
  const value = y ?? Object.keys(rows[0] ?? {}).find((c) => coerceNumber(rows[0]?.[c]) !== null);
  if (!label || !value || rows.length === 0) return null;
  return (
    <div className="genie-board">
      <div className="eyebrow genie-chart__title">Strategy board</div>
      <div className="genie-board__grid">
        {rows.slice(0, 6).map((row, i) => {
          const title = formatCell(label, row[label]);
          const metric = coerceNumber(row[value]);
          const offer = strategyOfferLabel(row);
          const segment = strategySegmentLabel(row.segment ?? row.segment_code ?? row.top_segment);
          return (
            <div key={`${title}-${i}`} className="genie-board__card">
              <div className="genie-board__title">{title}</div>
              <div className="genie-board__meta">
                {[segment, offer].filter(Boolean).map(String).join(' · ') || humanizeKey(value)}
              </div>
              {metric !== null && <div className="genie-board__value">{metric.toLocaleString()}</div>}
            </div>
          );
        })}
      </div>
    </div>
  );
}
