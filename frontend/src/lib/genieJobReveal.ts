/**
 * Verified sections of a running deep-research job (audit 2026-09-21
 * `genie-01` phase 1b), as the in-flight turn holds them: pure, keyed to its
 * job. The in-flight store (lib/genieInFlightTurn.ts `showJob`) folds every
 * running job status into `progress.job.reveal` with `nextJobReveal`, so the
 * reveal lives and dies with the in-flight turn: it is gone in the same
 * update that lands the answer, and with a failure, a Stop, an expiry or any
 * actor-scope reset or close. Nothing here is stored or announced.
 *
 *  - a status of a different job starts from nothing;
 *  - a terminal status changes nothing (the answer or the failure lands next);
 *  - below the floor there is nothing to show;
 *  - a non-null `revealed_sections` replaces the sections, otherwise (the
 *    same revision, not re-sent) the current ones stay.
 */
import type { GenieAnswerSection } from '../types';
import type { GenieCompletionJobStatus } from '../types/genieJobs';

/** The sweep's shipping floor (backend REVEAL_SECTION_FLOOR). */
export const GENIE_REVEAL_FLOOR = 3;

export interface GenieVerifiedRevealState {
  jobId: string;
  /** Sub-analyses verified so far (0 before the server says otherwise). */
  verified: number;
  partsPlanned: number | null;
  /** The revision of `sections`. */
  rev: number | null;
  /** Null below the floor; plan-ordered verified sections from it. */
  sections: readonly GenieAnswerSection[] | null;
}

function fresh(jobId: string): GenieVerifiedRevealState {
  return { jobId, verified: 0, partsPlanned: null, rev: null, sections: null };
}

/** The reveal after `status`, given the one the turn held. */
export function nextJobReveal(
  prev: GenieVerifiedRevealState | null | undefined,
  status: GenieCompletionJobStatus,
): GenieVerifiedRevealState {
  const base = prev && prev.jobId === status.job_id ? prev : fresh(status.job_id);
  if (status.terminal) return base;
  const verified = typeof status.verified_sections === 'number' ? status.verified_sections : 0;
  const rev = typeof status.sections_rev === 'number' ? status.sections_rev : base.rev;
  let sections = base.sections;
  if (verified < GENIE_REVEAL_FLOOR) sections = null;
  else if (status.revealed_sections) sections = status.revealed_sections;
  return { jobId: base.jobId, verified, partsPlanned: status.parts_planned ?? null, rev, sections };
}

/** The one line shown below the floor; null before anything is verified. */
export function genieRevealCountLine(reveal: GenieVerifiedRevealState): string | null {
  if (reveal.verified <= 0) return null;
  const of = typeof reveal.partsPlanned === 'number' && reveal.partsPlanned > 0 ? ` of ${reveal.partsPlanned}` : '';
  return `Partial research · ${reveal.verified}${of} sub-analyses verified so far`;
}
