/**
 * The refusal-reports client (D-audit-reads-d): the list URL carries only the
 * page size, the family filter and the opaque cursor (never question text),
 * and the question read targets exactly one report by its encoded id.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';

const getJson = vi.hoisted(() => vi.fn());
vi.mock('../apiTransport', () => ({ getJson }));

import { REFUSAL_REPORTS_PAGE_SIZE, refusalReportsApi } from './refusalReports';

describe('refusalReportsApi', () => {
  beforeEach(() => {
    getJson.mockReset();
    getJson.mockResolvedValue({});
  });

  it('lists the first page with only the page size', async () => {
    await refusalReportsApi.list({ family: null, cursor: null });
    expect(getJson).toHaveBeenCalledWith(`/api/audit/refusal-reports?limit=${REFUSAL_REPORTS_PAGE_SIZE}`, undefined);
  });

  it('adds the family filter and the opaque cursor, encoded', async () => {
    const signal = new AbortController().signal;
    await refusalReportsApi.list({ family: 'protected_class', cursor: 'eyJ2IjoxfQ.c2ln+/=' }, signal);
    const [url, passedSignal] = getJson.mock.calls[0] as [string, AbortSignal];
    const query = new URL(url, 'http://fixture').searchParams;
    expect(new URL(url, 'http://fixture').pathname).toBe('/api/audit/refusal-reports');
    expect([...query.keys()]).toEqual(['limit', 'family', 'cursor']);
    expect(query.get('family')).toBe('protected_class');
    expect(query.get('cursor')).toBe('eyJ2IjoxfQ.c2ln+/=');
    expect(passedSignal).toBe(signal);
  });

  it('reads one question by its encoded report id', async () => {
    await refusalReportsApi.question('5eed00a3-0000-4000-8000-0000000000a3');
    expect(getJson).toHaveBeenCalledWith(
      '/api/audit/refusal-reports/5eed00a3-0000-4000-8000-0000000000a3/question',
      undefined,
    );
    await refusalReportsApi.question('a/b?c');
    expect(getJson.mock.calls[1][0]).toBe('/api/audit/refusal-reports/a%2Fb%3Fc/question');
  });
});
