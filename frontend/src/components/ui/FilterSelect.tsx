import { useEffect, useRef, useState, type KeyboardEvent } from 'react';
import { Icon } from '../Icon';
import { pushEscapeLayer } from '../../lib/escapeStack';

/**
 * FilterSelect — presenter-friendly replacement for the old cycle-on-click
 * filter chip. Matches the prototype's `.filter` / `.filter__label` /
 * `.filter__value` BEM; the menu uses a `.filter-menu` class (defined in
 * components.css) positioned absolutely below the trigger.
 *
 * Keyboard: ArrowUp/Down navigate, Enter selects, Esc closes. Outside-click
 * closes. "Active" = value differs from the first option — mirrors the
 * existing behavior so the accent highlight still means "non-default".
 */

interface FilterSelectProps {
  label: string;
  value: string;
  options: string[];
  onChange: (next: string) => void;
}

export function FilterSelect({ label, value, options, onChange }: FilterSelectProps) {
  const [open, setOpen] = useState(false);
  const [focusIdx, setFocusIdx] = useState<number>(() => Math.max(0, options.indexOf(value)));
  const rootRef = useRef<HTMLDivElement>(null);
  const btnRef = useRef<HTMLButtonElement>(null);

  const active = value !== options[0];

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!rootRef.current) return;
      if (!rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    // Shared topmost-layer Escape stack (audit 2026-09-21 runtime-v2): an open
    // menu is the top layer, so Escape closes the menu and nothing beneath it.
    const popEscapeLayer = pushEscapeLayer(() => {
      setOpen(false);
      btnRef.current?.focus();
    });
    window.addEventListener('mousedown', onDown);
    return () => {
      window.removeEventListener('mousedown', onDown);
      popEscapeLayer();
    };
  }, [open]);

  useEffect(() => {
    if (open) setFocusIdx(Math.max(0, options.indexOf(value)));
  }, [open, options, value]);

  const onKeyDown = (e: KeyboardEvent<HTMLButtonElement>) => {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      if (!open) {
        setOpen(true);
        return;
      }
      setFocusIdx((i) => {
        const n = options.length;
        return e.key === 'ArrowDown' ? (i + 1) % n : (i - 1 + n) % n;
      });
    } else if (e.key === 'Enter') {
      if (open) {
        e.preventDefault();
        onChange(options[focusIdx]);
        setOpen(false);
      }
    }
  };

  const pick = (opt: string) => {
    onChange(opt);
    setOpen(false);
    btnRef.current?.focus();
  };

  return (
    <div ref={rootRef} className="filter-root">
      <button
        ref={btnRef}
        type="button"
        className={`filter ${active ? 'is-active' : ''}`}
        onClick={() => setOpen((o) => !o)}
        onKeyDown={onKeyDown}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={`${label}: ${value}`}
      >
        <span className="filter__label">{label}</span>
        <span className="filter__value">{value}</span>
        <Icon name="chevdown" size={11} />
      </button>
      {open && (
        <ul className="filter-menu" role="listbox" aria-label={label}>
          {options.map((opt, i) => {
            const selected = opt === value;
            const focused = i === focusIdx;
            return (
              <li
                key={opt}
                role="option"
                aria-selected={selected}
                className={`filter-menu__item${selected ? ' is-selected' : ''}${focused ? ' is-focused' : ''}`}
                onMouseEnter={() => setFocusIdx(i)}
                onClick={() => pick(opt)}
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
