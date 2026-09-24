import { useEffect, useRef, type ReactNode, type RefObject } from 'react';
import { ApprovalBanner } from '../components/mortgage/ApprovalBanner';
import type { SalesTeamMember } from '../types';
import { useGenieClearance } from './offer-orchestrator.genie-clearance';
import './offer-orchestrator.action-bar.css';

/** Set on `.main`; offer-orchestrator.action-bar.css reads it for the scroll-margin-block-end of everything outside the bar. */
export const ACTION_BAR_BLOCK_SIZE_PROPERTY = '--offer-action-bar-block-size';

const FOLLOW_UP_OPTIONS: ReadonlyArray<{ days: number; label: string }> = [
  { days: 0, label: 'None' },
  { days: 3, label: 'In 3 days' },
  { days: 5, label: 'In 5 days' },
  { days: 7, label: 'In 7 days' },
  { days: 14, label: 'In 14 days' },
];

/**
 * Keep keyboard focus clear of the docked bar (WCAG 2.2 SC 2.4.11 Focus Not
 * Obscured), as ask-genie.composer-clearance.ts does for the Ask Genie
 * composer. `.main`, the app's scroller, carries the bar's measured block
 * size; everything in it outside the bar takes that size as scroll-margin at
 * its end (offer-orchestrator.action-bar.css), so a control that takes focus
 * from below the view stops above the bar instead of behind it, while focus
 * inside the bar scrolls nothing. Re-measured when the bar resizes (the
 * reject rationale or a write error opens inside it); removed on unmount.
 */
function useActionBarScrollClearance(barRef: RefObject<HTMLElement | null>): void {
  useEffect(() => {
    const bar = barRef.current;
    const scroller = bar?.closest<HTMLElement>('.main');
    if (!bar || !scroller) return undefined;
    const write = () => {
      const size = bar.offsetHeight;
      if (size > 0) scroller.style.setProperty(ACTION_BAR_BLOCK_SIZE_PROPERTY, `${size}px`);
    };
    write();
    const observer = typeof ResizeObserver === 'function' ? new ResizeObserver(write) : null;
    observer?.observe(bar);
    return () => {
      observer?.disconnect();
      scroller.style.removeProperty(ACTION_BAR_BLOCK_SIZE_PROPERTY);
    };
  }, [barRef]);
}

/**
 * Give focus back to the gate's Reject button when the reject rationale
 * closes without a decision (Cancel). The form took focus when it opened, so
 * its unmount would otherwise drop focus to <body> (WCAG 2.4.3). A decision
 * unmounts the whole bar instead and the decision receipt takes over; focus
 * the reviewer already moved elsewhere is left where it is.
 */
function useRejectReviewFocusReturn(barRef: RefObject<HTMLElement | null>, reviewOpen: boolean): void {
  const wasOpen = useRef(reviewOpen);
  useEffect(() => {
    const closed = wasOpen.current && !reviewOpen;
    wasOpen.current = reviewOpen;
    if (!closed) return;
    const active = document.activeElement;
    if (active && active !== document.body && active.isConnected) return;
    // ApprovalBanner renders Reject first in `.approval__actions`, then Approve.
    barRef.current?.querySelector<HTMLButtonElement>('.approval__actions button')?.focus();
  }, [barRef, reviewOpen]);
}

export interface OfferActionBarProps {
  borrowerId: string | null;
  salesTeam: readonly SalesTeamMember[];
  assignedTo: string;
  onAssignedToChange: (email: string) => void;
  followUpDays: number;
  onFollowUpDaysChange: (days: number) => void;
  approving: boolean;
  onApprove: () => void;
  onReject: () => void;
  approveDisabled: boolean;
  isSubmitting: boolean;
  approverGate: string | null;
  actorEmail: string | null;
  /** The failed approve / reject write, shown beside the buttons that made it. */
  approveError: string | null;
  /** The reject rationale form while it is open (Reject's first click opens it). */
  rejectReview?: ReactNode;
  /** The floating Genie panel is open: the bar keeps its buttons clear of it. */
  genieOpen?: boolean;
}

/**
 * The Offer Orchestrator's decision bar (2026-09-21 audit visual-v1): the
 * loan-officer routing and the human-approval gate, docked at the bottom of
 * `.main` with `position: sticky` so they are on screen at 1440x900 without
 * scrolling and stay there while the reviewer reads the offer and the copy.
 * It replaces the hero Approve shortcut, which let a reviewer approve before
 * the routing controls were ever on screen.
 *
 * The bar is in flow (sticky, not fixed), so at the end of the page it sits
 * above the footer and nothing is permanently hidden behind it. It docks only
 * in a viewport at least 40rem tall: under browser zoom (1440x900 at 200% is
 * 720x450) a docked bar and the sticky route nav would cover the whole
 * scroller, so there it stays in flow at the end of the page. The reject
 * rationale and a failed write render inside the bar, next to the Reject /
 * Approve buttons that open them, instead of at the top of the page where a
 * reviewer scrolled down to the bar would never see them.
 *
 * The `.approval` block and its audit write are unchanged: this only places
 * the ApprovalBanner. Prototype deviation, declared: the prototype renders
 * `.approval` in flow (design_files/Module 0 Prototype.html:1495-1506, used at
 * :2181); report section 10 item 10 lists the sticky Approval.
 */
export function OfferActionBar({
  borrowerId,
  salesTeam,
  assignedTo,
  onAssignedToChange,
  followUpDays,
  onFollowUpDaysChange,
  approving,
  onApprove,
  onReject,
  approveDisabled,
  isSubmitting,
  approverGate,
  actorEmail,
  approveError,
  rejectReview,
  genieOpen = false,
}: OfferActionBarProps) {
  const barRef = useRef<HTMLElement>(null);
  useActionBarScrollClearance(barRef);
  useRejectReviewFocusReturn(barRef, Boolean(rejectReview));
  useGenieClearance(barRef, genieOpen);
  return (
    <section
      ref={barRef}
      className="offer-action-bar"
      aria-label="Routing and approval"
      data-testid="offer-action-bar"
    >
      {approveError && (
        <div className="surface surface--danger" role="alert">
          <div className="surface__body text-danger">{approveError}</div>
        </div>
      )}
      {rejectReview}
      <div className="offer-action-bar__row">
        <div className="outreach-routing" data-testid="outreach-routing">
          <div className="outreach-routing__field">
            <label htmlFor="lo-assign" className="outreach-routing__label">Assign to loan officer</label>
            <select
              id="lo-assign"
              className="outreach-routing__select"
              value={assignedTo}
              onChange={(e) => onAssignedToChange(e.target.value)}
              disabled={approving}
            >
              <option value="">Unassigned</option>
              {salesTeam.map((m) => (
                <option key={m.email} value={m.email}>
                  {m.display_label}
                  {m.region ? ` · ${m.region}` : ''}
                </option>
              ))}
            </select>
          </div>
          <div className="outreach-routing__field">
            <label htmlFor="lo-followup" className="outreach-routing__label">Follow-up reminder</label>
            <select
              id="lo-followup"
              className="outreach-routing__select"
              value={followUpDays}
              onChange={(e) => onFollowUpDaysChange(Number(e.target.value))}
              disabled={approving}
            >
              {FOLLOW_UP_OPTIONS.map((option) => (
                <option key={option.days} value={option.days}>{option.label}</option>
              ))}
            </select>
          </div>
        </div>
        <ApprovalBanner
          text={`${borrowerId ? `Borrower ${borrowerId}` : 'Borrower'} pending review. Approve writes an audit event and places the decision in the governed internal queue.`}
          onApprove={onApprove}
          onReject={onReject}
          approveDisabled={approveDisabled}
          isSubmitting={isSubmitting}
          approverGate={approverGate}
          actorEmail={actorEmail}
        />
      </div>
    </section>
  );
}
