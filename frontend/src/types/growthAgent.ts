export type GrowthAgentWorkflowId =
  | 'daily_refi_brief'
  | 'borrower_dossier_review'
  | 'listing_watch'
  | 'competitor_recapture_monitor'
  | 'high_equity_heloc_watch'
  | 'branch_capacity_review'
  | 'source_freshness_sentinel'
  | 'custom_segment_watch';

export type GrowthAgentCadence = 'daily' | 'weekly';
export type GrowthAgentSegmentCode = 'itm' | 'listed' | 'permit' | 'investor' | 'equity' | 'retention';
export type GrowthAgentSegmentMode = 'any' | 'all';
export type GrowthAgentNotificationChannel = 'slack' | 'teams';
export type GrowthAgentSpecialist =
  | 'structured_data_agent'
  | 'borrower_dossier_agent'
  | 'offer_agent'
  | 'compliance_agent'
  | 'campaign_agent'
  | 'data_ops_agent';
export type GrowthAgentExecutionMode = 'deterministic' | 'genie_conversation' | 'agent_framework';
export type GrowthAgentTraceKind = 'local_hash' | 'genie_conversation' | 'agent_framework' | 'mlflow_trace';

export interface GrowthAgentWorkflow {
  id: GrowthAgentWorkflowId;
  title: string;
  objective: string;
  trigger_label: string;
  action_label: string;
  source_assets: string[];
  default_route: string;
  proof_points: string[];
  cadence_options: GrowthAgentCadence[];
}

export interface GrowthAgentToolStep {
  label: string;
  status: 'completed' | 'blocked' | 'review_required';
  detail: string;
  source_asset?: string | null;
  tool_name?: string | null;
  result_hash?: string | null;
}

export interface GrowthAgentPolicyCheck {
  label: string;
  status: 'passed' | 'review_required' | 'blocked';
  detail: string;
}

export interface GrowthAgentGovernanceChip {
  label: string;
  status: 'passed' | 'review_required' | 'roadmap' | 'not_provisioned' | 'not_attached';
  detail: string;
  evidence_ref?: string | null;
}

export interface GrowthAgentMonitor {
  monitor_id: string;
  workflow_id: GrowthAgentWorkflowId;
  name: string;
  cadence: GrowthAgentCadence;
  status: 'active' | 'paused' | 'disabled';
  criteria: Record<string, unknown>;
  route: string;
  actionable_total: number;
  source_assets: string[];
  last_run_id?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface GrowthAgentRunRequest {
  states?: string[];
  save_monitor?: boolean;
  cadence?: GrowthAgentCadence;
  monitor_name?: string | null;
  request_id?: string | null;
}

export interface GrowthAgentMonitorDraftRequest {
  channels?: GrowthAgentNotificationChannel[];
  request_id?: string | null;
}

export interface GrowthAgentDueMonitorRunRequest extends GrowthAgentMonitorDraftRequest {
  limit?: number;
}

export interface GrowthAgentCustomRunRequest extends GrowthAgentRunRequest {
  segment_codes: GrowthAgentSegmentCode[];
  segment_mode: GrowthAgentSegmentMode;
}

export interface GrowthAgentPromptRunRequest extends GrowthAgentRunRequest {
  prompt: string;
  segment_codes?: GrowthAgentSegmentCode[];
  segment_mode?: GrowthAgentSegmentMode;
}

export interface GrowthAgentRunResponse {
  workflow: GrowthAgentWorkflow;
  run_id: string;
  monitor?: GrowthAgentMonitor | null;
  specialist_agent: GrowthAgentSpecialist;
  execution_mode: GrowthAgentExecutionMode;
  trace_kind: GrowthAgentTraceKind;
  planner_label: string;
  trace_id: string;
  tool_result_hash: string;
  actionable_cohort_fingerprint?: string | null;
  actionable_snapshot_id?: string | null;
  broad_label: string;
  actionable_label: string;
  broad_total: number;
  actionable_total: number;
  broad_avg_score?: number | null;
  actionable_avg_score?: number | null;
  avg_rate_spread_bps?: number | null;
  avg_equity_pct?: number | null;
  route: string;
  criteria: Record<string, unknown>;
  source_assets: string[];
  tool_steps: GrowthAgentToolStep[];
  policy_checks: GrowthAgentPolicyCheck[];
  governance_chips: GrowthAgentGovernanceChip[];
  interpreted_intent?: string | null;
  agent_reasoning?: string | null;
  genie_conversation_id?: string | null;
  genie_message_id?: string | null;
  genie_question_hash?: string | null;
  genie_sql_hash?: string | null;
  genie_row_count?: number | null;
  genie_trusted_assets: string[];
  audit_event_id?: string | null;
  created_at?: string | null;
}

export interface ComposePlanRequest {
  objective: string;
  execute?: boolean;
  states?: string[];
  request_id?: string | null;
}

export interface PlanStep {
  step_id: string;
  tool: string;
  params: Record<string, unknown>;
  rationale: string;
}

export interface ComposedPlan {
  objective_summary: string;
  steps: PlanStep[];
  expected_outcome: string;
  risk_notes: string;
  requires_approval: boolean;
}

export interface PlanStepTrace {
  step_id: string;
  tool: string;
  label: string;
  status: 'pending' | 'completed' | 'review_required' | 'blocked';
  detail: string;
  duration_ms: number;
  row_summary: number | null;
  result_hash: string | null;
  source_asset: string | null;
  approval_gate: boolean;
  audit_event_id: string | null;
}

export interface ComposePlanResponse {
  status: 'composed' | 'degraded' | 'invalid';
  planner: 'supervisor_composed';
  model_endpoint: string | null;
  plan: ComposedPlan | null;
  trace: PlanStepTrace[];
  approval_required: boolean;
  approval_gate_step_id: string | null;
  executed: boolean;
  plan_id: string | null;
  interpreted_intent: string | null;
  reasoning_summary: string | null;
  degraded_reason: string | null;
  message: string | null;
  fallback_workflows: GrowthAgentWorkflow[];
  audit_event_ids: string[];
}

export interface GrowthAgentNotificationDraft {
  draft_id: string;
  monitor_id: string;
  run_id: string;
  channel: GrowthAgentNotificationChannel;
  title: string;
  body: string;
  generation_mode?: 'supervisor' | 'governed_fallback';
  generator_label?: string;
  strategy_summary?: string;
  status: 'draft' | 'reviewed' | 'cancelled';
  created_at?: string | null;
  updated_at?: string | null;
}

export interface GrowthAgentCapabilityRow {
  key: string;
  label: string;
  ga: boolean;
  status: 'available' | 'configured' | 'not_provisioned' | 'preview_mirror' | 'hidden';
  claimable: boolean;
  detail: string;
}

export interface GrowthAgentHomeResponse {
  workflows: GrowthAgentWorkflow[];
  monitors: GrowthAgentMonitor[];
  capabilities?: GrowthAgentCapabilityRow[];
}

export interface GrowthAgentDueMonitorRunResponse {
  runs: GrowthAgentRunResponse[];
  drafts: GrowthAgentNotificationDraft[];
  due_count: number;
}
