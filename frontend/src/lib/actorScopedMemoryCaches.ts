type CacheClearer = () => void;

const ACTOR_SCOPED_MEMORY_CACHE_CLEARERS = new Set<CacheClearer>();

export function registerActorScopedMemoryCache(clearer: CacheClearer): () => void {
  ACTOR_SCOPED_MEMORY_CACHE_CLEARERS.add(clearer);
  return () => {
    ACTOR_SCOPED_MEMORY_CACHE_CLEARERS.delete(clearer);
  };
}

export function clearActorScopedMemoryCaches(): void {
  for (const clearer of ACTOR_SCOPED_MEMORY_CACHE_CLEARERS) {
    try {
      clearer();
    } catch {
      // Best effort: one route-local cache should not block the rest of
      // the actor-boundary reset.
    }
  }
}

export function _resetActorScopedMemoryCachesForTests(): void {
  ACTOR_SCOPED_MEMORY_CACHE_CLEARERS.clear();
}
