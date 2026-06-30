import { Chip } from '../components/Primitives';
import type { GrowthAgentCapabilityRow } from '../types/growthAgent';
import { capabilityStatusText } from './ask-genie.growth-agent.helpers';

interface GrowthAgentCapabilityPanelProps {
  rows: GrowthAgentCapabilityRow[];
  isPending: boolean;
}

export function GrowthAgentCapabilityPanel({
  rows,
  isPending,
}: GrowthAgentCapabilityPanelProps) {
  if (rows.length > 0) {
    return rows.map((capability) => (
      <div key={capability.key} className="growth-agent-capability">
        <Chip variant={capability.claimable && capability.status === 'available' ? 'success' : 'neutral'}>
          {capability.claimable || capability.status !== 'available'
            ? capabilityStatusText(capability.status)
            : 'Configured'}
        </Chip>
        <div>
          <div className="growth-agent-step__title">{capability.label}</div>
          <div className="growth-agent-step__detail">{capability.detail}</div>
        </div>
      </div>
    ));
  }

  if (isPending) {
    return (
      <div className="growth-agent-capability">
        <Chip variant="neutral">Checking</Chip>
        <div>
          <div className="growth-agent-step__title">Agentic capability snapshot</div>
          <div className="growth-agent-step__detail">
            Running live probes for Genie, certified metric views, reviewed SQL tools,
            and non-claimable roadmap items.
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="growth-agent-capability">
      <Chip variant="warning">Unverified</Chip>
      <div>
        <div className="growth-agent-step__title">Agentic capability snapshot</div>
        <div className="growth-agent-step__detail">
          Capability readiness is unavailable; treat multi-agent, AI Gateway, and MLflow claims as unverified.
        </div>
      </div>
    </div>
  );
}
