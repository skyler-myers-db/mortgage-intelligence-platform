/**
 * @vitest-environment happy-dom
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { DataOperationsPanel } from './DataOperationsPanel';

const apiMocks = vi.hoisted(() => ({
  adminOperations: vi.fn(),
  adminRunOperation: vi.fn(),
}));

vi.mock('../../lib/api', async () => {
  const actual = await vi.importActual<typeof import('../../lib/api')>('../../lib/api');
  return {
    ...actual,
    api: {
      ...actual.api,
      adminOperations: apiMocks.adminOperations,
      adminRunOperation: apiMocks.adminRunOperation,
    },
  };
});

const OPERATIONS = {
  jobs: [
    {
      key: 'fred_rates',
      label: 'Refresh market rates',
      job_name: 'mip_fred_rates_ingest',
      job_id: 101,
      configured: true,
      description: 'Pull the latest FRED MORTGAGE30US rate into silver.market_rates_weekly.',
      run_order: 1,
      cooldown_remaining_s: 0,
      latest_run: {
        run_id: 201,
        life_cycle_state: 'TERMINATED',
        result_state: 'SUCCESS',
        state_message: null,
        started_at: '2026-06-05T16:00:00+00:00',
        ended_at: '2026-06-05T16:02:00+00:00',
        run_page_url: 'https://example.com/runs/201',
        active: false,
      },
    },
    {
      key: 'gold_refresh',
      label: 'Rebuild scoring snapshot',
      job_name: 'mip_refresh_scores',
      job_id: 103,
      configured: true,
      description: 'Rebuild borrower_360 and lead scores.',
      run_order: 3,
      cooldown_remaining_s: 1800,
      latest_run: null,
    },
  ],
};

async function settle(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
    await new Promise((resolve) => window.setTimeout(resolve, 0));
  });
}

describe('DataOperationsPanel', () => {
  let root: Root;
  let queryClient: QueryClient;

  beforeEach(() => {
    document.body.innerHTML = '<div id="root"></div>';
    root = createRoot(document.getElementById('root') as HTMLElement);
    queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: Infinity } },
    });
    apiMocks.adminOperations.mockResolvedValue(OPERATIONS);
    apiMocks.adminRunOperation.mockResolvedValue({
      accepted: true,
      key: 'fred_rates',
      label: 'Refresh market rates',
      job_name: 'mip_fred_rates_ingest',
      job_id: 101,
      run_id: 301,
      run_page_url: 'https://example.com/runs/301',
      audit_event_id: '11111111-1111-4111-8111-111111111111',
    });
  });

  afterEach(() => {
    act(() => root.unmount());
    queryClient.clear();
    document.body.innerHTML = '';
    vi.clearAllMocks();
  });

  async function render(): Promise<void> {
    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <DataOperationsPanel
            sources={[
              {
                name: 'Cotality Public Records',
                status: 'live',
                rows: 100,
                last_updated: new Date().toISOString(),
                note: 'Delta Share',
              },
              {
                name: 'MLS Listings',
                status: 'roadmap',
                rows: null,
                last_updated: null,
                note: 'Pending Cotality feed',
              },
            ]}
          />
        </QueryClientProvider>,
      );
    });
    await settle();
  }

  it('summarizes source freshness and operation status for admins', async () => {
    await render();

    expect(document.body.textContent).toContain('Data operations');
    expect(document.body.textContent).toContain('Usable sources');
    expect(document.body.textContent).toContain('Demo synthetic');
    expect(document.body.textContent).toContain('Attention');
    expect(document.body.textContent).toContain('1. Refresh market rates');
    expect(document.body.textContent).toContain('cooldown 30m');
    expect(document.body.textContent).toContain('Run 201');
    expect(document.body.textContent).toContain('2m');
  });

  it('disables cooldown jobs and launches available jobs with a UUID request id', async () => {
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');
    await render();

    const buttons = Array.from(document.querySelectorAll('button'));
    const runButton = buttons.find((button) => button.textContent?.includes('Run'));
    const waitButton = buttons.find((button) => button.textContent?.includes('Wait'));
    expect(runButton).toBeTruthy();
    expect(waitButton).toBeTruthy();
    expect((waitButton as HTMLButtonElement).disabled).toBe(true);

    await act(async () => {
      (runButton as HTMLButtonElement).click();
    });
    await settle();

    expect(apiMocks.adminRunOperation).toHaveBeenCalledWith(expect.objectContaining({
      job_key: 'fred_rates',
      confirm: true,
      reason: 'operator_refresh',
      request_id: expect.stringMatching(
        /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i,
      ),
    }));
    expect(document.body.textContent).toContain('started');
    expect(document.body.textContent).toContain('run 301');
    expect(invalidateSpy).toHaveBeenCalledWith({
      queryKey: ['mip', 'admin', 'sources'],
    });
  });
});
