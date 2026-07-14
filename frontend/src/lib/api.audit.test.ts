import { afterEach, describe, expect, it, vi } from 'vitest';
import { api } from './api';

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('audit API client', () => {
  it('preserves legacy offset paging for existing audit consumers', async () => {
    const calls: Array<{ path: string; init?: RequestInit }> = [];
    vi.stubGlobal('fetch', async (path: string, init?: RequestInit) => {
      calls.push({ path, init });
      return jsonResponse(200, []);
    });

    await api.auditEvents(25, undefined, { offset: 50 });

    const url = new URL(calls[0].path, 'http://localhost');
    expect(url.pathname).toBe('/api/v1/audit/events');
    expect(url.searchParams.get('limit')).toBe('25');
    expect(url.searchParams.get('offset')).toBe('50');
  });

  it('forwards snapshot cursor paging and normalized filters', async () => {
    const calls: Array<{ path: string; init?: RequestInit }> = [];
    vi.stubGlobal('fetch', async (path: string, init?: RequestInit) => {
      calls.push({ path, init });
      return jsonResponse(200, { items: [], next_cursor: null });
    });

    await api.auditEventPage(25, undefined, {
      borrower_id: 'B-ABC123',
      action: 'outreach.approve',
      cursor: 'opaque-cursor',
    });

    const url = new URL(calls[0].path, 'http://localhost');
    expect(url.pathname).toBe('/api/v1/audit/events/page');
    expect(url.searchParams.get('limit')).toBe('25');
    expect(url.searchParams.get('cursor')).toBe('opaque-cursor');
    expect(url.searchParams.get('borrower_id')).toBe('B-ABC123');
    expect(url.searchParams.get('action')).toBe('outreach.approve');
  });
});
