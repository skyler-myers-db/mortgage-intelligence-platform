/**
 * @vitest-environment happy-dom
 */

import { Suspense, act, createElement, useEffect, type ReactNode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { isChunkLoadError } from './chunkLoadError';
import { hasRenderBlockedChunkLoad, lazyWithPreload, preloadBestEffort } from './lazyPreload';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function DummyComponent() {
  return null;
}

function Loaded() {
  return createElement('div', { 'data-testid': 'loaded' }, 'route content');
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe('lazyWithPreload', () => {
  it('retries after a failed preload instead of caching the rejection forever', async () => {
    const loader = vi
      .fn<() => Promise<{ default: typeof DummyComponent }>>()
      .mockRejectedValueOnce(new Error('transient chunk failure'))
      .mockResolvedValueOnce({ default: DummyComponent });
    const Component = lazyWithPreload(loader);

    await expect(Component.preload()).rejects.toThrow('transient chunk failure');
    await expect(Component.preload()).resolves.toEqual({ default: DummyComponent });

    expect(loader).toHaveBeenCalledTimes(2);
  });

  it('reuses a successful preload promise', async () => {
    const loader = vi
      .fn<() => Promise<{ default: typeof DummyComponent }>>()
      .mockResolvedValue({ default: DummyComponent });
    const Component = lazyWithPreload(loader);

    await Component.preload();
    await Component.preload();

    expect(loader).toHaveBeenCalledTimes(1);
  });

  it('swallows best-effort preload failures', async () => {
    const preload = vi.fn<() => Promise<unknown>>(() => Promise.reject(new Error('chunk failed')));

    preloadBestEffort(preload);
    await Promise.resolve();

    expect(preload).toHaveBeenCalledTimes(1);
  });

  it('treats a loader that resolves without a module as a chunk-load failure', async () => {
    // What Vite hands back when a vite:preloadError listener preventDefault()s.
    const loader = vi.fn(() => Promise.resolve(undefined as unknown as { default: typeof DummyComponent }));
    const Component = lazyWithPreload(loader);

    const failure = await Component.preload().catch((err: unknown) => err);

    expect(isChunkLoadError(failure)).toBe(true);
  });
});

describe('lazyWithPreload rendering', () => {
  let root: Root;
  let container: HTMLElement;
  let fallbackRenders: number;

  function Fallback() {
    fallbackRenders += 1;
    return createElement('div', { 'data-testid': 'route-fallback' }, 'loading');
  }

  function withSuspense(child: ReactNode) {
    return createElement(Suspense, { fallback: createElement(Fallback) }, child);
  }

  beforeEach(() => {
    fallbackRenders = 0;
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
  });

  it('renders a preloaded route synchronously: the Suspense fallback never renders', async () => {
    const Component = lazyWithPreload(() => Promise.resolve({ default: Loaded }));
    await Component.preload();

    // flushSync-style assertion: the very first commit already has the route.
    act(() => {
      root.render(withSuspense(createElement(Component)));
    });

    expect(container.textContent).toBe('route content');
    expect(fallbackRenders).toBe(0);
    expect(container.querySelector('[data-testid="route-fallback"]')).toBeNull();
  });

  it('still suspends on the fallback for a route that was never preloaded', async () => {
    const gate = deferred<{ default: typeof Loaded }>();
    const Component = lazyWithPreload(() => gate.promise);

    await act(async () => {
      root.render(withSuspense(createElement(Component)));
    });
    expect(container.textContent).toBe('loading');
    expect(hasRenderBlockedChunkLoad()).toBe(true);

    await act(async () => {
      gate.resolve({ default: Loaded });
      await gate.promise;
    });
    expect(container.textContent).toBe('route content');
    expect(hasRenderBlockedChunkLoad()).toBe(false);
  });

  it('renders after a rejected preload by running a fresh import', async () => {
    const loader = vi
      .fn<() => Promise<{ default: typeof Loaded }>>()
      .mockRejectedValueOnce(new TypeError('Failed to fetch dynamically imported module: /assets/x.js'))
      .mockResolvedValueOnce({ default: Loaded });
    const Component = lazyWithPreload(loader);
    await expect(Component.preload()).rejects.toThrow('dynamically imported module');

    await act(async () => {
      root.render(withSuspense(createElement(Component)));
    });
    await act(async () => {
      await Promise.resolve();
    });

    expect(loader).toHaveBeenCalledTimes(2);
    expect(container.textContent).toBe('route content');
  });

  it('does not count a speculative preload as a blocked render', async () => {
    const gate = deferred<{ default: typeof Loaded }>();
    const Component = lazyWithPreload(() => gate.promise);

    const pending = Component.preload();
    expect(hasRenderBlockedChunkLoad()).toBe(false);

    gate.resolve({ default: Loaded });
    await pending;
    expect(hasRenderBlockedChunkLoad()).toBe(false);
  });

  it('keeps a committed route mounted when its parent re-renders after the chunk resolved', async () => {
    let mounts = 0;
    function Counted() {
      useEffect(() => {
        mounts += 1;
      }, []);
      return createElement('div', null, 'route content');
    }
    const gate = deferred<{ default: typeof Counted }>();
    const Component = lazyWithPreload(() => gate.promise);
    const tree = (tick: number) =>
      createElement('div', { 'data-tick': tick }, withSuspense(createElement(Component)));

    await act(async () => {
      root.render(tree(0));
    });
    await act(async () => {
      gate.resolve({ default: Counted });
      await gate.promise;
    });
    expect(mounts).toBe(1);

    // The module is now cached. A parent re-render must not swap the element
    // type under the mounted route: a remount would replay its effects, and
    // route reads such as GET /leads write VIEW_* audit rows.
    await act(async () => {
      root.render(tree(1));
    });
    expect(mounts).toBe(1);
    expect(container.textContent).toBe('route content');
  });
});
