/**
 * A scripted Genie turn whose governed completion runs as a server-side JOB
 * (audit 2026-09-21 `genie-01`): submit advertises `completion_jobs`, the
 * complete call answers 202 with the job's status, and `/message/status` is
 * polled until the job is terminal. Mirrors backend/api/genie.py and
 * backend/services/genie_completion_stages.py.
 *
 * Nothing here is registered by default (like ./genieTurn.ts): a spec calls
 * `registerGenieJob(mockApi, ...)` and steps the job itself, so every stage the
 * rail shows is the one the test put on the wire. The answer is the shared
 * synthetic `genieAnswerFixture` (read-only import): no borrower ids, names or
 * contact fields, and it never overlays a live turn in the running app.
 *
 * Audit genie-03 / genie-01: the script can also answer `/message/cancel`
 * (an outcome, a delay and a status the test picks, recording every body
 * the browser sent) and stamp `typical_seconds` on every running status.
 */
import type { GenieAnswer } from '../../../../src/types';
import type { GenieLiveProgress } from '../../../../src/lib/apiTypes';
import type {
  GenieCancelResult,
  GenieCompletionJobStatus,
  GenieJobStage,
  GenieSubmitResultWithJobs,
} from '../../../../src/types/genieJobs';
import type { ContractSample } from '../contractSamples';
import type { FixtureReply, FixtureRequest, MockApi } from '../mockApi';
import { GENIE_CONVERSATION_ID } from './genie';
import { GENIE_MESSAGE_ID, GENIE_PROGRESS_TOKEN, genieAnswerFixture, genieProgressFixture } from './genieTurn';

export const GENIE_JOB_ID = '5f0c1a2b-3c4d-4e5f-8a6b-7c8d9e0f1a2b';

/** The backend's server-authored stage copy (genie_completion_stages.py). */
export const GENIE_JOB_STAGE_LABELS: Record<GenieJobStage, string> = {
  queued: 'Queued for governed completion',
  collecting: "Collecting Genie's finished turn",
  repairing: 'Asking Genie to regenerate governed SQL',
  verifying: 'Verifying the answer against its rows',
  cross_checking: 'Cross-checking against the governed metric framing',
  rewriting: 'Rewriting the summary from the verified figures',
  planning: 'Planning governed sub-analyses',
  researching: 'Running governed sub-analyses',
  synthesizing: 'Writing the cross-section summary from verified results',
  finalizing: 'Applying the output policy and recording the answer',
  done: 'Governed answer recorded',
  failed: 'Genie could not complete this question',
  expired: 'This Genie answer expired before it was collected',
  cancelled: 'Stopped at your request before the answer was recorded',
};

/** The job status as it travels: the app's GenieCompletionJobStatus, with the
 *  answer in the shape the fixture answers use (the same JSON). */
export type GenieJobStatusBody = Omit<GenieCompletionJobStatus, 'response'> & { response: GenieAnswer | null };

export const GENIE_JOB_EXPIRED_HINT = 'This answer is no longer available here. Check History, or ask the question again.';
export const GENIE_JOB_CANCELLED_HINT = 'You stopped this question before its answer was recorded. Ask it again to get an answer.';

/** One step of a scripted job: a running stage (optionally with sweep
 *  parts), or a terminal outcome. */
export type GenieJobStep =
  | { stage: GenieJobStage; parts?: readonly [done: number, planned: number] }
  | 'succeeded'
  | 'expired'
  | 'cancelled';

export function genieJobStatusFixture(
  step: GenieJobStep,
  answer: GenieAnswer = genieAnswerFixture(),
  typicalSeconds?: number,
): GenieJobStatusBody {
  if (step === 'succeeded') {
    return { ...base('succeeded', 'done'), terminal: true, response: answer };
  }
  if (step === 'expired') {
    return { ...base('expired', 'expired'), terminal: true, failed: true, error_hint: GENIE_JOB_EXPIRED_HINT };
  }
  if (step === 'cancelled') {
    return { ...base('cancelled', 'cancelled'), terminal: true, error_hint: GENIE_JOB_CANCELLED_HINT };
  }
  const status = step.stage === 'queued' ? 'queued' : 'running';
  return {
    ...base(status, step.stage),
    parts_done: step.parts?.[0] ?? null,
    parts_planned: step.parts?.[1] ?? null,
    ...(typicalSeconds === undefined ? {} : { typical_seconds: typicalSeconds }),
  };
}

/** The server's answer to a Stop (backend GenieCancelResponse). */
export function genieCancelFixture(outcome: GenieCancelResult['outcome']): GenieCancelResult {
  const status = outcome === 'cancelled' ? 'running' : outcome === 'recorded' ? 'succeeded' : 'failed';
  return { kind: 'genie_completion_cancel', job_id: GENIE_JOB_ID, outcome, status };
}

function base(status: GenieCompletionJobStatus['status'], stage: GenieJobStage): GenieJobStatusBody {
  return {
    kind: 'genie_completion_job',
    job_id: GENIE_JOB_ID,
    status,
    stage,
    stage_label: GENIE_JOB_STAGE_LABELS[stage],
    parts_done: null,
    parts_planned: null,
    terminal: false,
    failed: false,
    error_hint: null,
    response: null,
  };
}

export function genieJobSubmitFixture(deep = false): GenieSubmitResultWithJobs {
  return {
    completed: false,
    conversation_id: GENIE_CONVERSATION_ID,
    message_id: GENIE_MESSAGE_ID,
    progress_token: GENIE_PROGRESS_TOKEN,
    question_hash: 'd'.repeat(16),
    deep,
    completion_jobs: true,
    response: null,
  };
}

export interface GenieJobScript {
  deep?: boolean;
  answer?: GenieAnswer;
  /** Stages the status poll walks through with `next()`; the first is the 202's. */
  steps?: readonly GenieJobStep[];
  /** Hold every complete call until `releaseComplete()`. */
  holdComplete?: boolean;
  /** Stamped on every running status (audit genie-01 duration hint). */
  typicalSeconds?: number;
  /** How `/message/cancel` answers (audit genie-03): its outcome, a real
   *  delay before the reply, and the HTTP status (default 200). */
  cancel?: { outcome: GenieCancelResult['outcome']; delayMs?: number; status?: number };
}

export interface GenieJobController {
  readonly submits: number;
  readonly progressPolls: number;
  readonly completes: number;
  readonly statusPolls: number;
  readonly cancels: number;
  /** Request bodies the browser really sent. */
  readonly completeBodies: readonly unknown[];
  readonly statusBodies: readonly unknown[];
  readonly cancelBodies: readonly unknown[];
  /** The step the next status poll reports. */
  readonly step: GenieJobStep;
  /** Advance the job one scripted step (the last one repeats). */
  next(): void;
  releaseComplete(): void;
  /** Hold every status reply until the returned release is called. */
  holdStatus(): () => void;
}

const DEFAULT_STEPS: readonly GenieJobStep[] = [
  { stage: 'queued' },
  { stage: 'verifying' },
  { stage: 'cross_checking' },
  { stage: 'finalizing' },
  'succeeded',
];

function gate(): { promise: Promise<void>; open: () => void } {
  let open: () => void = () => undefined;
  const promise = new Promise<void>((resolve) => {
    open = resolve;
  });
  return { promise, open };
}

export function registerGenieJob(mockApi: MockApi, script: GenieJobScript = {}): GenieJobController {
  const answer = script.answer ?? genieAnswerFixture();
  const steps = script.steps ?? DEFAULT_STEPS;
  let index = 0;
  const completeGate = script.holdComplete ? gate() : null;
  let statusGate: Promise<void> = Promise.resolve();
  const counts = { submits: 0, progressPolls: 0, completes: 0, statusPolls: 0, cancels: 0 };
  const completeBodies: unknown[] = [];
  const statusBodies: unknown[] = [];
  const cancelBodies: unknown[] = [];
  const current = () => genieJobStatusFixture(steps[index], answer, script.typicalSeconds);

  mockApi.register<GenieSubmitResultWithJobs>('POST', '/api/genie/message/submit', () => {
    counts.submits += 1;
    return { body: genieJobSubmitFixture(script.deep === true) };
  });
  mockApi.register<GenieLiveProgress>('POST', '/api/genie/message/progress', () => {
    counts.progressPolls += 1;
    return { body: genieProgressFixture('COMPLETED') };
  });
  mockApi.register<GenieJobStatusBody>('POST', '/api/genie/message/complete', async (request: FixtureRequest) => {
    counts.completes += 1;
    completeBodies.push(request.body);
    if (completeGate) await completeGate.promise;
    return { status: 202, body: current() };
  });
  mockApi.register<GenieJobStatusBody>('POST', '/api/genie/message/status', async (request: FixtureRequest) => {
    counts.statusPolls += 1;
    statusBodies.push(request.body);
    await statusGate;
    return { body: current() };
  });
  mockApi.register<GenieCancelResult | { detail: string }>('POST', '/api/genie/message/cancel', async (request: FixtureRequest) => {
    counts.cancels += 1;
    cancelBodies.push(request.body);
    const cancel = script.cancel ?? { outcome: 'cancelled' as const };
    if (cancel.delayMs) await new Promise((resolve) => setTimeout(resolve, cancel.delayMs));
    const reply: FixtureReply<GenieCancelResult | { detail: string }> =
      (cancel.status ?? 200) === 200
        ? { body: genieCancelFixture(cancel.outcome) }
        : { status: cancel.status, body: { detail: 'lakebase is temporarily unavailable' } };
    return reply;
  });

  return {
    get submits() {
      return counts.submits;
    },
    get progressPolls() {
      return counts.progressPolls;
    },
    get completes() {
      return counts.completes;
    },
    get statusPolls() {
      return counts.statusPolls;
    },
    get cancels() {
      return counts.cancels;
    },
    completeBodies,
    statusBodies,
    cancelBodies,
    get step() {
      return steps[index];
    },
    next() {
      index = Math.min(index + 1, steps.length - 1);
    },
    releaseComplete() {
      completeGate?.open();
    },
    holdStatus() {
      const held = gate();
      statusGate = held.promise;
      return () => {
        statusGate = Promise.resolve();
        held.open();
      };
    },
  };
}

/** One exported sample per wire shape, for the fixture contract exporter
 *  (w3-api-contract). */
export function contractSamples(): ContractSample[] {
  const sample = (pattern: string, status: number, body: unknown): ContractSample => ({
    source: 'data/genieJobs.ts',
    method: 'POST',
    pattern,
    path: pattern.replace(/^\/api\//, '/api/v1/'),
    query: '',
    status,
    body,
  });
  const running: GenieJobStep[] = [
    { stage: 'queued' },
    { stage: 'collecting' },
    { stage: 'verifying' },
    { stage: 'cross_checking' },
    { stage: 'planning' },
    { stage: 'researching', parts: [3, 7] },
    { stage: 'synthesizing' },
    { stage: 'finalizing' },
  ];
  return [
    sample('/api/genie/message/submit', 200, genieJobSubmitFixture(true)),
    sample('/api/genie/message/complete', 202, genieJobStatusFixture({ stage: 'queued' })),
    ...running.map((step) => sample('/api/genie/message/status', 200, genieJobStatusFixture(step))),
    sample('/api/genie/message/status', 200, genieJobStatusFixture('succeeded')),
    sample('/api/genie/message/status', 200, genieJobStatusFixture('expired')),
    sample('/api/genie/message/status', 200, genieJobStatusFixture('cancelled')),
    sample('/api/genie/message/status', 200, genieJobStatusFixture({ stage: 'researching', parts: [3, 7] }, undefined, 170)),
    sample('/api/genie/message/complete', 202, genieJobStatusFixture({ stage: 'queued' }, undefined, 42)),
    ...(['cancelled', 'recorded', 'ended'] as const).map((outcome) =>
      sample('/api/genie/message/cancel', 200, genieCancelFixture(outcome)),
    ),
  ];
}
