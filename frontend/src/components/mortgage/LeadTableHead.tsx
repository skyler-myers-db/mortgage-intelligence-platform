import type { MouseEvent as ReactMouseEvent } from 'react';
import { Icon } from '../Icon';
import type { LeadTableColumn } from './LeadTable.columns';
import { LeadTableStatusSortMenu } from './LeadTableStatusSortMenu';
import type { SortDir, SortKey } from './LeadTable.types';

interface HeaderCheckboxState {
  checked: boolean;
  indeterminate: boolean;
}

/**
 * `<colgroup>` + `<thead>` of the ranked-borrower table, driven by the typed
 * column model (LeadTable.columns.ts) so a view preset can never put a
 * header over the wrong cell.
 *
 * 2026-06-11 audit P3 a11y: a sortable header renders the full `<th>` so
 * `aria-sort` lives on the columnheader role (the only role where the ARIA
 * spec defines it; on the inner button it would be invalid and ignored).
 */
export function LeadTableHead({
  columns,
  sortKey,
  sortDir,
  onSort,
  headerCheckbox,
  selectAllDisabled,
  onToggleSelectAll,
}: {
  columns: readonly LeadTableColumn[];
  sortKey: SortKey;
  sortDir: SortDir;
  onSort: (key: SortKey) => void;
  headerCheckbox: HeaderCheckboxState;
  selectAllDisabled: boolean;
  onToggleSelectAll: () => void;
}) {
  const stop = (event: ReactMouseEvent) => event.stopPropagation();
  return (
    <>
      <colgroup>
        {columns.map((column) => <col key={column.key} className={column.colClass} />)}
      </colgroup>
      <thead>
        <tr>
          {columns.map((column) => {
            if (column.key === 'select') {
              return (
                <th key={column.key} className={column.thClass}>
                  <input
                    type="checkbox"
                    aria-label="Select all eligible leads"
                    checked={headerCheckbox.checked}
                    ref={(el) => {
                      if (el) el.indeterminate = headerCheckbox.indeterminate;
                    }}
                    disabled={selectAllDisabled}
                    onChange={onToggleSelectAll}
                    onClick={stop}
                    data-testid="lead-select-all"
                  />
                </th>
              );
            }
            if (column.key === 'status') {
              return <LeadTableStatusSortMenu key={column.key} sortKey={sortKey} sortDir={sortDir} onSort={onSort} />;
            }
            if (column.sortKey) {
              const key = column.sortKey;
              const activeSort = sortKey === key;
              return (
                <th
                  key={column.key}
                  className={column.thClass}
                  aria-sort={activeSort ? (sortDir === 'desc' ? 'descending' : 'ascending') : 'none'}
                >
                  <button
                    type="button"
                    className="tbl__sort"
                    onClick={() => onSort(key)}
                    aria-label={`Sort by ${column.label}`}
                    aria-pressed={activeSort}
                  >
                    <span>{column.label}</span>
                    {activeSort && <Icon name={sortDir === 'desc' ? 'down' : 'up'} size={10} />}
                  </button>
                </th>
              );
            }
            return <th key={column.key} className={column.thClass}>{column.label}</th>;
          })}
        </tr>
      </thead>
    </>
  );
}
