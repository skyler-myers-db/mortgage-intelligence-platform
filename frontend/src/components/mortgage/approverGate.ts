/**
 * approverGate — one client-side reading of the server's `can_approve`
 * session flag for every approve / reject control (audit flow-02 / shell-06).
 *
 * The gate is ADVISORY UI: `require_approver` on the outreach endpoints stays
 * the real enforcement. Its job is to stop a non-approver's click (or A / R
 * keypress) BEFORE the draft call, which writes a DRAFT_OUTREACH audit row
 * and is not approver-gated, and to say why the control is disabled. Gated
 * controls stay visible: the human-approval gate is part of the product.
 */

export type SessionStatus = 'loading' | 'ready' | 'error';

/** The accessible reason a non-approver's approve controls are disabled. */
export const APPROVER_ROLE_REQUIRED = 'Requires approver role';

/** id of the visible status line the disabled controls are described by. */
export const APPROVER_ROLE_STATUS_ID = 'approver-role-status';

/**
 * Null when the actor may approve; otherwise the reason to show. Fails
 * closed: anything other than an explicit `true` is gated, and an unresolved
 * or failed session check says so instead of claiming a missing role.
 */
export function approverGateReason(
  canApprove: boolean | undefined,
  sessionStatus?: SessionStatus,
): string | null {
  if (canApprove === true) return null;
  if (sessionStatus === 'loading') return 'Checking approver role…';
  if (sessionStatus === 'error') return 'Approver role could not be verified';
  return APPROVER_ROLE_REQUIRED;
}

/** Space-joined `aria-describedby` from the ids that currently apply. */
export function describedBy(...ids: Array<string | false | null | undefined>): string | undefined {
  const present = ids.filter((id): id is string => Boolean(id));
  return present.length > 0 ? present.join(' ') : undefined;
}
