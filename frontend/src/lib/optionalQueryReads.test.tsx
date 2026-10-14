/**
 * @vitest-environment happy-dom
 *
 * Provider-optional session reads (D-audit-reads-d, flow-04): without a
 * QueryClientProvider every gate reads false (no throw); with one, the hooks
 * follow the cached session entry as it arrives and changes, and never fetch.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, useEffect } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { SessionResponse } from '../types';

const sessionFetch = vi.hoisted(() => vi.fn());
vi.mock('./api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./api')>()),
  api: { session: sessionFetch },
}));

import { useAuditLinkAccess, useOptionalSession, useRefusalTextCapture } from './optionalQueryReads';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let seen: { session: SessionResponse | undefined; capture: boolean; audit: boolean } | null = null;

function Probe() {
  const current = { session: useOptionalSession(), capture: useRefusalTextCapture(), audit: useAuditLinkAccess() };
  useEffect(() => {
    seen = current;
  });
  return null;
}

const BASE: SessionResponse = { can_access_admin: false, can_approve: false, can_read_audit: false };

describe('optionalQueryReads', () => {
  let container: HTMLDivElement;
  let root: Root;
  beforeEach(() => {
    seen = null;
    sessionFetch.mockReset();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });
  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  it('reads as no session, no capture and no ledger link without a provider', () => {
    act(() => root.render(<Probe />));
    expect(seen).toEqual({ session: undefined, capture: false, audit: false });
  });

  it('follows the cached session as it arrives and changes, and never fetches it', () => {
    const client = new QueryClient();
    act(() => root.render(<QueryClientProvider client={client}><Probe /></QueryClientProvider>));
    expect(seen).toEqual({ session: undefined, capture: false, audit: false });

    act(() => {
      client.setQueryData<SessionResponse>(['session', 'access'], { ...BASE, refusal_text_capture_enabled: true });
    });
    expect(seen?.capture).toBe(true);
    expect(seen?.audit).toBe(false);

    act(() => {
      client.setQueryData<SessionResponse>(['session', 'access'], { ...BASE, can_read_audit: true });
    });
    expect(seen).toMatchObject({ capture: false, audit: true });

    act(() => {
      client.setQueryData<SessionResponse>(['session', 'access'], { ...BASE, can_access_admin: true });
    });
    expect(seen?.audit).toBe(true);
    expect(sessionFetch).not.toHaveBeenCalled();
  });
});
