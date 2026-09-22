import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent } from 'react';
import { Icon } from '../Icon';

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
 * Keyboard (WCAG 2.1.1): ArrowDown/ArrowUp/Enter/Space on the trigger open the
 * menu; arrows, Home and End move the active option; Enter/Space toggle it
 * without closing (multi-select); Escape closes and returns focus to the
 * trigger; Tab closes and lets focus continue from the trigger. Outside-click
 * closes. The trigger mirrors the active option through
 * aria-activedescendant while DOM focus rides the roving-tabindex option.
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
  const menuId = useId();
  const [open, setOpen] = useState(false);
  const [focusIdx, setFocusIdx] = useState(0);
  const rootRef = useRef<HTMLDivElement>(null);
  const btnRef = useRef<HTMLButtonElement>(null);
  const optionRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const selectedSet = useMemo(() => new Set(selected), [selected]);
  const active = selected.length > 0;
  const display = selected.length === 0
    ? allLabel
    : selected.length === 1
      ? options.find((option) => option.value === selected[0])?.label ?? selected[0]
      : formatCount?.(selected.length) ?? `${selected.length} selected`;

  useEffect(() => {
    if (!open) return;
    const onDown = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: globalThis.KeyboardEvent) => {
      if (event.key === 'Escape') {
        setOpen(false);
        btnRef.current?.focus();
      }
    };
    window.addEventListener('mousedown', onDown);
    window.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('mousedown', onDown);
      window.removeEventListener('keydown', onKey);
    };
  }, [open]);

  const itemCount = options.length + 1;
  // Options can shrink under an open menu (the tenant footprint resolving);
  // keep the active index on a row that still exists.
  const activeIdx = Math.min(focusIdx, itemCount - 1);
  const activeOptionId = `${menuId}-option-${activeIdx}`;

  useEffect(() => {
    if (!open) return;
    optionRefs.current[activeIdx]?.focus();
  }, [activeIdx, open]);

  // The active option is seeded once, when the menu opens. The analytics
  // original re-seeded it in an effect keyed on the selection, so every toggle
  // yanked a keyboard user back up to the first selected option.
  const openMenu = () => {
    const selectedIdx = options.findIndex((option) => selectedSet.has(option.value));
    setFocusIdx(selectedIdx >= 0 ? selectedIdx + 1 : 0);
    setOpen(true);
  };

  const moveActive = (step: 1 | -1) => {
    setFocusIdx((activeIdx + step + itemCount) % itemCount);
  };

  const pickIndex = (idx: number) => {
    if (idx === 0) {
      onChange([]);
      return;
    }
    const option = options[idx - 1];
    if (option) onChange(toggleValue(selected, option.value));
  };

  const onTriggerKeyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      if (!open) {
        openMenu();
        return;
      }
      moveActive(event.key === 'ArrowDown' ? 1 : -1);
    } else if (event.key === 'Home' && open) {
      event.preventDefault();
      setFocusIdx(0);
    } else if (event.key === 'End' && open) {
      event.preventDefault();
      setFocusIdx(itemCount - 1);
    } else if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      if (!open) {
        openMenu();
        return;
      }
      pickIndex(activeIdx);
    } else if (event.key === 'Escape' && open) {
      event.preventDefault();
      setOpen(false);
    } else if (event.key === 'Tab' && open) {
      setOpen(false);
    }
  };

  const onOptionKeyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      moveActive(event.key === 'ArrowDown' ? 1 : -1);
    } else if (event.key === 'Home') {
      event.preventDefault();
      setFocusIdx(0);
    } else if (event.key === 'End') {
      event.preventDefault();
      setFocusIdx(itemCount - 1);
    } else if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      pickIndex(activeIdx);
    } else if (event.key === 'Escape') {
      event.preventDefault();
      setOpen(false);
      btnRef.current?.focus();
    } else if (event.key === 'Tab') {
      // No preventDefault: hand focus back to the trigger and let the browser
      // carry Tab / Shift+Tab on from there. Closing alone would unmount the
      // focused option and leave the next focus target browser-defined.
      setOpen(false);
      btnRef.current?.focus();
    }
  };

  return (
    <div ref={rootRef} className="filter-root">
      <button
        ref={btnRef}
        type="button"
        className={`filter ${active ? 'is-active' : ''}`}
        onClick={() => (open ? setOpen(false) : openMenu())}
        onKeyDown={onTriggerKeyDown}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        aria-activedescendant={open ? activeOptionId : undefined}
        aria-label={`${label}: ${display}`}
      >
        <span className="filter__label">{label}</span>
        <span className="filter__value">{display}</span>
        <Icon name="chevdown" size={11} />
      </button>
      {open && (
        <ul id={menuId} className="filter-menu filter-menu--multi" role="listbox" aria-label={label} aria-multiselectable="true">
          <li role="presentation">
            <button
              ref={(node) => {
                optionRefs.current[0] = node;
              }}
              id={`${menuId}-option-0`}
              type="button"
              role="option"
              aria-selected={!active}
              tabIndex={activeIdx === 0 ? 0 : -1}
              className={`filter-menu__item${!active ? ' is-selected' : ''}${activeIdx === 0 ? ' is-focused' : ''}`}
              onMouseEnter={() => setFocusIdx(0)}
              onKeyDown={onOptionKeyDown}
              onClick={() => onChange([])}
            >
              {allLabel}
              {!active && <Icon name="check" size={11} />}
            </button>
          </li>
          {options.map((option, idx) => {
            const selectedOption = selectedSet.has(option.value);
            return (
              <li key={option.value} role="presentation">
                <button
                  ref={(node) => {
                    optionRefs.current[idx + 1] = node;
                  }}
                  id={`${menuId}-option-${idx + 1}`}
                  type="button"
                  role="option"
                  aria-selected={selectedOption}
                  tabIndex={activeIdx === idx + 1 ? 0 : -1}
                  className={`filter-menu__item${selectedOption ? ' is-selected' : ''}${activeIdx === idx + 1 ? ' is-focused' : ''}`}
                  onMouseEnter={() => setFocusIdx(idx + 1)}
                  onKeyDown={onOptionKeyDown}
                  onClick={() => onChange(toggleValue(selected, option.value))}
                >
                  {option.label}
                  {selectedOption && <Icon name="check" size={11} />}
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
