/**
 * The audit-free count read (W5e items 3b / 4b): the query is an ALLOWLIST,
 * so no borrower list, approval-request scope, cohort, include_* switch,
 * page size, cursor, sort or Growth Agent proof ever reaches
 * GET /api/leads/count, whatever the caller's params hold.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { fetchLeadCount, leadCountQuery } from './leadCount';

const GROWTH_AGENT_PROOF_PARAMS = [
  'growth_agent_run_id', 'actionable_total', 'actionable_cohort_fingerprint',
  'actionable_snapshot_id', 'tool_result_hash', 'growth_handoff',
];
const FORBIDDEN = [
  'borrower_ids', 'approval_request_batch', 'cohort_id', 'include_identity_proof', 'include_contact',
  'limit', 'cursor', 'sort', 'sort_dir', 'dir', 'row', 'view', 'hide', 'mode', 'zip', 'zips', 'county',
  'counties', 'cities', 'assigned_to', ...GROWTH_AGENT_PROOF_PARAMS,
];

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('leadCountQuery', () => {
  it('copies only the grammar and pattern keys, in a fixed order', () => {
    const params = new URLSearchParams('aged_days=7&product=HELOC&states=IL,TX&segment=itm');
    expect(leadCountQuery(params)).toBe('states=IL%2CTX&segment=itm&product=HELOC&aged_days=7');
  });

  it('never carries a borrower list, a request scope, a cohort, a switch, paging, sort or a proof', () => {
    const params = new URLSearchParams('state=TX');
    for (const key of FORBIDDEN) params.set(key, key === 'borrower_ids' ? 'B-0123456789ABC' : '1');
    const query = new URLSearchParams(leadCountQuery(params));
    expect([...query.keys()]).toEqual(['state']);
    for (const key of FORBIDDEN) expect(query.has(key), key).toBe(false);
  });

  it('reads GET /api/leads/count with the allowlisted query only', async () => {
    const seen: string[] = [];
    vi.stubGlobal('fetch', async (path: string) => {
      seen.push(path);
      return new Response(JSON.stringify({ total_matching: 1234 }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    });
    const answer = await fetchLeadCount(new URLSearchParams('product=Refi&borrower_ids=B-0123456789ABC&approval_request_batch=x'));
    expect(answer.total_matching).toBe(1234);
    expect(seen).toHaveLength(1);
    expect(seen[0]).toMatch(/\/api\/(v1\/)?leads\/count\?product=Refi$/);
  });
});
