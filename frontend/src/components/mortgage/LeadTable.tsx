import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useSearchParams } from 'react-router';
import { useApp } from '../AppContext';
import { api } from '../../lib/api';
import { useIsOnline } from '../../lib/connectivity';
import { queryKeys } from '../../lib/queryKeys';
import { preloadRouteForPath } from '../../lib/routePreloaders';
import { planLeadCsvExport } from './LeadTable.csv';
import { useLeadCsvExport } from './useLeadCsvExport';
import { LEAD_VIRTUALIZATION_THRESHOLD, leadRowEstimatePx } from './LeadTable.constants';
import { leadTableColumnCount, leadTableColumns } from './LeadTable.columns';
import { sortValue, verifiedCampaignBinding } from './LeadTable.logic';
import { LeadTableBody } from './LeadTableBody';
import { useStableRowCallbacks } from './LeadTable.rowCallbacks';
import { LeadTableHead } from './LeadTableHead';
import { LeadTableHeader } from './LeadTableHeader';
import { LeadTableFooter } from './LeadTableFooter';
import { LeadTableBulkActions, LeadTableBulkToast } from './LeadTableBulkActions';
import { LeadBulkRunProgressFallback, LeadBulkRunResultFallback } from './LeadBulkRunFallback';
import { LeadTableStatusChips } from './LeadTableStatusChips';
import { LeadDispositionPanel, LeadRejectPanel } from './LeadTableDecisionPanels';
import { LEAD_TABLE_KEYS } from './LeadTable.keymap';
import { useLeadApprovalActions, type CampaignBindingState } from './useLeadApprovalActions';
import { useLeadSalesActions } from './useLeadSalesActions';
import { useLeadTableKeyboardFlow } from './useLeadTableKeyboardFlow';
import { useLeadTableFillHeight } from './useLeadTableFillHeight';
import { useTableScrollClearance } from './useTableScrollClearance';
import { useLeadTableScroll, type LeadTableVirtualScroll } from './useLeadTableScroll';
import { lazyModule, useLazyModule } from './useLazyModule';
import { approverGateReason } from './approverGate';
import { ariaKeyShortcuts } from '../../lib/keymap';
import { useSingleKeyShortcuts } from '../../lib/keymapPreference';
import type { OutreachDraftResult } from '../../lib/apiTypes';
import type { LeadSummary } from '../../types';
import type { LeadTableProps, LeadTableSort, SortDir, SortKey } from './LeadTable.types';
import './LeadTable.css';

export { buildLeadCsv } from './LeadTable.csv';
export {
  bulkActionFocusTarget,
  isEditableTarget,
  isLeadApprovalEligible,
  isLeadMarketingActionable,
  isLeadSelectableForSalesOps,
} from './LeadTable.logic';
export type { LeadExportContext } from './LeadTable.types';
export type { LeadTableView } from './LeadTable.columns';

// The approve review and the bulk gate's review are needed only on an
// explicit Approve: their own chunks, not the shared LeadTable chunk.
const REVIEW_CHUNK = lazyModule(() => import('./LeadApproveReview'));
const BULK_REVIEW_CHUNK = lazyModule(() => import('./LeadBulkApproveReview'));

/**
 * LeadTable — prototype `.surface` + `.tbl` BEM. Sticky thead, hover, row
 * expand into a mini borrower-detail preview. Approvals track per-row via
 * AppContext; a chip on the rightmost column shows Pending / Approved /
 * Rejected.
 *
 * This module is the table SHELL: props, campaign binding, sorting, the
 * thead/tbody composition, and CSV export; the rows and their virtualizer
 * are LeadTableBody (runtime-04 slice 3). The behavior
 * lives in focused siblings — `useLeadApprovalActions` (approve / reject /
 * bulk approve / selection), `useLeadSalesActions` (assignment, disposition,
 * optimistic row overrides), `useLeadTableKeyboardFlow` (row cursor, approve
 * review, keymap bindings, Cmd-K selection verbs), plus `LeadTableRow`,
 * `LeadTableStatusChips`, `LeadTableBulkActions`, and
 * `LeadTableDecisionPanels`.
 *
 * LO friction fix (2026-04-22): the Approval column is an inline control,
 * not a read-only chip. Once approved/rejected, the column reverts to the
 * chip shape. Wave 1c (audit flow-03 / states-06): the first Approve (or A
 * on the cursor row) opens the approve review, which drafts on that intent
 * and shows the subject, message, channel, disclosure and evidence; only
 * Confirm approves that exact draft.
 *
 * Sales-ops bulk workflow (2026-04-22): a leftmost checkbox column selects
 * rows for bulk approval. When >= 1 row is selected, a sticky action bar
 * inside the table container offers "Approve N eligible" / "Clear
 * selection". Bulk approve loops `api.approve()` per selected lead in chunks
 * of 3 to keep one audit row per approval (matching the single-row flow),
 * behind a required shared rationale; the gate shows the count by offer and
 * drafts stratified samples (one per offer) only on "Preview k sample drafts
 * (one per offer)", and Approve arms only once every offer in the run has
 * one. Each APPROVE row records its review_mode: 'bulk_sample' for a row
 * whose previewed copy is what it certifies, 'bulk_cohort' for a row
 * approved under the shared rationale without its copy being shown. Shift+A
 * and the Cmd-K verb open that same gate; one selected row opens its own
 * review instead.
 */

function ignoreScrollToIndex(): void {
  // Replaced by LeadTableBody once it has mounted its virtualizer.
}

export function LeadTable({
  leads,
  totalMatching = null,
  truncatedAt = null,
  growthAgentVerification = null,
  exportContext,
  salesTeam = [],
  view = 'default',
  onViewChange,
  fillHeight = false,
  sort: controlledSort,
  onSortChange,
  expandedId: controlledExpanded,
  onExpandedChange,
  restoreScroll = false,
  headerStatus,
}: LeadTableProps) {
  // Budget trade (audit runtime-04 slice 3, cut 5 of the wave-4b lane): the
  // compiled shell measured +2.63 KiB br on the LeadTable chunk (35.81 ->
  // 38.44, over its 38 KiB gate). It stays uncompiled with targeted manual
  // memoization instead: the sort and the id list (useMemo), the row
  // callbacks (useStableRowCallbacks) and the virtualizer inputs
  // (LeadTableBody). The chunk reads below rely on this pragma: compiled,
  // `REVIEW_CHUNK.current()` would be memoized once (the 5451de15 defect).
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
  const campaignBindingKey = `${campaignId}\n${variantName}`;
  const tableWrapRef = useRef<HTMLDivElement | null>(null);
  useLeadTableFillHeight(tableWrapRef, fillHeight);
  // Focus is never scrolled under the sticky thead or the pinned Approval
  // column (a11y-v2; the sticky route nav is 38-focus-clearance.css's).
  useTableScrollClearance(tableWrapRef, view);
  const columns = leadTableColumns(view);
  const columnCount = leadTableColumnCount(view);
  // Sort and expanded row: owned by the parent when it passes the change
  // handler (the Lead Queue keeps both in the URL, audit shell-03), else here.
  const [ownExpanded, setOwnExpanded] = useState<string | null>(null);
  const [ownSort, setOwnSort] = useState<LeadTableSort | null>(null);
  const expanded = onExpandedChange ? controlledExpanded ?? null : ownExpanded;
  const setExpanded = onExpandedChange ?? setOwnExpanded;
  const activeSort = onSortChange ? controlledSort ?? null : ownSort;
  const setSort = onSortChange ?? setOwnSort;
  const sortKey: SortKey = activeSort?.key ?? 'rank';
  const sortDir: SortDir = activeSort?.dir ?? 'desc';
  // Approve, reject and assign failures and gate refusals raise an error
  // toast on the shell region (states-07 item 2: leadWriteFailureToast);
  // the table keeps only the export and review-loading lines.
  const {
    approvals, setApproval, setLastBorrowerId, openConsoleRecentActivity,
    canApprove, canAccessAdmin, actorEmail, sessionStatus, setDrawer, density,
  } = useApp();
  // Audit flow-02 / shell-06: non-approvers keep a VISIBLE but disabled gate.
  const approverGate = approverGateReason(canApprove, sessionStatus);

  // Row expand (click or keys) warms route CODE only: no audited read, no draft (delivery-08).
  useEffect(() => {
    if (!expanded) return;
    setLastBorrowerId(expanded);
    ['/borrower-360', '/offer-orchestrator'].forEach((route) => preloadRouteForPath(`${route}/${expanded}`));
  }, [expanded, setLastBorrowerId]);

  const sales = useLeadSalesActions({
    leads,
    salesTeam,
    queryClient,
  });
  const { displayLeads, leadsById } = sales;
  // Audit runtime-04 slice 1: sorted once per (rows, sort), not on every
  // render; the id list is what the virtualizer keys rows by.
  const sortedLeads = useMemo(() => (sortKey === 'rank'
    ? displayLeads
    : [...displayLeads].sort((a, b) => {
        const direction = sortDir === 'asc' ? 1 : -1;
        const av = sortValue(a, sortKey);
        const bv = sortValue(b, sortKey);
        if (typeof av === 'number' && typeof bv === 'number') {
          return (av - bv) * direction;
        }
        return String(av).localeCompare(String(bv)) * direction;
      })), [displayLeads, sortKey, sortDir]);
  const rowIds = useMemo(() => sortedLeads.map((lead) => lead.borrower_id), [sortedLeads]);
  const expandedRowIndex = expanded ? rowIds.indexOf(expanded) : -1;
  const hasExpandedRow = expandedRowIndex >= 0;
  const shouldVirtualize = sortedLeads.length > LEAD_VIRTUALIZATION_THRESHOLD;
  // The row estimate follows the density (responsive-09 item 3).
  const rowEstimatePx = leadRowEstimatePx(density);
  // LeadTableBody owns the virtualizer and publishes its scrollToIndex here;
  // the keyboard flow reads it when it moves the cursor out of the window.
  const scrollToIndexRef = useRef<(index: number) => void>(ignoreScrollToIndex);
  // ...and the virtualizer itself while it is on, for the scroll restore
  // below, which runs here because the shell owns the scroller (its ref is
  // attached before the shell's layout effects, after the body's).
  const virtualScrollRef = useRef<LeadTableVirtualScroll | null>(null);
  const [virtualScroll] = useState<LeadTableVirtualScroll>(() => ({
    scrollToOffset: (offset) => virtualScrollRef.current?.scrollToOffset(offset),
    getTotalSize: () => virtualScrollRef.current?.getTotalSize() ?? 0,
  }));
  // Reveal the row the URL names (`?row=`) when the Lead Queue loads, or a
  // PUSH lands on an entry naming it (audit tables-09 follow-up): the table
  // scroller started at the top, and a virtualized row there was never
  // rendered. Requested by useLeadTableScroll's fresh-entry seam (never on
  // REPLACE, never on a POP that restores a saved offset) and run below,
  // once the named row is among the rows on screen. Scroll only; no read.
  // undefined: requested, not yet bound to the entry's `?row=`; null: none.
  const revealRef = useRef<string | null | undefined>(null);
  const requestReveal = useCallback(() => {
    revealRef.current = undefined;
  }, []);
  useLeadTableScroll({
    enabled: restoreScroll,
    tableWrapRef,
    virtualizer: shouldVirtualize ? virtualScroll : null,
    onFreshEntry: requestReveal,
  });

  const approval = useLeadApprovalActions({
    displayLeads,
    leadsById,
    approvals,
    setApproval,
    queryClient,
    campaignBinding,
    campaignBindingState,
    campaignBindingBlocked,
    canApprove: approverGate === null,
    tableWrapRef,
  });

  /**
   * Keyboard triage (wave 1c): a row cursor (J / K / arrows), Enter, X and
   * A / R on the cursor row, Shift+A for the selection, all registered in
   * the shared keymap and live only while focus is inside the `.tbl-wrap`
   * region with no dialog, drawer, listbox or menu open (audit tables-v2 /
   * a11y-09, WCAG 2.1.4), and never while typing. A opens the approve
   * review; nothing here approves without it.
   */
  const [singleKeysOn] = useSingleKeyShortcuts();
  const assigneeRef = useRef<HTMLSelectElement | null>(null);
  const sampleDraftsRef = useRef<ReadonlyMap<string, OutreachDraftResult>>(new Map());
  const [samplesShown, setSamplesShown] = useState(false);
  // The gate's coverage: every offer in the run has a previewed sample. Set
  // by the gate only when it changes (never per keystroke), it arms Approve.
  const [samplesCoverAllOffers, setSamplesCoverAllOffers] = useState(false);
  const flow = useLeadTableKeyboardFlow({
    sortedLeads,
    leadsById,
    approvals,
    expanded,
    setExpanded,
    // A restored row (the Lead Queue's `?row=`) starts as the cursor row.
    initialCursorId: expanded,
    approval,
    approverGate,
    campaignBindingBlocked,
    canAssign: salesTeam.length > 0 && !sales.salesBusy,
    assigneeRef,
    tableWrapRef,
    virtualized: shouldVirtualize,
    scrollToIndex: (index) => scrollToIndexRef.current(index),
    openEvidence: setDrawer,
    reviewChunk: {
      isReady: () => REVIEW_CHUNK.current() !== null,
      load: () => REVIEW_CHUNK.load().then(() => true, () => false),
    },
    campaignBindingKey,
    // The Lead Queue keeps the expanded row in `?row=` (a POP that leaves it
    // naming an open review's row does not abandon the review).
    urlRowId: searchParams.get('row'),
    onCampaignBindingChange: () => {
      sampleDraftsRef.current = new Map();
      setSamplesShown(false);
      setSamplesCoverAllOffers(false);
    },
  });
  const { review } = flow;
  const openReview = review.review;
  // The fresh-entry reveal (requested above), once the named row is loaded.
  const urlRow = searchParams.get('row');
  useEffect(() => {
    if (revealRef.current === null) return undefined;
    // Bound to the row this entry names; the URL moving on (an expand, Back)
    // drops it, and a placeholder page (the row not settled yet) waits.
    if (revealRef.current === undefined) revealRef.current = urlRow;
    const target = revealRef.current;
    if (target === null || urlRow !== target) {
      revealRef.current = null;
      return undefined;
    }
    if (expanded !== target || !rowIds.includes(target)) return undefined;
    // A frame later: on a first mount the virtualizer attaches to the
    // shell's scroller in LeadTableBody's layout re-render, after this effect.
    // The request is spent in that frame; a render before it cancels the
    // frame and checks again (the URL may have moved on), and so does an
    // unmount, which leaves no callback behind.
    const { revealRow } = flow.cursor;
    const frame = requestAnimationFrame(() => {
      revealRef.current = null;
      revealRow(target);
    });
    return () => cancelAnimationFrame(frame);
  });
  // Load the review chunk once the reader engages with rows (the draft is
  // requested only on Approve); the bulk review chunk once rows are selected.
  const reviewChunk = useLazyModule(REVIEW_CHUNK, openReview !== null || flow.cursor.cursorId !== null || expanded !== null);
  const bulkRun = approval.bulkRun;
  // The bulk chunk also carries a run's progress and report (tables-07).
  const bulkChunk = useLazyModule(
    BULK_REVIEW_CHUNK,
    approval.selectionCount > 1 || approval.bulkApproving || bulkRun.result !== null,
  );
  // The module cache is the truth: after one failed chunk load this hook's
  // state stays failed, yet the next Approve re-imports the chunk and drafts
  // (an audited DRAFT_OUTREACH write), so the review must render from the
  // cache or that draft would sit unseen with no Cancel. LeadTable is
  // 'use no memo', so this read is fresh on every render.
  const reviewModule = reviewChunk.module ?? REVIEW_CHUNK.current();
  // Offline, a chunk that could not load is not a stale deploy: reloading
  // would fail too, so the failure copy says to reconnect instead.
  const online = useIsOnline();
  const ReviewInline = reviewModule?.LeadApproveReviewInline;
  const ReviewDialog = reviewModule?.LeadApproveReviewDialog;
  // The result line follows an approve made through the review, so it ships
  // in the review's chunk (loaded by then), not in the table's.
  const DecisionToast = reviewModule?.LeadTableDecisionToast;
  const bulkModule = bulkChunk.module ?? BULK_REVIEW_CHUNK.current();
  const BulkReview = bulkModule?.LeadBulkApproveReview;
  const BulkRunProgress = bulkModule?.LeadBulkRunProgress;
  const BulkRunResult = bulkModule?.LeadBulkRunResult;
  const reviewProps = openReview && {
    review: openReview,
    actorEmail,
    onConfirm: () => void review.confirm(),
    onCancel: flow.cancelReview,
    onRetryDraft: review.retryDraft,
    confirmRef: flow.confirmRef,
    shouldTakeFocus: flow.shouldConfirmTakeFocus,
    claimDraftLanding: review.claimDraftLanding,
    // The dialog sits in the top layer, above the evidence drawer: opening
    // a source moves the SAME review (same draft) into its expanded row,
    // then opens the drawer with focus handed to it (useLeadTableKeyboardFlow).
    onInspectEvidence: openReview.mode === 'dialog' ? flow.inspectEvidenceFromDialog : undefined,
  };
  const skipTargetId = `${useId()}-end`;
  // Row callbacks are made here, once, never per row, with one identity for
  // the table's life: a compiled LeadTableRow then gets identical props
  // (LeadTableBody) and skips its cells on an expand elsewhere.
  const rowCallbacks = useStableRowCallbacks({
    onToggleRow: (row: LeadSummary, open: boolean) => {
      setLastBorrowerId(row.borrower_id);
      flow.toggleRow(row, open);
    },
    onToggleSelect: (borrowerId: string, range: boolean) => (range
      ? approval.selectRange(borrowerId, rowIds)
      : approval.toggleSelect(borrowerId)),
    onApprove: flow.openReview,
    onReject: flow.openReject,
    onOpenDisposition: sales.openDisposition,
    onAssignmentUpdate: sales.applyLeadUpdate,
    onFocusRow: flow.focusRow,
  });
  // An Approve waiting on the review chunk (nothing drafted yet), or a
  // review whose chunk is still rendering in.
  const reviewOpeningFor = flow.reviewLoading
    ?? (openReview && !reviewModule ? openReview.borrowerId : null);

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
   * Export as CSV. The bytes are built client-side from the real /api/leads
   * payload the parent route already narrowed; there is no server-streamed
   * export (an owner decision, audit tables-08 step 2) and no synthesized
   * field — so PII stays suppressed by construction.
   *
   * Audit tables-08 (2026-09-21): the export used to serialise the raw
   * `leads` prop — ignoring the selection AND the sort — and its label
   * counted pre-gate rows, so it could announce "500 leads" and write zero.
   * It now writes the selection when one exists, otherwise the rows in their
   * on-screen order, and every count is the post-eligibility count. Wave 1a:
   * the download waits for a LEAD_EXPORT ledger row (useLeadCsvExport) and
   * the audit id is shown next to the button.
   */
  const csvExport = planLeadCsvExport(sortedLeads, approval.selectedIds);
  const csvExportCount = csvExport.rows.length;
  const csvExportNoun = csvExport.scope === 'selected_rows'
    ? 'selected'
    : csvExportCount === 1 ? 'lead' : 'leads';
  const { state: exportState, exportCsv: runExport } = useLeadCsvExport();
  const exportBlockedReason = exportContext?.exportBlockedReason ?? null;
  function exportCsv() {
    if (csvExportCount === 0 || exportBlockedReason) return;
    const rowOrder = sortKey === 'rank' ? 'rank' : `${sortKey} ${sortDir}`;
    void runExport({ plan: csvExport, approvals, exportContext, rowOrder });
  }

  function toggleSort(key: SortKey) {
    if (key === 'rank') {
      setSort(null);
      return;
    }
    setSort({ key, dir: activeSort?.key === key && sortDir === 'desc' ? 'asc' : 'desc' });
  }

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
      <LeadTableHeader
        headerStatus={headerStatus}
        singleKeysOn={singleKeysOn}
        approverActive={approverGate === null}
        actorEmail={actorEmail}
        view={view}
        onViewChange={onViewChange}
        exportState={exportState}
        canAccessAdmin={canAccessAdmin}
        csvExportCount={csvExportCount}
        csvExportNoun={csvExportNoun}
        csvExportExcluded={csvExport.excluded}
        exportBlockedReason={exportBlockedReason}
        onExport={exportCsv}
      />
      <LeadTableStatusChips
        growthAgentVerification={growthAgentVerification}
        campaignBindingState={campaignBindingState}
        requestedCampaignBinding={requestedCampaignBinding}
        campaignBinding={campaignBinding}
        expandedBorrowerId={expanded}
        approverGate={approverGate}
        actorEmail={actorEmail}
      />
      {/* Keyed by the row: each form owns its fields, and a new row starts clean. */}
      {approval.pendingReject && (
        <LeadRejectPanel
          key={approval.pendingReject}
          borrowerId={approval.pendingReject}
          reasonRef={flow.rejectReasonRef}
          onCancel={flow.cancelReject}
          onSubmit={(reasonCode, rationale) => void flow.submitReject(reasonCode, rationale)}
        />
      )}
      {sales.pendingDisposition && (
        <LeadDispositionPanel
          key={sales.pendingDisposition}
          borrowerId={sales.pendingDisposition}
          salesTeam={salesTeam}
          salesBusy={sales.salesBusy}
          initialLo={sales.dispositionInitialLo}
          onCancel={() => sales.setPendingDisposition(null)}
          onSubmit={(payload) => void sales.submitDisposition(payload)}
        />
      )}
      {exportState.status === 'done' && exportState.notice && (
        <div role="status" aria-live="polite" className="table-success" data-testid="lead-export-notice">
          {exportState.notice}
        </div>
      )}
      {exportState.status === 'error' && (
        <div role="alert" className="table-error" data-testid="lead-export-error">
          {exportState.message}
        </div>
      )}
      {flow.toast && DecisionToast && (
        <DecisionToast
          toast={flow.toast}
          hasReceipt={Boolean(approval.decisionReceipts[flow.toast.borrowerId]?.auditEventId)}
          rowListed={leadsById.has(flow.toast.borrowerId)}
          onViewReceipt={() => flow.toast && flow.viewReceipt(flow.toast.borrowerId)}
          onReviewRecentActivity={() => {
            openConsoleRecentActivity();
            flow.dismissToast();
          }}
          onDismiss={flow.dismissToast}
        />
      )}
      {/* Keyboard users skip past the table's rows in one step (tables-03). */}
      <a
        href={`#${skipTargetId}`}
        className="sr-skip-link lead-table__skip"
        onClick={(event) => {
          // Move focus without a fragment navigation: the target id is
          // generated, so it never belongs in the URL or the history stack.
          const target = document.getElementById(skipTargetId);
          if (!target) return;
          event.preventDefault();
          target.focus();
        }}
      >
        Skip table
      </a>
      <span className="sr-only" role="status" aria-live="polite" data-testid="lead-cursor-status">
        {flow.cursor.announcement}
      </span>
      {/* The approval result is spoken from a live region that is always
          mounted (a region inserted already filled can go unannounced); the
          visible toast carries the "View receipt" action. */}
      <span className="sr-only" role="status" aria-live="polite" data-testid="lead-decision-status">
        {flow.toast ? `Approved ${flow.toast.borrowerId}.` : ''}
      </span>
      {/* A bulk run's start, each quarter, a Stop and the result (tables-07). */}
      <span className="sr-only" role="status" aria-live="polite" data-testid="lead-bulk-run-status">
        {bulkRun.announcement}
      </span>
      <div
        ref={tableWrapRef}
        className={fillHeight ? 'tbl-wrap tbl-wrap--fill' : 'tbl-wrap'}
        role="region"
        tabIndex={0}
        aria-label="Ranked borrowers table scroll region"
        aria-keyshortcuts={singleKeysOn
          ? ariaKeyShortcuts([...LEAD_TABLE_KEYS.next, ...LEAD_TABLE_KEYS.previous, ...LEAD_TABLE_KEYS.toggle, ...LEAD_TABLE_KEYS.select])
          : undefined}
      >
        <table
          className={`tbl lead-table__table lead-table__table--${view}`}
          aria-rowcount={sortedLeads.length + 1 + (hasExpandedRow ? 1 : 0)}
        >
          <LeadTableHead
            columns={columns}
            sortKey={sortKey}
            sortDir={sortDir}
            onSort={toggleSort}
            headerCheckbox={approval.headerCheckboxState}
            selectAllDisabled={approval.selectableIds.length === 0 || approval.bulkApproving}
            onToggleSelectAll={approval.toggleSelectAll}
          />
          <LeadTableBody
            rows={sortedLeads}
            rowIds={rowIds}
            view={view}
            columnCount={columnCount}
            expanded={expanded}
            expandedRowIndex={expandedRowIndex}
            virtualized={shouldVirtualize}
            rowEstimatePx={rowEstimatePx}
            restoreScroll={restoreScroll}
            tableWrapRef={tableWrapRef}
            scrollToIndexRef={scrollToIndexRef}
            virtualScrollRef={virtualScrollRef}
            approvals={approvals}
            selectedIds={approval.selectedIds}
            pendingDecisions={approval.pendingDecisions}
            decisionReceipts={approval.decisionReceipts}
            cursorId={flow.cursor.cursorId}
            campaignBindingBlocked={campaignBindingBlocked}
            approverGate={approverGate}
            bulkApproving={approval.bulkApproving}
            salesBusy={sales.salesBusy}
            salesTeamCount={salesTeam.length}
            shortcutsLive={singleKeysOn}
            reviewSlot={reviewProps && ReviewInline && openReview?.mode === 'inline'
              ? { borrowerId: openReview.borrowerId, node: <ReviewInline {...reviewProps} /> }
              : null}
            // From the flow state, not from whether the review chunk rendered.
            reviewBorrowerId={openReview?.borrowerId ?? flow.reviewLoading}
            rowCallbacks={rowCallbacks}
          />
        </table>
      </div>
      <span id={skipTargetId} className="sr-only lead-table__skip-target" tabIndex={-1}>End of ranked borrowers table</span>
      {reviewProps && ReviewDialog && openReview?.mode === 'dialog' && <ReviewDialog {...reviewProps} />}
      {flow.reviewLoadFailed ? (
        <div role="alert" className="table-error" data-testid="lead-approve-review-loading">
          {online
            ? 'The approval review could not load, so no draft was generated and nothing was approved. Reload the page, then approve again.'
            : 'You are offline, so the approval review could not open. Nothing was drafted or approved. Reconnect, then approve again.'}
        </div>
      ) : reviewOpeningFor && (
        <div role="status" className="table-neutral" data-testid="lead-approve-review-loading">
          Opening the review for {reviewOpeningFor}…
        </div>
      )}
      {/* The toolbar stays while a run is on the wire, even when a filter
          change took every selected row off screen. */}
      {(approval.selectionCount > 0 || approval.bulkApproving) && (
        <LeadTableBulkActions
          selectionCount={approval.selectionCount}
          selectedApprovalEligibleCount={approval.selectedApprovalEligibleCount}
          bulkApproving={approval.bulkApproving}
          bulkRationaleOpen={approval.bulkRationaleOpen}
          campaignBindingBlocked={campaignBindingBlocked}
          approverGate={approverGate}
          salesTeam={salesTeam}
          salesBusy={sales.salesBusy}
          selectedAssignee={sales.selectedAssignee}
          onSelectedAssigneeChange={sales.setSelectedAssignee}
          onAssign={assignSelected}
          onClearSelection={approval.clearSelection}
          onBulkApprove={(rationale) => flow.bulkApproveFromToolbar(sampleDraftsRef.current, rationale)}
          bulkApproveBtnRef={approval.bulkApproveBtnRef}
          bulkRationaleRef={approval.bulkRationaleRef}
          assigneeRef={assigneeRef}
          shortcutsLive={singleKeysOn}
          samplesShown={samplesShown}
          runKind={bulkRun.progress?.kind ?? null}
          samplesCoverAllOffers={samplesCoverAllOffers}
          bulkChunkFailed={bulkChunk.failed && !bulkModule}
          runStatus={!bulkRun.progress
            ? null
            : BulkRunProgress
              ? <BulkRunProgress progress={bulkRun.progress} onStop={bulkRun.requestStop} />
              // The bulk chunk failed to load: a static line still counts and stops the run.
              : bulkChunk.failed && <LeadBulkRunProgressFallback progress={bulkRun.progress} onStop={bulkRun.requestStop} />}
          gateReview={BulkReview ? (
            <BulkReview
              // Samples drafted under one campaign binding go with it.
              key={campaignBindingKey}
              leads={flow.eligibleSelectedIds().map((id) => leadsById.get(id)).filter((lead) => lead !== undefined)}
              canStartApproval={approval.canStartApproval}
              draftForApproval={approval.draftForApproval}
              onSamplesChange={(drafts) => {
                sampleDraftsRef.current = drafts;
                setSamplesShown(drafts.size > 0);
              }}
              onCoverageChange={(coverage) => setSamplesCoverAllOffers(coverage.complete)}
            />
          ) : null}
        />
      )}
      {bulkRun.result && (BulkRunResult
        ? <BulkRunResult result={bulkRun.result} onDismiss={bulkRun.dismissResult} />
        : bulkChunk.failed && <LeadBulkRunResultFallback result={bulkRun.result} onDismiss={bulkRun.dismissResult} />)}
      {approval.bulkToast && (
        <LeadTableBulkToast
          toast={approval.bulkToast}
          onReviewRecentActivity={() => {
            openConsoleRecentActivity();
            approval.setBulkToast(null);
          }}
        />
      )}
      <LeadTableFooter
        loadedCount={leads.length}
        totalMatching={totalMatching}
        truncatedAt={truncatedAt}
        sortKey={sortKey}
        sortedCount={sortedLeads.length}
        onResetSort={() => toggleSort('rank')}
      />
    </div>
  );
}
