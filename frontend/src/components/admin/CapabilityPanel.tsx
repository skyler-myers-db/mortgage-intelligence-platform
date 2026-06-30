import { useMemo } from 'react';
import { api } from '../../lib/api';
import { queryKeys } from '../../lib/queryKeys';
import { useWarmingUpRetry } from '../../lib/useWarmingUpRetry';
import { Chip } from '../Primitives';
import { WarmingUpBlock } from '../ui/WarmingUpBlock';

/**
 * Agentic capability readiness — renders the honest DAIS-2026 capability
 * snapshot from GET /api/admin/capabilities. This is the visible enforcement
 * of the no-overclaim posture: capabilities that aren't `claimable` render as
 * "roadmap" / "not provisioned", NEVER as integrated. `hidden` rows (preview
 * features with the mirror flag off) are not shown at all.
 */

export type CapabilityStatus =
  | 'available'
  | 'configured'
  | 'not_provisioned'
  | 'preview_mirror'
  | 'hidden';

export interface CapabilityRow {
  key: string;
  label: string;
  ga: boolean;
  status: CapabilityStatus;
  claimable: boolean;
  detail: string;
}

interface CapabilitiesResponse {
  capabilities: CapabilityRow[];
}

type Tone = 'success' | 'warning' | 'danger' | 'neutral';

interface CapabilityView {
  label: string;
  statusLabel: string;
  tone: Tone;
  detail: string;
}

const STATUS_LABEL: Record<CapabilityStatus, string> = {
  available: 'available',
  configured: 'configured',
  not_provisioned: 'not provisioned',
  preview_mirror: 'roadmap',
  hidden: 'hidden',
};

function toneFor(row: CapabilityRow): Tone {
  if (row.claimable && row.status === 'available') return 'success';
  if (row.status === 'configured') return 'neutral';
  if (row.status === 'preview_mirror') return 'warning';
  return 'neutral'; // not_provisioned
}

export function toCapabilityViews(rows: CapabilityRow[] | undefined): CapabilityView[] {
  return (rows ?? [])
    .filter((row) => row.status !== 'hidden')
    .map((row) => ({
      label: row.label,
      statusLabel: row.ga
        ? STATUS_LABEL[row.claimable || row.status !== 'available' ? row.status : 'configured']
        : `${STATUS_LABEL[row.status]} · preview`,
      tone: toneFor(row),
      detail: row.detail,
    }));
}

export function CapabilityPanel() {
  const { data, warmingUp, error } = useWarmingUpRetry<CapabilitiesResponse>(
    (signal) => api.adminCapabilities<CapabilitiesResponse>(signal),
    [],
    { queryKey: queryKeys.adminCapabilities() },
  );
  const views = useMemo(() => toCapabilityViews(data?.capabilities), [data]);
  const liveCount = (data?.capabilities ?? []).filter((row) => row.claimable).length;
  const roadmapCount = (data?.capabilities ?? []).filter(
    (row) => row.status === 'preview_mirror',
  ).length;

  return (
    <div className="surface mt-grid" id="capability-readiness">
      <div className="surface__hdr surface__hdr--split">
        <div>
          <div className="h-4">Agentic capability readiness</div>
          <div className="muted fs-12">
            DAIS-2026 stack. Available rows are live-proven; configured rows require a live probe before claims.
          </div>
        </div>
        <Chip variant={error ? 'warning' : 'neutral'}>
          {error
            ? 'capabilities unknown'
            : `${liveCount} live · ${roadmapCount} roadmap`}
        </Chip>
      </div>
      <div className="surface__body surface__body--stack-sm">
        {warmingUp && (
          <WarmingUpBlock state={warmingUp} title="Capability readiness loading" compact />
        )}
        {error && !warmingUp && (
          <div className="muted body fs-12">
            Capability snapshot unavailable; treat agentic claims as unverified.
          </div>
        )}
        <div className="admin-rollups" role="region" aria-label="Agentic capability readiness">
          <div className="admin-rollups__grid admin-rollups__grid--wide">
            {views.map((item) => (
              <div key={item.label} className="admin-rollup admin-rollup--readiness">
                <div className="split-row">
                  <span className="admin-rollup__label">{item.label}</span>
                  <Chip variant={item.tone}>{item.statusLabel}</Chip>
                </div>
                <span className="muted fs-12">{item.detail}</span>
              </div>
            ))}
          </div>
        </div>
        <div className="muted fs-12">
          Configured = dependency wiring is present but not claimable yet. Roadmap = the underlying
          Databricks capability is preview/no-API; MIP mirrors the pattern but does not claim integration.
        </div>
      </div>
    </div>
  );
}
