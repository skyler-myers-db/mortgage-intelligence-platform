/**
 * The server half of Stop (audit 2026-09-21 `genie-03`): the cancel POST's
 * exact body (ids, job id and the 16-hex label; never the question), the
 * explicit default retry, and a client that never throws and only reports an
 * outcome it knows for the job it asked about.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { GenieCancelResult } from '../types/genieJobs';
import { ApiError } from './api';
import { genieJobsApi } from './apiClients/genieJobs';
import { requestGenieTurnCancel, type GenieTurnCancelTarget } from './genieTurnCancel';
import * as transport from './apiTransport';

const JOB_ID = '0a1b2c3d-0000-4000-8000-000000000001';
const TARGET: GenieTurnCancelTarget = {
  ids: { conversationId: 'conv-1', messageId: 'msg-1', progressToken: 'tok-1' },
  jobId: JOB_ID,
  questionHash: '0123456789abcdef',
};

function result(partial: Partial<GenieCancelResult> = {}): GenieCancelResult {
  return { kind: 'genie_completion_cancel', job_id: JOB_ID, outcome: 'cancelled', status: 'running', ...partial };
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe('genieJobsApi.genieCancel', () => {
  it('POSTs exactly the five fields, never the question, with the default retry', async () => {
    const post = vi.spyOn(transport, 'postJson').mockResolvedValue(result());

    await genieJobsApi.genieCancel(TARGET.ids, JOB_ID, TARGET.questionHash);

    expect(post).toHaveBeenCalledTimes(1);
    const [path, body, signal, headers, options] = post.mock.calls[0];
    expect(path).toBe('/api/genie/message/cancel');
    expect(body).toEqual({
      conversation_id: 'conv-1',
      message_id: 'msg-1',
      progress_token: 'tok-1',
      job_id: JOB_ID,
      question_hash: '0123456789abcdef',
    });
    expect(Object.keys(body as object)).not.toContain('question');
    expect(signal).toBeUndefined();
    expect(headers).toBeUndefined();
    expect(options).toEqual({ retry: 'default' });
  });
});

describe('requestGenieTurnCancel', () => {
  it.each(['cancelled', 'recorded', 'ended'] as const)('reports the server outcome %s', async (outcome) => {
    vi.spyOn(genieJobsApi, 'genieCancel').mockResolvedValue(result({ outcome }));

    await expect(requestGenieTurnCancel(TARGET)).resolves.toBe(outcome);
  });

  it('never throws: a failed request is null', async () => {
    vi.spyOn(genieJobsApi, 'genieCancel').mockRejectedValue(
      new ApiError('unavailable', { path: '/api/genie/message/cancel', status: 503 }),
    );

    await expect(requestGenieTurnCancel(TARGET)).resolves.toBeNull();
  });

  it('an unknown outcome, another job or another shape is null', async () => {
    const cancel = vi.spyOn(genieJobsApi, 'genieCancel');
    cancel.mockResolvedValueOnce({ ...result(), outcome: 'aborted' } as unknown as GenieCancelResult);
    cancel.mockResolvedValueOnce(result({ job_id: '0a1b2c3d-0000-4000-8000-000000000002' }));
    cancel.mockResolvedValueOnce({ ...result(), kind: 'genie_completion_job' } as unknown as GenieCancelResult);
    cancel.mockResolvedValueOnce(null as unknown as GenieCancelResult);

    const outcomes = [];
    for (let call = 0; call < 4; call += 1) outcomes.push(await requestGenieTurnCancel(TARGET));

    expect(outcomes).toEqual([null, null, null, null]);
  });
});
