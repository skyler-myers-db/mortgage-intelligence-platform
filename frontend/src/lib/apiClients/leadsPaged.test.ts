import { afterEach, describe, expect, it, vi } from 'vitest';

/**
 * The paged /api/leads fetcher (W5c, D-audit-reads-a, F1): page 0 has no
 * cursor, a later page resends the identical request parameters plus the
 * cursor, a server sort rides as sort/sort_dir (never for rank), a limit
 * never rides, and the paging headers are read fail-closed.
 */

const transport = vi.hoisted(() => ({
  calls: [] as string[],
  headers: new Headers(),
  verify: vi.fn(),
}));

vi.mock('../apiTransport', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../apiTransport')>()),
  getJsonWithHeaders: (path: string) => {
    transport.calls.push(path);
    return Promise.resolve({ data: [{ borrower_id: 'B-0123456789ABC' }], headers: transport.headers });
  },
  _verifyGrowthAgentCohort: transport.verify,
}));

const { fetchLeadsPage } = await import('./leadsPaged');

const VIEW = '0123456789abcdef0123456789abcdef';
const CURSOR = 'eyJ2IjoxfQ.c2ln';
const RANK = { sort: 'rank', dir: 'desc' } as const;
const PROOF = {
  runId: '11111111-1111-4111-8111-111111111111', actionableTotal: 3, cohortFingerprint: 'a'.repeat(64), growthHandoff: 'h',
} as never;

function headers(values: Record<string, string>): Headers {
  return new Headers(values);
}

afterEach(() => {
  transport.calls = [];
  transport.headers = new Headers();
  transport.verify.mockReset();
});

describe('fetchLeadsPage', () => {
  it('reads page 0 with the request parameters only, never a limit or a rank sort', async () => {
    await fetchLeadsPage({ geo: { states: ['IL'] }, opts: { limit: 2000, growthAgentProof: null } }, RANK, null);
    expect(transport.calls).toEqual(['/api/leads?states=IL']);
  });

  it('sends a server sort, and a later page resends the same parameters plus the cursor', async () => {
    const request = { segment: 'itm', geo: { zip: '60617' }, opts: { growthAgentProof: null } };
    const equity = { sort: 'equity', dir: 'asc' } as const;
    await fetchLeadsPage(request, equity, null);
    await fetchLeadsPage(request, equity, CURSOR);
    const [first, next] = transport.calls.map((path) => new URL(path, 'http://app.test').searchParams);
    expect(Object.fromEntries(first)).toEqual({ segment: 'itm', zip: '60617', sort: 'equity', sort_dir: 'asc' });
    next.delete('cursor');
    expect(next.toString()).toBe(first.toString());
    expect(new URL(transport.calls[1], 'http://app.test').searchParams.get('cursor')).toBe(CURSOR);
  });

  it('reads the paging headers, and drops a malformed view id or cursor', async () => {
    transport.headers = headers({
      'X-Total-Matching': '1284', 'X-Ranked-Matching': '1284', 'X-Returned-Rows': '500',
      'X-Lead-View-Id': VIEW, 'X-Page-Index': '0', 'X-Next-Cursor': CURSOR,
    });
    const page = await fetchLeadsPage({ opts: { growthAgentProof: null } }, RANK, null);
    expect(page).toMatchObject({
      totalMatching: 1284, returnedRows: 500, viewId: VIEW, pageIndex: 0, nextCursor: CURSOR, paging: 'ok',
    });

    transport.headers = headers({
      'X-Lead-View-Id': 'not-a-view', 'X-Next-Cursor': 'has spaces.and=junk', 'X-Lead-Paging': 'unavailable',
    });
    const unpaged = await fetchLeadsPage({ opts: { growthAgentProof: null } }, RANK, null);
    expect(unpaged).toMatchObject({ viewId: null, nextCursor: null, paging: 'unavailable', pageIndex: 0 });
  });

  it('verifies a Growth Agent cohort on page 0 only', async () => {
    transport.verify.mockResolvedValue({ status: 'verified' });
    const request = { opts: { growthAgentProof: PROOF } };
    const first = await fetchLeadsPage(request, RANK, null);
    const later = await fetchLeadsPage(request, RANK, CURSOR);
    expect(transport.verify).toHaveBeenCalledTimes(1);
    expect(first.growthAgentVerification).toEqual({ status: 'verified' });
    expect(later.growthAgentVerification).toBeNull();
  });
});
