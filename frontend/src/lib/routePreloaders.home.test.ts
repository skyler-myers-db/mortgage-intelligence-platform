import { beforeEach, describe, expect, it, vi } from 'vitest';

/**
 * Audit bundle-07: the Home hero map's geometry used to be requested only from
 * a mount effect inside the map, a fourth sequential fetch after the route
 * chunk. The Home route loader now starts it beside the route chunk. These
 * tests exercise that loader directly (the layer where the defect lived), with
 * the route module and the geometry loader mocked so no chunk is really
 * evaluated.
 */

const geometry = vi.hoisted(() => ({
  loadUsaStateMap: vi.fn(),
}));

vi.mock('../components/mortgage/USStateMapData', () => ({
  loadUsaStateMap: geometry.loadUsaStateMap,
}));

vi.mock('../routes/home', () => ({
  default: () => null,
}));

async function settle() {
  for (let i = 0; i < 4; i += 1) await Promise.resolve();
}

// The app tsconfig deliberately excludes Node globals; this Vitest-only test
// reaches the process rejection hook structurally.
interface RejectionEmitter {
  on(event: 'unhandledRejection', listener: (reason: unknown) => void): unknown;
  off(event: 'unhandledRejection', listener: (reason: unknown) => void): unknown;
}
const nodeProcess = (globalThis as unknown as { process: RejectionEmitter }).process;

describe('HomeRoute.preload', () => {
  beforeEach(() => {
    vi.resetModules();
    geometry.loadUsaStateMap.mockReset();
  });

  it('starts the state geometry beside the Home route chunk, before any mount', async () => {
    geometry.loadUsaStateMap.mockReturnValue(Promise.resolve({ label: 'United States', viewBox: '0 0 1 1', locations: [] }));
    const { HomeRoute } = await import('./routePreloaders');

    await HomeRoute.preload();
    await settle();

    expect(geometry.loadUsaStateMap).toHaveBeenCalledTimes(1);
  });

  it('shares one loader between preload and render so geometry is warmed once', async () => {
    geometry.loadUsaStateMap.mockReturnValue(Promise.resolve({ label: 'United States', viewBox: '0 0 1 1', locations: [] }));
    const { HomeRoute } = await import('./routePreloaders');

    const first = HomeRoute.preload();
    const second = HomeRoute.preload();
    await Promise.all([first, second]);
    await settle();

    expect(first).toBe(second);
    expect(geometry.loadUsaStateMap).toHaveBeenCalledTimes(1);
  });

  it('never fails the route when the speculative geometry load rejects', async () => {
    geometry.loadUsaStateMap.mockReturnValue(Promise.reject(new Error('chunk offline')));
    const unhandled = vi.fn();
    nodeProcess.on('unhandledRejection', unhandled);
    try {
      const { HomeRoute } = await import('./routePreloaders');

      const mod = await HomeRoute.preload();
      await settle();

      expect(typeof mod.default).toBe('function');
      expect(geometry.loadUsaStateMap).toHaveBeenCalledTimes(1);
      expect(unhandled).not.toHaveBeenCalled();
    } finally {
      nodeProcess.off('unhandledRejection', unhandled);
    }
  });
});
