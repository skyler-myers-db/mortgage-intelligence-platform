/**
 * @vitest-environment happy-dom
 *
 * useLiveSearchParams: the reader answers the search params the data router
 * holds NOW, ahead of the committed render, and the committed ones wherever
 * the router's location is not the caller's (no data router, another
 * pathname).
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, Route, Routes, createMemoryRouter, useSearchParams } from 'react-router';
import { RouterProvider } from 'react-router/dom';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { useLiveSearchParams } from './useLiveSearchParams';

const reads: URLSearchParams[] = [];

/** Reads at EVENT time, as a write handler does: a click on the button. */
function Probe() {
  const [searchParams] = useSearchParams();
  const liveSearchParams = useLiveSearchParams(searchParams);
  return (
    <>
      <output data-testid="committed">{searchParams.toString()}</output>
      <button type="button" data-testid="read" onClick={() => reads.push(liveSearchParams())}>Read</button>
    </>
  );
}

/** The app's shape (appRouter.tsx): a data router whose one catch-all route renders descendant <Routes>. */
function appRouter(url: string, keptQueueUrl?: string) {
  const routes = (
    <Routes location={keptQueueUrl}>
      <Route path="/lead-queue" element={<Probe />} />
      <Route path="*" element={<p>elsewhere</p>} />
    </Routes>
  );
  return createMemoryRouter([{ path: '*', element: routes }], { initialEntries: [url] });
}

describe('useLiveSearchParams', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
  });

  const committed = () => container.querySelector('[data-testid="committed"]')?.textContent;
  /** One read through the committed render's handler (synchronous: no act, so nothing commits around it). */
  const readLive = (): URLSearchParams => {
    container.querySelector('[data-testid="read"]')?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    const read = reads.pop();
    if (!read) throw new Error('the probe did not read');
    return read;
  };

  it('reads the URL the data router holds while React still paints the previous one', async () => {
    const router = appRouter('/lead-queue?state=TX');
    await act(async () => {
      root.render(<RouterProvider router={router} />);
    });
    expect(committed()).toBe('state=TX');
    expect(readLive().toString()).toBe('state=TX');

    let seenInTheGap = { live: '', committed: '' };
    await act(async () => {
      void router.navigate('/lead-queue?state=TX&hide=segments');
      // The router has moved; act() has not let React commit it yet.
      seenInTheGap = { live: readLive().toString(), committed: committed() ?? '' };
    });
    expect(seenInTheGap).toEqual({ live: 'state=TX&hide=segments', committed: 'state=TX' });
    expect(committed()).toBe('state=TX&hide=segments');
    expect(readLive().toString()).toBe('state=TX&hide=segments');
    router.dispose();
  });

  it('answers a copy, so a writer may change it', async () => {
    const router = appRouter('/lead-queue?state=TX');
    await act(async () => {
      root.render(<RouterProvider router={router} />);
    });
    readLive().set('hide', 'rate');
    expect(readLive().toString()).toBe('state=TX');
    expect(router.state.location.search).toBe('?state=TX');
    router.dispose();
  });

  it('answers the committed params while the router is on another pathname (a kept-alive queue)', async () => {
    // LeadQueueKeepAlive renders the hidden queue at its kept location.
    const router = appRouter('/borrower-360/B-0123456789ABC?hide=rate', '/lead-queue?state=IL');
    await act(async () => {
      root.render(<RouterProvider router={router} />);
    });
    expect(committed()).toBe('state=IL');
    expect(readLive().toString()).toBe('state=IL');
    router.dispose();
  });

  it('answers the committed params without a data router', async () => {
    await act(async () => {
      root.render(
        <MemoryRouter initialEntries={['/lead-queue?state=TX&hide=rate']}>
          <Routes>
            <Route path="/lead-queue" element={<Probe />} />
          </Routes>
        </MemoryRouter>,
      );
    });
    expect(committed()).toBe('state=TX&hide=rate');
    expect(readLive().toString()).toBe('state=TX&hide=rate');
    // A copy here too: the committed object is the router hook's own.
    readLive().delete('hide');
    expect(readLive().toString()).toBe('state=TX&hide=rate');
  });
});
