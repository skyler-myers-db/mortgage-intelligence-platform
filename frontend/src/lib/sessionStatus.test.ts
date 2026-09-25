import { afterEach, describe, expect, it } from 'vitest';
import {
  _resetSessionStatusForTests,
  getSessionStatus,
  markSessionExpired,
  markUnrecordedWrite,
  unrecordedWriteFor,
} from './sessionStatus';

afterEach(() => _resetSessionStatusForTests());

describe('session status: what the dialog says was not recorded', () => {
  it('classifies only the decision endpoints and the update verbs as writes', () => {
    expect(unrecordedWriteFor('POST', '/api/v1/outreach/approve')).toBe('approval');
    expect(unrecordedWriteFor('POST', '/api/v1/outreach/reject')).toBe('rejection');
    expect(unrecordedWriteFor('POST', '/api/v1/outreach/draft')).toBeNull();
    expect(unrecordedWriteFor('PATCH', '/api/v1/workspace/leads/1')).toBe('change');
    expect(unrecordedWriteFor('GET', '/api/v1/leads')).toBeNull();
  });

  it('never marks anything before the session has ended', () => {
    markUnrecordedWrite('bulk_approval');
    expect(getSessionStatus()).toEqual({ expired: false, unrecorded: null });
  });

  it('a partly recorded bulk approval outranks the single approval its own POST reported', () => {
    markSessionExpired({ method: 'POST', path: '/api/v1/outreach/approve' });
    expect(getSessionStatus().unrecorded).toBe('approval');
    markUnrecordedWrite('bulk_approval');
    expect(getSessionStatus().unrecorded).toBe('bulk_approval');
    // Never downgraded by a later, smaller loss.
    markUnrecordedWrite('approval');
    markSessionExpired({ method: 'POST', path: '/api/v1/outreach/reject' });
    markSessionExpired({ method: 'PUT', path: '/api/v1/workspace/leads/1' });
    expect(getSessionStatus().unrecorded).toBe('bulk_approval');
  });
});
