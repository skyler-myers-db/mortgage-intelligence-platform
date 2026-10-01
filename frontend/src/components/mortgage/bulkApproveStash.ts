import { actorScopeStatus, readActorScoped, removeActorScoped, writeActorScoped } from '../../lib/actorScope';

/**
 * The partial result of a bulk run cut short by unmount (R5-21).
 *
 * Leaving the Lead Queue mid-run aborts the rest of the loop; the next mount
 * flashes "N landed, rest aborted" so the approver knows to check Recent
 * activity instead of blindly retrying (an aborted POST may have committed).
 * Kept out of the hook so no wall-clock read or storage access happens
 * during a render the React Compiler memoizes: the stash is written from the
 * bulk loop (a click, never render) and read once by a state initializer.
 *
 * The run's kind (approve or reject, tables-07) rides the same JSON value
 * under the same key, so the flash says "approved" or "rejected"; a value
 * written before bulk Reject existed reads as an approve run.
 *
 * The stash names one approver's decisions, so it is a PRIVATE_SESSION key
 * of lib/actorScope (D-identity-review-b; the W5b critique correction): it
 * goes through the actor gate's guarded accessors, never storage directly.
 * While the gate is pending a write is queued and a read returns null; while
 * closed both are dropped. A clear runs only while the gate is OPEN: a mount
 * before the gate opened read nothing, so the stash (if any) is kept for the
 * next mount (it still expires after STALE_AFTER_MS) instead of a queued
 * removal deleting a flash nobody saw. Storage that throws is the gate's
 * concern (it holds values in memory for the document).
 */

const STASH_KEY = 'mip.bulkApprove.lastCancelled';
/** Older snapshots are probably from a much earlier session and are dropped. */
const STALE_AFTER_MS = 10 * 60 * 1000;

export type CancelledBulkKind = 'approve' | 'reject';

export interface CancelledBulkApprove {
  ok: number;
  aborted: number;
  kind: CancelledBulkKind;
}

/** Record a run the unmount aborted (queued while the gate is pending, dropped while closed). */
export function stashCancelledBulk(ok: number, aborted: number, kind: CancelledBulkKind = 'approve'): void {
  writeActorScoped('session', STASH_KEY, JSON.stringify({ ok, aborted, kind, ts: Date.now() }));
}

/** The stashed run, if it is recent and says anything; never throws. */
export function readCancelledBulk(): CancelledBulkApprove | null {
  try {
    const raw = readActorScoped('session', STASH_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as { ok?: number; aborted?: number; kind?: unknown; ts?: number } | null;
    if (!parsed) return null;
    if (parsed.ts && Date.now() - parsed.ts > STALE_AFTER_MS) return null;
    const ok = parsed.ok ?? 0;
    const aborted = parsed.aborted ?? 0;
    const kind: CancelledBulkKind = parsed.kind === 'reject' ? 'reject' : 'approve';
    return ok + aborted === 0 ? null : { ok, aborted, kind };
  } catch {
    return null;
  }
}

/** Drop the stash once a mount has read it (stale or not); only while the gate is open. */
export function clearCancelledBulk(): void {
  if (actorScopeStatus() !== 'open') return;
  removeActorScoped('session', STASH_KEY);
}
