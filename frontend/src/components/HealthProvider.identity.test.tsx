// @vitest-environment happy-dom

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { onlineManager } from '@tanstack/react-query';
import type { HealthPayload } from '../lib/api';
import { markAuthFailedHealth, type ActorIdentity } from '../lib/healthTrust';
import { _resetSessionStatusForTests } from '../lib/sessionStatus';
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
    }, async () => payload('actor_a')]);
    expect(latest()).toBeNull();
    await nextPoll();
    expect(latest(), 'a thrown probe is untrusted').toBeNull();
    await nextPoll();
    expect(latest()).toEqual({ key: 'actor_a' });
  });

  it('an untrusted probe after a trusted one keeps the SAME object', async () => {
    const fetchHealth = await mount([
      async () => payload('actor_a'),
      async () => unreachable(),
      async () => {
        throw new TypeError('Failed to fetch');
      },
      async () => payload('actor_a'),
    ]);
    const first = latest();
    expect(first).toEqual({ key: 'actor_a' });
    await nextPoll();
    await nextPoll();
    await nextPoll();
    expect(fetchHealth).toHaveBeenCalledTimes(4);
    expect(latest(), 'unreachable, thrown and same-key probes never replace it').toBe(first);
  });

  it('a new key, a reachable null and an auth failure each replace it', async () => {
    await mount([
      async () => payload('actor_a'),
      async () => payload('actor_b'),
      async () => payload(undefined),
      async () => payload('actor_b'),
      async () => markAuthFailedHealth(unreachable()),
    ]);
    expect(latest()).toEqual({ key: 'actor_a' });
    await nextPoll();
    expect(latest()).toEqual({ key: 'actor_b' });
    await nextPoll();
    expect(latest(), 'the anonymous body is a trusted nobody').toEqual({ key: null });
    await nextPoll();
    expect(latest()).toEqual({ key: 'actor_b' });
    await nextPoll();
    expect(latest(), 'a marked auth failure is a trusted nobody').toEqual({ key: null });
  });
});
