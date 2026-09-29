import { useCallback, useLayoutEffect, useReducer, type ReactNode, type RefObject } from 'react';
import { useVirtualizer } from '@tanstack/react-virtual';
import type { LeadSummary } from '../../types';
import { LEAD_EXPANDED_PREVIEW_ESTIMATE_PX, LEAD_ROW_OVERSCAN } from './LeadTable.constants';
import type { LeadTableView } from './LeadTable.columns';
import { isLeadApprovalEligible, isLeadSelectableForSalesOps, isTerminalApproval } from './LeadTable.logic';
import type { LeadDecisionReceipt } from './DecisionReceipt';
import { LeadTableRow } from './LeadTableRow';
import { useLeadTableInitialOffset, type LeadTableVirtualScroll } from './useLeadTableScroll';
import type { OutreachDecision } from '../../lib/mutations/outreach';
import type { LeadRowCallbacks } from './LeadTable.rowCallbacks';

/** The approve review rendered inline in its (expanded) row, when there is one. */
export interface LeadTableReviewSlot {
  borrowerId: string;
  node: ReactNode;
}

export interface LeadTableBodyProps {
  /** Rows in on-screen order, and their ids (the virtualizer's keys). */
  rows: readonly LeadSummary[];
  rowIds: readonly string[];
  view: LeadTableView;
  columnCount: number;
  expanded: string | null;
  expandedRowIndex: number;
  /** More rows than LEAD_VIRTUALIZATION_THRESHOLD: render only the window. */
  virtualized: boolean;
  /** The one-line row's estimate for the current density (leadRowEstimatePx). */
  rowEstimatePx: number;
  /** The Lead Queue keeps the table scroller's place per history entry (runtime-08). */
  restoreScroll: boolean;
  tableWrapRef: RefObject<HTMLDivElement | null>;
  /** Published here: the keyboard flow reads it when it scrolls a row into the window. */
  scrollToIndexRef: RefObject<(index: number) => void>;
  /** Published here while virtualized: the shell's useLeadTableScroll restores through it. */
  virtualScrollRef: RefObject<LeadTableVirtualScroll | null>;
  approvals: Record<string, 'approved' | 'rejected'>;
  selectedIds: ReadonlySet<string>;
  pendingDecisions: ReadonlyMap<string, OutreachDecision>;
  decisionReceipts: Record<string, LeadDecisionReceipt>;
  cursorId: string | null;
  campaignBindingBlocked: boolean;
  approverGate: string | null;
  bulkApproving: boolean;
  salesBusy: boolean;
  salesTeamCount: number;
  shortcutsLive: boolean;
  reviewSlot: LeadTableReviewSlot | null;
  /** The row an approve review is open or opening for, in any mode. */
  reviewBorrowerId: string | null;
  /** The shell's row callbacks, one identity for the table's life (useStableRowCallbacks). */
  rowCallbacks: LeadRowCallbacks;
}

/**
 * LeadTableBody — the ranked-borrower table's `<tbody>` rows: the TanStack
 * virtualizer and the spacer rows around a virtual window (audit runtime-04
 * slice 3, tables-05 step 1). The table scroller's per-entry place
 * (useLeadTableScroll) stays in the shell, which owns the scroller: a
 * layout effect here runs before the shell's `.tbl-wrap` ref is attached, so
 * a restore run from here would find no scroller on the first commit. The
 * shell restores through the virtualizer this body publishes.
 *
 * It is the ONE file of the table that stays 'use no memo': TanStack Virtual
 * returns an imperative instance whose methods read live scroll state, which
 * React Compiler cannot memoize (the incompatible-library advisory), and
 * the virtualizer re-renders this component on every window change. Keeping
 * it here lets the shell (LeadTable) compile: a scroll re-renders only this
 * body, and a shell render with unchanged inputs skips it.
 *
 * Nothing here creates a per-row closure: every row callback comes from the
 * shell, so a compiled LeadTableRow gets identical props and its memo cache
 * hits (a row expand or an unrelated AppContext change re-derives no other
 * row's workflow states).
 */
export function LeadTableBody({
  rows,
  rowIds,
  view,
  columnCount,
  expanded,
  expandedRowIndex,
  virtualized,
  rowEstimatePx,
  restoreScroll,
  tableWrapRef,
  scrollToIndexRef,
  virtualScrollRef,
  approvals,
  selectedIds,
  pendingDecisions,
  decisionReceipts,
  cursorId,
  campaignBindingBlocked,
  approverGate,
  bulkApproving,
  salesBusy,
  salesTeamCount,
  shortcutsLive,
  reviewSlot,
  reviewBorrowerId,
  rowCallbacks,
}: LeadTableBodyProps) {
  'use no memo';

  const hasExpandedRow = expandedRowIndex >= 0;
  // Stable virtualizer inputs: an inline getItemKey is a virtual-core memo
  // dependency, so a new one on every render rebuilt all 500 measurements.
  // The row estimate follows the density (responsive-09 item 3).
  const getItemKey = useCallback((index: number) => rowIds[index] ?? index, [rowIds]);
  const estimateSize = useCallback((index: number) => rowEstimatePx + (
    index === expandedRowIndex ? LEAD_EXPANDED_PREVIEW_ESTIMATE_PX : 0
  ), [rowEstimatePx, expandedRowIndex]);
  // Back to this entry: the virtualizer starts at the saved offset (runtime-08).
  const initialTableOffset = useLeadTableInitialOffset(restoreScroll);
  // The scroller (`.tbl-wrap`) is the shell's element, whose ref is attached
  // after this body's first layout effects: the virtualizer's first look at
  // getScrollElement() finds null. One synchronous re-render before paint
  // lets it attach, as it did when it lived in the shell.
  const [, attachScroller] = useReducer((attached: boolean) => attached || true, false);
  useLayoutEffect(() => {
    attachScroller();
  }, []);
  // TanStack Virtual returns imperative instance methods tied to the scroll
  // element. The hook stays local to this body and its methods are not passed
  // into memoized children, so React Compiler's library advisory is expected.
  // eslint-disable-next-line react-hooks/incompatible-library
  const rowVirtualizer = useVirtualizer({
    count: rows.length,
    enabled: virtualized,
    estimateSize,
    getItemKey,
    getScrollElement: () => tableWrapRef.current,
    overscan: LEAD_ROW_OVERSCAN,
    initialOffset: initialTableOffset,
  });
  // The keyboard flow scrolls a cursor row outside the window into it; the
  // shell's scroll restore reads the virtualizer while it is on.
  useLayoutEffect(() => {
    scrollToIndexRef.current = (index) => rowVirtualizer.scrollToIndex(index, { align: 'auto' });
    virtualScrollRef.current = virtualized ? rowVirtualizer : null;
  });
  const virtualItems = virtualized ? rowVirtualizer.getVirtualItems() : [];
  const visibleRows = virtualized
    ? virtualItems.map((item) => ({
        lead: rows[item.index],
        virtualIndex: item.index,
      }))
    : rows.map((lead, virtualIndex) => ({ lead, virtualIndex }));
  const topSpacerHeight = virtualItems[0]?.start ?? 0;
  const bottomSpacerHeight = virtualItems.length > 0
    ? Math.max(0, rowVirtualizer.getTotalSize() - virtualItems[virtualItems.length - 1].end)
    : 0;

  return (
    <>
      {virtualized && topSpacerHeight > 0 && (
        <tbody aria-hidden="true">
          <tr aria-hidden="true" className="lead-table__virtual-spacer">
            <td colSpan={columnCount} style={{ height: topSpacerHeight }} />
          </tr>
        </tbody>
      )}
      {visibleRows.map(({ lead, virtualIndex }) => {
        const isOpen = expanded === lead.borrower_id;
        // Prefer in-session AppContext override (set optimistically on
        // approve/reject); fall back to the server-projected
        // approval_status so a page reload doesn't make approved
        // borrowers look pending. Round-2 hole-finder #12, 2026-04-23.
        const serverStatus = lead.approval_status;
        const rowApproval: string | undefined = approvals[lead.borrower_id]
          ?? (isTerminalApproval(serverStatus)
              ? serverStatus
              : undefined);
        const isSelected = selectedIds.has(lead.borrower_id);
        const isSelectable = isLeadSelectableForSalesOps(serverStatus, rowApproval, lead);
        const isApprovalEligible = isLeadApprovalEligible(serverStatus, rowApproval, lead);
        return (
          <tbody
            key={lead.borrower_id}
            data-index={virtualized ? virtualIndex : undefined}
            ref={virtualized ? rowVirtualizer.measureElement : undefined}
          >
            <LeadTableRow
              lead={lead}
              virtualIndex={virtualIndex}
              view={view}
              ariaRowIndex={virtualIndex + 2 + (
                hasExpandedRow && virtualIndex > expandedRowIndex ? 1 : 0
              )}
              isOpen={isOpen}
              approval={rowApproval}
              isSelected={isSelected}
              isSelectable={isSelectable}
              isApprovalEligible={isApprovalEligible}
              approvalActionsDisabled={campaignBindingBlocked}
              approverGate={approverGate}
              bulkApproving={bulkApproving}
              salesBusy={salesBusy}
              salesTeamCount={salesTeamCount}
              pendingDecision={pendingDecisions.get(lead.borrower_id) ?? null}
              decisionReceipt={decisionReceipts[lead.borrower_id] ?? null}
              isCursor={cursorId === lead.borrower_id}
              shortcutsLive={shortcutsLive}
              reviewSlot={isOpen && reviewSlot?.borrowerId === lead.borrower_id ? reviewSlot.node : null}
              reviewActive={reviewBorrowerId === lead.borrower_id}
              onToggleRow={rowCallbacks.onToggleRow}
              onToggleSelect={rowCallbacks.onToggleSelect}
              onApprove={rowCallbacks.onApprove}
              onReject={rowCallbacks.onReject}
              onOpenDisposition={rowCallbacks.onOpenDisposition}
              onAssignmentUpdate={rowCallbacks.onAssignmentUpdate}
            />
          </tbody>
        );
      })}
      {virtualized && bottomSpacerHeight > 0 && (
        <tbody aria-hidden="true">
          <tr aria-hidden="true" className="lead-table__virtual-spacer">
            <td colSpan={columnCount} style={{ height: bottomSpacerHeight }} />
          </tr>
        </tbody>
      )}
    </>
  );
}
