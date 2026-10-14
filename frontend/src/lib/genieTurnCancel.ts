/**
 * The server half of Stop on a job-backed Genie turn (audit 2026-09-21
 * `genie-03`): `POST /api/genie/message/cancel`.
 *
 * The store stops the turn synchronously and shows the unconfirmed Stopped
 * note at once; this call then asks the server not to record the answer.
 * It sends the turn's ids, the job the 202 named (null before it: the cancel
 * is then turn-keyed and the server pre-cancels the turn's job) and the
 * submit's 16-hex question label, never the question. It never throws: any
 * failure, a reply about another job (or no UUID at all), or an outcome or
 * status this client does not know, is `null`, and the note keeps its honest
 * unconfirmed copy.
 */
import { genieJobsApi } from './apiClients/genieJobs';
import type { GenieTurnIds } from './genieAsk';
import type { GenieJobStatusValue } from '../types/genieJobs';

/** What a confirmed Stop can say. `recording`: too late to stop, and the
 *  answer's History row was not found (yet); `status` says why. */
export type GenieTurnCancelOutcome = 'cancelled' | 'recorded' | 'recording' | 'ended';

/** The server's verdict on a Stop: its outcome and the job's status after it. */
export interface GenieTurnCancelResult {
  readonly outcome: GenieTurnCancelOutcome;
  readonly status: GenieJobStatusValue;
}

export interface GenieTurnCancelTarget {
  readonly ids: GenieTurnIds;
  /** The job the 202 named; null for a Stop before it (turn-keyed). */
  readonly jobId: string | null;
  readonly questionHash: string;
}

const JOB_ID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

const OUTCOMES: ReadonlySet<string> = new Set<GenieTurnCancelOutcome>(['cancelled', 'recorded', 'recording', 'ended']);
const STATUSES: ReadonlySet<string> = new Set<GenieJobStatusValue>([
  'queued',
  'running',
  'succeeded',
  'failed',
  'expired',
  'cancelled',
]);

function isOutcome(value: unknown): value is GenieTurnCancelOutcome {
  return typeof value === 'string' && OUTCOMES.has(value);
}

function isStatus(value: unknown): value is GenieJobStatusValue {
  return typeof value === 'string' && STATUSES.has(value);
}

export async function requestGenieTurnCancel(target: GenieTurnCancelTarget): Promise<GenieTurnCancelResult | null> {
  try {
    const result = await genieJobsApi.genieCancel(target.ids, target.jobId, target.questionHash);
    if (result?.kind !== 'genie_completion_cancel' || typeof result.job_id !== 'string') return null;
    if (!JOB_ID_RE.test(result.job_id) || (target.jobId !== null && result.job_id !== target.jobId)) return null;
    const { outcome, status } = result;
    return isOutcome(outcome) && isStatus(status) ? { outcome, status } : null;
  } catch {
    return null;
  }
}
