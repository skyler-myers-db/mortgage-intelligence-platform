import { afterEach, describe, expect, it, vi } from 'vitest';
import { api, ApiError, _parseRetryableBody } from './api';

/**
 * _parseRetryableBody — cycle-13 `reason` field extraction.
 *
 * The backend's `_dependency_down_handler` ships a 503 body of the
 * shape {detail, retryable, dependency, correlation_id, reason} where
 * `reason ∈ {warming_up, breaker_open, retries_exhausted}`. The
 * frontend retry hook branches on that field to pick the right
 * cadence (see useWarmingUpRetry.test.ts). These tests pin the parse
 * so a drift in the backend body shape or a regression in the parser
 * is caught before it reaches the hook.
 */

/** Tiny `Response` builder — avoids spinning up a real fetch. */
function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('_parseRetryableBody', () => {
  it('extracts reason="breaker_open" from a 503 body', async () => {
    const res = jsonResponse(503, {
      detail: 'warehouse is temporarily unavailable',
      retryable: true,
      dependency: 'warehouse',
      reason: 'breaker_open',
      correlation_id: 'abc-123',
    });
    const parsed = await _parseRetryableBody(res);
    expect(parsed.retryable).toBe(true);
    expect(parsed.dependency).toBe('warehouse');
    expect(parsed.reason).toBe('breaker_open');
    expect(parsed.correlationId).toBe('abc-123');
  });

  it('extracts reason="warming_up" from a 503 body', async () => {
    const res = jsonResponse(503, {
      detail: 'warehouse warming up',
      retryable: true,
      dependency: 'warehouse',
      reason: 'warming_up',
    });
    const parsed = await _parseRetryableBody(res);
    expect(parsed.reason).toBe('warming_up');
  });

  it('extracts reason="retries_exhausted" from a 503 body', async () => {
    const res = jsonResponse(503, {
      detail: 'lakebase retries exhausted',
      retryable: true,
      dependency: 'lakebase',
      reason: 'retries_exhausted',
    });
    const parsed = await _parseRetryableBody(res);
    expect(parsed.reason).toBe('retries_exhausted');
  });

  it('returns reason: null when the body omits the field (older backend)', async () => {
    const res = jsonResponse(503, {
      detail: 'warehouse down',
      retryable: true,
      dependency: 'warehouse',
    });
    const parsed = await _parseRetryableBody(res);
    expect(parsed.retryable).toBe(true);
    expect(parsed.reason).toBeNull();
  });

  it('returns a fully-null parse for non-503 responses', async () => {
    const res = jsonResponse(500, { detail: 'internal' });
    const parsed = await _parseRetryableBody(res);
    expect(parsed.retryable).toBe(false);
    expect(parsed.reason).toBeNull();
    expect(parsed.dependency).toBeNull();
  });

  it('tolerates a non-JSON 503 body without throwing', async () => {
    const res = new Response('upstream timeout', { status: 503 });
    const parsed = await _parseRetryableBody(res);
    expect(parsed.retryable).toBe(false);
    expect(parsed.reason).toBeNull();
  });
});

describe('ApiError', () => {
  it('carries the reason field through to callers', () => {
    const err = new ApiError('warehouse cooling', {
      path: '/api/segments',
      status: 503,
      retryable: true,
      dependency: 'warehouse',
      reason: 'breaker_open',
      correlationId: 'abc',
    });
    expect(err.reason).toBe('breaker_open');
    expect(err.retryable).toBe(true);
    expect(err.dependency).toBe('warehouse');
  });

  it('defaults reason to null when the caller omits it', () => {
    const err = new ApiError('boom', { path: '/api/leads', status: 500 });
    expect(err.reason).toBeNull();
  });
});

describe('workspace API client', () => {
  it('saves leads with PUT and deletes with DELETE', async () => {
    const calls: Array<{ path: string; init?: RequestInit }> = [];
    vi.stubGlobal('fetch', async (path: string, init?: RequestInit) => {
      calls.push({ path, init });
      if (init?.method === 'DELETE') {
        return jsonResponse(200, { ok: true, borrower_id: 'B-123' });
      }
      return jsonResponse(200, {
        borrower_id: 'B-123',
        city: 'Seattle',
        state: 'WA',
        zip: '98118',
        recommended_offer: 'Refinance + HELOC',
        opportunity_score: 86,
        confidence: 81,
        saved_at: '2026-05-05T00:00:00Z',
        updated_at: '2026-05-05T00:00:00Z',
      });
    });

    await api.saveWorkspaceLead({
      borrower_id: 'B-123',
      city: 'Seattle',
      state: 'WA',
      zip: '98118',
      recommended_offer: 'Refinance + HELOC',
      opportunity_score: 86,
      confidence: 81,
    });
    await api.deleteWorkspaceLead('B-123');

    expect(calls[0].path).toBe('/api/workspace/leads/B-123');
    expect(calls[0].init?.method).toBe('PUT');
    expect(JSON.parse(String(calls[0].init?.body))).toMatchObject({
      borrower_id: 'B-123',
    });
    expect(calls[1].path).toBe('/api/workspace/leads/B-123');
    expect(calls[1].init?.method).toBe('DELETE');
  });
});

describe('genie API client', () => {
  it('starts or resumes a governed Genie session', async () => {
    const calls: Array<{ path: string; init?: RequestInit }> = [];
    vi.stubGlobal('fetch', async (path: string, init?: RequestInit) => {
      calls.push({ path, init });
      return jsonResponse(200, {
        conversation_id: 'conv-123',
        trusted_assets: ['mip.gold.lead_population'],
      });
    });

    const result = await api.genieStart();

    expect(result.conversation_id).toBe('conv-123');
    expect(calls[0].path).toBe('/api/genie/start');
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({ context: {} });
  });

  it('passes conversation_id on follow-up questions', async () => {
    const calls: Array<{ path: string; init?: RequestInit }> = [];
    vi.stubGlobal('fetch', async (path: string, init?: RequestInit) => {
      calls.push({ path, init });
      return jsonResponse(200, {
        answer: 'Follow-up answer',
        source: 'genie',
        trusted_assets: ['mip.gold.lead_population'],
        conversation_id: 'conv-123',
      });
    });

    await api.genie('show that by ZIP', 'conv-123');

    expect(calls[0].path).toBe('/api/genie/message');
    expect(JSON.parse(String(calls[0].init?.body))).toMatchObject({
      question: 'show that by ZIP',
      conversation_id: 'conv-123',
    });
  });

  it('runs governed Genie actions with confirmation payload fields', async () => {
    const calls: Array<{ path: string; init?: RequestInit }> = [];
    vi.stubGlobal('fetch', async (path: string, init?: RequestInit) => {
      calls.push({ path, init });
      return jsonResponse(200, {
        ok: true,
        action_type: 'save_borrowers',
        saved_count: 1,
        message: 'Saved 1 borrower.',
      });
    });

    await api.genieAction({
      id: 'save-borrowers',
      label: 'Save borrowers',
      action_type: 'save_borrowers',
      description: 'Save returned borrowers',
      borrower_ids: ['B-123'],
      criteria: { source: 'genie', row_count: 1 },
      request_id: 'genie-action-server-issued',
      confirmation_token: 'token-123',
      conversation_id: 'conv-123',
      message_id: 'msg-456',
      question_hash: 'abc',
    });

    expect(calls[0].path).toBe('/api/genie/actions');
    const body = JSON.parse(String(calls[0].init?.body));
    expect(body).toMatchObject({
      action_type: 'save_borrowers',
      conversation_id: 'conv-123',
      message_id: 'msg-456',
      question_hash: 'abc',
      borrower_ids: ['B-123'],
      request_id: 'genie-action-server-issued',
      confirmed: true,
      confirmation_token: 'token-123',
    });
  });
});
