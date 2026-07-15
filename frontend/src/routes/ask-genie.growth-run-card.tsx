import type { ReactNode } from 'react';
import { Button, Chip } from '../components/Primitives';
import { Icon } from '../components/Icon';
import type {
  GrowthAgentGovernanceChip,
  GrowthAgentPolicyCheck,
  GrowthAgentRunResponse,
  GrowthAgentToolStep,
} from '../types';

export const DATABRICKS_AGENT_RESPONSES_LABEL = 'Databricks Agent Responses';

export function publicAgentResponsesText(value: string | null | undefined): string {
  if (!value) return '';
  switch (value.trim()) {
    case 'Supervisor-composed notification':
    case 'Databricks Agent Responses endpoint':
    case 'Agent Responses endpoint':
    case 'Databricks Supervisor Agent':
    case 'Supervisor Agent':
    case 'Multi-agent framework':
    case 'Agent framework':
    case 'agent_framework_supervisor':
      return DATABRICKS_AGENT_RESPONSES_LABEL;
    default:
      return value;
  }
}

function publicToolName(value: string | null | undefined): string | null {
  if (!value) return null;
  return publicAgentResponsesText(value);
}

export function formatGrowthAgentCount(value: number | null | undefined): string {
  return new Intl.NumberFormat('en-US').format(Math.max(0, Number(value ?? 0)));
}

function workflowOwnerLabel(agent: GrowthAgentRunResponse['specialist_agent']): string {
  switch (agent) {
    case 'structured_data_agent':
      return 'Structured data lens';
    case 'borrower_dossier_agent':
      return 'Borrower dossier lens';
    case 'offer_agent':
      return 'Offer lens';
    case 'compliance_agent':
      return 'Compliance lens';
    case 'campaign_agent':
      return 'Campaign lens';
    case 'data_ops_agent':
      return 'Data operations lens';
    default:
      return 'Reviewed mortgage lens';
  }
}

function shortHash(value: string | null | undefined): string {
  if (!value) return 'not recorded';
  return value.slice(0, 12);
}

function hasGrowthAgentCohortProof(run: GrowthAgentRunResponse): boolean {
  let route: URL;
  try {
    route = new URL(run.route, 'https://mortgage-intelligence.local');
  } catch {
    return false;
  }
  return (
    route.origin === 'https://mortgage-intelligence.local'
    && route.pathname === '/lead-queue'
    && Boolean(route.searchParams.get('growth_handoff')?.trim())
    && /^[A-Za-z0-9_-]{8,128}$/.test(run.run_id)
    && Number.isSafeInteger(run.actionable_total)
    && run.actionable_total >= 0
    && /^[0-9a-f]{64}$/.test(run.tool_result_hash)
    && /^[0-9a-f]{64}$/.test(run.actionable_cohort_fingerprint ?? '')
    && Boolean(run.actionable_snapshot_id?.trim())
  );
}

export function growthAgentActionRoute(run: GrowthAgentRunResponse): string {
  if (!hasGrowthAgentCohortProof(run)) return run.route;
  const url = new URL(run.route, 'https://mortgage-intelligence.local');
  if (url.origin !== 'https://mortgage-intelligence.local' || url.pathname !== '/lead-queue') {
    return run.route;
  }
  url.searchParams.set('growth_agent_run_id', run.run_id);
  url.searchParams.set('actionable_total', String(run.actionable_total));
  url.searchParams.set('tool_result_hash', run.tool_result_hash);
  url.searchParams.set('actionable_cohort_fingerprint', run.actionable_cohort_fingerprint!);
  url.searchParams.set('actionable_snapshot_id', run.actionable_snapshot_id!);
  return `${url.pathname}${url.search}${url.hash}`;
}

export function traceDisplay(value: string | null | undefined): string {
  if (!value) return 'not recorded';
  const normalized = value.startsWith('agent-trace-') ? value.slice('agent-trace-'.length) : value;
  return normalized.length > 12 ? normalized.slice(-12) : normalized;
}

function executionModeLabel(run: GrowthAgentRunResponse): string {
  if (run.execution_mode === 'genie_conversation') return 'Genie Conversation';
  if (run.execution_mode === 'agent_framework') return DATABRICKS_AGENT_RESPONSES_LABEL;
  return 'Reviewed workflow';
}

function traceLabel(run: GrowthAgentRunResponse): string {
  if (run.trace_kind === 'mlflow_trace') return 'MLflow trace';
  if (run.trace_kind === 'genie_conversation') return 'Genie message';
  if (run.trace_kind === 'agent_framework') return DATABRICKS_AGENT_RESPONSES_LABEL;
  return 'Run correlation';
}

function governanceStatusLabel(status: GrowthAgentGovernanceChip['status']): string {
  switch (status) {
    case 'passed':
      return 'Passed';
    case 'review_required':
      return 'Review';
    case 'not_provisioned':
      return 'Not provisioned';
    case 'not_attached':
      return 'Not attached';
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
  const cohortProofAttached = hasGrowthAgentCohortProof(run);
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
          onClick={() => onOpenRoute(growthAgentActionRoute(run))}
        >
          {run.workflow.action_label}
        </Button>
      </div>
      <div className="chip-row growth-agent-run__trace">
        <Chip variant="neutral" icon="sparkle">{executionModeLabel(run)}</Chip>
        <Chip variant="neutral" icon="audit">Owner: {workflowOwnerLabel(run.specialist_agent)}</Chip>
        <Chip variant="neutral" icon="audit" title={run.trace_id ?? undefined}>
          {traceLabel(run)} {traceDisplay(run.trace_id)}
        </Chip>
        <Chip variant="neutral" icon="link" title={run.tool_result_hash ?? undefined}>
          Hash {shortHash(run.tool_result_hash)}
        </Chip>
        <Chip
          variant={cohortProofAttached ? 'success' : 'warning'}
          icon="shield"
          title={run.actionable_cohort_fingerprint ?? undefined}
        >
          {cohortProofAttached
            ? `Cohort proof ${shortHash(run.actionable_cohort_fingerprint)}`
            : 'Cohort proof unavailable'}
        </Chip>
        {run.actionable_snapshot_id && (
          <Chip variant="neutral" icon="audit" title={run.actionable_snapshot_id}>
            Snapshot {run.actionable_snapshot_id}
          </Chip>
        )}
        <Chip
          variant={run.audit_event_id ? 'success' : 'warning'}
          icon="audit"
          title={run.audit_event_id ?? undefined}
        >
          {run.audit_event_id ? `Audit ${shortHash(run.audit_event_id)}` : 'Audit pending'}
        </Chip>
      </div>
      <div className="growth-agent-run__intent">
        {publicAgentResponsesText(run.planner_label)}
        {run.genie_conversation_id ? ` · conversation ${shortHash(run.genie_conversation_id)}` : ''}
        {run.genie_row_count !== null && run.genie_row_count !== undefined
          ? ` · ${formatGrowthAgentCount(run.genie_row_count)} Genie rows`
          : ''}
      </div>
      {run.interpreted_intent && (
        <div className="growth-agent-run__intent">
          {publicAgentResponsesText(run.interpreted_intent)}
        </div>
      )}
      {run.agent_reasoning && (
        <div className="growth-agent-run__intent">
          {publicAgentResponsesText(run.agent_reasoning)}
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
                  <div className="growth-agent-step__title">
                    {publicAgentResponsesText(step.label)}
                  </div>
                  <div className="growth-agent-step__detail">
                    {publicAgentResponsesText(step.detail)}
                  </div>
                  {(step.tool_name || step.result_hash) && (
                    <div className="growth-agent-step__meta">
                      {[publicToolName(step.tool_name), step.result_hash ? `hash ${shortHash(step.result_hash)}` : null]
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
                  <div className="growth-agent-step__title">
                    {publicAgentResponsesText(check.label)}
                  </div>
                  <div className="growth-agent-step__detail">
                    {publicAgentResponsesText(check.detail)}
                  </div>
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
                  title={publicAgentResponsesText(chip.detail)}
                >
                  {governanceStatusLabel(chip.status)}
                </Chip>
                <div>
                  <div className="growth-agent-step__title">
                    {publicAgentResponsesText(chip.label)}
                  </div>
                  <div className="growth-agent-step__detail">
                    {publicAgentResponsesText(chip.detail)}
                  </div>
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
