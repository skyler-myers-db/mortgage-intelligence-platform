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
  | 'expired';

export type GenieJobStatusValue = 'queued' | 'running' | 'succeeded' | 'failed' | 'expired';

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
}

/** What the progress rail shows of a running job (no id, no answer). */
export interface GenieJobProgress {
  stage: GenieJobStage;
  stage_label: string;
  parts_done: number | null;
  parts_planned: number | null;
}

/** Genie's own (last) progress, plus the completion job once there is one. */
export type GenieTurnProgress = GenieLiveProgress & { job?: GenieJobProgress };

/** Submit's live response: `completion_jobs` when the server runs jobs. */
export type GenieSubmitResultWithJobs = GenieSubmitResult & { completion_jobs?: boolean };
