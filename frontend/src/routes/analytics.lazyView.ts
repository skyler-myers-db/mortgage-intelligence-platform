import type { ComponentType, FunctionComponent } from 'react';
import { onlineManager } from '@tanstack/react-query';
import { lazyWithPreload, preloadBestEffort } from '../lib/lazyPreload';
import { saveDataRequested } from '../lib/prefetch';

/**
 * A lazily loaded Analytics view that takes props (2026-09-21 audit
 * bundle-06, W5e item 2b). lazyWithPreload forwards its props, renders a
 * preloaded module synchronously (no skeleton flash) and lets a failed
 * preload retry; this only types the wrapper with the view's own props.
 */
export type LazyView<P> = FunctionComponent<P> & { preload: () => Promise<unknown> };

export function lazyView<P>(load: () => Promise<ComponentType<P>>): LazyView<P> {
  return lazyWithPreload(() => load().then((view) => ({ default: view as unknown as ComponentType<unknown> }))) as unknown as LazyView<P>;
}

/**
 * Warm a chunk on intent (a tab hovered or focused): the module only, never a
 * query, and nothing while the browser asks to save data or is offline (the
 * fetch could only fail; lib/connectivity reads the same onlineManager).
 */
export function warmChunk(preload: (() => Promise<unknown>) | undefined): void {
  if (preload && !saveDataRequested() && onlineManager.isOnline()) preloadBestEffort(preload);
}
