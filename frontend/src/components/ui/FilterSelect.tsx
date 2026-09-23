import { useRef, useState } from 'react';
import { Icon } from '../Icon';
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
  const menuRef = useRef<HTMLUListElement>(null);
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
  const menuLayout = useMenuPlacement(open, btnRef, menuRef);

  const active = value !== options[0];

  const pick = (index: number) => {
    const next = options[index];
    if (next !== undefined) onChange(next);
    listbox.close(true);
  };

  return (
    <div ref={rootRef} className="filter-root">
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
      {open && (
        <ul
          ref={menuRef}
          id={listbox.listboxId}
          className={`filter-menu${menuLayout.placement === 'above' ? ' filter-menu--up' : ''}`}
          style={menuSpaceStyle(menuLayout)}
          role="listbox"
          aria-label={label}
          // Keep DOM focus on the combobox while the pointer picks an option.
          onMouseDown={(event) => event.preventDefault()}
        >
          {options.map((opt, i) => {
            const selected = opt === value;
            const focused = i === listbox.activeIndex;
            return (
              <li
                key={opt}
                id={listbox.optionId(i)}
                role="option"
                aria-selected={selected}
                className={`filter-menu__item${selected ? ' is-selected' : ''}${focused ? ' is-focused' : ''}`}
                onMouseEnter={() => listbox.setActiveIndex(i)}
                onClick={() => pick(i)}
              >
                {opt}
                {selected && <Icon name="check" size={11} />}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
