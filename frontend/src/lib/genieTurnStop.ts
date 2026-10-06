/**
 * What a Stop on a job-backed Genie turn says once the server answered
 * (audit 2026-09-21 `genie-03`, the 2026-09-30 Stop copy ruling). Pure: the
 * in-flight store (lib/genieInFlightTurn.ts) calls it and owns the note.
 *
 * Only `recorded` is promised to be in History: the server answers it only
 * when the turn's History row exists. `recording` means the Stop came too
 * late and no History row was found; the job's status says whether it is
 * still being written, is an answer History never keeps, or did not finish.
 * `cancelled` and `ended` are the confirmed Stopped copy, never announced
 * (the Stop itself was). Any other combination keeps the unconfirmed note.
 */
import type { GenieTurnCancelResult } from './genieTurnCancel';
import {
  GENIE_STOP_CONFIRMED_REASON,
  GENIE_STOP_INCOMPLETE_REASON,
  GENIE_STOP_NOT_KEPT_REASON,
  GENIE_STOP_RECORDED_REASON,
  GENIE_STOP_RECORDING_REASON,
} from './genieTurnOutcome';

export interface GenieStopNote {
  /** The Stopped note's new visible text. */
  readonly reason: string;
  /** Said through the surface announcer (a too-late Stop is news). */
  readonly announce: boolean;
}

export function stopNoteFor(result: GenieTurnCancelResult | null): GenieStopNote | null {
  if (result === null) return null;
  const { outcome, status } = result;
  if (outcome === 'cancelled' || outcome === 'ended') return { reason: GENIE_STOP_CONFIRMED_REASON, announce: false };
  if (outcome === 'recorded') return { reason: GENIE_STOP_RECORDED_REASON, announce: true };
  if (status === 'running') return { reason: GENIE_STOP_RECORDING_REASON, announce: true };
  if (status === 'succeeded') return { reason: GENIE_STOP_NOT_KEPT_REASON, announce: true };
  if (status === 'failed' || status === 'expired') return { reason: GENIE_STOP_INCOMPLETE_REASON, announce: true };
  return null;
}
