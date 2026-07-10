import { useState } from 'react';
import { Chip } from '../components/Primitives';
import { Icon } from '../components/Icon';
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

/**
 * Collapsed-by-default disclosure around the capability panel. Platform /
 * proof-status diagnostics stay reachable without dominating a general-user
 * surface. Mirrors admin-config's "Workspace appearance" appearance-toggle
 * pattern (surface__hdr appearance-toggle header + conditionally rendered
 * body); the panel's honesty labels are untouched once expanded.
 */
export function GrowthAgentCapabilityDisclosure({
  rows,
  isPending,
}: GrowthAgentCapabilityPanelProps) {
  const [open, setOpen] = useState(false);
  return (
    <div className="surface surface--inset">
      <button
        type="button"
        className="surface__hdr appearance-toggle"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
      >
        <div className="appearance-toggle__side">
          <div>
            <div className="eyebrow">Platform capabilities</div>
            <div className="muted fs-12">Live capability and proof status for this workspace</div>
          </div>
        </div>
        <div className="appearance-toggle__side">
          <Icon name={open ? 'up' : 'down'} size={12} />
        </div>
      </button>
      {open && (
        <div className="surface__body">
          <section className="growth-agent-capabilities" aria-label="Growth Agent capability boundaries">
            <GrowthAgentCapabilityPanel rows={rows} isPending={isPending} />
          </section>
        </div>
      )}
    </div>
  );
}
