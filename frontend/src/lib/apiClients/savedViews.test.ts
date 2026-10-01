import { afterEach, describe, expect, it, vi } from 'vitest';
import { createSavedView, deleteSavedView, fetchSavedViews } from './savedViews';

afterEach(() => {
  vi.unstubAllGlobals();
});

function stub(status = 200, body: unknown = {}) {
  const calls: Array<{ path: string; init?: RequestInit }> = [];
  vi.stubGlobal('fetch', async (path: string, init?: RequestInit) => {
    calls.push({ path, init });
    return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
  });
  return calls;
}

describe('saved views client', () => {
  it('lists, saves and deletes under /workspace/saved-views', async () => {
    const calls = stub(200, { saved_views: [] });
    await fetchSavedViews();
    await createSavedView({ name: 'TX refi', params: 'state=TX' });
    await deleteSavedView('11111111-1111-4111-8111-111111111111');
    expect(calls.map((call) => `${call.init?.method ?? 'GET'} ${call.path}`)).toEqual([
      'GET /api/v1/workspace/saved-views',
      'POST /api/v1/workspace/saved-views',
      'DELETE /api/v1/workspace/saved-views/11111111-1111-4111-8111-111111111111',
    ]);
    expect(JSON.parse(String(calls[1].init?.body))).toEqual({ name: 'TX refi', params: 'state=TX' });
  });

  it('never re-sends a save the server may have written (a 503 surfaces once)', async () => {
    const calls = stub(503, { detail: 'Lakebase temporarily unavailable', retryable: true, dependency: 'lakebase' });
    await expect(createSavedView({ name: 'TX refi', params: 'state=TX' })).rejects.toMatchObject({ status: 503 });
    expect(calls).toHaveLength(1);
  });
});
