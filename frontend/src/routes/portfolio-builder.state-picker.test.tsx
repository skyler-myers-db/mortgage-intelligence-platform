/**
 * @vitest-environment happy-dom
 *
 * 2026-09-21 audit a11y-v1 (WCAG 2.1.1 Level A). The Portfolio Builder GEO
 * picker was a private listbox whose options were `li role=option` with
 * onClick and nothing else: no tabIndex, no key handler, no Escape. A keyboard
 * user could open it and then do nothing, so step one of the product flow
 * (choose the states) was mouse-only.
 *
 * These tests drive the component the route renders (`StateMultiSelect`) with
 * the keyboard alone. No test here clicks.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, useEffect, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, useNavigate, type NavigateFunction } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
// @ts-expect-error Frontend app types intentionally exclude Node globals; this
// unit test reads the route source under Vitest only.
import { readFileSync } from 'node:fs';
// @ts-expect-error see node:fs note above.
import { join } from 'node:path';
import { StateMultiSelect } from './portfolio-builder.components';

declare const process: { cwd(): string };

// The route-level cases below (browser back/forward and a footprint change)
// render PortfolioBuilder itself; StateMultiSelect imports none of these.
const apiMocks = vi.hoisted(() => ({
  portfolioPreview: vi.fn(),
  campaigns: vi.fn(),
  salesCampaignPerformance: vi.fn(),
  campaignRecommendation: vi.fn(),
}));
const footprintMock = vi.hoisted(() => ({
  value: { ready: true, usingFallback: false, states: [] as Array<{ state_code: string; state_name: string }> },
}));

vi.mock('../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/api')>()),
  api: apiMocks,
}));
vi.mock('../lib/configOptionsQuery', () => {
  const STABLE = { data: { target_lender_refs: ['All'], target_lender_refs_status: 'live' }, isError: false };
  return { useConfigOptionsQuery: () => STABLE };
});
vi.mock('../components/FootprintProvider', () => ({ useFootprint: () => footprintMock.value }));
vi.mock('../components/AppContext', () => ({
  useApp: () => ({ setDrawer: vi.fn(), showEvidence: true, showConfidence: true, canAccessAdmin: false }),
}));

import PortfolioBuilder from './portfolio-builder';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const STATES = [
  { state_code: 'AZ', state_name: 'Arizona' },
  { state_code: 'IL', state_name: 'Illinois' },
  { state_code: 'TX', state_name: 'Texas' },
];

const changes = vi.fn<(next: string[]) => void>();

function Harness({ initial = [] as string[] }) {
  const [value, setValue] = useState<string[]>(initial);
  return (
    <>
      <StateMultiSelect
        label="GEO"
        allLabel="All 3 states"
        states={STATES}
        value={value}
        onChange={(next) => {
          changes(next);
          setValue(next);
        }}
      />
      <button type="button" id="after">Next control</button>
    </>
  );
}

function press(key: string, init: KeyboardEventInit = {}): void {
  const target = document.activeElement ?? document.body;
  act(() => {
    target.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true, ...init }));
  });
}

const trigger = (): HTMLButtonElement =>
  document.querySelector<HTMLButtonElement>('button.filter') as HTMLButtonElement;
const listbox = (): HTMLElement | null => document.querySelector<HTMLElement>('[role="listbox"]');
const options = (): HTMLElement[] =>
  [...document.querySelectorAll<HTMLElement>('[role="option"]')];
// a11y-02: the listbox holds DOM focus while open, so IT carries
// aria-activedescendant; the trigger button never does.
const activeOption = (): HTMLElement | null => {
  const id = listbox()?.getAttribute('aria-activedescendant');
  return id ? document.getElementById(id) : null;
};

describe('Portfolio Builder state picker — keyboard operation', () => {
  let root: Root;

  beforeEach(() => {
    document.body.innerHTML = '<div id="root"></div>';
    root = createRoot(document.getElementById('root') as HTMLElement);
    changes.mockClear();
  });

  afterEach(() => {
    act(() => root.unmount());
    document.body.innerHTML = '';
  });

  function render(initial: string[] = []): void {
    act(() => root.render(<Harness initial={initial} />));
    trigger().focus();
  }

  it('ArrowDown opens the listbox and lands the active option on a real, focused option', () => {
    render();
    expect(listbox()).toBeNull();
    expect(trigger().getAttribute('aria-expanded')).toBe('false');

    press('ArrowDown');

    expect(trigger().getAttribute('aria-expanded')).toBe('true');
    expect(listbox()?.getAttribute('aria-multiselectable')).toBe('true');
    expect(trigger().getAttribute('aria-controls')).toBe(listbox()?.id);
    expect(options().map((option) => option.textContent)).toEqual([
      'All 3 states',
      'Arizona',
      'Illinois',
      'Texas',
    ]);
    // aria-activedescendant must resolve to an element on the focused listbox.
    expect(activeOption()).toBe(options()[0]);
    expect(document.activeElement).toBe(listbox());
    expect(trigger().hasAttribute('aria-activedescendant')).toBe(false);
  });

  it('arrow keys, Home and End move the active option (aria-activedescendant changes)', () => {
    render();
    press('ArrowDown');
    const first = listbox()?.getAttribute('aria-activedescendant');

    press('ArrowDown');
    expect(listbox()?.getAttribute('aria-activedescendant')).not.toBe(first);
    expect(activeOption()?.textContent).toBe('Arizona');
    expect(document.activeElement).toBe(listbox());

    press('ArrowDown');
    expect(activeOption()?.textContent).toBe('Illinois');
    press('ArrowUp');
    expect(activeOption()?.textContent).toBe('Arizona');
    press('End');
    expect(activeOption()?.textContent).toBe('Texas');
    press('ArrowDown');
    expect(activeOption()?.textContent).toBe('All 3 states');
    press('Home');
    expect(activeOption()?.textContent).toBe('All 3 states');

    // The focused listbox is the one tab stop; options are not tabbable.
    expect(listbox()?.tabIndex).toBe(0);
    expect(options().filter((option) => option.tabIndex >= 0)).toHaveLength(0);
  });

  it('Space toggles a state, keeps the menu open, and leaves the active option where the user is', () => {
    render();
    press('ArrowDown');
    press('ArrowDown'); // Arizona
    press(' ');

    expect(changes).toHaveBeenLastCalledWith(['AZ']);
    expect(options()[1].getAttribute('aria-selected')).toBe('true');
    expect(trigger().getAttribute('aria-label')).toBe('GEO: Arizona');
    expect(listbox()).not.toBeNull();

    press('End'); // Texas
    press(' ');
    expect(changes).toHaveBeenLastCalledWith(['AZ', 'TX']);
    expect(trigger().getAttribute('aria-label')).toBe('GEO: 2 states');
    // The active option stays on Texas; it must not jump back up to Arizona.
    expect(activeOption()?.textContent).toBe('Texas');
    expect(activeOption()).toBe(options()[3]);

    press(' ');
    expect(changes).toHaveBeenLastCalledWith(['AZ']);
    expect(options()[3].getAttribute('aria-selected')).toBe('false');

    press('Home');
    press('Enter');
    expect(changes).toHaveBeenLastCalledWith([]);
    expect(trigger().getAttribute('aria-label')).toBe('GEO: All 3 states');
  });

  it('collapses "every state selected" to the footprint default', () => {
    render(['AZ', 'IL']);
    press('ArrowDown');
    expect(activeOption()?.textContent).toBe('Arizona'); // opens on the first selected state
    press('End');
    press(' ');

    expect(changes).toHaveBeenLastCalledWith([]);
    expect(trigger().getAttribute('aria-label')).toBe('GEO: All 3 states');
  });

  it('Escape closes the listbox and returns focus to the trigger', () => {
    render();
    press('ArrowDown');
    press('ArrowDown');
    expect(document.activeElement).not.toBe(trigger());

    press('Escape');

    expect(listbox()).toBeNull();
    expect(trigger().getAttribute('aria-expanded')).toBe('false');
    expect(trigger().hasAttribute('aria-activedescendant')).toBe(false);
    expect(document.activeElement).toBe(trigger());
  });

  it('Tab closes the listbox without swallowing the key, so focus moves on from the trigger', () => {
    render();
    press('ArrowDown');
    press('ArrowDown');
    // Non-vacuity: the menu really is open with focus inside it. Against the
    // old mouse-only picker nothing opened, so "closed after Tab" proved nothing.
    expect(listbox()).not.toBeNull();
    expect(document.activeElement).toBe(listbox());
    expect(activeOption()).toBe(options()[1]);

    const tab = new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true });
    act(() => {
      document.activeElement?.dispatchEvent(tab);
    });

    expect(listbox()).toBeNull();
    // Not prevented: the browser's own Tab carries on. Focus is parked on the
    // trigger first so the next stop is the control after the picker, not
    // wherever the browser lands after the focused option unmounts.
    expect(tab.defaultPrevented).toBe(false);
    expect(document.activeElement).toBe(trigger());
  });
});

describe('Portfolio Builder reconciles GEO and filters in render (no effect)', () => {
  const AZ_IL_TX = [
    { state_code: 'AZ', state_name: 'Arizona' },
    { state_code: 'IL', state_name: 'Illinois' },
    { state_code: 'TX', state_name: 'Texas' },
  ];
  let root: Root;
  let container: HTMLDivElement;
  let navigate: NavigateFunction | null = null;
  let queryClient: QueryClient;
  const keepNavigate = (next: NavigateFunction) => {
    navigate = next;
  };

  function NavigateProbe({ onNavigate }: { onNavigate: (next: NavigateFunction) => void }) {
    const next = useNavigate();
    useEffect(() => onNavigate(next), [next, onNavigate]);
    return null;
  }

  beforeEach(() => {
    vi.clearAllMocks();
    footprintMock.value = { ready: true, usingFallback: false, states: AZ_IL_TX };
    apiMocks.portfolioPreview.mockResolvedValue({ marketable_population: 0, high_intent_leads: 5, campaign_build_eligible: false });
    apiMocks.campaigns.mockResolvedValue({ campaigns: [] });
    apiMocks.salesCampaignPerformance.mockReturnValue(new Promise(() => undefined));
    apiMocks.campaignRecommendation.mockReturnValue(new Promise(() => undefined));
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  });

  afterEach(() => {
    act(() => root.unmount());
    queryClient.clear();
    container.remove();
    navigate = null;
  });

  function render(entries: string[], index: number) {
    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter initialEntries={entries} initialIndex={index}>
            <NavigateProbe onNavigate={keepNavigate} />
            <PortfolioBuilder />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
  }

  async function waitUntil(condition: () => boolean, ms = 4000) {
    const started = Date.now();
    while (!condition()) {
      if (Date.now() - started > ms) throw new Error('waitUntil timeout');
      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 10));
      });
    }
  }

  const label = (prefix: string) =>
    container.querySelector<HTMLButtonElement>(`button[aria-label^="${prefix}:"]`)?.getAttribute('aria-label');
  const lastCriteria = () => {
    const calls = apiMocks.portfolioPreview.mock.calls;
    return calls[calls.length - 1]?.[0] as Record<string, unknown> | undefined;
  };
  /** The Lead Queue CTA is built from the COMMITTED build (filters + states). */
  const committedQuery = () => {
    const link = [...container.querySelectorAll<HTMLAnchorElement>('a')].find((a) => a.textContent?.includes('Open lead queue'));
    return new URL(link?.getAttribute('href') ?? '/', 'https://mip.local').searchParams;
  };

  it('browser back and forward reset the filters, the GEO picker and the committed build to the URL', async () => {
    render(['/portfolio-builder?occupancy=Non-owner-occupied&states=IL', '/portfolio-builder'], 1);
    await waitUntil(() => apiMocks.portfolioPreview.mock.calls.length === 1);
    expect(label('OCCUPANCY')).toBe('OCCUPANCY: Owner-occupied');
    expect(label('GEO')).toBe('GEO: All 3 states');

    await act(async () => {
      void navigate?.(-1);
    });
    await waitUntil(() => apiMocks.portfolioPreview.mock.calls.length === 2);
    expect(label('OCCUPANCY')).toBe('OCCUPANCY: Non-owner-occupied');
    expect(label('GEO')).toBe('GEO: Illinois');
    expect(lastCriteria()).toMatchObject({ occupancy: 'Non-owner-occupied', states: ['IL'] });
    await waitUntil(() => committedQuery().get('states') === 'IL');
    expect(committedQuery().get('occupancy')).toBe('Non-owner-occupied');

    await act(async () => {
      void navigate?.(1);
    });
    await waitUntil(() => label('OCCUPANCY') === 'OCCUPANCY: Owner-occupied');
    expect(label('GEO')).toBe('GEO: All 3 states');
    expect(committedQuery().get('states')).toBeNull();
    expect(committedQuery().get('occupancy')).toBe('Owner-occupied');
  });

  it('a footprint change drops the selected states outside it', async () => {
    render(['/portfolio-builder?states=AZ,IL'], 0);
    await waitUntil(() => apiMocks.portfolioPreview.mock.calls.length === 1);
    expect(label('GEO')).toBe('GEO: 2 states');
    expect(lastCriteria()).toMatchObject({ states: ['AZ', 'IL'] });

    footprintMock.value = { ready: true, usingFallback: false, states: AZ_IL_TX.slice(1) };
    render(['/portfolio-builder?states=AZ,IL'], 0);
    await waitUntil(() => label('GEO') === 'GEO: Illinois');
    await waitUntil(() => apiMocks.portfolioPreview.mock.calls.length === 2);
    expect(lastCriteria()).toMatchObject({ states: ['IL'] });
  });

  it('a footprint change drops a committed state the URL reconcile does not reach', async () => {
    // Run with AZ + IL, then deselect AZ in the picker without running: the
    // picker holds [IL], the committed build (and the URL) still [AZ, IL].
    render(['/portfolio-builder?states=AZ,IL'], 0);
    await waitUntil(() => apiMocks.portfolioPreview.mock.calls.length === 1);
    await waitUntil(() => committedQuery().get('states') === 'AZ,IL');
    const geo = container.querySelector<HTMLButtonElement>('button[aria-label^="GEO:"]')!;
    act(() => geo.focus());
    press('ArrowDown'); // opens on the first selected state, Arizona
    expect(activeOption()?.textContent).toBe('Arizona');
    press(' ');
    press('Escape');
    expect(label('GEO')).toBe('GEO: Illinois');
    expect(committedQuery().get('states')).toBe('AZ,IL');

    // The footprint loses AZ. Parsed against it the URL now reads [IL], which
    // equals the picker, so the URL reconcile has nothing to do; only the
    // footprint sanitize takes AZ out of the committed build.
    footprintMock.value = { ready: true, usingFallback: false, states: AZ_IL_TX.slice(1) };
    render(['/portfolio-builder?states=AZ,IL'], 0);
    await waitUntil(() => apiMocks.portfolioPreview.mock.calls.length === 2);
    expect(lastCriteria()).toMatchObject({ states: ['IL'] });
    await waitUntil(() => committedQuery().get('states') === 'IL');
    expect(label('GEO')).toBe('GEO: Illinois');
  });

  it('keeps the route free of the eslint-disable that made React Compiler skip it', () => {
    const source = readFileSync(join(process.cwd(), 'src', 'routes', 'portfolio-builder.tsx'), 'utf-8');
    expect(source).not.toMatch(/eslint-disable[^\n]*react-hooks/);
    expect(source).not.toMatch(/useEffect\(/);
  });
});
