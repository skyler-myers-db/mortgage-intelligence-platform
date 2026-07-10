import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Chip } from '../Primitives';
import { Icon } from '../Icon';
import { api } from '../../lib/api';
import { queryKeys } from '../../lib/queryKeys';
import { capabilityStatusText, visibleGrowthAgentCapabilities } from '../../lib/growthAgentCapabilities';
import type { GrowthAgentCapabilityRow } from '../../types/growthAgent';

/**
 * Platform capability diagnostics. Relocated from the general-user Ask Genie
 * surface to the Admin console (2026-07-10): the claimable / Configured /
 * Unverified honesty logic below is untouched; only its home changed so
 * operators — not every task user — see live proof status.
 */

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
 * Collapsed-by-default disclosure around the capability panel, wired to the
 * same capabilities API the Growth Agent uses. Diagnostics stay reachable in
 * the operator console without dominating a task surface. Mirrors admin-config's
 * appearance-toggle disclosure pattern; the panel's honesty labels are untouched
 * once expanded.
 */
export function PlatformCapabilitiesPanel() {
  const [open, setOpen] = useState(false);
  const capabilitiesQuery = useQuery({
    queryKey: queryKeys.growthAgentCapabilities(),
    queryFn: ({ signal }) => api.growthAgentCapabilities(signal),
    staleTime: 60_000,
    refetchOnWindowFocus: false,
  });
  const rows = visibleGrowthAgentCapabilities(capabilitiesQuery.data?.capabilities);
  return (
    <div className="surface mt-grid">
      <button
        type="button"
        className="surface__hdr appearance-toggle"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
      >
        <div className="appearance-toggle__side">
          <Icon name="bolt" size={14} className="icon-accent" />
          <div>
            {/* Title distinguishes this live-probe panel from the buyer-claim
                "Buyer readiness" panel lower in the console. */}
            <div className="h-4">Platform capabilities (live probes)</div>
            <div className="muted fs-12">Live capability and proof status for this workspace</div>
          </div>
        </div>
        <div className="appearance-toggle__side">
          <Icon name={open ? 'up' : 'down'} size={12} />
        </div>
      </button>
      {open && (
        <div className="surface__body">
          <section className="growth-agent-capabilities" aria-label="Platform capability boundaries">
            <GrowthAgentCapabilityPanel rows={rows} isPending={capabilitiesQuery.isPending} />
          </section>
        </div>
      )}
    </div>
  );
}
