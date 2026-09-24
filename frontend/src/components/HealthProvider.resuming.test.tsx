// @vitest-environment happy-dom

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { onlineManager } from '@tanstack/react-query';
import type { HealthPayload } from '../lib/api';
import type { HealthHint } from '../lib/apiTypes';
import { _resetSessionStatusForTests } from '../lib/sessionStatus';
import { HealthProvider, applyDownUpDebounce, shouldPollFast, useHealth } from './HealthProvider';

/**
 * A warehouse resuming from auto-stop is not an outage (audit delivery-01),
 * and the poll carries the tab's idle hint for the activity keep-warm policy
 * (delivery-v1). Rendered through the real provider with fake timers.
 */

const deps = (warehouse: string): HealthPayload => ({
  status: 'ok',
  mode: 'live',
  dependencies: { warehouse, lakebase: 'up', genie: 'up' },
  circuit_breakers: { warehouse: 'closed', lakebase: 'closed', genie: 'closed' },
});
const RESUMING = deps('resuming');
const UP = deps('up');
const DOWN: HealthPayload = { ...deps('down'), status: 'degraded' };

describe('applyDownUpDebounce with resuming', () => {
  it('shows resuming at once, even straight after a down', () => {
    const afterDown = applyDownUpDebounce(DOWN, {}, 0, 5000).next;
    const { payload, next } = applyDownUpDebounce(RESUMING, afterDown, 100, 5000);

    expect(payload.dependencies?.warehouse).toBe('resuming');
    expect(next.warehouse).toEqual({ filtered: 'resuming', pendingUpSince: null });
  });

  it('flips resuming to up at once: a finished resume is not a flap', () => {
    const resuming = applyDownUpDebounce(RESUMING, {}, 0, 5000).next;
    const { payload } = applyDownUpDebounce(UP, resuming, 1, 5000);

    expect(payload.dependencies?.warehouse).toBe('up');
  });

  it('keeps the 5 s debounce for down to up', () => {
    const down = applyDownUpDebounce(DOWN, {}, 0, 5000).next;
    const first = applyDownUpDebounce(UP, down, 1000, 5000);

    expect(first.payload.dependencies?.warehouse).toBe('down');
    expect(applyDownUpDebounce(UP, first.next, 6000, 5000).payload.dependencies?.warehouse).toBe('up');
  });

  it('polls fast while resuming, and resuming is never degraded-cadence noise when up', () => {
    expect(shouldPollFast(RESUMING)).toBe(true);
    expect(shouldPollFast(UP)).toBe(false);
  });
});

function Probe() {
  const { warehouseResumingSince, degraded, health } = useHealth();
  return (
    <>
      <span data-testid="since">{warehouseResumingSince === null ? 'null' : String(warehouseResumingSince)}</span>
      <span data-testid="degraded">{String(degraded)}</span>
      <span data-testid="warehouse">{health?.dependencies?.warehouse ?? 'none'}</span>
    </>
  );
}

describe('HealthProvider resuming + idle hint', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-09-24T12:00:00Z'));
    _resetSessionStatusForTests();
    onlineManager.setOnline(true);
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

  async function mount(fetchHealth: (signal?: AbortSignal, hint?: HealthHint) => Promise<HealthPayload>) {
    await act(async () => {
      root.render(
        <HealthProvider pollIntervalOkMs={8000} pollIntervalDegradedMs={3000} debounceUpMs={5000} fetchHealth={fetchHealth}>
          <Probe />
        </HealthProvider>,
      );
    });
  }

  async function advance(ms: number) {
    await act(async () => {
      vi.advanceTimersByTime(ms);
    });
  }

  const text = (id: string) => container.querySelector(`[data-testid="${id}"]`)?.textContent;

  it('stamps warehouseResumingSince on entering resuming, polls fast, and clears it when up', async () => {
    let answer = RESUMING;
    const fetchHealth = vi.fn(async () => answer);
    await mount(fetchHealth);

    const mountedAt = Date.parse('2026-09-24T12:00:00Z');
    expect(text('warehouse')).toBe('resuming');
    expect(text('since')).toBe(String(mountedAt));
    expect(text('degraded'), 'resuming is not degraded').toBe('false');

    await advance(3000);
    expect(fetchHealth, 'the fast 3 s cadence while resuming').toHaveBeenCalledTimes(2);
    expect(text('since'), 'a second resuming probe keeps the first stamp').toBe(String(mountedAt));

    answer = UP;
    await advance(3000);
    expect(text('warehouse'), 'no 5 s debounce after a resume').toBe('up');
    expect(text('since')).toBe('null');

    await advance(3000);
    expect(fetchHealth, 'back to the 8 s cadence once up').toHaveBeenCalledTimes(3);
  });

  it('hands fetchHealth an integer idle hint that grows while idle and resets on input', async () => {
    const hints: Array<HealthHint | undefined> = [];
    const fetchHealth = vi.fn(async (_signal?: AbortSignal, hint?: HealthHint) => {
      hints.push(hint);
      return UP;
    });
    await mount(fetchHealth);
    expect(hints[0], 'mount counts as input').toEqual({ idleS: 0 });

    await advance(8000);
    await advance(8000);
    expect(hints[2]).toEqual({ idleS: 16 });

    act(() => {
      window.dispatchEvent(new Event('pointerdown'));
    });
    await advance(8000);
    expect(hints[3]).toEqual({ idleS: 8 });

    act(() => {
      window.dispatchEvent(new Event('keydown'));
    });
    await advance(8000);
    expect(hints[4]).toEqual({ idleS: 8 });
    for (const hint of hints) expect(Number.isInteger(hint?.idleS)).toBe(true);
  });
});
