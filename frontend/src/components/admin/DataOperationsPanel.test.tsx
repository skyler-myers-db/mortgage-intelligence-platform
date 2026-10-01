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

  const buttonNamed = (pattern: RegExp): HTMLButtonElement | undefined =>
    Array.from(document.querySelectorAll<HTMLButtonElement>('button')).find((button) =>
      pattern.test(button.textContent ?? ''),
    );
  const runButton = () => buttonNamed(/^Run$|Starting/) as HTMLButtonElement;
  const dialog = () => document.querySelector<HTMLDialogElement>('dialog.admin-run-dialog');
  const confirmButton = () => dialog()?.querySelector<HTMLButtonElement>('.btn--primary') ?? null;
  const reasonRadio = (label: string) =>
    [...(dialog()?.querySelectorAll<HTMLLabelElement>('label') ?? [])]
      .find((candidate) => candidate.textContent === label)
      ?.querySelector<HTMLInputElement>('input[type="radio"]') ?? null;

  async function openDialog(): Promise<void> {
    await act(async () => {
      runButton().focus();
      runButton().click();
    });
    await settle();
  }

  async function press(key: string): Promise<void> {
    await act(async () => {
      document.activeElement?.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }));
    });
    await settle();
  }

  it('disables cooldown jobs, and Run opens a confirm naming the job, its last run and effect, posting nothing', async () => {
    await render();

    expect(buttonNamed(/Wait/)?.disabled).toBe(true);
    await openDialog();

    expect(apiMocks.adminRunOperation).not.toHaveBeenCalled();
    const open = dialog();
    expect(open).not.toBeNull();
    const title = document.getElementById(open?.getAttribute('aria-labelledby') ?? '');
    expect(title?.textContent).toBe('Run Refresh market rates?');
    expect(open?.textContent).toContain('mip_fred_rates_ingest');
    expect(open?.textContent).toContain('Run 201');
    expect(open?.textContent).toContain('success');
    expect(document.getElementById(open?.getAttribute('aria-describedby') ?? '')?.textContent).toContain(
      'Updates the weekly 30-year rate in silver',
    );
    expect(open?.querySelector('legend')?.textContent).toBe('Reason (required)');
    const radios = [...(open?.querySelectorAll<HTMLInputElement>('input[type="radio"]') ?? [])];
    expect(radios.map((radio) => radio.value)).toEqual([
      'operator_refresh',
      'source_update',
      'release_validation',
      'support_triage',
    ]);
    expect(radios.some((radio) => radio.checked)).toBe(false);
    expect(document.activeElement?.textContent).toBe('Cancel');
  });

  it('keeps Confirm disabled until a reason is chosen', async () => {
    await render();
    await openDialog();

    expect(confirmButton()?.disabled).toBe(true);
    expect(confirmButton()?.textContent).toBe('Start Refresh market rates');
    await act(async () => {
      reasonRadio('Source data was updated')?.click();
    });
    expect(confirmButton()?.disabled).toBe(false);
  });

  it('posts nothing on Cancel or Escape, and focus returns to Run', async () => {
    await render();
    await openDialog();
    await act(async () => {
      buttonNamed(/^Cancel$/)?.click();
    });
    await settle();
    expect(dialog()).toBeNull();
    expect(document.activeElement).toBe(runButton());

    await openDialog();
    await act(async () => {
      reasonRadio('Release validation')?.click();
    });
    await press('Escape');
    expect(dialog()).toBeNull();
    expect(document.activeElement).toBe(runButton());
    expect(apiMocks.adminRunOperation).not.toHaveBeenCalled();
  });

  it('posts the chosen reason with confirm true and a UUID request id, then closes on success', async () => {
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');
    await render();
    await openDialog();
    await act(async () => {
      reasonRadio('Source data was updated')?.click();
    });
    await act(async () => {
      confirmButton()?.click();
    });
    await settle();

    expect(apiMocks.adminRunOperation).toHaveBeenCalledTimes(1);
    expect(apiMocks.adminRunOperation).toHaveBeenCalledWith({
      job_key: 'fred_rates',
      confirm: true,
      reason: 'source_update',
      request_id: expect.stringMatching(
        /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i,
      ),
    });
    expect(dialog()).toBeNull();
    expect(document.body.textContent).toContain('started');
    expect(document.body.textContent).toContain('run 301');
    expect(invalidateSpy).toHaveBeenCalledWith({
      queryKey: ['mip', 'admin', 'sources'],
    });
    expect(document.activeElement).toBe(runButton());
  });

  it('holds both buttons while the start is in flight', async () => {
    let resolveLaunch: (value: unknown) => void = () => undefined;
    apiMocks.adminRunOperation.mockReturnValue(new Promise((resolve) => {
      resolveLaunch = resolve;
    }));
    await render();
    await openDialog();
    await act(async () => {
      reasonRadio('Support triage')?.click();
    });
    await act(async () => {
      confirmButton()?.click();
    });

    expect(confirmButton()?.textContent).toBe('Starting…');
    expect(confirmButton()?.disabled).toBe(true);
    expect(buttonNamed(/^Cancel$/)?.disabled).toBe(true);
    await press('Escape');
    expect(dialog()).not.toBeNull();

    await act(async () => {
      resolveLaunch({ accepted: true, key: 'fred_rates', label: 'Refresh market rates', run_id: 302 });
    });
    await settle();
    expect(dialog()).toBeNull();
  });

  // runtime-03 pins: the launch helper holds the try/catch. A rejection shows
  // its error (inside the dialog, which stays open) and clears the running
  // key; each Start sends its own request id.
  it('a rejected launch keeps the dialog open with the error and clears the running key', async () => {
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');
    apiMocks.adminRunOperation.mockRejectedValue(new Error('Job launch refused'));
    await render();
    await openDialog();
    await act(async () => {
      reasonRadio('Routine operator refresh')?.click();
    });
    await act(async () => {
      confirmButton()?.click();
    });
    await settle();

    expect(dialog()).not.toBeNull();
    expect(dialog()?.querySelector('[role="alert"]')?.textContent).toBe('Job launch refused');
    expect(confirmButton()?.disabled).toBe(false);
    expect(runButton().textContent).toContain('Run');
    expect(invalidateSpy).not.toHaveBeenCalledWith({ queryKey: ['mip', 'admin', 'sources'] });

    await act(async () => {
      confirmButton()?.click();
    });
    await settle();
    const requestIds = apiMocks.adminRunOperation.mock.calls.map(
      ([body]) => (body as { request_id: string }).request_id,
    );
    expect(requestIds).toHaveLength(2);
    expect(new Set(requestIds).size).toBe(2);
  });
});
