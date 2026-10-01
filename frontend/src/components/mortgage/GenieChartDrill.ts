/**
 * Drill-through from a Genie chart mark (audit 2026-09-21 `dataviz-05`,
 * Genie half). deviation:genie-chart-axes-drill
 *
 * A chart point's source row is the answer row it was charted from: the
 * chart keeps, in SQL order, exactly the rows whose measure coerces
 * (GenieAnswer.logic chartFromColumns), so the same filter recovers them.
 * When the counts disagree the rows cannot be aligned and NOTHING drills
 * (fail-safe: a link must never open another row's population).
 *
 * Every href is built by genieCellHref with the answer's own cohort, the
 * same links the table cells carry, so a drill opens the population the
 * figure reports; a /lead-queue URL is never hand-built here. Clicks only:
 * no hover or focus prefetch and no data read. A preview (partial research)
 * never drills.
 */
import { genieCellHref, type GenieAnswerCohort } from '../../lib/genieCellLinks';
import { coerceMeasure, type ChartRow } from './GenieAnswer.logic';

/** Shown under a chart whose links open the Lead Queue (map tooltip twin). */
export const GENIE_LEAD_QUEUE_DRILL_NOTE =
  "Links open the Lead Queue with this answer's filters. The queue lists contact-eligible borrowers only, so it can show fewer than the figure here.";

/** Each chart point's source row, or null when they cannot be aligned. */
export function chartSourceRows(
  rows: ReadonlyArray<Record<string, unknown>>,
  valueCol: string,
  chartRows: readonly ChartRow[],
): Array<Record<string, unknown>> | null {
  const source = rows.filter((row) => coerceMeasure(row[valueCol], valueCol) !== null);
  return source.length === chartRows.length ? source : null;
}

export interface ChartDrillInput {
  rows: ReadonlyArray<Record<string, unknown>> | null | undefined;
  chartRows: readonly ChartRow[];
  labelCol: string;
  valueCol: string;
  cohort?: GenieAnswerCohort;
  preview?: boolean;
}

/** One href (or null) per chart point, through genieCellHref only. */
export function chartPointHrefs({ rows, chartRows, labelCol, valueCol, cohort, preview = false }: ChartDrillInput): Array<string | null> {
  const none = chartRows.map(() => null);
  if (preview || !rows) return none;
  const source = chartSourceRows(rows, valueCol, chartRows);
  if (!source) return none;
  return source.map((row) => genieCellHref(labelCol, row[labelCol], cohort, row));
}

/** Whether any link opens the Lead Queue (the disclosure's condition). */
export function linksLeadQueue(hrefs: ReadonlyArray<string | null>): boolean {
  return hrefs.some((href) => typeof href === 'string' && href.startsWith('/lead-queue'));
}
