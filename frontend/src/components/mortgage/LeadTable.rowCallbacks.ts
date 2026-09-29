import { useLayoutEffect, useRef, useState } from 'react';
import type { LeadSummary } from '../../types';

/** What a ranked-borrower row asks the table to do. */
export interface LeadRowCallbacks {
  onToggleRow: (lead: LeadSummary, isOpen: boolean) => void;
  onToggleSelect: (borrowerId: string, range: boolean) => void;
  onApprove: (borrowerId: string) => void;
  onReject: (borrowerId: string) => void;
  onOpenDisposition: (borrowerId: string) => void;
  onAssignmentUpdate: (borrowerId: string, update: Partial<LeadSummary>) => void;
  /** Focus landed on a control in the row: it becomes the cursor row (no scroll, no announcement). */
  onFocusRow: (borrowerId: string) => void;
}

/**
 * The row callbacks, with ONE identity for the table's lifetime (audit
 * runtime-04 slice 3). Each call runs the shell's handler from its latest
 * render, synced in a layout effect (so before any click or key reaches a
 * row). The shell's handlers close over the expanded row, the open review
 * and the selection, so passing them straight down gave every row a new
 * prop on each expand, and a compiled LeadTableRow re-derived all of its
 * cells. With these, a row expand or an unrelated AppContext change
 * re-renders only the rows whose own props changed.
 */
export function useStableRowCallbacks(latest: LeadRowCallbacks): LeadRowCallbacks {
  const latestRef = useRef(latest);
  useLayoutEffect(() => {
    latestRef.current = latest;
  });
  const [stable] = useState<LeadRowCallbacks>(() => ({
    onToggleRow: (lead, isOpen) => latestRef.current.onToggleRow(lead, isOpen),
    onToggleSelect: (borrowerId, range) => latestRef.current.onToggleSelect(borrowerId, range),
    onApprove: (borrowerId) => latestRef.current.onApprove(borrowerId),
    onReject: (borrowerId) => latestRef.current.onReject(borrowerId),
    onOpenDisposition: (borrowerId) => latestRef.current.onOpenDisposition(borrowerId),
    onAssignmentUpdate: (borrowerId, update) => latestRef.current.onAssignmentUpdate(borrowerId, update),
    onFocusRow: (borrowerId) => latestRef.current.onFocusRow(borrowerId),
  }));
  return stable;
}
