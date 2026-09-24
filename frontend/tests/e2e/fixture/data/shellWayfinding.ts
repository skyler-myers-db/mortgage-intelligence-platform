/**
 * Shell-wayfinding lane fixtures (wave 1c: breadcrumbs, queue pager,
 * identity menu). The registry's shell session carries only the wave-0
 * fields; these tests replace it per test with the full identity payload the
 * backend now returns. Synthetic identity only.
 */
import type { SessionResponse } from '../../../../src/types';
import { json, type FixtureReply } from '../mockApi';

export const SIGNED_IN_APPROVER: SessionResponse = {
  can_access_admin: true,
  can_approve: true,
  actor_email: 'jane.doe@summit-mortgage.example',
  actor_display_name: 'Jane Doe',
  role_labels: ['Administrator', 'Approver'],
};

export function sessionReply(session: SessionResponse = SIGNED_IN_APPROVER): FixtureReply<SessionResponse> {
  return json<SessionResponse>(session);
}
