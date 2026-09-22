import { describe, expect, it } from 'vitest';
import statesTopology from 'us-atlas/states-albers-10m.json';
import { createUsaStateMapLoader, loadUsaStateMap } from './USStateMapData';

/**
 * Audit bundle-07: loadUsaStateMap is shared by the Home hero map, the Genie
 * answer map and the Home route preloader. It must be single-flight (one
 * fetch + one TopoJSON conversion per page load) and must not stay poisoned
 * after a failed speculative load.
 */

describe('loadUsaStateMap', () => {
  it('returns one shared promise and converts the real topology by name', async () => {
    const first = loadUsaStateMap();
    const second = loadUsaStateMap();
    expect(first).toBe(second);

    const map = await first;
    expect(map.label).toBe('United States');
    expect(map.locations.length).toBeGreaterThanOrEqual(50);
    expect(map.locations.find((location) => location.id === 'il')?.name).toBe('Illinois');
    expect(loadUsaStateMap()).toBe(first);
  });
});

describe('createUsaStateMapLoader', () => {
  it('imports the topology once for concurrent and repeat callers', async () => {
    let attempts = 0;
    const load = createUsaStateMapLoader(async () => {
      attempts += 1;
      return { default: statesTopology };
    });

    const [a, b] = await Promise.all([load(), load()]);
    expect(a).toBe(b);
    expect(await load()).toBe(a);
    expect(attempts).toBe(1);
  });

  it('drops a rejected load so the next caller retries instead of inheriting the failure', async () => {
    let attempts = 0;
    const load = createUsaStateMapLoader(async () => {
      attempts += 1;
      if (attempts === 1) throw new Error('geometry chunk offline');
      return { default: statesTopology };
    });

    await expect(load()).rejects.toThrow('geometry chunk offline');

    const map = await load();
    expect(map.locations.length).toBeGreaterThanOrEqual(50);
    expect(attempts).toBe(2);
  });
});
