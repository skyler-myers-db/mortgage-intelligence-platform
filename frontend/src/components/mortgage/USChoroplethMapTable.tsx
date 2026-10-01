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
 *
 * deviation:rate-change-as-numbers (wow-stage-1, D-dataviz-geo-b): in rate
 * mode the table adds "In the money: change vs today" (signed, hidden at
 * step 0) and "Contactable in the money at {rate}" beside the scenario
 * column, and every numeric header sorts (`.tbl__sort`, aria-sort on the
 * active header only; ties by name, each group sorted within itself). The
 * change footer sums every group, so it equals the legend's "N more / fewer
 * than today"; the contactable footer prints only when every row with a
 * grid value reports it.
 */
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { formatCount, formatNumber, signedCount } from '../../lib/formatters';
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
  /** In the money at the shown step minus today (rate mode, state rows); null off the grid. */
  change?: number | null;
  /** The contact-eligible subset in the money at the shown step; null when not reported. */
  scenarioContactable?: number | null;
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
  /** "In the money: change vs today" in rate mode away from step 0; else null. */
  changeColumn?: string | null;
  /** "Contactable in the money at 5.80%" in rate mode (shown when any row reports it); else null. */
  scenarioContactableColumn?: string | null;
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

type SortKey = 'count' | 'extra' | 'change';
type SortDirection = 'descending' | 'ascending';

/** The sorted value; a row with none sorts after every valued row, in both directions. */
function sortValue(row: MapTableRow, key: SortKey): number | null {
  if (key === 'count') return countOf(row);
  const value = key === 'extra' ? row.extra : row.change;
  return typeof value === 'number' ? value : null;
}

function sortRows(rows: readonly MapTableRow[], key: SortKey, direction: SortDirection): MapTableRow[] {
  const sign = direction === 'descending' ? -1 : 1;
  return [...rows].sort((a, b) => {
    const left = sortValue(a, key);
    const right = sortValue(b, key);
    if (left === null || right === null) {
      return (left === null ? 1 : 0) - (right === null ? 1 : 0) || a.name.localeCompare(b.name);
    }
    return sign * (left - right) || a.name.localeCompare(b.name);
  });
}

const sumOf = (rows: readonly MapTableRow[], value: (row: MapTableRow) => number | null | undefined) =>
  rows.reduce((sum, row) => sum + (value(row) ?? 0), 0);

export function USChoroplethMapTable({
  unitLabel,
  caption,
  groups,
  extraColumn,
  changeColumn = null,
  scenarioContactableColumn = null,
  autoFocus = false,
  onAutoFocused,
  focusRowId = null,
  onFocusRowDone,
}: USChoroplethMapTableProps) {
  const [sort, setSort] = useState<{ key: SortKey; direction: SortDirection }>({ key: 'count', direction: 'descending' });
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
  // A column that left (a mode or step change) cannot stay the sort key.
  const key: SortKey = (sort.key === 'extra' && !extraColumn) || (sort.key === 'change' && !changeColumn)
    ? 'count'
    : sort.key;
  const direction = key === sort.key ? sort.direction : 'descending';
  const sorted = useMemo(
    () => ({ rows: sortRows(rows, key, direction), offMap: sortRows(offMap, key, direction), empty: sortRows(empty, key, direction) }),
    [direction, empty, key, offMap, rows],
  );
  const onSort = (next: SortKey) =>
    setSort({ key: next, direction: next === key && direction === 'descending' ? 'ascending' : 'descending' });
  // The borrower total counts the populated and not-drawn groups; the extra
  // and change columns every group (every grid id is in exactly one group).
  const counted = [...rows, ...offMap];
  const every = [...counted, ...empty];
  const total = sumOf(counted, countOf);
  const extraTotal = sumOf(every, (row) => row.extra);
  const changeTotal = sumOf(every, (row) => row.change);
  const gridRows = every.filter((row) => typeof row.extra === 'number');
  const showContactable = rows.some((row) => typeof row.contactable === 'number');
  const showScenarioContactable = scenarioContactableColumn !== null
    && every.some((row) => typeof row.scenarioContactable === 'number');
  const scenarioContactableTotal = gridRows.every((row) => typeof row.scenarioContactable === 'number')
    ? sumOf(gridRows, (row) => row.scenarioContactable)
    : null;
  const show = {
    contactable: showContactable,
    extra: extraColumn !== null,
    change: changeColumn !== null,
    scenarioContactable: showScenarioContactable,
  };
  const columns = 4 + Object.values(show).filter(Boolean).length;
  const renderRow = (row: MapTableRow) => <MapTableBodyRow key={row.id} row={row} show={show} />;
  const sortHeader = (column: SortKey, label: string) => (
    <th scope="col" className="tbl-cell--right" aria-sort={key === column ? direction : undefined}>
      <button type="button" className="tbl__sort" onClick={() => onSort(column)}>
        {label}
        {key === column && <span aria-hidden="true">{direction === 'descending' ? '↓' : '↑'}</span>}
      </button>
    </th>
  );
  return (
    <div className="map-table" data-testid="map-table">
      <table ref={tableRef} className="tbl map-table__table" tabIndex={-1}>
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr>
            <th scope="col">{unitLabel}</th>
            {sortHeader('count', 'Marketable borrowers')}
            {show.contactable && <th scope="col" className="tbl-cell--right">Contactable</th>}
            {extraColumn && sortHeader('extra', extraColumn)}
            {changeColumn && sortHeader('change', changeColumn)}
            {show.scenarioContactable && <th scope="col" className="tbl-cell--right">{scenarioContactableColumn}</th>}
            <th scope="col" className="tbl-cell--right">Avg. score</th>
            <th scope="col">Top segment</th>
          </tr>
        </thead>
        <tbody>{sorted.rows.map(renderRow)}</tbody>
        {[[OFF_MAP_GROUP_LABEL, sorted.offMap] as const, [EMPTY_GROUP_LABEL, sorted.empty] as const].map(([label, group]) =>
          group.length > 0 ? (
            <tbody key={label}>
              <tr className="map-table__group">
                <th scope="colgroup" colSpan={columns}>
                  {label} ({formatCount(group.length)})
                </th>
              </tr>
              {group.map(renderRow)}
            </tbody>
          ) : null,
        )}
        <tfoot>
          <tr>
            <th scope="row">Total ({formatCount(counted.length)})</th>
            <td className="num tbl-cell--right" data-testid="map-table-total">{formatCount(total)}</td>
            {show.contactable && (
              <td className="num tbl-cell--right">{formatCount(sumOf(counted, (row) => row.contactable))}</td>
            )}
            {show.extra && (
              <td className="num tbl-cell--right" data-testid="map-table-extra-total">
                {formatCount(extraTotal)}
              </td>
            )}
            {show.change && (
              <td className="num tbl-cell--right" data-testid="map-table-change-total">{signedCount(changeTotal)}</td>
            )}
            {show.scenarioContactable && (
              <td className="num tbl-cell--right" data-testid="map-table-scenario-contactable-total">
                {formatCount(scenarioContactableTotal)}
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
  show: { contactable: boolean; extra: boolean; change: boolean; scenarioContactable: boolean };
}

/** One unit's row; a grouped row (no borrowers) carries its note in the last cell. */
function MapTableBodyRow({ row, show }: MapTableBodyRowProps) {
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
      <td className="num tbl-cell--right">{formatCount(row.count)}</td>
      {show.contactable && <td className="num tbl-cell--right">{formatCount(row.contactable)}</td>}
      {show.extra && <td className="num tbl-cell--right">{formatCount(row.extra)}</td>}
      {show.change && <td className="num tbl-cell--right">{signedCount(row.change)}</td>}
      {show.scenarioContactable && <td className="num tbl-cell--right">{formatCount(row.scenarioContactable)}</td>}
      <td className="num tbl-cell--right">{formatNumber(row.avgScore)}</td>
      {row.note ? <td className="map-table__note">{row.note}</td> : <td>{row.topSegment ?? '—'}</td>}
    </tr>
  );
}
