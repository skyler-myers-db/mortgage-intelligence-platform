import { useRef } from 'react';
import { useNavigate } from 'react-router';
import { Icon } from '../components/Icon';
import type { QueueContext } from '../lib/queueContext';
import { queuePosition } from '../lib/queuePosition';
import { borrowerPath } from '../lib/routeMeta';
import { usePagerHotkeys } from '../lib/usePagerHotkeys';
import './borrower-360.pager.css';

/**
 * "3 of 23" pager for a dossier opened from the Lead Queue (audit 2026-09-21
 * `shell-04`): Previous / Next buttons plus K / J keys (lib/usePagerHotkeys)
 * step through the queue's ranked masked ids, carrying the same queue context
 * so the breadcrumbs keep pointing at the exact filtered queue.
 *
 * Opening a dossier writes a VIEW_BORROWER audit row, so nothing here
 * prefetches, preloads or hovers the neighbouring borrowers: a neighbour is
 * read only when the reviewer actually moves to it.
 *
 * Renders nothing when the dossier has no queue (a search result, a pasted
 * link outside the last queue). New BEM block `.queue-pager`, composed from
 * the prototype `.btn` (design_files/index.html) and token vocabulary only.
 */
export function BorrowerQueuePager({ borrowerId, queue }: { borrowerId: string; queue: QueueContext | null }) {
  const navigate = useNavigate();
  const navRef = useRef<HTMLElement | null>(null);
  const position = queue ? queuePosition(queue, borrowerId) : null;
  const go = (id: string | null) => {
    if (!id || !queue) return null;
    return () => navigate(borrowerPath(id), { state: { queue } });
  };
  const previous = go(position?.previous ?? null);
  const next = go(position?.next ?? null);
  // J / K are live only while focus is inside the dossier's <main>.
  usePagerHotkeys({ previous, next }, navRef);

  if (!queue || !position) return null;
  return (
    <nav ref={navRef} className="queue-pager" aria-label="Lead queue position">
      <span className="queue-pager__position">
        <span className="mono num">{position.position.toLocaleString('en-US')}</span>
        {' of '}
        <span className="mono num">{position.total.toLocaleString('en-US')}</span>
        <span className="queue-pager__scope">{queue.label ? ` in ${queue.label}` : ' in the Lead Queue'}</span>
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
