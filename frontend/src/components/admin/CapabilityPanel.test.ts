import { describe, expect, it } from 'vitest';
import { toCapabilityViews, type CapabilityRow } from './CapabilityPanel';

const rows: CapabilityRow[] = [
  {
    key: 'certified_metric_views',
    label: 'Certified UC Metric Views',
    ga: true,
    status: 'available',
    claimable: true,
    detail: 'live',
  },
  {
    key: 'ai_gateway',
    label: 'Unity AI Gateway governance',
    ga: true,
    status: 'not_provisioned',
    claimable: false,
    detail: 'off',
  },
  {
    key: 'customerlake',
    label: 'CustomerLake (Agentic CDP)',
    ga: false,
    status: 'preview_mirror',
    claimable: false,
    detail: 'mirror',
  },
  {
    key: 'lakehouse_rt',
    label: 'Lakehouse//RT',
    ga: false,
    status: 'hidden',
    claimable: false,
    detail: 'hidden',
  },
];

describe('toCapabilityViews', () => {
  it('drops hidden rows entirely', () => {
    const views = toCapabilityViews(rows);
    expect(views.map((v) => v.label)).not.toContain('Lakehouse//RT');
    expect(views).toHaveLength(3);
  });

  it('marks preview (non-GA) rows as roadmap · preview, never available', () => {
    const cl = toCapabilityViews(rows).find((v) => v.label.startsWith('CustomerLake'));
    expect(cl?.statusLabel).toBe('roadmap · preview');
    expect(cl?.statusLabel).not.toContain('available');
    expect(cl?.tone).toBe('warning');
  });

  it('renders an available GA capability as success', () => {
    const mv = toCapabilityViews(rows).find((v) => v.label.includes('Metric Views'));
    expect(mv?.statusLabel).toBe('available');
    expect(mv?.tone).toBe('success');
  });

  it('does not render success when a row is marked available but not claimable', () => {
    const inconsistent = toCapabilityViews([
      {
        key: 'agent_orchestrator',
        label: 'Agent Framework orchestration',
        ga: true,
        status: 'available',
        claimable: false,
        detail: 'inconsistent backend row',
      },
    ]);
    expect(inconsistent[0]?.statusLabel).toBe('configured');
    expect(inconsistent[0]?.tone).not.toBe('success');
  });

  it('renders a flag-off GA capability as not provisioned (neutral)', () => {
    const gw = toCapabilityViews(rows).find((v) => v.label.includes('AI Gateway'));
    expect(gw?.statusLabel).toBe('not provisioned');
    expect(gw?.tone).toBe('neutral');
  });

  it('handles undefined input', () => {
    expect(toCapabilityViews(undefined)).toEqual([]);
  });
});
