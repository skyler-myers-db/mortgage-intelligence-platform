/**
 * The partial result of a bulk approve cut short by unmount (R5-21).
 *
 * Leaving the Lead Queue mid-run aborts the rest of the loop; the next mount
 * flashes "N landed, rest aborted" so the approver knows to check Recent
 * activity instead of blindly retrying (an aborted POST may have committed).
 * Kept out of the hook so no wall-clock read or storage access happens
 * during a render the React Compiler memoizes: the stash is written from the
 * bulk loop (a click, never render) and read once by a state initializer.
 */

const STASH_KEY = 'mip.bulkApprove.lastCancelled';
/** Older snapshots are probably from a much earlier session and are dropped. */
const STALE_AFTER_MS = 10 * 60 * 1000;

export interface CancelledBulkApprove {
  ok: number;
  aborted: number;
}

/** Record a run the unmount aborted. Private mode or a full quota is ignored. */
export function stashCancelledBulk(ok: number, aborted: number): void {
  try {
    sessionStorage.setItem(STASH_KEY, JSON.stringify({ ok, aborted, ts: Date.now() }));
  } catch {
    // private mode or quota: the flash is a courtesy, never a requirement
  }
}

/** The stashed run, if it is recent and says anything; never throws. */
export function readCancelledBulk(): CancelledBulkApprove | null {
  try {
    const raw = sessionStorage.getItem(STASH_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as { ok?: number; aborted?: number; ts?: number } | null;
    if (!parsed) return null;
    if (parsed.ts && Date.now() - parsed.ts > STALE_AFTER_MS) return null;
    const ok = parsed.ok ?? 0;
    const aborted = parsed.aborted ?? 0;
    return ok + aborted === 0 ? null : { ok, aborted };
  } catch {
    return null;
  }
}

/** Drop the stash once a mount has read it (stale or not). */
export function clearCancelledBulk(): void {
  try {
    sessionStorage.removeItem(STASH_KEY);
  } catch {
    // storage unavailable: nothing to clear
  }
}
