import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError, api } from './api';

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

const HASH = 'b'.repeat(64);

describe('genieRefusalReport API client', () => {
  it('POSTs the hash-only body as JSON to the versioned path', async () => {
    const calls: Array<{ path: string; init?: RequestInit }> = [];
    vi.stubGlobal('fetch', async (path: string, init?: RequestInit) => {
      calls.push({ path, init });
      return jsonResponse(200, { accepted: true, duplicate: false, report_id: 'r-1', audit_event_id: 'evt-1' });
    });

    const res = await api.genieRefusalReport({
      question_hash: HASH,
      refusal_reason: 'unreviewed_criterion',
      conversation_id: 'conv-1',
    });

    expect(res.accepted).toBe(true);
    expect(res.duplicate).toBe(false);
    expect(calls[0].path).toBe('/api/v1/genie/refusal-report');
    expect(calls[0].init?.method).toBe('POST');
    expect((calls[0].init?.headers as Record<string, string>)['Content-Type']).toBe('application/json');
    const body = JSON.parse(String(calls[0].init?.body)) as Record<string, unknown>;
    expect(body).toEqual({
      question_hash: HASH,
      refusal_reason: 'unreviewed_criterion',
      conversation_id: 'conv-1',
      message_id: null,
    });
    // Hash-only invariant: the body has exactly these keys and no text field.
    expect(Object.keys(body).sort()).toEqual(['conversation_id', 'message_id', 'question_hash', 'refusal_reason']);
  });

  it('raises an ApiError on a 422 rejection', async () => {
    vi.stubGlobal('fetch', async () => jsonResponse(422, { detail: 'question_hash must be the 64-hex refusal_report_hash of the refused turn' }));
    await expect(
      api.genieRefusalReport({ question_hash: 'not-a-hash', refusal_reason: 'unknown' }),
    ).rejects.toBeInstanceOf(ApiError);
  });
});
