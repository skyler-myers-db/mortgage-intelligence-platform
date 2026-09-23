/**
 * Client-side failure classification for the fetch core (audit 2026-09-21
 * `states-02`, `critic-v2`, `shell-v1`).
 *
 * Captured on the live Databricks Apps proxy (2026-09-23, no session): every
 * `/api/*` call answers HTTP 401 with the proxy's own `{}` JSON body, while
 * `/` and `/assets/*` answer 302 to the workspace's OIDC authorize URL. The
 * backend also raises 401 itself when the forwarded identity is missing
 * (`backend/services/rbac.py`), and that is its only 401. So:
 *
 *   - 401 on `/api`                         -> `session_expired`, directly.
 *   - opaque redirect, or a 2xx body that
 *     is not JSON (an HTML sign-in page)    -> ONE `/api/v1/health` probe with
 *                                              `redirect: 'manual'` decides.
 *                                              FastAPI's trailing-slash 307s
 *                                              are opaque redirects too, so a
 *                                              redirect alone never proves
 *                                              expiry.
 *   - fetch rejected with a TypeError       -> `offline` while
 *                                              `navigator.onLine` is false,
 *                                              else `unreachable`.
 *
 * The messages below are what a route prints when it shows `error.message`:
 * buyer-readable copy, never the browser's "Failed to fetch" or a JSON
 * SyntaxError. Nothing here is sent anywhere; client telemetry must never
 * carry messages, URLs with ids, or borrower ids.
 */
import { apiPath } from './apiPaths';

/** Reasons the client assigns itself, next to the backend's transient reasons. */
export type ClientFailureReason = 'session_expired' | 'offline' | 'unreachable' | 'unreadable_response';

export const CLIENT_FAILURE_MESSAGES: Readonly<Record<ClientFailureReason, string>> = {
  session_expired: 'Your session ended. Reload to sign in.',
  offline: 'You are offline. This will load when your connection returns.',
  unreachable: 'The app could not be reached. Check your connection, then try again.',
  unreadable_response: 'The server sent a response the app could not read.',
};

export type SessionProbeVerdict = 'expired' | 'active' | 'offline' | 'unreachable';

/** `navigator.onLine === false` is reliable; `true` only means "has a network". */
export function isBrowserOffline(): boolean {
  return typeof navigator !== 'undefined' && navigator.onLine === false;
}

/** A fetch that rejected with a TypeError never reached the server. */
export function networkFailureReason(): 'offline' | 'unreachable' {
  return isBrowserOffline() ? 'offline' : 'unreachable';
}

/** True for the TypeError `fetch()` rejects with on a network failure. */
export function isNetworkFailure(err: unknown): boolean {
  return err instanceof TypeError;
}

const networkFailureListeners = new Set<() => void>();

/**
 * Tell the shell a request could not reach the server while the browser was
 * online. HealthProvider listens and probes sooner than its next scheduled
 * poll, so "Connection lost" does not wait out an eight-second healthy cadence.
 * Carries nothing: no path, no message.
 */
export function reportNetworkFailure(): void {
  for (const listener of [...networkFailureListeners]) listener();
}

export function subscribeNetworkFailures(listener: () => void): () => void {
  networkFailureListeners.add(listener);
  return () => {
    networkFailureListeners.delete(listener);
  };
}

/**
 * Ask the health endpoint, without following redirects, whether the session
 * is still valid. Only called after an ambiguous response, so at most one
 * probe per failed request. A caller abort propagates; every other failure is
 * reported as a verdict.
 */
export async function probeSession(signal?: AbortSignal): Promise<SessionProbeVerdict> {
  let res: Response;
  try {
    res = await fetch(apiPath('/health'), {
      redirect: 'manual',
      cache: 'no-store',
      headers: { Accept: 'application/json' },
      signal,
    });
  } catch (err) {
    if (err instanceof Error && err.name === 'AbortError') throw err;
    return networkFailureReason();
  }
  if (res.type === 'opaqueredirect' || res.status === 401) return 'expired';
  // A 5xx from the proxy means the app is down, not that the session ended.
  if (!res.ok) return 'unreachable';
  try {
    await res.json();
    return 'active';
  } catch (err) {
    if (err instanceof Error && err.name === 'AbortError') throw err;
    return isNetworkFailure(err) ? networkFailureReason() : 'expired';
  }
}
