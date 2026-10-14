/**
 * Test factory for a Growth Agent run (audit quality-04 D-api-types-a3 P3,
 * W5e w5-wire-types B3): a reviewed-workflow run with no monitor and no
 * audit row, so a test that needs a ledger-backed (savable) run says so.
 */
import type { GrowthAgentRunResponse } from '../../types/growthAgent';
import { GROWTH_AGENT_RUN_BASE } from '../../../tests/e2e/fixture/data/wireBases';

export function makeGrowthAgentRunResponse(overrides: Partial<GrowthAgentRunResponse> = {}): GrowthAgentRunResponse {
  return { ...GROWTH_AGENT_RUN_BASE, monitor: null, audit_event_id: null, ...overrides };
}
