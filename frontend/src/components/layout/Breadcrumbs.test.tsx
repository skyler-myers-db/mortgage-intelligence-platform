/**
 * @vitest-environment happy-dom
 *
 * Linked breadcrumbs (audit 2026-09-21 shell-04): the dossier crumb names the
 * borrower and returns to the exact filtered queue it was opened from.
 * Rendered through the real router so the links, their Link state and
 * aria-current are what a user gets.
 */

import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { clearQueueContext, type QueueContext } from '../../lib/queueContext';
import { publishQueueContext } from '../../lib/queueContextPublish';
import { Breadcrumbs } from './Breadcrumbs';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const IDS = ['B-0000000000001', 'B-0000000000002', 'B-0000000000003'];

function Probe() {
  const location = useLocation();
  return <output id="probe">{`${location.pathname}${location.search}|${JSON.stringify(location.state)}`}</output>;
}

describe('Breadcrumbs', () => {
  let container: HTMLDivElement;
  let root: Root;
  let queue: QueueContext | null;

  beforeEach(() => {
    window.sessionStorage.clear();
    clearQueueContext();
    queue = publishQueueContext({ search: '?state=IL&segment=itm', label: 'IL · In the Money', ids: IDS });
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  async function renderAt(pathname: string, state: unknown = null) {
    await act(async () => {
      root.render(
        <MemoryRouter initialEntries={[{ pathname, state }]}>
          <Breadcrumbs />
          <Routes>
            <Route path="*" element={<Probe />} />
          </Routes>
        </MemoryRouter>,
      );
    });
  }

  const nav = () => container.querySelector('nav[aria-label="Breadcrumb"]')!;
  const items = () => Array.from(nav().querySelectorAll('li')).map((li) => li.textContent?.replace('/', '').trim());

  it('links a dossier back to the exact filtered queue and marks the borrower as the current page', async () => {
    await renderAt(`/borrower-360/${IDS[1]}`, { queue });
    expect(nav().classList.contains('topbar__crumbs')).toBe(true);
    expect(items()).toEqual(['Lead Queue · IL · In the Money', IDS[1]]);
    const link = nav().querySelector<HTMLAnchorElement>('a.topbar__crumb-link')!;
    expect(link.getAttribute('href')).toBe('/lead-queue?state=IL&segment=itm');
    const current = nav().querySelector('[aria-current="page"]')!;
    expect(current.textContent).toBe(IDS[1]);
    expect(current.classList.contains('cur')).toBe(true);
    // A detail trail drops the product root so it fits the side track.
    expect(nav().querySelector('.topbar__crumbs-root')).toBeNull();

    await act(async () => link.click());
    expect(container.querySelector('#probe')?.textContent).toBe('/lead-queue?state=IL&segment=itm|null');
  });

  it('offer detail: queue, then the dossier (carrying the queue), then the page', async () => {
    await renderAt(`/offer-orchestrator/${IDS[0]}`, { queue });
    expect(items()).toEqual(['Lead Queue · IL · In the Money', IDS[0], 'Offer Orchestrator']);
    const links = Array.from(nav().querySelectorAll<HTMLAnchorElement>('a'));
    expect(links.map((a) => a.getAttribute('href'))).toEqual(['/lead-queue?state=IL&segment=itm', `/borrower-360/${IDS[0]}`]);
    await act(async () => links[1].click());
    const [path, state] = (container.querySelector('#probe')?.textContent ?? '').split('|');
    expect(path).toBe(`/borrower-360/${IDS[0]}`);
    expect(JSON.parse(state).queue.ids).toEqual(IDS);
  });

  it('falls back to the unfiltered queue for a borrower outside the published queue', async () => {
    await renderAt('/borrower-360/B-9999999999999');
    expect(items()).toEqual(['Lead Queue', 'B-9999999999999']);
    expect(nav().querySelector('a')?.getAttribute('href')).toBe('/lead-queue');
  });

  it('top-level pages keep the product root and one current crumb', async () => {
    await renderAt('/segment-intelligence');
    expect(nav().querySelector('.topbar__crumbs-root')?.textContent).toBe('Mortgage Intelligence Platform');
    expect(items()).toEqual(['Segment Intelligence']);
    expect(nav().querySelector('a')).toBeNull();
    expect(nav().querySelector('[aria-current="page"]')?.textContent).toBe('Segment Intelligence');
  });

  it('asset detail links to the governed-asset index', async () => {
    await renderAt('/data-estate/assets/lead_population');
    expect(items()).toEqual(['Data estate', 'Governed asset']);
    expect(nav().querySelector('a')?.getAttribute('href')).toBe('/admin-config');
  });
});
