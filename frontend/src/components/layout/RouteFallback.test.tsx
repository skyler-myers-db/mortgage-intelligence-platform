// @vitest-environment happy-dom

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { onlineManager } from '@tanstack/react-query';
import { ROUTE_FALLBACK_DELAY_MS, RouteFallback } from './RouteFallback';

/**
 * The page-shaped route fallback (audit 2026-09-21 `states-10`): laid out at
 * once so its space is reserved, visible only after the show-delay, and
 * honest about waiting while offline.
 */

describe('RouteFallback', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.useFakeTimers();
    onlineManager.setOnline(true);
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
    onlineManager.setOnline(true);
    vi.useRealTimers();
  });

  const fallback = () => container.querySelector<HTMLElement>('[data-route-fallback]');

  it('reserves the page frame at once but stays invisible until the show-delay passes', async () => {
    await act(async () => {
      root.render(<RouteFallback />);
    });
    const el = fallback();
    expect(el, 'laid out immediately, so nothing shifts when it appears').not.toBeNull();
    expect(el?.className).toBe('main__content');
    expect(el?.querySelector('.main__inner > .proto-hero .skeleton--title')).not.toBeNull();
    expect(el?.querySelector('.surface__body--reserve')).not.toBeNull();
    expect(el?.style.visibility, 'a fast chunk or session check never flashes it').toBe('hidden');

    await act(async () => {
      vi.advanceTimersByTime(ROUTE_FALLBACK_DELAY_MS - 1);
    });
    expect(fallback()?.style.visibility).toBe('hidden');

    await act(async () => {
      vi.advanceTimersByTime(1);
    });
    expect(fallback()?.style.visibility).toBe('');
    expect(fallback()?.getAttribute('aria-busy')).toBe('true');
  });

  it('says it is waiting for the connection while offline, not loading', async () => {
    await act(async () => {
      onlineManager.setOnline(false);
      root.render(<RouteFallback />);
    });
    expect(fallback()?.getAttribute('aria-busy')).toBe('false');
    expect(fallback()?.textContent).toContain('Waiting for a connection');
    // A route chunk that was never preloaded cannot load offline: import()
    // rejects and the route error boundary takes over, with no reload on
    // reconnect. So the fallback asks for the connection and promises nothing.
    expect(fallback()?.textContent).toContain('Reconnect to load this page.');
    expect(fallback()?.textContent).not.toMatch(/as soon as|on its own/i);
  });
});
