/**
 * @vitest-environment happy-dom
 *
 * The topbar borrower search is an APG editable combobox with a list popup
 * (2026-09-21 audit shell-07 / a11y-02). The defect lived here: the results
 * were `button role=option` rows no key could reach (the input had no key
 * handler or combobox ARIA, and it closed the results 120 ms after blur, so
 * Tab never landed on one). Now ArrowDown walks the results while focus stays
 * in the input, Enter opens the highlighted borrower, and Escape closes, then
 * clears.
 *
 * W5d (runtime-06): the search is the shared hooks/useBorrowerSearch, the
 * same cache the ⌘K palette reads, on ONE 160 ms debounce (the topbar's own
 * was 180 ms; the fake-timer steps below changed on purpose). The options are
 * buttons out of the Tab order (a11y-05).
 */

import { act } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, useLocation } from 'react-router';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { BORROWER_SEARCH_DEBOUNCE_MS } from '../../hooks/useBorrowerSearch';
import { escapeLayerCount } from '../../lib/escapeStack';
import { mount } from '../../test/render';
import type { LeadSummary } from '../../types';

const apiMocks = vi.hoisted(() => ({ borrowerSearch: vi.fn() }));

vi.mock('../../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../lib/api')>()),
  api: { borrowerSearch: apiMocks.borrowerSearch },
}));
vi.mock('../AppContext', () => ({
  useApp: () => ({
    lender: 'Summit Mortgage',
    theme: 'dark',
    setTheme: vi.fn(),
    genieOpen: false,
    setGenieOpen: vi.fn(),
    consoleOpen: false,
    setConsoleOpen: vi.fn(),
  }),
}));
vi.mock('../HealthProvider', () => ({ useHealth: () => ({ health: null }) }));
vi.mock('../FootprintProvider', () => ({ useFootprint: () => ({ usingFallback: false }) }));
// The identity menu reads /api/session through TanStack Query; it has its own test.
vi.mock('./IdentityMenu', () => ({ IdentityMenu: () => null }));

import { Topbar } from './Topbar';

const ROWS = [
  { borrower_id: 'B-0000000000001', city: 'Chicago', state: 'IL', zip: '60611' },
  { borrower_id: 'B-0000000000002', city: 'Chicago', state: 'IL', zip: '60614' },
] as unknown as LeadSummary[];

function LocationProbe() {
  return <output id="location-probe">{useLocation().pathname}</output>;
}
const pathname = (): string => document.getElementById('location-probe')?.textContent ?? '';

let container: HTMLElement;

const input = (): HTMLInputElement =>
  container.querySelector<HTMLInputElement>('input[aria-label="Search borrowers"]') as HTMLInputElement;
const listbox = (): HTMLElement | null => container.querySelector<HTMLElement>('[role="listbox"]');
const options = (): HTMLElement[] => [...container.querySelectorAll<HTMLElement>('[role="option"]')];
const activeOption = (): HTMLElement | null => {
  const id = input().getAttribute('aria-activedescendant');
  return id ? document.getElementById(id) : null;
};

function press(key: string): KeyboardEvent {
  const event = new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true });
  act(() => {
    (document.activeElement ?? document.body).dispatchEvent(event);
  });
  return event;
}

/** Let a settled search reach the screen (TanStack notifies on a macrotask). */
async function settle(): Promise<void> {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 20));
  });
}

async function typeQuery(text: string): Promise<void> {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
  await act(async () => {
    setter?.call(input(), text);
    input().dispatchEvent(new Event('input', { bubbles: true }));
  });
  // The search is debounced (BORROWER_SEARCH_DEBOUNCE_MS, 160 ms); let it
  // fire, then let the query answer and render.
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, BORROWER_SEARCH_DEBOUNCE_MS + 90));
  });
  await settle();
  await settle();
}


// mount() unmounts the Topbar and removes its container after each test.
beforeEach(async () => {
  apiMocks.borrowerSearch.mockReset();
  apiMocks.borrowerSearch.mockResolvedValue(ROWS);
  // A fresh cache per test: a term another test searched never answers here.
  const queryClient = new QueryClient();
  ({ container } = await mount(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <Topbar />
        <button type="button" id="elsewhere">Elsewhere</button>
        <LocationProbe />
      </MemoryRouter>
    </QueryClientProvider>,
  ));
  act(() => input().focus());
});

describe('Topbar borrower search — keyboard', () => {
  it('is a combobox whose results listbox appears without stealing focus', async () => {
    expect(input().getAttribute('role')).toBe('combobox');
    expect(input().getAttribute('aria-expanded')).toBe('false');

    await typeQuery('Chic');

    expect(options()).toHaveLength(2);
    expect(input().getAttribute('aria-expanded')).toBe('true');
    expect(input().getAttribute('aria-controls')).toBe(listbox()?.id);
    expect(input().hasAttribute('aria-activedescendant')).toBe(false);
    expect(document.activeElement).toBe(input());
  });

  it('ArrowDown reaches the results and Enter opens the highlighted borrower', async () => {
    await typeQuery('Chic');
    press('ArrowDown');
    expect(activeOption()).toBe(options()[0]);
    expect(options()[0].getAttribute('aria-selected')).toBe('true');
    press('ArrowDown');
    expect(activeOption()).toBe(options()[1]);
    press('ArrowUp');
    expect(activeOption()).toBe(options()[0]);
    press('ArrowUp');
    expect(activeOption()).toBe(options()[1]);
    expect(document.activeElement).toBe(input());

    const enter = press('Enter');
    expect(enter.defaultPrevented).toBe(true);
    expect(pathname()).toBe('/borrower-360/B-0000000000002');
    expect(listbox()).toBeNull();
  });

  it('Escape closes the results and keeps focus; a second Escape clears the query', async () => {
    await typeQuery('Chic');
    press('ArrowDown');
    expect(listbox()).not.toBeNull();

    act(() => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
    });
    expect(listbox()).toBeNull();
    expect(input().getAttribute('aria-expanded')).toBe('false');
    expect(document.activeElement).toBe(input());
    expect(input().value).toBe('Chic');

    press('Escape');
    expect(input().value).toBe('');
  });

  it('results that land after focus left stay closed and off the Escape stack', async () => {
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
    await act(async () => {
      setter?.call(input(), 'Chic');
      input().dispatchEvent(new Event('input', { bubbles: true }));
    });
    // Leave before the debounced search answers.
    act(() => document.getElementById('elsewhere')?.focus());
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, BORROWER_SEARCH_DEBOUNCE_MS + 90));
    });
    expect(apiMocks.borrowerSearch).toHaveBeenCalledTimes(1);
    expect(listbox()).toBeNull();
    expect(input().getAttribute('aria-expanded')).toBe('false');
    expect(escapeLayerCount()).toBe(0);
  });

  // runtime-03 pins: the submit path (Enter with nothing highlighted, or the
  // form's own submit) searches at once through the shared hook's searchNow
  // (the same key and function). A rejected search reads as the error status
  // and never navigates; a found exact id wins over the first row.
  async function submitQuery(text: string): Promise<void> {
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
    await act(async () => {
      setter?.call(input(), text);
      input().dispatchEvent(new Event('input', { bubbles: true }));
    });
    // Submit before the 160 ms debounce, so only the submit path searches.
    await act(async () => {
      container.querySelector('form')?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
      await Promise.resolve();
    });
    await settle();
  }

  /** The search calls' terms; every call consumes an abort signal. */
  const searchedTerms = (): string[] => apiMocks.borrowerSearch.mock.calls.map((call: unknown[]) => {
    expect(call[1]).toBeInstanceOf(AbortSignal);
    return String(call[0]);
  });

  it('a submit whose search rejects shows the error status and does not navigate', async () => {
    apiMocks.borrowerSearch.mockReset();
    // A failure arrives over the network, after the observer joined the fetch.
    apiMocks.borrowerSearch.mockImplementation(() => new Promise((_resolve, reject) => {
      setTimeout(() => reject(new Error('warehouse down')), 5);
    }));
    await submitQuery('Chic');
    await settle();

    // One request: the submit's. The debounced term joins it, never a second.
    expect(searchedTerms()).toEqual(['Chic']);
    const status = container.querySelector('.topbar__search-status--error');
    expect(status?.textContent).toBe('Search is temporarily unavailable.');
    expect(pathname()).toBe('/');
    expect(input().value).toBe('Chic');
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, BORROWER_SEARCH_DEBOUNCE_MS + 90));
    });
    expect(searchedTerms()).toEqual(['Chic']);
    expect(container.querySelector('.topbar__search-status--error')?.textContent).toBe('Search is temporarily unavailable.');
  });

  it('a submit that finds the typed id opens that borrower, not the first row', async () => {
    await submitQuery('b-0000000000002');

    expect(searchedTerms()).toEqual(['b-0000000000002']);
    expect(pathname()).toBe('/borrower-360/B-0000000000002');
    expect(input().value).toBe('');
  });

  it('closes when focus leaves the search', async () => {
    await typeQuery('Chic');
    expect(listbox()).not.toBeNull();
    act(() => document.getElementById('elsewhere')?.focus());
    expect(listbox()).toBeNull();
  });

  it('a submit of a term the debounce already answered opens it from the cache', async () => {
    await typeQuery('Chic');
    expect(searchedTerms()).toEqual(['Chic']);
    await act(async () => {
      container.querySelector('form')?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
      await Promise.resolve();
    });
    await settle();
    expect(searchedTerms()).toEqual(['Chic']);
    expect(pathname()).toBe('/borrower-360/B-0000000000001');
  });

  it('debounces: one request for a burst of keystrokes, none under two characters', async () => {
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
    for (const text of ['C', 'Ch', 'Chi', 'Chic']) {
      await act(async () => {
        setter?.call(input(), text);
        input().dispatchEvent(new Event('input', { bubbles: true }));
      });
    }
    expect(apiMocks.borrowerSearch).not.toHaveBeenCalled();
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, BORROWER_SEARCH_DEBOUNCE_MS + 90));
    });
    expect(searchedTerms()).toEqual(['Chic']);
    await typeQuery('C');
    expect(searchedTerms()).toEqual(['Chic']);
    expect(listbox()).toBeNull();
  });

  it('a cleared search never flashes the rows of the search before it', async () => {
    await typeQuery('Chic');
    expect(options()).toHaveLength(2);
    await typeQuery('C');
    let release: (rows: LeadSummary[]) => void = () => undefined;
    apiMocks.borrowerSearch.mockReturnValueOnce(new Promise<LeadSummary[]>((resolve) => { release = resolve; }));
    await typeQuery('Cl');
    expect(options()).toHaveLength(0);
    expect(container.querySelector('.topbar__search-status')?.textContent).toContain('Searching');
    await act(async () => {
      release([]);
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
    expect(container.querySelector('.topbar__search-status')?.textContent).toBe(
      'No borrower, ZIP, city, county, or state matches "Cl".',
    );
  });

  it('options are buttons out of the Tab order: Tab leaves the search', async () => {
    await typeQuery('Chic');
    for (const option of options()) {
      expect(option.tagName).toBe('BUTTON');
      expect(option.getAttribute('type')).toBe('button');
      expect(option.tabIndex).toBe(-1);
    }
    // The listbox carries no handler of its own; each option keeps focus in the input.
    const press = new MouseEvent('mousedown', { bubbles: true, cancelable: true });
    act(() => {
      options()[0].dispatchEvent(press);
    });
    expect(press.defaultPrevented).toBe(true);
    const focusable = [...container.querySelectorAll<HTMLElement>('button, input, a[href], [tabindex]')]
      .filter((el) => el.tabIndex >= 0);
    expect(focusable.some((el) => el.getAttribute('role') === 'option')).toBe(false);
  });
});
