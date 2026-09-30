import { QueuePager } from '../components/mortgage/QueuePager';
import type { QueueContext } from '../lib/queueContext';
import { borrowerPath } from '../lib/routeMeta';

/**
 * Borrower 360's "3 of 23" pager (audit 2026-09-21 `shell-04`): the shared
 * components/mortgage/QueuePager stepping to the neighbouring dossiers. The
 * pager's contract (ranked order, J / K scope, no neighbour read ahead of
 * time, nothing rendered without a queue) lives there.
 */
export function BorrowerQueuePager({ borrowerId, queue }: { borrowerId: string; queue: QueueContext | null }) {
  return <QueuePager borrowerId={borrowerId} queue={queue} pathFor={borrowerPath} />;
}
