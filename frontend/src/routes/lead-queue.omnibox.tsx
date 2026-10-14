/**
 * The Lead Queue filter omnibox (wow-power-6, merges wow-ai-6; W5e item 3c).
 * deviation:filter-omnibox
 *
 * A compact `.filter` pill, "Filter…" with an F keycap, first in the filter
 * row (the prototype's pill family, design_files/index.html:822-838; the
 * popover borrows the `.cmdk` / `.filter-menu` vocabulary). Activating it, or
 * pressing F on the queue, opens a popover anchored to the pill where a
 * person types a cohort ("TX IL heloc aged>7"), sees each reading and the
 * live match count, and applies it with Enter.
 *
 * Only the pill, its keycap and the F binding ride the route chunk: the
 * popover (the combobox, the chips, the count status) and the grammar load
 * together on the FIRST open (components/mortgage/LeadQueueOmniboxPopover),
 * never on mount; this module hands it the count read.
 * The F binding is registered in an effect, so it is released while the
 * kept-alive queue is hidden behind a dossier, and it is off in the Triage
 * deck. Nothing here reads, writes or persists the typed text.
 */
import { useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router';
import { Icon } from '../components/Icon';
import { lazyModule, useLazyModule } from '../components/mortgage/useLazyModule';
import { leadCountQueryOptions } from '../lib/apiClients/leadCount';
import { registerKeyBinding } from '../lib/keymap';
import { searchParamsCleared } from './lead-queue.activeFilters';
import { parseTriageMode, TRIAGE_MODE_PARAM } from './lead-queue.filters';
import './lead-queue.omnibox.css';

const POPOVER = lazyModule(() => import('../components/mortgage/LeadQueueOmniboxPopover'));

export const OMNIBOX_BINDING_ID = 'lead-queue-omnibox';

/** The applied URL: filters REPLACED by the reading; view, hide, sort, dir and the place kept. */
export function searchParamsWithOmniboxFilters(current: URLSearchParams, params: URLSearchParams): URLSearchParams {
  const next = searchParamsCleared(current);
  next.delete('row');
  for (const [key, value] of params) next.set(key, value);
  return next;
}

export function LeadQueueOmnibox() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [open, setOpen] = useState(false);
  const pillRef = useRef<HTMLButtonElement | null>(null);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const triageOn = parseTriageMode(searchParams.get(TRIAGE_MODE_PARAM)) !== null;
  const { module: popover, failed } = useLazyModule(POPOVER, open);

  useEffect(() => {
    if (triageOn) return undefined;
    return registerKeyBinding({
      id: OMNIBOX_BINDING_ID,
      scope: 'lead-queue',
      keys: ['f'],
      description: 'Filter leads',
      run: () => {
        setOpen(true);
      },
    });
  }, [triageOn]);

  const close = (refocus = true) => {
    setOpen(false);
    if (refocus) pillRef.current?.focus();
  };

  return (
    <div ref={rootRef} className="lead-queue-omnibox">
      <button
        ref={pillRef}
        type="button"
        className={`filter lead-queue-omnibox__pill${open ? ' is-active' : ''}`}
        aria-expanded={open}
        aria-haspopup="dialog"
        aria-keyshortcuts="F"
        aria-label="Filter leads"
        onClick={() => (open ? close() : setOpen(true))}
        data-testid="lead-queue-omnibox"
      >
        <Icon name="search" size={11} />
        <span className="lead-queue-omnibox__label" aria-hidden="true">Filter…</span>
        <kbd aria-hidden="true">F</kbd>
      </button>
      {open && (popover ? (
        <popover.LeadQueueOmniboxPopover
          rootRef={rootRef}
          countOptions={leadCountQueryOptions}
          onClose={close}
          onApply={(params) => {
            setSearchParams(searchParamsWithOmniboxFilters(searchParams, params));
            close();
          }}
        />
      ) : (
        <div className="filter-menu lead-queue-omnibox__panel" role="group" aria-label="Filter leads">
          <p className="lead-queue-omnibox__status">{failed ? 'Filter unavailable. Close and try again.' : 'Loading…'}</p>
        </div>
      ))}
    </div>
  );
}
