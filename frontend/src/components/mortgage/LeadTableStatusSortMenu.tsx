import { useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from 'react';
import { pushEscapeLayer } from '../../lib/escapeStack';
import { Icon } from '../Icon';
import { STATUS_SORT_OPTIONS } from './LeadTable.columns';
import type { SortDir, SortKey } from './LeadTable.types';

/**
 * Header of the merged Status column (audit visual-01 / tables-05). The four
 * workflow columns it replaces carried three sort keys (relationship,
 * assignee, outreach); they stay reachable through this small menu so the
 * merge costs no sort capability. `aria-sort` sits on the `<th>`, the only
 * role that defines it, and names the direction while one of the three keys
 * is the active sort.
 *
 * The menu reuses the `.filter-menu` / `.filter-menu__item` dropdown
 * primitives (as GenieHistoryMenu does); role="menu" also switches the A / R
 * row hotkeys off while it is open (useLeadTableHotkeys).
 */
export function LeadTableStatusSortMenu({
  sortKey,
  sortDir,
  onSort,
}: {
  sortKey: SortKey;
  sortDir: SortDir;
  onSort: (key: SortKey) => void;
}) {
  const [open, setOpen] = useState(false);
  const [cursor, setCursor] = useState(0);
  const rootRef = useRef<HTMLTableCellElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const itemRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const active = STATUS_SORT_OPTIONS.find((option) => option.key === sortKey) ?? null;

  useEffect(() => {
    if (!open) return undefined;
    const onDown = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const popEscapeLayer = pushEscapeLayer(() => {
      setOpen(false);
      triggerRef.current?.focus();
    });
    window.addEventListener('mousedown', onDown);
    return () => {
      window.removeEventListener('mousedown', onDown);
      popEscapeLayer();
    };
  }, [open]);

  useEffect(() => {
    if (open) itemRefs.current[cursor]?.focus();
  }, [open, cursor]);

  const openMenu = () => {
    const index = STATUS_SORT_OPTIONS.findIndex((option) => option.key === sortKey);
    setCursor(Math.max(0, index));
    setOpen(true);
  };

  const pick = (key: SortKey) => {
    onSort(key);
    setOpen(false);
    triggerRef.current?.focus();
  };

  const onMenuKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    const count = STATUS_SORT_OPTIONS.length;
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      setCursor((index) => (event.key === 'ArrowDown' ? (index + 1) % count : (index - 1 + count) % count));
    } else if (event.key === 'Home' || event.key === 'End') {
      event.preventDefault();
      setCursor(event.key === 'Home' ? 0 : count - 1);
    } else if (event.key === 'Tab') {
      setOpen(false);
    }
  };

  const ariaSort = active ? (sortDir === 'desc' ? 'descending' : 'ascending') : 'none';

  return (
    <th
      ref={rootRef}
      className={`lead-table__status-header${open ? ' is-open' : ''}`}
      aria-sort={ariaSort}
    >
      <button
        ref={triggerRef}
        type="button"
        className="tbl__sort"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={active ? `Status, sorted by ${active.label}. Sort options` : 'Status. Sort options'}
        title={active ? `Sorted by ${active.label}` : 'Sort by relationship, assignee or outreach'}
        onClick={(event) => {
          event.stopPropagation();
          if (open) setOpen(false);
          else openMenu();
        }}
        onKeyDown={(event) => {
          if (!open && (event.key === 'ArrowDown' || event.key === 'ArrowUp')) {
            event.preventDefault();
            openMenu();
          }
        }}
        data-testid="lead-status-sort"
      >
        <span>Status</span>
        {active
          ? <Icon name={sortDir === 'desc' ? 'down' : 'up'} size={10} />
          : <Icon name="chevdown" size={10} />}
      </button>
      {open && (
        <div
          className="filter-menu lead-table__status-menu"
          role="menu"
          aria-label="Sort the Status column by"
          onKeyDown={onMenuKeyDown}
        >
          {STATUS_SORT_OPTIONS.map((option, index) => {
            const checked = option.key === sortKey;
            return (
              <button
                key={option.key}
                ref={(el) => {
                  itemRefs.current[index] = el;
                }}
                type="button"
                role="menuitemradio"
                aria-checked={checked}
                tabIndex={index === cursor ? 0 : -1}
                className={`filter-menu__item${checked ? ' is-selected' : ''}${index === cursor ? ' is-focused' : ''}`}
                onMouseEnter={() => setCursor(index)}
                onClick={(event) => {
                  event.stopPropagation();
                  pick(option.key);
                }}
              >
                {option.label}
                {checked && <Icon name={sortDir === 'desc' ? 'down' : 'up'} size={11} />}
              </button>
            );
          })}
        </div>
      )}
    </th>
  );
}
