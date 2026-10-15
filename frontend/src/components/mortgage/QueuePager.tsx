import { useRef } from 'react';
import { useNavigate } from 'react-router';
import { Icon } from '../Icon';
import { Tooltip } from '../ui/Tooltip';
import { formatCount } from '../../lib/formatters';
import { ariaKeyShortcuts } from '../../lib/keymap';
import { useSingleKeyShortcuts } from '../../lib/keymapPreference';
import { MAX_QUEUE_CONTEXT_IDS, type QueueContext } from '../../lib/queueContext';
import { queuePosition } from '../../lib/queuePosition';
import { usePagerHotkeys } from '../../lib/usePagerHotkeys';
import './QueuePager.css';

export interface QueuePagerProps {
  borrowerId: string;
  queue: QueueContext | null;
  /**
   * Where a neighbour opens: `borrowerPath` on Borrower 360, `offerPath` on
   * the Offer Orchestrator. Passed in rather than imported from routeMeta so
   * this module stays out of the queueContext chunk-hoist trap
   * (lib/queueContext.ts header).
   */
  pathFor: (borrowerId: string) => string;
  /** J / K are live only while true (default). Off while a form or a write owns the page. */
  hotkeys?: boolean;
  /** Previous / Next are disabled (a decision is on the wire); the keys go off too. */
  disabled?: boolean;
}

/**
 * "3 of 23" pager for a borrower surface opened from the Lead Queue (audit
 * 2026-09-21 `shell-04`, `flow-09`): Previous / Next buttons plus K / J keys
 * (lib/usePagerHotkeys) step through the queue's ranked masked ids, carrying
 * the same queue context so the breadcrumbs keep pointing at the exact
 * filtered queue.
 *
 * The order is the queue's rank order and "of N" counts the ranked rows it
 * loaded (its footer's "Showing N ranked borrowers"), so the pager says
 * "ranked": a column sort on the queue reorders only its own table, and
 * more borrowers may match than the queue loaded.
 *
 * Opening a dossier or an offer writes audit rows (VIEW_BORROWER, and on
 * Offer RECOMMEND_OFFER and DRAFT_OUTREACH), so nothing here prefetches,
 * preloads or hovers the neighbouring borrowers: a neighbour is read only
 * when the reviewer actually moves to it. The buttons carry no
 * onMouseEnter / onFocus data work.
 *
 * Renders nothing when the page has no queue (a search result, a pasted
 * link outside the last queue). New BEM block `.queue-pager`, composed from
 * the prototype `.btn` (design_files/index.html) and token vocabulary only.
 *
 * J / K are keymap bindings in the `queue-pager` scope (wow-power-4), so the
 * `?` sheet lists them and the Console's single-key switch turns them off;
 * the buttons name them in a Tooltip and aria-keyshortcuts only while they
 * work. The published queue keeps at most MAX_QUEUE_CONTEXT_IDS ranked ids:
 * a queue that reached the cap reads "N of the first <cap> ranked", since
 * more borrowers may follow in the Lead Queue than the pager walks.
 */
export function QueuePager({ borrowerId, queue, pathFor, hotkeys = true, disabled = false }: QueuePagerProps) {
  const navigate = useNavigate();
  const navRef = useRef<HTMLElement | null>(null);
  const position = queue ? queuePosition(queue, borrowerId) : null;
  const go = (id: string | null) => {
    if (!id || !queue || disabled) return null;
    return () => navigate(pathFor(id), { state: { queue } });
  };
  const previous = go(position?.previous ?? null);
  const next = go(position?.next ?? null);
  // J / K are live only while focus is inside the page's <main>, and only
  // while the caller allows them (never inside an open form or a write).
  const keysLive = hotkeys && !disabled;
  usePagerHotkeys({ previous: keysLive ? previous : null, next: keysLive ? next : null }, navRef);
  // The keys are named only while they work: single-character keys are off
  // with the Console's switch (WCAG 2.1.4).
  const [singleKeysOn] = useSingleKeyShortcuts();
  const named = keysLive && singleKeysOn;

  if (!queue || !position) return null;
  // The published queue stops at the cap (lib/queueContext): say so.
  const capped = queue.ids.length >= MAX_QUEUE_CONTEXT_IDS;
  return (
    <nav ref={navRef} className="queue-pager" data-rum-target="pager" aria-label="Lead queue position">
      <span className="queue-pager__position">
        <span className="mono num">{formatCount(position.position)}</span>
        {capped ? ' of the first ' : ' of '}
        <span className="mono num">{formatCount(capped ? MAX_QUEUE_CONTEXT_IDS : position.total)}</span>
        <span className="queue-pager__scope">{queue.label ? ` ranked in ${queue.label}` : ' ranked in the Lead Queue'}</span>
      </span>
      <Tooltip content="Previous borrower" shortcut={named ? 'K' : undefined}>
        <button
          type="button"
          className="btn btn--sm queue-pager__step"
          onClick={previous ?? undefined}
          disabled={!previous}
          aria-keyshortcuts={named ? ariaKeyShortcuts(['k']) : undefined}
        >
          <Icon name="chevright" size={12} className="queue-pager__prev-icon" />
          Previous
        </button>
      </Tooltip>
      <Tooltip content="Next borrower" shortcut={named ? 'J' : undefined}>
        <button
          type="button"
          className="btn btn--sm queue-pager__step"
          onClick={next ?? undefined}
          disabled={!next}
          aria-keyshortcuts={named ? ariaKeyShortcuts(['j']) : undefined}
        >
          Next
          <Icon name="chevright" size={12} />
        </button>
      </Tooltip>
    </nav>
  );
}
