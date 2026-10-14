/**
 * @vitest-environment happy-dom
 *
 * useLazyModule reads the module cache as an external store (runtime-04
 * slice 3(b)): a compiled consumer renders a module that loaded through
 * ANY path, and a fresh hook re-attempts an import that failed (W5e
 * correction C2: the Lead Queue's 5d Retry depends on it).
 */
import { act, createElement } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { mount } from '../../test/render';
import { lazyModule, useLazyModule, type LazyModule } from './useLazyModule';

interface Chunk { label: string }

function Probe({ source, wanted }: { source: LazyModule<Chunk>; wanted: boolean }) {
  const { module, failed } = useLazyModule(source, wanted);
  return createElement('output', null, module ? module.label : failed ? 'failed' : 'waiting');
}

async function flush() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

describe('lazyModule', () => {
  it('notifies each subscriber once when the module loads, and stops after unsubscribe', async () => {
    const source = lazyModule<Chunk>(() => Promise.resolve({ label: 'ready' }));
    const kept = vi.fn();
    const dropped = vi.fn();
    source.subscribe(kept);
    source.subscribe(dropped)();

    await source.load();
    await source.load();

    expect(kept).toHaveBeenCalledTimes(1);
    expect(dropped).not.toHaveBeenCalled();
    expect(source.current()).toEqual({ label: 'ready' });
  });
});

describe('useLazyModule', () => {
  it('re-renders a consumer that does not want the module when the cache fills through load()', async () => {
    const source = lazyModule<Chunk>(() => Promise.resolve({ label: 'loaded elsewhere' }));
    const { container } = await mount(createElement(Probe, { source, wanted: false }));
    expect(container.textContent).toBe('waiting');

    await act(async () => {
      await source.load();
    });

    expect(container.textContent).toBe('loaded elsewhere');
  });

  it('loads on need and renders the module', async () => {
    const loader = vi.fn(() => Promise.resolve({ label: 'on need' }));
    const source = lazyModule<Chunk>(loader);
    const { container } = await mount(createElement(Probe, { source, wanted: true }));
    await flush();

    expect(container.textContent).toBe('on need');
    expect(loader).toHaveBeenCalledTimes(1);
  });

  it('reports a failed import, and a fresh instance re-attempts it', async () => {
    let attempt = 0;
    const loader = vi.fn(() => {
      attempt += 1;
      return attempt === 1 ? Promise.reject(new Error('Chunk unavailable')) : Promise.resolve({ label: 'second try' });
    });
    const source = lazyModule<Chunk>(loader);
    const first = await mount(createElement(Probe, { source, wanted: true }));
    await flush();
    expect(first.container.textContent).toBe('failed');
    expect(loader).toHaveBeenCalledTimes(1);

    // The failed instance does not loop: nothing changed, so no new import.
    await first.rerender(createElement(Probe, { source, wanted: true }));
    await flush();
    expect(loader).toHaveBeenCalledTimes(1);

    const second = await mount(createElement(Probe, { source, wanted: true }));
    await flush();
    expect(loader).toHaveBeenCalledTimes(2);
    expect(second.container.textContent).toBe('second try');
    // The cache filled, so the first instance is no longer failed either.
    expect(first.container.textContent).toBe('second try');
  });

  it('resolves an undefined import (a prevented vite:preloadError) to a failure', async () => {
    const source = lazyModule<Chunk>(() => Promise.resolve(undefined));
    const { container } = await mount(createElement(Probe, { source, wanted: true }));
    await flush();

    expect(container.textContent).toBe('failed');
    expect(source.current()).toBeNull();
  });
});
