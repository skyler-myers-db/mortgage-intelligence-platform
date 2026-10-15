/**
 * bulkRefusalCopy: a refused bulk row reads one fixed sentence per status
 * class, never the server's text (audit states-04 residual).
 */
import { describe, expect, it } from 'vitest';
import { ApiError } from '../../lib/apiTransport';
import { bulkRefusalCopy } from './bulkRefusalCopy';

const SERVER_TEXT = 'Approval requires the audited generated draft proof';

function refused(status: number | null): ApiError {
  return new ApiError(SERVER_TEXT, { path: '/api/outreach/approve', status });
}

describe('bulkRefusalCopy', () => {
  it('names each refusal class in fixed words', () => {
    expect(bulkRefusalCopy(refused(403))).toBe('your role cannot make this decision');
    expect(bulkRefusalCopy(refused(409))).toBe('it changed after you selected it; review it on its own');
    expect(bulkRefusalCopy(refused(422))).toBe('it did not pass the review checks; open it to review the draft and reason');
    expect(bulkRefusalCopy(refused(429))).toBe('too many decisions at once; wait a minute, then retry it');
  });

  it('reads a server failure, an unknown status and a non-API error as not recorded', () => {
    for (const err of [refused(500), refused(503), refused(418), refused(null), new Error(SERVER_TEXT), 'boom']) {
      expect(bulkRefusalCopy(err)).toBe('the server did not record it; retry it');
    }
  });

  it('never echoes the server text', () => {
    for (const status of [400, 401, 403, 404, 409, 410, 422, 429, 500, 502, 503]) {
      expect(bulkRefusalCopy(refused(status))).not.toContain(SERVER_TEXT);
    }
  });
});
