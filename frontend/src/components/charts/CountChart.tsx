/**
 * CountChart: the kit's one renderer for a chart over a count axis
 * (2026-09-21 audit dataviz-07 step 2, stack-06, dataviz-10). The histograms
 * and Evidence Events Per Day share it, so the frame, the keyboard cursor,
 * the tooltip, the 1-2-5 y axis with its gridlines and the x ticks are
 * written once; a caller supplies only its points, x ticks and marks.
 *
 *  - y plots over [0, the last 1-2-5 integer tick], so the top gridline is
 *    the domain maximum, and each y label sits on its own gridline.
 *  - The readout (tooltip on hover or focus, the live region on a keyboard
 *    move) is the point's label and its count in the y axis's unit.
 *  - The first and last x labels are edge-anchored (stack-06).
 *
 * The axis chrome is the shared `.analytics-chart__*`, so the y-tick formats
 * other specs pin stay.
 *
 * The chart compiles under the React Compiler as written. The cursor state
 * lives here, so a pointer or key move re-renders this component, not its
 * caller. Everything drawn with the y scale (the y labels, the gridlines and
 * the caller's marks) is built before the cursor is read and depends only on
 * the points, the marks and the svg class, so the compiler keeps it across
 * cursor moves; the tooltip reads the point's precomputed row instead of the
 * scale. A move re-renders the tooltip, the live text and the frame only.
 */
import type { CSSProperties, ReactNode } from 'react';
import { niceTicks } from '../../lib/chartTicks';
import { fixedAttr } from '../../lib/fixedPrecision';
import { formatCompact, formatCount } from '../../lib/formatters';
import { ChartFrame } from './ChartFrame';
import { ChartTooltip } from './ChartTooltip';
import { linearScale, tickEdgeClass, type Scale } from './scales';
import { useChartCursor } from './useChartCursor';

export interface CountChartPoint {
  /** Percent of the plot width. */
  x: number;
  count: number;
  /** The readout's name for the point: a bin range, a date. */
  label: string;
}

export interface CountChartTick {
  /** Percent of the plot width. */
  x: number;
  label: string;
}

/**
 * Plot rows, top-down: the zero baseline at 96% and the domain maximum at 6%,
 * so the top gridline's label and the baseline stroke stay inside the plot.
 */
export const PLOT_Y_RANGE: readonly [number, number] = [96, 6];

export interface CountChartProps {
  title: string;
  summary: string;
  table: ReactNode;
  notice?: ReactNode;
  points: ReadonlyArray<CountChartPoint>;
  xTicks: ReadonlyArray<CountChartTick>;
  xLabel: string;
  /** The count's name, which the readout lower-cases as its unit ("Borrowers": "900 borrowers"). */
  yLabel: string;
  svgClassName: string;
  /** The data marks, over the gridlines in the 0-100 viewBox; `y` maps a count to percent. */
  marks: (y: Scale) => ReactNode;
  /** HTML over the plot (a threshold rule's label). */
  overlay?: ReactNode;
}

export function CountChart({
  title,
  summary,
  table,
  notice = null,
  points,
  xTicks,
  xLabel,
  yLabel,
  svgClassName,
  marks,
  overlay = null,
}: CountChartProps) {
  const cursor = useChartCursor(points.length, points.map((point) => point.x));
  const yTicks = niceTicks(0, Math.max(0, ...points.map((point) => point.count)), 5, { integer: true });
  const y = linearScale([0, yTicks[yTicks.length - 1]], PLOT_Y_RANGE);
  const pointY = points.map((point) => y(point.count));
  const yAxis = (
    <div className="analytics-chart__y-ticks" aria-hidden="true">
      {[...yTicks].reverse().map((tick) => (
        <span
          key={tick}
          className="analytics-chart__tick analytics-chart__tick--y"
          style={{ '--tick-pos': `${y(tick)}%` } as CSSProperties}
        >
          {formatCompact(tick)}
        </span>
      ))}
    </div>
  );
  const svg = (
    <svg viewBox="0 0 100 100" preserveAspectRatio="none" className={svgClassName} aria-hidden="true">
      {yTicks.map((tick) => (
        <line
          key={tick}
          x1="0"
          x2="100"
          y1={fixedAttr(y(tick))}
          y2={fixedAttr(y(tick))}
          className="analytics-chart__grid"
          vectorEffect="non-scaling-stroke"
        />
      ))}
      {marks(y)}
    </svg>
  );
  // The cursor is read only below this line (see the doc comment above).
  const unit = yLabel.toLowerCase();
  const at = cursor.index;
  const live = cursor.liveIndex === null ? null : points[cursor.liveIndex];

  return (
    <ChartFrame
      title={title}
      summary={summary}
      table={table}
      notice={notice}
      liveText={live ? `${live.label}: ${formatCount(live.count)} ${unit}` : ''}
      plotProps={cursor.plotProps}
    >
      <div className="analytics-chart">
        <div className="analytics-chart__plot">
          {yAxis}
          <div className="analytics-chart__canvas">
            {svg}
            {overlay}
            <ChartTooltip
              point={at === null ? null : {
                x: points[at].x,
                y: pointY[at],
                label: points[at].label,
                value: `${formatCount(points[at].count)} ${unit}`,
              }}
              surfaceProps={cursor.surfaceProps}
            />
            <div className="analytics-chart__x-ticks" aria-hidden="true">
              {xTicks.map((tick) => (
                <span
                  key={tick.x}
                  className={`analytics-chart__tick analytics-chart__tick--x${tickEdgeClass(tick.x)}`}
                  style={{ '--tick-pos': `${tick.x}%` } as CSSProperties}
                >
                  {tick.label}
                </span>
              ))}
            </div>
          </div>
        </div>
        <div className="analytics-chart__axis analytics-chart__axis--x">{xLabel}</div>
        <div className="analytics-chart__axis analytics-chart__axis--y">{yLabel}</div>
      </div>
    </ChartFrame>
  );
}
