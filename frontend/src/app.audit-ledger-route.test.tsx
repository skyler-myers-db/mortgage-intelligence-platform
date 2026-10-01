/**
 * @vitest-environment happy-dom
 *
 * AuditLedgerGate and the legacy explorer redirect (D-audit-reads-c3).
 * /audit-ledger admits an administrator or a configured read-only auditor by
 * the same session decision the ledger reads enforce; everyone else gets the
 * 403 surface naming "Administrator or Auditor", and the ledger chunk never
 * mounts (so no ledger read is made). An old `/admin-config?audit_…` explorer
 * link is sent on to the ledger before Admin's own decision, so an auditor
 * following it lands on the ledger, not on the admin 403.
 */

import { act, Suspense } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { api } from './lib/api';
import type { SessionResponse } from './types';

vi.mock('./lib/routePreloaders', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./lib/routePreloaders')>()),
  AdminConfigRoute: () => <div data-testid="admin-console">Rules, data sources, and audit</div>,
  AuditLedgerRoute: () => <div data-testid="audit-ledger">Audit ledger</div>,
}));

import { AdminRouteGate, AuditLedgerGate } from './app';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const SETTLE_ATTEMPTS = 300;
const TEST_TIMEOUT_MS = 15_000;
const EVENT_ID = '0f8fad5b-d9cb-469f-a165-70867728950e';

const ADMIN: SessionResponse = { can_access_admin: true, can_approve: true, can_read_audit: true };
const AUDITOR: SessionResponse = { can_access_admin: false, can_approve: false, can_read_audit: true };
const PLAIN: SessionResponse = { can_access_admin: false, can_approve: true, can_read_audit: false };

function WhereAmI() {
  const { pathname, search, hash } = useLocation();
  return <output data-testid="where">{`${pathname}${search}${hash}`}</output>;
}

describe('AuditLedgerGate', { timeout: TEST_TIMEOUT_MS }, () => {
  let root: Root;
  let container: HTMLElement;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    vi.restoreAllMocks();
    await act(async () => root.unmount());
    container.remove();
  });

  const ledger = () => container.querySelector('[data-testid="audit-ledger"]');
  const console_ = () => container.querySelector('[data-testid="admin-console"]');
  const ledgerDenied = () => container.querySelector<HTMLElement>('[data-testid="audit-ledger-access-denied"]');
  const adminDenied = () => container.querySelector<HTMLElement>('[data-testid="admin-access-denied"]');
  const where = () => container.querySelector('[data-testid="where"]')?.textContent;

  async function renderAt(entries: string[], { settle = true } = {}): Promise<void> {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter initialEntries={entries}>
            <Suspense fallback={<div data-testid="route-loading">Loading route</div>}>
              <Routes>
                <Route path="/audit-ledger" element={<AuditLedgerGate />} />
                <Route path="/admin-config" element={<AdminRouteGate />} />
              </Routes>
              <WhereAmI />
            </Suspense>
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
    if (!settle) return;
    const settled = () => Boolean(ledger() || console_() || ledgerDenied() || adminDenied());
    for (let attempt = 0; attempt < SETTLE_ATTEMPTS && !settled(); attempt += 1) {
      await act(async () => {
        await new Promise((resolve) => window.setTimeout(resolve, 10));
      });
    }
  }

  it('holds on the route fallback while the session check is pending, loading nothing', async () => {
    vi.spyOn(api, 'session').mockReturnValue(new Promise<SessionResponse>(() => undefined));

    await renderAt(['/audit-ledger'], { settle: false });

    expect(ledger()).toBeNull();
    expect(ledgerDenied()).toBeNull();
    expect(container.querySelector('.route-fallback, [data-route-fallback]')).not.toBeNull();
  });

  it.each([
    ['an administrator', ADMIN],
    ['a read-only auditor', AUDITOR],
  ])('opens the ledger for %s', async (_who, session) => {
    vi.spyOn(api, 'session').mockResolvedValue(session);

    await renderAt(['/audit-ledger']);

    expect(ledger()).not.toBeNull();
    expect(ledgerDenied()).toBeNull();
  });

  it('shows a plain workspace user the 403 naming "Administrator or Auditor", never the ledger', async () => {
    vi.spyOn(api, 'session').mockResolvedValue(PLAIN);

    await renderAt(['/audit-ledger']);

    expect(ledger()).toBeNull();
    expect(ledgerDenied()).not.toBeNull();
    expect(container.querySelector('h1')?.textContent).toBe('Auditor access required');
    expect(ledgerDenied()?.querySelector('[data-testid="access-denied-role"]')?.textContent)
      .toContain('Required role: Administrator or Auditor.');
    expect(ledgerDenied()?.textContent).toContain('403');
  });

  it('stays closed when the session check fails, without claiming a missing role', async () => {
    vi.spyOn(api, 'session').mockRejectedValue(new Error('network down'));

    await renderAt(['/audit-ledger']);

    expect(ledger()).toBeNull();
    expect(container.querySelector('h1')?.textContent).toBe('Access could not be confirmed');
    expect(ledgerDenied()?.textContent).toContain('Access not verified');
    expect(ledgerDenied()?.textContent).not.toContain('403');
  });

  it.each([
    ['an auditor', AUDITOR],
    ['an administrator', ADMIN],
  ])('sends %s on an old /admin-config explorer link to the ledger (replace)', async (_who, session) => {
    vi.spyOn(api, 'session').mockResolvedValue(session);

    await renderAt([`/admin-config?audit_event_id=${EVENT_ID}#audit`]);

    expect(ledger()).not.toBeNull();
    expect(console_()).toBeNull();
    expect(adminDenied()).toBeNull();
    expect(where()).toBe(`/audit-ledger?audit_event_id=${EVENT_ID}#audit`);
  });

  it('carries every explorer filter of an old link over unchanged', async () => {
    vi.spyOn(api, 'session').mockResolvedValue(AUDITOR);

    await renderAt(['/admin-config?audit_actor=approver%40summit-mortgage.example&audit_since=2026-07-01']);

    expect(where()).toBe('/audit-ledger?audit_actor=approver%40summit-mortgage.example&audit_since=2026-07-01#audit');
  });

  it('keeps a plain /admin-config#audit on Admin (the ledger card), with no redirect', async () => {
    vi.spyOn(api, 'session').mockResolvedValue(ADMIN);

    await renderAt(['/admin-config#audit']);

    expect(console_()).not.toBeNull();
    expect(ledger()).toBeNull();
    expect(where()).toBe('/admin-config#audit');
  });
});
