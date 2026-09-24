/**
 * Session status — the one place the app records that its Databricks Apps
 * session has ended (audit 2026-09-21 `states-02`, `critic-v2`, `shell-v1`).
 *
 * The fetch core (`apiTransport.ts`) records the expiry: it calls
 * `markSessionExpired` when a request comes back 401, or when an opaque
 * redirect / an unreadable 2xx body is confirmed by a `/api/v1/health` probe.
 * A user action whose request is not itself the write (the Lead Queue's
 * Approve drafts first, then approves) says what was lost with
 * `markUnrecordedWrite`. `SessionExpiredDialog` is the only reader that
 * renders anything.
 *
 * Once expired, the state never clears in this document: the only recovery is
 * a reload, which re-runs the proxy's sign-in and starts a fresh module graph.
 *
 * Deliberately not React state: the writer is a plain async function that runs
 * outside any component (approve / reject are called directly, not through a
 * query), so the store is a module singleton read with useSyncExternalStore.
 */

/** What the dialog must say was not recorded, when a write failed on expiry. */
export type UnrecordedWrite = 'approval' | 'rejection' | 'change';

export interface SessionStatusSnapshot {
  expired: boolean;
  /** Set when a write (POST/PUT/PATCH/DELETE) failed because the session ended. */
  unrecorded: UnrecordedWrite | null;
}

const ACTIVE: SessionStatusSnapshot = { expired: false, unrecorded: null };

let snapshot: SessionStatusSnapshot = ACTIVE;
const listeners = new Set<() => void>();

const UPDATE_METHODS = new Set(['PUT', 'PATCH', 'DELETE']);

/**
 * Classify a failed request by what it recorded on its own. Only the decision
 * endpoints name themselves: POST /outreach/approve and /outreach/reject.
 *
 * POST /outreach/draft is deliberately NOT an approval here. The Offer page
 * loads its draft automatically on open (and again on a channel switch), so a
 * draft that met the ended session usually means the user only looked at a
 * page; the dialog must not tell them an approval was lost. The Lead Queue's
 * Approve click, which drafts first and then approves, reports its own lost
 * approval through `markUnrecordedWrite` instead (useLeadApprovalActions).
 *
 * Other POSTs are NOT assumed to be writes: several reads go out as POST
 * (portfolio preview, offer recommendation, Genie questions), and telling a
 * user a change was lost when they only viewed a page would be false.
 * PUT / PATCH / DELETE always record something.
 */
export function unrecordedWriteFor(method: string, path: string): UnrecordedWrite | null {
  const verb = method.toUpperCase();
  if (verb === 'POST' && /\/outreach\/approve(?:[/?]|$)/.test(path)) return 'approval';
  if (verb === 'POST' && /\/outreach\/reject(?:[/?]|$)/.test(path)) return 'rejection';
  return UPDATE_METHODS.has(verb) ? 'change' : null;
}

const PRECEDENCE: Record<UnrecordedWrite, number> = { change: 1, rejection: 2, approval: 3 };

/** An approval outranks a rejection outranks a generic change; never downgrade. */
function upgraded(current: UnrecordedWrite | null, write: UnrecordedWrite | null): UnrecordedWrite | null {
  if (!write) return current;
  return !current || PRECEDENCE[write] > PRECEDENCE[current] ? write : current;
}

function notify(): void {
  for (const listener of [...listeners]) listener();
}

/**
 * Record that the session ended while `method path` was in flight. Idempotent;
 * a later failed write upgrades the "not recorded" line (an approval outranks
 * a generic change) but never downgrades it.
 */
export function markSessionExpired(request: { method: string; path: string }): void {
  const unrecorded = upgraded(snapshot.unrecorded, unrecordedWriteFor(request.method, request.path));
  if (snapshot.expired && unrecorded === snapshot.unrecorded) return;
  snapshot = { expired: true, unrecorded };
  notify();
}

/**
 * Say that a user action was not recorded because the session ended, when
 * the request that met the expiry is not itself the write (the Lead Queue's
 * Approve click fails on its draft step). Same precedence as
 * `markSessionExpired`, and it never ends the session itself: before expiry it
 * is a no-op, so a stale mark can never surface in a later, unrelated dialog.
 */
export function markUnrecordedWrite(write: UnrecordedWrite): void {
  if (!snapshot.expired) return;
  const unrecorded = upgraded(snapshot.unrecorded, write);
  if (unrecorded === snapshot.unrecorded) return;
  snapshot = { expired: true, unrecorded };
  notify();
}

export function isSessionExpired(): boolean {
  return snapshot.expired;
}

export function getSessionStatus(): SessionStatusSnapshot {
  return snapshot;
}

export function subscribeSessionStatus(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** Test seam: vitest shares this module across the tests of one file. */
export function _resetSessionStatusForTests(): void {
  snapshot = ACTIVE;
  notify();
}
