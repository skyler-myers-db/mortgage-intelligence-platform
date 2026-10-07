/**
 * Genie chart caps and the honest truncation caption (audit 2026-09-21
 * `genie-06` slice 1). Moved out of GenieAnswerCharts.tsx so the new kit
 * charts (GenieLineChart, GenieBarChart) import it without an import cycle;
 * GenieAnswerCharts re-exports every name, so callers are unchanged.
 */
import { MAX_TABLE_ROWS } from './GenieAnswer.logic';

/** Bars a bar chart draws before it truncates. */
export const MAX_BAR_POINTS = 12;
/** Points a line chart draws before it truncates. */
export const MAX_LINE_POINTS = 24;

/**
 * Honest chart caption (audit 2026-09-21 `genie-06`). The old caption
 * promised "full N rows in the table below" while the table shows at most
 * MAX_TABLE_ROWS. It names both real caps, and it counts the two row sets
 * separately because they differ: the chart drops every row whose measure is
 * null or not numeric, the table keeps them. The chart takes its rows in SQL
 * order, so it shows the FIRST rows, not the "top" ones.
 *
 * `shown` bars/points out of `charted` chartable rows; `tableRows` is the row
 * count of the answer's table. Null when the chart shows every table row.
 * `tableExpanded`: "Show all" put every row in the table below, so the
 * caption stops counting the compact cap (genie-06 item 1).
 */
export function chartTruncationCaption(
  shown: number,
  charted: number,
  tableRows: number,
  unit: 'rows' | 'points',
  tableExpanded = false,
): string | null {
  const uncharted = Math.max(0, tableRows - charted);
  if (shown >= charted && uncharted === 0) return null;
  const chart =
    shown < charted
      ? `Chart shows the first ${shown} of ${charted} charted ${unit}`
      : `Chart shows all ${charted} charted ${unit}`;
  const skipped =
    uncharted > 0 ? ` (${uncharted} row${uncharted === 1 ? ' has' : 's have'} no value to chart)` : '';
  const table = tableExpanded
    ? `the table below shows all ${tableRows} rows`
    : `the table below shows ${Math.min(MAX_TABLE_ROWS, tableRows)} of ${tableRows} rows`;
  return `${chart}${skipped}; ${table}.`;
}
