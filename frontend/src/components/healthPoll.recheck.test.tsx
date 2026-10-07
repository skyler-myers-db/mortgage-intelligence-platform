// @vitest-environment happy-dom

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { onlineManager } from '@tanstack/react-query';
import type { HealthPayload } from '../lib/api';
import { requestHealthRecheck } from '../lib/healthRecheck';
import type { ActorIdentity } from '../lib/healthTrust';
import { _resetSessionStatusForTests, markSessionExpired } from '../lib/sessionStatus';
import { ACTOR_A } from '../test/actorKeys';
import { HealthProvider, useHealth } from './HealthProvider';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

/**
 * The health recheck (lib/healthRecheck, D-identity-review-a3): the actor
 * gate suspends a tab whose shared stamp another tab changed and asks for one
 * more TRUSTED probe. Proven on the real HealthProvider poll (components/
 * healthPoll.ts) with a scripted fetcher and fake timers: a probe now when it
 * may, else exactly one scheduled one; nothing while hidden, offline or
 * session-expired; and the first trusted probe started after the request
 * publishes a FRESH identity object for an unchanged key (or a suspended gate
 * would never hear "still A"). Every other probe keeps the object.
 */

/** Far beyond every timer a test advances: only a recheck can probe. */
const POLL_MS = 600_000;
const NUDGE_GAP_MS = 1_000;

const payload = (): HealthPayload => ({
  status: 'ok',
  mode: 'live',
  dependencies: { warehouse: 'up', lakebase: 'up', genie: 'up' },
  actor_cache_key: ACTOR_A,
});

const seen: Array<ActorIdentity | null> = [];

function IdentityProbe() {
  const { actorIdentity } = useHealth();
  seen.push(actorIdentity);
  return null;
}

function setHidden(hidden: boolean): void {
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => (hidden ? 'hidden' : 'visible') });
}

describe('the health recheck (lib/healthRecheck)', () => {
  let container: HTMLDivElement;
  let root: Root;
  let fetchHealth: ReturnType<typeof vi.fn<() => Promise<HealthPayload>>>;
  let release: (() => void) | null;

  beforeEach(() => {
    vi.useFakeTimers();
    _resetSessionStatusForTests();
    onlineManager.setOnline(true);
    setHidden(false);
    seen.length = 0;
    release = null;
    fetchHealth = vi.fn(async () => payload());
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
    onlineManager.setOnline(true);
    setHidden(false);
    vi.useRealTimers();
  });

  async function mount(): Promise<void> {
    await act(async () => {
      root.render(
        <HealthProvider pollIntervalOkMs={POLL_MS} pollIntervalDegradedMs={POLL_MS} debounceUpMs={0} fetchHealth={fetchHealth}>
          <IdentityProbe />
        </HealthProvider>,
      );
    });
    await settle(0);
    expect(fetchHealth, 'the boot probe').toHaveBeenCalledTimes(1);
  }

  async function settle(ms: number): Promise<void> {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(ms);
    });
  }

  async function recheck(): Promise<void> {
    await act(async () => {
      requestHealthRecheck();
      await Promise.resolve();
    });
  }

  /** The next probe stays in flight until release(). */
  function holdNextProbe(): void {
    fetchHealth.mockImplementationOnce(
      () => new Promise<HealthPayload>((resolve) => {
        release = () => resolve(payload());
      }),
    );
  }

  const latest = () => seen[seen.length - 1];

  it('probes at once when no probe is in flight and the nudge gap has passed', async () => {
    await mount();
    await settle(NUDGE_GAP_MS);
    await recheck();
    expect(fetchHealth).toHaveBeenCalledTimes(2);
  });

  it('schedules exactly one tick for when the gap elapses', async () => {
    await mount();
    await recheck();
    await recheck();
    expect(fetchHealth, 'inside the gap: not now').toHaveBeenCalledTimes(1);
    await settle(NUDGE_GAP_MS);
    expect(fetchHealth).toHaveBeenCalledTimes(2);
    await settle(NUDGE_GAP_MS * 5);
    expect(fetchHealth, 'one tick, not one per request').toHaveBeenCalledTimes(2);
  });

  it('schedules exactly one tick for when the in-flight probe ends; that earlier probe never satisfies the request', async () => {
    await mount();
    const first = latest();
    holdNextProbe();
    await settle(NUDGE_GAP_MS);
    await recheck();
    expect(fetchHealth, 'the recheck probe is in flight').toHaveBeenCalledTimes(2);
    holdNextProbe();
    await recheck();
    await recheck();
    expect(fetchHealth, 'nothing more while it is in flight').toHaveBeenCalledTimes(2);
    const startedBefore = release;
    await act(async () => {
      startedBefore?.();
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(latest(), 'it started before the newer requests, but after the first: fresh').not.toBe(first);
    expect(fetchHealth, 'one more probe once it ended').toHaveBeenCalledTimes(3);
    const afterSecond = latest();
    await act(async () => {
      release?.();
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(latest(), 'the probe started after the newer requests: fresh again').not.toBe(afterSecond);
    await settle(NUDGE_GAP_MS * 5);
    expect(fetchHealth).toHaveBeenCalledTimes(3);
  });

  it('publishes a fresh identity object for an unchanged key once, then dedupes again', async () => {
    await mount();
    const first = latest();
    expect(first).toEqual({ key: ACTOR_A });
    await settle(NUDGE_GAP_MS);
    await recheck();
    await settle(0);
    const fresh = latest();
    expect(fresh).toEqual({ key: ACTOR_A });
    expect(fresh, 'a recheck probe republishes the same key').not.toBe(first);

    // The next ordinary probe (here a second, unrelated nudge path: the poll
    // itself) keeps the object.
    await settle(POLL_MS);
    expect(fetchHealth).toHaveBeenCalledTimes(3);
    expect(latest(), 'the request was cleared: the dedupe is back').toBe(fresh);
  });

  it('an untrusted probe does not answer the request: the next trusted one does', async () => {
    await mount();
    const first = latest();
    fetchHealth.mockResolvedValueOnce({ status: 'unreachable', mode: 'unknown', dependencies: {} });
    await settle(NUDGE_GAP_MS);
    await recheck();
    await settle(0);
    expect(latest(), 'unreachable: nothing published').toBe(first);
    await settle(POLL_MS);
    expect(latest(), 'the next trusted probe is still fresh').not.toBe(first);
  });

  it('is a no-op while hidden; the probe on becoming visible answers it', async () => {
    await mount();
    const first = latest();
    await settle(NUDGE_GAP_MS);
    setHidden(true);
    await recheck();
    await settle(NUDGE_GAP_MS * 3);
    expect(fetchHealth).toHaveBeenCalledTimes(1);
    setHidden(false);
    await act(async () => {
      document.dispatchEvent(new Event('visibilitychange'));
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(fetchHealth).toHaveBeenCalledTimes(2);
    expect(latest(), 'the visibility probe satisfies the request').not.toBe(first);
  });

  it('is a no-op while offline and once the session has expired', async () => {
    await mount();
    await settle(NUDGE_GAP_MS);
    act(() => onlineManager.setOnline(false));
    await recheck();
    await settle(NUDGE_GAP_MS * 3);
    expect(fetchHealth, 'offline').toHaveBeenCalledTimes(1);
    act(() => onlineManager.setOnline(true));
    await settle(0);
    const afterOnline = fetchHealth.mock.calls.length;
    expect(afterOnline, 'the online path probes (and answers the request)').toBe(2);

    act(() => markSessionExpired({ method: 'GET', path: '/api/v1/health' }));
    await settle(NUDGE_GAP_MS);
    await recheck();
    await settle(NUDGE_GAP_MS * 3);
    expect(fetchHealth, 'session expired').toHaveBeenCalledTimes(afterOnline);
  });

  it('unsubscribes on cleanup', async () => {
    await mount();
    await settle(NUDGE_GAP_MS);
    act(() => {
      root.unmount();
    });
    root = createRoot(container);
    await recheck();
    await settle(NUDGE_GAP_MS * 3);
    expect(fetchHealth).toHaveBeenCalledTimes(1);
  });
});
