import { useRef, useState } from 'react';
import { useExitPresence } from '../../hooks/useExitRetained';
import { formatCount } from '../../lib/formatters';
import { Icon } from '../Icon';
import { eventOptionIndex } from './eventOptionIndex';
import { useFilterFacetCounts } from './FilterSelect.facets';
import { useListboxNavigation } from './useListboxNavigation';
import { menuSpaceStyle, useMenuPlacement } from './useMenuPlacement';

/**
 * FilterSelect — presenter-friendly replacement for the old cycle-on-click
 * filter chip. Matches the prototype's `.filter` / `.filter__label` /
 * `.filter__value` BEM; the menu uses a `.filter-menu` class (defined in
 * components.css) anchored below the trigger, or above it
 * (`.filter-menu--up`) when it would not fit below the viewport edge.
 *
 * ARIA pattern: the APG select-only combobox (2026-09-21 audit a11y-02 /
 * tables-06). The trigger keeps DOM focus and is `role="combobox"`, so it is
 * the element that carries `aria-activedescendant`; every option has an id,
 * so arrowing through them is announced. Keyboard (useListboxNavigation):
 * ArrowUp/Down, Home/End, typeahead on the option labels, Enter/Space select,
 * Escape closes (topmost Escape layer) and keeps focus on the trigger, Tab
 * and focus leaving the filter close. Outside-click closes. "Active" = value
 * differs from the first option, so the accent highlight still means
 * "non-default".
 *
 * Option counts (audit tables-06): inside a FilterFacetContext provider an
 * open menu may show a count beside each option. The count is aria-hidden
 * and linked by aria-describedby, so each option's accessible name stays its
 * label; the listbox is aria-busy while counts load, and a failed read shows
 * a dash with a polite "Counts unavailable". With no provider the DOM is
 * unchanged. deviation:filter-menu-facet-counts
 *
 * Exit (motion-01 item 2e, deviation:menu-exit-motion): a closed menu stays
 * mounted, `is-closing`, aria-hidden and inert, until its fade ends
 * (useExitPresence); its id and aria-activedescendant exist only while open.
 * The listbox and options are `div`s with roles (a11y-05: a `ul`/`li` may
 * not take interactive roles), and option clicks and hover are delegated to
 * the listbox through each option's index.
 */


interface FilterSelectProps {
  label: string;
  value: string;
  options: string[];
  onChange: (next: string) => void;
}

export function FilterSelect({ label, value, options, onChange }: FilterSelectProps) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const btnRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const selectedIndex = Math.max(0, options.indexOf(value));
  const listbox = useListboxNavigation({
    count: options.length,
    open,
    onOpenChange: setOpen,
    onCommit: (index) => {
      const next = options[index];
      if (next !== undefined) onChange(next);
    },
    rootRef,
    returnFocusRef: btnRef,
    labels: options,
    initialIndex: selectedIndex,
  });
  const { present, closing } = useExitPresence(open, menuRef);
  // Measured while the menu is present, so a closing menu keeps its side
  // and its cap through the exit instead of jumping below.
  const menuLayout = useMenuPlacement(present, btnRef, menuRef);
  const counts = useFilterFacetCounts(label, open);

  const active = value !== options[0];

  const pick = (index: number) => {
    const next = options[index];
    if (next !== undefined) onChange(next);
    listbox.close(true);
  };

  return (
    <div ref={rootRef} className="filter-root" data-rum-target="filter">
      <button
        ref={btnRef}
        type="button"
        role="combobox"
        className={`filter ${active ? 'is-active' : ''}`}
        onClick={() => (open ? listbox.close() : listbox.openAt(selectedIndex))}
        onKeyDown={listbox.onKeyDown}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listbox.listboxId : undefined}
        aria-activedescendant={listbox.activeDescendant}
        aria-label={`${label}: ${value}`}
      >
        <span className="filter__label">{label}</span>
        <span className="filter__value">{value}</span>
        <Icon name="chevdown" size={11} />
      </button>
      {present && (
        <div
          ref={menuRef}
          id={open ? listbox.listboxId : undefined}
          className={`filter-menu${menuLayout.placement === 'above' ? ' filter-menu--up' : ''}${closing ? ' is-closing' : ''}`}
          style={menuSpaceStyle(menuLayout)}
          role="listbox"
          aria-label={label}
          aria-busy={counts?.status === 'loading' || undefined}
          aria-hidden={closing || undefined}
          inert={closing}
          tabIndex={-1}
          // Keep DOM focus on the combobox while the pointer picks an option.
          onMouseDown={(event) => event.preventDefault()}
          onMouseMove={(event) => {
            const index = eventOptionIndex(event);
            if (index !== null) listbox.setActiveIndex(index);
          }}
          onClick={(event) => {
            const index = eventOptionIndex(event);
            if (index !== null) pick(index);
          }}
          onKeyDown={listbox.onKeyDown}
        >
          {options.map((opt, i) => {
            const selected = opt === value;
            const focused = i === listbox.activeIndex;
            const optionId = listbox.optionId(i);
            const count = counts?.status === 'ready' ? counts.countFor(opt) : null;
            const countText = count !== null ? formatCount(count) : counts?.status === 'error' ? '—' : null;
            return (
              <div
                key={opt}
                id={optionId}
                role="option"
                aria-selected={selected}
                aria-describedby={count !== null ? `${optionId}-count` : undefined}
                data-option-index={i}
                className={`filter-menu__item${selected ? ' is-selected' : ''}${focused ? ' is-focused' : ''}`}
              >
                {opt}
                {countText !== null && (
                  <span className="filter-menu__count" id={`${optionId}-count`} aria-hidden="true">{countText}</span>
                )}
                {selected && <Icon name="check" size={11} />}
              </div>
            );
          })}
        </div>
      )}
      {open && counts && (
        <span className="sr-only" role="status">{counts.status === 'error' ? 'Counts unavailable' : ''}</span>
      )}
    </div>
  );
}
