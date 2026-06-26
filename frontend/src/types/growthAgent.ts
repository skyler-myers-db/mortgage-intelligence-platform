export type GrowthAgentWorkflowId =
  | 'daily_refi_brief'
  | 'listing_watch'
  | 'competitor_recapture_monitor'
  | 'high_equity_heloc_watch';

export type GrowthAgentCadence = 'daily' | 'weekly';

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
}

export interface GrowthAgentPolicyCheck {
  label: string;
  status: 'passed' | 'review_required' | 'blocked';
  detail: string;
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

export interface GrowthAgentRunResponse {
  workflow: GrowthAgentWorkflow;
  run_id: string;
  monitor?: GrowthAgentMonitor | null;
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
  audit_event_id?: string | null;
  created_at?: string | null;
}

export interface GrowthAgentHomeResponse {
  workflows: GrowthAgentWorkflow[];
  monitors: GrowthAgentMonitor[];
}
