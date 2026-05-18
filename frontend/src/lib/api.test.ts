import { afterEach, describe, expect, it, vi } from 'vitest';
import { api, ApiError, isWarmingUpError, _parseHttpErrorBody, _parseRetryableBody } from './api';
import { apiPath } from './apiPaths';
import type { SegmentCode } from '../types';

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

function jsonResponseWithHeaders(status: number, body: unknown, headers: Record<string, string>): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json', ...headers },
  });
}

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('apiPath', () => {
  it('centralizes the canonical API version prefix', () => {
    expect(apiPath('/health')).toBe('/api/v1/health');
    expect(apiPath('/api/health')).toBe('/api/v1/health');
    expect(apiPath('/api/v1/health')).toBe('/api/v1/health');
  });
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

  it('extracts retry-after from retryable 429 backpressure responses', async () => {
    const res = jsonResponseWithHeaders(
      429,
      {
        detail: 'rate limit exceeded',
        retryable: true,
        correlation_id: 'corr-429',
      },
      { 'Retry-After': '1.5' },
    );
    const parsed = await _parseRetryableBody(res);
    expect(parsed.retryable).toBe(true);
    expect(parsed.detail).toBe('rate limit exceeded');
    expect(parsed.correlationId).toBe('corr-429');
    expect(parsed.retryAfterMs).toBe(1500);
  });

  it('honors longer retry-after windows from 429 backpressure responses', async () => {
    const res = jsonResponseWithHeaders(
      429,
      {
        detail: 'rate limit exceeded',
        retryable: true,
        correlation_id: 'corr-429-long',
      },
      { 'Retry-After': '60' },
    );
    const parsed = await _parseRetryableBody(res);
    expect(parsed.retryable).toBe(true);
    expect(parsed.retryAfterMs).toBe(60_000);
  });

  it('honors Retry-After timing before retrying a retryable 429', async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn(async () => {
      if (fetchMock.mock.calls.length === 1) {
        return jsonResponseWithHeaders(
          429,
          {
            detail: 'rate limit exceeded',
            retryable: true,
            dependency: 'warehouse',
            reason: 'rate_limited',
            correlation_id: 'corr-429',
          },
          { 'Retry-After': '1.5' },
        );
      }
      return jsonResponse(200, []);
    });
    vi.stubGlobal('fetch', fetchMock);

    const pending = api.segments();
    await Promise.resolve();
    await Promise.resolve();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1499);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);

    await expect(pending).resolves.toEqual([]);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('tolerates a non-JSON 503 body without throwing', async () => {
    const res = new Response('upstream timeout', { status: 503 });
    const parsed = await _parseRetryableBody(res);
    expect(parsed.retryable).toBe(false);
    expect(parsed.reason).toBeNull();
  });
});

describe('_parseHttpErrorBody', () => {
  it('extracts FastAPI validation details into public field messages', async () => {
    const res = jsonResponse(422, {
      detail: [
        {
          loc: ['query', 'aged_days'],
          msg: 'Input should be less than or equal to 90',
        },
      ],
    });

    const parsed = await _parseHttpErrorBody(res);

    expect(parsed.message).toBe('aged_days: Input should be less than or equal to 90');
    expect(parsed.validationIssues).toEqual([
      {
        field: 'aged_days',
        location: ['query', 'aged_days'],
        message: 'Input should be less than or equal to 90',
      },
    ]);
  });

  it('extracts string detail messages for not-found responses', async () => {
    const parsed = await _parseHttpErrorBody(jsonResponse(404, { detail: 'Borrower B-X not found' }));

    expect(parsed.message).toBe('Borrower B-X not found');
    expect(parsed.validationIssues).toEqual([]);
  });
});

describe('ApiError', () => {
  it('carries the reason field through to callers', () => {
    const err = new ApiError('warehouse cooling', {
      path: '/api/v1/segments',
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
    const err = new ApiError('boom', { path: '/api/v1/leads', status: 500 });
    expect(err.reason).toBeNull();
  });

  it('carries validation issues through to route-level error copy', () => {
    const err = new ApiError('aged_days: Input should be less than or equal to 90', {
      path: '/api/v1/leads',
      status: 422,
      validationIssues: [
        {
          field: 'aged_days',
          location: ['query', 'aged_days'],
          message: 'Input should be less than or equal to 90',
        },
      ],
    });
    expect(err.validationIssues[0]?.field).toBe('aged_days');
  });
});

describe('isWarmingUpError', () => {
  it.each([
    ['warming_up', true],
    ['breaker_open', true],
    [null, true],
    ['brand_new_reason', true],
    ['retries_exhausted', false],
  ])('classifies reason=%s as warmingUp=%s', (reason, expected) => {
    const err = new ApiError('dependency unavailable', {
      path: '/api/v1/segments',
      status: 503,
      retryable: true,
      dependency: 'warehouse',
      reason,
    });
    expect(isWarmingUpError(err)).toBe(expected);
  });

  it('rejects aborted, non-503, and non-retryable errors', () => {
    expect(isWarmingUpError(new ApiError('abort', { path: '/api/v1/x', aborted: true }))).toBe(false);
    expect(isWarmingUpError(new ApiError('boom', { path: '/api/v1/x', status: 500 }))).toBe(false);
    expect(isWarmingUpError(new ApiError('no retry', { path: '/api/v1/x', status: 503 }))).toBe(false);
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

    expect(calls[0].path).toBe('/api/v1/workspace/leads/B-123');
    expect(calls[0].init?.method).toBe('PUT');
    expect(JSON.parse(String(calls[0].init?.body))).toMatchObject({
      borrower_id: 'B-123',
    });
    expect(calls[1].path).toBe('/api/v1/workspace/leads/B-123');
    expect(calls[1].init?.method).toBe('DELETE');
  });
});

describe('lead queue API client', () => {
  it('serializes Genie cohort filters into the leads query string', async () => {
    const calls: Array<{ path: string; init?: RequestInit }> = [];
    vi.stubGlobal('fetch', async (path: string, init?: RequestInit) => {
      calls.push({ path, init });
      return jsonResponse(200, []);
    });

    await api.leads(
      undefined,
      undefined,
      {
        states: ['IL', 'TX'],
        zips: ['60617', '75217'],
        borrowerIds: ['B-11111', 'B-22222'],
      },
      {
        segmentCodes: ['itm', 'equity'] as SegmentCode[],
        segmentMode: 'all',
        targetLenderRef: 'Competitor B',
        cohortId: '11111111-1111-1111-1111-111111111111',
        portfolioCriteria: {
          geography: 'All',
          occupancy: 'Owner-occupied',
          lien_status: 'Open 1st lien',
          lender_relationship: 'Current customer',
          product: 'Refi',
          min_equity_pct_label: '≥ 25%',
        },
        limit: 25,
      },
    );

    const url = new URL(calls[0].path, 'http://localhost');
    expect(url.pathname).toBe('/api/v1/leads');
    expect(url.searchParams.get('states')).toBe('IL,TX');
    expect(url.searchParams.get('zips')).toBe('60617,75217');
    expect(url.searchParams.get('borrower_ids')).toBe('B-11111,B-22222');
    expect(url.searchParams.get('segment_codes')).toBe('itm,equity');
    expect(url.searchParams.get('segment_mode')).toBe('all');
    expect(url.searchParams.get('target_lender_ref')).toBe('Competitor B');
    expect(url.searchParams.get('cohort_id')).toBe('11111111-1111-1111-1111-111111111111');
    expect(url.searchParams.get('geography')).toBe('All');
    expect(url.searchParams.get('occupancy')).toBe('Owner-occupied');
    expect(url.searchParams.get('lien_status')).toBe('Open 1st lien');
    expect(url.searchParams.get('lender_relationship')).toBe('Current customer');
    expect(url.searchParams.get('product')).toBe('Refi');
    expect(url.searchParams.get('min_equity_pct_label')).toBe('≥ 25%');
    expect(url.searchParams.get('limit')).toBe('25');
  });

  it('serializes singular state county zip and segment filters', async () => {
    const calls: Array<{ path: string; init?: RequestInit }> = [];
    vi.stubGlobal('fetch', async (path: string, init?: RequestInit) => {
      calls.push({ path, init });
      return jsonResponse(200, []);
    });

    await api.leads(
      undefined,
      undefined,
      {
        state: 'FL',
        county: '12011',
        zip: '33311',
      },
      {
        segmentCodes: ['itm', 'investor', 'equity', 'retention'] as SegmentCode[],
        segmentMode: 'all',
      },
    );

    const url = new URL(calls[0].path, 'http://localhost');
    expect(url.pathname).toBe('/api/v1/leads');
    expect(url.searchParams.get('state')).toBe('FL');
    expect(url.searchParams.get('county')).toBe('12011');
    expect(url.searchParams.get('zip')).toBe('33311');
    expect(url.searchParams.get('segment_codes')).toBe('itm,investor,equity,retention');
    expect(url.searchParams.get('segment_mode')).toBe('all');
  });
});

describe('segment API client', () => {
  it('serializes selected segments and secondary criteria into the segment query string', async () => {
    const calls: Array<{ path: string; init?: RequestInit }> = [];
    vi.stubGlobal('fetch', async (path: string, init?: RequestInit) => {
      calls.push({ path, init });
      return jsonResponse(200, []);
    });

    await api.segments(
      undefined,
      ['itm', 'equity'] as SegmentCode[],
      'all',
      {
        occupancy: 'Owner-occupied',
        lien_status: 'Open HELOC',
        min_equity_pct_label: '≥ 25%',
      },
    );

    const url = new URL(calls[0].path, 'http://localhost');
    expect(url.pathname).toBe('/api/v1/segments');
    expect(url.searchParams.get('segment_codes')).toBe('itm,equity');
    expect(url.searchParams.get('segment_mode')).toBe('all');
    expect(url.searchParams.get('occupancy')).toBe('Owner-occupied');
    expect(url.searchParams.get('lien_status')).toBe('Open HELOC');
    expect(url.searchParams.get('min_equity_pct_label')).toBe('≥ 25%');
  });
});

describe('geo API client', () => {
  it('serializes segment and secondary portfolio filters through geo rollups', async () => {
    const calls: Array<{ path: string; init?: RequestInit }> = [];
    vi.stubGlobal('fetch', async (path: string, init?: RequestInit) => {
      calls.push({ path, init });
      return jsonResponse(200, { rollups: [], snapshot_date: null });
    });

    const portfolioCriteria = {
      occupancy: 'Owner-occupied',
      lien_status: 'Open 1st lien',
      owner_link: 'Portfolio investor (5+)',
      purchase_intent: 'Listed for sale',
      min_equity_pct_label: '≥ 25%',
    };

    await api.stateRollups(
      ['itm', 'investor', 'equity', 'retention'],
      undefined,
      'all',
      portfolioCriteria,
    );
    await api.countyRollups(
      'fl',
      undefined,
      ['itm', 'investor', 'equity', 'retention'],
      'all',
      portfolioCriteria,
    );
    await api.zipRollups(
      '12011',
      undefined,
      ['itm', 'investor', 'equity', 'retention'],
      'all',
      portfolioCriteria,
    );

    const stateUrl = new URL(calls[0].path, 'http://localhost');
    expect(stateUrl.pathname).toBe('/api/v1/geo/state-rollups');
    expect(stateUrl.searchParams.get('segment_codes')).toBe('itm,investor,equity,retention');
    expect(stateUrl.searchParams.get('segment_mode')).toBe('all');

    const countyUrl = new URL(calls[1].path, 'http://localhost');
    expect(countyUrl.pathname).toBe('/api/v1/geo/county-rollups');
    expect(countyUrl.searchParams.get('state')).toBe('FL');
    expect(countyUrl.searchParams.get('segment_codes')).toBe('itm,investor,equity,retention');
    expect(countyUrl.searchParams.get('segment_mode')).toBe('all');

    const zipUrl = new URL(calls[2].path, 'http://localhost');
    expect(zipUrl.pathname).toBe('/api/v1/geo/zip-rollups');
    expect(zipUrl.searchParams.get('county_fips')).toBe('12011');
    expect(zipUrl.searchParams.get('segment_codes')).toBe('itm,investor,equity,retention');
    expect(zipUrl.searchParams.get('segment_mode')).toBe('all');

    for (const url of [stateUrl, countyUrl, zipUrl]) {
      expect(url.searchParams.get('occupancy')).toBe('Owner-occupied');
      expect(url.searchParams.get('lien_status')).toBe('Open 1st lien');
      expect(url.searchParams.get('owner_link')).toBe('Portfolio investor (5+)');
      expect(url.searchParams.get('purchase_intent')).toBe('Listed for sale');
      expect(url.searchParams.get('min_equity_pct_label')).toBe('≥ 25%');
    }
  });
});

describe('data estate API client', () => {
  it('fetches the data-estate proof surface', async () => {
    const calls: Array<{ path: string; init?: RequestInit }> = [];
    vi.stubGlobal('fetch', async (path: string, init?: RequestInit) => {
      calls.push({ path, init });
      return jsonResponse(200, {
        generated_at: '2026-05-06T00:00:00Z',
        lender_name: 'Summit Mortgage',
        public_demo_masking: true,
        lanes: [],
        known_data_gaps: [],
        proof_assets: [],
      });
    });

    const result = await api.dataEstate();

    expect(calls[0].path).toBe('/api/v1/data-estate');
    expect(result.public_demo_masking).toBe(true);
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
        sample_questions: ['How many borrowers are currently in-the-money?'],
      });
    });

    const result = await api.genieStart();

    expect(result.conversation_id).toBe('conv-123');
    expect(result.sample_questions).toEqual(['How many borrowers are currently in-the-money?']);
    expect(calls[0].path).toBe('/api/v1/genie/start');
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

    expect(calls[0].path).toBe('/api/v1/genie/message');
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

    expect(calls[0].path).toBe('/api/v1/genie/actions');
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
