// @vitest-environment happy-dom

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider, onlineManager, useQuery } from '@tanstack/react-query';
import type { HealthPayload } from '../lib/api';
import { ApiError } from '../lib/api';
import { CLIENT_FAILURE_MESSAGES, reportNetworkFailure } from '../lib/apiFailure';
import { _resetSessionStatusForTests, markSessionExpired } from '../lib/sessionStatus';
import { HealthProvider, useHealth } from './HealthProvider';
import { DegradedBanner } from './mortgage/DegradedBanner';
import { nextConnection, INITIAL_CONNECTION, UNREACHABLE_PROBES_BEFORE_BANNER } from './connectionState';

/**
 * Connection states of the shared health poll (audit 2026-09-21 `states-02`,
 * `shell-v1`), rendered through the real DegradedBanner so every assertion is
 * about what the shell shows, not a helper's return value.
 */

const OK: HealthPayload = { status: 'ok', mode: 'live', dependencies: { warehouse: 'up', lakebase: 'up', genie: 'up' } };
const UNREACHABLE: HealthPayload = { status: 'unreachable', mode: 'unknown', dependencies: {} };

function ConnectionProbe() {
  const { connection } = useHealth();
  return <span data-testid="connection">{connection}</span>;
}

describe('nextConnection', () => {
  it('needs two consecutive unreachable probes while online', () => {
    expect(UNREACHABLE_PROBES_BEFORE_BANNER).toBe(2);
    const one = nextConnection(INITIAL_CONNECTION, { sessionExpired: false, online: true, probeReachable: false });
    expect(one.status).toBe('online');
    const two = nextConnection(one, { sessionExpired: false, online: true, probeReachable: false });
    expect(two.status).toBe('unreachable');
    expect(nextConnection(two, { sessionExpired: false, online: true, probeReachable: true })).toEqual(INITIAL_CONNECTION);
  });

  it('lets offline and an ended session outrank a failed probe', () => {
    expect(nextConnection(INITIAL_CONNECTION, { sessionExpired: false, online: false, probeReachable: false }).status).toBe('offline');
    expect(nextConnection(INITIAL_CONNECTION, { sessionExpired: true, online: true, probeReachable: false }).status).toBe('session_expired');
  });
});

describe('HealthProvider connection', () => {
  let container: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;

  beforeEach(() => {
    vi.useFakeTimers();
    _resetSessionStatusForTests();
    onlineManager.setOnline(true);
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
    queryClient.clear();
    onlineManager.setOnline(true);
    _resetSessionStatusForTests();
    vi.useRealTimers();
  });

  async function mount(fetchHealth: (signal?: AbortSignal) => Promise<HealthPayload>, extra: React.ReactNode = null) {
    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <HealthProvider pollIntervalOkMs={8000} pollIntervalDegradedMs={3000} debounceUpMs={0} fetchHealth={fetchHealth}>
            <ConnectionProbe />
            <DegradedBanner onReload={() => undefined} />
            {extra}
          </HealthProvider>
        </QueryClientProvider>,
      );
    });
  }

  async function advance(ms: number) {
    await act(async () => {
      vi.advanceTimersByTime(ms);
    });
  }

  const banner = () => container.querySelector<HTMLElement>('.degraded-banner');
  const sub = () => banner()?.querySelector('.degraded-banner__sub')?.textContent;
  const connection = () => container.querySelector('[data-testid="connection"]')?.textContent;

  it('shows "Connection lost" with Reload only after the SECOND unreachable probe, probing at the fast cadence', async () => {
    const fetchHealth = vi.fn(async () => UNREACHABLE);
    await mount(fetchHealth);
    expect(fetchHealth).toHaveBeenCalledTimes(1);
    expect(banner(), 'one failed probe is a blip, not an outage').toBeNull();

    await advance(3000);
    expect(fetchHealth, 'the second probe runs at the 3 s cadence, not 8 s').toHaveBeenCalledTimes(2);
    expect(connection()).toBe('unreachable');
    expect(banner()?.dataset.connection).toBe('unreachable');
    expect(banner()?.textContent).toContain('Connection lost');
    expect(banner()?.querySelector('button')?.textContent).toBe('Reload');
    // Not every panel reloads by itself (the Offer page loads through its own
    // effects and offers Retry / Regenerate), so the copy promises no more.
    expect(sub()).toBe(
      'The app did not answer the last two checks. Checking again every 3 seconds; once it answers, panels reload or let you try again.',
    );
  });

  it('clears "Connection lost" and refetches only the mounted queries that could not reach the server', async () => {
    const unreachable = new ApiError(CLIENT_FAILURE_MESSAGES.unreachable, { path: '/api/v1/leads', reason: 'unreachable' });
    const forbidden = new ApiError('forbidden', { path: '/api/v1/admin', status: 403 });
    const leads = vi.fn(async () => {
      throw unreachable;
    });
    const admin = vi.fn(async () => {
      throw forbidden;
    });
    function Reads() {
      useQuery({ queryKey: ['leads'], queryFn: leads });
      useQuery({ queryKey: ['admin'], queryFn: admin });
      return null;
    }
    let reachable = false;
    const fetchHealth = vi.fn(async () => (reachable ? OK : UNREACHABLE));
    await mount(fetchHealth, <Reads />);
    await advance(3000);
    expect(connection()).toBe('unreachable');
    const leadCalls = leads.mock.calls.length;
    const adminCalls = admin.mock.calls.length;

    reachable = true;
    await advance(3000);
    expect(connection()).toBe('online');
    expect(banner()).toBeNull();
    expect(leads.mock.calls.length, 'the unreachable read is refetched').toBe(leadCalls + 1);
    expect(admin.mock.calls.length, 'a 403 is not an outage and is not re-fired').toBe(adminCalls);
  });

  it('reloads a read that could not reach the server after a ONE-probe blip too (no banner, no Retry click)', async () => {
    const unreachable = new ApiError(CLIENT_FAILURE_MESSAGES.unreachable, { path: '/api/v1/leads', reason: 'unreachable' });
    const forbidden = new ApiError('forbidden', { path: '/api/v1/admin', status: 403 });
    const leads = vi.fn(async () => {
      throw unreachable;
    });
    const admin = vi.fn(async () => {
      throw forbidden;
    });
    function Reads() {
      useQuery({ queryKey: ['leads'], queryFn: leads });
      useQuery({ queryKey: ['admin'], queryFn: admin });
      return null;
    }
    let reachable = false;
    const fetchHealth = vi.fn(async () => (reachable ? OK : UNREACHABLE));
    await mount(fetchHealth, <Reads />);
    expect(fetchHealth).toHaveBeenCalledTimes(1);
    expect(connection(), 'one failed probe is a blip').toBe('online');
    expect(banner()).toBeNull();
    const leadCalls = leads.mock.calls.length;
    const adminCalls = admin.mock.calls.length;

    reachable = true;
    await advance(3000);
    expect(fetchHealth).toHaveBeenCalledTimes(2);
    expect(leads.mock.calls.length, 'the unreachable read is refetched once the app answers').toBe(leadCalls + 1);
    expect(admin.mock.calls.length, 'a 403 is not an outage and is not re-fired').toBe(adminCalls);
  });

  it('never re-fires a read that fails while every probe reaches the app: no refetch loop', async () => {
    const leads = vi.fn(async () => {
      throw new ApiError(CLIENT_FAILURE_MESSAGES.unreachable, { path: '/api/v1/leads', reason: 'unreachable' });
    });
    function Reads() {
      useQuery({ queryKey: ['leads'], queryFn: leads });
      return null;
    }
    const fetchHealth = vi.fn(async () => OK);
    await mount(fetchHealth, <Reads />);
    await act(async () => {
      reportNetworkFailure();
    });
    // One healthy poll at a time (each tick awaits its probe before scheduling the next).
    for (let i = 0; i < 6; i += 1) await advance(8000);
    expect(fetchHealth.mock.calls.length, 'the poll kept running').toBeGreaterThanOrEqual(6);
    expect(leads, 'only the mount fetched it').toHaveBeenCalledTimes(1);
  });

  it('stops probing and shows the offline banner while the browser has no network, then probes on return', async () => {
    const fetchHealth = vi.fn(async () => OK);
    await mount(fetchHealth);
    expect(fetchHealth).toHaveBeenCalledTimes(1);

    await act(async () => {
      onlineManager.setOnline(false);
    });
    expect(connection()).toBe('offline');
    expect(banner()?.dataset.connection).toBe('offline');
    expect(banner()?.textContent).toContain('You are offline');
    // Only a PAUSED read resumes on its own; a request that already failed
    // offline (the Offer page's effects, an Approve click) does not.
    expect(sub()).toBe(
      'Panels waiting for a connection load when it returns. Approvals and other changes are not recorded while you are offline.',
    );
    expect(banner()?.querySelector('button'), 'reloading offline would fail too').toBeNull();
    expect(document.documentElement.dataset.connection).toBe('offline');

    await advance(60_000);
    expect(fetchHealth, 'no probes while offline').toHaveBeenCalledTimes(1);

    await act(async () => {
      onlineManager.setOnline(true);
    });
    expect(fetchHealth).toHaveBeenCalledTimes(2);
    expect(connection()).toBe('online');
    expect(banner()).toBeNull();
    expect(document.documentElement.dataset.connection).toBeUndefined();
  });

  it('stops polling for good once the session ended, and shows the dialog instead of a banner', async () => {
    const fetchHealth = vi.fn(async () => OK);
    await mount(fetchHealth);
    await act(async () => {
      markSessionExpired({ method: 'GET', path: '/api/v1/leads' });
    });
    expect(connection()).toBe('session_expired');
    expect(banner()).toBeNull();
    expect(container.querySelector('dialog.session-dialog')).not.toBeNull();

    await advance(120_000);
    expect(fetchHealth, 'no probe after the session ended').toHaveBeenCalledTimes(1);
  });

  it('probes at once when a request elsewhere could not reach the server', async () => {
    const fetchHealth = vi.fn(async () => OK);
    await mount(fetchHealth);
    expect(fetchHealth).toHaveBeenCalledTimes(1);
    await advance(2000);
    await act(async () => {
      reportNetworkFailure();
    });
    expect(fetchHealth, 'nudged before the 8 s poll').toHaveBeenCalledTimes(2);
    await act(async () => {
      reportNetworkFailure();
    });
    expect(fetchHealth, 'a burst of failures probes once').toHaveBeenCalledTimes(2);
  });
});
