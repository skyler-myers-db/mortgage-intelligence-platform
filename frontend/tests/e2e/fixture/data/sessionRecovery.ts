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
 * The live Databricks Apps proxy's answer to `/api/*` with no session,
 * captured 2026-09-23: HTTP 401, `application/json`, body `{}` (the proxy's,
 * not FastAPI's).
 */
export const PROXY_SESSION_EXPIRED: DegradeOptions = { status: 401, body: {} };

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
