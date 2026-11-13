/**
 * @vitest-environment happy-dom
 *
 * The Lead Queue's column WRITES under the app's data router
 * (lead-queue.columns.ts useLeadQueueColumnWrites, hooks/useLiveSearchParams),
 * through the route's own HIDE slot and control.
 *
 * The router pushes a `?hide=` write at once and commits it to React in a
 * transition. A write derived from the committed render lost an update: a
 * second HIDE pick inside that gap rebuilt `hide` from the stale set and
 * dropped the first column. Each case below makes two writes in ONE act(), so
 * React commits nothing between them, which is the gap at its widest; the
 * production-build proof is tests/e2e/fixture/lead-queue-columns.fixture.spec.ts.
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, Route, Routes, createMemoryRouter, useSearchParams } from 'react-router';
import { RouterProvider } from 'react-router/dom';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import type { SavedViewPrefs } from './lead-queue.savedViewPrefs';

const prefs = vi.hoisted(() => ({ current: { defaultView: null, recent: [] } as SavedViewPrefs }));

// Synchronous factory: lead-queue.columns.ts reads only this hook from the module.
vi.mock('./lead-queue.savedViewPrefs', () => ({ useSavedViewPrefs: () => prefs.current }));

import { hiddenAfterPick, useLeadQueueColumnState, useLeadQueueColumnWrites } from './lead-queue.columns';
import { LeadQueueHideSlot } from './lead-queue.hideSlot';
import { searchParamsWithLeadTablePlace } from './lead-queue.filters';

/** The route's wiring (routes/lead-queue.tsx), with one more URL write of the route beside it. */
function Queue() {
  const [searchParams, setSearchParams] = useSearchParams();
  const columnState = useLeadQueueColumnState(searchParams);
  const columnWrites = useLeadQueueColumnWrites(searchParams, setSearchParams);
  return (
    <>
      <LeadQueueHideSlot view={columnState.view} hidden={columnState.hidden} onPick={columnWrites.pickHidden} />
      <button type="button" data-testid="view-sales-ops" onClick={() => columnWrites.setView('sales-ops')}>Sales ops</button>
      <button
        type="button"
        data-testid="sort-by-score"
        onClick={() => setSearchParams(searchParamsWithLeadTablePlace(searchParams, { sort: { key: 'score', dir: 'asc' }, row: null }))}
      >
        Sort
      </button>
      <output data-testid="committed">{searchParams.toString()}</output>
    </>
  );
}

const ROUTES = (
  <Routes>
    <Route path="/lead-queue" element={<Queue />} />
  </Routes>
);

describe('Lead Queue column writes', () => {
  let container: HTMLDivElement;
  let root: Root;

  // The HIDE control is its own chunk (lead-queue.hideSlot.tsx). Transformed
  // here, so the slot's own import() of it settles within a few turns.
  beforeAll(async () => {
    await import('../components/mortgage/LeadTableHideControl');
  });

  beforeEach(() => {
    prefs.current = { defaultView: null, recent: [] };
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
  });

  const flush = () => act(async () => {
    await new Promise((resolve) => window.setTimeout(resolve, 0));
  });
  const committed = () => container.querySelector('[data-testid="committed"]')?.textContent;
  const pill = () => container.querySelector<HTMLButtonElement>('button[aria-label^="HIDE:"]');
  const option = (label: string): HTMLElement => {
    const found = [...container.querySelectorAll<HTMLElement>('[role="listbox"][aria-label="HIDE"] [role="option"]')]
      .find((item) => item.textContent === label);
    if (!found) throw new Error(`no HIDE option "${label}"`);
    return found;
  };
  const click = (element: Element) => element.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  const testId = (id: string): HTMLElement => container.querySelector<HTMLElement>(`[data-testid="${id}"]`) as HTMLElement;
  /** An inert placeholder holds the control's place until its chunk lands. */
  async function hideControlLoaded() {
    for (let attempt = 0; attempt < 100 && pill() === null; attempt += 1) await flush();
    expect(pill(), 'the HIDE control loaded').not.toBeNull();
  }

  /** Mounts the queue under the app's data-router shape (appRouter.tsx) with the HIDE menu open. */
  async function mountWithMenuOpen(url: string) {
    const router = createMemoryRouter([{ path: '*', element: ROUTES }], { initialEntries: [url] });
    await act(async () => {
      root.render(<RouterProvider router={router} />);
    });
    await hideControlLoaded();
    await act(async () => {
      click(pill()!);
    });
    return router;
  }

  it('keeps both columns of two HIDE picks made before the first one commits', async () => {
    const router = await mountWithMenuOpen('/lead-queue?state=TX');
    let gap = '';
    await act(async () => {
      click(option('Segments'));
      // The router has the first pick; React has not committed it.
      gap = `${router.state.location.search} | ${committed()}`;
      click(option('Rate Δ (bps)'));
    });
    expect(gap).toBe('?state=TX&hide=segments | state=TX');
    expect(router.state.location.search).toBe('?state=TX&hide=segments%2Crate');
    expect(committed()).toBe('state=TX&hide=segments%2Crate');
    expect(pill()?.getAttribute('aria-label')).toBe('HIDE: 2 columns');
    router.dispose();
  });

  it('applies an un-hide and a hide made in one gap to the live set', async () => {
    const router = await mountWithMenuOpen('/lead-queue?hide=segments');
    await act(async () => {
      click(option('Rate Δ (bps)'));
      click(option('Segments'));
    });
    expect(router.state.location.search).toBe('?hide=rate');
    expect(pill()?.getAttribute('aria-label')).toBe('HIDE: Rate Δ (bps)');
    router.dispose();
  });

  it('"No columns" clears the live set, and a pick after it starts from nothing', async () => {
    const router = await mountWithMenuOpen('/lead-queue?hide=rate');
    await act(async () => {
      click(option('Equity'));
      click(option('No columns'));
    });
    expect(router.state.location.search).toBe('');
    await act(async () => {
      click(option('Equity'));
      click(option('No columns'));
      click(option('Segments'));
    });
    expect(router.state.location.search).toBe('?hide=segments');
    router.dispose();
  });

  it('a HIDE pick keeps another URL write of the route that is still committing', async () => {
    const router = await mountWithMenuOpen('/lead-queue?state=TX');
    await act(async () => {
      click(testId('sort-by-score'));
      click(option('Segments'));
    });
    expect(router.state.location.search).toBe('?state=TX&sort=score&dir=asc&hide=segments');
    router.dispose();
  });

  it('the VIEW preset keeps a hide that is still committing', async () => {
    const router = await mountWithMenuOpen('/lead-queue?state=TX');
    await act(async () => {
      click(option('Segments'));
      click(option('Status'));
      click(testId('view-sales-ops'));
    });
    // Status is not a Sales ops column, so only Segments stays hidden there.
    expect(router.state.location.search).toBe('?state=TX&hide=segments&view=sales-ops');
    expect(pill()?.getAttribute('aria-label')).toBe('HIDE: Segments');
    router.dispose();
  });

  it("starts from the actor's default saved view, then from the URL the first pick wrote", async () => {
    prefs.current = {
      defaultView: { id: 'v1', name: 'Ops desk', params: 'state=TX&view=sales-ops&hide=relationship,last-touch' },
      recent: [],
    };
    const router = await mountWithMenuOpen('/lead-queue?state=IL');
    expect(pill()?.getAttribute('aria-label')).toBe('HIDE: 2 columns');
    await act(async () => {
      click(option('Relationship'));
      click(option('Segments'));
    });
    // The default's filter (state=TX) is never written; its columns are, once.
    expect(router.state.location.search).toBe('?state=IL&view=sales-ops&hide=last-touch%2Csegments');
    await act(async () => {
      click(option('No columns'));
    });
    expect(router.state.location.search).toBe('?state=IL&view=sales-ops');
    router.dispose();
  });

  it('writes from the committed params where there is no data router', async () => {
    await act(async () => {
      root.render(<MemoryRouter initialEntries={['/lead-queue?state=TX&hide=rate']}>{ROUTES}</MemoryRouter>);
    });
    await hideControlLoaded();
    await act(async () => {
      click(pill()!);
    });
    await act(async () => {
      click(option('Segments'));
    });
    expect(committed()).toBe('state=TX&hide=segments%2Crate');
    await act(async () => {
      click(testId('view-sales-ops'));
    });
    expect(committed()).toBe('state=TX&hide=segments%2Crate&view=sales-ops');
  });
});

describe('hiddenAfterPick', () => {
  it('toggles the picked token and clears on the "No columns" row', () => {
    expect(hiddenAfterPick(new Set(['segments'] as const), 'rate')).toEqual(['segments', 'rate']);
    expect(hiddenAfterPick(new Set(['segments', 'rate'] as const), 'segments')).toEqual(['rate']);
    expect(hiddenAfterPick([], 'status')).toEqual(['status']);
    expect(hiddenAfterPick(new Set(['segments', 'rate'] as const), null)).toEqual([]);
  });
});
