import { useEffect, useMemo, useRef, useState } from 'react';
import { Icon } from '../Icon';
import { useListboxNavigation } from './useListboxNavigation';
import { useMenuPlacement } from './useMenuPlacement';

/**
 * MultiFilterSelect — the multi-select sibling of FilterSelect. Same prototype
 * BEM (`.filter` / `.filter__label` / `.filter__value` trigger, `.filter-menu`
 * / `.filter-menu__item` listbox); `.filter-menu--multi` marks the
 * multi-selectable variant.
 *
 * Moved here from routes/analytics.sections.tsx (2026-09-21 audit a11y-v1) so
 * the Portfolio Builder state picker can share the one focus-managed listbox
 * instead of carrying a mouse-only copy.
 *
 * ARIA pattern: the APG multi-select listbox behind a button that opens it
 * (2026-09-21 audit a11y-02 / stack-05). The menu button keeps
 * `aria-haspopup` / `aria-expanded` / `aria-controls`; on open, DOM focus
 * moves to the `role="listbox"` itself, which carries `aria-activedescendant`
 * (the element that really holds focus, not the trigger). Keyboard
 * (useListboxNavigation, WCAG 2.1.1): ArrowDown/ArrowUp/Enter/Space on the
 * trigger open the menu; arrows, Home and End move the active option;
 * typeahead matches option labels; Enter/Space toggle it without closing;
 * Escape closes (topmost Escape layer) and returns focus to the trigger; Tab
 * closes and lets focus continue from the trigger. Outside-click closes.
 */

export type MultiFilterSelectOption<T extends string> = {
  label: string;
  value: T;
};

interface MultiFilterSelectProps<T extends string> {
  label: string;
  allLabel: string;
  selected: readonly T[];
  options: ReadonlyArray<MultiFilterSelectOption<T>>;
  onChange: (next: T[]) => void;
  /** Trigger text when more than one option is selected. Default: "N selected". */
  formatCount?: (count: number) => string;
}

function toggleValue<T extends string>(selected: readonly T[], value: T): T[] {
  return selected.includes(value)
    ? selected.filter((item) => item !== value)
    : [...selected, value];
}

export function MultiFilterSelect<T extends string>({
  label,
  allLabel,
  selected,
  options,
  onChange,
  formatCount,
}: MultiFilterSelectProps<T>) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const btnRef = useRef<HTMLButtonElement>(null);
  const listRef = useRef<HTMLUListElement>(null);
  const selectedSet = useMemo(() => new Set(selected), [selected]);
  const labels = useMemo(() => [allLabel, ...options.map((option) => option.label)], [allLabel, options]);
  const active = selected.length > 0;
  const display = selected.length === 0
    ? allLabel
    : selected.length === 1
      ? options.find((option) => option.value === selected[0])?.label ?? selected[0]
      : formatCount?.(selected.length) ?? `${selected.length} selected`;

  // Index 0 is the "all" row. The active option is seeded once, when the
  // menu opens (on the first selected option), so a toggle never yanks a
  // keyboard user back up the list.
  const firstSelected = options.findIndex((option) => selectedSet.has(option.value));
  const initialIndex = firstSelected >= 0 ? firstSelected + 1 : 0;

  const pickIndex = (idx: number) => {
    if (idx === 0) {
      onChange([]);
      return;
    }
    const option = options[idx - 1];
    if (option) onChange(toggleValue(selected, option.value));
  };

  const listbox = useListboxNavigation({
    count: options.length + 1,
    open,
    onOpenChange: setOpen,
    onCommit: pickIndex,
    rootRef,
    returnFocusRef: btnRef,
    labels,
    multiple: true,
    initialIndex,
  });
  const placement = useMenuPlacement(open, btnRef, listRef);

  // The listbox owns focus while it is open.
  useEffect(() => {
    if (open) listRef.current?.focus();
  }, [open]);

  const renderOption = (idx: number, text: string, isSelected: boolean) => (
    <li
      key={idx === 0 ? '__all__' : options[idx - 1]?.value}
      id={listbox.optionId(idx)}
      role="option"
      aria-selected={isSelected}
      className={`filter-menu__item${isSelected ? ' is-selected' : ''}${listbox.activeIndex === idx ? ' is-focused' : ''}`}
      onMouseEnter={() => listbox.setActiveIndex(idx)}
      onClick={() => {
        listbox.setActiveIndex(idx);
        pickIndex(idx);
      }}
    >
      {text}
      {isSelected && <Icon name="check" size={11} />}
    </li>
  );

  return (
    <div ref={rootRef} className="filter-root">
      <button
        ref={btnRef}
        type="button"
        className={`filter ${active ? 'is-active' : ''}`}
        onClick={() => (open ? listbox.close() : listbox.openAt(initialIndex))}
        onKeyDown={open ? undefined : listbox.onKeyDown}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listbox.listboxId : undefined}
        aria-label={`${label}: ${display}`}
      >
        <span className="filter__label">{label}</span>
        <span className="filter__value">{display}</span>
        <Icon name="chevdown" size={11} />
      </button>
      {open && (
        <ul
          ref={listRef}
          id={listbox.listboxId}
          className={`filter-menu filter-menu--multi${placement === 'above' ? ' filter-menu--up' : ''}`}
          role="listbox"
          aria-label={label}
          aria-multiselectable="true"
          aria-activedescendant={listbox.activeDescendant}
          // In the tab order while open, so the scrolling menu is keyboard
          // reachable (WCAG 2.1.1; axe scrollable-region-focusable).
          tabIndex={0}
          onKeyDown={listbox.onKeyDown}
        >
          {renderOption(0, allLabel, !active)}
          {options.map((option, idx) => renderOption(idx + 1, option.label, selectedSet.has(option.value)))}
        </ul>
      )}
    </div>
  );
}
