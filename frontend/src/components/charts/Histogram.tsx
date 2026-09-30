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
 * The frame, the count axis, the tooltip and the keyboard cursor are the
 * kit's CountChart; a histogram adds only its bins, rule and x ticks.
 */
import { useLayoutEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react';
import { niceTicksWithin } from '../../lib/chartTicks';
import { fixedAttr } from '../../lib/fixedPrecision';
import { formatCount } from '../../lib/formatters';
import { CountChart } from './CountChart';
import { linearScale } from './scales';

export interface HistogramBin {
  start: number;
  count: number;
}

/** Lower edge at or above the threshold. A straddling bin is not past. */
export function binIsPast(start: number, threshold: number | null): boolean {
  return threshold !== null && start >= threshold;
}

/** Before the label is measured, a rule past 70% of the plot hangs it to the left. */
const RULE_LABEL_FLIP_PCT = 70;

type RuleLabelSide = 'start' | 'end';

/**
 * Which side of its rule the label fits on. Right of the rule while its width
 * (plus the --sp-1 gap) fits the room there; otherwise the side with more
 * room. The label is measured, not guessed from the rule's position: at the
 * Console-open column the score rule sits at 68.75% and its "75+ · N
 * borrowers" label is wider than the 31% of plot to its right (and ~6% wider
 * again in Linux Chromium's Geist Mono), which scrolled the surface sideways.
 */
export function ruleLabelSide(ruleX: number, plotWidth: number, labelWidth: number, gap: number): RuleLabelSide {
  const right = (plotWidth * (100 - ruleX)) / 100;
  const left = (plotWidth * ruleX) / 100;
  if (labelWidth + gap <= right) return 'start';
  return left > right ? 'end' : 'start';
}

/** The threshold rule's label, placed on the side of the rule it fits. */
function HistogramRuleLabel({ ruleX, children }: { ruleX: number; children: ReactNode }) {
  const ref = useRef<HTMLSpanElement>(null);
  const [side, setSide] = useState<RuleLabelSide>(ruleX > RULE_LABEL_FLIP_PCT ? 'end' : 'start');
  useLayoutEffect(() => {
    const label = ref.current;
    const plot = label?.parentElement;
    if (!label || !plot) return undefined;
    const place = () => {
      const gap = parseFloat(getComputedStyle(label).getPropertyValue('--sp-1')) || 0;
      setSide(ruleLabelSide(ruleX, plot.clientWidth, label.offsetWidth, gap));
    };
    place();
    if (typeof ResizeObserver === 'undefined') return undefined;
    const observer = new ResizeObserver(place);
    observer.observe(plot);
    return () => observer.disconnect();
  }, [ruleX, children]);
  return (
    <span
      ref={ref}
      className={`chart-hist__rule-label${side === 'end' ? ' chart-hist__rule-label--end' : ''}`}
      style={{ '--rule-x': `${ruleX}%` } as CSSProperties}
    >
      {children}
    </span>
  );
}

export interface HistogramProps {
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
}

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
}: HistogramProps) {
  const ordered = [...bins].sort((a, b) => a.start - b.start);
  if (ordered.length === 0) return <div className="analytics-empty">No distribution returned.</div>;

  const d0 = ordered[0].start;
  const d1 = ordered[ordered.length - 1].start + binWidth;
  const x = linearScale([d0, d1], [0, 100]);
  const ruleX = threshold !== null && threshold >= d0 && threshold <= d1 ? x(threshold) : null;

  return (
    <CountChart
      title={title}
      summary={summary}
      table={table}
      notice={notice}
      points={ordered.map((bin) => ({ x: x(bin.start + binWidth / 2), count: bin.count, label: formatRange(bin.start) }))}
      xTicks={niceTicksWithin(d0, d1, 5, { integer: true }).map((tick) => ({ x: x(tick), label: formatCount(tick) }))}
      xLabel={xLabel}
      yLabel={yLabel}
      svgClassName="chart-hist"
      marks={(y) => (
        <>
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
                height={fixedAttr(y(0) - top)}
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
        </>
      )}
      overlay={ruleX !== null && ruleLabel ? <HistogramRuleLabel ruleX={ruleX}>{ruleLabel}</HistogramRuleLabel> : null}
    />
  );
}
