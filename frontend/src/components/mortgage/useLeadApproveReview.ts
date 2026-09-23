/**
 * useLeadApproveReview — the approve-review sheet's state (audit flow-03,
 * states-06).
 *
 * A row approve used to draft and approve in one click: the audit row
 * certified subject, body and channel the approver never saw. Now the first
 * Approve click (or A) OPENS a review, which drafts on that explicit intent
 * only and shows the copy; Confirm approves that exact generation (its id
 * and hash, via `approveLead(..., reviewedDraft)`); Cancel abandons it.
 *
 * Audit posture:
 *   - Nothing drafts on row expand, hover, cursor movement or prefetch:
 *     `open` is the only caller of `draftForApproval` here, and a second
 *     `open` for the review already showing never drafts again.
 *   - The approver-role / campaign-binding gate runs BEFORE the draft call.
 *   - Cancel after the draft leaves its DRAFT_OUTREACH row, which stays
 *     truthful: a draft was generated and shown, and nothing was approved.
 *   - Approve stays pessimistic: `onApproved` runs only after the approve
 *     write returned ok; until then the review reads "Approving…".
 */
import { useEffect, useRef, useState } from 'react';
import { isAbortError } from '../../lib/api';
import type { OutreachDraftResult } from '../../lib/apiTypes';
import type { useLeadApprovalActions } from './useLeadApprovalActions';

export type LeadApproveReviewMode = 'inline' | 'dialog';
export type LeadApproveReviewPhase = 'drafting' | 'ready' | 'submitting' | 'error';

export interface LeadApproveReviewState {
  borrowerId: string;
  /** Inline in the expanded row, or a native dialog when the row is collapsed. */
  mode: LeadApproveReviewMode;
  phase: LeadApproveReviewPhase;
  draft: OutreachDraftResult | null;
  /** Why drafting or approving failed; the review stays open for a retry. */
  error: string | null;
}

type ApprovalActions = ReturnType<typeof useLeadApprovalActions>;

export interface UseLeadApproveReviewInput {
  canStartApproval: ApprovalActions['canStartApproval'];
  draftForApproval: ApprovalActions['draftForApproval'];
  approveLead: ApprovalActions['approveLead'];
  /** The approve write returned ok for this borrower. */
  onApproved: (borrowerId: string) => void;
}

export type OpenReviewResult = 'opened' | 'already-open' | 'busy' | 'blocked';

export function useLeadApproveReview({
  canStartApproval,
  draftForApproval,
  approveLead,
  onApproved,
}: UseLeadApproveReviewInput) {
  'use no memo';

  const [review, setReviewState] = useState<LeadApproveReviewState | null>(null);
  // Synchronous mirror: two A presses in one frame must not both draft.
  const reviewRef = useRef<LeadApproveReviewState | null>(null);
  const draftAbortRef = useRef<AbortController | null>(null);

  const setReview = (next: LeadApproveReviewState | null) => {
    reviewRef.current = next;
    setReviewState(next);
  };

  function startDraft(borrowerId: string, mode: LeadApproveReviewMode) {
    draftAbortRef.current?.abort();
    const ctrl = new AbortController();
    draftAbortRef.current = ctrl;
    setReview({ borrowerId, mode, phase: 'drafting', draft: null, error: null });
    draftForApproval(borrowerId, ctrl.signal)
      .then((draft) => {
        const current = reviewRef.current;
        if (ctrl.signal.aborted || current?.borrowerId !== borrowerId) return;
        setReview({ ...current, phase: 'ready', draft, error: null });
      })
      .catch((err: unknown) => {
        const current = reviewRef.current;
        if (ctrl.signal.aborted || isAbortError(err) || current?.borrowerId !== borrowerId) return;
        setReview({
          ...current,
          phase: 'error',
          draft: null,
          error: err instanceof Error ? err.message : 'The governed draft could not be generated.',
        });
      });
  }

  /** Open the review for one row. Drafts on this explicit intent only. */
  function open(borrowerId: string, mode: LeadApproveReviewMode): OpenReviewResult {
    const current = reviewRef.current;
    if (current?.borrowerId === borrowerId) return 'already-open';
    // Never abandon a review whose approval is on the wire.
    if (current?.phase === 'submitting') return 'busy';
    if (!canStartApproval()) return 'blocked';
    startDraft(borrowerId, mode);
    return 'opened';
  }

  /** "Generate draft again" after a failed draft: a new explicit intent. */
  function retryDraft() {
    const current = reviewRef.current;
    if (!current || current.phase !== 'error') return;
    if (!canStartApproval()) return;
    startDraft(current.borrowerId, current.mode);
  }

  /** Approve the exact draft on screen. No-op unless the draft is ready. */
  async function confirm(): Promise<void> {
    const current = reviewRef.current;
    if (!current || current.phase !== 'ready' || !current.draft) return;
    const submitting: LeadApproveReviewState = { ...current, phase: 'submitting', error: null };
    setReview(submitting);
    const outcome = await approveLead(current.borrowerId, undefined, {}, current.draft);
    if (reviewRef.current !== submitting) return;
    if (outcome === 'ok') {
      setReview(null);
      onApproved(current.borrowerId);
      return;
    }
    setReview({
      ...submitting,
      phase: 'ready',
      error: 'Not approved: nothing was recorded for this borrower. Confirm again or cancel.',
    });
  }

  /** Abandon the review. Refused while the approval is on the wire. */
  function cancel(): boolean {
    const current = reviewRef.current;
    if (!current) return true;
    if (current.phase === 'submitting') return false;
    draftAbortRef.current?.abort();
    draftAbortRef.current = null;
    setReview(null);
    return true;
  }

  /** A dialog review moves into its row (e.g. to open an evidence source). */
  function moveInline() {
    const current = reviewRef.current;
    if (current && current.mode === 'dialog') setReview({ ...current, mode: 'inline' });
  }

  useEffect(() => () => draftAbortRef.current?.abort(), []);

  return { review, open, retryDraft, confirm, cancel, moveInline };
}
