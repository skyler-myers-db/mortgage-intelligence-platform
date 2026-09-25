import { useId, type ReactNode } from 'react';
import { Button, Chip, SurfaceTitle } from '../components/Primitives';
import { Icon } from '../components/Icon';
import { formatStepParams, type SegmentLabeler } from './ask-genie.compose-plan-params';
import { CUSTOM_SEGMENTS } from './ask-genie.growth-agent.helpers';
import {
  DATABRICKS_AGENT_RESPONSES_LABEL,
  formatGrowthAgentCount,
  publicAgentResponsesText,
} from './ask-genie.growth-run-card';
import type {
  ComposePlanResponse,
  GrowthAgentWorkflow,
  PlanStepTrace,
} from '../types';

function traceStepIcon(status: PlanStepTrace['status']): 'check' | 'audit' | 'cross' {
  if (status === 'completed') return 'check';
  if (status === 'blocked') return 'cross';
  return 'audit';
}

function traceStepChipVariant(
  status: PlanStepTrace['status'],
): 'success' | 'warning' | 'danger' | 'neutral' {
  if (status === 'completed') return 'success';
  if (status === 'blocked') return 'danger';
  if (status === 'review_required') return 'warning';
  return 'neutral';
}

function traceStepStatusLabel(status: PlanStepTrace['status']): string {
  switch (status) {
    case 'completed':
      return 'Completed';
    case 'review_required':
      return 'Review required';
    case 'blocked':
      return 'Blocked';
    default:
      return 'Pending';
  }
}

function shortHash(value: string | null | undefined): string {
  if (!value) return 'not recorded';
  return value.slice(0, 12);
}

const SEGMENT_LABELS: ReadonlyMap<string, string> = new Map(
  CUSTOM_SEGMENTS.map((segment) => [segment.code, segment.label]),
);
const segmentLabel: SegmentLabeler = (code) => SEGMENT_LABELS.get(code) ?? code;

/** Run controls for a composed, unexecuted plan (audit 2026-09-21 `critic-01`). */
export interface ComposePlanRunControls {
  pending: boolean;
  /** The server answered 409: the plan changed, expired or no longer passes review. */
  conflict: boolean;
  errorMessage: string | null;
  /** Post the displayed plan and its digest. */
  onRun: () => void;
  /** Compose the objective again, to review the current plan. */
  onComposeAgain: () => void;
}

interface ComposePlanCardProps {
  response: ComposePlanResponse;
  onOpenRoute?: (route: string) => void;
  renderSourceAssetChip?: (asset: string) => ReactNode;
  run?: ComposePlanRunControls;
}

function runHint(stepCount: number, signed: boolean): string {
  if (!signed) {
    return 'This plan can be reviewed here but not run: this deployment is missing a required security setting. Ask an administrator.';
  }
  const steps = stepCount === 1 ? 'the step above' : `the ${stepCount} steps above`;
  return `Runs exactly ${steps} with the inputs shown. Read-only: it counts and checks, stops at the first approval gate and sends nothing.`;
}

/**
 * Run the plan the card shows (audit 2026-09-21 `critic-01`): the Button
 * posts exactly this plan and its server digest, and nothing renders as run
 * until the server answers. A 409 means nothing ran; the lender composes
 * again and reviews the current plan. After a run, the trace replaces this
 * row: there is no re-run from the same card.
 */
function ComposePlanRunRow({ run, stepCount, signed }: { run: ComposePlanRunControls; stepCount: number; signed: boolean }) {
  const hintId = useId();
  return (
    <div className="growth-agent-run__section mt-3">
      {run.conflict && (
        <div className="status-callout status-callout--danger" role="alert">
          This plan was not run. It changed, expired or no longer passes review since it was composed. Compose it
          again to review the current plan.
        </div>
      )}
      {!run.conflict && run.errorMessage && (
        <div className="status-callout status-callout--danger" role="alert">
          {run.errorMessage}
        </div>
      )}
      <div className="growth-agent-card__actions mt-3">
        <Button
          variant="primary"
          size="sm"
          icon="play"
          onClick={run.onRun}
          disabled={!signed || run.pending || run.conflict}
          aria-describedby={hintId}
        >
          {run.pending ? 'Running…' : 'Run this plan'}
        </Button>
        {run.conflict && (
          <Button variant="ghost" size="sm" icon="sparkle" onClick={run.onComposeAgain}>
            Compose again
          </Button>
        )}
        <span className="sr-only" role="status">
          {run.pending ? 'Running the plan you reviewed.' : ''}
        </span>
      </div>
      <p id={hintId} className="growth-agent__hint mt-2">
        {runHint(stepCount, signed)}
      </p>
    </div>
  );
}

export function ComposePlanCard({
  response,
  onOpenRoute,
  renderSourceAssetChip,
  run,
}: ComposePlanCardProps) {
  const { status, plan } = response;

  return (
    <section className="growth-agent-run" aria-label="Composed Growth Agent plan">
      <div className="growth-agent-run__head">
        <div>
          <div className="eyebrow">Composed plan</div>
          <SurfaceTitle level={3}>
            {publicAgentResponsesText(
              plan ? plan.objective_summary : response.message ?? 'Plan unavailable',
            )}
          </SurfaceTitle>
        </div>
        {status === 'composed' && (
          <Chip variant={response.executed ? 'success' : 'neutral'} icon="sparkle">
            {response.executed ? 'Executed' : 'Composed'}
          </Chip>
        )}
      </div>

      <div className="chip-row growth-agent-run__trace">
        <Chip variant="warning" icon="sparkle" title={response.model_endpoint ?? undefined}>
          Model-composed ({DATABRICKS_AGENT_RESPONSES_LABEL}
          {response.model_endpoint ? `, endpoint=${response.model_endpoint}` : ''})
        </Chip>
        {status === 'composed' && plan && (
          <Chip variant={plan.requires_approval ? 'warning' : 'success'} icon="shield">
            {plan.requires_approval ? 'Requires approval' : 'No approval gate'}
          </Chip>
        )}
        {status === 'degraded' && (
          <Chip variant="warning" icon="audit">Degraded — reviewed fallback</Chip>
        )}
        {status === 'invalid' && (
          <Chip variant="danger" icon="cross">Invalid request</Chip>
        )}
      </div>

      {response.interpreted_intent && (
        <div className="growth-agent-run__intent">
          {publicAgentResponsesText(response.interpreted_intent)}
        </div>
      )}
      {response.reasoning_summary && (
        <div className="growth-agent-run__intent">
          {publicAgentResponsesText(response.reasoning_summary)}
        </div>
      )}

      {status !== 'composed' && response.message && (
        <div
          className={`status-callout ${status === 'invalid' ? 'status-callout--danger' : 'status-callout--warning'} mt-3`}
          role={status === 'invalid' ? 'alert' : 'status'}
        >
          {publicAgentResponsesText(response.message)}
          {response.degraded_reason
            ? ` (${publicAgentResponsesText(response.degraded_reason)})`
            : ''}
        </div>
      )}

      {status === 'composed' && plan && (
        <>
          {response.approval_required && (
            <div className="status-callout status-callout--warning mt-3" role="status">
              Human approval required before execution continues
              {response.approval_gate_step_id ? ` at step ${response.approval_gate_step_id}` : ''}.
              No outreach is sent automatically.
            </div>
          )}

          <div className="growth-agent-run__section">
            <div className="eyebrow">Plan steps</div>
            <div className="growth-agent-timeline">
              {plan.steps.map((step, index) => (
                <div key={step.step_id} className="growth-agent-step">
                  <Icon name="sparkle" size={12} />
                  <div>
                    <div className="growth-agent-step__title">
                      {index + 1}. {publicAgentResponsesText(step.tool)}
                    </div>
                    <div className="growth-agent-step__detail">
                      {publicAgentResponsesText(step.rationale)}
                    </div>
                    <div className="growth-agent-step__meta">{formatStepParams(step.params, segmentLabel)}</div>
                    <div className="growth-agent-step__meta">step {step.step_id}</div>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {plan.expected_outcome && (
            <div className="growth-agent-run__intent">
              Expected: {publicAgentResponsesText(plan.expected_outcome)}
            </div>
          )}
          {plan.risk_notes && (
            <div className="growth-agent-run__intent">
              Risk: {publicAgentResponsesText(plan.risk_notes)}
            </div>
          )}

          {run && !response.executed && (
            <ComposePlanRunRow run={run} stepCount={plan.steps.length} signed={Boolean(response.plan_digest)} />
          )}

          {response.executed && response.trace.length > 0 && (
            <div className="growth-agent-run__section">
              <div className="eyebrow">Execution trace</div>
              <div className="growth-agent-timeline">
                {response.trace.map((step) => {
                  const isGate =
                    step.approval_gate || step.step_id === response.approval_gate_step_id;
                  return (
                    <div
                      key={step.step_id}
                      className={`growth-agent-step growth-agent-step--${step.status}`}
                    >
                      <Icon name={traceStepIcon(step.status)} size={12} />
                      <div>
                        <div className="growth-agent-step__title">
                          {publicAgentResponsesText(step.label)}
                        </div>
                        <div className="growth-agent-step__detail">
                          {publicAgentResponsesText(step.detail)}
                        </div>
                        <div className="chip-row">
                          <Chip variant={traceStepChipVariant(step.status)} icon="audit">
                            {traceStepStatusLabel(step.status)}
                          </Chip>
                          {isGate && (
                            <Chip variant="warning" icon="shield">Approval gate</Chip>
                          )}
                        </div>
                        <div className="growth-agent-step__meta">
                          {[
                            step.tool,
                            step.row_summary !== null
                              ? `${formatGrowthAgentCount(step.row_summary)} rows`
                              : null,
                            step.result_hash ? `hash ${shortHash(step.result_hash)}` : null,
                            step.audit_event_id ? `audit ${shortHash(step.audit_event_id)}` : null,
                          ]
                            .filter(Boolean)
                            .join(' · ')}
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </>
      )}

      {status === 'degraded' && response.fallback_workflows.length > 0 && (
        <div className="growth-agent-run__section">
          <div className="eyebrow">Reviewed fallback workflows</div>
          <div className="growth-agent-timeline">
            {response.fallback_workflows.map((workflow: GrowthAgentWorkflow) => (
              <div key={workflow.id} className="growth-agent-step">
                <Icon name="audit" size={12} />
                <div>
                  <div className="growth-agent-step__title">{workflow.title}</div>
                  <div className="growth-agent-step__detail">{workflow.objective}</div>
                  <div className="growth-agent-step__meta">
                    Reviewed catalog workflow (fallback)
                  </div>
                  {onOpenRoute && (
                    <Button
                      variant="ghost"
                      size="sm"
                      icon="chevright"
                      onClick={() => onOpenRoute(workflow.default_route)}
                    >
                      {workflow.action_label}
                    </Button>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {renderSourceAssetChip && plan && (
        <div className="chip-row growth-agent-run__assets">
          {Array.from(
            new Set(response.trace.map((step) => step.source_asset).filter((asset): asset is string => Boolean(asset))),
          ).map((asset) => renderSourceAssetChip(asset))}
        </div>
      )}
    </section>
  );
}
