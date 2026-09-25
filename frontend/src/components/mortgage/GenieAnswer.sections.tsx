import { lazy, Suspense, useId, useState } from 'react';
import { Link } from 'react-router';
import { Icon } from '../Icon';
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
  unionColumns,
  type GenieVisualizationPlan,
} from './GenieAnswer.logic';
import { genieCellHref, type GenieAnswerCohort } from '../../lib/genieCellLinks';
import {
  GenieAnswerRowsActions,
  GenieRowsCsvDownload,
  heldRowsNote,
  type GenieRowsExtent,
} from './GenieAnswerRowsActions';
import type { GenieAnswerExportBase, GenieRowsExportTarget } from './GenieAnswer.exportTarget';

// Every row and column, loaded only when the reader asks for it (genie-06).
const GenieAnswerAllRows = lazy(() => import('./GenieAnswerAllRows'));

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
 * (chart plan, single-row stat strip, capped table, and the row actions:
 * "Show all" rows and columns in place, genie-06 slice 2).
 */

export function GenieRowsVisual({
  rows,
  plan,
  cellCohort,
  reportedRowCount = null,
  dense = false,
  exportTarget = null,
  onAnnounce,
}: {
  rows: Array<Record<string, unknown>>;
  plan: GenieVisualizationPlan;
  /** The answer's own filters, so a row link opens the population the row
   *  reports rather than every borrower in that geography. */
  cellCohort?: GenieAnswerCohort;
  /** The row count the answer (or section) reports: above rows.length only
   *  for a History replay that kept fewer rows than the query returned. */
  reportedRowCount?: number | null;
  /** The floating panel: the all-rows region is shorter. */
  dense?: boolean;
  /** Set on a trusted, live answer: offers the audited CSV download. */
  exportTarget?: GenieRowsExportTarget | null;
  /** The surface's one announcer (a11y-06). */
  onAnnounce?: (text: string) => void;
}) {
  const [showAll, setShowAll] = useState(false);
  const chart = plan.chart;
  const visibleRows = rows.slice(0, MAX_TABLE_ROWS);
  const hiddenRows = Math.max(0, rows.length - MAX_TABLE_ROWS);
  const allColumns = visibleRows[0] ? Object.keys(visibleRows[0]) : [];
  const columns = allColumns.slice(0, MAX_TABLE_COLS);
  // Columns past the cap used to be dropped without a word (audit 2026-09-21
  // `genie-06`). They are named below the table, in the same humanized form
  // as the headers, so the reader knows what the answer holds that the
  // compact table does not show.
  const hiddenColumns = allColumns.slice(MAX_TABLE_COLS);
  const everyColumn = unionColumns(rows);
  const extent: GenieRowsExtent = {
    held: rows.length,
    reported: reportedRowCount,
    hiddenRows,
    columns: everyColumn.length,
    hiddenColumns: everyColumn.length - columns.length,
  };
  const hiddenColumnsNote =
    hiddenColumns.length > 0 ? (
      <div className="genie-answer__more genie-answer__hidden-columns">
        {hiddenColumns.length} column{hiddenColumns.length === 1 ? '' : 's'} not shown:{' '}
        {hiddenColumns.map(humanizeKey).join(', ')}
      </div>
    ) : null;
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
        <GenieLineChart
          data={chart.rows}
          labelCol={chart.labelCol}
          valueCol={chart.valueCol}
          tableRowCount={rows.length}
        />
      )}
      {(plan.kind === 'bar' || plan.kind === 'funnel' || (!['strategy_board', 'borrower_list', 'map', 'line'].includes(plan.kind) && chart)) && chart && (
        <GenieBarChart
          data={chart.rows}
          labelCol={chart.labelCol}
          valueCol={chart.valueCol}
          tableRowCount={rows.length}
        />
      )}
      {/* A single METRIC row is a set of headline facts, not a table —
          render it as a stat strip (value-first, KPI style) so answers like
          "count + avg spread + refreshed at" read at a glance. A single
          RECORD row (identifier columns, e.g. a borrower id) keeps the
          table: masked ids as KPI headlines misread, and borrower_list
          plans already render their own list above (QA M6).
          "Show all" replaces either compact form in place (genie-06). */}
      {showAll ? (
        <Suspense fallback={<div className="genie-answer__more">Loading every row…</div>}>
          <GenieAnswerAllRows
            rows={rows}
            columns={everyColumn}
            cellCohort={cellCohort}
            dense={dense}
            heldNote={heldRowsNote(extent)}
          />
        </Suspense>
      ) : visibleRows.length === 1 &&
      columns.length > 0 &&
      plan.kind === 'none' &&
      columns.every((c) => !isIdentifierColumn(c)) &&
      // Warehouse rows arrive as strings — coerce like the chart layer does.
      columns.some((c) => coerceNumber(visibleRows[0][c]) !== null) ? (
        <>
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
          {hiddenColumnsNote}
        </>
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
            {hiddenColumnsNote}
          </>
        )
      )}
      {/* The inert "+N more rows" became the control that shows them. */}
      {rows.length > 0 && (
        <GenieAnswerRowsActions extent={extent} expanded={showAll} onToggle={() => setShowAll((open) => !open)}>
          {exportTarget && (
            <GenieRowsCsvDownload
              rows={rows}
              columns={everyColumn}
              target={exportTarget}
              reportedRowCount={reportedRowCount}
              onAnnounce={onAnnounce}
            />
          )}
        </GenieAnswerRowsActions>
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
  dense = false,
  exportTarget = null,
  onAnnounce,
}: {
  section: GenieAnswerSection;
  cellCohort?: GenieAnswerCohort;
  dense?: boolean;
  exportTarget?: GenieRowsExportTarget | null;
  onAnnounce?: (text: string) => void;
}) {
  const rows = Array.isArray(section.table_rows) ? section.table_rows : [];
  if (rows.length === 0) return null;
  const chartColumns = rows[0] ? Object.keys(rows[0]) : [];
  const plan = pickPlan(
    { answer: '', visualization: section.visualization ?? null } as GenieAnswerShape,
    rows,
    chartColumns,
  );
  return (
    <GenieRowsVisual
      rows={rows}
      plan={plan}
      cellCohort={cellCohort}
      reportedRowCount={section.row_count ?? null}
      dense={dense}
      exportTarget={exportTarget}
      onAnnounce={onAnnounce}
    />
  );
}

/** Sections from which a long sweep gets collapsible bodies. */
export const GENIE_ACCORDION_MIN_SECTIONS = 4;

/**
 * Deep-research body (audit 2026-09-21 `genie-08`). The Summary and every
 * section title are REAL h3 headings (the same classes the old <p> carried,
 * so the pixels match), and the prose inside a section heads at h4.
 *
 * A sweep of four or more sections also gets collapsible bodies. The APG
 * accordion pattern is used rather than <details>: a heading inside
 * <summary> loses its heading semantics in Firefox (its children are
 * presentational). Every section starts open; the open/closed state is
 * presentation only and is never written to any store. (The outline above
 * the sections is held for wave 4 by the lane's JS budget cut line.)
 */
export function GenieAnswerSections({
  summary,
  sections,
  workspaceHost,
  cellCohort,
  dense = false,
  exportBase = null,
  onAnnounce,
}: {
  summary?: string | null;
  sections: GenieAnswerSection[];
  workspaceHost?: string | null;
  cellCohort?: GenieAnswerCohort;
  /** The floating panel. */
  dense?: boolean;
  /** Set on a trusted, live answer: each section offers the audited CSV. */
  exportBase?: GenieAnswerExportBase | null;
  onAnnounce?: (text: string) => void;
}) {
  const summaryText = (summary ?? '').trim();
  const collapsible = sections.length >= GENIE_ACCORDION_MIN_SECTIONS;
  const idBase = useId();
  const [closed, setClosed] = useState<ReadonlySet<number>>(() => new Set());
  const titleOf = (section: GenieAnswerSection) => section.title || section.question;
  const setOpen = (index: number, open: boolean) =>
    setClosed((current) => {
      const next = new Set(current);
      if (open) next.delete(index);
      else next.add(index);
      return next;
    });
  return (
    <div className="genie-answer__sections">
      {summaryText && (
        <>
          <h3 className="genie-md-p genie-md-p--heading genie-md-p--first">Summary</h3>
          <MarkdownAnswer text={summaryText} workspaceHost={workspaceHost} headingLevel={4} />
        </>
      )}
      {sections.map((section, i) => {
        const headingClass = `genie-md-p genie-md-p--heading${!summaryText && i === 0 ? ' genie-md-p--first' : ''}`;
        const bodyId = `${idBase}-section-${i}`;
        const open = !closed.has(i);
        // A section whose narrative failed verification still renders its
        // prose here — the reason is disclosed in the proof drawer's known
        // data gaps, not duplicated as an inline notice.
        const body = (
          <>
            {(section.answer ?? '').trim() && (
              <MarkdownAnswer text={section.answer} workspaceHost={workspaceHost} headingLevel={4} />
            )}
            <GenieSectionVisual
              section={section}
              cellCohort={cellCohort}
              dense={dense}
              exportTarget={exportBase ? { ...exportBase, scope: 'section', sectionIndex: i + 1 } : null}
              onAnnounce={onAnnounce}
            />
          </>
        );
        return (
          <section className="genie-answer__section" key={`${section.title || 'section'}-${i}`}>
            {collapsible ? (
              <>
                <h3 className={headingClass}>
                  <button
                    type="button"
                    className="genie-answer__section-toggle"
                    aria-expanded={open}
                    aria-controls={bodyId}
                    onClick={() => setOpen(i, !open)}
                  >
                    <span>{titleOf(section)}</span>
                    <Icon name="chevdown" size={12} />
                  </button>
                </h3>
                <div id={bodyId} className="genie-answer__section-body" hidden={!open}>
                  {body}
                </div>
              </>
            ) : (
              <>
                <h3 className={headingClass}>{titleOf(section)}</h3>
                {body}
              </>
            )}
          </section>
        );
      })}
    </div>
  );
}
