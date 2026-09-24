/**
 * @vitest-environment happy-dom
 *
 * RUM stays identity- and borrower-free (audit 2026-09-21 shell-06 / shell-04).
 * The identity menu puts the actor's email on screen and the queue pager rides
 * masked borrower ids in React Router location state; neither may reach the
 * `/telemetry/rum` payload. This drives the real builder the way the app does:
 * a data router registered as the RUM route source (main.tsx), a navigation
 * whose location state carries both, a flush, and the body the beacon was
 * handed.
 */

import { createMemoryRouter } from 'react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushRum, installRum } from './rum';
import { setRumRouteSource } from './rumBridge';

const EMAIL = 'jane.doe@summit-mortgage.example';
const IDS = ['B-0123456789ABC', 'B-1123456789ABC'];

async function nextFrames(count: number): Promise<void> {
  for (let frame = 0; frame < count; frame += 1) {
    await new Promise((resolve) => requestAnimationFrame(() => resolve(undefined)));
  }
}

describe('RUM payload', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('reports the route template, never the actor email or the queue ids in location state', async () => {
    const bodies: string[] = [];
    const beacon = vi.fn((_url: string | URL, data?: BodyInit | null) => {
      if (data instanceof Blob) void data.text().then((text) => bodies.push(text));
      return true;
    });
    Object.defineProperty(navigator, 'sendBeacon', { configurable: true, value: beacon });

    const router = createMemoryRouter([{ path: '*', element: null }], { initialEntries: ['/lead-queue'] });
    setRumRouteSource((listener) => router.subscribe((state) => listener(state.location.pathname)));
    window.history.replaceState(null, '', '/lead-queue');
    installRum();

    await router.navigate(`/borrower-360/${IDS[1]}?from=${encodeURIComponent(EMAIL)}`, {
      state: { queue: { search: '?state=IL', ids: IDS, label: 'IL' }, actor: EMAIL },
    });
    await nextFrames(3);
    flushRum();
    await vi.waitFor(() => expect(bodies.length).toBeGreaterThan(0));

    const payload = bodies.join('\n');
    // Non-vacuity: the route change really was reported.
    expect(payload).toContain('"metric":"route_change"');
    expect(payload).toContain('/borrower-360/:borrower_id');
    expect(payload).toContain('"from_route":"/lead-queue"');
    expect(payload).not.toContain('@');
    expect(payload).not.toContain('jane');
    for (const id of IDS) expect(payload).not.toContain(id);
  });
});
