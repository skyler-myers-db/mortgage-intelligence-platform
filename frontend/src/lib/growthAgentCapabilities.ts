import type { GrowthAgentCapabilityRow } from '../types/growthAgent';

// Pure helpers for the platform-capability panel. Relocated out of
// routes/ask-genie.growth-agent.helpers.tsx (2026-07-10) so the Admin console
// PlatformCapabilitiesPanel can consume them without a components -> routes
// back-import when the capability diagnostics moved off the general-user Ask
// Genie surface.

const GROWTH_AGENT_CAPABILITY_KEYS = new Set([
  'genie_conversation_api',
  'certified_metric_views',
  'uc_function_tools',
  'agent_orchestrator',
  'ai_gateway',
  'lakebase_sync',
  'agent_eval',
]);

export function visibleGrowthAgentCapabilities(
  rows: GrowthAgentCapabilityRow[] | undefined,
): GrowthAgentCapabilityRow[] {
  return (rows ?? []).filter(
    (row) => GROWTH_AGENT_CAPABILITY_KEYS.has(row.key) && row.status !== 'hidden',
  );
}

export function capabilityStatusText(status: string): string {
  switch (status) {
    case 'available':
      return 'Available';
    case 'configured':
      return 'Configured';
    case 'not_provisioned':
      return 'Not provisioned';
    case 'preview_mirror':
      return 'Roadmap mirror';
    case 'hidden':
      return 'Hidden';
    default:
      return status.replace('_', ' ');
  }
}
