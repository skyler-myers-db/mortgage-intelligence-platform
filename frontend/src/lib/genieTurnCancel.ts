/**
 * The server half of Stop on a job-backed Genie turn (audit 2026-09-21
 * `genie-03`): `POST /api/genie/message/cancel`.
 *
 * The store stops the turn synchronously and shows the unconfirmed Stopped
 * note at once; this call then asks the server not to record the answer.
 * It sends the turn's ids, the job the 202 named and the submit's 16-hex
 * question label, never the question. It never throws: any failure, or an
 * outcome this client does not know, is `null`, and the note keeps its
 * honest unconfirmed copy.
 */
import { genieJobsApi } from './apiClients/genieJobs';
import type { GenieTurnIds } from './genieAsk';

/** What a confirmed Stop can say. */
export type GenieTurnCancelOutcome = 'cancelled' | 'recorded' | 'ended';

export interface GenieTurnCancelTarget {
  readonly ids: GenieTurnIds;
  readonly jobId: string;
  readonly questionHash: string;
}

const OUTCOMES: ReadonlySet<string> = new Set<GenieTurnCancelOutcome>(['cancelled', 'recorded', 'ended']);

function isOutcome(value: unknown): value is GenieTurnCancelOutcome {
  return typeof value === 'string' && OUTCOMES.has(value);
}

export async function requestGenieTurnCancel(target: GenieTurnCancelTarget): Promise<GenieTurnCancelOutcome | null> {
  try {
    const result = await genieJobsApi.genieCancel(target.ids, target.jobId, target.questionHash);
    if (result?.kind !== 'genie_completion_cancel' || result.job_id !== target.jobId) return null;
    return isOutcome(result.outcome) ? result.outcome : null;
  } catch {
    return null;
  }
}
