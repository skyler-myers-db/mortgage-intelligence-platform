import { Link } from 'react-router';
import type {
  GenieAnswer as GenieAnswerShape,
  GenieAnswerSection,
} from '../../types';
import {
  GenieBarChart,
  GenieBorrowerList,
  GenieLineChart,
  GenieMapChart,
  GenieStrategyBoard,
} from './GenieAnswerCharts';
import { MarkdownAnswer } from './GenieAnswer.markdown';
import {
  coerceNumber,
  formatCell,
  humanizeKey,
  isIdentifierColumn,
  MAX_TABLE_COLS,
  MAX_TABLE_ROWS,
  pickPlan,
  type GenieVisualizationPlan,
} from './GenieAnswer.logic';
import { genieCellHref, type GenieAnswerCohort } from '../../lib/genieCellLinks';

/**
 * Deep-research presentation for a Genie answer.
 *
 * A sweep answers one business question with several planned sub-analyses.
 * The single-turn layout showed the stitched prose plus ONE chart built from
 * the last sub-query's rows, which read as "seven questions, one picture".
 * Here each section keeps its own verified rows and its own chart plan, and
 * the verified executive summary leads.
 *
 * `GenieRowsVisual` is the rows→visual block lifted out of GenieAnswer so the
 * top-level answer and every section render through one implementation
 * (chart plan, single-row stat strip, capped table, "+N more rows").
 */

export function GenieRowsVisual({
  rows,
  plan,
  cellCohort,
}: {
  rows: Array<Record<string, unknown>>;
  plan: GenieVisualizationPlan;
  /** The answer's own filters, so a row link opens the population the row
   *  reports rather than every borrower in that geography. */
  cellCohort?: GenieAnswerCohort;
}) {
  const chart = plan.chart;
  const visibleRows = rows.slice(0, MAX_TABLE_ROWS);
  const hiddenRows = Math.max(0, rows.length - MAX_TABLE_ROWS);
  const columns = visibleRows[0] ? Object.keys(visibleRows[0]).slice(0, MAX_TABLE_COLS) : [];
  return (
    <>
      {/* FIX Δ3: chart renders BEFORE the underlying table so the user
          sees the visual summary first; the table stays as the
          authoritative data source below. Only renders when the data
          shape is chartable (1 categorical + 1 numeric column). */}
      {plan.kind === 'strategy_board' && (
        <GenieStrategyBoard rows={rows} x={plan.viz?.x} y={plan.viz?.y} />
      )}
      {plan.kind === 'borrower_list' && <GenieBorrowerList rows={rows} />}
      {plan.kind === 'map' && (
        <GenieMapChart rows={rows} x={plan.viz?.x ?? chart?.labelCol} y={plan.viz?.y ?? chart?.valueCol} />
      )}
      {plan.kind === 'line' && chart && (
        <GenieLineChart data={chart.rows} labelCol={chart.labelCol} valueCol={chart.valueCol} />
      )}
      {(plan.kind === 'bar' || plan.kind === 'funnel' || (!['strategy_board', 'borrower_list', 'map', 'line'].includes(plan.kind) && chart)) && chart && (
        <GenieBarChart
          data={chart.rows}
          labelCol={chart.labelCol}
          valueCol={chart.valueCol}
        />
      )}
      {/* A single METRIC row is a set of headline facts, not a table —
          render it as a stat strip (value-first, KPI style) so answers like
          "count + avg spread + refreshed at" read at a glance. A single
          RECORD row (identifier columns, e.g. a borrower id) keeps the
          table: masked ids as KPI headlines misread, and borrower_list
          plans already render their own list above (QA M6). */}
      {visibleRows.length === 1 &&
      columns.length > 0 &&
      plan.kind === 'none' &&
      columns.every((c) => !isIdentifierColumn(c)) &&
      // Warehouse rows arrive as strings — coerce like the chart layer does.
      columns.some((c) => coerceNumber(visibleRows[0][c]) !== null) ? (
        <dl className="genie-answer__stats" aria-label="Genie answer headline facts">
          {columns.map((c) => {
            const v = visibleRows[0][c];
            const isNum = coerceNumber(v) !== null && !isIdentifierColumn(c);
            return (
              <div key={c} className="genie-answer__stat">
                <dt className="genie-answer__stat-label">{humanizeKey(c)}</dt>
                <dd className={`genie-answer__stat-value${isNum ? ' num' : ''}`}>
                  {formatCell(c, v)}
                </dd>
              </div>
            );
          })}
        </dl>
      ) : (
        visibleRows.length > 0 &&
        columns.length > 0 && (
          <>
            <div
              className="genie-answer__table-scroll"
              role="region"
              aria-label="Genie answer table"
              tabIndex={0}
            >
              <table className="genie-answer__table">
                <thead>
                  <tr>
                    {columns.map((c) => (
                      <th key={c}>{humanizeKey(c)}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {visibleRows.map((row, i) => (
                    <tr key={i}>
                      {columns.map((c) => {
                        const v = row[c];
                        const isNum = typeof v === 'number' && !isIdentifierColumn(c);
                        // Linkage: a masked borrower id drills into the same
                        // Borrower 360 route the drill-down cards use; a state
                        // code opens the same filtered Lead Queue the
                        // geography map navigates to on a state click.
                        // Everything else (including city — no route filters
                        // by city) renders as plain text.
                        const href = genieCellHref(c, v, cellCohort, row);
                        return (
                          <td key={c} className={isNum ? 'num' : undefined}>
                            {href ? (
                              <Link className="mono" to={href}>
                                {formatCell(c, v)}
                              </Link>
                            ) : (
                              formatCell(c, v)
                            )}
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {hiddenRows > 0 && (
              <div className="genie-answer__more">+{hiddenRows} more row{hiddenRows === 1 ? '' : 's'}</div>
            )}
          </>
        )
      )}
    </>
  );
}

/**
 * One section's own visual. The section carries its own rows and its own
 * visualization descriptor, so the plan is picked from a minimal payload-like
 * object rather than from the parent answer — a section must never inherit
 * the sweep's top-level chart type.
 */
export function GenieSectionVisual({
  section,
  cellCohort,
}: {
  section: GenieAnswerSection;
  cellCohort?: GenieAnswerCohort;
}) {
  const rows = Array.isArray(section.table_rows) ? section.table_rows : [];
  if (rows.length === 0) return null;
  const chartColumns = rows[0] ? Object.keys(rows[0]) : [];
  const plan = pickPlan(
    { answer: '', visualization: section.visualization ?? null } as GenieAnswerShape,
    rows,
    chartColumns,
  );
  return <GenieRowsVisual rows={rows} plan={plan} cellCohort={cellCohort} />;
}

export function GenieAnswerSections({
  summary,
  sections,
  workspaceHost,
  cellCohort,
}: {
  summary?: string | null;
  sections: GenieAnswerSection[];
  workspaceHost?: string | null;
  cellCohort?: GenieAnswerCohort;
}) {
  const summaryText = (summary ?? '').trim();
  return (
    <div className="genie-answer__sections">
      {summaryText && (
        <>
          <p className="genie-md-p genie-md-p--heading genie-md-p--first">Summary</p>
          <MarkdownAnswer text={summaryText} workspaceHost={workspaceHost} />
        </>
      )}
      {sections.map((section, i) => (
        <section
          className="genie-answer__section"
          key={`${section.title || 'section'}-${i}`}
        >
          {/* A section whose narrative failed verification still renders its
              prose here — the reason is disclosed in the proof drawer's
              known data gaps, not duplicated as an inline notice. */}
          <p
            className={`genie-md-p genie-md-p--heading${
              !summaryText && i === 0 ? ' genie-md-p--first' : ''
            }`}
          >
            {section.title || section.question}
          </p>
          {(section.answer ?? '').trim() && (
            <MarkdownAnswer text={section.answer} workspaceHost={workspaceHost} />
          )}
          <GenieSectionVisual section={section} cellCohort={cellCohort} />
        </section>
      ))}
    </div>
  );
}
