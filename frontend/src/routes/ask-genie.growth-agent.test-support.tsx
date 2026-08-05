/**
 * @vitest-environment happy-dom
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, vi } from 'vitest';
import type {
  GenieStartResult,
  GrowthAgentHomeResponse,
  GrowthAgentRunResponse,
  GrowthAgentWorkflow,
} from '../types';
import type { GrowthAgentCapabilityRow } from '../types/growthAgent';

export const growthAgent = vi.fn();
export const growthAgentCapabilities = vi.fn();
export const genieStart = vi.fn();
export const genie = vi.fn();
export const genieAction = vi.fn();
export const runGrowthAgentWorkflow = vi.fn();
export const runCustomGrowthAgentWorkflow = vi.fn();
export const runMortgageGrowthAgent = vi.fn();
export const rerunGrowthAgentMonitor = vi.fn();
export const createGrowthAgentMonitorNotificationDrafts = vi.fn();
export const navigate = vi.fn();
export const setDrawer = vi.fn();
export const refreshWorkspace = vi.fn();

vi.mock('../lib/api', () => ({
  api: {
    growthAgent: (...args: unknown[]) => growthAgent(...args),
    growthAgentCapabilities: (...args: unknown[]) => growthAgentCapabilities(...args),
    genieStart: (...args: unknown[]) => genieStart(...args),
    genie: (...args: unknown[]) => genie(...args),
    genieAction: (...args: unknown[]) => genieAction(...args),
    runGrowthAgentWorkflow: (...args: unknown[]) => runGrowthAgentWorkflow(...args),
    runCustomGrowthAgentWorkflow: (...args: unknown[]) => runCustomGrowthAgentWorkflow(...args),
    runMortgageGrowthAgent: (...args: unknown[]) => runMortgageGrowthAgent(...args),
    rerunGrowthAgentMonitor: (...args: unknown[]) => rerunGrowthAgentMonitor(...args),
    createGrowthAgentMonitorNotificationDrafts: (...args: unknown[]) => (
      createGrowthAgentMonitorNotificationDrafts(...args)
    ),
  },
  ApiError: class ApiError extends Error {
    status = 500;
  },
  isAbortError: () => false,
  isWarmingUpError: () => false,
}));

vi.mock('../components/AppContext', () => ({
  useApp: () => ({
    refreshWorkspace,
    setDrawer,
    showEvidence: true,
    showConfidence: true,
  }),
}));

vi.mock('react-router', async () => {
  const actual = await vi.importActual<typeof import('react-router')>('react-router');
  return { ...actual, useNavigate: () => navigate };
});

import AskGenie from './ask-genie';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
vi.setConfig({ testTimeout: 20_000, hookTimeout: 20_000 });

export const WORKFLOW: GrowthAgentWorkflow = {
  id: 'daily_refi_brief',
  title: 'Daily Refi Opportunity Brief',
  objective: 'Find borrowers with rate-spread economics worth reviewing today.',
  trigger_label: 'Prime refinance economics',
  action_label: 'Open eligible refi subset',
  source_assets: [
    'mip.gold.borrower_360',
    'mip.gold.lead_population',
    'mip.gold.evidence_events',
  ],
  default_route: '/lead-queue?segment=itm&marketing_eligibility=Eligible+only',
  proof_points: [
    'Broad count uses borrower_360.in_the_money.',
    'Actionable count requires Lead Queue eligibility and opt-in.',
    'The route opens only the eligible Prime Refi Candidate subset.',
  ],
  cadence_options: ['daily', 'weekly'],
};

export const BORROWER_DOSSIER_WORKFLOW: GrowthAgentWorkflow = {
  id: 'borrower_dossier_review',
  title: 'Borrower Dossier Review',
  objective: 'Prepare the top-opportunity borrower story queue for human review.',
  trigger_label: 'Top opportunity dossier signals',
  action_label: 'Open top-opportunity dossier queue',
  source_assets: [
    'mip.gold.borrower_360',
    'mip.gold.borrower_dossier',
    'mip.gold.evidence_events',
  ],
  default_route: '/lead-queue?funnel_stage=high_opportunity&marketing_eligibility=Eligible+only',
  proof_points: [
    'Dossier evidence comes from governed borrower assets.',
    'The handoff uses the high-opportunity Lead Queue stage.',
  ],
  cadence_options: ['daily', 'weekly'],
};

function capability(
  key: GrowthAgentCapabilityRow['key'],
  label: string,
  status: GrowthAgentCapabilityRow['status'],
  detail: string,
) {
  return { key, label, ga: true, status, claimable: false, detail };
}

export const HOME: GrowthAgentHomeResponse = {
  workflows: [WORKFLOW, BORROWER_DOSSIER_WORKFLOW],
  monitors: [],
  capabilities: [
    capability('genie_conversation_api', 'Genie Conversation API', 'configured', 'Genie Conversation API dependencies are configured; a live Genie probe must pass before this row is claimable.'),
    capability('certified_metric_views', 'UC metric-view certification', 'configured', 'Certified metric-view SQL contracts are bundled; live UC deployment must be verified before claiming them active.'),
    capability('uc_function_tools', 'Application-reviewed SQL tools', 'configured', 'Reviewed UC-function SQL contracts are bundled; live registration must be verified before claiming them active.'),
    capability('agent_orchestrator', 'Agent Framework orchestration', 'not_provisioned', 'Disabled or missing agent framework libraries.'),
    capability('ai_gateway', 'Unity AI Gateway governance', 'not_provisioned', 'Gateway endpoint/inference-table config is missing.'),
    capability('lakebase_sync', 'Lakebase synced-table serving', 'not_provisioned', 'Disabled; reads stay on the warehouse path.'),
    capability('agent_eval', 'MLflow Agent Evaluation', 'not_provisioned', 'MLflow evaluation is not configured.'),
  ],
};

export const START: GenieStartResult = {
  conversation_id: null,
  trusted_assets: ['mip.gold.borrower_360'],
  sample_questions: [],
};

export const RUN: GrowthAgentRunResponse = {
  workflow: WORKFLOW,
  run_id: '11111111-1111-4111-8111-111111111111',
  specialist_agent: 'structured_data_agent',
  execution_mode: 'deterministic',
  trace_kind: 'local_hash',
  planner_label: 'Reviewed deterministic planner',
  trace_id: 'agent-trace-11111111-1111-4111-8111-111111111111',
  tool_result_hash: 'a'.repeat(64),
  broad_label: 'Broad opportunity',
  actionable_label: 'Eligible subset',
  broad_total: 117404,
  actionable_total: 5394,
  broad_avg_score: 64.2,
  actionable_avg_score: 73.1,
  avg_rate_spread_bps: 187.9,
  avg_equity_pct: 42.4,
  route: '/lead-queue?segment=itm&marketing_eligibility=Eligible+only&states=IL%2CTX',
  criteria: {
    states: ['IL', 'TX'],
    lead_queue_filters: {
      segment_codes: ['itm'],
      segment_mode: 'any',
    },
  },
  source_assets: ['mip.gold.borrower_360', 'mip.gold.lead_population'],
  tool_steps: [
    {
      label: 'Read trusted borrower signals',
      status: 'completed',
      detail: 'Found 117,404 borrowers in the broad opportunity screen.',
      source_asset: 'mip.gold.borrower_360',
      tool_name: 'fn_build_cohort',
      result_hash: 'a'.repeat(64),
    },
  ],
  policy_checks: [
    {
      label: 'Broad vs actionable reconciliation',
      status: 'passed',
      detail: '117,404 broad opportunities reconcile to 5,394 eligible leads.',
    },
  ],
  governance_chips: [
    {
      label: 'Masked references only',
      status: 'passed',
      detail: 'The run returns counts and route filters only.',
      evidence_ref: 'agent-trace-11111111-1111-4111-8111-111111111111',
    },
  ],
  interpreted_intent: 'Reviewed deterministic planner selected the daily refi opportunity brief.',
  agent_reasoning: 'Reviewed keyword routing selected a governed SQL workflow.',
  genie_trusted_assets: [],
  audit_event_id: 'audit-11111111-1111-4111-8111-111111111111',
};

export function setNativeValue(el: HTMLInputElement | HTMLSelectElement, value: string) {
  const prototype = el instanceof HTMLSelectElement
    ? HTMLSelectElement.prototype
    : HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(prototype, 'value')?.set;
  if (!setter) throw new Error('missing value setter');
  setter.call(el, value);
  el.dispatchEvent(new Event('input', { bubbles: true }));
  el.dispatchEvent(new Event('change', { bubbles: true }));
}


export let container: HTMLDivElement;
let root: Root;

export function registerGrowthAgentRoutePanelHooks() {
  beforeEach(() => {
    vi.resetAllMocks();
    growthAgent.mockResolvedValue(HOME);
    growthAgentCapabilities.mockResolvedValue(HOME);
    genieStart.mockResolvedValue(START);
    genie.mockResolvedValue(null);
    genieAction.mockResolvedValue(null);
    runGrowthAgentWorkflow.mockResolvedValue(RUN);
    runMortgageGrowthAgent.mockResolvedValue(RUN);
    rerunGrowthAgentMonitor.mockResolvedValue({
      ...RUN,
      planner_label: 'Saved watchlist runner',
      interpreted_intent: 'Saved watchlist re-run: Mortgage Growth Agent - IL.',
      monitor: {
        monitor_id: '22222222-2222-4222-8222-222222222222',
        workflow_id: 'daily_refi_brief',
        name: 'Mortgage Growth Agent - IL',
        cadence: 'weekly',
        status: 'active',
        criteria: RUN.criteria,
        route: RUN.route,
        actionable_total: RUN.actionable_total,
        source_assets: RUN.source_assets,
        last_run_id: RUN.run_id,
      },
    });
    createGrowthAgentMonitorNotificationDrafts.mockResolvedValue([
      {
        draft_id: 'draft-slack',
        monitor_id: '22222222-2222-4222-8222-222222222222',
        run_id: RUN.run_id,
        channel: 'slack',
        title: 'Mortgage Growth Agent - IL: 5,394 eligible',
        body: '5,394 eligible borrowers in Mortgage Growth Agent - IL. Review: /lead-queue?segment=itm',
        generation_mode: 'supervisor',
        generator_label: 'Supervisor-composed notification',
        status: 'draft',
      },
      {
        draft_id: 'draft-teams',
        monitor_id: '22222222-2222-4222-8222-222222222222',
        run_id: RUN.run_id,
        channel: 'teams',
        title: 'Operations brief: Mortgage Growth Agent - IL',
        body: [
          'Operations brief',
          'Watchlist: Mortgage Growth Agent - IL',
          'Eligible population: 5,394 borrowers',
          'Operator action: Review the current watchlist and confirm the Lead Queue handoff.',
          'MIP route: /lead-queue?segment=itm',
        ].join('\n'),
        status: 'draft',
      },
    ]);
    runCustomGrowthAgentWorkflow.mockResolvedValue({
      ...RUN,
      workflow: {
        ...WORKFLOW,
        id: 'custom_segment_watch',
        title: 'Custom Segment Workflow',
        trigger_label: 'Prime Refi Candidates or Listed for Sale segment screen',
        action_label: 'Open eligible custom subset',
      },
      specialist_agent: 'campaign_agent',
      route: '/lead-queue?segment_codes=itm%2Clisted&segment_mode=any&marketing_eligibility=Eligible+only&states=IL%2CTX',
      criteria: {
        states: ['IL', 'TX'],
        lead_queue_filters: {
          segment_codes: ['itm', 'listed'],
          segment_mode: 'any',
        },
      },
      policy_checks: [
        {
          label: 'Reviewed custom workflow',
          status: 'passed',
          detail: 'Custom workflow criteria are reviewed segment codes and explicit Any/All mode only.',
        },
      ],
      interpreted_intent: 'Campaign lens built a custom ANY segment workflow.',
    });
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });
}

export function mount() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  act(() => {
    root.render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/ask-genie']}>
          <AskGenie />
        </MemoryRouter>
      </QueryClientProvider>,
    );
  });
}

export async function waitUntil(cond: () => boolean, ms = 10_000) {
  const start = Date.now();
  while (!cond()) {
    if (Date.now() - start > ms) throw new Error('waitUntil timeout');
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 10));
    });
  }
}

export function stateInput() {
  const input = container.querySelector<HTMLInputElement>(
    'input[aria-label="Growth Agent state scope"]',
  );
  if (!input) throw new Error('state input not rendered');
  return input;
}

export function button(name: RegExp) {
  const candidates = Array.from(container.querySelectorAll<HTMLButtonElement>('button'));
  const match = candidates.find((candidate) => name.test(candidate.textContent ?? ''));
  if (!match) throw new Error(`button not rendered: ${name}`);
  return match;
}
