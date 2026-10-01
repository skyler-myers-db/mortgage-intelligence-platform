import { useEffect, useLayoutEffect, useRef, useState, type ComponentType } from 'react';
import { Link } from 'react-router';
import type { LeadSummary } from '../../types';
import type { OutreachDraftResult } from '../../lib/apiTypes';
import { auditEventHref } from '../../lib/auditLinks';
import { pushEscapeLayer } from '../../lib/escapeStack';
import { hasOpenOverlay, isEditableElement, registerKeyBinding } from '../../lib/keymap';
import { Icon } from '../Icon';
import { Button, SurfaceTitle } from '../Primitives';
import type { LeadApproveReviewProps } from './LeadApproveReview';
import type { LeadTableTriageState } from './LeadTable.triage';
import { RowPreview } from './LeadRowPreview';
import { triageHotkeys, type TriageKeymapActions } from './TriageDeck.keys';
import type { CampaignBinding } from './LeadTable.logic';
import type { RejectReasonCode } from './LeadTable.types';
import { LeadRejectPanel } from './LeadTableDecisionPanels';
import type { useLeadApprovalActions } from './useLeadApprovalActions';
import type { useLeadTableKeyboardFlow } from './useLeadTableKeyboardFlow';
import {
  triageEntryOrder, triageReducer, triageView, useLeadTriage, type TriageAction, type TriageAvailability,
} from './useLeadTriage';
import './TriageDeck.css';

/**
 * The Triage deck (audit wow-power-1 / flow-03 / tables-03 / wow-power-4,
 * D-approval-flow-a2; deviation:triage-deck): one borrower at a time at
 * /lead-queue?mode=triage, the prototype's BorrowerDetail beside its
 * ApprovalBanner (design_files/Module 0 Prototype.html:1888-1990) as a
 * two-column card.
 *
 * Audit posture: the deck writes nothing until A. Showing a card, J / K,
 * Skip, Back and Esc read nothing (the card is the loaded row); A drafts once
 * through the table's own review (one DRAFT_OUTREACH row), Enter confirms
 * that exact draft as review_mode 'triage', R records one OUTREACH_REJECT
 * with a chosen reason. The deck advances only after a write returned ok.
 */
export interface TriageDeckProps {
  /** The table's deck state: the approved signal, the last card shown, the URL contract. */
  state: LeadTableTriageState;
  /** The table's rows in its current sort: the deck snapshots its order from them at mount. */
  sortedLeads: readonly LeadSummary[];
  /** The table's keyboard flow: its review, guarded open (always inline here) and Confirm focus rules. */
  flow: ReturnType<typeof useLeadTableKeyboardFlow>;
  /** The table's approval actions: locks, the reject panel and its write, the receipts. */
  approval: ReturnType<typeof useLeadApprovalActions>;
  leadsById: ReadonlyMap<string, LeadSummary>;
  approvals: Record<string, 'approved' | 'rejected'>;
  /** The review chunk, read by the (uncompiled) table, never by the deck. */
  ReviewInline: ComponentType<LeadApproveReviewProps> | null;
  actorEmail: string | null;
  campaignBinding: CampaignBinding | null;
  canAccessAdmin: boolean;
}

function inDeck(root: HTMLElement | null, node: EventTarget | Element | null): boolean {
  return root !== null && node instanceof Node && root.contains(node);
}

export function TriageDeck(props: TriageDeckProps) {
  const {
    state: table, sortedLeads, flow, approval, leadsById, approvals, ReviewInline, actorEmail, campaignBinding,
    canAccessAdmin,
  } = props;
  // Bumped by the table flow once an approve made through the review returned ok.
  const { approvedSignal, setLastShown } = table;
  /** Back to the table (replace, with the row to return to): the card's review or reject panel is closed first. */
  function onExit(lastId: string | null) {
    table.triage?.onModeChange(null, lastId ?? table.lastShown);
  }
  const reviewApi = flow.review;
  const review = reviewApi.review;
  const { pendingReject, setPendingReject, submitReject, decisionReceipts } = approval;
  // Locked outside the deck: a decision latched, on the wire or recorded.
  const isLocked = (id: string) => approval.isDecisionLocked(id) || approval.pendingDecisions.has(id)
    || approvals[id] !== undefined;
  // The order is snapshotted once, at mount: the selection the entry kept, or every eligible row.
  const [state, dispatch] = useLeadTriage(
    () => triageEntryOrder(sortedLeads, approval.approvalEligibleIds, approval.selectedIds),
  );
  // The rows at entry: a card decided here stays readable (K) after a filter
  // change took it out of the loaded rows. Only decided cards outlive them.
  const [entryLeads] = useState(() => new Map(leadsById));
  const [rejectingId, setRejectingId] = useState<string | null>(null);
  // The draft whose approve answered 409: "Review draft again", never
  // automatic. Keyed by the draft itself, so a fresh review (a new draft for
  // the same card, after J / K / A) is never offered a re-draft.
  const [staleDraft, setStaleDraft] = useState<OutreachDraftResult | null>(null);
  const [announcement, setAnnouncement] = useState('');
  const rootRef = useRef<HTMLElement | null>(null);
  const headingRef = useRef<HTMLHeadingElement | null>(null);
  const rejectReasonRef = useRef<HTMLSelectElement | null>(null);
  // Bumped by every move: the next card's heading (or the summary's) takes focus.
  const [focusRequest, setFocusRequest] = useState(1);

  const submittingId = review?.phase === 'submitting' ? review.borrowerId : rejectingId;
  // A card this deck is deciding stays on screen while its write is on the wire.
  const available: TriageAvailability = (id) => leadsById.has(id) && (id === submittingId || !isLocked(id));
  const view = triageView(state, available);
  const currentId = view.currentId;
  const lead = currentId ? leadsById.get(currentId) ?? entryLeads.get(currentId) : undefined;
  const decision = currentId ? state.decided[currentId] : undefined;
  const busy = submittingId !== null;

  // The table returns to the last card shown, also after a history move.
  useEffect(() => {
    if (currentId) setLastShown(currentId);
  }, [currentId, setLastShown]);

  // The heading of each new card (or the summary) takes focus after a move.
  useLayoutEffect(() => {
    headingRef.current?.focus({ preventScroll: true });
  }, [focusRequest]);

  /** A decision returned ok: record it, advance and say where the deck is now. */
  function decide(borrowerId: string, outcome: 'approved' | 'rejected') {
    const action: TriageAction = { type: 'decided', borrowerId, decision: outcome, available };
    const next = triageView(triageReducer(state, action), available);
    setFocusRequest((request) => request + 1);
    dispatch(action);
    setAnnouncement(`${outcome === 'approved' ? 'Approved' : 'Rejected'} ${borrowerId}. ${next.currentId === null
      ? 'Triage complete.'
      : `Borrower ${next.position} of ${next.total}.`}`);
  }

  // An approve made through the review returned ok: record it and advance.
  const lastSignalRef = useRef(approvedSignal?.seq ?? 0);
  useEffect(() => {
    if (!approvedSignal || approvedSignal.seq === lastSignalRef.current) return;
    lastSignalRef.current = approvedSignal.seq;
    const decidedId = approvedSignal.borrowerId;
    if (!state.order.includes(decidedId)) return;
    decide(decidedId, 'approved');
  });

  /** Close what is open on the card (never a write on the wire); false when one is. */
  function settleCard(): boolean {
    if (busy) return false;
    if (review !== null && !reviewApi.cancel()) return false;
    if (pendingReject !== null) setPendingReject(null);
    return true;
  }

  function move(type: 'skip' | 'back') {
    if (!settleCard()) return;
    setFocusRequest((request) => request + 1);
    dispatch({ type, available });
  }

  function startReview() {
    if (!currentId || decision || busy) return;
    if (pendingReject !== null) setPendingReject(null);
    flow.openTriageReview(currentId);
  }

  function startReject() {
    if (!currentId || decision || busy) return;
    if (review !== null && !reviewApi.cancel()) return;
    setPendingReject(currentId);
    requestAnimationFrame(() => rejectReasonRef.current?.focus());
  }

  function submit(reasonCode: RejectReasonCode, rationale: string) {
    if (!currentId || busy) return;
    const id = currentId;
    setRejectingId(id);
    void submitReject(reasonCode, rationale).then((rejected) => {
      setRejectingId(null);
      if (rejected === id) decide(id, 'rejected');
    }, () => setRejectingId(null));
  }

  function exit() {
    if (!settleCard()) return;
    onExit(currentId);
  }

  /** Esc: an open reject panel closes first (as its Cancel does), else back to the table. */
  function escape() {
    if (pendingReject === null) {
      exit();
      return;
    }
    if (busy) return;
    setPendingReject(null);
    headingRef.current?.focus({ preventScroll: true });
  }

  // Keys (scope 'triage'), live only with focus inside the deck; Escape is
  // the escape stack's, listed on the sheet by a display-only binding.
  const actions: TriageKeymapActions & { escape: () => void } = {
    review: startReview,
    reject: startReject,
    skip: () => move('skip'),
    back: () => move('back'),
    openOffer: () => {
      if (currentId) rootRef.current?.querySelector<HTMLAnchorElement>(`[data-testid="lead-build-offer-${currentId}"]`)?.click();
    },
    escape,
  };
  const actionsRef = useRef(actions);
  useLayoutEffect(() => {
    actionsRef.current = actions;
  });
  useEffect(() => {
    const when = (event: KeyboardEvent) => inDeck(rootRef.current, event.target)
      && inDeck(rootRef.current, document.activeElement)
      && !isEditableElement(event.target instanceof Element ? event.target : null)
      && !hasOpenOverlay();
    const offs = triageHotkeys({
      review: () => actionsRef.current.review(),
      reject: () => actionsRef.current.reject(),
      skip: () => actionsRef.current.skip(),
      back: () => actionsRef.current.back(),
      openOffer: () => actionsRef.current.openOffer(),
    }).map((hotkey) => registerKeyBinding({
      id: hotkey.id,
      scope: 'triage',
      keys: hotkey.keys,
      description: hotkey.description,
      when,
      run: (event) => hotkey.run(event, rootRef.current),
    }));
    offs.push(registerKeyBinding({
      id: 'triage-exit',
      scope: 'triage',
      keys: ['Escape'],
      description: 'Back to the table',
      when: () => false,
      run: () => false,
    }));
    // Topmost only, and only for the deck: Escape in another surface (the
    // Console, the Genie panel) is declined and left to that surface.
    const popEscape = pushEscapeLayer(() => {
      const focused = document.activeElement;
      if (focused !== null && focused !== document.body && !inDeck(rootRef.current, focused)) return false;
      actionsRef.current.escape();
      return true;
    });
    return () => {
      offs.forEach((off) => off());
      popEscape();
    };
  }, []);

  const decidedIds = Object.keys(state.decided);
  const last = decidedIds.length > 0 ? decidedIds[decidedIds.length - 1] : undefined;
  const lastReceipt = last ? decisionReceipts[last] : undefined;
  return (
    <section ref={rootRef} className="triage" aria-label="Triage deck" data-triage-deck="" data-testid="triage-deck">
      <div className="triage__hdr">
        <span className="mono fs-12" data-testid="triage-position">
          {currentId ? `Borrower ${view.position} of ${view.total}` : `${view.total} reviewed`}
        </span>
        <Button variant="ghost" size="sm" onClick={exit} aria-disabled={busy || undefined} data-testid="triage-exit">
          Back to table
        </Button>
      </div>
      <div className="triage__progress" data-testid="triage-progress">
        <span>{`Approved ${view.approved} · Rejected ${view.rejected} · Skipped ${view.skipped}`}</span>
        {last && (
          <span data-testid="triage-last-receipt">
            {`${state.decided[last] === 'approved' ? 'Approved' : 'Rejected'} ${last}`}
            {lastReceipt?.auditEventId && (
              <>
                {' · audit '}
                {canAccessAdmin
                  ? <Link className="mono" to={auditEventHref(lastReceipt.auditEventId)}>{lastReceipt.auditEventId}</Link>
                  : <span className="mono">{lastReceipt.auditEventId}</span>}
              </>
            )}
          </span>
        )}
      </div>
      <span className="sr-only" role="status" aria-live="polite" data-testid="triage-status">{announcement}</span>
      {currentId && lead ? (
        <div className="triage__card" key={currentId} data-testid={`triage-card-${currentId}`}>
          <SurfaceTitle level={3} ref={headingRef} tabIndex={-1} className="triage__name">
            {lead.borrower_id}
            <span className="muted fs-12">{` · ${lead.city}, ${lead.state}`}</span>
          </SurfaceTitle>
          <div className="triage__context">
            <RowPreview
              lead={lead}
              approval={decision ?? approvals[lead.borrower_id] ?? lead.approval_status}
              decisionReceipt={decision ? decisionReceipts[lead.borrower_id] ?? null : null}
              offerSearch={campaignBinding ? `?${new URLSearchParams({ ...campaignBinding })}` : undefined}
            />
          </div>
          <div className="triage__decision">
            {decision ? (
              <p className="triage__outcome" data-testid="triage-outcome">
                {decision === 'approved' ? 'Approved in this deck.' : 'Rejected in this deck.'} The receipt is beside it.
              </p>
            ) : review !== null && review.borrowerId === currentId && ReviewInline ? (
              <ReviewInline
                review={review}
                actorEmail={actorEmail}
                onConfirm={() => {
                  const shown = review.draft;
                  void reviewApi.confirm({ reviewMode: 'triage', onConflict: () => setStaleDraft(shown) });
                }}
                onCancel={() => {
                  if (!reviewApi.cancel()) return;
                  headingRef.current?.focus({ preventScroll: true });
                }}
                onRetryDraft={reviewApi.retryDraft}
                stale={review.draft !== null && review.draft === staleDraft}
                onRedraft={() => {
                  // A new explicit intent: the same guarded open as A (eligible,
                  // nothing in flight, no bulk run), drafting once.
                  if (!reviewApi.cancel()) return;
                  setStaleDraft(null);
                  flow.openTriageReview(review.borrowerId);
                }}
                confirmRef={flow.confirmRef}
                shouldTakeFocus={flow.shouldConfirmTakeFocus}
                claimDraftLanding={reviewApi.claimDraftLanding}
                copyRegionLabel={`Draft outreach for ${lead.borrower_id}`}
              />
            ) : flow.reviewLoading === currentId ? (
              <p role="status" className="table-neutral">Opening the review for {currentId}…</p>
            ) : pendingReject === currentId ? (
              <LeadRejectPanel
                key={currentId}
                borrowerId={currentId}
                reasonRef={rejectReasonRef}
                onCancel={() => {
                  setPendingReject(null);
                  headingRef.current?.focus({ preventScroll: true });
                }}
                onSubmit={submit}
              />
            ) : (
              <div className="approval" data-testid="triage-gate">
                <div className="approval__ico" aria-hidden="true">
                  <Icon name="shield" size={16} />
                </div>
                <div className="approval__body">
                  <div className="approval__title">Human approval required before outreach</div>
                  <div className="approval__sub">Nothing is drafted or sent until you review the draft.</div>
                  {flow.reviewLoadFailed && (
                    <div role="alert" className="text-danger fs-12">
                      The approval review could not load, so nothing was drafted. Reload the page, then try again.
                    </div>
                  )}
                </div>
                <div className="approval__actions">
                  <Button variant="primary" size="sm" icon="check" onClick={startReview} data-testid="triage-review">
                    Review draft (A)
                  </Button>
                  <Button variant="ghost" size="sm" onClick={startReject} data-testid="triage-reject">Reject (R)</Button>
                  <Button variant="ghost" size="sm" onClick={() => move('skip')} data-testid="triage-skip">Skip (J)</Button>
                </div>
              </div>
            )}
            <div className="triage__nav">
              <Button
                variant="ghost"
                size="sm"
                onClick={() => move('back')}
                aria-disabled={view.previousId === null || busy || undefined}
                data-testid="triage-back"
              >
                Previous (K)
              </Button>
              {decision && (
                <Button variant="ghost" size="sm" onClick={() => move('skip')} data-testid="triage-next">Next (J)</Button>
              )}
            </div>
          </div>
        </div>
      ) : (
        <div className="triage__summary" data-testid="triage-summary">
          <SurfaceTitle level={3} ref={headingRef} tabIndex={-1}>Triage complete</SurfaceTitle>
          <p className="body flush">{`Approved ${view.approved} · Rejected ${view.rejected} · Skipped ${view.skipped}`}</p>
          <div className="chip-row">
            <Button
              size="sm"
              onClick={() => {
                if (view.skipped === 0) return;
                setFocusRequest((request) => request + 1);
                dispatch({ type: 'restart-skipped', available });
              }}
              aria-disabled={view.skipped === 0 || undefined}
              data-testid="triage-review-skipped"
            >
              {`Review skipped (${view.skipped})`}
            </Button>
            <Button variant="primary" size="sm" onClick={exit} data-testid="triage-summary-exit">Back to table</Button>
          </div>
        </div>
      )}
    </section>
  );
}
