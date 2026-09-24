/**
 * askGenieLive lifecycle: submit → progress polling → complete, with the
 * deterministic-inline shortcut, failure hint surfacing, and poll-error
 * tolerance pinned.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { GenieCompletionJobStatus, GenieSubmitResultWithJobs } from '../types/genieJobs';
import { ApiError, api, type GenieLiveProgress } from './api';
import { genieJobsApi } from './apiClients/genieJobs';
import {
  GenieLiveError,
  JOB_RESUME_WINDOW_MS,
  MAX_LIVE_WAIT_MS,
  askGenieLive,
  pollGenieJob,
  pollGenieTurn,
  requestGenieCompletion,
  submitGenieTurn,
} from './genieAsk';

const noSleep = () => Promise.resolve();

function progressOf(partial: Partial<GenieLiveProgress>): GenieLiveProgress {
  return {
    status: 'SUBMITTED',
    stage: 'understanding',
    stage_label: 'Genie accepted the question',
    terminal: false,
    failed: false,
    reasoning_trace: [],
    sql_preview: null,
    error_hint: null,
    ...partial,
  };
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe('askGenieLive', () => {
  it('returns the inline response for deterministically completed turns', async () => {
    vi.spyOn(api, 'genieSubmit').mockResolvedValue({
      completed: true,
      response: { answer: 'refused', source: 'refused', trusted_assets: [] },
    });
    const progressSpy = vi.spyOn(api, 'genieProgress');

    const result = await askGenieLive('question?', null, { sleep: noSleep });

    expect(result.answer).toBe('refused');
    expect(progressSpy).not.toHaveBeenCalled();
  });

  it('polls progress until terminal then completes', async () => {
    vi.spyOn(api, 'genieSubmit').mockResolvedValue({
      completed: false,
      conversation_id: 'conv-1',
      message_id: 'msg-1',
      progress_token: 'tok',
      question_hash: 'hash',
    });
    const states = [
      progressOf({ status: 'ASKING_AI', stage: 'drafting', stage_label: 'Drafting' }),
      progressOf({ status: 'EXECUTING_QUERY', stage: 'executing', stage_label: 'Running' }),
      progressOf({
        status: 'COMPLETED',
        stage: 'complete',
        stage_label: 'Formatting',
        terminal: true,
      }),
    ];
    vi.spyOn(api, 'genieProgress').mockImplementation(() =>
      Promise.resolve(states.shift() ?? progressOf({ terminal: true, status: 'COMPLETED' })),
    );
    const complete = vi.spyOn(api, 'genieComplete').mockResolvedValue({
      answer: 'final answer',
      source: 'genie',
      trusted_assets: [],
    });
    const seen: string[] = [];

    const result = await askGenieLive('question?', 'conv-1', {
      sleep: noSleep,
      onProgress: (p) => seen.push(p.status),
    });

    expect(result.answer).toBe('final answer');
    expect(seen).toEqual(['ASKING_AI', 'EXECUTING_QUERY', 'COMPLETED']);
    expect(complete).toHaveBeenCalledWith('conv-1', 'msg-1', 'tok', 'question?', undefined);
  });

  it('stamps the submit response\'s deep flag onto every progress update (genie-01 phase 0)', async () => {
    const terminal = progressOf({ status: 'COMPLETED', stage: 'complete', terminal: true });
    vi.spyOn(api, 'genieComplete').mockResolvedValue({
      answer: 'final answer',
      source: 'genie',
      trusted_assets: [],
    });

    for (const [submitDeep, expected] of [
      [true, true],
      [false, false],
      // An older backend omits the field entirely: never guessed as deep.
      [undefined, false],
    ] as const) {
      vi.spyOn(api, 'genieSubmit').mockResolvedValue({
        completed: false,
        conversation_id: 'conv-1',
        message_id: 'msg-1',
        progress_token: 'tok',
        ...(submitDeep === undefined ? {} : { deep: submitDeep }),
      });
      const states = [progressOf({ status: 'ASKING_AI', stage: 'drafting' }), terminal];
      vi.spyOn(api, 'genieProgress').mockImplementation(() =>
        Promise.resolve(states.shift() ?? terminal),
      );
      const seen: Array<boolean | undefined> = [];

      await askGenieLive('question?', null, {
        sleep: noSleep,
        onProgress: (p) => seen.push(p.deep),
      });

      expect(seen).toEqual([expected, expected]);
    }
  });

  it('throws GenieLiveError with the canned hint on a failed turn', async () => {
    vi.spyOn(api, 'genieSubmit').mockResolvedValue({
      completed: false,
      conversation_id: 'conv-1',
      message_id: 'msg-1',
      progress_token: 'tok',
    });
    vi.spyOn(api, 'genieProgress').mockResolvedValue(
      progressOf({
        status: 'FAILED',
        stage: 'failed',
        stage_label: 'Failed',
        terminal: true,
        failed: true,
        error_hint: 'Genie reported a failure for this turn.',
      }),
    );
    const complete = vi.spyOn(api, 'genieComplete');

    await expect(askGenieLive('question?', null, { sleep: noSleep })).rejects.toThrowError(
      GenieLiveError,
    );
    expect(complete).not.toHaveBeenCalled();
  });

  it('tolerates transient poll failures and keeps waiting', async () => {
    vi.spyOn(api, 'genieSubmit').mockResolvedValue({
      completed: false,
      conversation_id: 'conv-1',
      message_id: 'msg-1',
      progress_token: 'tok',
    });
    let calls = 0;
    vi.spyOn(api, 'genieProgress').mockImplementation(() => {
      calls += 1;
      if (calls < 3) return Promise.reject(new Error('transient 503'));
      return Promise.resolve(progressOf({ status: 'COMPLETED', stage: 'complete', terminal: true }));
    });
    vi.spyOn(api, 'genieComplete').mockResolvedValue({
      answer: 'recovered',
      source: 'genie',
      trusted_assets: [],
    });

    const result = await askGenieLive('question?', null, { sleep: noSleep });

    expect(result.answer).toBe('recovered');
    expect(calls).toBe(3);
  });

  it('gives up after repeated consecutive poll failures', async () => {
    vi.spyOn(api, 'genieSubmit').mockResolvedValue({
      completed: false,
      conversation_id: 'conv-1',
      message_id: 'msg-1',
      progress_token: 'tok',
    });
    vi.spyOn(api, 'genieProgress').mockRejectedValue(new Error('genie down'));

    await expect(askGenieLive('question?', null, { sleep: noSleep })).rejects.toThrowError(
      'genie down',
    );
  });
});

const IDS = { conversationId: 'conv-1', messageId: 'msg-1', progressToken: 'tok' };

describe('submitGenieTurn / pollGenieTurn (runtime-01 split)', () => {
  it('submit returns the live ids and deep flag, or the inline answer, and POSTs exactly once', async () => {
    const submit = vi.spyOn(api, 'genieSubmit').mockResolvedValueOnce({
      completed: false,
      conversation_id: 'conv-1',
      message_id: 'msg-1',
      progress_token: 'tok',
      deep: true,
    });
    await expect(submitGenieTurn('question?', 'conv-1')).resolves.toEqual({
      kind: 'live',
      conversationId: 'conv-1',
      messageId: 'msg-1',
      progressToken: 'tok',
      deep: true,
      completionJobs: false,
    });
    submit.mockResolvedValueOnce({
      completed: true,
      response: { answer: 'inline', source: 'refused', trusted_assets: [] },
    });
    await expect(submitGenieTurn('question?', null)).resolves.toMatchObject({
      kind: 'completed',
      response: { answer: 'inline' },
    });
    expect(submit).toHaveBeenCalledTimes(2);
  });

  it('submit never re-POSTs on a failure: the error goes to the caller', async () => {
    const submit = vi.spyOn(api, 'genieSubmit').mockRejectedValue(
      new ApiError('warming', { path: '/api/genie/message/submit', status: 503, retryable: true }),
    );
    await expect(submitGenieTurn('question?', null)).rejects.toThrowError('warming');
    expect(submit).toHaveBeenCalledTimes(1);
  });

  it('poll gives up at the deadline it was handed, not one it computes', async () => {
    const progress = vi
      .spyOn(api, 'genieProgress')
      .mockResolvedValue(progressOf({ status: 'EXECUTING_QUERY', stage: 'executing' }));
    // A resumed turn keeps its original start: a deadline already in the past
    // ends the wait after the first non-terminal poll.
    await expect(
      pollGenieTurn(IDS, { deadline: Date.now() - 1, sleep: noSleep }),
    ).rejects.toThrowError(/taking longer than expected/);
    expect(progress).toHaveBeenCalledTimes(1);
    expect(progress).toHaveBeenCalledWith('conv-1', 'msg-1', 'tok', undefined);
  });

  it.each([400, 403])('poll fails at once on a %i instead of counting it as transient', async (status) => {
    const progress = vi
      .spyOn(api, 'genieProgress')
      .mockRejectedValue(new ApiError('token rejected', { path: '/api/genie/message/progress', status }));
    await expect(
      pollGenieTurn(IDS, { deadline: Date.now() + MAX_LIVE_WAIT_MS, sleep: noSleep }),
    ).rejects.toMatchObject({ status });
    expect(progress).toHaveBeenCalledTimes(1);
  });

  it('poll still tolerates a transient 503 and stamps the deep flag it is handed', async () => {
    let calls = 0;
    vi.spyOn(api, 'genieProgress').mockImplementation(() => {
      calls += 1;
      if (calls === 1) {
        return Promise.reject(new ApiError('busy', { path: '/api/genie/message/progress', status: 503 }));
      }
      return Promise.resolve(progressOf({ status: 'COMPLETED', stage: 'complete', terminal: true }));
    });
    const seen: Array<boolean | undefined> = [];
    await pollGenieTurn(IDS, {
      deadline: Date.now() + MAX_LIVE_WAIT_MS,
      deep: true,
      sleep: noSleep,
      onProgress: (p) => seen.push(p.deep),
    });
    expect(calls).toBe(2);
    expect(seen).toEqual([true]);
  });
});

// --- audit 2026-09-21 genie-01: completion as a server-side job -----------

function jobOf(partial: Partial<GenieCompletionJobStatus> = {}): GenieCompletionJobStatus {
  return {
    kind: 'genie_completion_job',
    job_id: '0a1b2c3d-0000-4000-8000-000000000001',
    status: 'running',
    stage: 'verifying',
    stage_label: 'Verifying the answer against its rows',
    parts_done: null,
    parts_planned: null,
    terminal: false,
    failed: false,
    error_hint: null,
    response: null,
    ...partial,
  };
}

const ANSWER = { answer: 'final answer', source: 'genie', trusted_assets: [] };

describe('requestGenieCompletion (genie-01)', () => {
  it('discriminates a 202 job from a 200 answer an older server sends', async () => {
    const completeAsync = vi
      .spyOn(genieJobsApi, 'genieCompleteAsync')
      .mockResolvedValueOnce(jobOf({ status: 'queued', stage: 'queued' }))
      .mockResolvedValueOnce(ANSWER);

    await expect(requestGenieCompletion(IDS, 'question?', { asyncComplete: true })).resolves.toMatchObject({
      kind: 'job',
      job: { status: 'queued' },
    });
    await expect(requestGenieCompletion(IDS, 'question?', { asyncComplete: true })).resolves.toEqual({
      kind: 'answer',
      response: ANSWER,
    });
    expect(completeAsync).toHaveBeenNthCalledWith(1, { ...IDS, question: 'question?' }, expect.any(AbortSignal));
  });

  it('legacy (no jobs) is today\'s single blocking complete, with no timeout', async () => {
    const complete = vi.spyOn(api, 'genieComplete').mockResolvedValue(ANSWER);
    const completeAsync = vi.spyOn(genieJobsApi, 'genieCompleteAsync');

    await expect(requestGenieCompletion(IDS, 'question?', { asyncComplete: false })).resolves.toEqual({
      kind: 'answer',
      response: ANSWER,
    });
    expect(complete).toHaveBeenCalledWith('conv-1', 'msg-1', 'tok', 'question?', undefined);
    expect(completeAsync).not.toHaveBeenCalled();
  });

  it('re-sends a timed-out async complete exactly once, then gives up', async () => {
    const hang = (_turn: unknown, signal?: AbortSignal) =>
      new Promise<GenieCompletionJobStatus>((_resolve, reject) => {
        signal?.addEventListener('abort', () => reject(new ApiError('aborted', { path: '', aborted: true })));
      });
    const completeAsync = vi
      .spyOn(genieJobsApi, 'genieCompleteAsync')
      .mockImplementationOnce(hang)
      .mockResolvedValueOnce(jobOf());

    await expect(
      requestGenieCompletion(IDS, 'question?', { asyncComplete: true, timeoutMs: 5 }),
    ).resolves.toMatchObject({ kind: 'job' });
    expect(completeAsync).toHaveBeenCalledTimes(2);

    completeAsync.mockReset();
    completeAsync.mockImplementation(hang);
    await expect(
      requestGenieCompletion(IDS, 'question?', { asyncComplete: true, timeoutMs: 5 }),
    ).rejects.toThrowError(GenieLiveError);
    expect(completeAsync).toHaveBeenCalledTimes(2);
  });

  it('never re-sends after a user abort', async () => {
    const controller = new AbortController();
    const completeAsync = vi
      .spyOn(genieJobsApi, 'genieCompleteAsync')
      .mockImplementation(
        (_turn, signal) =>
          new Promise((_resolve, reject) => {
            signal?.addEventListener('abort', () => reject(new ApiError('aborted', { path: '', aborted: true })));
            controller.abort();
          }),
      );

    await expect(
      requestGenieCompletion(IDS, 'question?', { asyncComplete: true, signal: controller.signal, timeoutMs: 60_000 }),
    ).rejects.toMatchObject({ aborted: true });
    expect(completeAsync).toHaveBeenCalledTimes(1);
  });
});

describe('pollGenieJob (genie-01)', () => {
  it('polls until the job succeeds and returns its answer, reporting every status', async () => {
    const status = vi
      .spyOn(genieJobsApi, 'genieJobStatus')
      .mockResolvedValueOnce(jobOf({ stage: 'researching', parts_done: 3, parts_planned: 7 }))
      .mockResolvedValueOnce(jobOf({ status: 'succeeded', stage: 'done', terminal: true, response: ANSWER }));
    const seen: string[] = [];

    const result = await pollGenieJob(IDS, 'question?', 'job-1', {
      deadline: Date.now() + JOB_RESUME_WINDOW_MS,
      sleep: noSleep,
      onJob: (job) => seen.push(job.stage),
    });

    expect(result).toEqual(ANSWER);
    expect(seen).toEqual(['researching', 'done']);
    expect(status).toHaveBeenCalledWith({ ...IDS, question: 'question?' }, 'job-1', undefined);
  });

  it.each(['failed', 'expired'] as const)('ends a %s job with the server hint', async (outcome) => {
    vi.spyOn(genieJobsApi, 'genieJobStatus').mockResolvedValue(
      jobOf({ status: outcome, stage: outcome, terminal: true, failed: true, error_hint: 'Ask it again.' }),
    );

    await expect(
      pollGenieJob(IDS, 'question?', 'job-1', { deadline: Date.now() + JOB_RESUME_WINDOW_MS, sleep: noSleep }),
    ).rejects.toMatchObject({ name: 'GenieLiveError', hint: 'Ask it again.' });
  });

  it.each([400, 403, 404])('fails at once on a %i', async (code) => {
    const status = vi
      .spyOn(genieJobsApi, 'genieJobStatus')
      .mockRejectedValue(new ApiError('refused', { path: '/api/genie/message/status', status: code }));

    await expect(
      pollGenieJob(IDS, 'question?', 'job-1', { deadline: Date.now() + JOB_RESUME_WINDOW_MS, sleep: noSleep }),
    ).rejects.toMatchObject({ status: code });
    expect(status).toHaveBeenCalledTimes(1);
  });

  it('tolerates four consecutive transient failures, not five', async () => {
    const transient = new ApiError('busy', { path: '/api/genie/message/status', status: 503 });
    const status = vi
      .spyOn(genieJobsApi, 'genieJobStatus')
      .mockRejectedValueOnce(transient)
      .mockRejectedValueOnce(transient)
      .mockRejectedValueOnce(transient)
      .mockResolvedValueOnce(jobOf({ status: 'succeeded', stage: 'done', terminal: true, response: ANSWER }));

    await expect(
      pollGenieJob(IDS, 'question?', 'job-1', { deadline: Date.now() + JOB_RESUME_WINDOW_MS, sleep: noSleep }),
    ).resolves.toEqual(ANSWER);
    expect(status).toHaveBeenCalledTimes(4);

    status.mockReset();
    status.mockRejectedValue(transient);
    await expect(
      pollGenieJob(IDS, 'question?', 'job-1', { deadline: Date.now() + JOB_RESUME_WINDOW_MS, sleep: noSleep }),
    ).rejects.toBe(transient);
    expect(status).toHaveBeenCalledTimes(4);
  });
});

describe('askGenieLive with completion jobs (genie-01)', () => {
  it('asks for a job when submit advertises one and returns the polled answer', async () => {
    const submitted: GenieSubmitResultWithJobs = {
      completed: false,
      conversation_id: 'conv-1',
      message_id: 'msg-1',
      progress_token: 'tok',
      completion_jobs: true,
    };
    vi.spyOn(api, 'genieSubmit').mockResolvedValue(submitted);
    vi.spyOn(api, 'genieProgress').mockResolvedValue(
      progressOf({ status: 'COMPLETED', stage: 'complete', terminal: true }),
    );
    const complete = vi.spyOn(api, 'genieComplete');
    vi.spyOn(genieJobsApi, 'genieCompleteAsync').mockResolvedValue(jobOf({ status: 'queued', stage: 'queued' }));
    vi.spyOn(genieJobsApi, 'genieJobStatus').mockResolvedValue(
      jobOf({ status: 'succeeded', stage: 'done', terminal: true, response: ANSWER }),
    );

    await expect(askGenieLive('question?', null, { sleep: noSleep })).resolves.toEqual(ANSWER);
    expect(complete).not.toHaveBeenCalled();
  });
});

