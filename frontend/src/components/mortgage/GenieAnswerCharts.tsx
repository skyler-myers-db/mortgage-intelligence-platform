import { useEffect, useState } from 'react';
import { Link } from 'react-router';
import {
  coerceMeasure,
  coerceNumber,
  findMeasureColumn,
  formatCell,
  formatGenieNumber,
  humanizeKey,
  isIdentifierColumn,
  level,
  normalizeState,
} from './GenieAnswer.logic';
import { loadUsaStateMap } from './USStateMapData';
import type { UsaSvgMap } from './USChoroplethMap.utils';
import { offerDisplayLabel } from '../../lib/offerLanguage';
import { safeSegmentName } from '../../lib/segmentMetadata';
import { borrower360Path } from '../../lib/genieCellLinks';

// The line and bar charts live on the chart kit in their own modules
// (dataviz-05 / stack-06); every name stays importable from here.
export { GenieBarChart } from './GenieBarChart';
export { GenieLineChart } from './GenieLineChart';
export { chartTruncationCaption, MAX_BAR_POINTS, MAX_LINE_POINTS } from './GenieChartCaption';

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
    // Shading is a measure axis too — an identifier column must never drive it.
    const value = coerceMeasure(row[y], y);
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
              aria-label={`${location.name}: ${formatGenieNumber(value)}`}
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
              {state} {formatGenieNumber(value)}
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
              {score !== null && <div className="genie-board__value">{formatGenieNumber(score)}</div>}
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
  // The measure must come from a column that can honestly BE a measure. The
  // old fallback took the first coercible column, and `coerceNumber` happily
  // parses a digit string — so a result set with no numeric column rendered
  // the ZIP ("75,040") as the board's headline metric (2026-08-07 audit C2).
  // An explicit viz.y gets the same guard: the backend's own _value_column
  // filters identifiers, so an identifier arriving here is already wrong.
  const explicitValue = y && !isIdentifierColumn(y) ? y : null;
  const value = explicitValue ?? findMeasureColumn(rows[0]);
  if (!label || rows.length === 0) return null;
  return (
    <div className="genie-board">
      <div className="eyebrow genie-chart__title">Strategy board</div>
      <div className="genie-board__grid">
        {rows.slice(0, 6).map((row, i) => {
          const title = formatCell(label, row[label]);
          // No measure column → the card carries its label and context only.
          // Better an unnumbered card than a confident wrong number.
          const metric = value ? coerceMeasure(row[value], value) : null;
          const offer = strategyOfferLabel(row);
          const segment = strategySegmentLabel(row.segment ?? row.segment_code ?? row.top_segment);
          const meta = [segment, offer].filter(Boolean).map(String).join(' · ')
            || (value ? humanizeKey(value) : '');
          return (
            <div key={`${title}-${i}`} className="genie-board__card">
              <div className="genie-board__title">{title}</div>
              {meta && <div className="genie-board__meta">{meta}</div>}
              {metric !== null && value && (
                <div className="genie-board__value">{formatCell(value, metric)}</div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
