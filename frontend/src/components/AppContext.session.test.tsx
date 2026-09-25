/**
 * @vitest-environment happy-dom
 *
 * AppProvider session threading (audit flow-02 / shell-06, 2026-09-21).
 * `/session` already returned `can_approve`; the frontend type omitted it and
 * nothing read it, so every persona got live Approve buttons. Pins that the
 * provider exposes the server's decision fail-closed, plus the actor's own
 * identity for the "Approving as …" line.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const apiMocks = vi.hoisted(() => ({
  session: vi.fn(),
  workspace: vi.fn(),
}));

vi.mock('../lib/api', () => ({ api: apiMocks }));

// A stable object per test (the provider reads it on every render); the
// delivery-07 cases swap `data` before mounting.
const configOptions = vi.hoisted(() => ({
  current: { data: { lender_name: 'Summit Mortgage', rum_enabled: false } as { lender_name?: string; rum_enabled?: boolean } | undefined },
}));

vi.mock('../lib/configOptionsQuery', () => ({ useConfigOptionsQuery: () => configOptions.current }));

const rum = vi.hoisted(() => ({ installRum: vi.fn() }));

vi.mock('../lib/rum', () => rum);

import { AppProvider, useApp } from './AppContext';

function LenderProbe() {
  const { lender } = useApp();
  return <output data-testid="lender">{lender}</output>;
}

function Probe() {
  const { canApprove, actorEmail, sessionStatus, canAccessAdmin } = useApp();
  return (
    <output data-testid="probe">
      {JSON.stringify({ canApprove, actorEmail, sessionStatus, canAccessAdmin })}
    </output>
  );
}

describe('AppProvider session fields', () => {
  let root: Root;
  let queryClient: QueryClient;

  beforeEach(() => {
    document.body.innerHTML = '<div id="root"></div>';
    root = createRoot(document.getElementById('root') as HTMLElement);
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    apiMocks.workspace.mockResolvedValue({ saved_leads: [], saved_drafts: [] });
    configOptions.current = { data: { lender_name: 'Summit Mortgage', rum_enabled: false } };
  });

  afterEach(() => {
    act(() => root.unmount());
    queryClient.clear();
    document.body.innerHTML = '';
    vi.clearAllMocks();
  });

  async function mount() {
    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <AppProvider><Probe /><LenderProbe /></AppProvider>
        </QueryClientProvider>,
      );
    });
  }

  async function settle() {
    await act(async () => {
      await Promise.resolve();
      await new Promise((resolve) => window.setTimeout(resolve, 0));
    });
  }

  const probe = () => JSON.parse(
    document.querySelector('[data-testid="probe"]')?.textContent ?? '{}',
  ) as Record<string, unknown>;

  it('fails closed while the session is still loading', async () => {
    apiMocks.session.mockReturnValue(new Promise(() => {}));
    await mount();

    expect(probe()).toEqual({
      canApprove: false, actorEmail: null, sessionStatus: 'loading', canAccessAdmin: false,
    });
  });

  it("threads the server's can_approve and the actor's own identity", async () => {
    apiMocks.session.mockResolvedValue({
      can_access_admin: false,
      can_approve: true,
      actor_email: ' approver.one@summit.example ',
    });
    await mount();
    await settle();

    expect(probe()).toEqual({
      canApprove: true,
      actorEmail: 'approver.one@summit.example',
      sessionStatus: 'ready',
      canAccessAdmin: false,
    });
  });

  it('keeps a non-approver gated and treats a missing identity as unknown', async () => {
    apiMocks.session.mockResolvedValue({ can_access_admin: true, can_approve: false });
    await mount();
    await settle();

    expect(probe()).toEqual({
      canApprove: false, actorEmail: null, sessionStatus: 'ready', canAccessAdmin: true,
    });
  });

  it('never grants approval from a truthy non-boolean payload', async () => {
    apiMocks.session.mockResolvedValue({ can_access_admin: false, can_approve: 'yes' });
    await mount();
    await settle();

    expect(probe().canApprove).toBe(false);
  });

  it('fails closed and says so when the session check errors', async () => {
    apiMocks.session.mockRejectedValue(new Error('session unavailable'));
    await mount();
    await settle();

    expect(probe()).toEqual({
      canApprove: false, actorEmail: null, sessionStatus: 'error', canAccessAdmin: false,
    });
  });

  describe('tenant label and RUM gate (audit delivery-07)', () => {
    const lender = () => document.querySelector('[data-testid="lender"]')?.textContent;

    async function rumInstalls(): Promise<number> {
      await act(async () => {
        await new Promise((resolve) => window.setTimeout(resolve, 0));
        await Promise.resolve();
      });
      return rum.installRum.mock.calls.length;
    }

    it('the session lender wins over the options lender', async () => {
      configOptions.current = { data: { lender_name: 'Options Lender', rum_enabled: false } };
      apiMocks.session.mockResolvedValue({ can_access_admin: false, can_approve: false, lender_name: ' Session Lender ' });
      await mount();
      await settle();

      expect(lender()).toBe('Session Lender');
    });

    it('paints the session lender while the options call is still pending', async () => {
      configOptions.current = { data: undefined } as unknown as typeof configOptions.current;
      apiMocks.session.mockResolvedValue({ can_access_admin: false, can_approve: false, lender_name: 'Session Lender' });
      await mount();
      await settle();

      expect(lender()).toBe('Session Lender');
    });

    it('falls back to the options lender, then the placeholder, when the session has none', async () => {
      configOptions.current = { data: { lender_name: 'Options Lender', rum_enabled: false } };
      apiMocks.session.mockResolvedValue({ can_access_admin: false, can_approve: false });
      await mount();
      await settle();
      expect(lender()).toBe('Options Lender');

      act(() => root.unmount());
      root = createRoot(document.getElementById('root') as HTMLElement);
      queryClient.clear();
      configOptions.current = { data: { rum_enabled: false } };
      apiMocks.session.mockResolvedValue({ can_access_admin: false, can_approve: false, lender_name: '  ' });
      await mount();
      await settle();
      expect(lender()).toBe('Configured lender');
    });

    it("the session's rum_enabled wins over the options value, both ways", async () => {
      configOptions.current = { data: { lender_name: 'Summit Mortgage', rum_enabled: true } };
      apiMocks.session.mockResolvedValue({ can_access_admin: false, can_approve: false, rum_enabled: false });
      await mount();
      await settle();
      expect(await rumInstalls(), 'session false beats options true').toBe(0);

      act(() => root.unmount());
      root = createRoot(document.getElementById('root') as HTMLElement);
      queryClient.clear();
      configOptions.current = { data: { lender_name: 'Summit Mortgage', rum_enabled: false } };
      apiMocks.session.mockResolvedValue({ can_access_admin: false, can_approve: false, rum_enabled: true });
      await mount();
      await settle();
      expect(await rumInstalls(), 'session true beats options false').toBe(1);
    });

    it('reads the options RUM gate when the session carries none', async () => {
      configOptions.current = { data: { lender_name: 'Summit Mortgage', rum_enabled: true } };
      apiMocks.session.mockResolvedValue({ can_access_admin: false, can_approve: false });
      await mount();
      await settle();
      expect(await rumInstalls()).toBe(1);
    });

    it('waits for the session before installing RUM, and falls back to options when it fails', async () => {
      configOptions.current = { data: { lender_name: 'Summit Mortgage', rum_enabled: true } };
      let rejectSession: (err: Error) => void = () => undefined;
      apiMocks.session.mockReturnValue(new Promise((_resolve, reject) => {
        rejectSession = reject;
      }));
      await mount();
      await settle();
      expect(await rumInstalls(), 'nothing installs while the session is pending').toBe(0);

      await act(async () => {
        rejectSession(new Error('session unavailable'));
      });
      await settle();
      expect(await rumInstalls()).toBe(1);
    });
  });
});
