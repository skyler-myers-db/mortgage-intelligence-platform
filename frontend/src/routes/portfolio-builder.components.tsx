import { useState } from 'react';
import { Icon } from '../components/Icon';
import type { FootprintState } from './portfolio-builder.logic';
import { stateLabel } from './portfolio-builder.logic';

export function StateMultiSelect({
  label,
  allLabel,
  states,
  value,
  onChange,
}: {
  label: string;
  allLabel: string;
  states: ReadonlyArray<FootprintState>;
  value: string[];
  onChange: (next: string[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const active = value.length > 0;
  const display = !active
    ? allLabel
    : value.length === 1
      ? stateLabel(value[0], states)
      : `${value.length} states`;
  const toggleState = (code: string) => {
    const next = value.includes(code)
      ? value.filter((state) => state !== code)
      : [...value, code];
    onChange(next.length === states.length ? [] : next);
  };

  return (
    <div className="filter-root">
      <button
        type="button"
        className={`filter ${active ? 'is-active' : ''}`}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={`${label}: ${display}`}
        onClick={() => setOpen((next) => !next)}
      >
        <span className="filter__label">{label}</span>
        <span className="filter__value">{display}</span>
        <Icon name="chevdown" size={11} />
      </button>
      {open && (
        <ul className="filter-menu" role="listbox" aria-label={label}>
          <li
            role="option"
            aria-selected={!active}
            className={`filter-menu__item${!active ? ' is-selected' : ''}`}
            onClick={() => {
              onChange([]);
              setOpen(false);
            }}
          >
            {allLabel}
            {!active && <Icon name="check" size={11} />}
          </li>
          {states.map((state) => {
            const selected = value.includes(state.state_code);
            return (
              <li
                key={state.state_code}
                role="option"
                aria-selected={selected}
                className={`filter-menu__item${selected ? ' is-selected' : ''}`}
                onClick={() => toggleState(state.state_code)}
              >
                {state.state_name}
                {selected && <Icon name="check" size={11} />}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
