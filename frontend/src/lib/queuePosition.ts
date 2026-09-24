import type { QueueContext } from './queueContext';

/**
 * Where a borrower sits in the queue a dossier was opened from (audit
 * 2026-09-21 `shell-04`): the Borrower 360 pager's "3 of 23" and its
 * neighbours. Pure arithmetic over the context's ranked masked ids; it reads
 * no borrower. Kept out of lib/queueContext.ts so it ships with the lazy
 * Borrower 360 chunk.
 */
export interface QueuePosition {
  /** 1-based position of the borrower in the queue. */
  position: number;
  total: number;
  previous: string | null;
  next: string | null;
}

export function queuePosition(context: QueueContext, borrowerId: string): QueuePosition | null {
  const index = context.ids.indexOf(borrowerId);
  if (index === -1) return null;
  return {
    position: index + 1,
    total: context.ids.length,
    previous: index > 0 ? context.ids[index - 1] : null,
    next: index < context.ids.length - 1 ? context.ids[index + 1] : null,
  };
}
