import { useRef } from 'react';
import { useNavigate } from 'react-router';
import { Icon } from '../Icon';
import { formatCount } from '../../lib/formatters';
import type { QueueContext } from '../../lib/queueContext';
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

  if (!queue || !position) return null;
  return (
    <nav ref={navRef} className="queue-pager" aria-label="Lead queue position">
      <span className="queue-pager__position">
        <span className="mono num">{formatCount(position.position)}</span>
        {' of '}
        <span className="mono num">{formatCount(position.total)}</span>
        <span className="queue-pager__scope">{queue.label ? ` ranked in ${queue.label}` : ' ranked in the Lead Queue'}</span>
      </span>
      <button
        type="button"
        className="btn btn--sm queue-pager__step"
        onClick={previous ?? undefined}
        disabled={!previous}
        aria-keyshortcuts="K"
        title="Previous borrower (K)"
      >
        <Icon name="chevright" size={12} className="queue-pager__prev-icon" />
        Previous
      </button>
      <button
        type="button"
        className="btn btn--sm queue-pager__step"
        onClick={next ?? undefined}
        disabled={!next}
        aria-keyshortcuts="J"
        title="Next borrower (J)"
      >
        Next
        <Icon name="chevright" size={12} />
      </button>
    </nav>
  );
}
