/**
 * USChoroplethMapTable — "View as table" for the geography map (audit a11y-04
 * slice 3). A real `<table>` with the numbers the map paints: one row per
 * populated state (or per ZIP of the drilled state), sortable by count, with
 * a total row that equals the legend's "in selection" figure. It is the
 * non-visual and forced-colours equivalent of the fill, and the fast way to
 * compare units without hovering 51 shapes.
 *
 * Prototype vocabulary: `.tbl` / `.tbl__sort` (design_files/index.html:595,
 * the ranked-borrower table); `.map-table` only sizes it inside the map.
 *
 * A state row's button drills, which re-mounts the stage and removes that
 * button. The ZIP table then takes focus itself (it is named by its
 * caption), so a keyboard or screen-reader user lands on the drilled data
 * instead of <body>.
 *
 * deviation:map-table-coverage-groups (dataviz-10, wow-stage-1): the ids
 * the map cannot draw (PR, VI) and the drawn states it skips (no borrowers in
 * this selection) follow as their own <tbody> groups under a
 * `.map-table__group` header, the skipped ones with a `.map-table__note`
 * saying why, so the table lists the whole book. The borrower total counts
 * the populated and not-drawn groups (the legend's total); the extra
 * column's total counts every group (USChoroplethMap.table.ts).
 */
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { claimDrillFocus } from './USChoroplethMap.a11y';
import type { MapClass } from './USChoroplethMap.scale';
import { EMPTY_GROUP_LABEL, OFF_MAP_GROUP_LABEL, type MapTableGroups } from './USChoroplethMap.table';

export interface MapTableRow {
  id: string;
  name: string;
  /** Marketable borrowers; null when the unit has none in this selection. */
  count: number | null;
  avgScore: number | null;
  topSegment?: string;
  /** Contact-eligible subset (state rows); undefined when not reported. */
  contactable?: number | null;
  /**
   * The value the fill encodes when it is not borrowers: unattended leads
   * under the S9 overlay, or in the money at the Rate Lever's shown step.
   */
  extra?: number | null;
  cls: MapClass | null;
  /** Drill into this unit (populated states only). */
  onOpen?: () => void;
  /** Why a grouped row has no borrowers ("Outside Cotality evaluation scope"). */
  note?: string;
}

interface USChoroplethMapTableProps {
  unitLabel: 'State' | 'ZIP';
  caption: string;
  groups: MapTableGroups;
  /**
   * Header of the `extra` column ("Unattended leads", "In the money at
   * 5.80%"), or null when the fill encodes borrowers. Its total equals the
   * legend's figure.
   */
  extraColumn: string | null;
  /** Take focus once rendered: a drill from a row of the previous table removed the focused button. */
  autoFocus?: boolean;
  /** Called once focus has moved, so a later Back navigation does not steal it. */
  onAutoFocused?: () => void;
  /** A row whose button takes focus back (Escape out of that state's ZIP level, dataviz-10). */
  focusRowId?: string | null;
  /** Called once the row request has been answered. */
  onFocusRowDone?: () => void;
}

const countOf = (row: MapTableRow) => row.count ?? 0;

const fmt = (value: number | null | undefined) =>
  typeof value === 'number' ? value.toLocaleString('en-US') : '—';

export function USChoroplethMapTable({
  unitLabel,
  caption,
  groups,
  extraColumn,
  autoFocus = false,
  onAutoFocused,
  focusRowId = null,
  onFocusRowDone,
}: USChoroplethMapTableProps) {
  const [direction, setDirection] = useState<'descending' | 'ascending'>('descending');
  const tableRef = useRef<HTMLTableElement | null>(null);
  useEffect(() => {
    if (!autoFocus) return;
    claimDrillFocus(tableRef.current);
    onAutoFocused?.();
  }, [autoFocus, onAutoFocused]);
  useLayoutEffect(() => {
    if (!focusRowId) return;
    const row = [...(tableRef.current?.querySelectorAll<HTMLElement>('tr[data-map-row]') ?? [])]
      .find((candidate) => candidate.getAttribute('data-map-row') === focusRowId);
    claimDrillFocus(row?.querySelector<HTMLButtonElement>('button') ?? null);
    onFocusRowDone?.();
  }, [focusRowId, onFocusRowDone]);
  const { rows, offMap, empty } = groups;
  const sorted = useMemo(() => {
    const sign = direction === 'descending' ? -1 : 1;
    return [...rows].sort((a, b) => sign * (countOf(a) - countOf(b)) || a.name.localeCompare(b.name));
  }, [direction, rows]);
  // The borrower total counts the populated and not-drawn groups; the extra column's every group.
  const counted = [...rows, ...offMap];
  const total = counted.reduce((sum, row) => sum + countOf(row), 0);
  const extraTotal = [...counted, ...empty].reduce((sum, row) => sum + (row.extra ?? 0), 0);
  const showContactable = rows.some((row) => typeof row.contactable === 'number');
  const columns = 4 + (showContactable ? 1 : 0) + (extraColumn ? 1 : 0);
  const renderRow = (row: MapTableRow) => (
    <MapTableBodyRow key={row.id} row={row} showContactable={showContactable} showExtra={extraColumn !== null} />
  );
  return (
    <div className="map-table" data-testid="map-table">
      <table ref={tableRef} className="tbl map-table__table" tabIndex={-1}>
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr>
            <th scope="col">{unitLabel}</th>
            <th scope="col" className="tbl-cell--right" aria-sort={direction}>
              <button
                type="button"
                className="tbl__sort"
                onClick={() => setDirection((d) => (d === 'descending' ? 'ascending' : 'descending'))}
              >
                Marketable borrowers
                <span aria-hidden="true">{direction === 'descending' ? '↓' : '↑'}</span>
              </button>
            </th>
            {showContactable && <th scope="col" className="tbl-cell--right">Contactable</th>}
            {extraColumn && <th scope="col" className="tbl-cell--right">{extraColumn}</th>}
            <th scope="col" className="tbl-cell--right">Avg. score</th>
            <th scope="col">Top segment</th>
          </tr>
        </thead>
        <tbody>{sorted.map(renderRow)}</tbody>
        {[[OFF_MAP_GROUP_LABEL, offMap] as const, [EMPTY_GROUP_LABEL, empty] as const].map(([label, group]) =>
          group.length > 0 ? (
            <tbody key={label}>
              <tr className="map-table__group">
                <th scope="colgroup" colSpan={columns}>
                  {label} ({group.length.toLocaleString('en-US')})
                </th>
              </tr>
              {group.map(renderRow)}
            </tbody>
          ) : null,
        )}
        <tfoot>
          <tr>
            <th scope="row">Total ({counted.length.toLocaleString('en-US')})</th>
            <td className="num tbl-cell--right" data-testid="map-table-total">{fmt(total)}</td>
            {showContactable && (
              <td className="num tbl-cell--right">
                {fmt(counted.reduce((sum, row) => sum + (row.contactable ?? 0), 0))}
              </td>
            )}
            {extraColumn && (
              <td className="num tbl-cell--right" data-testid="map-table-extra-total">
                {fmt(extraTotal)}
              </td>
            )}
            <td />
            <td />
          </tr>
        </tfoot>
      </table>
    </div>
  );
}

interface MapTableBodyRowProps {
  row: MapTableRow;
  showContactable: boolean;
  showExtra: boolean;
}

/** One unit's row; a grouped row (no borrowers) carries its note in the last cell. */
function MapTableBodyRow({ row, showContactable, showExtra }: MapTableBodyRowProps) {
  return (
    <tr data-map-row={row.id}>
      <th scope="row" className="map-table__unit">
        <span className={`map-table__swatch lvl-${row.cls ?? 0}`} aria-hidden="true" />
        {row.onOpen ? (
          <button type="button" className="map-table__open" onClick={row.onOpen}>
            {row.name}
          </button>
        ) : (
          row.name
        )}
      </th>
      <td className="num tbl-cell--right">{fmt(row.count)}</td>
      {showContactable && <td className="num tbl-cell--right">{fmt(row.contactable)}</td>}
      {showExtra && <td className="num tbl-cell--right">{fmt(row.extra)}</td>}
      <td className="num tbl-cell--right">{fmt(row.avgScore)}</td>
      {row.note ? <td className="map-table__note">{row.note}</td> : <td>{row.topSegment ?? '—'}</td>}
    </tr>
  );
}
