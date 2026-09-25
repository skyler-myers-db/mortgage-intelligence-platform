import { useLayoutEffect, useRef, useState, type FocusEvent, type UIEvent } from 'react';
import { Link } from 'react-router';
import { formatCount } from '../../lib/formatters';
import { genieCellHref, type GenieAnswerCohort } from '../../lib/genieCellLinks';
import { formatCell, humanizeKey, isIdentifierColumn } from './GenieAnswer.logic';

/**
 * Every row and every column a Genie answer holds (audit 2026-09-21
 * `genie-06`, slice 2), in place of the capped compact table. A lazy
 * interaction chunk: it loads only when the reader asks for it.
 *
 * The region scrolls inside the answer (its height is capped by tokens,
 * lower in the dense floating panel), so the panel's own size never changes.
 * The header row is sticky and only the rows in view (plus an overscan) are
 * mounted between two spacer rows; `aria-rowcount` / `aria-rowindex` keep
 * the table's true size for assistive technology. Cells go through the same
 * formatCell / genieCellHref as the compact table.
 *
 * Windowing is done here rather than with @tanstack/react-virtual: its
 * `useVirtualizer` is a library the React Compiler refuses to compile
 * ("Use of incompatible library"), and the compiler gate may not grow. The
 * rows are one line each (cells never wrap here), so a measured row height
 * is exact for every row.
 */

/** Row height before the first row is measured. */
const ROW_ESTIMATE_PX = 28;
/** Rows mounted beyond each edge of the view. */
const OVERSCAN_ROWS = 6;
/** View height before the region is measured. */
const VIEW_ESTIMATE_PX = 320;

interface WindowState {
  scrollTop: number;
  viewHeight: number;
  rowHeight: number;
}

export default function GenieAnswerAllRows({
  rows,
  columns,
  cellCohort,
  dense = false,
  heldNote = null,
}: {
  rows: Array<Record<string, unknown>>;
  /** Every column the rows hold, in first-seen order. */
  columns: string[];
  cellCohort?: GenieAnswerCohort;
  /** The floating panel: a shorter region. */
  dense?: boolean;
  /** Shown above the rows when a History replay kept fewer rows than ran. */
  heldNote?: string | null;
}) {
  const regionRef = useRef<HTMLDivElement | null>(null);
  const [view, setView] = useState<WindowState>({
    scrollTop: 0,
    viewHeight: VIEW_ESTIMATE_PX,
    rowHeight: ROW_ESTIMATE_PX,
  });

  // Measure the region and one row now and whenever the region resizes (the
  // floating panel is resizable).
  useLayoutEffect(() => {
    const region = regionRef.current;
    if (!region) return undefined;
    const measure = () => {
      const row = region.querySelector<HTMLElement>('tbody tr[aria-rowindex]');
      const rowHeight = row?.getBoundingClientRect().height || ROW_ESTIMATE_PX;
      const viewHeight = region.clientHeight || VIEW_ESTIMATE_PX;
      setView((current) =>
        current.rowHeight === rowHeight && current.viewHeight === viewHeight
          ? current
          : { ...current, rowHeight, viewHeight },
      );
    };
    measure();
    if (typeof ResizeObserver !== 'function') return undefined;
    const observer = new ResizeObserver(measure);
    observer.observe(region);
    return () => observer.disconnect();
  }, []);

  const onScroll = (event: UIEvent<HTMLDivElement>) => {
    const scrollTop = event.currentTarget.scrollTop;
    setView((current) => (current.scrollTop === scrollTop ? current : { ...current, scrollTop }));
  };

  // The row holding focus (a cell link), tracked from focusin / focusout on
  // the body: it stays mounted when it scrolls out of the window, so the
  // focused link is never unmounted under the reader (w3-genie-reading
  // review; genie-06 item 3).
  const [focusedIndex, setFocusedIndex] = useState<number | null>(null);
  const onBodyFocus = (event: FocusEvent<HTMLTableSectionElement>) => {
    const rowIndex = Number(event.target.closest('tr')?.getAttribute('aria-rowindex'));
    setFocusedIndex(Number.isInteger(rowIndex) && rowIndex >= 2 ? rowIndex - 2 : null);
  };
  const onBodyBlur = (event: FocusEvent<HTMLTableSectionElement>) => {
    const next = event.relatedTarget;
    if (!(next instanceof Node) || !event.currentTarget.contains(next)) setFocusedIndex(null);
  };

  const inView = Math.ceil(view.viewHeight / view.rowHeight);
  const start = Math.max(0, Math.min(rows.length, Math.floor(view.scrollTop / view.rowHeight) - OVERSCAN_ROWS));
  const end = Math.min(rows.length, start + inView + OVERSCAN_ROWS * 2);
  const retained =
    focusedIndex !== null && focusedIndex < rows.length && (focusedIndex < start || focusedIndex >= end)
      ? focusedIndex
      : null;

  const renderRow = (index: number) => (
    <tr key={index} aria-rowindex={index + 2}>
      {columns.map((c) => {
        const v = rows[index][c];
        const isNum = typeof v === 'number' && !isIdentifierColumn(c);
        const href = genieCellHref(c, v, cellCohort, rows[index]);
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
  );
  const spacer = (key: string, rowCount: number) =>
    rowCount > 0 ? (
      <tr key={key} className="genie-answer__all-rows-spacer" aria-hidden="true">
        <td colSpan={columns.length} style={{ blockSize: rowCount * view.rowHeight }} />
      </tr>
    ) : null;
  const windowRows = Array.from({ length: end - start }, (_, offset) => renderRow(start + offset));
  // ONE keyed list, so the retained row keeps its DOM node (and focus) as it
  // moves between the window and its own slot; the spacers split around it.
  const bodyRows =
    retained === null
      ? [spacer('pad-top', start), ...windowRows, spacer('pad-bottom', rows.length - end)]
      : retained < start
        ? [
            spacer('pad-top', retained),
            renderRow(retained),
            spacer('gap', start - retained - 1),
            ...windowRows,
            spacer('pad-bottom', rows.length - end),
          ]
        : [
            spacer('pad-top', start),
            ...windowRows,
            spacer('gap', retained - end),
            renderRow(retained),
            spacer('pad-bottom', rows.length - retained - 1),
          ];

  return (
    <>
      {heldNote && <p className="genie-answer__rows-note">{heldNote}</p>}
      <div
        ref={regionRef}
        className={`genie-answer__all-rows${dense ? ' genie-answer__all-rows--dense' : ''}`}
        role="region"
        aria-label={`All ${formatCount(rows.length)} rows of this answer`}
        tabIndex={0}
        onScroll={onScroll}
      >
        <table className="genie-answer__table" aria-rowcount={rows.length + 1}>
          <thead>
            <tr aria-rowindex={1}>
              {columns.map((c) => (
                <th key={c} scope="col">
                  {humanizeKey(c)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody onFocus={onBodyFocus} onBlur={onBodyBlur}>
            {bodyRows}
          </tbody>
        </table>
      </div>
    </>
  );
}
