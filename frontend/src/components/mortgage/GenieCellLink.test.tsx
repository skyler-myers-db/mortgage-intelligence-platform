/**
 * @vitest-environment happy-dom
 *
 * GenieCellLink (audit bundle-09 item 1): a Genie table's masked borrower id
 * warms the Borrower 360 route CHUNK on hover or keyboard focus, and nothing
 * else: no fetch (the dossier read writes a VIEW_BORROWER audit row, so it
 * stays click-only), nothing under Save-Data, and a non-borrower cell link
 * (a state's Lead Queue) warms nothing.
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const preloadRouteForPath = vi.fn();
vi.mock('../../lib/routePreloaders', () => ({ preloadRouteForPath: (path: string) => preloadRouteForPath(path) }));
const saveData = vi.hoisted(() => ({ on: false }));
vi.mock('../../lib/prefetch', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../lib/prefetch')>()),
  saveDataRequested: () => saveData.on,
}));

import { GenieCellLink } from './GenieCellLink';

const BORROWER_HREF = '/borrower-360/B-0000000000001';

describe('GenieCellLink', () => {
  let root: Root;
  let container: HTMLDivElement;
  const fetchSpy = vi.fn();

  beforeEach(() => {
    preloadRouteForPath.mockReset();
    saveData.on = false;
    fetchSpy.mockReset();
    vi.stubGlobal('fetch', fetchSpy);
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.unstubAllGlobals();
  });

  function render(href: string): HTMLAnchorElement {
    act(() => {
      root.render(
        <MemoryRouter>
          <GenieCellLink href={href}>B-0000000000001</GenieCellLink>
        </MemoryRouter>,
      );
    });
    return container.querySelector('a') as HTMLAnchorElement;
  }

  it('renders the mono cell link to the borrower', () => {
    const link = render(BORROWER_HREF);
    expect(link.className).toBe('mono');
    expect(link.getAttribute('href')).toBe(BORROWER_HREF);
  });

  it('warms the Borrower 360 chunk on hover and on focus, with no request', () => {
    const link = render(BORROWER_HREF);
    act(() => {
      link.dispatchEvent(new PointerEvent('pointerover', { bubbles: true }));
      link.dispatchEvent(new PointerEvent('pointerenter', { bubbles: false }));
    });
    act(() => link.focus());
    expect(preloadRouteForPath).toHaveBeenCalledWith(BORROWER_HREF);
    expect(preloadRouteForPath.mock.calls.every(([path]) => path === BORROWER_HREF)).toBe(true);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it('warms nothing under Save-Data', () => {
    saveData.on = true;
    const link = render(BORROWER_HREF);
    act(() => link.focus());
    expect(preloadRouteForPath).not.toHaveBeenCalled();
  });

  it('warms nothing for a state cell that opens the Lead Queue', () => {
    const link = render('/lead-queue?state=TX');
    act(() => link.focus());
    expect(preloadRouteForPath).not.toHaveBeenCalled();
  });
});
