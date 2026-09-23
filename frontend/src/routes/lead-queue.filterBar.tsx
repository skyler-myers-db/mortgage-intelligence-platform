import { useId, useState, type ReactNode } from 'react';
import { Icon } from '../components/Icon';
import type { LeadQueueFilterChip } from './lead-queue.activeFilters';
import './lead-queue.css';

/**
 * The Lead Queue filter bar (audit tables-06, L part). The prototype's core
 * pills stay visible (design_files/Module 0 Prototype.html:1772-1778 is a
 * `.filter-row` of FilterSelect pills; the queue's core set is state,
 * segment, relationship, product and approval) and every other pill moves
 * behind a "More filters" disclosure.
 *
 * Declared prototype deviation: the disclosure is a button that expands an
 * inline panel BELOW the pill row, not a floating popover (the prototype has
 * neither: its builder shows all six pills). Inline keeps it in flow, so no
 * positioning library is needed and nothing overlaps the table.
 */
export function LeadQueueFilterBar({
  core,
  more,
  moreActiveCount,
  filtersActive,
  onClearAll,
}: {
  core: ReactNode;
  more: ReactNode;
  moreActiveCount: number;
  filtersActive: boolean;
  onClearAll: () => void;
}) {
  const [open, setOpen] = useState(false);
  const panelId = useId();
  return (
    <>
      <div className="filter-row filter-row--lead-queue" role="group" aria-label="Queue filters">
        {core}
        <button
          type="button"
          className={`filter lead-queue-filters__more${moreActiveCount > 0 ? ' is-active' : ''}`}
          aria-expanded={open}
          aria-controls={panelId}
          aria-label={moreActiveCount > 0 ? `More filters (${moreActiveCount} active)` : 'More filters'}
          onClick={() => setOpen((value) => !value)}
          data-testid="lead-queue-more-filters"
        >
          <Icon name="filter" size={11} />
          <span className="filter__label">More filters</span>
          {moreActiveCount > 0 && <span className="filter__value">{moreActiveCount}</span>}
          <Icon name={open ? 'up' : 'chevdown'} size={11} />
        </button>
        <span className="filter-row__spacer" />
        <button
          type="button"
          className="btn btn--ghost btn--sm"
          disabled={!filtersActive}
          aria-disabled={!filtersActive}
          onClick={onClearAll}
          data-testid="lead-queue-clear-all"
        >
          Clear all
        </button>
      </div>
      <div
        id={panelId}
        className="filter-row lead-queue-filters__panel"
        role="group"
        aria-label="More queue filters"
        hidden={!open}
      >
        {more}
      </div>
    </>
  );
}

/**
 * Hero chip row: one removable `.filter` chip per active non-core filter.
 * `.filter__remove` is a BEM extension of the prototype's `.filter` pill
 * (design_files/index.html:826-838 has no dismiss element); the pill's own
 * label / value typography is unchanged. The row WRAPS
 * (`.page-filter-chips--lead-queue`): the shared row is one nowrap line
 * justified to the end, whose start-edge overflow cannot be scrolled, so a
 * third active filter clipped the first chips and their Remove buttons.
 */
export function LeadQueueHeroFilterChips({
  chips,
  onRemove,
}: {
  chips: readonly LeadQueueFilterChip[];
  onRemove: (chip: LeadQueueFilterChip) => void;
}) {
  const empty = chips.length === 0;
  return (
    <div
      className={`page-filter-chips page-filter-chips--lead-queue ${empty ? 'is-empty' : ''}`}
      role={empty ? undefined : 'group'}
      aria-label={empty ? undefined : 'Active filters'}
      aria-hidden={empty || undefined}
      data-testid="lead-queue-active-filters"
    >
      {chips.map((chip) => (
        <span key={chip.key} className="filter is-active filter--removable" title={`${chip.label}: ${chip.value}`}>
          <span className="filter__label">{chip.label}</span>
          <span className="filter__value">{chip.value}</span>
          <button
            type="button"
            className="filter__remove"
            aria-label={`Remove ${chip.label}: ${chip.value} filter`}
            onClick={() => onRemove(chip)}
          >
            <Icon name="cross" size={9} />
          </button>
        </span>
      ))}
    </div>
  );
}
