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
  it('loads governed workflows through canonical API v1', async () => {
    const calls: Array<{ path: string; init?: RequestInit }> = [];
    vi.stubGlobal('fetch', async (path: string, init?: RequestInit) => {
      calls.push({ path, init });
      return jsonResponse(200, { workflows: [], monitors: [] });
    });

    const res = await api.growthAgent();

    expect(res.workflows).toEqual([]);
    expect(calls[0].path).toBe('/api/v1/growth-agent');
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
});
