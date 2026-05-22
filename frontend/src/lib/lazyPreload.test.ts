import { describe, expect, it, vi } from 'vitest';
import { lazyWithPreload, preloadBestEffort } from './lazyPreload';

function DummyComponent() {
  return null;
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
});
