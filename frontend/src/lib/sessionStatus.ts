/**
 * Session status — the one place the app records that its Databricks Apps
 * session has ended (audit 2026-09-21 `states-02`, `critic-v2`, `shell-v1`).
 *
 * The fetch core (`apiTransport.ts`) is the only writer: it calls
 * `markSessionExpired` when a request comes back 401, or when an opaque
 * redirect / an unreadable 2xx body is confirmed by a `/api/v1/health` probe.
 * `SessionExpiredDialog` is the only reader that renders anything.
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

const READ_METHODS = new Set(['GET', 'HEAD', 'OPTIONS']);

/**
 * Classify a write by its endpoint. The outreach draft is the first half of
 * an approval (LeadTable drafts, then approves that exact copy), so a draft
 * that failed on expiry is reported as an unrecorded approval.
 */
export function unrecordedWriteFor(method: string, path: string): UnrecordedWrite | null {
  if (READ_METHODS.has(method.toUpperCase())) return null;
  if (/\/outreach\/(approve|draft)(?:[/?]|$)/.test(path)) return 'approval';
  if (/\/outreach\/reject(?:[/?]|$)/.test(path)) return 'rejection';
  return 'change';
}

const PRECEDENCE: Record<UnrecordedWrite, number> = { change: 1, rejection: 2, approval: 3 };

function notify(): void {
  for (const listener of [...listeners]) listener();
}

/**
 * Record that the session ended while `method path` was in flight. Idempotent;
 * a later failed write upgrades the "not recorded" line (an approval outranks
 * a generic change) but never downgrades it.
 */
export function markSessionExpired(request: { method: string; path: string }): void {
  const write = unrecordedWriteFor(request.method, request.path);
  const unrecorded = write && (!snapshot.unrecorded || PRECEDENCE[write] > PRECEDENCE[snapshot.unrecorded])
    ? write
    : snapshot.unrecorded;
  if (snapshot.expired && unrecorded === snapshot.unrecorded) return;
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
