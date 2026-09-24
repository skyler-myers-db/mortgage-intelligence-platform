/**
 * askGenieLive lifecycle: submit → progress polling → complete, with the
 * deterministic-inline shortcut, failure hint surfacing, and poll-error
 * tolerance pinned.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError, api, type GenieLiveProgress } from './api';
import {
  GenieLiveError,
  MAX_LIVE_WAIT_MS,
  askGenieLive,
  pollGenieTurn,
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

