/**
 * @vitest-environment happy-dom
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { mount, type Mounted } from '../../test/render';
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
      recent_runs: [
        {
          run_id: 201,
          life_cycle_state: 'TERMINATED',
          result_state: 'SUCCESS',
          state_message: null,
          started_at: '2026-06-05T16:00:00+00:00',
          ended_at: '2026-06-05T16:02:00+00:00',
          run_page_url: 'https://example.com/runs/201',
          active: false,
        },
        {
          run_id: 199,
          life_cycle_state: 'TERMINATED',
          result_state: 'FAILED',
          state_message: 'failed',
          started_at: '2026-06-05T15:00:00+00:00',
          ended_at: '2026-06-05T15:01:00+00:00',
          run_page_url: 'https://example.com/runs/199',
          active: false,
        },
      ],
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
      recent_runs: [],
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
  let view: Mounted | null = null;
  let queryClient: QueryClient;

  beforeEach(() => {
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
    // Unmount before the cache is cleared; mount()'s own afterEach runs last
    // and removes the container.
    view?.unmount();
    view = null;
    queryClient.clear();
    vi.clearAllMocks();
  });

  async function render(options: {
    sourcesError?: boolean;
    sourcesLoading?: boolean;
  } = {}): Promise<void> {
    view = await mount(
      <QueryClientProvider client={queryClient}>
        <DataOperationsPanel
          sourcesError={options.sourcesError}
          sourcesLoading={options.sourcesLoading}
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
            {
              name: 'Demo outcomes',
              status: 'demo_synthetic',
              rows: 3,
              last_updated: null,
              note: 'Synthetic operational state',
            },
          ]}
        />
      </QueryClientProvider>,
    );
    await settle();
  }

  it('summarizes source freshness and operation status for admins', async () => {
    await render();

    expect(document.body.textContent).toContain('Data operations');
    expect(document.body.textContent).toContain('Usable sources');
    expect(document.body.textContent).toContain('Demo synthetic');
    expect(document.body.textContent).toContain('Attention');
    expect(document.body.textContent).toContain('2 attention');
    expect(document.body.textContent).toContain('1. Refresh market rates');
    expect(document.body.textContent).toContain('cooldown 30m');
    expect(document.body.textContent).toContain('Run 201');
    expect(document.body.textContent).toContain('2m');
    expect(document.body.textContent).toContain('2 recent runs');
  });

  it('does not mark data operations ready when source freshness is unavailable', async () => {
    await render({ sourcesError: true });

    expect(document.body.textContent).toContain('freshness unavailable');
    expect(document.body.textContent).not.toContain('ready');
  });

  it('does not mark data operations ready while source freshness is still loading', async () => {
    await render({ sourcesLoading: true });

    expect(document.body.textContent).toContain('loading...');
    expect(document.body.textContent).not.toContain('ready');
    expect(document.body.textContent).toContain('Usable sources...');
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

  // runtime-03 pins: the launch helper holds the try/catch. A rejection shows
  // its error and clears the running key (the button reads Run again); each
  // click sends its own request id.
  it('a rejected launch shows the error and clears the running key', async () => {
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');
    apiMocks.adminRunOperation.mockRejectedValue(new Error('Job launch refused'));
    await render();

    const runButton = (): HTMLButtonElement =>
      Array.from(document.querySelectorAll('button')).find((button) =>
        button.textContent?.includes('Run') || button.textContent?.includes('Starting'),
      ) as HTMLButtonElement;
    await act(async () => {
      runButton().click();
    });
    await settle();

    expect(document.body.textContent).toContain('Job launch refused');
    expect(runButton().textContent).toContain('Run');
    expect(runButton().disabled).toBe(false);
    expect(invalidateSpy).not.toHaveBeenCalledWith({ queryKey: ['mip', 'admin', 'sources'] });

    await act(async () => {
      runButton().click();
    });
    await settle();
    const requestIds = apiMocks.adminRunOperation.mock.calls.map(
      ([body]) => (body as { request_id: string }).request_id,
    );
    expect(requestIds).toHaveLength(2);
    expect(new Set(requestIds).size).toBe(2);
  });
});
