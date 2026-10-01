// @vitest-environment happy-dom

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider, onlineManager } from '@tanstack/react-query';
import type { HealthPayload } from '../lib/api';
import { markAuthFailedHealth, type ActorIdentity } from '../lib/healthTrust';
import { _resetSessionStatusForTests } from '../lib/sessionStatus';
import { ACTOR_A, ACTOR_B } from '../test/actorKeys';
import { HealthProvider, useHealth } from './HealthProvider';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

/**
 * HealthProvider's `actorIdentity` (Genie residual #3): only a TRUSTED probe
 * may set it (lib/healthTrust), and it is replaced only when the key changes.
 * An unreachable probe, or a fetcher that throws, never touches it.
 */

const payload = (key: string | null | undefined): HealthPayload => ({
  status: 'ok',
  mode: 'live',
  dependencies: { warehouse: 'up', lakebase: 'up', genie: 'up' },
  ...(key === undefined ? {} : { actor_cache_key: key }),
});
const unreachable = (): HealthPayload => ({ status: 'unreachable', mode: 'unknown', dependencies: {} });

const seen: Array<ActorIdentity | null> = [];

function IdentityProbe() {
  const { actorIdentity } = useHealth();
  seen.push(actorIdentity);
  return null;
}

describe('HealthProvider actorIdentity', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.useFakeTimers();
    _resetSessionStatusForTests();
    onlineManager.setOnline(true);
    seen.length = 0;
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
    _resetSessionStatusForTests();
    vi.useRealTimers();
  });

  /** Mounts the provider over a scripted fetcher; each poll takes the next answer. */
  async function mount(answers: Array<() => Promise<HealthPayload>>) {
    const fetchHealth = vi.fn(() => {
      const next = answers.shift();
      return next ? next() : Promise.resolve(payload('never-asked'));
    });
    await act(async () => {
      root.render(
        <HealthProvider pollIntervalOkMs={1000} pollIntervalDegradedMs={1000} debounceUpMs={0} fetchHealth={fetchHealth}>
          <IdentityProbe />
        </HealthProvider>,
      );
    });
    return fetchHealth;
  }

  async function nextPoll() {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
  }

  const latest = () => seen[seen.length - 1];

  it('is null until the first trusted probe, and stays null through untrusted ones', async () => {
    await mount([async () => unreachable(), async () => {
      throw new Error('custom fetcher failed');
    }, async () => payload(ACTOR_A)]);
    expect(latest()).toBeNull();
    await nextPoll();
    expect(latest(), 'a thrown probe is untrusted').toBeNull();
    await nextPoll();
    expect(latest()).toEqual({ key: ACTOR_A });
  });

  it('an untrusted probe after a trusted one keeps the SAME object', async () => {
    const fetchHealth = await mount([
      async () => payload(ACTOR_A),
      async () => unreachable(),
      async () => {
        throw new TypeError('Failed to fetch');
      },
      async () => payload(ACTOR_A),
    ]);
    const first = latest();
    expect(first).toEqual({ key: ACTOR_A });
    await nextPoll();
    await nextPoll();
    await nextPoll();
    expect(fetchHealth).toHaveBeenCalledTimes(4);
    expect(latest(), 'unreachable, thrown and same-key probes never replace it').toBe(first);
  });

  it('a new key, a reachable null and an auth failure each replace it', async () => {
    await mount([
      async () => payload(ACTOR_A),
      async () => payload(ACTOR_B),
      async () => payload(undefined),
      async () => payload(ACTOR_B),
      async () => markAuthFailedHealth(unreachable()),
    ]);
    expect(latest()).toEqual({ key: ACTOR_A });
    await nextPoll();
    expect(latest()).toEqual({ key: ACTOR_B });
    await nextPoll();
    expect(latest(), 'the anonymous body is a trusted nobody').toEqual({ key: null });
    await nextPoll();
    expect(latest()).toEqual({ key: ACTOR_B });
    await nextPoll();
    expect(latest(), 'a marked auth failure is a trusted nobody').toEqual({ key: null });
  });
});

/**
 * The session seed (D-identity-review-b part B): before the first trusted
 * probe, the boot-primed /api/session body may name the actor, so the gate
 * opens without waiting for the poll. Read from the query cache only: no
 * observer, no fetch; health stays the only ongoing detector.
 */
describe('HealthProvider actorIdentity: the /api/session seed', () => {
  let container: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;
  let fetches: string[];

  const SESSION_KEY = ['session', 'access'] as const;
  const sessionBody = (key: unknown) => ({ can_access_admin: false, can_approve: true, actor_cache_key: key });

  beforeEach(() => {
    _resetSessionStatusForTests();
    seen.length = 0;
    fetches = [];
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      fetches.push(typeof input === 'string' ? input : input instanceof URL ? input.href : input.url);
      return new Response('{}', { status: 404 });
    }));
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
    queryClient.clear();
    vi.unstubAllGlobals();
    _resetSessionStatusForTests();
  });

  /** A probe that never answers: only the seed can set the identity. */
  const heldProbe = () => new Promise<HealthPayload>(() => undefined);

  async function mount(fetchHealth: () => Promise<HealthPayload> = heldProbe) {
    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <HealthProvider pollIntervalOkMs={1000} pollIntervalDegradedMs={1000} debounceUpMs={0} fetchHealth={fetchHealth}>
            <IdentityProbe />
          </HealthProvider>
        </QueryClientProvider>,
      );
    });
  }

  const latest = () => seen[seen.length - 1];

  it('a primed session seeds the identity before the first probe, with no observer and no session fetch', async () => {
    queryClient.setQueryData(SESSION_KEY, sessionBody(ACTOR_A));
    await mount();
    expect(latest()).toEqual({ key: ACTOR_A });
    expect(queryClient.getQueryCache().find({ queryKey: SESSION_KEY })?.getObserversCount()).toBe(0);
    expect(fetches.filter((url) => url.includes('/session'))).toEqual([]);
  });

  it('a session that lands after mount seeds from its FIRST success only', async () => {
    await mount();
    expect(latest()).toBeNull();
    await act(async () => {
      queryClient.setQueryData(SESSION_KEY, sessionBody(null));
    });
    expect(latest(), 'a null key is a trusted nobody').toEqual({ key: null });
    await act(async () => {
      queryClient.setQueryData(SESSION_KEY, sessionBody(ACTOR_B));
    });
    expect(latest(), 'a later success never re-seeds').toEqual({ key: null });
    expect(fetches).toEqual([]);
  });

  it('a malformed session body seeds nothing', async () => {
    queryClient.setQueryData(SESSION_KEY, sessionBody('bob@x'));
    await mount();
    expect(latest()).toBeNull();
  });

  it('never overrides a trusted health observation', async () => {
    let answer: (payload: HealthPayload) => void = () => undefined;
    await mount(() => new Promise<HealthPayload>((resolve) => {
      answer = resolve;
    }));
    await act(async () => {
      answer(payload(ACTOR_B));
    });
    expect(latest()).toEqual({ key: ACTOR_B });
    await act(async () => {
      queryClient.setQueryData(SESSION_KEY, sessionBody(ACTOR_A));
    });
    expect(latest(), 'health spoke first').toEqual({ key: ACTOR_B });
  });
});
