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
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { claimDrillFocus } from './USChoroplethMap.a11y';
import type { MapClass } from './USChoroplethMap.scale';

export interface MapTableRow {
  id: string;
  name: string;
  count: number;
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
  /** Drill into this unit (states only). */
  onOpen?: () => void;
}

interface USChoroplethMapTableProps {
  unitLabel: 'State' | 'ZIP';
  caption: string;
  rows: MapTableRow[];
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
}

const fmt = (value: number | null | undefined) =>
  typeof value === 'number' ? value.toLocaleString('en-US') : '—';

export function USChoroplethMapTable({
  unitLabel,
  caption,
  rows,
  extraColumn,
  autoFocus = false,
  onAutoFocused,
}: USChoroplethMapTableProps) {
  const [direction, setDirection] = useState<'descending' | 'ascending'>('descending');
  const tableRef = useRef<HTMLTableElement | null>(null);
  useEffect(() => {
    if (!autoFocus) return;
    claimDrillFocus(tableRef.current);
    onAutoFocused?.();
  }, [autoFocus, onAutoFocused]);
  const sorted = useMemo(() => {
    const sign = direction === 'descending' ? -1 : 1;
    return [...rows].sort((a, b) => sign * (a.count - b.count) || a.name.localeCompare(b.name));
  }, [direction, rows]);
  const total = rows.reduce((sum, row) => sum + row.count, 0);
  const showContactable = rows.some((row) => typeof row.contactable === 'number');
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
        <tbody>
          {sorted.map((row) => (
            <tr key={row.id}>
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
              {extraColumn && <td className="num tbl-cell--right">{fmt(row.extra)}</td>}
              <td className="num tbl-cell--right">{fmt(row.avgScore)}</td>
              <td>{row.topSegment ?? '—'}</td>
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr>
            <th scope="row">Total ({rows.length.toLocaleString('en-US')})</th>
            <td className="num tbl-cell--right" data-testid="map-table-total">{fmt(total)}</td>
            {showContactable && (
              <td className="num tbl-cell--right">
                {fmt(rows.reduce((sum, row) => sum + (row.contactable ?? 0), 0))}
              </td>
            )}
            {extraColumn && (
              <td className="num tbl-cell--right" data-testid="map-table-extra-total">
                {fmt(rows.reduce((sum, row) => sum + (row.extra ?? 0), 0))}
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
