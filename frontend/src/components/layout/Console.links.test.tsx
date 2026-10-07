/**
 * @vitest-environment happy-dom
 *
 * The Console's saved-workspace links warm their route CHUNK on intent
 * (2026-09-21 audit bundle-09 item 1): a pointer entering or focus landing
 * on a saved lead or a saved draft preloads the borrower-360 / offer route
 * module, never a borrower or offer read (those write VIEW_BORROWER,
 * RECOMMEND_OFFER and DRAFT_OUTREACH and stay click-only), and nothing at
 * all under Save-Data.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { installLocalStorage } from '../../test/installLocalStorage';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

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
