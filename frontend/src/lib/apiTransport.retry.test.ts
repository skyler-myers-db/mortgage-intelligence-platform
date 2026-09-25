import { afterEach, describe, expect, it, vi } from 'vitest';
import { api, ApiError } from './api';
import { IDEMPOTENT_UNKEYED_POSTS, INNER_RETRY_AFTER_CAP_MS, getJson, postJson } from './apiTransport';
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

describe('an idempotent, unkeyed POST read on IDEMPOTENT_UNKEYED_POSTS (default policy)', () => {
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

/**
 * Audit delivery-v2: the inner re-send is reason-aware and key-aware. Only a
 * short `warming_up` (or unclassified) 503 is ridden out here; every other
 * transient reason is the outer plan's (lib/retryPlan.ts). An unkeyed write
 * defaults to 'rejected-only'; a keyed one re-sends its very same request_id.
 */
describe('the reason-aware inner re-send (GET, default policy)', () => {
  const LEADS = '/api/v1/leads';

  it('re-sends a warming_up 503 once the blip passes', async () => {
    const paths = stubFetch([reply(503, { ...WARMING_503, dependency: 'warehouse' }), reply(200, [])]);
    await expect(getJson('/api/leads')).resolves.toEqual([]);
    expect(paths).toEqual([LEADS, LEADS]);
  });

  it.each([
    ['breaker_open', { detail: 'x', retryable: true, dependency: 'warehouse', reason: 'breaker_open' }],
    ['retries_exhausted', { detail: 'x', retryable: true, dependency: 'warehouse', reason: 'retries_exhausted' }],
    ['dependency_saturated', { detail: 'x', retryable: true, dependency: 'warehouse', reason: 'dependency_saturated' }],
    ['an unknown reason', { detail: 'x', retryable: true, dependency: 'warehouse', reason: 'something_new' }],
    ['permission_denied (retryable: false)', { detail: 'x', retryable: false, dependency: 'warehouse', reason: 'permission_denied' }],
  ])('sends a 503 %s exactly once and surfaces it', async (_label, body) => {
    const paths = stubFetch([reply(503, body), reply(200, [])]);
    const error = await getJson('/api/leads').catch((err: unknown) => err);
    expect((error as ApiError).status).toBe(503);
    expect(paths).toEqual([LEADS]);
  });

  it('surfaces a 429 whose Retry-After exceeds the cap at once, carrying the wait', async () => {
    const paths = stubFetch([reply(429, RATE_429, { 'Retry-After': '12' }), reply(200, [])]);
    const error = await getJson('/api/leads').catch((err: unknown) => err);
    expect((error as ApiError).status).toBe(429);
    expect((error as ApiError).retryAfterMs).toBe(12_000);
    expect(paths).toEqual([LEADS]);
  });

  it('sleeps through a 429 whose Retry-After is within the cap (1.5 s), then re-sends', async () => {
    vi.useFakeTimers();
    try {
      const paths = stubFetch([reply(429, RATE_429, { 'Retry-After': '1.5' }), reply(200, [])]);
      const pending = getJson('/api/leads');
      await vi.advanceTimersByTimeAsync(1_499);
      expect(paths).toHaveLength(1);
      await vi.advanceTimersByTimeAsync(1);
      await expect(pending).resolves.toEqual([]);
      expect(paths).toEqual([LEADS, LEADS]);
    } finally {
      vi.useRealTimers();
    }
  });

  it('caps the inner wait at 2 s', () => {
    expect(INNER_RETRY_AFTER_CAP_MS).toBe(2_000);
  });
});

describe('the method policy (unkeyed writes are rejected-only)', () => {
  it('sends an unkeyed POST /offers/recommend once after a warming 503', async () => {
    const paths = stubFetch([reply(503, WARMING_503), reply(200, {})]);
    const error = await api.recommendOffer('B-0123456789ABC').catch((err: unknown) => err);
    expect((error as ApiError).status).toBe(503);
    expect(paths).toEqual(['/api/v1/offers/recommend']);
  });

  it('sends a workspace PUT once after a warming 503', async () => {
    const paths = stubFetch([reply(503, WARMING_503), reply(200, {})]);
    const lead = { borrower_id: 'B-0123456789ABC' } as Parameters<typeof api.saveWorkspaceLead>[0];
    const error = await api.saveWorkspaceLead(lead).catch((err: unknown) => err);
    expect((error as ApiError).status).toBe(503);
    expect(paths).toEqual(['/api/v1/workspace/leads/B-0123456789ABC']);
  });

  it('sends a workspace DELETE once after a warming 503', async () => {
    const paths = stubFetch([reply(503, WARMING_503), reply(200, {})]);
    const error = await api.deleteWorkspaceLead('B-0123456789ABC').catch((err: unknown) => err);
    expect((error as ApiError).status).toBe(503);
    expect(paths).toEqual(['/api/v1/workspace/leads/B-0123456789ABC']);
  });

  it('still re-sends an unkeyed write after a pre-handler 429', async () => {
    const paths = stubFetch([reply(429, RATE_429), reply(200, {})]);
    await api.recommendOffer('B-0123456789ABC');
    expect(paths).toHaveLength(2);
  });

  it('re-sends a keyed approve after a warming 503 with the identical request_id', async () => {
    const bodies: string[] = [];
    const responses = [reply(503, WARMING_503), reply(200, { audit_event_id: 'a-1' })];
    vi.stubGlobal('fetch', async (_path: string, init: RequestInit) => {
      bodies.push(String(init.body));
      return responses.shift() as Response;
    });
    await api.approve('B-0123456789ABC', { request_id: 'req-approve-1' });
    expect(bodies).toHaveLength(2);
    expect(bodies[1]).toBe(bodies[0]);
    expect(JSON.parse(bodies[0]).request_id).toBe('req-approve-1');
  });

  it('re-sends a POST carrying an Idempotency-Key after a warming 503', async () => {
    const paths = stubFetch([reply(503, WARMING_503), reply(200, { ok: true })]);
    await postJson('/api/genie/actions', {}, undefined, { 'Idempotency-Key': 'key-1' });
    expect(paths).toHaveLength(2);
  });

  it('re-sends an unkeyed POST whose caller asks for the default policy', async () => {
    const paths = stubFetch([reply(503, WARMING_503), reply(200, { ok: true })]);
    await postJson('/api/genie/message/cancel', {}, undefined, undefined, { retry: 'default' });
    expect(paths).toHaveLength(2);
  });

  it('re-sends POST /api/portfolio/preview (an audit-free read) after a warming 503', async () => {
    const paths = stubFetch([reply(503, { ...WARMING_503, dependency: 'warehouse' }), reply(200, { ok: true })]);
    await postJson('/api/portfolio/preview', { criteria: {} });
    expect(paths).toEqual(['/api/v1/portfolio/preview', '/api/v1/portfolio/preview']);
  });

  it('keeps exactly the reviewed idempotent unkeyed POSTs on the default policy', () => {
    expect([...IDEMPOTENT_UNKEYED_POSTS].sort()).toEqual([
      '/api/genie/message/complete',
      '/api/genie/message/progress',
      '/api/genie/message/status',
      '/api/genie/start',
      '/api/portfolio/campaign-recommendation',
      '/api/portfolio/preview',
    ]);
  });
});
