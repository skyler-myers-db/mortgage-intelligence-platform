/**
 * ChartFrame: the kit's figure (2026-09-21 audit dataviz-07 step 2,
 * dataviz-10). One frame gives every chart
 *
 *  - a one-sentence summary as the <figcaption>, which the focusable plot
 *    names as its description (aria-describedby);
 *  - a "View as table" / "View as chart" toggle whose label names the view it
 *    switches to (a view switch, not a pressed state, so no aria-pressed);
 *  - the plot or the caller's table node (the kit never imports a route
 *    module: callers pass their own DataTable over the rows the chart plots);
 *  - a notice slot for a truncation or scale disclosure;
 *  - one persistent polite live region for the keyboard cursor.
 *
 * The focusable plot is a named group, not role="img": its tooltip layer is
 * aria-hidden, and the summary, the table and the live region carry the
 * values. No heading element: the section around the frame owns the title.
 */
import { useId, useState, type ReactNode } from 'react';
import type { ChartCursorPlotProps } from './useChartCursor';
import './charts.css';

export function ChartFrame({
  title,
  summary,
  table,
  notice = null,
  liveText = '',
  plotProps,
  children,
}: {
  /** The plot's accessible name: the section title it sits under. */
  title: string;
  summary: string;
  table: ReactNode;
  notice?: ReactNode;
  liveText?: string;
  plotProps?: ChartCursorPlotProps;
  children: ReactNode;
}) {
  const summaryId = useId();
  const [asTable, setAsTable] = useState(false);
  return (
    <figure className="chart-frame">
      <figcaption id={summaryId} className="chart-frame__summary">{summary}</figcaption>
      <div className="chart-frame__toolbar">
        <button type="button" className="btn btn--sm chart-frame__toggle" onClick={() => setAsTable((current) => !current)}>
          {asTable ? 'View as chart' : 'View as table'}
        </button>
      </div>
      {asTable ? (
        <div className="chart-frame__table">{table}</div>
      ) : (
        <div className="chart-frame__plot" role="group" aria-label={title} aria-describedby={summaryId} {...plotProps}>
          {children}
        </div>
      )}
      {notice ? <p className="analytics-panel-note chart-frame__notice">{notice}</p> : null}
      <span className="sr-only" aria-live="polite">{asTable ? '' : liveText}</span>
    </figure>
  );
}
