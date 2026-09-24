// @vitest-environment happy-dom

import { afterEach, describe, expect, it, vi } from 'vitest';
import { onlineManager, type QueryClient } from '@tanstack/react-query';
import { createMipQueryClient } from './queryClient';

/**
 * The QueryClient's online signal (audit 2026-09-21 `states-02`). TanStack v5
 * starts every page "online" and only learns otherwise from an `offline`
 * event, so a page that boots with no network would fire its queries into a
 * dead network and show failures instead of waiting. `createMipQueryClient`
 * seeds `onlineManager` from `navigator.onLine`; these tests drive a real
 * query through the real client to prove the query waits (`paused`) and
 * fires once the network returns.
 */

let client: QueryClient | null = null;

afterEach(() => {
  client?.unmount();
  client?.clear();
  client = null;
  vi.unstubAllGlobals();
  onlineManager.setOnline(true);
});

function setNavigatorOnline(onLine: boolean): void {
  vi.stubGlobal('navigator', { onLine });
}

describe('createMipQueryClient online signal', () => {
  it('starts offline when the page boots with no network: the query pauses instead of firing', async () => {
    setNavigatorOnline(false);
    client = createMipQueryClient();
    client.mount();
    expect(onlineManager.isOnline()).toBe(false);

    const queryFn = vi.fn(async () => 'rows');
    const result = client.fetchQuery({ queryKey: ['connectivity', 'boot-offline'], queryFn });
    await Promise.resolve();
    expect(client.getQueryState(['connectivity', 'boot-offline'])?.fetchStatus).toBe('paused');
    expect(queryFn, 'a paused query never reaches the network').not.toHaveBeenCalled();

    // The browser's own `online` event, read back through navigator.onLine.
    setNavigatorOnline(true);
    window.dispatchEvent(new Event('online'));
    expect(onlineManager.isOnline()).toBe(true);
    await expect(result).resolves.toBe('rows');
    expect(queryFn).toHaveBeenCalledTimes(1);
  });

  it('starts online when the browser has a network, and follows the offline event', () => {
    setNavigatorOnline(true);
    client = createMipQueryClient();
    client.mount();
    expect(onlineManager.isOnline()).toBe(true);

    setNavigatorOnline(false);
    window.dispatchEvent(new Event('offline'));
    expect(onlineManager.isOnline()).toBe(false);
  });
});
