/**
 * @vitest-environment happy-dom
 *
 * Borrower 360's failure states (audit 2026-09-21 states-04 slice 2 and
 * states-03 part a), rendered through the real route: under a banner that
 * already names the outage the dossier waits calmly; any other failure speaks
 * the shared vocabulary and never the transport message; a 404 is unchanged.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, useEffect } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, Route, Routes, useNavigate } from 'react-router';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '../lib/apiTransport';

const state = vi.hoisted(() => ({
  borrower: { data: null as unknown, warmingUp: null as unknown, error: null as unknown, manualRetry: vi.fn() },
  health: null as null | { health: { dependencies: Record<string, string> }; connection: string },
}));

const apiMocks = vi.hoisted(() => ({
  borrowerLifecycle: vi.fn(() => new Promise(() => undefined)),
}));

vi.mock('../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/api')>()),
  api: apiMocks,
}));
vi.mock('../lib/useWarmingUpRetry', () => ({
  useWarmingUpRetry: () => state.borrower,
}));
vi.mock('../components/HealthProvider', () => ({
  useOptionalHealth: () => state.health,
}));
vi.mock('../components/AppContext', () => ({
  useApp: () => ({ lastBorrowerId: null, setLastBorrowerId: vi.fn(), saveLead: vi.fn(), isLeadSaved: () => false }),
}));

const preload = vi.hoisted(() => ({ route: vi.fn(), saveData: false }));
vi.mock('../lib/routePreloaders', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/routePreloaders')>()),
  preloadRouteForPath: preload.route,
}));
vi.mock('../lib/prefetch', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/prefetch')>()),
  saveDataRequested: () => preload.saveData,
}));

const printMocks = vi.hoisted(() => ({ createPrintReceipt: vi.fn() }));
vi.mock('../lib/apiClients/printReceipt', () => ({ createPrintReceipt: printMocks.createPrintReceipt }));

import Borrower360 from './borrower-360';
import { UNAUDITED_PRINT } from '../components/print/PrintProvenance';
import type { Borrower360 as Borrower360Type } from '../types';

const ID = 'B-0123456789ABC';
const SENTINEL = 'SENTINEL SQL warehouse unavailable 500 Internal Server Error';
const WAREHOUSE_DOWN = { health: { dependencies: { warehouse: 'down', lakebase: 'up', genie: 'up' } }, connection: 'online' };

function outage(): ApiError {
  return new ApiError(SENTINEL, { path: `/api/v1/borrowers/${ID}`, status: 503, retryable: true, dependency: 'warehouse', reason: 'retries_exhausted' });
}

let root: Root;
let queryClient: QueryClient;

// The route's lazy pieces (the failure page, the decision history) cost a
// cold transform the first time a case mounts them: about 0.7 s here, more
// than 5 s on a loaded runner. Import them once, under the hook's own
// timeout, so each case's budget covers only its own work.
beforeAll(async () => {
  await Promise.all([import('../components/mortgage/DossierFailure'), import('../components/mortgage/BorrowerDecisionHistory')]);
}, 60_000);

beforeEach(() => {
  document.body.innerHTML = '<div id="root"></div>';
  root = createRoot(document.getElementById('root') as HTMLElement);
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  state.health = null;
  state.borrower = { data: null, warmingUp: null, error: null, manualRetry: vi.fn() };
});

afterEach(() => {
  act(() => root.unmount());
  queryClient.clear();
  document.body.innerHTML = '';
});

async function mount() {
  await act(async () => {
    root.render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={[`/borrower-360/${ID}`]}>
          <Routes>
            <Route path="/borrower-360/:id" element={<Borrower360 />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
  });
  // A failed read's page is its own chunk, loaded on the failure.
  await act(async () => {
    await vi.dynamicImportSettled();
  });
}

const text = () => document.body.textContent ?? '';
const retry = () => document.querySelector<HTMLButtonElement>(`button[aria-label="Retry loading borrower ${ID}"]`);

describe('Borrower 360 failure states', () => {
  it('waits calmly under a banner that names the outage, with a secondary Retry', async () => {
    state.health = WAREHOUSE_DOWN;
    state.borrower.error = outage();
    await mount();

    // The title names the borrower (the morph target, BorrowerTitle); the
    // loading words live in the lede only.
    expect(document.querySelector('h1')?.textContent).toBe(`Borrower ${ID}`);
    expect(document.querySelector('h1 .page-title__id')?.textContent).toBe(ID);
    expect(text()).toContain('Loading this dossier. It reloads when the analytics warehouse reconnects.');
    expect(text()).toContain('Reconnecting');
    expect(text()).not.toContain('Backend unavailable');
    expect(text()).not.toContain('SENTINEL');
    expect(document.querySelector('[role="alert"]')).toBeNull();
    act(() => retry()?.click());
    expect(state.borrower.manualRetry).toHaveBeenCalledTimes(1);
  });

  it('titles the warming-up and skeleton states "Borrower B-…", with the loading words in the lede only', async () => {
    state.borrower.warmingUp = {
      dependency: 'warehouse', label: 'Warehouse warming up', attempt: 2, maxAttempts: 6, correlationId: null,
    };
    await mount();
    expect(document.querySelector('h1')?.textContent).toBe(`Borrower ${ID}`);
    expect(document.querySelector('h1 .page-title__id')?.textContent).toBe(ID);
    expect(document.querySelector('h1')?.textContent).not.toMatch(/Loading/);

    act(() => root.unmount());
    root = createRoot(document.getElementById('root') as HTMLElement);
    state.borrower = { data: null, warmingUp: null, error: null, manualRetry: vi.fn() };
    await mount();
    expect(document.querySelector('h1')?.textContent).toBe(`Borrower ${ID}`);
    expect(document.querySelector('.lede')?.textContent).toBe(`Loading borrower ${ID}…`);
  });

  it('names the same outage in the shared vocabulary when no banner does', async () => {
    state.borrower.error = outage();
    await mount();

    expect(document.querySelector('h1')?.textContent).toBe('The analytics warehouse is unavailable');
    expect(text()).toContain('The app already retried; try again shortly.');
    expect(text()).toContain('Unavailable');
    expect(text()).not.toContain('SENTINEL');
    expect(retry()).not.toBeNull();
  });

  it('never prints a 500\'s transport message', async () => {
    state.health = WAREHOUSE_DOWN;
    state.borrower.error = new ApiError(SENTINEL, { path: `/api/v1/borrowers/${ID}`, status: 500 });
    await mount();

    expect(document.querySelector('h1')?.textContent).toBe(`Couldn't load borrower ${ID}`);
    expect(text()).toContain('The server hit an unexpected error.');
    expect(text()).not.toContain('SENTINEL');
    expect(text()).not.toContain('reloads when');
  });

  it('offers no Retry for a permission failure', async () => {
    state.borrower.error = new ApiError(SENTINEL, { path: `/api/v1/borrowers/${ID}`, status: 403 });
    await mount();

    expect(document.querySelector('h1')?.textContent).toBe(`Your role can't open borrower ${ID}`);
    expect(text()).toContain('Access required');
    expect(retry()).toBeNull();
  });

  it('keeps the 404 copy', async () => {
    state.borrower.error = new ApiError('not found', { path: `/api/v1/borrowers/${ID}`, status: 404 });
    await mount();

    expect(document.querySelector('h1')?.textContent).toBe(`Borrower ${ID} not found`);
    expect(text()).toContain('Check the ID, use search, or return to the lead queue.');
    expect(retry()).toBeNull();
  });
});

/**
 * The loaded dossier (W5d). D-platform-process-g step 3: the panel speaks the
 * prototype's vocabulary, 'In the Money', never the scrubbed 'refi screen' /
 * 'refi economics' variants. bundle-09 item 1: intent on 'Build outreach
 * draft' warms the Offer route's chunk only (no read: the Offer read writes
 * VIEW_BORROWER and RECOMMEND_OFFER rows). responsive-08: Export PDF appears
 * only once the dossier has loaded.
 *
 * Mutation check (lane report): restoring 'Refi economics check' fails
 * "names the panel and its chip in the prototype vocabulary".
 */
function dossier(inTheMoney: boolean): Borrower360Type {
  return {
    borrower_id: ID,
    city: 'Chicago', state: 'IL', zip: '60611',
    segment_codes: ['itm'],
    equity_estimate: 285_000,
    rate_spread_bps: 88,
    opportunity_score: 82,
    confidence: 80,
    recommended_offer: 'Refinance',
    recommended_offer_code: 'refi',
    why_now: 'Rate spread clears the bar.',
    is_investor: false,
    related_property_count: 1,
    clip_id: 'clip_demo_48291',
    owner_link_id: 'ol_demo_48291',
    subject_property: 'Chicago, IL 60611',
    avm_value: 625_000,
    current_lien_balance: 340_000,
    current_rate: 5.75,
    ltv: 54,
    ltv_basis_is_unreliable: false,
    approval_status: 'pending',
    outreach_status: 'none',
    trigger_timeline: [],
    evidence_events: [],
    why_panel: {
      in_the_money: inTheMoney,
      in_the_money_reason: 'Spread and equity both clear the screen.',
      rate_spread_bps: 88,
      market_rate: 0.0487,
      sources: [],
    },
  } as unknown as Borrower360Type;
}

const BANNED_VARIANTS = /\bprime[- ]refi\w*|\brefi(?:nance)?[- ]economics\b|\brefi screen\b/i;

describe('Borrower 360 loaded dossier (W5d)', () => {
  beforeEach(() => {
    preload.route.mockReset();
    preload.saveData = false;
    // The dossier's sibling cards read through the transport; they are not
    // under test here and must never reach a network.
    vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>(() => undefined)));
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('names the panel and its chip in the prototype vocabulary', async () => {
    state.borrower.data = dossier(true);
    await mount();

    expect(text()).toContain('In the Money check');
    expect(text()).toContain('In the Money rule.');
    const chip = [...document.querySelectorAll('.chip')].find((node) => node.textContent?.startsWith('In the Money'));
    expect(chip?.querySelector('a.glossary-term')?.getAttribute('href')).toBe('/glossary#in-the-money');
    expect(text()).not.toMatch(BANNED_VARIANTS);
  });

  it('says "Not in the money" below the rule, with the same glossary link', async () => {
    state.borrower.data = dossier(false);
    await mount();

    const chip = [...document.querySelectorAll('.chip')].find((node) => node.textContent?.startsWith('Not in the money'));
    expect(chip?.querySelector('a.glossary-term')?.getAttribute('href')).toBe('/glossary#in-the-money');
    expect(text()).not.toMatch(BANNED_VARIANTS);
  });

  it('links the Approval status label to the human-approval definition', async () => {
    state.borrower.data = dossier(true);
    await mount();
    const term = [...document.querySelectorAll('a.glossary-term')].find((node) => node.textContent?.startsWith('Approval status'));
    expect(term?.getAttribute('href')).toBe('/glossary#human-approval');
  });

  it('warms only the Offer route chunk on intent, and nothing under Save-Data', async () => {
    state.borrower.data = dossier(true);
    await mount();
    const link = [...document.querySelectorAll<HTMLAnchorElement>('a.btn--primary')]
      .find((node) => node.textContent?.includes('Build outreach draft'));
    expect(link).toBeTruthy();
    const fetches = (globalThis.fetch as unknown as { mock: { calls: unknown[] } }).mock.calls.length;
    const lifecycleReads = apiMocks.borrowerLifecycle.mock.calls.length;

    act(() => {
      link!.dispatchEvent(new PointerEvent('pointerover', { bubbles: true }));
      link!.dispatchEvent(new PointerEvent('pointerenter', { bubbles: false }));
    });
    act(() => link!.focus());
    expect(preload.route).toHaveBeenCalledWith(`/offer-orchestrator/${ID}`);
    // The chunk only: no request of any kind leaves on intent.
    expect((globalThis.fetch as unknown as { mock: { calls: unknown[] } }).mock.calls.length).toBe(fetches);
    expect(apiMocks.borrowerLifecycle.mock.calls.length).toBe(lifecycleReads);

    preload.route.mockReset();
    preload.saveData = true;
    act(() => link!.blur());
    act(() => link!.focus());
    expect(preload.route).not.toHaveBeenCalled();
  });

  // Fix round 1 (responsive-08): the route element is keyed by the route, so
  // the pager swaps borrower A for B in place. A receipt that resolves after
  // that swap is A's; it must never open the print dialog on B's dossier.
  it('prints nothing when the receipt resolves after the dossier changed to another borrower', async () => {
    const OTHER_ID = 'B-0123456789ABD';
    let navigateTo: (path: string) => void = () => undefined;
    function NavigateProbe() {
      const navigate = useNavigate();
      useEffect(() => {
        navigateTo = (path) => void navigate(path);
      }, [navigate]);
      return null;
    }
    let resolveReceipt: (value: unknown) => void = () => undefined;
    printMocks.createPrintReceipt.mockReset();
    printMocks.createPrintReceipt.mockReturnValue(new Promise((resolve) => {
      resolveReceipt = resolve;
    }));
    const print = vi.fn();
    Object.defineProperty(window, 'print', { configurable: true, writable: true, value: print });
    state.borrower.data = dossier(true);
    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter initialEntries={[`/borrower-360/${ID}`]}>
            <NavigateProbe />
            <Routes>
              <Route path="/borrower-360/:id" element={<Borrower360 />} />
            </Routes>
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
    // Like mount(): the dossier's lazy pieces settle before the click, so
    // this case never depends on an earlier test having warmed the chunks.
    await act(async () => {
      await vi.dynamicImportSettled();
    });

    act(() => document.querySelector<HTMLButtonElement>('[data-testid="export-pdf"]')!.click());
    expect(printMocks.createPrintReceipt).toHaveBeenCalledWith({ surface: 'borrower_360', borrower_id: ID, row_count: 1 });

    state.borrower = { data: { ...dossier(true), borrower_id: OTHER_ID }, warmingUp: null, error: null, manualRetry: vi.fn() };
    await act(async () => navigateTo(`/borrower-360/${OTHER_ID}`));
    await act(async () => resolveReceipt({
      audit_event_id: 'receipt-for-a',
      event_type: 'PRINT_EXPORT',
      actor: 'ledger.reader@summit.example',
      surface: 'borrower_360',
      row_count: 1,
      borrower_id: ID,
      filter_fingerprint: null,
      recorded_at: '2026-10-07T15:00:04Z',
    }));

    expect(print).not.toHaveBeenCalled();
    const provenance = document.querySelector('[data-testid="print-provenance"]')?.textContent ?? '';
    expect(provenance).toContain(`Borrower 360 · ${OTHER_ID}`);
    expect(provenance).toContain(UNAUDITED_PRINT);
    expect(provenance).not.toContain('receipt-for-a');
  });

  it('offers Export PDF only once the dossier has loaded', async () => {
    state.borrower.warmingUp = { dependency: 'warehouse', label: 'Warehouse warming up', attempt: 1, maxAttempts: 6, correlationId: null };
    await mount();
    expect(document.querySelector('[data-testid="export-pdf"]')).toBeNull();

    act(() => root.unmount());
    root = createRoot(document.getElementById('root') as HTMLElement);
    state.borrower = { data: dossier(true), warmingUp: null, error: null, manualRetry: vi.fn() };
    await mount();
    expect(document.querySelector('[data-testid="export-pdf"]')?.textContent).toContain('Export PDF');
  });
});
