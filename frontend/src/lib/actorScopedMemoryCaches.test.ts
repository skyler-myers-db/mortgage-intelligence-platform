import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  _resetActorScopedMemoryCachesForTests,
  clearActorScopedMemoryCaches,
  registerActorScopedMemoryCache,
} from './actorScopedMemoryCaches';

describe('actor-scoped memory cache registry', () => {
  afterEach(() => {
    _resetActorScopedMemoryCachesForTests();
  });

  it('clears every registered route-local memory cache', () => {
    const first = vi.fn();
    const second = vi.fn();
    registerActorScopedMemoryCache(first);
    registerActorScopedMemoryCache(second);

    clearActorScopedMemoryCaches();

    expect(first).toHaveBeenCalledTimes(1);
    expect(second).toHaveBeenCalledTimes(1);
  });

  it('continues clearing later caches if one clearer throws', () => {
    const throwing = vi.fn(() => {
      throw new Error('clear failed');
    });
    const later = vi.fn();
    registerActorScopedMemoryCache(throwing);
    registerActorScopedMemoryCache(later);

    expect(() => clearActorScopedMemoryCaches()).not.toThrow();
    expect(later).toHaveBeenCalledTimes(1);
  });

  it('unregisters cache clearers', () => {
    const clearer = vi.fn();
    const unregister = registerActorScopedMemoryCache(clearer);

    unregister();
    clearActorScopedMemoryCaches();

    expect(clearer).not.toHaveBeenCalled();
  });
});
