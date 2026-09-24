/**
 * api_call templating, exclusions, Server-Timing parsing and sampling (audit
 * 2026-09-21 delivery-v3). The wire-level proof (what the beacon carries) is
 * rum.events.test.ts; the server rejects anything outside the same closed
 * vocabulary (tests/unit/test_rum_client_error.py).
 */
import { describe, expect, it, vi } from 'vitest';
import {
  RUM_API_SAMPLE_KEY,
  apiCallRoute,
  isApiCallSampled,
  serverTimingDetails,
  templateApiPath,
} from './rumApiRoute';

const ORIGIN = 'https://mip.example';

function timing(name: string, duration = 0, description = '') {
  return { name, duration, description };
}

describe('templateApiPath', () => {
  it('canonicalizes /api/v1 and keeps only known literal segments', () => {
    expect(templateApiPath('/api/v1/borrowers/B-0123456789ABC/proof')).toBe('/api/borrowers/:id/proof');
    expect(templateApiPath('/api/borrowers/B-0123456789ABC')).toBe('/api/borrowers/:id');
    expect(templateApiPath('/api/v1/leads')).toBe('/api/leads');
  });

  it('templates the ids sanitizeRumRoute leaves in place', () => {
    expect(templateApiPath('/api/v1/analytics/funnel/loan-officers/lo-jdoe')).toBe(
      '/api/analytics/funnel/loan-officers/:id',
    );
    expect(templateApiPath('/api/v1/data-estate/assets/lead_population/metadata')).toBe(
      '/api/data-estate/assets/:id/metadata',
    );
    expect(templateApiPath('/api/v1/campaigns/cmp-summit-01/outcomes')).toBe('/api/campaigns/:id/outcomes');
  });

  it('drops the query string and hash, and keeps at most ten segments', () => {
    expect(templateApiPath('/api/leads?state=TX&q=jane@summit.example')).toBe('/api/leads');
    expect(templateApiPath('/api/v1/leads#top')).toBe('/api/leads');
    const deep = `/api/${Array.from({ length: 12 }, () => 'leads').join('/')}`;
    expect(templateApiPath(deep)?.split('/').length).toBe(12); // '' + 'api' + 10 segments
  });

  it('returns null outside /api', () => {
    expect(templateApiPath('/lead-queue')).toBeNull();
    expect(templateApiPath('/api')).toBeNull();
    expect(templateApiPath('/api/v1')).toBeNull();
    expect(templateApiPath('/apis/leads')).toBeNull();
  });
});

describe('apiCallRoute', () => {
  it('reports same-origin /api calls only', () => {
    expect(apiCallRoute(`${ORIGIN}/api/v1/leads?state=TX`, ORIGIN)).toBe('/api/leads');
    expect(apiCallRoute('https://other.example/api/v1/leads', ORIGIN)).toBeNull();
    expect(apiCallRoute(`${ORIGIN}/assets/index-a1.js`, ORIGIN)).toBeNull();
  });

  it('never reports the telemetry POST, the health probes or the Genie progress poll', () => {
    for (const path of [
      '/api/v1/telemetry/rum',
      '/api/telemetry/rum',
      '/api/v1/health',
      '/api/v1/admin/health',
      '/api/v1/genie/message/progress',
    ]) {
      expect(apiCallRoute(`${ORIGIN}${path}`, ORIGIN), path).toBeNull();
    }
    // Siblings of an excluded path still report.
    expect(apiCallRoute(`${ORIGIN}/api/v1/genie/message/submit`, ORIGIN)).toBe('/api/genie/message/submit');
    expect(apiCallRoute(`${ORIGIN}/api/v1/admin/settings`, ORIGIN)).toBe('/api/admin/settings');
  });
});

describe('serverTimingDetails', () => {
  it('reads cache hit, miss and stale and rounds the durations', () => {
    for (const state of ['hit', 'miss', 'stale'] as const) {
      expect(serverTimingDetails([timing('cache', 0, state)])).toEqual({ cache: state });
    }
    expect(
      serverTimingDetails([
        timing('cache', 0, 'miss'),
        timing('warehouse', 812.4),
        timing('lakebase', 2.5),
        timing('total', 840.49),
      ]),
    ).toEqual({ cache: 'miss', warehouse_ms: 812, lakebase_ms: 3, total_ms: 840 });
  });

  it('yields no keys when the header is absent', () => {
    expect(serverTimingDetails([])).toEqual({});
  });

  it('ignores a garbage desc, unknown names and out-of-range durations; the first entry of a name wins', () => {
    expect(
      serverTimingDetails([
        timing('cache', 0, 'warm'),
        timing('cache', 0, 'hit'),
        timing('db', 5),
        timing('warehouse', -1),
        timing('lakebase', 600_001),
        timing('total', Number.NaN),
        timing('total', 4),
      ]),
    ).toEqual({});
    expect(serverTimingDetails([timing('total', 4.2), timing('total', 99)])).toEqual({ total_ms: 4 });
  });
});

describe('isApiCallSampled', () => {
  function memoryStorage(initial?: string) {
    const values = new Map<string, string>(initial === undefined ? [] : [[RUM_API_SAMPLE_KEY, initial]]);
    return {
      getItem: vi.fn((key: string) => values.get(key) ?? null),
      setItem: vi.fn((key: string, value: string) => {
        values.set(key, value);
      }),
    };
  }

  it("'1' samples the tab and '0' does not, without a draw", () => {
    const random = vi.fn(() => 0);
    expect(isApiCallSampled(() => memoryStorage('1'), random)).toBe(true);
    expect(isApiCallSampled(() => memoryStorage('0'), random)).toBe(false);
    expect(random).not.toHaveBeenCalled();
  });

  it('draws once when the key is absent and persists the result', () => {
    const storage = memoryStorage();
    const random = vi.fn(() => 0.05);
    expect(isApiCallSampled(() => storage, random)).toBe(true);
    expect(storage.setItem).toHaveBeenCalledWith(RUM_API_SAMPLE_KEY, '1');
    expect(isApiCallSampled(() => storage, random)).toBe(true);
    expect(random).toHaveBeenCalledTimes(1);

    const unsampled = memoryStorage();
    expect(isApiCallSampled(() => unsampled, () => 0.5)).toBe(false);
    expect(unsampled.setItem).toHaveBeenCalledWith(RUM_API_SAMPLE_KEY, '0');
  });

  it('does not sample a tab whose storage is unavailable or refuses the write', () => {
    expect(isApiCallSampled(() => null, () => 0)).toBe(false);
    expect(
      isApiCallSampled(
        () => {
          throw new Error('SecurityError');
        },
        () => 0,
      ),
    ).toBe(false);
    const refusing = memoryStorage();
    refusing.setItem.mockImplementation(() => {
      throw new Error('QuotaExceededError');
    });
    expect(isApiCallSampled(() => refusing, () => 0)).toBe(false);
  });
});
