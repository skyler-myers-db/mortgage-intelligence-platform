/**
 * Fixtures for the wave-1c session-recovery lane (audit states-02, critic-v2,
 * shell-v1, states-10). Nothing here is registered by default: each test opts
 * into the failure it proves.
 */
import type { DegradeOptions, HttpMethod, MockApi } from '../mockApi';
import { defaultFixtures } from '../registry';

/** Every API path, version-normalized (`/api/v1/...` is matched as `/api/...`). */
export const EVERY_API_PATH = /^\/api(\/|$)/;

/**
 * Every API path except the health probe. With the probe left healthy the
 * TEST decides when the session ends (its own click), not the shell's
 * eight-second poll, which under load could open the blocking dialog before
 * the click it is waiting to make.
 */
export const EVERY_API_PATH_BUT_HEALTH = /^\/api\/(?!health(?:\/|$))/;

/**
 * The live Databricks Apps proxy's answer to `/api/*` with no session,
 * captured 2026-09-23: HTTP 401, `application/json`, body `{}` (the proxy's,
 * not FastAPI's).
 */
export const PROXY_SESSION_EXPIRED: DegradeOptions = { status: 401, body: {} };

/**
 * The proxy's answer to an unauthenticated PAGE request (`/`, `/assets/*`),
 * captured 2026-09-23: a 302 to the workspace's OIDC authorize URL. The host
 * is a placeholder; the production CSP blocks it, so following the redirect
 * would be a hygiene violation.
 */
export const PROXY_SIGN_IN_REDIRECT: DegradeOptions = {
  status: 302,
  headers: { Location: 'https://workspace.invalid/oidc/oauth2/v2.0/authorize?client_id=fixture' },
  body: {},
};

export interface HeldFixture {
  /** Requests RECEIVED (the mock's call log only records answered calls). */
  readonly received: number;
  release(): void;
}

/**
 * Hold one default fixture's reply until the test releases it, so a loading
 * state can be measured. Answers with the default registry's own payload.
 */
export function holdDefaultFixture(mockApi: MockApi, method: HttpMethod, pattern: string): HeldFixture {
  const entry = defaultFixtures().find((candidate) => candidate.method === method && candidate.pattern === pattern);
  if (!entry) throw new Error(`No default fixture registered for ${method} ${pattern}`);
  let release: () => void = () => undefined;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  let received = 0;
  mockApi.register(method, pattern, async (request) => {
    received += 1;
    await gate;
    return entry.handler(request);
  });
  return {
    get received() {
      return received;
    },
    release,
  };
}
