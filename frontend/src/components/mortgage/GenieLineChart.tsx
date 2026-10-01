/**
 * Genie line chart on the chart kit (audit 2026-09-21 `dataviz-05` /
 * `stack-06`, Genie half). deviation:genie-chart-axes-drill
 *
 * The old chart was a bare path in a scaling 520x216 viewBox: no y axis, no
 * values, no hover, two x labels, text that grew and shrank with the panel.
 * Now: HTML y ticks on 1-2-5 nice values over [min(0, lowest), highest]
 * (money keeps "$"), gridlines and the path in a 0-100
 * preserveAspectRatio="none" plot (non-scaling strokes), points as aria-hidden
 * HTML marks placed by percent with x mapped into [2, 98] so the edge points
 * are never clipped, up to six edge-anchored x labels, and the kit's readout
 * (pointer and keyboard) printing formatCell, so money keeps its unit.
 * Text is HTML and never scales with the container. The live region speaks
 * keyboard moves only. Drill-through on the line is W5d (lane cut order 3).
 */
import type { CSSProperties } from 'react';
import { ChartFrame } from '../charts/ChartFrame';
import { ChartTooltip } from '../charts/ChartTooltip';
import { PLOT_Y_RANGE } from '../charts/CountChart';
import { linearScale, tickEdgeClass } from '../charts/scales';
import { useChartCursor } from '../charts/useChartCursor';
import { niceTicks } from '../../lib/chartTicks';
import { fixedAttr } from '../../lib/fixedPrecision';
import { formatCompact, formatUsdCompact } from '../../lib/formatters';
import { formatCell, humanizeKey, isMoneyColumn, type ChartRow } from './GenieAnswer.logic';
import { chartTruncationCaption, MAX_LINE_POINTS } from './GenieChartCaption';
import './GenieAnswerCharts.css';

const MAX_X_LABELS = 6;

/** At least three nice y values: a flat or tiny series still gets an axis. */
export function lineYTicks(values: readonly number[]): number[] {
  const ticks = niceTicks(Math.min(0, ...values), Math.max(...values), 5);
  if (ticks.length >= 3) return ticks;
  const step = ticks.length === 2 ? ticks[1] - ticks[0] : 1;
  const base = ticks.length > 0 ? ticks : [0];
  return [...base, ...Array.from({ length: 3 - base.length }, (_, k) => base[base.length - 1] + step * (k + 1))];
}

/** Indices of up to six evenly spaced x labels, the first and last included. */
export function lineXLabelIndices(count: number): number[] {
  if (count <= MAX_X_LABELS) return Array.from({ length: count }, (_, i) => i);
  return Array.from({ length: MAX_X_LABELS }, (_, k) => Math.round((k * (count - 1)) / (MAX_X_LABELS - 1)));
}

function xPct(index: number, count: number): number {
  return count <= 1 ? 50 : 2 + (index / (count - 1)) * 96;
}

export function GenieLineChart({
  data,
  labelCol,
  valueCol,
  tableRowCount,
  tableExpanded = false,
}: {
  data: ChartRow[];
  labelCol: string;
  valueCol: string;
  /** Rows of the answer table below: the chart's own `data` can be shorter. */
  tableRowCount: number;
  /** "Show all" is open: the table below holds every row. */
  tableExpanded?: boolean;
}) {
  const points = data.slice(0, MAX_LINE_POINTS);
  const positions = points.map((_, i) => xPct(i, points.length));
  const cursor = useChartCursor(points.length, positions);
  if (points.length === 0) return null;
  const yTicks = lineYTicks(points.map((point) => point.value));
  const y = linearScale([yTicks[0], yTicks[yTicks.length - 1]], PLOT_Y_RANGE);
  const tick = isMoneyColumn(valueCol) ? formatUsdCompact : formatCompact;
  const value = (v: number) => formatCell(valueCol, v);
  const path = points.map((point, i) => `${i === 0 ? 'M' : 'L'}${fixedAttr(positions[i])},${fixedAttr(y(point.value))}`).join(' ');
  const highest = points.reduce((best, point) => (point.value > best.value ? point : best), points[0]);
  const first = points[0];
  const last = points[points.length - 1];
  const title = `${humanizeKey(valueCol)} over ${humanizeKey(labelCol)}`;
  const summary =
    `${points.length} point${points.length === 1 ? '' : 's'}: first ${first.label} at ${value(first.value)}, ` +
    `last ${last.label} at ${value(last.value)}, highest ${highest.label} at ${value(highest.value)}.`;
  const at = cursor.index;
  const live = cursor.liveIndex === null ? null : points[cursor.liveIndex];
  const caption = chartTruncationCaption(points.length, data.length, tableRowCount, 'points', tableExpanded);
  return (
    <div className="genie-chart">
      <div className="eyebrow genie-chart__title">{title}</div>
      <ChartFrame
        title={title}
        summary={summary}
        liveText={live ? `${live.label}: ${value(live.value)}` : ''}
        plotProps={cursor.plotProps}
      >
        <div className="analytics-chart genie-line">
          <div className="analytics-chart__plot">
            <div className="analytics-chart__y-ticks" aria-hidden="true">
              {[...yTicks].reverse().map((t) => (
                <span key={t} className="analytics-chart__tick analytics-chart__tick--y" style={{ '--tick-pos': `${y(t)}%` } as CSSProperties}>
                  {tick(t)}
                </span>
              ))}
            </div>
            <div className="analytics-chart__canvas">
              <svg viewBox="0 0 100 100" preserveAspectRatio="none" className="genie-line__svg" aria-hidden="true">
                {yTicks.map((t) => (
                  <line key={t} x1="0" x2="100" y1={fixedAttr(y(t))} y2={fixedAttr(y(t))} className="analytics-chart__grid" vectorEffect="non-scaling-stroke" />
                ))}
                <path d={path} className="genie-line__path" vectorEffect="non-scaling-stroke" />
              </svg>
              <div className="genie-line__points" aria-hidden="true">
                {points.map((point, i) => (
                  <span
                    key={`${point.label}-${i}`}
                    className="genie-line__point"
                    style={{ '--point-x': `${positions[i]}%`, '--point-y': `${y(point.value)}%` } as CSSProperties}
                  />
                ))}
              </div>
              <ChartTooltip
                point={at === null ? null : { x: positions[at], y: y(points[at].value), label: points[at].label, value: value(points[at].value) }}
                surfaceProps={cursor.surfaceProps}
              />
              <div className="analytics-chart__x-ticks" aria-hidden="true">
                {lineXLabelIndices(points.length).map((i) => (
                  <span
                    key={i}
                    className={`analytics-chart__tick analytics-chart__tick--x${tickEdgeClass(positions[i] <= 2 ? 0 : positions[i] >= 98 ? 100 : positions[i])}`}
                    style={{ '--tick-pos': `${positions[i]}%` } as CSSProperties}
                  >
                    {points[i].label}
                  </span>
                ))}
              </div>
            </div>
          </div>
          <div className="analytics-chart__axis analytics-chart__axis--x">{humanizeKey(labelCol)}</div>
          <div className="analytics-chart__axis analytics-chart__axis--y">{humanizeKey(valueCol)}</div>
        </div>
      </ChartFrame>
      {caption ? <div className="genie-chart__more">{caption}</div> : null}
    </div>
  );
}
