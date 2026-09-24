import { parseBackendTimestamp } from '../lib/time';
import { toast } from '../lib/toast';

/**
 * Offer Orchestrator's shell feedback (2026-09-21 audit states-05 and
 * states-07 slice 1), kept out of the route file.
 */

/**
 * The "Leave without saving?" message for this page's unsaved work, or null.
 * The outreach draft is review-only since critic-02, so an edited draft is
 * guarded for when editing returns; the work a reviewer can actually type
 * here today is a rejection note that has not been recorded yet.
 */
export function offerUnsavedMessage(draftEdited: boolean, rejectionNoteTyped: boolean): string | null {
  if (draftEdited) return 'Your edits to the outreach draft have not been approved. Leaving discards them.';
  if (rejectionNoteTyped) return 'Your rejection note has not been recorded. Leaving discards it.';
  return null;
}

const FOLLOW_UP_DATE = new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric' });

/**
 * Say where an approval was routed (loan officer, follow-up reminder) as a
 * toast linked to the approval's audit row. It replaces the in-page routing
 * chip that sat below the fold; the Decision receipt stays the durable
 * record of the decision. Nothing is raised when the approval was not routed.
 */
export function announceApprovalRouting(
  assignedEmail: string | null,
  followUpAt: string | null,
  auditEventId: string | null,
): void {
  if (!assignedEmail && !followUpAt) return;
  const followUp = parseBackendTimestamp(followUpAt);
  const parts = [assignedEmail ? `Assigned to ${assignedEmail}` : 'Unassigned'];
  if (followUp) parts.push(`follow-up ${FOLLOW_UP_DATE.format(followUp)}`);
  toast.success('Approval routed', { detail: parts.join(' · '), auditEventId });
}
