/**
 * Genie completion-job contracts (audit 2026-09-21 `genie-01`).
 *
 * The governed completion of a live turn runs as a server-side job:
 * `POST /api/genie/message/complete` with `respond_async` answers 202 with a
 * `GenieCompletionJobStatus`, and `POST /api/genie/message/status` is polled
 * until the job is terminal. Mirrors the backend `GenieCompletionJobStatus`
 * and the closed vocabularies in `genie_completion_stages.py`.
 */
import type { GenieLiveProgress, GenieResult, GenieSubmitResult } from '../lib/apiTypes';
import type { GenieVerifiedRevealState } from '../lib/genieJobReveal';
import type { GenieAnswerSection } from './genie';

/** Server-owned completion stages; `stage_label` is the words for each. */
export type GenieJobStage =
  | 'queued'
  | 'collecting'
  | 'repairing'
  | 'verifying'
  | 'cross_checking'
  | 'rewriting'
  | 'planning'
  | 'researching'
  | 'synthesizing'
  | 'finalizing'
  | 'done'
  | 'failed'
  | 'expired'
  | 'cancelled';

/** `cancelled` (audit genie-03): the owner stopped the turn before its answer
 *  was recorded. Terminal, and not `failed`. */
export type GenieJobStatusValue = 'queued' | 'running' | 'succeeded' | 'failed' | 'expired' | 'cancelled';

export interface GenieCompletionJobStatus {
  kind: 'genie_completion_job';
  job_id: string;
  status: GenieJobStatusValue;
  stage: GenieJobStage;
  stage_label: string;
  /** Deep-research sweep: governed sub-analyses finished / planned. */
  parts_done: number | null;
  parts_planned: number | null;
  terminal: boolean;
  failed: boolean;
  /** Fixed server copy for a failed or expired job; never exception text. */
  error_hint: string | null;
  /** The governed answer, only once `status` is `succeeded`. */
  response: GenieResult | null;
  /** Recent median completion seconds of this job's class, while it runs
   *  (audit genie-01); absent from older servers. */
  typical_seconds?: number | null;
  /** Verified sections (audit genie-01 phase 1b), only while the job runs
   *  and no cancel was requested: how many sub-analyses passed their own
   *  checks, their revision, and (from the three-section floor, only for a
   *  revision the poll did not send) the sections themselves, plan-ordered.
   *  Absent from older servers. */
  verified_sections?: number | null;
  sections_rev?: number | null;
  revealed_sections?: GenieAnswerSection[] | null;
}

/** What the progress rail shows of a running job (no answer). The verified-
 *  sections reveal is keyed to its job: a status of another job never
 *  renders here (lib/genieJobReveal, folded by the in-flight store). */
export interface GenieJobProgress {
  stage: GenieJobStage;
  stage_label: string;
  parts_done: number | null;
  parts_planned: number | null;
  typical_seconds?: number | null;
  /** Verified sections so far (genie-01 phase 1b); never announced. */
  reveal?: GenieVerifiedRevealState | null;
}

/** `POST /api/genie/message/cancel` (audit genie-03). `cancelled`: this app
 *  will not verify or record the answer (never that Genie's own message was
 *  cancelled). `recorded`: too late, and the answer's History row exists.
 *  `recording`: too late, and no History row was found; `status` says
 *  whether it is still being written (running), is never kept (succeeded) or
 *  did not finish (failed, expired). `ended`: the job had already failed or
 *  expired with nothing recorded. */
export interface GenieCancelResult {
  kind: 'genie_completion_cancel';
  job_id: string;
  outcome: 'cancelled' | 'recorded' | 'recording' | 'ended';
  status: GenieJobStatusValue;
}

/** Genie's own (last) progress, plus the completion job once there is one.
 *  `deep` is NOT on the progress wire (quality-04 P2): the client stamps the
 *  submit response's flag onto every update so the rail can label the long
 *  completion wait honestly on both Genie surfaces. */
export type GenieTurnProgress = GenieLiveProgress & { deep?: boolean; job?: GenieJobProgress };

/** Submit's live response: `completion_jobs` when the server runs jobs. */
export type GenieSubmitResultWithJobs = GenieSubmitResult & { completion_jobs?: boolean };
