import { afterEach, describe, expect, it, vi } from 'vitest';
import { preloadRouteForPath, routePreloaders } from './routePreloaders';

const originals = Object.fromEntries(Object.entries(routePreloaders));

function stubRoutePreloaders() {
  const calls: string[] = [];
  const mutable = routePreloaders as unknown as Record<string, () => Promise<unknown>>;
  for (const key of Object.keys(mutable)) {
    mutable[key] = vi.fn(() => {
      calls.push(key);
      return Promise.resolve();
    });
  }
  return calls;
}

afterEach(() => {
  const mutable = routePreloaders as unknown as Record<string, () => Promise<unknown>>;
  for (const [key, original] of Object.entries(originals)) {
    mutable[key] = original;
  }
  vi.restoreAllMocks();
});

describe('preloadRouteForPath', () => {
  it('maps exact route paths to their static route chunk preloader', async () => {
    const calls = stubRoutePreloaders();

    preloadRouteForPath('/lead-queue');
    await Promise.resolve();

    expect(calls).toEqual(['/lead-queue']);
  });

  it('maps deep-linked borrower and offer routes by prefix', async () => {
    const calls = stubRoutePreloaders();

    preloadRouteForPath('/borrower-360/B-LOCAL-SMOKE?tab=evidence');
    preloadRouteForPath('/offer-orchestrator/B-LOCAL-SMOKE#approval');
    await Promise.resolve();

    expect(calls).toEqual(['/borrower-360', '/offer-orchestrator']);
  });

  it('ignores unknown paths without invoking any preloader', async () => {
    const calls = stubRoutePreloaders();

    preloadRouteForPath('/api/borrowers/B-LOCAL-SMOKE');
    preloadRouteForPath('/not-a-route');
    await Promise.resolve();

    expect(calls).toEqual([]);
  });
});
