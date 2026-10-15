/**
 * @vitest-environment happy-dom
 *
 * The filter omnibox (wow-power-6, W5e item 3c), rendered with the real
 * grammar and the real count client over a stubbed fetch:
 *  - mounting reads nothing; F (or the pill) opens the popover, whose module
 *    loads on that first open, and focuses the combobox;
 *  - 'TX IL heloc aged>7 zyrplax' shows four chips with their source phrases
 *    and 'Not recognized: zyrplax'; ONE debounced GET /api/leads/count whose
 *    query is the allowlisted readings only, even from a URL that carries a
 *    borrower list and an approval-request scope; the status is aria-busy
 *    while it loads, then the number, or 'Count unavailable';
 *  - Enter replaces the filters and keeps the column state and sort; Escape
 *    closes; both return focus to the pill;
 *  - 'Ask Genie instead' only prefills the Genie composer (no /api/genie
 *    request), and the typed text is never stored or put in the URL;
 *  - F is not bound in the Triage deck, and is released on unmount.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, useEffect } from 'react';
import { MemoryRouter, useLocation } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { listKeyBindings } from '../lib/keymap';
import { consumeGeniePrefill, GENIE_OPEN_REQUEST_EVENT } from '../lib/genieOpen';
import { installLocalStorage } from '../test/installLocalStorage';
import { mount } from '../test/render';
import { LeadQueueOmnibox } from './lead-queue.omnibox';

let calls: string[] = [];
let respond: (path: string) => Response | Promise<Response> = () => json({ total_matching: 1234 });
const searches: string[] = [];

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

function LocationProbe() {
  const { search } = useLocation();
  useEffect(() => {
    searches.push(search);
  }, [search]);
  return null;
}

async function mountAt(query: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return mount(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[`/lead-queue${query}`]}>
        <LeadQueueOmnibox />
        <LocationProbe />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const byTestId = <T extends HTMLElement>(id: string) => document.querySelector<T>(`[data-testid="${id}"]`);
const pill = () => byTestId<HTMLButtonElement>('lead-queue-omnibox')!;
const input = () => byTestId<HTMLInputElement>('lead-queue-omnibox-input');
const countCalls = () => calls.filter((path) => path.includes('/leads/count'));

async function wait(ms: number) {
  await act(async () => {
    await new Promise((resolve) => window.setTimeout(resolve, ms));
  });
}

async function openWithF() {
  await act(async () => {
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'f', bubbles: true, cancelable: true }));
  });
  await vi.waitFor(async () => {
    await wait(0);
    expect(input()).not.toBeNull();
  }, { timeout: 20_000 });
}

async function type(text: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!;
  await act(async () => {
    setter.call(input()!, text);
    input()!.dispatchEvent(new Event('input', { bubbles: true }));
  });
}

async function press(key: string) {
  await act(async () => {
    input()!.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }));
  });
}

beforeEach(() => {
  installLocalStorage();
  calls = [];
  searches.length = 0;
  respond = () => json({ total_matching: 1234 });
  vi.stubGlobal('fetch', async (path: string) => {
    calls.push(path);
    return respond(path);
  });
  consumeGeniePrefill();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('the filter omnibox', () => {
  it('reads nothing on mount, opens on F and focuses the combobox', async () => {
    await mountAt('?state=IL');
    expect(calls).toEqual([]);
    expect(pill().getAttribute('aria-keyshortcuts')).toBe('F');
    expect(pill().getAttribute('aria-expanded')).toBe('false');
    expect(listKeyBindings().some((binding) => binding.id === 'lead-queue-omnibox')).toBe(true);
    await openWithF();
    expect(pill().getAttribute('aria-expanded')).toBe('true');
    expect(document.activeElement).toBe(input());
    expect(input()!.getAttribute('role')).toBe('combobox');
    expect(calls).toEqual([]);
  }, 30_000);

  it('shows the readings and one debounced, allowlisted count; the status is busy then the number', async () => {
    let release: () => void = () => undefined;
    respond = () => new Promise<Response>((resolve) => {
      release = () => resolve(json({ total_matching: 1234 }));
    });
    await mountAt('?borrower_ids=B-0123456789ABC&approval_request_batch=0f8fad5b-d9cb-469f-a165-70867728950e&view=sales-ops');
    await openWithF();
    const status = document.querySelector<HTMLElement>('.lead-queue-omnibox__count')!;
    expect(status.getAttribute('role')).toBe('status');
    expect(status.textContent).toBe('');
    await type('TX IL heloc aged>7 zyrplax');
    const chips = [...document.querySelectorAll('.lead-queue-omnibox__chip')].map((chip) => chip.textContent);
    expect(chips).toEqual([
      'STATE · TX (from "TX")', 'STATE · IL (from "IL")', 'PRODUCT · HELOC (from "heloc")', 'AGING · Aged >7d (from "aged>7")',
    ]);
    expect(byTestId('lead-queue-omnibox-unrecognized')?.textContent).toBe('Not recognized: zyrplax');
    expect(input()!.getAttribute('aria-expanded')).toBe('true');
    expect(countCalls()).toEqual([]);
    await wait(260);
    expect(countCalls()).toHaveLength(1);
    const query = new URL(countCalls()[0], 'http://queue.test').searchParams;
    expect([...query]).toEqual([['states', 'IL,TX'], ['product', 'HELOC'], ['aged_days', '7']]);
    expect(status.getAttribute('aria-busy')).toBe('true');
    await act(async () => release());
    await wait(0);
    expect(status.textContent).toBe('1,234 borrowers match');
    expect(status.hasAttribute('aria-busy')).toBe(false);
    expect(calls.filter((path) => !path.includes('/leads/count'))).toEqual([]);
  }, 30_000);

  it('says Count unavailable when the count read fails', async () => {
    respond = () => json({ detail: 'down' }, 503);
    await mountAt('');
    await openWithF();
    await type('CA refi');
    await vi.waitFor(async () => {
      await wait(50);
      expect(document.querySelector('.lead-queue-omnibox__count')?.textContent).toBe('Count unavailable');
    }, { timeout: 5_000 });
  }, 30_000);

  it('applies on Enter: filters replaced, the column state and sort kept, focus back on the pill', async () => {
    await mountAt('?state=WA&segment=itm&view=sales-ops&hide=rate&sort=score&dir=asc&row=B-0123456789ABC');
    await openWithF();
    await type('TX IL heloc');
    await press('Enter');
    await wait(0);
    expect(new URLSearchParams(searches[searches.length - 1]).toString()).toBe(
      'view=sales-ops&hide=rate&sort=score&dir=asc&states=IL%2CTX&product=HELOC',
    );
    expect(input()).toBeNull();
    expect(document.activeElement).toBe(pill());
  }, 30_000);

  it('closes on Escape and returns focus to the pill', async () => {
    await mountAt('');
    await openWithF();
    await act(async () => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    });
    expect(input()).toBeNull();
    expect(document.activeElement).toBe(pill());
  }, 30_000);

  it('offers Ask Genie instead only for unrecognized text, and only prefills the composer', async () => {
    const opened = vi.fn();
    window.addEventListener(GENIE_OPEN_REQUEST_EVENT, opened);
    await mountAt('');
    await openWithF();
    await type('TX heloc');
    expect(byTestId('lead-queue-omnibox-ask-genie')).toBeNull();
    await type('TX heloc borrowers near the lake');
    await act(async () => byTestId<HTMLButtonElement>('lead-queue-omnibox-ask-genie')!.click());
    expect(opened).toHaveBeenCalledTimes(1);
    expect(consumeGeniePrefill()).toBe('TX heloc borrowers near the lake');
    expect(calls.some((path) => path.includes('/genie'))).toBe(false);
    // The typed text is never persisted or put in the URL (C13).
    const stored = JSON.stringify({ ...window.localStorage }) + JSON.stringify({ ...window.sessionStorage });
    expect(stored).not.toContain('lake');
    expect(searches.join('|')).not.toContain('lake');
    window.removeEventListener(GENIE_OPEN_REQUEST_EVENT, opened);
  }, 30_000);

  it('binds no F in the Triage deck, and releases F on unmount', async () => {
    const triage = await mountAt('?mode=triage');
    expect(listKeyBindings().some((binding) => binding.id === 'lead-queue-omnibox')).toBe(false);
    await act(async () => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'f', bubbles: true, cancelable: true }));
    });
    expect(pill().getAttribute('aria-expanded')).toBe('false');
    triage.unmount();
    const queue = await mountAt('');
    expect(listKeyBindings().some((binding) => binding.id === 'lead-queue-omnibox')).toBe(true);
    queue.unmount();
    expect(listKeyBindings().some((binding) => binding.id === 'lead-queue-omnibox')).toBe(false);
  });
});
