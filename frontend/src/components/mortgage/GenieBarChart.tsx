/**
 * Genie bar chart on the chart kit (audit 2026-09-21 `dataviz-05` /
 * `stack-06`, Genie half). deviation:genie-chart-axes-drill
 *
 * The old chart drew bars, labels and values inside a fixed SVG viewBox, so
 * 11px text scaled with the panel and values bypassed formatCell (money lost
 * its "$"). Now it is an HTML list: each row is a label (a drill link when
 * genieCellHref gives one, with the full label as its name and a CSS
 * ellipsis), an aria-hidden track whose bar width is a custom property on
 * the bar leaf itself, and the value through formatCell. A value axis of
 * 1-2-5 nice ticks sits below with gridline overlays; the domain max is the
 * last tick. Every bar prints its value, so the readout is that cell plus a
 * :hover / :focus-within row emphasis, no floating tip. Links open the Lead
 * Queue with the answer's cohort, disclosed below the chart; clicks only.
 */
import type { CSSProperties } from 'react';
import { Link } from 'react-router';
import { ChartFrame } from '../charts/ChartFrame';
import { niceTicks } from '../../lib/chartTicks';
import { formatCompact, formatUsdCompact } from '../../lib/formatters';
import type { GenieAnswerCohort } from '../../lib/genieCellLinks';
import { formatCell, humanizeKey, isMoneyColumn, type ChartRow } from './GenieAnswer.logic';
import { chartTruncationCaption, MAX_BAR_POINTS } from './GenieChartCaption';
import { chartPointHrefs, GENIE_LEAD_QUEUE_DRILL_NOTE, linksLeadQueue } from './GenieChartDrill';
import './GenieAnswerCharts.css';

export function GenieBarChart({
  data,
  labelCol,
  valueCol,
  tableRowCount,
  tableExpanded = false,
  sourceRows = null,
  cellCohort,
  preview = false,
}: {
  data: ChartRow[];
  labelCol: string;
  valueCol: string;
  /** Rows of the answer table below: the chart's own `data` can be shorter. */
  tableRowCount: number;
  /** "Show all" is open: the table below holds every row. */
  tableExpanded?: boolean;
  /** The answer rows the chart was built from; enables the drill. */
  sourceRows?: ReadonlyArray<Record<string, unknown>> | null;
  /** The answer's own filters, carried by every drill link. */
  cellCohort?: GenieAnswerCohort;
  /** Partial research: no links. */
  preview?: boolean;
}) {
  const bars = data.slice(0, MAX_BAR_POINTS);
  if (bars.length === 0) return null;
  const ticks = niceTicks(0, Math.max(0, ...bars.map((bar) => bar.value)), 5);
  const domainMax = ticks[ticks.length - 1] > 0 ? ticks[ticks.length - 1] : 1;
  const pct = (v: number) => Math.min(100, Math.max(0, (v / domainMax) * 100));
  const tick = isMoneyColumn(valueCol) ? formatUsdCompact : formatCompact;
  const hrefs = chartPointHrefs({ rows: sourceRows, chartRows: data, labelCol, valueCol, cohort: cellCohort, preview }).slice(0, bars.length);
  const title = `${humanizeKey(valueCol)} by ${humanizeKey(labelCol)}`;
  const highest = bars.reduce((best, bar) => (bar.value > best.value ? bar : best), bars[0]);
  const lowest = bars.reduce((low, bar) => (bar.value < low.value ? bar : low), bars[0]);
  const summary =
    `${bars.length} bar${bars.length === 1 ? '' : 's'}: highest ${highest.label} at ${formatCell(valueCol, highest.value)}, ` +
    `lowest ${lowest.label} at ${formatCell(valueCol, lowest.value)}.`;
  const caption = chartTruncationCaption(bars.length, data.length, tableRowCount, 'rows', tableExpanded);
  return (
    <div className="genie-chart">
      <div className="eyebrow genie-chart__title">{title}</div>
      <ChartFrame title={title} summary={summary}>
        <div className="genie-bars">
          <ol className="genie-bars__list">
            {bars.map((bar, i) => {
              const href = hrefs[i];
              return (
                <li key={`${bar.label}-${i}`} className="genie-bars__row">
                  <span className="genie-bars__label">
                    {href ? (
                      <Link className="genie-bars__link" to={href}>
                        {bar.label}
                      </Link>
                    ) : (
                      bar.label
                    )}
                  </span>
                  <span className="genie-bars__track" aria-hidden="true">
                    <span className="genie-bars__bar" style={{ '--bar-w': `${pct(bar.value)}%` } as CSSProperties} />
                  </span>
                  <span className="genie-bars__value">{formatCell(valueCol, bar.value)}</span>
                </li>
              );
            })}
          </ol>
          <div className="genie-bars__grid" aria-hidden="true">
            {ticks.map((t) => (
              <span key={t} className="genie-bars__gridline" style={{ '--tick-pos': `${pct(t)}%` } as CSSProperties} />
            ))}
          </div>
          <div className="genie-bars__axis" aria-hidden="true">
            {ticks.map((t) => (
              <span key={t} className="genie-bars__tick" style={{ '--tick-pos': `${pct(t)}%` } as CSSProperties}>
                {tick(t)}
              </span>
            ))}
          </div>
        </div>
      </ChartFrame>
      {linksLeadQueue(hrefs) ? <p className="genie-chart__drill-note">{GENIE_LEAD_QUEUE_DRILL_NOTE}</p> : null}
      {caption ? <div className="genie-chart__more">{caption}</div> : null}
    </div>
  );
}
