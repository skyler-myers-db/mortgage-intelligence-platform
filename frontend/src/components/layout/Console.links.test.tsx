/**
 * @vitest-environment happy-dom
 *
 * The Console's saved-workspace links warm their route CHUNK on intent
 * (2026-09-21 audit bundle-09 item 1): a pointer entering or focus landing
 * on a saved lead or a saved draft preloads the borrower-360 / offer route
 * module, never a borrower or offer read (those write VIEW_BORROWER,
 * RECOMMEND_OFFER and DRAFT_OUTREACH and stay click-only), and nothing at
 * all under Save-Data.
 *
 * And the Console's labels (a11y-05, deviation console-row-captions): every
 * <label> names a control (the three switches, by htmlFor), the row captions
 * are .tweak-row__label spans that name their groups, and the <aside> is a
 * complementary landmark by its name alone.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { installLocalStorage } from '../../test/installLocalStorage';

const LEAD = 'B-0000000000001';
const DRAFT = 'B-0000000000002';

vi.mock('../AppContext', () => ({
  useApp: () => ({
    consoleOpen: true,
    setConsoleOpen: vi.fn(),
    theme: 'dark',
    setTheme: vi.fn(),
    themePreference: 'dark',
    setThemePreference: vi.fn(),
    accent: 'bright',
    setAccent: vi.fn(),
    density: 'comfortable',
    setDensity: vi.fn(),
    lender: 'Summit Mortgage',
    showEvidence: true,
    setShowEvidence: vi.fn(),
    showConfidence: true,
    setShowConfidence: vi.fn(),
    setGenieOpen: vi.fn(),
    savedLeads: {
      [LEAD]: { borrower_id: LEAD, city: 'Chicago', state: 'IL', zip: '60611', saved_at: '1', updated_at: '1' },
    },
    savedDrafts: {
      [`${DRAFT}::email`]: { borrower_id: DRAFT, channel: 'email', saved_at: '1', updated_at: '1' },
    },
    workspaceStatus: 'ready',
    workspaceError: null,
    refreshWorkspace: vi.fn(),
    recentActivityFocusRequest: 0,
    acknowledgeRecentActivityFocus: vi.fn(),
  }),
}));

const apiCalls = vi.hoisted(() => ({ count: 0 }));
vi.mock('../../lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../lib/api')>();
  // Every api method counts, so a borrower / offer read on intent is caught.
  const counted = new Proxy(actual.api, {
    get(target, prop, receiver) {
      const value: unknown = Reflect.get(target, prop, receiver);
      if (typeof value !== 'function') return value;
      return (...args: unknown[]) => {
        if (prop !== 'myAuditEvents') apiCalls.count += 1;
        return prop === 'myAuditEvents' ? new Promise(() => {}) : (value as (...a: unknown[]) => unknown)(...args);
      };
    },
  });
  return { ...actual, api: counted };
});

const preloadRouteForPath = vi.fn();
vi.mock('../../lib/routePreloaders', () => ({ preloadRouteForPath: (path: string) => preloadRouteForPath(path) }));
const saveData = vi.hoisted(() => ({ on: false }));
vi.mock('../../lib/prefetch', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../lib/prefetch')>()),
  saveDataRequested: () => saveData.on,
}));

import { Console } from './Console';

describe('Console saved-workspace links (bundle-09 item 1)', () => {
  let container: HTMLDivElement;
  let root: Root;
  let fetchSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(async () => {
    installLocalStorage();
    preloadRouteForPath.mockReset();
    saveData.on = false;
    apiCalls.count = 0;
    fetchSpy = vi.spyOn(globalThis, 'fetch');
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter><Console /></MemoryRouter>
        </QueryClientProvider>,
      );
    });
    apiCalls.count = 0;
    fetchSpy.mockClear();
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    fetchSpy.mockRestore();
  });

  const link = (href: string) => container.querySelector<HTMLAnchorElement>(`a.saved-workspace__item[href="${href}"]`)!;

  function hover(el: Element) {
    act(() => {
      el.dispatchEvent(new PointerEvent('pointerover', { bubbles: true }));
      el.dispatchEvent(new PointerEvent('pointerenter', { bubbles: false }));
    });
  }

  it('hovering a saved lead warms the borrower-360 chunk and makes no request', () => {
    hover(link(`/borrower-360/${LEAD}`));
    expect(preloadRouteForPath).toHaveBeenCalledWith(`/borrower-360/${LEAD}`);
    expect(apiCalls.count).toBe(0);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it('focusing a saved draft warms the offer chunk and makes no request', () => {
    act(() => link(`/offer-orchestrator/${DRAFT}`).focus());
    expect(preloadRouteForPath).toHaveBeenCalledWith(`/offer-orchestrator/${DRAFT}`);
    expect(apiCalls.count).toBe(0);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it('preloads nothing under Save-Data', () => {
    saveData.on = true;
    hover(link(`/borrower-360/${LEAD}`));
    act(() => link(`/offer-orchestrator/${DRAFT}`).focus());
    expect(preloadRouteForPath).not.toHaveBeenCalled();
  });
});

describe('Console labels and captions (a11y-05)', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(async () => {
    installLocalStorage();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter><Console /></MemoryRouter>
        </QueryClientProvider>,
      );
    });
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  it('every <label> names an existing control', () => {
    // The row labels (the property-lookup form inside a row has its own).
    const labels = [...container.querySelectorAll<HTMLLabelElement>('.tweak-row > label, .tweak-row > .row > label')];
    expect(labels.map((label) => label.textContent)).toEqual([
      'Show evidence chips',
      'Show signal meters',
      'Single-key shortcuts',
    ]);
    for (const label of labels) {
      const control = document.getElementById(label.htmlFor);
      expect(control?.classList.contains('switch'), label.textContent ?? '').toBe(true);
    }
  });

  it('keeps each switch\'s accessible name (fixture specs pin them)', () => {
    expect(document.getElementById('console-evidence-switch')?.getAttribute('aria-label')).toBe('Toggle evidence chips');
    expect(document.getElementById('console-signal-switch')?.getAttribute('aria-label')).toBe('Toggle signal strength meters');
    expect(document.getElementById('console-single-key-switch')?.getAttribute('aria-labelledby')).toBe('console-single-key-label');
  });

  it('captions the rows with spans that name their groups', () => {
    const captions = [...container.querySelectorAll('.tweak-row > .tweak-row__label, .tweak-row > .row > .tweak-row__label')];
    expect(captions.map((caption) => caption.textContent)).toEqual([
      'Theme', 'Accent', 'Density', 'Property lookup', 'Recent activity', 'Configured tenant', 'Saved workspace', 'Ask Genie',
    ]);
    expect(captions.every((caption) => caption.tagName === 'SPAN')).toBe(true);
    const accent = container.querySelector('.swatches');
    expect(accent?.getAttribute('role')).toBe('group');
    expect(document.getElementById(accent?.getAttribute('aria-labelledby') ?? '')?.textContent).toBe('Accent');
    const density = [...container.querySelectorAll('.tweak-row > .segmented')]
      .find((group) => group.textContent?.includes('Comfortable'));
    expect(density?.getAttribute('role')).toBe('group');
    expect(density?.hasAttribute('aria-label')).toBe(false);
    expect(document.getElementById(density?.getAttribute('aria-labelledby') ?? '')?.textContent).toBe('Density');
    // "Open Genie" keeps its own name: no label renames it.
    const genie = [...container.querySelectorAll('button')].find((button) => button.textContent === 'Open Genie');
    expect(genie?.labels?.length ?? 0).toBe(0);
  });

  it('is a complementary landmark by its name alone, with no redundant role', () => {
    const aside = container.querySelector('aside#workspace-console');
    expect(aside?.hasAttribute('role')).toBe(false);
    expect(aside?.getAttribute('aria-label')).toBe('Workspace console');
  });
});
