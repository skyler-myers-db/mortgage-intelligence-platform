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

const HOME: GrowthAgentHomeResponse = {
  workflows: [WORKFLOW],
  monitors: [],
};

const START: GenieStartResult = {
  conversation_id: null,
  trusted_assets: ['mip.gold.borrower_360'],
  sample_questions: [],
};

const RUN: GrowthAgentRunResponse = {
  workflow: WORKFLOW,
  run_id: '11111111-1111-4111-8111-111111111111',
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
    },
  ],
  policy_checks: [
    {
      label: 'Broad vs actionable reconciliation',
      status: 'passed',
      detail: '117,404 broad opportunities reconcile to 5,394 eligible leads.',
    },
  ],
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
    runCustomGrowthAgentWorkflow.mockResolvedValue({
      ...RUN,
      workflow: {
        ...WORKFLOW,
        id: 'custom_segment_watch',
        title: 'Custom Segment Workflow',
        trigger_label: 'Prime Refi Candidates or Listed for Sale segment screen',
        action_label: 'Open eligible custom subset',
      },
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
    expect(container.textContent).toContain('No auto-send');
    expect(container.textContent).toContain('Audited Lakebase run');

    act(() => setNativeValue(stateInput(), 'IL illinois'));
    await waitUntil(() => container.textContent?.includes('Invalid: illinois') ?? false);

    expect(button(/^Run$/).disabled).toBe(true);
    expect(runGrowthAgentWorkflow).not.toHaveBeenCalled();
    expect(runCustomGrowthAgentWorkflow).not.toHaveBeenCalled();
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

  it('saves monitors with backend-safe generated labels', async () => {
    mount();
    await waitUntil(() => container.textContent?.includes('Daily Refi Opportunity Brief') ?? false);

    act(() => setNativeValue(stateInput(), 'IL TX'));
    act(() => button(/Save monitor/).click());

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
    act(() => button(/Save custom monitor/).click());

    await waitUntil(() => runCustomGrowthAgentWorkflow.mock.calls.length === 1);
    expect(runCustomGrowthAgentWorkflow.mock.calls[0][0]).toEqual({
      states: [],
      segment_codes: ['itm', 'listed'],
      segment_mode: 'all',
      save_monitor: true,
      cadence: 'daily',
      monitor_name: 'Custom Segment Workflow - ITM+LISTED',
    });
  });

  it('updates custom segment payloads from chip selection and disables zero-segment runs', async () => {
    mount();
    await waitUntil(() => container.textContent?.includes('Build a custom segment workflow') ?? false);

    act(() => button(/^Listed for Sale$/).click());
    act(() => button(/^Prime Refi Candidates$/).click());
    expect(button(/^Run custom$/).disabled).toBe(true);
    expect(button(/^Save custom monitor$/).disabled).toBe(true);

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

    act(() => button(/^Save custom monitor$/).click());

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
