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

describe('genieFeedback API client', () => {
  it('POSTs the frozen feedback body as JSON to the versioned path', async () => {
    const calls: Array<{ path: string; init?: RequestInit }> = [];
    vi.stubGlobal('fetch', async (path: string, init?: RequestInit) => {
      calls.push({ path, init });
      return jsonResponse(200, { accepted: true, audit_event_id: 'evt-1' });
    });

    const res = await api.genieFeedback({
      conversation_id: 'conv-1',
      message_id: 'msg-1',
      helpful: true,
      request_id: '11111111-1111-4111-8111-111111111111',
    });

    expect(res.accepted).toBe(true);
    expect(res.audit_event_id).toBe('evt-1');
    expect(calls[0].path).toBe('/api/v1/genie/feedback');
    expect(calls[0].init?.method).toBe('POST');
    expect((calls[0].init?.headers as Record<string, string>)['Content-Type']).toBe(
      'application/json',
    );
    expect((calls[0].init?.headers as Record<string, string>)['Idempotency-Key']).toBe(
      '11111111-1111-4111-8111-111111111111',
    );
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({
      conversation_id: 'conv-1',
      message_id: 'msg-1',
      helpful: true,
    });
  });

  it('raises an ApiError on a 415 wrong-content-type response', async () => {
    vi.stubGlobal('fetch', async () =>
      jsonResponse(415, { detail: 'Unsupported Media Type' }),
    );

    const err = await api
      .genieFeedback({ conversation_id: 'conv-1', message_id: 'msg-1', helpful: true })
      .catch((e) => e);

    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(415);
  });
});
