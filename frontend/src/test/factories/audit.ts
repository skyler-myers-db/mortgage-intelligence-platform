/**
 * Test factory for one weekly audit rollup (audit quality-04 D-api-types-a3
 * P3, W5e w5-wire-types B3), the explorer's fail-closed row type.
 */
import type { AuditRollupResponse } from '../../lib/apiClients/audit';
import { AUDIT_ROLLUP_BASE } from '../../../tests/e2e/fixture/data/wireBases';

export function makeAuditRollupResponse(overrides: Partial<AuditRollupResponse> = {}): AuditRollupResponse {
  return { ...AUDIT_ROLLUP_BASE, ...overrides };
}
