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
 */
import { useMemo, useState } from 'react';
import type { MapClass } from './USChoroplethMap.scale';

export interface MapTableRow {
  id: string;
  name: string;
  count: number;
  avgScore: number | null;
  topSegment?: string;
  /** Contact-eligible subset (state rows); undefined when not reported. */
  contactable?: number | null;
  /** Unattended leads, when the overlay colours the map. */
  unattended?: number | null;
  cls: MapClass | null;
  /** Drill into this unit (states only). */
  onOpen?: () => void;
}

interface USChoroplethMapTableProps {
  unitLabel: 'State' | 'ZIP';
  caption: string;
  rows: MapTableRow[];
  overlayActive: boolean;
}

const fmt = (value: number | null | undefined) =>
  typeof value === 'number' ? value.toLocaleString('en-US') : '—';

export function USChoroplethMapTable({ unitLabel, caption, rows, overlayActive }: USChoroplethMapTableProps) {
  const [direction, setDirection] = useState<'descending' | 'ascending'>('descending');
  const sorted = useMemo(() => {
    const sign = direction === 'descending' ? -1 : 1;
    return [...rows].sort((a, b) => sign * (a.count - b.count) || a.name.localeCompare(b.name));
  }, [direction, rows]);
  const total = rows.reduce((sum, row) => sum + row.count, 0);
  const showContactable = rows.some((row) => typeof row.contactable === 'number');
  return (
    <div className="map-table" data-testid="map-table">
      <table className="tbl map-table__table">
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
            {overlayActive && <th scope="col" className="tbl-cell--right">Unattended leads</th>}
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
              {overlayActive && <td className="num tbl-cell--right">{fmt(row.unattended)}</td>}
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
            {overlayActive && (
              <td className="num tbl-cell--right">
                {fmt(rows.reduce((sum, row) => sum + (row.unattended ?? 0), 0))}
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
