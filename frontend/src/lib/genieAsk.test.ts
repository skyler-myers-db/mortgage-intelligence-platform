/**
 * askGenieLive lifecycle: submit → progress polling → complete, with the
 * deterministic-inline shortcut, failure hint surfacing, and poll-error
 * tolerance pinned.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { api, type GenieLiveProgress } from './api';
import { GenieLiveError, askGenieLive } from './genieAsk';

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
