/**
 * Test factory for the session (audit quality-04 D-api-types-a3 P3, W5e
 * w5-wire-types B3). The wire base is a privileged signed-in session; the
 * factory re-sets every capability to the least-privileged default, so a
 * test that needs an approver, an administrator or an auditor says so.
 */
import type { SessionResponse } from '../../types/workspace';
import { SESSION_BASE } from '../../../tests/e2e/fixture/data/wireBases';

const LEAST_PRIVILEGED = {
  can_approve: false,
  can_access_admin: false,
  can_read_audit: false,
  presenter_mode: false,
} as const;

export function makeSessionResponse(overrides: Partial<SessionResponse> = {}): SessionResponse {
  return { ...SESSION_BASE, ...LEAST_PRIVILEGED, ...overrides };
}
