import { ROUTES, borrowerPath, maskedBorrowerIdFor, resolveRouteMeta, type AppPath } from './routeMeta';
import { queueCrumbLabel, queueHref, type QueueContext, type QueueLinkState } from './queueContext';

/**
 * The topbar breadcrumb trail for a pathname (audit 2026-09-21 `shell-04`),
 * derived from the route registry. Detail pages get a trail with REAL index
 * links; every other page is a single current crumb:
 *
 *   Borrower 360     Lead Queue · IL · In the Money  /  B-0123456789ABC
 *   Offer            Lead Queue · IL  /  B-0123456789ABC  /  Offer Orchestrator
 *   Governed asset   Data estate (#data-estate)  /  Governed asset
 *
 * The Lead Queue crumb returns to the exact filtered queue the dossier was
 * opened from (lib/queueContext), else to the unfiltered queue.
 */

export interface Crumb {
  label: string;
  /** Absent on the current page. */
  to?: AppPath;
  /** Link state carried to the target (the queue context for a dossier). */
  state?: QueueLinkState;
  /** Masked ids render in the mono face, like everywhere else in the app. */
  mono?: boolean;
}

function queueCrumb(queue: QueueContext | null): Crumb {
  return { label: queueCrumbLabel(queue), to: queueHref(queue) };
}

export function breadcrumbTrail(pathname: string, queue: QueueContext | null): Crumb[] {
  const meta = resolveRouteMeta(pathname);
  const borrowerId = maskedBorrowerIdFor(pathname);
  if (meta.id === 'borrower' && borrowerId) {
    return [queueCrumb(queue), { label: borrowerId, mono: true }];
  }
  if (meta.id === 'offer' && borrowerId) {
    return [
      queueCrumb(queue),
      {
        label: borrowerId,
        to: borrowerPath(borrowerId),
        state: queue ? { queue } : undefined,
        mono: true,
      },
      { label: meta.name },
    ];
  }
  if (meta.id === 'asset') {
    // The governed-asset index is the Data estate panel on the admin page:
    // land on the panel itself (#data-estate, DataEstatePanel), not the top
    // of Admin (wave-1c follow-up #15).
    return [{ label: 'Data estate', to: `${ROUTES.admin.pattern}#data-estate` }, { label: meta.name }];
  }
  return [{ label: meta.name }];
}
