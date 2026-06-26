import type { ReactNode } from 'react';
import { Button, Chip } from '../components/Primitives';
import { Icon } from '../components/Icon';
import type {
  GrowthAgentGovernanceChip,
  GrowthAgentPolicyCheck,
  GrowthAgentRunResponse,
  GrowthAgentToolStep,
} from '../types';

export function formatGrowthAgentCount(value: number | null | undefined): string {
  return new Intl.NumberFormat('en-US').format(Math.max(0, Number(value ?? 0)));
}

function specialistAgentLabel(agent: GrowthAgentRunResponse['specialist_agent']): string {
  switch (agent) {
    case 'structured_data_agent':
      return 'Structured Data Agent';
    case 'borrower_dossier_agent':
      return 'Borrower Dossier Agent';
    case 'offer_agent':
      return 'Offer Agent';
    case 'compliance_agent':
      return 'Compliance Agent';
    case 'campaign_agent':
      return 'Campaign Agent';
    case 'data_ops_agent':
      return 'Data Ops Agent';
    default:
      return 'Mortgage Growth Agent';
  }
}

function shortHash(value: string | null | undefined): string {
  if (!value) return 'not recorded';
  return value.slice(0, 12);
}

export function traceDisplay(value: string | null | undefined): string {
  if (!value) return 'not recorded';
  const normalized = value.startsWith('agent-trace-') ? value.slice('agent-trace-'.length) : value;
  return normalized.length > 12 ? normalized.slice(-12) : normalized;
}

function governanceStatusLabel(status: GrowthAgentGovernanceChip['status']): string {
  switch (status) {
    case 'passed':
      return 'Passed';
    case 'review_required':
      return 'Review';
    case 'not_provisioned':
      return 'Not provisioned';
    case 'roadmap':
      return 'Roadmap';
    default:
      return 'Review';
  }
}

function toolStepIcon(status: GrowthAgentToolStep['status']): 'check' | 'audit' | 'cross' {
  if (status === 'completed') return 'check';
  if (status === 'blocked') return 'cross';
  return 'audit';
}

function policyStatusLabel(status: GrowthAgentPolicyCheck['status']): string {
  if (status === 'passed') return 'Passed';
  if (status === 'blocked') return 'Blocked';
  return 'Review';
}

function policyStatusVariant(status: GrowthAgentPolicyCheck['status']): 'success' | 'warning' | 'danger' {
  if (status === 'passed') return 'success';
  if (status === 'blocked') return 'danger';
  return 'warning';
}

interface GrowthAgentRunCardProps {
  run: GrowthAgentRunResponse;
  onOpenRoute: (route: string) => void;
  renderSourceAssetChip: (asset: string) => ReactNode;
}

export function GrowthAgentRunCard({
  run,
  onOpenRoute,
  renderSourceAssetChip,
}: GrowthAgentRunCardProps) {
  return (
    <section className="growth-agent-run" aria-label="Latest Growth Agent run">
      <div className="growth-agent-run__head">
        <div>
          <div className="eyebrow">Latest run</div>
          <div className="h-4">{run.workflow.title}</div>
        </div>
        <Button
          variant="success"
          icon="chevright"
          onClick={() => onOpenRoute(run.route)}
        >
          {run.workflow.action_label}
        </Button>
      </div>
      <div className="chip-row growth-agent-run__trace">
        <Chip variant="neutral" icon="sparkle">{specialistAgentLabel(run.specialist_agent)}</Chip>
        <Chip variant="neutral" icon="audit" title={run.trace_id ?? undefined}>
          Trace {traceDisplay(run.trace_id)}
        </Chip>
        <Chip variant="neutral" icon="link" title={run.tool_result_hash ?? undefined}>
          Hash {shortHash(run.tool_result_hash)}
        </Chip>
      </div>
      {run.interpreted_intent && (
        <div className="growth-agent-run__intent">
          {run.interpreted_intent}
        </div>
      )}
      <div className="growth-agent-run__metrics">
        <div>
          <span>{run.broad_label}</span>
          <strong>{formatGrowthAgentCount(run.broad_total)}</strong>
        </div>
        <div>
          <span>{run.actionable_label}</span>
          <strong>{formatGrowthAgentCount(run.actionable_total)}</strong>
        </div>
        <div>
          <span>Avg score</span>
          <strong>{run.actionable_avg_score ?? '—'}</strong>
        </div>
      </div>
      <div className="growth-agent-run__body">
        <div className="growth-agent-run__section">
          <div className="eyebrow">Tool timeline</div>
          <div className="growth-agent-timeline">
            {run.tool_steps.map((step) => (
              <div key={step.label} className={`growth-agent-step growth-agent-step--${step.status}`}>
                <Icon name={toolStepIcon(step.status)} size={12} />
                <div>
                  <div className="growth-agent-step__title">{step.label}</div>
                  <div className="growth-agent-step__detail">{step.detail}</div>
                  {(step.tool_name || step.result_hash) && (
                    <div className="growth-agent-step__meta">
                      {[step.tool_name, step.result_hash ? `hash ${shortHash(step.result_hash)}` : null]
                        .filter(Boolean)
                        .join(' · ')}
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
        <div className="growth-agent-run__section">
          <div className="eyebrow">Policy checks</div>
          <div className="growth-agent-policy-list">
            {run.policy_checks.map((check) => (
              <div key={check.label} className="growth-agent-policy">
                <Chip variant={policyStatusVariant(check.status)} icon="shield">
                  {policyStatusLabel(check.status)}
                </Chip>
                <div>
                  <div className="growth-agent-step__title">{check.label}</div>
                  <div className="growth-agent-step__detail">{check.detail}</div>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
      {run.governance_chips.length > 0 && (
        <section className="growth-agent-run__governance" aria-label="Growth Agent governance proof">
          <div className="eyebrow">Governance proof</div>
          <div className="growth-agent-governance-list">
            {run.governance_chips.map((chip) => (
              <div
                key={`${chip.label}-${chip.evidence_ref ?? ''}`}
                className="growth-agent-governance-item"
              >
                <Chip
                  variant={chip.status === 'passed' ? 'success' : 'warning'}
                  icon="shield"
                  title={chip.detail}
                >
                  {governanceStatusLabel(chip.status)}
                </Chip>
                <div>
                  <div className="growth-agent-step__title">{chip.label}</div>
                  <div className="growth-agent-step__detail">{chip.detail}</div>
                </div>
              </div>
            ))}
          </div>
        </section>
      )}
      <div className="chip-row growth-agent-run__assets">
        {run.source_assets.map((asset) => renderSourceAssetChip(asset))}
      </div>
    </section>
  );
}
