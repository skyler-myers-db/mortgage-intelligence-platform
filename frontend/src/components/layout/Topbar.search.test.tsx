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
 */

import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, useLocation } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { escapeLayerCount } from '../../lib/escapeStack';
import type { LeadSummary } from '../../types';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const apiMocks = vi.hoisted(() => ({ borrowerSearch: vi.fn() }));

vi.mock('../../lib/api', () => ({ api: { borrowerSearch: apiMocks.borrowerSearch } }));
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

let container: HTMLDivElement;
let root: Root;

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

async function typeQuery(text: string): Promise<void> {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
  await act(async () => {
    setter?.call(input(), text);
    input().dispatchEvent(new Event('input', { bubbles: true }));
  });
  // The search is debounced (180 ms); let it fire and resolve.
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 250));
  });
}

beforeEach(() => {
  apiMocks.borrowerSearch.mockReset();
  apiMocks.borrowerSearch.mockResolvedValue(ROWS);
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  act(() =>
    root.render(
      <MemoryRouter>
        <Topbar />
        <button type="button" id="elsewhere">Elsewhere</button>
        <LocationProbe />
      </MemoryRouter>,
    ),
  );
  act(() => input().focus());
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
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
      await new Promise((resolve) => setTimeout(resolve, 250));
    });
    expect(apiMocks.borrowerSearch).toHaveBeenCalledTimes(1);
    expect(listbox()).toBeNull();
    expect(input().getAttribute('aria-expanded')).toBe('false');
    expect(escapeLayerCount()).toBe(0);
  });

  it('closes when focus leaves the search', async () => {
    await typeQuery('Chic');
    expect(listbox()).not.toBeNull();
    act(() => document.getElementById('elsewhere')?.focus());
    expect(listbox()).toBeNull();
  });
});
