import { afterEach, describe, expect, it, vi } from 'vitest';
import { api, ApiError } from './api';
import { postJson } from './apiTransport';
import { requestGenieCompletion } from './genieAsk';

/**
 * The transport's transient retry never re-sends a non-idempotent POST that
 * may already have acted (w2-genie-turn deferred #3): the Genie submit creates
 * a Genie message and carries no Idempotency-Key, so it is re-sent only after
 * a 429 the backpressure middleware returned BEFORE the handler ran. Every
 * other caller keeps the default policy. Driven through the real `api` client
 * and `postJson`, with `fetch` stubbed.
 */

const SUBMIT = '/api/v1/genie/message/submit';
const LIVE_SUBMIT = { completed: false, conversation_id: 'conv-1', message_id: 'msg-1', progress_token: 'tok-1' };

function reply(status: number, body: unknown, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json', 'Retry-After': '0', ...headers },
  });
}

const WARMING_503 = { detail: 'Genie is warming up', retryable: true, dependency: 'genie', reason: 'warming_up' };
const RETRIES_503 = { detail: 'Genie retries exhausted', retryable: true, dependency: 'genie', reason: 'retries_exhausted' };
const RATE_429 = { detail: 'Request budget exceeded', retryable: true, dependency: 'genie', reason: 'rate_limited' };
const SATURATED_429 = { detail: 'Request budget exceeded', retryable: true, dependency: 'genie', reason: 'dependency_saturated' };

function stubFetch(responses: Response[]): string[] {
  const paths: string[] = [];
  vi.stubGlobal('fetch', async (path: string) => {
    paths.push(path);
    const next = responses.shift();
    if (!next) throw new Error('unexpected extra request');
    return next;
  });
  return paths;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('the Genie submit (rejected-only)', () => {
  it.each([
    ['warming_up', WARMING_503],
    ['retries_exhausted', RETRIES_503],
  ])('is never re-sent after a retryable 503 (%s): one POST, one error', async (_reason, body) => {
    const paths = stubFetch([reply(503, body), reply(200, LIVE_SUBMIT)]);

    const error = await api.genieSubmit('Which states lead?', null).catch((err: unknown) => err);

    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(503);
    expect(paths).toEqual([SUBMIT]);
  });

  it.each([
    ['rate_limited', RATE_429],
    ['dependency_saturated', SATURATED_429],
  ])('is re-sent after a 429 the middleware returned before the handler ran (%s)', async (_reason, body) => {
    const paths = stubFetch([reply(429, body), reply(200, LIVE_SUBMIT)]);

    const result = await api.genieSubmit('Which states lead?', null);

    expect(result.message_id).toBe('msg-1');
    expect(paths).toEqual([SUBMIT, SUBMIT]);
  });

  it('is never re-sent after a 429 whose reason is not a pre-handler rejection', async () => {
    const paths = stubFetch([reply(429, { ...RATE_429, reason: 'something_else' }), reply(200, LIVE_SUBMIT)]);

    const error = await api.genieSubmit('Which states lead?', null).catch((err: unknown) => err);

    expect((error as ApiError).status).toBe(429);
    expect(paths).toEqual([SUBMIT]);
  });
});

describe('every other POST (default policy, unchanged)', () => {
  it('is re-sent after a retryable 503', async () => {
    const paths = stubFetch([reply(503, WARMING_503), reply(200, { ok: true })]);

    const result = await postJson<{ ok: boolean }, Record<string, never>>('/api/genie/message/progress', {});

    expect(result.ok).toBe(true);
    expect(paths).toEqual(['/api/v1/genie/message/progress', '/api/v1/genie/message/progress']);
  });

  it('is re-sent after a retryable 429', async () => {
    const paths = stubFetch([reply(429, RATE_429), reply(200, { ok: true })]);

    await postJson<{ ok: boolean }, Record<string, never>>('/api/genie/message/progress', {});

    expect(paths).toHaveLength(2);
  });
});

describe('the async Genie complete (default policy)', () => {
  const COMPLETE = '/api/v1/genie/message/complete';
  const TURN = { conversationId: 'conv-1', messageId: 'msg-1', progressToken: 'tok-1' };
  // What the server answers when it cannot give the turn a job: a plain 503
  // with no retryable flag. A job-less run could not be joined, so a re-send
  // would run the governed tail, and its audit row, twice.
  const REFUSED_503 = { detail: 'lakebase is temporarily unavailable' };

  it('is sent exactly once when the server refuses it for want of a job', async () => {
    const paths = stubFetch([reply(503, REFUSED_503), reply(202, { kind: 'genie_completion_job' })]);

    const error = await requestGenieCompletion(TURN, 'Which states lead?', { asyncComplete: true }).catch(
      (err: unknown) => err,
    );

    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(503);
    expect((error as ApiError).retryable).toBe(false);
    expect(paths).toEqual([COMPLETE]);
  });
});
