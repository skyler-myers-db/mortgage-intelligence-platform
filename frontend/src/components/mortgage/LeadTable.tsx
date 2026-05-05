import { Fragment, useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type KeyboardEvent as ReactKeyboardEvent } from 'react';
import { Link } from 'react-router-dom';
import type { LeadSummary } from '../../types';
import { Icon } from '../Icon';
import { Chip, Button, EvidenceChip } from '../Primitives';
import { ScoreBadge } from './ScoreBadge';
import { ConfidenceMeter } from './ConfidenceMeter';
import { useApp } from '../AppContext';
import { api, ApiError, isAbortError } from '../../lib/api';
import { DRAWER_SOURCES } from '../../lib/drawerSources';
import { segmentColor, segmentName } from '../../lib/segmentMetadata';

/**
 * LeadTable — prototype `.surface` + `.tbl` BEM. Sticky thead, hover, row
 * expand into a mini borrower-detail preview. Approvals track per-row via
 * AppContext; a chip on the rightmost column shows Pending / Approved /
 * Rejected.
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

/** Concurrency cap for the bulk-approve client-side loop. */
const BULK_APPROVE_CONCURRENCY = 3;

/**
 * Return true when `el` is an editable element that the window-level
 * hotkey handler must skip over. Exported for unit tests — the
 * actual hotkey listener uses both this and `document.activeElement`
 * so typing "a" into a text field can never trigger bulk-approve.
 * R5-12 (2026-04-23).
 */
export function isEditableTarget(el: Element | null | undefined): boolean {
  if (!el) return false;
  const tag = (el as HTMLElement).tagName;
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true;
  return (el as HTMLElement).isContentEditable === true;
}

/**
 * Chunk a list into groups of `size`. Used by the bulk-approve loop to
 * bound in-flight POSTs to the approve endpoint without inventing a
 * server-side bulk API.
 */
function chunk<T>(items: T[], size: number): T[][] {
  if (size <= 0) return [items];
  const out: T[][] = [];
  for (let i = 0; i < items.length; i += size) {
    out.push(items.slice(i, i + size));
  }
  return out;
}

function RowPreview({ lead }: { lead: LeadSummary }) {
  const { setLastBorrowerId, saveLead, isLeadSaved } = useApp();
  // Prefer the real Cotality CLIP projected by the backend (2026-04-22
  // contract addition). Fall back to a borrower-id derived placeholder
  // only for rows predating that projection (empty string).
  const clipValue = lead.clip && lead.clip.length > 0
    ? lead.clip
    : `clip_${lead.borrower_id.toLowerCase().replace('-', '')}`;
  const saved = isLeadSaved(lead.borrower_id);
  const saveCurrentLead = () => {
    saveLead({
      borrower_id: lead.borrower_id,
      city: lead.city,
      state: lead.state,
      zip: lead.zip,
      recommended_offer: lead.recommended_offer,
      opportunity_score: lead.opportunity_score,
      confidence: lead.confidence,
    });
  };
  return (
    <div className="tbl__expand-inner tbl__expand-inner--lead">
      <div>
        <div className="eyebrow mb-2">Customer 360 preview</div>
        <div className="preview-grid">
          <Cell k="CLIP"          v={clipValue} mono />
          <Cell k="Location"      v={`${lead.city}, ${lead.state} · ${lead.zip}`} />
          <Cell k="Equity"        v={`$${(lead.equity_estimate / 1000).toFixed(0)}k`} mono />
          <Cell k="Rate spread"   v={`+${lead.rate_spread_bps} bps`} mono />
          <Cell k="Score"         v={`${lead.opportunity_score}`} mono />
          <Cell k="Confidence"    v={`${lead.confidence}%`} mono />
        </div>
        <div className="eyebrow mt-4 mb-2">Segments</div>
        <div className="chip-row">
          {lead.segment_codes.map((sid) => {
            const color = segmentColor(sid);
            return (
              <span
                key={sid}
                className="chip chip--segment"
                style={{ '--chip-hue': color } as CSSProperties}
              >
                {segmentName(sid)}
              </span>
            );
          })}
        </div>
      </div>

      <div>
        <div className="eyebrow mb-2">Why now</div>
        <p className="body flush">{lead.why_now}</p>
        <div className="chip-row mt-3">
          <span className="muted fs-11">Evidence:</span>
          {/*
            Prototype-parity-audit P1-5 (2026-05-04): the row preview
            previously surfaced only two chips — Rate + equity ruleset and
            Next-best-offer model — which understated the depth of evidence
            the platform actually carries. Borrower 360 already renders 5+
            chips per dossier; the inline lead-queue preview should match
            that posture so an LO scrolling the queue can see what each
            recommendation is grounded in without opening the dossier. We
            render a fixed core set (CLIP + NBO scoring) and append
            data-driven chips for whichever signals the row carries
            (Permit when has_permit, MLS when listed_for_sale, lien-status
            when current_lien_balance > 0, AVM when equity_estimate > 0).
            Each chip routes into the existing EvidenceDrawer with the
            matching DRAWER_SOURCES entry so lineage stays one click away.
          */}
          <EvidenceChip source={DRAWER_SOURCES.itm}>Rate + equity ruleset</EvidenceChip>
          <EvidenceChip source={DRAWER_SOURCES.leadScore}>Lead score model</EvidenceChip>
          <EvidenceChip source={DRAWER_SOURCES.nbo}>Next-best-offer model</EvidenceChip>
          <EvidenceChip source={DRAWER_SOURCES.population}>CLIP · Owner Link</EvidenceChip>
          {lead.equity_estimate > 0 && (
            <EvidenceChip source={DRAWER_SOURCES.itm}>AVM equity</EvidenceChip>
          )}
          {(lead.current_lien_balance ?? 0) > 0 && (
            <EvidenceChip source={DRAWER_SOURCES.population}>Voluntary lien</EvidenceChip>
          )}
          {lead.has_permit === true && (
            <EvidenceChip source={DRAWER_SOURCES.permit}>Recent permit</EvidenceChip>
          )}
          {lead.listed_for_sale === true && (
            // Re-uses the population drawer until a dedicated MLS source
            // entry lands; the lineage already references mortgage records,
            // and routing into a "TBD" drawer would be more confusing than
            // grouping with the broader public-records lineage.
            <EvidenceChip source={DRAWER_SOURCES.population}>MLS listing</EvidenceChip>
          )}
        </div>
      </div>

      <div>
        <div className="eyebrow mb-2">Next-best-offer</div>
        <div className="surface preview-offer-card">
          <div className="split-row">
            <div className="offer-title">{lead.recommended_offer}</div>
            <ScoreBadge value={lead.opportunity_score} />
          </div>
          <div className="muted fs-12 mt-1">
            Confidence <ConfidenceMeter value={lead.confidence} compact />
          </div>
          <div className="chip-row mt-3">
            <Link
              className="btn btn--primary btn--sm"
              to={`/borrower-360/${lead.borrower_id}`}
              onClick={() => setLastBorrowerId(lead.borrower_id)}
            >
              Open Borrower 360
            </Link>
            <Button
              variant={saved ? 'ghost' : 'default'}
              size="sm"
              icon={saved ? 'check' : 'tag'}
              onClick={saveCurrentLead}
              aria-label={`${saved ? 'Saved' : 'Save'} borrower ${lead.borrower_id}`}
            >
              {saved ? 'Saved' : 'Save lead'}
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}

function Cell({ k, v, mono }: { k: string; v: string; mono?: boolean }) {
  return (
    <div>
      <div className="field__label">{k}</div>
      <div className={`field__value ${mono ? 'mono num' : ''}`}>{v}</div>
    </div>
  );
}

export function LeadTable({ leads }: { leads: LeadSummary[] }) {
  const [expanded, setExpanded] = useState<string | null>(leads[0]?.borrower_id ?? null);
  const { approvals, setApproval, setLastBorrowerId } = useApp();
  const [pendingApproval, setPendingApproval] = useState<Record<string, boolean>>({});
  const [approvalError, setApprovalError] = useState<string | null>(null);
  // Bulk-approve state. `selectedIds` is a Set so toggling is O(1); we
  // copy-on-write when updating to keep React's reference check happy.
  const [selectedIds, setSelectedIds] = useState<Set<string>>(() => new Set());
  const [bulkApproving, setBulkApproving] = useState<boolean>(false);
  // R5-04 (2026-04-23): synchronous in-flight latches. `setState` is
  // async so two rapid clicks can both read `bulkApproving=false` before
  // either commit schedules, producing two parallel approve loops that
  // each write an audit row per borrower. `useRef` gives us a
  // synchronous read/write we can flip before returning from the click
  // handler; the existing React state still drives the disabled UI.
  const bulkInFlightRef = useRef<boolean>(false);
  const rowInFlightRef = useRef<Record<string, boolean>>({});
  // Tracks the bulk-approve loop's AbortController so unmount can
  // cancel the remaining in-flight POSTs. Round-2 hole-finder #10/#11,
  // 2026-04-23.
  const bulkAbortRef = useRef<AbortController | null>(null);
  // Last bulk result surfaced as a compact toast. Clears on the next bulk
  // run or when the user dismisses it (auto-dismiss after 4s).
  //
  // `network` is the subset of `fail` that failed with an unreachable
  // backend (ApiError.status === null) — these rows never reached the
  // audit table and the approver should retry them explicitly.
  // Hole-finder finding #2, 2026-04-23.
  //
  // `aborted` rows fall in an ambiguous state: the client cancelled the
  // POST mid-flight on unmount, but the server may have already
  // committed the audit row. We surface these as a distinct "check the
  // audit log" message rather than pushing retry language, because a
  // blind retry can produce a duplicate audit row. R5-21 (2026-04-23).
  //
  // TODO: once R5-01 (server-side idempotency keys on /api/outreach/
  // approve) lands, aborted becomes safe to retry and this branch can
  // collapse back into the network-retry path.
  const [bulkToast, setBulkToast] = useState<
    { ok: number; fail: number; network: number; aborted: number } | null
  >(null);

  useEffect(() => {
    if (expanded) setLastBorrowerId(expanded);
  }, [expanded, setLastBorrowerId]);

  /**
   * Approve from the queue without leaving the page. Uses the same
   * `/api/outreach/approve` endpoint Offer Orchestrator calls. We mark
   * the row as 'approved' in AppContext optimistically on success so the
   * chip flips immediately and stays flipped on route change.
   *
   * Returns a tagged outcome so bulk-approve can distinguish a network
   * drop ("request never reached the audit table") from a backend
   * rejection ("server said no"). Hole-finder finding #2, 2026-04-23.
   */
  const approveLead = useCallback(
    async (borrowerId: string, signal?: AbortSignal): Promise<'ok' | 'network' | 'backend' | 'aborted' | 'duplicate'> => {
      // R5-04: synchronous latch check. setState is async, so a rapid
      // second click could slip in before `pendingApproval[id]` flips
      // to true and produce a second audit row. The ref flips
      // immediately.
      if (rowInFlightRef.current[borrowerId]) return 'duplicate';
      rowInFlightRef.current[borrowerId] = true;
      setApprovalError(null);
      setPendingApproval((p) => ({ ...p, [borrowerId]: true }));
      try {
        const res = await api.approve(borrowerId, {}, signal);
        if (res.approved) {
          setApproval(borrowerId, 'approved');
          return 'ok';
        }
        setApprovalError(`Approve failed for ${borrowerId}: endpoint returned approved=false.`);
        return 'backend';
      } catch (err: unknown) {
        if (isAbortError(err)) return 'aborted';
        const isNetwork = err instanceof ApiError && err.status === null;
        setApprovalError(
          err instanceof Error
            ? `Couldn't approve ${borrowerId}: ${err.message}`
            : `Couldn't approve ${borrowerId}.`,
        );
        return isNetwork ? 'network' : 'backend';
      } finally {
        rowInFlightRef.current[borrowerId] = false;
        setPendingApproval((p) => {
          const { [borrowerId]: _discard, ...rest } = p;
          return rest;
        });
      }
    },
    [setApproval],
  );

  /**
   * Reject from the queue without leaving the page. Audit finding
   * 2026-04-22: this used to only mutate AppContext, so rejected
   * borrowers left no durable trace. Now calls `/api/outreach/reject`
   * which writes `mip_app.approvals` (action='reject') +
   * `mip_app.action_audit` (OUTREACH_REJECT) and fires the same
   * lifecycle-sync debounce the approve path uses.
   *
   * On failure we surface the error but do NOT flip the local state,
   * so the user can retry. Matches the approve flow's error posture.
   */
  const rejectLead = useCallback(
    async (borrowerId: string) => {
      // R5-04: synchronous latch — see approveLead above.
      if (rowInFlightRef.current[borrowerId]) return;
      rowInFlightRef.current[borrowerId] = true;
      setApprovalError(null);
      setPendingApproval((p) => ({ ...p, [borrowerId]: true }));
      try {
        const res = await api.reject(borrowerId);
        if (res.rejected) {
          setApproval(borrowerId, 'rejected');
        } else {
          setApprovalError(`Reject failed for ${borrowerId}: endpoint returned rejected=false.`);
        }
      } catch (err: unknown) {
        if (isAbortError(err)) return;
        setApprovalError(
          err instanceof Error
            ? `Couldn't reject ${borrowerId}: ${err.message}`
            : `Couldn't reject ${borrowerId}.`,
        );
      } finally {
        rowInFlightRef.current[borrowerId] = false;
        setPendingApproval((p) => {
          const { [borrowerId]: _discard, ...rest } = p;
          return rest;
        });
      }
    },
    [setApproval],
  );

  /**
   * Toggle one row's selection. Called by the row checkbox onChange; the
   * checkbox click is stopped from bubbling in the markup so the row
   * still expands/collapses independently.
   */
  const toggleSelect = useCallback((borrowerId: string) => {
    setSelectedIds((cur) => {
      const next = new Set(cur);
      if (next.has(borrowerId)) next.delete(borrowerId);
      else next.add(borrowerId);
      return next;
    });
  }, []);

  const clearSelection = useCallback(() => {
    setSelectedIds(new Set());
  }, []);

  // IDs that are actually eligible for bulk approval: not already approved
  // or rejected. The "select all" header checkbox targets this subset so
  // already-decided rows don't get bulk-approved again.
  const eligibleIds = useMemo(
    () =>
      leads
        .filter((l) => {
          const serverDecided = l.approval_status === 'approved' || l.approval_status === 'rejected';
          return !approvals[l.borrower_id] && !serverDecided;
        })
        .map((l) => l.borrower_id),
    [leads, approvals],
  );

  // Indeterminate state for the header checkbox: some (but not all)
  // eligible rows selected. We also reflect "all eligible selected" as
  // the checked state.
  const headerCheckboxState = useMemo(() => {
    if (eligibleIds.length === 0) return { checked: false, indeterminate: false };
    const selectedEligibleCount = eligibleIds.filter((id) => selectedIds.has(id)).length;
    if (selectedEligibleCount === 0) return { checked: false, indeterminate: false };
    if (selectedEligibleCount === eligibleIds.length) return { checked: true, indeterminate: false };
    return { checked: false, indeterminate: true };
  }, [eligibleIds, selectedIds]);

  const toggleSelectAll = useCallback(() => {
    setSelectedIds((cur) => {
      // If any eligible rows remain unselected, select all eligible. Else
      // clear the selection.
      const allEligibleSelected =
        eligibleIds.length > 0 && eligibleIds.every((id) => cur.has(id));
      if (allEligibleSelected) return new Set();
      return new Set(eligibleIds);
    });
  }, [eligibleIds]);

  /**
   * Bulk-approve: loop `api.approve()` per selected id in chunks of
   * BULK_APPROVE_CONCURRENCY. We deliberately do NOT invent a server-side
   * bulk endpoint — the audit trail wants one row per approval.
   *
   * Successes drop out of the selection set; failures stay selected so
   * the operator can retry. A compact toast summarizes ok/fail counts.
   */
  const bulkApprove = useCallback(async () => {
    // R5-04: synchronous latch. React setState is async, so two rapid
    // clicks can both read `bulkApproving=false` before either commit
    // schedules — producing two parallel loops with the same selection
    // and two audit rows per borrower. Flip the ref before any await.
    if (bulkInFlightRef.current || bulkApproving) return;
    bulkInFlightRef.current = true;
    // Snapshot which ids to run: skip already-decided rows silently.
    const ids = [...selectedIds].filter((id) => !approvals[id]);
    if (ids.length === 0) {
      bulkInFlightRef.current = false;
      return;
    }
    // One controller for the whole bulk loop; unmount aborts every
    // still-inflight POST. sessionStorage stashes the partial result so
    // the next mount can flash "N landed, rest aborted" — otherwise
    // the user sees no feedback that their bulk action got cut short.
    const ctrl = new AbortController();
    bulkAbortRef.current = ctrl;
    setBulkApproving(true);
    setBulkToast(null);
    let ok = 0;
    let fail = 0;
    let network = 0;
    let aborted = 0;
    // `failedIds` is only the subset safe to retry (backend/network
    // rejections — the server definitely did not commit). Aborted ids
    // stay out of this list because the server may have committed and
    // a retry would duplicate the audit row. R5-21.
    const failedIds: string[] = [];
    const abortedIds: string[] = [];
    for (const group of chunk(ids, BULK_APPROVE_CONCURRENCY)) {
      if (ctrl.signal.aborted) {
        aborted += group.length;
        abortedIds.push(...group);
        continue;
      }
      const results = await Promise.all(group.map((id) => approveLead(id, ctrl.signal)));
      results.forEach((outcome, i) => {
        if (outcome === 'ok') {
          ok += 1;
        } else if (outcome === 'aborted') {
          aborted += 1;
          abortedIds.push(group[i]);
        } else {
          fail += 1;
          if (outcome === 'network') network += 1;
          failedIds.push(group[i]);
        }
      });
    }
    if (ctrl.signal.aborted) {
      // Stash the partial result so the next mount can flash it. We
      // accept that the user may never come back to this page; the
      // alternative (loud toast on unmount) wouldn't render anyway.
      try {
        sessionStorage.setItem(
          'mip.bulkApprove.lastCancelled',
          JSON.stringify({ ok, aborted, ts: Date.now() }),
        );
      } catch {
        // private mode or quota — ignore
      }
      bulkInFlightRef.current = false;
      return;
    }
    // Quieten unused-var lint: abortedIds is tracked for future reuse
    // (R5-01 idempotency can retry by id) but not needed in this frame.
    void abortedIds;
    // Replace selection with the retryable subset so retries are
    // trivial. Aborted ids are deliberately NOT re-selected — the
    // server may have committed them and a blind re-click would
    // duplicate the audit row. R5-21 (2026-04-23).
    setSelectedIds(new Set(failedIds));
    setBulkApproving(false);
    setBulkToast({ ok, fail, network, aborted });
    bulkAbortRef.current = null;
    bulkInFlightRef.current = false;
  }, [bulkApproving, selectedIds, approvals, approveLead]);

  // On mount: if the previous mount left a partial bulk-approve snapshot
  // in sessionStorage (user navigated away mid-loop), flash a compact
  // toast so the operator knows how many landed.
  useEffect(() => {
    try {
      const raw = sessionStorage.getItem('mip.bulkApprove.lastCancelled');
      if (!raw) return;
      sessionStorage.removeItem('mip.bulkApprove.lastCancelled');
      const parsed = JSON.parse(raw) as { ok?: number; aborted?: number; ts?: number };
      // Drop stale messages (older than 10 minutes) — they're probably
      // from a much-earlier session.
      if (parsed?.ts && Date.now() - parsed.ts > 10 * 60 * 1000) return;
      const ok = parsed.ok ?? 0;
      const aborted = parsed.aborted ?? 0;
      if (ok + aborted === 0) return;
      // R5-21: route unmounted mid-loop. Aborted ids are in ambiguous
      // state (server may have committed). Surface a "check audit log"
      // message rather than mixing them into the retryable `fail` count.
      setBulkToast({ ok, fail: 0, network: 0, aborted });
    } catch {
      // malformed payload — ignore
    }
  }, []);

  // Abort the bulk-approve loop on unmount so the remaining POSTs
  // cancel cleanly.
  useEffect(() => {
    return () => {
      bulkAbortRef.current?.abort();
    };
  }, []);

  // Auto-dismiss the toast after 4s so it doesn't pile up next to the
  // action bar.
  useEffect(() => {
    if (!bulkToast) return;
    const t = window.setTimeout(() => setBulkToast(null), 4000);
    return () => window.clearTimeout(t);
  }, [bulkToast]);

  /**
   * Keyboard: A approves / R rejects the expanded row; Shift+A fires
   * bulk-approve when >= 1 row is selected. We listen at the window
   * level but bail out if focus is inside an editable element so typing
   * in the Genie chat or a filter input never triggers approval.
   */
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
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
      // Shift+A: bulk approve. Takes precedence over single-row A when
      // any row is selected.
      if (key === 'a' && e.shiftKey) {
        if (selectedIds.size === 0 || bulkApproving) return;
        e.preventDefault();
        void bulkApprove();
        return;
      }
      if (!expanded) return;
      if (key === 'a') {
        if (approvals[expanded] === 'approved') return;
        e.preventDefault();
        void approveLead(expanded);
      } else if (key === 'r') {
        if (approvals[expanded] === 'rejected') return;
        e.preventDefault();
        void rejectLead(expanded);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [expanded, approvals, approveLead, rejectLead, selectedIds, bulkApproving, bulkApprove]);

  const stop = (e: ReactKeyboardEvent | React.MouseEvent) => e.stopPropagation();

  const selectionCount = selectedIds.size;

  /**
   * Export the currently-visible leads as CSV. Client-side only: the
   * bytes come straight from the `leads` prop the caller already passed
   * in (which is the real /api/leads payload after any segment/filter
   * narrowing the parent route applied). We do NOT invent a server-side
   * export endpoint or synthesize fields the payload doesn't carry — so
   * PII stays suppressed by construction.
   */
  const exportCsv = useCallback(() => {
    if (leads.length === 0) return;
    const header = [
      'borrower_id',
      'clip',
      'city',
      'state',
      'zip',
      'segments',
      'equity_estimate',
      'rate_spread_bps',
      'opportunity_score',
      'confidence',
      'recommended_offer',
      'approval_status',
    ];
    const escape = (v: string): string =>
      /[",\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v;
    const rows = leads.map((l) =>
      [
        l.borrower_id,
        l.clip ?? '',
        l.city,
        l.state,
        l.zip,
        l.segment_codes.join('|'),
        String(l.equity_estimate),
        String(l.rate_spread_bps),
        String(l.opportunity_score),
        String(l.confidence),
        l.recommended_offer,
        approvals[l.borrower_id] ?? l.approval_status ?? 'pending',
      ]
        .map(escape)
        .join(','),
    );
    const csv = [header.join(','), ...rows].join('\n');
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
  }, [leads, approvals]);

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
              Click a row to expand the preview. Keyboard: <kbd>A</kbd> approve, <kbd>R</kbd> reject the expanded row.
            </div>
          </div>
        </div>
        <div className="lead-table__header-actions">
          <Chip variant="neutral" icon="shield">PII suppressed</Chip>
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
      <div className="tbl-wrap">
        <table className="tbl">
          <thead>
            <tr>
              <th className="tbl-cell--select">
                <input
                  type="checkbox"
                  aria-label="Select all eligible leads"
                  checked={headerCheckboxState.checked}
                  ref={(el) => {
                    if (el) el.indeterminate = headerCheckboxState.indeterminate;
                  }}
                  disabled={eligibleIds.length === 0 || bulkApproving}
                  onChange={toggleSelectAll}
                  onClick={stop}
                  data-testid="lead-select-all"
                />
              </th>
              <th className="tbl-cell--narrow"></th>
              <th>Borrower</th>
              <th>Location</th>
              <th>Segments</th>
              <th className="tbl-cell--right">Equity</th>
              <th className="tbl-cell--right">Rate Δ (bps)</th>
              <th>Next-best-offer</th>
              <th className="tbl-cell--right">Score</th>
              <th>Confidence</th>
              <th>Approval</th>
            </tr>
          </thead>
          <tbody>
            {leads.map((lead) => {
              const isOpen = expanded === lead.borrower_id;
              // Prefer in-session AppContext override (set optimistically on
              // approve/reject); fall back to the server-projected
              // approval_status so a page reload doesn't make approved
              // borrowers look pending. Round-2 hole-finder #12, 2026-04-23.
              const serverStatus = lead.approval_status;
              const approval = approvals[lead.borrower_id]
                ?? (serverStatus === 'approved' || serverStatus === 'rejected'
                    ? serverStatus
                    : undefined);
              const isSelected = selectedIds.has(lead.borrower_id);
              const isEligible = !approval;
              return (
                <Fragment key={lead.borrower_id}>
                  <tr
                    className={isOpen ? 'is-expanded' : ''}
                    tabIndex={0}
                    role="button"
                    aria-expanded={isOpen}
                    aria-label={`Lead ${lead.borrower_id}, ${isOpen ? 'expanded' : 'collapsed'}. Press Enter or Space to toggle preview; A to approve, R to reject.`}
                    onClick={() => {
                      setLastBorrowerId(lead.borrower_id);
                      setExpanded(isOpen ? null : lead.borrower_id);
                    }}
                    onKeyDown={(e) => {
                      // R5-10 (2026-04-23): make rows toggleable from
                      // the keyboard so A/R hotkeys work without a
                      // prior mouse click. We only intercept when the
                      // focus target is the row itself — if focus is
                      // on the nested checkbox or approve button,
                      // their own handlers run and we bail.
                      if (e.target !== e.currentTarget) return;
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault();
                        setLastBorrowerId(lead.borrower_id);
                        setExpanded(isOpen ? null : lead.borrower_id);
                      }
                    }}
                  >
                    <td className="tbl-cell--select" onClick={stop}>
                      <input
                        type="checkbox"
                        aria-label={`Select lead ${lead.borrower_id}`}
                        checked={isSelected}
                        disabled={!isEligible || bulkApproving}
                        onChange={() => toggleSelect(lead.borrower_id)}
                        onClick={stop}
                        data-testid={`lead-select-${lead.borrower_id}`}
                      />
                    </td>
                    <td>
                      <Icon name={isOpen ? 'down' : 'chevright'} size={14} className="muted" />
                    </td>
                    <td className="is-primary">
                      <div className="mono lead-table__borrower">{lead.borrower_id}</div>
                      <div className="mono muted lead-table__clip">
                        {lead.clip && lead.clip.length > 0
                          ? lead.clip
                          : `clip_${lead.borrower_id.toLowerCase().replace(/-/g, '')}`}
                      </div>
                    </td>
                    <td>
                      {lead.city}, {lead.state}
                      <div className="muted mono lead-table__zip">{lead.zip}</div>
                    </td>
                    <td>
                      <div className="lead-table__segments">
                        {lead.segment_codes.slice(0, 2).map((sid) => {
                          const color = segmentColor(sid);
                          return (
                            <span
                              key={sid}
                              className="chip chip--segment chip--compact"
                              style={{ '--chip-hue': color } as CSSProperties}
                            >
                              {segmentName(sid)}
                            </span>
                          );
                        })}
                        {lead.segment_codes.length > 2 && (
                          <span className="chip chip--neutral chip--compact">
                            +{lead.segment_codes.length - 2}
                          </span>
                        )}
                      </div>
                    </td>
                    <td className="num tbl-cell--right">${(lead.equity_estimate / 1000).toFixed(0)}k</td>
                    <td
                      className={`num tbl-cell--right ${lead.rate_spread_bps >= 75 ? 'lead-table__rate--positive' : 'lead-table__rate--neutral'}`}
                    >
                      +{lead.rate_spread_bps}
                    </td>
                    <td>
                      <span className="mono fs-12 text-1">{lead.recommended_offer}</span>{' '}
                      <EvidenceChip source={DRAWER_SOURCES.nbo}>{DRAWER_SOURCES.nbo.short}</EvidenceChip>
                    </td>
                    <td className="tbl-cell--right"><ScoreBadge value={lead.opportunity_score} /></td>
                    <td><ConfidenceMeter value={lead.confidence} compact /></td>
                    <td
                      className="tbl-cell--approval"
                      data-testid={`lead-approval-cell-${lead.borrower_id}`}
                    >
                      {approval === 'approved' && <Chip variant="success" icon="check">Approved</Chip>}
                      {approval === 'rejected' && <Chip variant="danger" icon="cross">Rejected</Chip>}
                      {!approval && (
                        <div
                          className="lead-table__approval-actions"
                          onClick={stop}
                        >
                          <Button
                            variant="primary"
                            size="sm"
                            icon="check"
                            disabled={Boolean(pendingApproval[lead.borrower_id])}
                            onClick={(e) => {
                              e.stopPropagation();
                              void approveLead(lead.borrower_id);
                            }}
                            aria-label={`Approve ${lead.borrower_id}`}
                            data-testid={`lead-approve-${lead.borrower_id}`}
                          >
                            {pendingApproval[lead.borrower_id] ? 'Approving…' : 'Approve'}
                          </Button>
                          <button
                            type="button"
                            className="btn btn--sm lead-table__reject"
                            aria-label={`Reject ${lead.borrower_id}`}
                            title="Reject"
                            disabled={Boolean(pendingApproval[lead.borrower_id])}
                            onClick={(e) => {
                              e.stopPropagation();
                              void rejectLead(lead.borrower_id);
                            }}
                            data-testid={`lead-reject-${lead.borrower_id}`}
                          >
                            <Icon name="cross" size={12} />
                          </button>
                        </div>
                      )}
                    </td>
                  </tr>
                  {isOpen && (
                    <tr className="tbl__expand">
                      <td colSpan={11}>
                        <RowPreview lead={lead} />
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
      {selectionCount > 0 && (
        <div
          role="toolbar"
          aria-label="Bulk actions"
          data-testid="lead-bulk-actions"
          className="bulk-actions"
        >
          <div className="bulk-actions__label">
            <span className="mono num">{selectionCount}</span> {selectionCount === 1 ? 'lead' : 'leads'} selected
          </div>
          <div className="bulk-actions__controls">
            {bulkToast && (
              <span
                role="status"
                aria-live="polite"
                data-testid="lead-bulk-toast"
                className={`bulk-actions__toast ${
                  bulkToast.aborted > 0 || bulkToast.network > 0
                    ? 'bulk-actions__toast--danger'
                    : bulkToast.fail > 0
                      ? 'bulk-actions__toast--warn'
                      : 'bulk-actions__toast--ok'
                }`}
              >
                {bulkToast.ok} approved
                {bulkToast.fail > 0 ? `, ${bulkToast.fail} failed` : ''}
                {bulkToast.network > 0
                  ? ` (${bulkToast.network} network dropped — retry)`
                  : ''}
                {/*
                  R5-21: aborted rows are in an ambiguous state — the
                  client cancelled the POST but the server may have
                  committed. Do NOT encourage retry; direct the user to
                  the audit log instead. TODO: once R5-01 (server-side
                  idempotency) lands, retry becomes safe.
                */}
                {bulkToast.aborted > 0
                  ? ` · ${bulkToast.aborted} cancelled in flight — unknown state, check the audit log`
                  : ''}
              </span>
            )}
            <Button
              variant="ghost"
              size="sm"
              onClick={clearSelection}
              disabled={bulkApproving}
              data-testid="lead-bulk-clear"
            >
              Clear selection
            </Button>
            <Button
              variant="primary"
              size="sm"
              icon={bulkApproving ? undefined : 'check'}
              onClick={() => void bulkApprove()}
              disabled={bulkApproving || selectionCount === 0}
              data-testid="lead-bulk-approve"
              aria-label={`Approve ${selectionCount} leads`}
            >
              {bulkApproving ? 'Approving…' : `Approve ${selectionCount} leads`}
            </Button>
          </div>
        </div>
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
      </div>
    </div>
  );
}
