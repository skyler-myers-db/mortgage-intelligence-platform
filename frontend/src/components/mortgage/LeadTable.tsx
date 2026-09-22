import { useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type MouseEvent as ReactMouseEvent } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useVirtualizer } from '@tanstack/react-virtual';
import { useSearchParams } from 'react-router';
import { Icon } from '../Icon';
import { Button } from '../Primitives';
import { useApp } from '../AppContext';
import { api } from '../../lib/api';
import { queryKeys } from '../../lib/queryKeys';
import { buildLeadCsv } from './LeadTable.csv';
import {
  LEAD_EXPANDED_PREVIEW_ESTIMATE_PX,
  LEAD_ROW_ESTIMATE_PX,
  LEAD_ROW_OVERSCAN,
  LEAD_TABLE_COL_COUNT,
  LEAD_VIRTUALIZATION_THRESHOLD,
} from './LeadTable.constants';
import {
  isEditableTarget,
  isLeadApprovalEligible,
  isLeadSelectableForSalesOps,
  isTerminalApproval,
  sortValue,
  verifiedCampaignBinding,
} from './LeadTable.logic';
import { LeadTableRow } from './LeadTableRow';
import { LeadTableBulkActions, LeadTableBulkToast } from './LeadTableBulkActions';
import { LeadTableStatusChips } from './LeadTableStatusChips';
import { LeadDispositionPanel, LeadRejectPanel } from './LeadTableDecisionPanels';
import { useLeadApprovalActions, type CampaignBindingState } from './useLeadApprovalActions';
import { useLeadSalesActions } from './useLeadSalesActions';
import { useLeadTableHotkeys } from './useLeadTableHotkeys';
import type { LeadTableProps, SortDir, SortKey } from './LeadTable.types';

export { buildLeadCsv } from './LeadTable.csv';
export {
  bulkActionFocusTarget,
  isEditableTarget,
  isLeadApprovalEligible,
  isLeadMarketingActionable,
  isLeadSelectableForSalesOps,
} from './LeadTable.logic';
export type { LeadExportContext } from './LeadTable.types';

/**
 * LeadTable — prototype `.surface` + `.tbl` BEM. Sticky thead, hover, row
 * expand into a mini borrower-detail preview. Approvals track per-row via
 * AppContext; a chip on the rightmost column shows Pending / Approved /
 * Rejected.
 *
 * This module is the table SHELL: props, campaign binding, sorting,
 * virtualization, the thead/tbody composition, and CSV export. The behavior
 * lives in focused siblings — `useLeadApprovalActions` (approve / reject /
 * bulk approve / selection), `useLeadSalesActions` (assignment, disposition,
 * optimistic row overrides), `useLeadTableHotkeys` (the single window
 * listener), plus `LeadTableRow`, `LeadTableStatusChips`,
 * `LeadTableBulkActions`, and `LeadTableDecisionPanels`.
 *
 * LO friction fix (2026-04-22): the Approval column is now an inline
 * control, not a read-only chip. Pending rows expose an "Approve" primary
 * button + a reject icon so loan officers can burn through the queue in
 * one click per lead instead of navigating to Offer Orchestrator. Once
 * approved/rejected, the column reverts to the chip shape. Keyboard
 * shortcuts (A / R) act on the expanded row.
 *
 * Sales-ops bulk workflow (2026-04-22): a leftmost checkbox column selects
 * rows for bulk approval. When >= 1 row is selected, a sticky action bar
 * inside the table container offers "Approve N leads" / "Clear selection".
 * Bulk approve loops `api.approve()` per selected lead in chunks of 3 to
 * keep one audit row per approval (matching the single-row flow). Already
 * approved/rejected rows are skipped silently. Shift+A fires bulk-approve
 * when the table has focus; plain A still approves only the expanded row.
 */

export function LeadTable({
  leads,
  totalMatching = null,
  truncatedAt = null,
  growthAgentVerification = null,
  exportContext,
  salesTeam = [],
}: LeadTableProps) {
  'use no memo';

  const queryClient = useQueryClient();
  const [searchParams] = useSearchParams();
  const campaignId = searchParams.get('campaign_id')?.trim() ?? '';
  const variantName = searchParams.get('variant_name')?.trim() ?? '';
  const hasCampaignBindingRequest = Boolean(campaignId || variantName);
  const requestedCampaignBinding = campaignId && variantName
    ? { campaign_id: campaignId, variant_name: variantName }
    : null;
  const campaignBindingQuery = useQuery({
    queryKey: queryKeys.campaign(campaignId),
    queryFn: ({ signal }) => api.campaign(campaignId, signal),
    enabled: requestedCampaignBinding !== null,
    retry: false,
    staleTime: 60_000,
  });
  const campaignBinding = verifiedCampaignBinding(
    campaignBindingQuery.data,
    requestedCampaignBinding,
  );
  const campaignBindingState: CampaignBindingState = !hasCampaignBindingRequest
    ? 'absent'
    : requestedCampaignBinding === null
      ? 'invalid'
      : campaignBinding
        ? 'verified'
        : campaignBindingQuery.isPending || campaignBindingQuery.isFetching
          ? 'validating'
          : 'invalid';
  const campaignBindingBlocked = hasCampaignBindingRequest && campaignBinding === null;
  const tableWrapRef = useRef<HTMLDivElement | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [sortKey, setSortKey] = useState<SortKey>('rank');
  const [sortDir, setSortDir] = useState<SortDir>('desc');
  // Shared error surface: both the approval path and the sales-ops path
  // report into the single `.table-error` alert this shell renders.
  const [approvalError, setApprovalError] = useState<string | null>(null);
  const { approvals, setApproval, setLastBorrowerId, openConsoleRecentActivity } = useApp();

  useEffect(() => {
    if (expanded) setLastBorrowerId(expanded);
  }, [expanded, setLastBorrowerId]);

  const sales = useLeadSalesActions({
    leads,
    salesTeam,
    queryClient,
    setApprovalError,
  });
  const { displayLeads, leadsById } = sales;
  const sortedLeads = sortKey === 'rank'
    ? displayLeads
    : [...displayLeads].sort((a, b) => {
        const direction = sortDir === 'asc' ? 1 : -1;
        const av = sortValue(a, sortKey);
        const bv = sortValue(b, sortKey);
        if (typeof av === 'number' && typeof bv === 'number') {
          return (av - bv) * direction;
        }
        return String(av).localeCompare(String(bv)) * direction;
      });
  const expandedRowIndex = expanded
    ? sortedLeads.findIndex((lead) => lead.borrower_id === expanded)
    : -1;
  const hasExpandedRow = expandedRowIndex >= 0;
  const shouldVirtualize = sortedLeads.length > LEAD_VIRTUALIZATION_THRESHOLD;
  // TanStack Virtual returns imperative instance methods tied to the scroll
  // element. The hook stays local to this table and its methods are not passed
  // into memoized children, so React Compiler's library advisory is expected.
  // eslint-disable-next-line react-hooks/incompatible-library
  const rowVirtualizer = useVirtualizer({
    count: sortedLeads.length,
    enabled: shouldVirtualize,
    estimateSize: (index) => LEAD_ROW_ESTIMATE_PX + (
      index === expandedRowIndex ? LEAD_EXPANDED_PREVIEW_ESTIMATE_PX : 0
    ),
    getItemKey: (index) => sortedLeads[index]?.borrower_id ?? index,
    getScrollElement: () => tableWrapRef.current,
    overscan: LEAD_ROW_OVERSCAN,
  });
  const virtualItems = shouldVirtualize ? rowVirtualizer.getVirtualItems() : [];
  const visibleRows = shouldVirtualize
    ? virtualItems.map((item) => ({
        lead: sortedLeads[item.index],
        virtualIndex: item.index,
      }))
    : sortedLeads.map((lead, virtualIndex) => ({ lead, virtualIndex }));
  const topSpacerHeight = virtualItems[0]?.start ?? 0;
  const bottomSpacerHeight = virtualItems.length > 0
    ? Math.max(0, rowVirtualizer.getTotalSize() - virtualItems[virtualItems.length - 1].end)
    : 0;

  const approval = useLeadApprovalActions({
    displayLeads,
    leadsById,
    approvals,
    setApproval,
    queryClient,
    campaignBinding,
    campaignBindingState,
    campaignBindingBlocked,
    tableWrapRef,
    setApprovalError,
  });

  /**
   * Keyboard: A approves / R rejects the expanded row; Shift+A fires
   * bulk-approve when >= 1 row is selected. We listen at the window
   * level but bail out if focus is inside an editable element so typing
   * in the Genie chat or a filter input never triggers approval.
   */
  useLeadTableHotkeys((e: KeyboardEvent) => {
    // R5-12 (2026-04-23): belt-and-suspenders check against both the
    // event target AND document.activeElement. For window-level
    // keydowns `e.target` is usually the focused element, but when
    // nothing is focused it falls back to `document.body` — which
    // would bypass an input check. Checking `activeElement` too
    // means typing "a" in the Genie textarea can never trigger the
    // approve hotkey.
    if (isEditableTarget(e.target as Element | null)) return;
    if (isEditableTarget(document.activeElement)) return;
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    const key = e.key.toLowerCase();
    if (campaignBindingBlocked && (key === 'a' || key === 'r')) return;
    // Shift+A: bulk approve. Takes precedence over single-row A when
    // any row is selected.
    if (key === 'a' && e.shiftKey) {
      if (approval.selectedIds.size === 0 || approval.bulkApproving) return;
      e.preventDefault();
      void approval.bulkApprove();
      return;
    }
    if (!expanded) return;
    const expandedLead = leadsById.get(expanded);
    const expandedStatus = approvals[expanded] ?? expandedLead?.approval_status;
    if (key === 'a') {
      if (!isLeadApprovalEligible(expandedStatus, approvals[expanded], expandedLead)) return;
      e.preventDefault();
      void approval.approveLead(expanded);
    } else if (key === 'r') {
      if (isTerminalApproval(expandedStatus)) return;
      e.preventDefault();
      approval.setPendingReject(expanded);
    }
  });

  const stop = (e: ReactKeyboardEvent | ReactMouseEvent) => e.stopPropagation();

  /** Assign / distribute the current selection. The selection set lives in
   *  the approval hook, so the shell hands both it and the clear callback to
   *  the sales hook — see `useLeadSalesActions.assignSelected`. */
  function assignSelected(mode: 'selected-lo' | 'round-robin') {
    const borrowerIds = [...approval.selectedIds].filter(
      (id) => approval.selectableIds.includes(id),
    );
    void sales.assignSelected(mode, borrowerIds, approval.clearSelection);
  }

  /**
   * Export the currently-visible leads as CSV. Client-side only: the
   * bytes come straight from the `leads` prop the caller already passed
   * in (which is the real /api/leads payload after any segment/filter
   * narrowing the parent route applied). We do NOT invent a server-side
   * export endpoint or synthesize fields the payload doesn't carry — so
   * PII stays suppressed by construction.
   */
  function exportCsv() {
    if (leads.length === 0) return;
    const csv = buildLeadCsv(leads, approvals, exportContext);
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    const stamp = new Date().toISOString().slice(0, 10);
    a.download = `mip-leads-${stamp}.csv`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }

  function toggleSort(key: SortKey) {
    if (key === 'rank') {
      setSortKey('rank');
      setSortDir('desc');
      return;
    }
    setSortKey((current) => {
      if (current === key) {
        setSortDir((dir) => (dir === 'desc' ? 'asc' : 'desc'));
        return current;
      }
      setSortDir('desc');
      return key;
    });
  }

  // 2026-06-11 audit P3 a11y: renderSortHeader returns the full <th> so
  // `aria-sort` lives on the columnheader role (the only role where the
  // ARIA spec defines it — putting it on the inner button would be
  // invalid ARIA and ignored by screen readers).
  const renderSortHeader = (key: SortKey, label: string, thClassName?: string) => (
    <th
      className={thClassName}
      aria-sort={sortKey === key ? (sortDir === 'desc' ? 'descending' : 'ascending') : 'none'}
    >
      <button
        type="button"
        className="tbl__sort"
        onClick={() => toggleSort(key)}
        aria-label={`Sort by ${label}`}
        aria-pressed={sortKey === key}
      >
        <span>{label}</span>
        {sortKey === key && key !== 'rank' && (
          <Icon name={sortDir === 'desc' ? 'down' : 'up'} size={10} />
        )}
      </button>
    </th>
  );

  return (
    // 2026-05-04 fix (alignment): removed inline `overflow: hidden`.
    // It was establishing a new block formatting context that, combined
    // with the table's intrinsic min-content width and the wrap div's
    // overflowY: auto, was nudging the surface off the .main__inner
    // left edge on the lead-queue page. The intended scroll behaviour
    // for table-containing surfaces is provided by the
    // `.surface:has(> div > .tbl) { overflow-x: auto }` rule in
    // components.css; the inline override was both unnecessary and the
    // proximate cause of the shift the user reported.
    <div className="surface">
      <div className="surface__hdr surface__hdr--split">
        <div className="surface__hdr-main">
          <div className="surface__icon">
            <Icon name="user" size={14} />
          </div>
          <div>
            <div className="h-4">Ranked borrowers</div>
            <div className="muted fs-12">
              {/*
                Prototype-parity-audit P2 (2026-05-04): keyboard hints
                were rendered as `.mono` spans, which read as inline code
                rather than a keycap. Switching to `<kbd>` (styled in
                design-system/components.css as a subtle keycap chip)
                makes the affordance scannable — an LO scrolling the
                queue can spot the shortcut without reading prose.
              */}
              Click a row to expand the preview. Keyboard: <kbd>A</kbd> approve, <kbd>R</kbd> reject the expanded row while it is still pending.
            </div>
          </div>
        </div>
        <div className="lead-table__header-actions">
          <Button
            size="sm"
            icon="export"
            onClick={exportCsv}
            disabled={leads.length === 0}
            data-testid="lead-export"
            aria-label={`Export ${leads.length} leads as CSV`}
          >
            Export list
          </Button>
        </div>
      </div>
      <LeadTableStatusChips
        growthAgentVerification={growthAgentVerification}
        campaignBindingState={campaignBindingState}
        requestedCampaignBinding={requestedCampaignBinding}
        campaignBinding={campaignBinding}
        expandedBorrowerId={expanded}
      />
      {approval.pendingReject && (
        <LeadRejectPanel
          borrowerId={approval.pendingReject}
          reasonCode={approval.rejectReasonCode}
          rationale={approval.rejectRationale}
          onReasonChange={approval.setRejectReasonCode}
          onRationaleChange={approval.setRejectRationale}
          onCancel={() => {
            approval.setPendingReject(null);
            approval.setRejectRationale('');
            approval.setRejectReasonCode('low_intent');
          }}
          onSubmit={() => void approval.submitReject()}
        />
      )}
      {sales.pendingDisposition && (
        <LeadDispositionPanel
          borrowerId={sales.pendingDisposition}
          salesTeam={salesTeam}
          salesBusy={sales.salesBusy}
          loEmail={sales.dispositionLo}
          outcome={sales.dispositionOutcome}
          callbackAt={sales.dispositionCallbackAt}
          notes={sales.dispositionNotes}
          onLoChange={sales.setDispositionLo}
          onOutcomeChange={sales.setDispositionOutcome}
          onCallbackAtChange={sales.setDispositionCallbackAt}
          onNotesChange={sales.setDispositionNotes}
          onCancel={() => sales.setPendingDisposition(null)}
          onSubmit={() => void sales.submitDisposition()}
        />
      )}
      {sales.salesToast && (
        <div role="status" aria-live="polite" className="table-success">
          {sales.salesToast}
        </div>
      )}
      <div
        ref={tableWrapRef}
        className="tbl-wrap"
        role="region"
        tabIndex={0}
        aria-label="Ranked borrowers table scroll region"
      >
        <table
          className="tbl lead-table__table"
          aria-rowcount={sortedLeads.length + 1 + (hasExpandedRow ? 1 : 0)}
        >
          <colgroup>
            <col className="lead-table__col-select" />
            <col className="lead-table__col-expand" />
            <col className="lead-table__col-borrower" />
            <col className="lead-table__col-location" />
            <col className="lead-table__col-relationship" />
            <col className="lead-table__col-assignment" />
            <col className="lead-table__col-outreach" />
            <col className="lead-table__col-disposition" />
            <col className="lead-table__col-segments" />
            <col className="lead-table__col-equity" />
            <col className="lead-table__col-rate" />
            <col className="lead-table__col-offer" />
            <col className="lead-table__col-score" />
            <col className="lead-table__col-confidence" />
            <col className="lead-table__col-approval" />
          </colgroup>
          <thead>
            <tr>
              <th className="tbl-cell--select">
                <input
                  type="checkbox"
                  aria-label="Select all eligible leads"
                  checked={approval.headerCheckboxState.checked}
                  ref={(el) => {
                    if (el) el.indeterminate = approval.headerCheckboxState.indeterminate;
                  }}
                  disabled={approval.selectableIds.length === 0 || approval.bulkApproving}
                  onChange={approval.toggleSelectAll}
                  onClick={stop}
                  data-testid="lead-select-all"
                />
              </th>
              <th className="tbl-cell--narrow"></th>
              <th>Borrower</th>
              <th>Location</th>
              {renderSortHeader('relationship', 'Relationship')}
              {renderSortHeader('assignment', 'Assigned to')}
              {renderSortHeader('outreach', 'Outreach')}
              <th>Last touch</th>
              <th>Segments</th>
              {renderSortHeader('equity', 'Equity', 'tbl-cell--right')}
              {renderSortHeader('rate', 'Rate Δ (bps)', 'tbl-cell--right')}
              <th>Primary offer</th>
              {renderSortHeader('score', 'Score', 'tbl-cell--right')}
              {renderSortHeader('confidence', 'Signal')}
              <th className="tbl-cell--approval lead-table__approval-header">Approval</th>
            </tr>
          </thead>
          {shouldVirtualize && topSpacerHeight > 0 && (
            <tbody aria-hidden="true">
              <tr aria-hidden="true" className="lead-table__virtual-spacer">
                <td colSpan={LEAD_TABLE_COL_COUNT} style={{ height: topSpacerHeight }} />
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
              const isSelected = approval.selectedIds.has(lead.borrower_id);
              const isSelectable = isLeadSelectableForSalesOps(serverStatus, rowApproval, lead);
              const isApprovalEligible = isLeadApprovalEligible(serverStatus, rowApproval, lead);
              return (
                <tbody
                  key={lead.borrower_id}
                  data-index={shouldVirtualize ? virtualIndex : undefined}
                  ref={shouldVirtualize ? rowVirtualizer.measureElement : undefined}
                >
                  <LeadTableRow
                    lead={lead}
                    virtualIndex={virtualIndex}
                    ariaRowIndex={virtualIndex + 2 + (
                      hasExpandedRow && virtualIndex > expandedRowIndex ? 1 : 0
                    )}
                    isOpen={isOpen}
                    approval={rowApproval}
                    isSelected={isSelected}
                    isSelectable={isSelectable}
                    isApprovalEligible={isApprovalEligible}
                    approvalActionsDisabled={campaignBindingBlocked}
                    bulkApproving={approval.bulkApproving}
                    salesBusy={sales.salesBusy}
                    salesTeamCount={salesTeam.length}
                    pendingApproval={Boolean(approval.pendingApproval[lead.borrower_id])}
                    onToggleRow={(row, open) => {
                      setLastBorrowerId(row.borrower_id);
                      setExpanded(open ? null : row.borrower_id);
                    }}
                    onToggleSelect={approval.toggleSelect}
                    onApprove={(borrowerId) => void approval.approveLead(borrowerId)}
                    onReject={approval.setPendingReject}
                    onOpenDisposition={sales.openDisposition}
                    onAssignmentUpdate={sales.applyLeadUpdate}
                  />
                </tbody>
              );
            })}
            {shouldVirtualize && bottomSpacerHeight > 0 && (
              <tbody aria-hidden="true">
              <tr aria-hidden="true" className="lead-table__virtual-spacer">
                <td colSpan={LEAD_TABLE_COL_COUNT} style={{ height: bottomSpacerHeight }} />
              </tr>
              </tbody>
            )}
        </table>
      </div>
      {approval.selectionCount > 0 && (
        <LeadTableBulkActions
          selectionCount={approval.selectionCount}
          selectedApprovalEligibleCount={approval.selectedApprovalEligibleCount}
          bulkApproving={approval.bulkApproving}
          bulkRationaleOpen={approval.bulkRationaleOpen}
          bulkRationale={approval.bulkRationale}
          onBulkRationaleChange={approval.setBulkRationale}
          campaignBindingBlocked={campaignBindingBlocked}
          salesTeam={salesTeam}
          salesBusy={sales.salesBusy}
          selectedAssignee={sales.selectedAssignee}
          onSelectedAssigneeChange={sales.setSelectedAssignee}
          onAssign={assignSelected}
          onClearSelection={approval.clearSelection}
          onBulkApprove={() => void approval.bulkApprove()}
          bulkApproveBtnRef={approval.bulkApproveBtnRef}
        />
      )}
      {approval.bulkToast && (
        <LeadTableBulkToast
          toast={approval.bulkToast}
          onReviewRecentActivity={() => {
            openConsoleRecentActivity();
            approval.setBulkToast(null);
          }}
        />
      )}
      {approvalError && (
        <div
          role="alert"
          className="table-error"
        >
          {approvalError}
        </div>
      )}
      <div className="surface__ft">
        Showing {leads.length.toLocaleString()} ranked borrower{leads.length === 1 ? '' : 's'}
        {totalMatching !== null && (
          <>
            {' '}of {totalMatching.toLocaleString()} total matching filters
          </>
        )}
        {truncatedAt !== null && totalMatching !== null && totalMatching > leads.length && (
          <span className="muted"> · capped at {truncatedAt.toLocaleString()}</span>
        )}
      </div>
    </div>
  );
}
