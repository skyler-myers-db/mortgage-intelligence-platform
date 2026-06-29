/**
 * @vitest-environment happy-dom
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type {
  GenieStartResult,
  GrowthAgentHomeResponse,
  GrowthAgentRunResponse,
  GrowthAgentWorkflow,
} from '../types';

const growthAgent = vi.fn();
const genieStart = vi.fn();
const genie = vi.fn();
const genieAction = vi.fn();
const runGrowthAgentWorkflow = vi.fn();
const runCustomGrowthAgentWorkflow = vi.fn();
const runMortgageGrowthAgent = vi.fn();
const rerunGrowthAgentMonitor = vi.fn();
const navigate = vi.fn();
const setDrawer = vi.fn();
const refreshWorkspace = vi.fn();

vi.mock('../lib/api', () => ({
  api: {
    growthAgent: (...args: unknown[]) => growthAgent(...args),
    genieStart: (...args: unknown[]) => genieStart(...args),
    genie: (...args: unknown[]) => genie(...args),
    genieAction: (...args: unknown[]) => genieAction(...args),
    runGrowthAgentWorkflow: (...args: unknown[]) => runGrowthAgentWorkflow(...args),
    runCustomGrowthAgentWorkflow: (...args: unknown[]) => runCustomGrowthAgentWorkflow(...args),
    runMortgageGrowthAgent: (...args: unknown[]) => runMortgageGrowthAgent(...args),
    rerunGrowthAgentMonitor: (...args: unknown[]) => rerunGrowthAgentMonitor(...args),
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

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return { ...actual, useNavigate: () => navigate };
});

import AskGenie from './ask-genie';

const WORKFLOW: GrowthAgentWorkflow = {
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

const BORROWER_DOSSIER_WORKFLOW: GrowthAgentWorkflow = {
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

const HOME: GrowthAgentHomeResponse = {
  workflows: [WORKFLOW, BORROWER_DOSSIER_WORKFLOW],
  monitors: [],
  capabilities: [
    {
      key: 'genie_conversation_api',
      label: 'Genie Conversation API',
      ga: true,
      status: 'configured',
      claimable: false,
      detail: 'Genie Conversation API dependencies are configured; a live Genie probe must pass before this row is claimable.',
    },
    {
      key: 'certified_metric_views',
      label: 'UC metric-view certification',
      ga: true,
      status: 'configured',
      claimable: false,
      detail: 'Certified metric-view SQL contracts are bundled; live UC deployment must be verified before claiming them active.',
    },
    {
      key: 'uc_function_tools',
      label: 'Application-reviewed SQL tools',
      ga: true,
      status: 'configured',
      claimable: false,
      detail: 'Reviewed UC-function SQL contracts are bundled; live registration must be verified before claiming them active.',
    },
    {
      key: 'agent_orchestrator',
      label: 'Agent Framework orchestration',
      ga: true,
      status: 'not_provisioned',
      claimable: false,
      detail: 'Disabled or missing agent framework libraries.',
    },
    {
      key: 'ai_gateway',
      label: 'Unity AI Gateway governance',
      ga: true,
      status: 'not_provisioned',
      claimable: false,
      detail: 'Gateway endpoint/inference-table config is missing.',
    },
    {
      key: 'lakebase_sync',
      label: 'Lakebase synced-table serving',
      ga: true,
      status: 'not_provisioned',
      claimable: false,
      detail: 'Disabled; reads stay on the warehouse path.',
    },
    {
      key: 'agent_eval',
      label: 'MLflow Agent Evaluation',
      ga: true,
      status: 'not_provisioned',
      claimable: false,
      detail: 'MLflow evaluation is not configured.',
    },
  ],
};

const START: GenieStartResult = {
  conversation_id: null,
  trusted_assets: ['mip.gold.borrower_360'],
  sample_questions: [],
};

const RUN: GrowthAgentRunResponse = {
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
      label: 'PII-safe output',
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

function setNativeValue(el: HTMLInputElement | HTMLSelectElement, value: string) {
  const prototype = el instanceof HTMLSelectElement
    ? HTMLSelectElement.prototype
    : HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(prototype, 'value')?.set;
  if (!setter) throw new Error('missing value setter');
  setter.call(el, value);
  el.dispatchEvent(new Event('input', { bubbles: true }));
  el.dispatchEvent(new Event('change', { bubbles: true }));
}

describe('AskGenie Growth Agent route panel', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.clearAllMocks();
    growthAgent.mockResolvedValue(HOME);
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

  function mount() {
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

  async function waitUntil(cond: () => boolean, ms = 4000) {
    const start = Date.now();
    while (!cond()) {
      if (Date.now() - start > ms) throw new Error('waitUntil timeout');
      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 10));
      });
    }
  }

  function stateInput() {
    const input = container.querySelector<HTMLInputElement>(
      'input[aria-label="Growth Agent state scope"]',
    );
    if (!input) throw new Error('state input not rendered');
    return input;
  }

  function button(name: RegExp) {
    const candidates = Array.from(container.querySelectorAll<HTMLButtonElement>('button'));
    const match = candidates.find((candidate) => name.test(candidate.textContent ?? ''));
    if (!match) throw new Error(`button not rendered: ${name}`);
    return match;
  }

  it('renders governed workflows and blocks invalid state scopes before posting', async () => {
    mount();
    await waitUntil(() => container.textContent?.includes('Daily Refi Opportunity Brief') ?? false);

    expect(container.textContent).toContain('Mortgage Growth Agent');
    expect(container.textContent).toContain('Borrower Dossier Review');
    expect(container.textContent).toContain('No auto-send · no scheduled automation');
    expect(container.textContent).toContain('Audit checked after each run');

    act(() => setNativeValue(stateInput(), 'IL illinois'));
    await waitUntil(() => container.textContent?.includes('Invalid: illinois') ?? false);

    expect(button(/^Run$/).disabled).toBe(true);
    expect(runGrowthAgentWorkflow).not.toHaveBeenCalled();
    expect(runCustomGrowthAgentWorkflow).not.toHaveBeenCalled();
    expect(runMortgageGrowthAgent).not.toHaveBeenCalled();
  });

  it('routes a natural-language agent objective through reviewed workflows', async () => {
    mount();
    await waitUntil(() => container.textContent?.includes('Growth objective') ?? false);
    await waitUntil(() => container.textContent?.includes('Daily Refi Opportunity Brief') ?? false);
    expect(container.textContent).toContain('Agent Framework orchestration');
    expect(container.textContent).toContain('Not provisioned');
    expect(container.textContent).toContain('UC metric-view certification');
    expect(container.textContent).toContain('Application-reviewed SQL tools');
    expect(container.textContent).toContain('Lakebase synced-table serving');
    expect(container.textContent).toContain('Configured');
    expect(container.textContent).toContain('Gateway endpoint/inference-table config is missing.');

    const prompt = container.querySelector<HTMLTextAreaElement>(
      'textarea[aria-label="Mortgage Growth Agent prompt"]',
    );
    if (!prompt) throw new Error('agent prompt not rendered');
    act(() => {
      const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')?.set;
      setter?.call(prompt, 'Find refi and listed borrowers in IL before the branch review.');
      prompt.dispatchEvent(new Event('input', { bubbles: true }));
      prompt.dispatchEvent(new Event('change', { bubbles: true }));
    });
    act(() => setNativeValue(stateInput(), 'IL'));
    act(() => button(/^Plan reviewed workflow$/).click());

    await waitUntil(() => runMortgageGrowthAgent.mock.calls.length === 1);
    expect(runMortgageGrowthAgent.mock.calls[0][0]).toEqual({
      prompt: 'Find refi and listed borrowers in IL before the branch review.',
      states: ['IL'],
      save_monitor: false,
      cadence: 'daily',
      monitor_name: null,
    });
    await waitUntil(() => container.textContent?.includes('Structured data lens') ?? false);
    expect(container.textContent).toContain('Reviewed workflow');
    expect(container.textContent).toContain('Run correlation 111111111111');
    expect(container.textContent).toContain('Hash aaaaaaaaaaaa');
    expect(container.textContent).toContain('Audit audit-1111');
    expect(container.textContent).toContain('PII-safe output');
    expect(container.textContent).toContain('fn_build_cohort');
    expect(container.textContent).not.toContain('MLflow trace');
  });

  it('saves natural-language monitors with reviewed filters only', async () => {
    const savedMonitor = {
      monitor_id: '22222222-2222-4222-8222-222222222222',
      workflow_id: 'daily_refi_brief' as const,
      name: 'Mortgage Growth Agent - IL',
      cadence: 'weekly' as const,
      status: 'active' as const,
      criteria: {
        states: ['IL'],
        lead_queue_filters: {
          segment_codes: ['itm'],
          segment_mode: 'any',
        },
      },
      route: RUN.route,
      actionable_total: RUN.actionable_total,
      source_assets: RUN.source_assets,
      last_run_id: RUN.run_id,
    };
    growthAgent
      .mockResolvedValueOnce(HOME)
      .mockResolvedValueOnce(HOME)
      .mockResolvedValueOnce({ ...HOME, monitors: [savedMonitor] });
    runMortgageGrowthAgent.mockResolvedValueOnce({
      ...RUN,
      monitor: savedMonitor,
    });
    mount();
    await waitUntil(() => container.textContent?.includes('Growth objective') ?? false);

    const prompt = container.querySelector<HTMLTextAreaElement>(
      'textarea[aria-label="Mortgage Growth Agent prompt"]',
    );
    if (!prompt) throw new Error('agent prompt not rendered');
    act(() => {
      const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')?.set;
      setter?.call(prompt, 'Find refinance opportunities for branch follow-up.');
      prompt.dispatchEvent(new Event('input', { bubbles: true }));
      prompt.dispatchEvent(new Event('change', { bubbles: true }));
    });
    act(() => setNativeValue(stateInput(), 'IL'));
    const cadence = container.querySelector<HTMLSelectElement>(
      'select[aria-label="Growth Agent review cadence"]',
    );
    if (!cadence) throw new Error('review cadence select not rendered');
    act(() => setNativeValue(cadence, 'weekly'));
    act(() => button(/^Save reviewed watchlist$/).click());

    await waitUntil(() => runMortgageGrowthAgent.mock.calls.length === 1);
    expect(runMortgageGrowthAgent.mock.calls[0][0]).toEqual({
      prompt: 'Find refinance opportunities for branch follow-up.',
      states: ['IL'],
      save_monitor: true,
      cadence: 'weekly',
      monitor_name: 'Mortgage Growth Agent - IL',
    });
    await waitUntil(() => container.textContent?.includes('Mortgage Growth Agent - IL') ?? false);
    expect(container.textContent).toContain('Saved watchlists');
    expect(container.textContent).toContain('5,394');
  });

  it('re-runs saved watchlists without replaying raw prompt text', async () => {
    const savedMonitor = {
      monitor_id: '22222222-2222-4222-8222-222222222222',
      workflow_id: 'daily_refi_brief' as const,
      name: 'Mortgage Growth Agent - IL',
      cadence: 'weekly' as const,
      status: 'active' as const,
      criteria: {
        states: ['IL'],
        lead_queue_filters: {
          segment_codes: ['itm'],
          segment_mode: 'any',
        },
      },
      route: RUN.route,
      actionable_total: RUN.actionable_total,
      source_assets: RUN.source_assets,
      last_run_id: RUN.run_id,
    };
    growthAgent
      .mockResolvedValueOnce({ ...HOME, monitors: [savedMonitor] })
      .mockResolvedValueOnce({ ...HOME, monitors: [savedMonitor] });
    mount();
    await waitUntil(() => container.textContent?.includes('Mortgage Growth Agent - IL') ?? false);

    act(() => button(/^Run now$/).click());

    await waitUntil(() => rerunGrowthAgentMonitor.mock.calls.length === 1);
    expect(rerunGrowthAgentMonitor.mock.calls[0]).toEqual([
      '22222222-2222-4222-8222-222222222222',
      {},
    ]);
    await waitUntil(() => container.textContent?.includes('Saved watchlist runner') ?? false);
    expect(container.textContent).toContain('Saved watchlist re-run: Mortgage Growth Agent - IL.');
    expect(container.textContent).toContain('Eligible subset');
    expect(container.textContent).not.toContain('run this for John Smith');

    act(() => button(/Open eligible refi subset/).click());
    expect(navigate).toHaveBeenCalledWith(RUN.route);
  });

  it('shows inactive saved watchlists but blocks reruns', async () => {
    const pausedMonitor = {
      monitor_id: '33333333-3333-4333-8333-333333333333',
      workflow_id: 'daily_refi_brief' as const,
      name: 'Paused refi watchlist',
      cadence: 'daily' as const,
      status: 'paused' as const,
      criteria: RUN.criteria,
      route: RUN.route,
      actionable_total: RUN.actionable_total,
      source_assets: RUN.source_assets,
      last_run_id: RUN.run_id,
    };
    growthAgent.mockResolvedValue({ ...HOME, monitors: [pausedMonitor] });
    mount();
    await waitUntil(() => container.textContent?.includes('Paused refi watchlist') ?? false);

    expect(container.textContent).toContain('Paused');
    const runButton = button(/^Run now$/);
    expect(runButton.disabled).toBe(true);
    act(() => runButton.click());
    expect(rerunGrowthAgentMonitor).not.toHaveBeenCalled();
    act(() => button(/^Open$/).click());
    expect(navigate).toHaveBeenCalledWith(RUN.route);
  });

  it('does not imply a state scope in the default prompt', async () => {
    mount();
    await waitUntil(() => container.textContent?.includes('Growth objective') ?? false);

    const prompt = container.querySelector<HTMLTextAreaElement>(
      'textarea[aria-label="Mortgage Growth Agent prompt"]',
    );
    if (!prompt) throw new Error('agent prompt not rendered');
    expect(prompt.value).toContain('current coverage');
    expect(prompt.value).not.toContain('Illinois');

    act(() => button(/^Plan reviewed workflow$/).click());
    await waitUntil(() => runMortgageGrowthAgent.mock.calls.length === 1);
    expect(runMortgageGrowthAgent.mock.calls[0][0]).toEqual({
      prompt: 'Find prime refinance and listed-for-sale opportunities across current coverage.',
      states: [],
      save_monitor: false,
      cadence: 'daily',
      monitor_name: null,
    });
  });

  it('renders non-passed governance caveats visibly', async () => {
    runMortgageGrowthAgent.mockResolvedValueOnce({
      ...RUN,
      governance_chips: [
        ...RUN.governance_chips,
        {
          label: 'Preview integration',
          status: 'not_provisioned',
          detail: 'This Databricks preview feature is not provisioned in the workspace.',
          evidence_ref: 'capability:not_provisioned',
        },
      ],
    });
    mount();
    await waitUntil(() => container.textContent?.includes('Growth objective') ?? false);

    act(() => button(/^Plan reviewed workflow$/).click());
    await waitUntil(() => container.textContent?.includes('Preview integration') ?? false);

    expect(container.textContent).toContain('Not provisioned');
    expect(container.textContent).toContain('This Databricks preview feature is not provisioned in the workspace.');
  });

  it('renders blocked tool and policy states as blocked, not completed or review-only', async () => {
    runMortgageGrowthAgent.mockResolvedValueOnce({
      ...RUN,
      tool_steps: [
        {
          label: 'Approval gate',
          status: 'blocked',
          detail: 'Human approval is required before activation.',
          tool_name: 'fn_lead_queue_url',
          result_hash: 'b'.repeat(64),
        },
      ],
      policy_checks: [
        {
          label: 'Activation policy',
          status: 'blocked',
          detail: 'The agent cannot send outreach automatically.',
        },
      ],
    });
    mount();
    await waitUntil(() => container.textContent?.includes('Growth objective') ?? false);

    act(() => button(/^Plan reviewed workflow$/).click());
    await waitUntil(() => container.textContent?.includes('Activation policy') ?? false);

    expect(container.querySelector('.growth-agent-step--blocked')).not.toBeNull();
    expect(container.textContent).toContain('Blocked');
    expect(container.textContent).not.toContain('Approval gatePassed');
  });

  it('runs and opens the reconciled eligible Lead Queue subset', async () => {
    mount();
    await waitUntil(() => container.textContent?.includes('Daily Refi Opportunity Brief') ?? false);

    act(() => setNativeValue(stateInput(), 'il tx il'));
    act(() => button(/^Run$/).click());

    await waitUntil(() => runGrowthAgentWorkflow.mock.calls.length === 1);
    expect(runGrowthAgentWorkflow.mock.calls[0][0]).toBe('daily_refi_brief');
    expect(runGrowthAgentWorkflow.mock.calls[0][1]).toEqual({
      states: ['IL', 'TX'],
      save_monitor: false,
      cadence: 'daily',
      monitor_name: null,
    });
    await waitUntil(() => container.textContent?.includes('117,404') ?? false);
    expect(container.textContent).toContain('5,394');

    act(() => button(/Open eligible refi subset/).click());
    expect(navigate).toHaveBeenCalledWith(
      '/lead-queue?segment=itm&marketing_eligibility=Eligible+only&states=IL%2CTX',
    );
  });

  it('clears the prior run card when a later workflow fails validation', async () => {
    mount();
    await waitUntil(() => container.textContent?.includes('Daily Refi Opportunity Brief') ?? false);

    act(() => button(/^Run$/).click());
    await waitUntil(() => container.textContent?.includes('117,404') ?? false);
    expect(container.textContent).toContain('5,394');

    act(() => setNativeValue(stateInput(), 'XX'));
    await waitUntil(() => container.textContent?.includes('Invalid: XX') ?? false);

    expect(container.textContent).not.toContain('117,404');
    expect(container.textContent).not.toContain('5,394');
    expect(runGrowthAgentWorkflow).toHaveBeenCalledTimes(1);
  });

  it('clears the prior run card when valid workflow criteria change', async () => {
    mount();
    await waitUntil(() => container.textContent?.includes('Daily Refi Opportunity Brief') ?? false);

    act(() => setNativeValue(stateInput(), 'IL'));
    act(() => button(/^Run$/).click());
    await waitUntil(() => container.textContent?.includes('117,404') ?? false);
    expect(container.textContent).toContain('5,394');

    act(() => setNativeValue(stateInput(), 'TX'));
    await waitUntil(() => !(container.textContent?.includes('117,404') ?? false));

    act(() => button(/^Run$/).click());
    await waitUntil(() => runGrowthAgentWorkflow.mock.calls.length === 2);
    expect(runGrowthAgentWorkflow.mock.calls[1][1]).toMatchObject({ states: ['TX'] });
  });

  it('clears the custom run card when custom segment logic changes', async () => {
    mount();
    await waitUntil(() => container.textContent?.includes('Build a custom segment workflow') ?? false);

    act(() => button(/^Run custom$/).click());
    await waitUntil(() => container.textContent?.includes('117,404') ?? false);
    expect(container.textContent).toContain('5,394');

    const mode = container.querySelector<HTMLSelectElement>(
      'select[aria-label="Custom Growth Agent segment logic"]',
    );
    if (!mode) throw new Error('custom mode select not rendered');
    act(() => setNativeValue(mode, 'all'));
    await waitUntil(() => !(container.textContent?.includes('117,404') ?? false));
  });

  it('saves monitors with backend-safe generated labels', async () => {
    mount();
    await waitUntil(() => container.textContent?.includes('Daily Refi Opportunity Brief') ?? false);

    act(() => setNativeValue(stateInput(), 'IL TX'));
    act(() => button(/Save watchlist/).click());

    await waitUntil(() => runGrowthAgentWorkflow.mock.calls.length === 1);
    expect(runGrowthAgentWorkflow.mock.calls[0][1]).toEqual({
      states: ['IL', 'TX'],
      save_monitor: true,
      cadence: 'daily',
      monitor_name: 'Daily Refi Opportunity Brief - IL, TX',
    });
    await waitUntil(() => growthAgent.mock.calls.length >= 2);
  });

  it('runs custom reviewed segment workflows with any mode by default', async () => {
    mount();
    await waitUntil(() => container.textContent?.includes('Build a custom segment workflow') ?? false);

    act(() => setNativeValue(stateInput(), 'IL TX'));
    act(() => button(/^Run custom$/).click());

    await waitUntil(() => runCustomGrowthAgentWorkflow.mock.calls.length === 1);
    expect(runCustomGrowthAgentWorkflow.mock.calls[0][0]).toEqual({
      states: ['IL', 'TX'],
      segment_codes: ['itm', 'listed'],
      segment_mode: 'any',
      save_monitor: false,
      cadence: 'daily',
      monitor_name: null,
    });
    await waitUntil(() => container.textContent?.includes('Custom Segment Workflow') ?? false);
    expect(container.textContent).toContain('Reviewed custom workflow');
    expect(container.textContent).toContain('Open eligible custom subset');
  });

  it('saves custom monitors with all mode and selected segment labels', async () => {
    mount();
    await waitUntil(() => container.textContent?.includes('Build a custom segment workflow') ?? false);

    const select = container.querySelector<HTMLSelectElement>(
      'select[aria-label="Custom Growth Agent segment logic"]',
    );
    if (!select) throw new Error('custom segment mode select not rendered');
    act(() => setNativeValue(select, 'all'));
    act(() => button(/Save custom watchlist/).click());

    await waitUntil(() => runCustomGrowthAgentWorkflow.mock.calls.length === 1);
    expect(runCustomGrowthAgentWorkflow.mock.calls[0][0]).toEqual({
      states: [],
      segment_codes: ['itm', 'listed'],
      segment_mode: 'all',
      save_monitor: true,
      cadence: 'daily',
      monitor_name: 'Custom Segment Workflow - ALL - ITM+LISTED',
    });
  });

  it('locks other Growth Agent actions while a saved monitor rerun is pending', async () => {
    let resolveRerun: ((value: GrowthAgentRunResponse) => void) | undefined;
    const savedMonitor = {
      monitor_id: '22222222-2222-4222-8222-222222222222',
      workflow_id: 'daily_refi_brief' as const,
      name: 'Mortgage Growth Agent - IL',
      cadence: 'weekly' as const,
      status: 'active' as const,
      criteria: RUN.criteria,
      route: RUN.route,
      actionable_total: RUN.actionable_total,
      source_assets: RUN.source_assets,
      last_run_id: RUN.run_id,
    };
    growthAgent.mockResolvedValue({ ...HOME, monitors: [savedMonitor] });
    rerunGrowthAgentMonitor.mockReturnValueOnce(
      new Promise<GrowthAgentRunResponse>((resolve) => {
        resolveRerun = resolve;
      }),
    );
    mount();
    await waitUntil(() => container.textContent?.includes('Mortgage Growth Agent - IL') ?? false);

    act(() => button(/^Run now$/).click());
    await waitUntil(() => rerunGrowthAgentMonitor.mock.calls.length === 1);

    expect(button(/^Plan reviewed workflow$/).disabled).toBe(true);
    expect(button(/^Run$/).disabled).toBe(true);
    expect(button(/^Run custom$/).disabled).toBe(true);
    expect(button(/^Open$/).disabled).toBe(false);

    await act(async () => {
      resolveRerun?.(RUN);
    });
    await waitUntil(() => button(/^Run$/).disabled === false);
  });

  it('clears validation errors when the Growth Agent prompt is corrected', async () => {
    mount();
    await waitUntil(() => container.textContent?.includes('Growth objective') ?? false);

    const prompt = container.querySelector<HTMLTextAreaElement>(
      'textarea[aria-label="Mortgage Growth Agent prompt"]',
    );
    if (!prompt) throw new Error('agent prompt not rendered');
    act(() => {
      const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')?.set;
      setter?.call(prompt, '  ');
      prompt.dispatchEvent(new Event('input', { bubbles: true }));
      prompt.dispatchEvent(new Event('change', { bubbles: true }));
    });
    act(() => button(/^Plan reviewed workflow$/).click());
    await waitUntil(() => container.textContent?.includes('Enter a borrower-growth objective') ?? false);

    act(() => {
      const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')?.set;
      setter?.call(prompt, 'Find prime refinance opportunities for review.');
      prompt.dispatchEvent(new Event('input', { bubbles: true }));
      prompt.dispatchEvent(new Event('change', { bubbles: true }));
    });

    expect(container.textContent).not.toContain('Enter a borrower-growth objective');
  });

  it('updates custom segment payloads from chip selection and disables zero-segment runs', async () => {
    mount();
    await waitUntil(() => container.textContent?.includes('Build a custom segment workflow') ?? false);

    act(() => button(/^Listed for Sale$/).click());
    act(() => button(/^Prime Refi Candidates$/).click());
    expect(button(/^Run custom$/).disabled).toBe(true);
    expect(button(/^Save custom watchlist$/).disabled).toBe(true);

    act(() => button(/^Home Equity Candidate$/).click());
    expect(button(/^Run custom$/).disabled).toBe(false);
    act(() => button(/^Run custom$/).click());

    await waitUntil(() => runCustomGrowthAgentWorkflow.mock.calls.length === 1);
    expect(runCustomGrowthAgentWorkflow.mock.calls[0][0]).toMatchObject({
      segment_codes: ['equity'],
      segment_mode: 'any',
      save_monitor: false,
    });
  });

  it('shows a save-specific pending label while saving a custom monitor', async () => {
    let resolveRun: ((value: GrowthAgentRunResponse) => void) | undefined;
    runCustomGrowthAgentWorkflow.mockReturnValueOnce(
      new Promise<GrowthAgentRunResponse>((resolve) => {
        resolveRun = resolve;
      }),
    );
    mount();
    await waitUntil(() => container.textContent?.includes('Build a custom segment workflow') ?? false);

    act(() => button(/^Save custom watchlist$/).click());

    await waitUntil(() => container.textContent?.includes('Saving…') ?? false);
    expect(button(/^Run custom$/).disabled).toBe(true);
    expect(button(/^Saving…$/).disabled).toBe(true);
    expect(container.textContent).not.toContain('Running…');

    await act(async () => {
      resolveRun?.(RUN);
      await Promise.resolve();
    });
    await waitUntil(() => runCustomGrowthAgentWorkflow.mock.calls.length === 1);
  });
});
