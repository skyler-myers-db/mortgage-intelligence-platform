/**
 * Histogram (2026-09-21 audit dataviz-06, visual-08 item 2): binned counts
 * drawn as bins, not a polyline that implies values between them, with the
 * business threshold drawn where it applies.
 *
 *  - One <rect> per bin, from its start to start + binWidth, on a linear x
 *    over [first start, last start + binWidth] in a 0-100 viewBox.
 *  - y plots over [0, the last 1-2-5 tick], so the top gridline is the domain
 *    maximum.
 *  - A bin whose LOWER edge is at or above the threshold is "past" and takes
 *    the data ink; a bin straddling a non-aligned threshold is not. The rule
 *    is drawn at the exact threshold, with an HTML label the caller supplies.
 *  - No threshold: no rule and no partition, every bin in data ink; the
 *    caller's notice says why.
 *
 * The axis chrome is the shared `.analytics-chart__*` (so the y-tick formats
 * other specs pin stay); the frame, tooltip and keyboard cursor are the kit's.
 */
import type { CSSProperties, ReactNode } from 'react';
import { niceTicks, niceTicksWithin } from '../../lib/chartTicks';
import { fixedAttr } from '../../lib/fixedPrecision';
import { formatCompact, formatCount } from '../../lib/formatters';
import { ChartFrame } from './ChartFrame';
import { ChartTooltip } from './ChartTooltip';
import { linearScale, tickEdgeClass } from './scales';
import { useChartCursor } from './useChartCursor';

export interface HistogramBin {
  start: number;
  count: number;
}

/** Lower edge at or above the threshold. A straddling bin is not past. */
export function binIsPast(start: number, threshold: number | null): boolean {
  return threshold !== null && start >= threshold;
}

/**
 * Plot rows, top-down: the zero baseline at 96% and the domain maximum at 6%,
 * so the top gridline's label and the baseline stroke stay inside the plot.
 */
export const PLOT_Y_RANGE: readonly [number, number] = [96, 6];

/** A rule label past 70% of the plot hangs to the left of its rule. */
const RULE_LABEL_FLIP_PCT = 70;

export function Histogram({
  title,
  summary,
  table,
  notice = null,
  bins,
  binWidth,
  threshold,
  ruleLabel = null,
  xLabel,
  yLabel,
  formatRange,
}: {
  title: string;
  summary: string;
  table: ReactNode;
  notice?: ReactNode;
  bins: ReadonlyArray<HistogramBin>;
  binWidth: number;
  threshold: number | null;
  ruleLabel?: string | null;
  xLabel: string;
  yLabel: string;
  /** A bin's range for the readout, from its start. */
  formatRange: (start: number) => string;
}) {
  const ordered = [...bins].sort((a, b) => a.start - b.start);
  const d0 = ordered.length > 0 ? ordered[0].start : 0;
  const d1 = ordered.length > 0 ? ordered[ordered.length - 1].start + binWidth : 1;
  const x = linearScale([d0, d1], [0, 100]);
  const yTicks = niceTicks(0, Math.max(0, ...ordered.map((bin) => bin.count)), 5, { integer: true });
  const y = linearScale([0, yTicks[yTicks.length - 1]], PLOT_Y_RANGE);
  const cursor = useChartCursor(ordered.length, ordered.map((bin) => x(bin.start + binWidth / 2)));

  if (ordered.length === 0) return <div className="analytics-empty">No distribution returned.</div>;

  const unit = yLabel.toLowerCase();
  const describe = (bin: HistogramBin) => `${formatRange(bin.start)}: ${formatCount(bin.count)} ${unit}`;
  const ruleX = threshold !== null && threshold >= d0 && threshold <= d1 ? x(threshold) : null;
  const active = cursor.index === null ? null : ordered[cursor.index];
  const live = cursor.liveIndex === null ? null : ordered[cursor.liveIndex];
  const baseline = y(0);

  return (
    <ChartFrame
      title={title}
      summary={summary}
      table={table}
      notice={notice}
      liveText={live ? describe(live) : ''}
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
            <svg viewBox="0 0 100 100" preserveAspectRatio="none" className="chart-hist" aria-hidden="true">
              {yTicks.map((tick) => (
                <line
                  key={`y-${tick}`}
                  x1="0"
                  x2="100"
                  y1={fixedAttr(y(tick))}
                  y2={fixedAttr(y(tick))}
                  className="analytics-chart__grid"
                  vectorEffect="non-scaling-stroke"
                />
              ))}
              {ordered.map((bin) => {
                const left = x(bin.start);
                const top = y(bin.count);
                const inked = threshold === null || binIsPast(bin.start, threshold);
                return (
                  <rect
                    key={bin.start}
                    className={`chart-hist__bar${inked ? ' chart-hist__bar--past' : ''}`}
                    x={fixedAttr(left)}
                    y={fixedAttr(top)}
                    width={fixedAttr(x(bin.start + binWidth) - left)}
                    height={fixedAttr(baseline - top)}
                    vectorEffect="non-scaling-stroke"
                  />
                );
              })}
              {ruleX !== null && (
                <line
                  className="chart-hist__rule"
                  x1={fixedAttr(ruleX)}
                  x2={fixedAttr(ruleX)}
                  y1="0"
                  y2="100"
                  vectorEffect="non-scaling-stroke"
                />
              )}
            </svg>
            {ruleX !== null && ruleLabel && (
              <span
                className={`chart-hist__rule-label${ruleX > RULE_LABEL_FLIP_PCT ? ' chart-hist__rule-label--end' : ''}`}
                style={{ '--rule-x': `${ruleX}%` } as CSSProperties}
              >
                {ruleLabel}
              </span>
            )}
            <ChartTooltip
              point={active ? {
                x: x(active.start + binWidth / 2),
                y: y(active.count),
                label: formatRange(active.start),
                value: `${formatCount(active.count)} ${unit}`,
              } : null}
              surfaceProps={cursor.surfaceProps}
            />
            <div className="analytics-chart__x-ticks" aria-hidden="true">
              {niceTicksWithin(d0, d1, 5, { integer: true }).map((tick) => (
                <span
                  key={tick}
                  className={`analytics-chart__tick analytics-chart__tick--x${tickEdgeClass(x(tick))}`}
                  style={{ '--tick-pos': `${x(tick)}%` } as CSSProperties}
                >
                  {formatCount(tick)}
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
