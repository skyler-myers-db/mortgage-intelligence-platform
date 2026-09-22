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

vi.mock('../lib/configOptionsQuery', () => {
  const STABLE = { data: { lender_name: 'Summit Mortgage', rum_enabled: false } };
  return { useConfigOptionsQuery: () => STABLE };
});

import { AppProvider, useApp } from './AppContext';

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
          <AppProvider><Probe /></AppProvider>
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
});
