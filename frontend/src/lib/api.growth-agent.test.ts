import { afterEach, describe, expect, it, vi } from 'vitest';
import { api } from './api';

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('growth agent API client', () => {
  it('loads governed workflows with live capability readiness through canonical API v1', async () => {
    const calls: Array<{ path: string; init?: RequestInit }> = [];
    vi.stubGlobal('fetch', async (path: string, init?: RequestInit) => {
      calls.push({ path, init });
      return jsonResponse(200, { workflows: [], monitors: [] });
    });

    const res = await api.growthAgent();

    expect(res.workflows).toEqual([]);
    expect(calls[0].path).toBe('/api/v1/growth-agent?live_capabilities=1');
    expect(calls[0].init?.method).toBeUndefined();
  });

  it('loads admin capabilities with the live readiness probe enabled', async () => {
    const calls: Array<{ path: string; init?: RequestInit }> = [];
    vi.stubGlobal('fetch', async (path: string, init?: RequestInit) => {
      calls.push({ path, init });
      return jsonResponse(200, { capabilities: [] });
    });

    await api.adminCapabilities();

    expect(calls[0].path).toBe('/api/v1/admin/capabilities?live=1');
    expect(calls[0].init?.method).toBeUndefined();
  });

  it('runs a workflow with JSON content type and reviewed filters', async () => {
    const calls: Array<{ path: string; init?: RequestInit }> = [];
    vi.stubGlobal('fetch', async (path: string, init?: RequestInit) => {
      calls.push({ path, init });
      return jsonResponse(200, {
        workflow: {
          id: 'daily_refi_brief',
          title: 'Daily Refi Opportunity Brief',
          objective: 'Find borrowers with rate-spread economics.',
          trigger_label: 'Prime refinance economics',
          action_label: 'Open eligible refi subset',
          source_assets: ['mip.gold.borrower_360'],
          default_route: '/lead-queue?segment=itm',
          proof_points: [],
          cadence_options: ['daily', 'weekly'],
        },
        run_id: '11111111-1111-4111-8111-111111111111',
        broad_total: 117404,
        actionable_total: 5394,
        route: '/lead-queue?segment=itm&marketing_eligibility=Eligible+only&states=IL',
        criteria: {},
        source_assets: ['mip.gold.borrower_360'],
        tool_steps: [],
        policy_checks: [],
      });
    });

    const res = await api.runGrowthAgentWorkflow('daily_refi_brief', {
      states: ['IL'],
      save_monitor: true,
      cadence: 'daily',
      monitor_name: 'IL Refi Watch',
    });

    expect(res.actionable_total).toBe(5394);
    expect(calls[0].path).toBe('/api/v1/growth-agent/workflows/daily_refi_brief/run');
    expect(calls[0].init?.method).toBe('POST');
    expect(new Headers(calls[0].init?.headers).get('Content-Type')).toBe('application/json');
    const body = JSON.parse(String(calls[0].init?.body));
    expect(body.request_id).toMatch(/^[0-9a-f-]{36}$/);
    expect(body).toMatchObject({
      states: ['IL'],
      save_monitor: true,
      cadence: 'daily',
      monitor_name: 'IL Refi Watch',
    });
  });

  it('runs a custom workflow through the custom endpoint with reviewed segment payload', async () => {
    const calls: Array<{ path: string; init?: RequestInit }> = [];
    vi.stubGlobal('fetch', async (path: string, init?: RequestInit) => {
      calls.push({ path, init });
      return jsonResponse(200, {
        workflow: {
          id: 'custom_segment_watch',
          title: 'Custom Segment Workflow',
          objective: 'Build a reviewed segment workflow.',
          trigger_label: 'Prime Refi or Listed segment screen',
          action_label: 'Open eligible custom subset',
          source_assets: ['mip.gold.borrower_360'],
          default_route: '/lead-queue?segment_codes=itm%2Clisted&segment_mode=any',
          proof_points: [],
          cadence_options: ['daily', 'weekly'],
        },
        run_id: '11111111-1111-4111-8111-111111111111',
        broad_total: 100,
        actionable_total: 12,
        route: '/lead-queue?segment_codes=itm%2Clisted&segment_mode=any',
        criteria: {},
        source_assets: ['mip.gold.borrower_360'],
        tool_steps: [],
        policy_checks: [],
      });
    });

    const res = await api.runCustomGrowthAgentWorkflow({
      states: ['IL'],
      segment_codes: ['itm', 'listed'],
      segment_mode: 'any',
      save_monitor: true,
      cadence: 'weekly',
      monitor_name: 'Custom Segment Workflow - ANY - ITM+LISTED - IL',
    });

    expect(res.workflow.id).toBe('custom_segment_watch');
    expect(calls[0].path).toBe('/api/v1/growth-agent/custom/run');
    expect(calls[0].init?.method).toBe('POST');
    expect(new Headers(calls[0].init?.headers).get('Content-Type')).toBe('application/json');
    const body = JSON.parse(String(calls[0].init?.body));
    expect(body.request_id).toMatch(/^[0-9a-f-]{36}$/);
    expect(body).toMatchObject({
      states: ['IL'],
      segment_codes: ['itm', 'listed'],
      segment_mode: 'any',
      save_monitor: true,
      cadence: 'weekly',
    });
  });

  it('re-runs a saved monitor with JSON content type and request id', async () => {
    const calls: Array<{ path: string; init?: RequestInit }> = [];
    vi.stubGlobal('fetch', async (path: string, init?: RequestInit) => {
      calls.push({ path, init });
      return jsonResponse(200, {
        workflow: {
          id: 'daily_refi_brief',
          title: 'Daily Refi Opportunity Brief',
          objective: 'Find borrowers with rate-spread economics.',
          trigger_label: 'Prime refinance economics',
          action_label: 'Open eligible refi subset',
          source_assets: ['mip.gold.borrower_360'],
          default_route: '/lead-queue?segment=itm',
          proof_points: [],
          cadence_options: ['daily', 'weekly'],
        },
        run_id: '11111111-1111-4111-8111-111111111111',
        broad_total: 117404,
        actionable_total: 5394,
        route: '/lead-queue?segment=itm&marketing_eligibility=Eligible+only&states=IL',
        criteria: {},
        source_assets: ['mip.gold.borrower_360'],
        tool_steps: [],
        policy_checks: [],
      });
    });

    const res = await api.rerunGrowthAgentMonitor('monitor-123', {});

    expect(res.actionable_total).toBe(5394);
    expect(calls[0].path).toBe('/api/v1/growth-agent/monitors/monitor-123/run');
    expect(calls[0].init?.method).toBe('POST');
    expect(new Headers(calls[0].init?.headers).get('Content-Type')).toBe('application/json');
    const body = JSON.parse(String(calls[0].init?.body));
    expect(body.request_id).toMatch(/^[0-9a-f-]{36}$/);
  });

  it('creates saved-monitor notification drafts with JSON content type and request id', async () => {
    const calls: Array<{ path: string; init?: RequestInit }> = [];
    vi.stubGlobal('fetch', async (path: string, init?: RequestInit) => {
      calls.push({ path, init });
      return jsonResponse(200, [
        {
          draft_id: 'draft-1',
          monitor_id: 'monitor-123',
          run_id: 'run-123',
          channel: 'slack',
          title: 'IL Refi Watch - 5,394 eligible',
          body: 'Draft for Slack: IL Refi Watch refreshed with 5,394 eligible borrowers.',
          status: 'draft',
        },
      ]);
    });

    const res = await api.createGrowthAgentMonitorNotificationDrafts('monitor-123', {
      channels: ['slack', 'teams'],
    });

    expect(res[0].channel).toBe('slack');
    expect(calls[0].path).toBe('/api/v1/growth-agent/monitors/monitor-123/notification-drafts');
    expect(calls[0].init?.method).toBe('POST');
    expect(new Headers(calls[0].init?.headers).get('Content-Type')).toBe('application/json');
    const body = JSON.parse(String(calls[0].init?.body));
    expect(body.request_id).toMatch(/^[0-9a-f-]{36}$/);
    expect(body.channels).toEqual(['slack', 'teams']);
  });

  it('runs due saved monitors through the scheduler-safe endpoint', async () => {
    const calls: Array<{ path: string; init?: RequestInit }> = [];
    vi.stubGlobal('fetch', async (path: string, init?: RequestInit) => {
      calls.push({ path, init });
      return jsonResponse(200, {
        runs: [],
        drafts: [],
        due_count: 0,
      });
    });

    const res = await api.runDueGrowthAgentMonitors({ limit: 5 });

    expect(res.due_count).toBe(0);
    expect(calls[0].path).toBe('/api/v1/growth-agent/monitors/run-due');
    expect(calls[0].init?.method).toBe('POST');
    expect(new Headers(calls[0].init?.headers).get('Content-Type')).toBe('application/json');
    const body = JSON.parse(String(calls[0].init?.body));
    expect(body.request_id).toMatch(/^[0-9a-f-]{36}$/);
    expect(body.channels).toEqual(['slack', 'teams']);
    expect(body.limit).toBe(5);
  });
});
