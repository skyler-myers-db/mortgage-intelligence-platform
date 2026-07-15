import { afterEach, describe, expect, it, vi } from 'vitest';
import { api } from './api';

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function jsonResponseWithHeaders(
  status: number,
  body: unknown,
  headers: Record<string, string>,
): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json', ...headers },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('campaign-bound outreach API client', () => {
  const campaign = {
    campaign_id: '11111111-1111-4111-8111-111111111111',
    variant_name: 'A',
  };

  it('carries the selected campaign variant through draft, approve, and reject', async () => {
    const calls: Array<{ path: string; init?: RequestInit }> = [];
    vi.stubGlobal('fetch', async (path: string, init?: RequestInit) => {
      calls.push({ path, init });
      if (path.endsWith('/outreach/draft')) {
        return jsonResponse(200, { ...campaign, body: 'draft' });
      }
      return jsonResponse(200, path.endsWith('/approve') ? { approved: true } : { rejected: true });
    });

    await api.draftOutreach('B-48291', 'email', undefined, campaign);
    await api.approve('B-48291', { ...campaign, draft_body: 'draft', rationale: 'reviewed' });
    await api.reject('B-48291', { ...campaign, rationale_code: 'low_intent' });

    expect(calls.map((call) => JSON.parse(String(call.init?.body)))).toEqual([
      expect.objectContaining(campaign),
      expect.objectContaining(campaign),
      expect.objectContaining(campaign),
    ]);
  });
});

describe('Growth Agent Lead Queue proof', () => {
  const proof = {
    actionableTotal: 12,
    cohortFingerprint: '8c91a6378bcc3cd62df18369faed832c2016d8343fdd85b9d298978eea7eb40d',
    snapshotId: '2026-07-14 12:00:00',
    toolResultHash: 'a'.repeat(64),
  };

  it('requests atomic identity headers and verifies total, fingerprint, and snapshot', async () => {
    const calls: string[] = [];
    vi.stubGlobal('fetch', async (path: string) => {
      calls.push(path);
      return jsonResponseWithHeaders(200, [], {
        'X-Total-Matching': '12',
        'X-Returned-Rows': '0',
        'X-Cohort-Digest': 'b'.repeat(64),
        'X-Cohort-Snapshot-ID': proof.snapshotId,
      });
    });

    const result = await api.leadsPage(undefined, undefined, undefined, {
      growthAgentProof: proof,
    });

    expect(new URL(calls[0], 'https://mortgage-intelligence.local').searchParams.get(
      'include_identity_proof',
    )).toBe('true');
    expect(result.growthAgentVerification).toEqual({
      status: 'verified',
      total: 12,
      cohortFingerprint: proof.cohortFingerprint,
      snapshotId: proof.snapshotId,
    });
  });

  it('fails closed with compact stale guidance when the destination count changes', async () => {
    vi.stubGlobal('fetch', async () => jsonResponseWithHeaders(200, [], {
      'X-Total-Matching': '13',
      'X-Returned-Rows': '0',
      'X-Cohort-Digest': 'b'.repeat(64),
      'X-Cohort-Snapshot-ID': proof.snapshotId,
    }));

    await expect(api.leadsPage(undefined, undefined, undefined, {
      growthAgentProof: proof,
    })).rejects.toThrow('Growth Agent cohort is stale');
  });

  it('fails closed with the same stale guidance when identity headers are incomplete', async () => {
    vi.stubGlobal('fetch', async () => jsonResponseWithHeaders(200, [], {
      'X-Total-Matching': '12',
      'X-Returned-Rows': '0',
    }));

    await expect(api.leadsPage(undefined, undefined, undefined, {
      growthAgentProof: proof,
    })).rejects.toThrow('Growth Agent cohort is stale');
  });

  it('rejects a handoff URL that omits the signed actionable total', async () => {
    vi.stubGlobal('window', {
      location: {
        search: `?actionable_cohort_fingerprint=${proof.cohortFingerprint}`
          + `&actionable_snapshot_id=${encodeURIComponent(proof.snapshotId)}`
          + `&tool_result_hash=${proof.toolResultHash}`,
      },
    });

    expect(() => api.leadsPage()).toThrow('Growth Agent cohort proof is incomplete');
  });
});
