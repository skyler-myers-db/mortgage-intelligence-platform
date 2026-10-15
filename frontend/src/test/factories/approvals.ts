/**
 * Test factories for the approve / reject results and the approval-request
 * rows (audit quality-04 D-api-types-a3 P3, W5e w5-wire-types B3). The
 * defaults record nothing: approved and rejected are false and a nullable
 * approval id is null, so a test that needs a recorded decision says so
 * (approvals stay pessimistic in the code under test, too).
 */
import type { ApproveResult, RejectResult } from '../../lib/apiTypes';
import type { ApprovalRequestRow } from '../../types/approvalRequests';
import {
  APPROVAL_REQUEST_ROW_BASE,
  APPROVE_RESULT_BASE,
  REJECT_RESULT_BASE,
} from '../../../tests/e2e/fixture/data/wireBases';

export function makeApproveResult(overrides: Partial<ApproveResult> = {}): ApproveResult {
  return { ...APPROVE_RESULT_BASE, approved: false, ...overrides };
}

export function makeRejectResult(overrides: Partial<RejectResult> = {}): RejectResult {
  return { ...REJECT_RESULT_BASE, rejected: false, ...overrides };
}

export function makeApprovalRequestRow(overrides: Partial<ApprovalRequestRow> = {}): ApprovalRequestRow {
  return { ...APPROVAL_REQUEST_ROW_BASE, state: 'open', approval_id: null, ...overrides };
}
