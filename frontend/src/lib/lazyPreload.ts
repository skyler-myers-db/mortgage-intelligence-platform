import { lazy, type ComponentType, type LazyExoticComponent } from 'react';

export type PreloadableComponent<T extends ComponentType<unknown>> = LazyExoticComponent<T> & {
  preload: () => Promise<{ default: T }>;
};

export function lazyWithPreload<T extends ComponentType<unknown>>(
  loader: () => Promise<{ default: T }>,
): PreloadableComponent<T> {
  let promise: Promise<{ default: T }> | null = null;
  const load = () => {
    promise ??= loader().catch((err: unknown) => {
      promise = null;
      throw err;
    });
    return promise;
  };
  const Component = lazy(load) as PreloadableComponent<T>;
  Component.preload = load;
  return Component;
}

export function preloadBestEffort(preload: () => Promise<unknown>): void {
  void preload().catch(() => {
    // Speculative preload is a performance hint. A transient chunk
    // failure must not become an unhandled rejection or alter later
    // explicit navigation/open behavior; lazyWithPreload clears failed
    // cached promises so the real path can retry.
  });
}
