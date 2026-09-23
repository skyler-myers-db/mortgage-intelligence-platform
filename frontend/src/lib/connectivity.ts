import { useSyncExternalStore } from 'react';
import { onlineManager } from '@tanstack/react-query';

/**
 * Browser connectivity for the shell (audit 2026-09-21 `states-02`).
 *
 * TanStack Query's `onlineManager` is the single online/offline signal: while
 * it reports offline, queries in the default `networkMode: 'online'` do not
 * fire and their retries pause (`fetchStatus: 'paused'`), and they resume on
 * their own when it flips back. The shell reads the same signal, so the
 * offline banner, the paused skeletons and the paused queries can never
 * disagree.
 *
 * TanStack v5 starts every page as "online" and only learns otherwise from an
 * `offline` event. `installOnlineManager` seeds it from `navigator.onLine`
 * as well; `false` there is reliable (no network at all), `true` is not proof
 * the app is reachable, which is what the health poll's "Connection lost"
 * state is for.
 */
export function installOnlineManager(): void {
  onlineManager.setEventListener((setOnline) => {
    if (typeof window === 'undefined' || !window.addEventListener) return undefined;
    const sync = () => setOnline(typeof navigator === 'undefined' || navigator.onLine !== false);
    sync();
    window.addEventListener('online', sync, false);
    window.addEventListener('offline', sync, false);
    return () => {
      window.removeEventListener('online', sync);
      window.removeEventListener('offline', sync);
    };
  });
}

function subscribeOnline(onChange: () => void): () => void {
  return onlineManager.subscribe(() => onChange());
}

function readOnline(): boolean {
  return onlineManager.isOnline();
}

function readOnlineOnServer(): boolean {
  return true;
}

/** True unless the browser reports it has no network. */
export function useIsOnline(): boolean {
  return useSyncExternalStore(subscribeOnline, readOnline, readOnlineOnServer);
}
