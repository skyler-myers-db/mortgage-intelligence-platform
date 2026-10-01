/**
 * @vitest-environment happy-dom
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { SessionResponse } from '../types';

const apiMocks = vi.hoisted(() => ({ session: vi.fn() }));

vi.mock('./api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./api')>()),
  api: apiMocks,
}));

import { canReadAuditLedger, useAuditLedgerAccess, usePresenterMode } from './sessionQuery';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

/**
 * The two session-derived UI flags (D-audit-reads-c3, D-shell-deviations-e1)
 * read the shell's one `/api/session` query and fail closed: while it is
 * pending, when it errored, or from an older backend without the keys, the
 * ledger is not offered and presenter mode is off.
 */

let root: Root;
let queryClient: QueryClient;

function Probe() {
  const ledger = useAuditLedgerAccess();
  const presenter = usePresenterMode();
  return <output data-testid="flags">{`${ledger ? 'ledger' : 'no-ledger'} ${presenter ? 'presenter' : 'customer'}`}</output>;
}

async function render(): Promise<string | null | undefined> {
  await act(async () => {
    root.render(
      <QueryClientProvider client={queryClient}>
        <Probe />
      </QueryClientProvider>,
    );
  });
  await act(async () => {
    await new Promise((resolve) => window.setTimeout(resolve, 0));
  });
  return document.querySelector('[data-testid="flags"]')?.textContent;
}

beforeEach(() => {
  document.body.innerHTML = '<div id="root"></div>';
  root = createRoot(document.getElementById('root') as HTMLElement);
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  apiMocks.session.mockReset();
});

afterEach(() => {
  act(() => root.unmount());
  queryClient.clear();
  document.body.innerHTML = '';
});

describe('session-derived UI flags', () => {
  it('are both off while the session read is pending', async () => {
    apiMocks.session.mockReturnValue(new Promise<SessionResponse>(() => undefined));
    expect(await render()).toBe('no-ledger customer');
  });

  it('are both off when the session read failed', async () => {
    apiMocks.session.mockRejectedValue(new Error('network down'));
    expect(await render()).toBe('no-ledger customer');
  });

  it('are both off for an older backend that sends neither key', async () => {
    apiMocks.session.mockResolvedValue({ can_access_admin: false, can_approve: true });
    expect(await render()).toBe('no-ledger customer');
  });

  it('offer the ledger to an auditor and follow the presenter flag', async () => {
    apiMocks.session.mockResolvedValue({
      can_access_admin: false,
      can_approve: false,
      can_read_audit: true,
      presenter_mode: true,
    });
    expect(await render()).toBe('ledger presenter');
  });

  it('offer the ledger to an administrator even without the auditor key', () => {
    expect(canReadAuditLedger({ can_access_admin: true, can_approve: true })).toBe(true);
    expect(canReadAuditLedger({ can_access_admin: false, can_approve: true, can_read_audit: false })).toBe(false);
    expect(canReadAuditLedger(undefined)).toBe(false);
  });
});
