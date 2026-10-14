/**
 * delivery-06 client half (D-platform-process-e1 items 7-9): the
 * `X-Data-Last-Good-At` validator and the geo `*WithFreshness` reads.
 *
 * The header is trusted only in its exact server shape on a 2xx response:
 * a non-2xx rejects in the transport before any header is read, so a
 * failure can never produce a "last good" age (W5a ruling).
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { api, ApiError } from '../api';
import { dataRefreshedAtHeader, lastGoodAtHeader } from './headers';

const headersWith = (value: string | null) => new Headers(value === null ? {} : { 'X-Data-Last-Good-At': value });

function jsonResponse(status: number, body: unknown, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json', ...headers } });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('lastGoodAtHeader', () => {
  it('accepts exactly the server shape, a UTC instant to the second', () => {
    expect(lastGoodAtHeader(headersWith('2026-09-30T14:05:09Z'))).toBe('2026-09-30T14:05:09Z');
  });

  it('rejects a missing header and every other shape', () => {
    for (const value of [
      null,
      '',
      '2026-09-30',
      '2026-09-30T14:05:09',
      '2026-09-30T14:05:09.123Z',
      '2026-09-30T14:05:09+00:00',
      '2026-9-30T14:05:09Z',
      'x2026-09-30T14:05:09Z',
      '2026-09-30T14:05:09Zjunk',
      'now',
    ]) {
      expect(lastGoodAtHeader(headersWith(value)), String(value)).toBeNull();
    }
  });

  it('rejects a well-shaped but impossible instant', () => {
    expect(lastGoodAtHeader(headersWith('2026-13-45T25:61:61Z'))).toBeNull();
  });
});

describe('dataRefreshedAtHeader', () => {
  it('keeps the loose refresh-instant shape and drops anything else', () => {
    expect(dataRefreshedAtHeader('2026-09-30T14:05:09.123+00:00')).toBe('2026-09-30T14:05:09.123+00:00');
    expect(dataRefreshedAtHeader(null)).toBeNull();
    expect(dataRefreshedAtHeader('<script>')).toBeNull();
  });
});

describe('geo *WithFreshness reads', () => {
  it('return the payload with the validated header, on the same URL as the plain read', async () => {
    const calls: string[] = [];
    vi.stubGlobal('fetch', async (path: string) => {
      calls.push(path);
      return jsonResponse(200, { rollups: [], snapshot_date: '2026-09-30' }, { 'X-Data-Last-Good-At': '2026-09-30T08:00:00Z' });
    });
    const fresh = await api.stateRollupsWithFreshness(['itm'], undefined, 'all', { lien_status: 'Open' });
    expect(fresh).toEqual({ data: { rollups: [], snapshot_date: '2026-09-30' }, lastGoodAt: '2026-09-30T08:00:00Z' });
    await api.stateRollups(['itm'], undefined, 'all', { lien_status: 'Open' });
    expect(calls[0]).toBe(calls[1]);
    expect(calls[0]).toContain('/api/v1/geo/state-rollups?segment_codes=itm&segment_mode=all');
  });

  it('read no age without the header, or from a malformed one', async () => {
    vi.stubGlobal('fetch', async () => jsonResponse(200, [], { 'X-Data-Last-Good-At': 'yesterday' }));
    expect(await api.segmentsWithFreshness()).toEqual({ data: [], lastGoodAt: null });
    vi.stubGlobal('fetch', async () => jsonResponse(200, { rollups: [] }));
    expect((await api.zipRollupsWithFreshness({ state: 'tx' })).lastGoodAt).toBeNull();
  });

  it('keep every URL byte for byte, county and a cohort query included', async () => {
    const calls: string[] = [];
    vi.stubGlobal('fetch', async (path: string) => {
      calls.push(path);
      return jsonResponse(200, { rollups: [] });
    });
    await api.countyRollups('tx', undefined, ['equity'], 'any');
    await api.countyRollupsWithFreshness('tx', undefined, ['equity'], 'any');
    // W5c deleted the unused plain zipRollups; the state pair stands in for the cohort query.
    await api.stateRollups(null, undefined, 'any', { occupancy: 'Owner occupied' });
    await api.stateRollupsWithFreshness(null, undefined, 'any', { occupancy: 'Owner occupied' });
    await api.segments(undefined, null);
    await api.segmentsWithFreshness(undefined, null);
    expect(calls[0]).toBe(calls[1]);
    expect(calls[2]).toBe(calls[3]);
    expect(calls[4]).toBe(calls[5]);
    expect(calls[4]).toBe('/api/v1/segments');
  });

  it('a 503 that carries the header rejects: no age is ever produced on a non-2xx', async () => {
    vi.stubGlobal('fetch', async () =>
      jsonResponse(
        503,
        { detail: 'Warehouse unavailable', retryable: true, dependency: 'warehouse', reason: 'retries_exhausted' },
        { 'X-Data-Last-Good-At': '2026-09-30T08:00:00Z' },
      ));
    await expect(api.stateRollupsWithFreshness(null)).rejects.toBeInstanceOf(ApiError);
    await expect(api.segmentsWithFreshness()).rejects.toBeInstanceOf(ApiError);
  });
});
