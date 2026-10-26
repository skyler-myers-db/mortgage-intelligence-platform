// @vitest-environment happy-dom

/**
 * The borrower-id morph's pathname-commit clear (W5c dossier-decisions
 * review2 NB[1]; deviation:borrower-id-morph). A Lead Queue click names its
 * row id for the Borrower 360 title's View Transition; AppShell drops that
 * pending mark on every committed location change, so a stale name can never
 * reach a later transition. Proven on the REAL AppShell: a click marks the
 * id (control), then a committed navigation leaves borrowerMorphNameFor(id)
 * undefined while the one-second hold has not run out (the clock is fake).
 */
import { act, useEffect } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, useNavigate, type NavigateFunction } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { BORROWER_MORPH_NAME, borrowerMorphNameFor } from '../../lib/borrowerMorph';
import { installLocalStorage } from '../../test/installLocalStorage';
import { AppShell } from './AppShell';

// The dock's lazy resume import is out of this suite (see AppShell.actorBoundary.test.tsx).
vi.mock('./GenieDock', () => ({ GenieDock: () => null }));

const ID = 'B-0123456789ABC';
const RECT = { top: 0, left: 0, bottom: 600, right: 1200, width: 1200, height: 600, x: 0, y: 0 };

let navigate: NavigateFunction | null = null;
function NavigateProbe() {
  const go = useNavigate();
  useEffect(() => {
    navigate = go;
  }, [go]);
  return null;
}

function LeadRow() {
  return (
    <table>
      <tbody>
        <tr data-borrower-row={ID}>
          <td>
            <span className="lead-table__borrower">{ID}</span>
          </td>
          <td>
            <a id="open" href={`/borrower-360/${ID}`}>
              Open Borrower 360
            </a>
          </td>
        </tr>
      </tbody>
    </table>
  );
}

// Keeps happy-dom from following the plain anchor; the capture-phase morph
// listener has already run by then.
const stayOnPage = (event: Event) => event.preventDefault();

describe('AppShell clears a pending borrower-id morph on a committed location change', () => {
  let container: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;

  beforeEach(() => {
    vi.useFakeTimers();
    installLocalStorage();
    navigate = null;
    Object.defineProperty(document, 'startViewTransition', { configurable: true, value: vi.fn() });
    vi.spyOn(Element.prototype, 'getBoundingClientRect').mockReturnValue({ ...RECT, toJSON: () => RECT } as DOMRect);
    vi.stubGlobal('fetch', vi.fn(async () => new Response('{"detail":"not found"}', { status: 404 })));
    document.addEventListener('click', stayOnPage);
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    queryClient.clear();
    document.removeEventListener('click', stayOnPage);
    Reflect.deleteProperty(document, 'startViewTransition');
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  const clickOpen = () =>
    document.getElementById('open')?.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, button: 0 }));

  async function flush(): Promise<void> {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
  }

  it('a click names the row id; the next committed pathname drops it within the hold', async () => {
    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter initialEntries={['/lead-queue']}>
            <AppShell>
              <NavigateProbe />
              <LeadRow />
            </AppShell>
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
    // The morph listener installs from its own lazy chunk: click until it marks.
    for (let attempt = 0; attempt < 50 && borrowerMorphNameFor(ID) === undefined; attempt += 1) {
      await flush();
      clickOpen();
    }
    expect(borrowerMorphNameFor(ID), 'control: the click marked the row id').toBe(BORROWER_MORPH_NAME);

    await act(async () => {
      navigate?.(`/borrower-360/${ID}`);
    });
    for (let attempt = 0; attempt < 5; attempt += 1) await flush();

    expect(borrowerMorphNameFor(ID), 'the committed pathname cleared the pending mark').toBeUndefined();
  });
});
