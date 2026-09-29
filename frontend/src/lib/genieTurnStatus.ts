import { useSyncExternalStore } from 'react';

/**
 * Launcher-facing status of the floating Genie panel's turn (audit 2026-09-21
 * `runtime-01` / `genie-02`).
 *
 * The panel now stays mounted while closed, so a 30-200 second turn keeps
 * running after the user dismisses it. The launchers (the topbar Genie toggle
 * and the mobile `.genie__fab`) live in other subtrees and need exactly one
 * fact: is a turn running behind the closed panel, or did an answer land that
 * the user has not opened yet?
 *
 * This is a status SIGNAL, not the in-flight turn itself: the turn (its
 * promise, progress, persisted record and abort controller) lives in the lazy
 * in-flight turn store (lib/genieInFlightTurn.ts, wave 2 `runtime-01`), which
 * this initial-chunk module deliberately does not import. Two writers, never
 * at once (audit `genie-02` item 2, Genie residual #2):
 *   - the mounted panel (GenieChat) claims the signal and drives it;
 *   - before the panel's first mount, the lazy lib/genieLauncherSignal
 *     drives it from the store: the shell loads it when a reload left a turn
 *     to resume, and /ask-genie when it mounts, so a turn resumed on any
 *     route, or started on the route, rings the launchers.
 * The launchers' `aria-describedby` target is GenieDock's until the panel
 * mounts, then GenieChat's.
 *
 *   idle    — nothing to tell the user (no turn, or the panel is open)
 *   running — a turn is in flight while the panel is closed
 *   ready   — a turn landed while the panel was closed and is still unseen
 */
export type GenieTurnStatus = 'idle' | 'running' | 'ready';

/** How the unseen turn ended (mirrors the store's GenieTurnOutcome). */
export type GenieLauncherOutcome = 'answered' | 'withheld' | 'failed';

/** id of the sr-only description the launchers point `aria-describedby` at. */
export const GENIE_LAUNCHER_STATUS_ID = 'genie-launcher-status';

let status: GenieTurnStatus = 'idle';
let outcome: GenieLauncherOutcome = 'answered';
let claims = 0;
const listeners = new Set<() => void>();

export function getGenieTurnStatus(): GenieTurnStatus {
  return status;
}

/** How the unseen turn ended ('answered' unless the status is 'ready'). */
export function getGenieLauncherOutcome(): GenieLauncherOutcome {
  return outcome;
}

export function setGenieTurnStatus(next: GenieTurnStatus, nextOutcome: GenieLauncherOutcome = 'answered'): void {
  if (next === status && nextOutcome === outcome) return;
  status = next;
  outcome = nextOutcome;
  for (const listener of listeners) listener();
}

/** The mounted panel claims the signal: the store-driven writer then writes
 *  nothing. Returns the release. */
export function claimGenieLauncherStatus(): () => void {
  claims += 1;
  let released = false;
  return () => {
    if (released) return;
    released = true;
    claims -= 1;
  };
}

export function isGenieLauncherStatusClaimed(): boolean {
  return claims > 0;
}

export function subscribeGenieTurnStatus(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function useGenieTurnStatus(): GenieTurnStatus {
  return useSyncExternalStore(subscribeGenieTurnStatus, getGenieTurnStatus, getGenieTurnStatus);
}

export function useGenieLauncherOutcome(): GenieLauncherOutcome {
  return useSyncExternalStore(subscribeGenieTurnStatus, getGenieLauncherOutcome, getGenieLauncherOutcome);
}

/** State class shared by both launchers (`.genie__fab`, `.topbar__icon-btn`). */
export function genieLauncherStateClass(current: GenieTurnStatus): string {
  if (current === 'running') return 'is-genie-running';
  if (current === 'ready') return 'is-genie-ready';
  return '';
}

/** Screen-reader description for the launcher in the given state. "Answer
 *  ready" only for an answered turn: a withheld or failed turn has no answer
 *  to read, only a result to see. */
export function genieLauncherStatusText(
  current: GenieTurnStatus,
  outcome: GenieLauncherOutcome = 'answered',
): string {
  if (current === 'running') return 'Genie is still working on your question.';
  if (current === 'ready') {
    return outcome === 'answered'
      ? 'Genie answer ready. Open Genie to read it.'
      : 'Genie finished your question. Open Genie to see the result.';
  }
  return '';
}
