/**
 * Verified sections of a running deep-research job (audit 2026-09-21
 * `genie-01` phase 1b): a tiny in-memory store the job poll publishes into
 * and the progress card reads.
 *
 * The in-flight turn store (lib/genieInFlightTurn.ts) copies only five job
 * fields into the rail, and it is off-limits in this wave, so the reveal
 * travels here instead. It holds nothing in browser storage (no actor-scope
 * registry entry): a reload re-fetches the sections on its first poll.
 *
 *  - beginGenieReveal(jobId): a new job starts from nothing.
 *  - publishGenieReveal(status): below the floor there is nothing to show; a
 *    non-null `revealed_sections` replaces the sections; otherwise (the same
 *    revision, not re-sent) the current ones stay. Returns the revision the
 *    next poll sends.
 *  - endGenieReveal(jobId, 'settled' | 'ended'): a settled job clears on the
 *    next task, after the answer has replaced the rail, so nothing collapses
 *    mid-paint; a failed, stopped or expired one clears at once (the server
 *    has withdrawn the sections too).
 *
 * Every actor-scope event except 'opened' clears it: another actor must
 * never see a turn's partial research.
 */
import { useSyncExternalStore } from 'react';
import type { GenieAnswerSection } from '../types';
import type { GenieCompletionJobStatus } from '../types/genieJobs';
import { subscribeActorScope } from './actorScope';

/** The sweep's shipping floor (backend REVEAL_SECTION_FLOOR). */
export const GENIE_REVEAL_FLOOR = 3;

export interface GenieVerifiedRevealState {
  jobId: string;
  /** Sub-analyses verified so far (0 before the server says otherwise). */
  verified: number;
  partsPlanned: number | null;
  /** The revision of `sections`; the poll sends it back. */
  rev: number | null;
  /** Null below the floor; plan-ordered verified sections from it. */
  sections: readonly GenieAnswerSection[] | null;
}

let current: GenieVerifiedRevealState | null = null;
const listeners = new Set<() => void>();

function replace(next: GenieVerifiedRevealState | null): void {
  if (next === current) return;
  current = next;
  for (const listener of [...listeners]) listener();
}

export function beginGenieReveal(jobId: string): void {
  if (current?.jobId === jobId) return;
  replace({ jobId, verified: 0, partsPlanned: null, rev: null, sections: null });
}

export function publishGenieReveal(status: GenieCompletionJobStatus): number | null {
  const reveal = current;
  if (!reveal || reveal.jobId !== status.job_id) return null;
  // A terminal status carries no sections: endGenieReveal decides when they
  // go, so a succeeded job's sections stay until the answer replaces them.
  if (status.terminal) return reveal.rev;
  const verified = typeof status.verified_sections === 'number' ? status.verified_sections : 0;
  const rev = typeof status.sections_rev === 'number' ? status.sections_rev : reveal.rev;
  let sections = reveal.sections;
  if (verified < GENIE_REVEAL_FLOOR) sections = null;
  else if (status.revealed_sections) sections = status.revealed_sections;
  replace({ jobId: reveal.jobId, verified, partsPlanned: status.parts_planned ?? null, rev, sections });
  return rev;
}

export function endGenieReveal(jobId: string, how: 'settled' | 'ended'): void {
  const clear = () => {
    if (current?.jobId === jobId) replace(null);
  };
  if (how === 'ended') clear();
  else setTimeout(clear, 0);
}

/** The one line shown below the floor; null before anything is verified. */
export function genieRevealCountLine(reveal: GenieVerifiedRevealState): string | null {
  if (reveal.verified <= 0) return null;
  const of = typeof reveal.partsPlanned === 'number' && reveal.partsPlanned > 0 ? ` of ${reveal.partsPlanned}` : '';
  return `Partial research · ${reveal.verified}${of} sub-analyses verified so far`;
}

export function genieRevealSnapshot(): GenieVerifiedRevealState | null {
  return current;
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function useGenieVerifiedReveal(): GenieVerifiedRevealState | null {
  return useSyncExternalStore(subscribe, genieRevealSnapshot, () => null);
}

subscribeActorScope(({ reason }) => {
  if (reason !== 'opened') replace(null);
});
