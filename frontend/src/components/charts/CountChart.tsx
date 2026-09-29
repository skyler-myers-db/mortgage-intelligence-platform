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
 * Compiled shells (the analytics route budget, wave 4b). Each kit component
 * is a small compiled shell that holds its hooks and passes its props to a
 * plain render function, so the React Compiler memoizes the chart on those
 * inputs as one unit. A kit chart re-renders whole on every cursor move
 * anyway: its caller hands it fresh points and marks, and the y scale is a
 * fresh closure. The per-element cache the compiler emitted for the frame,
 * the tooltip, the histogram and this chart kept only the caption and the
 * toggle between moves, and cost about 0.8 KiB br of the analytics route
 * closure (46.37 compiled per element, 45.61 as shells, on a macOS build).
 * The shells still compile: no 'use no memo', no allowlist entry.
 */
import type { CSSProperties, ReactNode } from 'react';
import { niceTicks } from '../../lib/chartTicks';
import { fixedAttr } from '../../lib/fixedPrecision';
import { formatCompact, formatCount } from '../../lib/formatters';
import { ChartFrame } from './ChartFrame';
import { ChartTooltip } from './ChartTooltip';
import { linearScale, tickEdgeClass, type Scale } from './scales';
import { useChartCursor, type ChartCursor } from './useChartCursor';

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

/** The compiled shell holds the cursor; the chart renders as one unit. */
export function CountChart(props: CountChartProps) {
  const cursor = useChartCursor(props.points.length, props.points.map((point) => point.x));
  return countChart(props, cursor);
}

function countChart(
  { title, summary, table, notice = null, points, xTicks, xLabel, yLabel, svgClassName, marks, overlay = null }: CountChartProps,
  cursor: ChartCursor,
) {
  const yTicks = niceTicks(0, Math.max(0, ...points.map((point) => point.count)), 5, { integer: true });
  const y = linearScale([0, yTicks[yTicks.length - 1]], PLOT_Y_RANGE);
  const reading = (point: CountChartPoint) => `${formatCount(point.count)} ${yLabel.toLowerCase()}`;
  const active = cursor.index === null ? null : points[cursor.index];
  const live = cursor.liveIndex === null ? null : points[cursor.liveIndex];

  return (
    <ChartFrame
      title={title}
      summary={summary}
      table={table}
      notice={notice}
      liveText={live ? `${live.label}: ${reading(live)}` : ''}
      plotProps={cursor.plotProps}
    >
      <div className="analytics-chart">
        <div className="analytics-chart__plot">
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
          <div className="analytics-chart__canvas">
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
            {overlay}
            <ChartTooltip
              point={active ? { x: active.x, y: y(active.count), label: active.label, value: reading(active) } : null}
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
