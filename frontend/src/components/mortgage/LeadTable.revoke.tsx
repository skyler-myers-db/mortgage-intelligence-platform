/**
 * The Lead Queue's revoke slot (audit flow-v2; deviation:revoke-approval):
 * which borrower's revoke panel is open, from the Approved row's Revoke or
 * the in-session queue receipt's 'Revoke approval', and the panel itself,
 * loaded on the open (React.lazy: its own chunk).
 *
 * The receipt asks through a window event (REVOKE_APPROVAL_EVENT, carrying
 * the receipt's approval id), as the account menu asks for the shortcut
 * overlay: the receipt chunk (also Borrower 360's and Offer's) then imports
 * nothing of the queue, and the panel has one opener, this shell.
 *
 * The shell owns the revoke mutation and hands it, with the queue's
 * write-failure toast functions, to the panel: the lazy panel then imports
 * no module of a shared chunk (each such import split the chunk and put a new
 * name into every route's preload list in the entry chunk; W5e budget). The
 * mutation also outlives the panel: a revoke still on the wire when the
 * panel closes lands all the same (its toast, the override drop and the
 * queue refresh). A 429's Retry re-sends only while its panel is open.
 */
import { lazy, Suspense, useEffect, useState, type ReactNode } from 'react';
import type { QueryClient } from '@tanstack/react-query';
import { useRevokeApproval } from '../../lib/mutations/outreach';
import { REVOKE_APPROVAL_EVENT, type RevokeApprovalRequest } from './LeadTable.constants';
import { describeWriteFailure, toastWriteFailure, type WriteFailureToasts } from './leadWriteFailureToast';

const RevokeApprovalPanel = lazy(() => import('./RevokeApprovalPanel'));

/** The queue's write-failure toasts, handed to the lazy panel and the lazy bulk gate (W5e budget). */
export const WRITE_FAILURE_TOASTS: WriteFailureToasts = { toastWriteFailure, describeWriteFailure };

export interface LeadRevokeSlot {
  /** Open the panel for a row; `approvalId` null reads the lifecycle on open. */
  open: (borrowerId: string, approvalId?: string | null) => void;
  /** The open panel, or null. */
  panel: ReactNode;
}

function isRevokeRequest(value: unknown): value is RevokeApprovalRequest {
  if (typeof value !== 'object' || value === null) return false;
  const { borrowerId, approvalId } = value as Partial<RevokeApprovalRequest>;
  return typeof borrowerId === 'string' && typeof approvalId === 'string';
}

export function useLeadRevoke(queryClient: QueryClient): LeadRevokeSlot {
  const revoke = useRevokeApproval(queryClient);
  const [target, setTarget] = useState<{ borrowerId: string; approvalId: string | null } | null>(null);
  useEffect(() => {
    const onRequest = (event: Event) => {
      const detail = (event as CustomEvent<unknown>).detail;
      if (isRevokeRequest(detail)) setTarget({ borrowerId: detail.borrowerId, approvalId: detail.approvalId });
    };
    window.addEventListener(REVOKE_APPROVAL_EVENT, onRequest);
    return () => window.removeEventListener(REVOKE_APPROVAL_EVENT, onRequest);
  }, []);
  return {
    open: (borrowerId, approvalId = null) => setTarget({ borrowerId, approvalId }),
    panel: target && (
      <Suspense fallback={null}>
        <RevokeApprovalPanel
          key={`${target.borrowerId}:${target.approvalId ?? ''}`}
          borrowerId={target.borrowerId}
          approvalId={target.approvalId}
          revoke={revoke}
          failureDeps={WRITE_FAILURE_TOASTS}
          onClose={() => setTarget(null)}
        />
      </Suspense>
    ),
  };
}
