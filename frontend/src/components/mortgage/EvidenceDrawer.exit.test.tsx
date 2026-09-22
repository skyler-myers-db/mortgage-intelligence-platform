/**
 * @vitest-environment happy-dom
 *
 * 2026-09-21 audit css-03, TSX half. EvidenceDrawer rendered its body as
 * `{d && ...}` off the live source, so `setDrawer(null)` emptied the panel in
 * the same commit that started its slide-out: the drawer animated out blank.
 *
 * Contract pinned here, at the rendered DOM:
 *  - the closing source stays rendered until the PANEL's own transition ends;
 *  - the focus trap is released and focus is back on the trigger when the exit
 *    STARTS, and the retained copy is inert;
 *  - nothing is retained when there is nothing to wait for: reduced motion, or
 *    a panel with no transition (the default in this DOM: no stylesheet).
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { EvidenceDrawer } from './EvidenceDrawer';
import type { DrawerSource } from '../AppContext';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const appMocks = vi.hoisted(() => ({
  drawer: null as DrawerSource | null,
  setDrawer: vi.fn(),
}));

vi.mock('../AppContext', () => ({
  useApp: () => ({ drawer: appMocks.drawer, setDrawer: appMocks.setDrawer, canAccessAdmin: false }),
}));

vi.mock('../../lib/api', () => ({ api: {} }));

const FIRST: DrawerSource = {
  title: 'Lead population',
  short: 'mip.gold.lead_population',
  description: 'Governed borrower candidates used by Module 0 lead ranking.',
  assetPath: 'mip.gold.lead_population',
};

const SECOND: DrawerSource = {
  title: 'Rate spread',
  short: 'mip.silver.rate_spread',
  description: 'Spread between the borrower note rate and the current market rate.',
  assetPath: 'mip.silver.rate_spread',
};

const PLACEHOLDER = 'Tap any evidence chip or KPI source line to inspect the lineage.';

async function settle(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
    await new Promise((resolve) => window.setTimeout(resolve, 0));
  });
}

describe('EvidenceDrawer exit', () => {
  let root: Root;
  let queryClient: QueryClient;

  beforeEach(() => {
    document.body.innerHTML = '<button id="launcher">Open evidence</button><div id="root"></div>';
    root = createRoot(document.getElementById('root') as HTMLElement);
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    appMocks.drawer = null;
  });

  afterEach(() => {
    act(() => root.unmount());
    queryClient.clear();
    document.body.innerHTML = '';
    vi.restoreAllMocks();
  });

  async function show(source: DrawerSource | null): Promise<void> {
    appMocks.drawer = source;
    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <EvidenceDrawer />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
    await settle();
  }

  const panel = (): HTMLElement => document.querySelector<HTMLElement>('.drawer') as HTMLElement;
  const title = (): string => document.getElementById('evidence-drawer-title')?.textContent ?? '';

  /** Stand in for the stylesheet: the panel animates its exit. */
  function animatePanel(durationSeconds: number): void {
    const realGetComputedStyle = window.getComputedStyle.bind(window);
    vi.spyOn(window, 'getComputedStyle').mockImplementation((element: Element, pseudo?: string | null) => {
      const style = realGetComputedStyle(element, pseudo);
      if (element !== panel()) return style;
      return new Proxy(style, {
        get(target, property, receiver) {
          if (property === 'transitionDuration') return `${durationSeconds}s, ${durationSeconds}s`;
          if (property === 'transitionDelay') return '0s';
          return Reflect.get(target, property, receiver);
        },
      });
    });
  }

  async function transitionEnd(target: Element): Promise<void> {
    await act(async () => {
      target.dispatchEvent(new Event('transitionend', { bubbles: true }));
    });
  }

  it('keeps the closing source rendered until the panel transition ends, releasing focus at the start', async () => {
    const launcher = document.getElementById('launcher') as HTMLButtonElement;
    launcher.focus();
    await show(FIRST);
    animatePanel(0.36);
    expect(panel().classList.contains('is-open')).toBe(true);
    expect(panel().hasAttribute('inert')).toBe(false);
    expect(document.activeElement).toBe(document.querySelector('.drawer__close'));

    await show(null);

    // Exit has STARTED: closed class, trap released, focus back on the trigger…
    expect(panel().classList.contains('is-open')).toBe(false);
    expect(panel().getAttribute('aria-hidden')).toBe('true');
    expect(panel().hasAttribute('inert')).toBe(true);
    expect(document.activeElement).toBe(launcher);
    // …while the panel still shows what it is sliding out with.
    expect(title()).toBe(FIRST.title);
    expect(panel().textContent).toContain(FIRST.description);
    expect(panel().querySelector('[role="tablist"]')).not.toBeNull();
    expect(panel().textContent).not.toContain(PLACEHOLDER);

    // A child's transition (a button hover) bubbles through the panel: not the end.
    await transitionEnd(document.querySelector('.drawer__close') as Element);
    expect(title()).toBe(FIRST.title);

    await transitionEnd(panel());
    expect(title()).toBe('Data source');
    expect(panel().textContent).not.toContain(FIRST.description);
    expect(panel().textContent).toContain(PLACEHOLDER);
  });

  it('shows a source opened mid-exit immediately, on Overview', async () => {
    await show(FIRST);
    animatePanel(0.36);
    await act(async () => (document.getElementById('drawer-tab-lineage') as HTMLButtonElement).click());
    expect(document.getElementById('drawer-panel-lineage')).not.toBeNull();

    await show(null);
    // The retained body does not flip tabs while it slides out.
    expect(document.getElementById('drawer-panel-lineage')).not.toBeNull();

    await show(SECOND);
    expect(panel().classList.contains('is-open')).toBe(true);
    expect(title()).toBe(SECOND.title);
    expect(document.getElementById('drawer-panel-overview')).not.toBeNull();

    // The abandoned exit must not clear the newly opened source.
    await transitionEnd(panel());
    expect(title()).toBe(SECOND.title);
  });

  it('does not strand a stale source if transitionend never fires', async () => {
    await show(FIRST);
    animatePanel(0.02);
    await show(null);
    expect(title()).toBe(FIRST.title);

    await act(async () => {
      await new Promise((resolve) => window.setTimeout(resolve, 260));
    });
    expect(title()).toBe('Data source');
  });

  it('clears immediately under prefers-reduced-motion', async () => {
    await show(FIRST);
    animatePanel(0.36);
    vi.spyOn(window, 'matchMedia').mockImplementation(
      (query: string) => ({ matches: query.includes('prefers-reduced-motion'), media: query }) as MediaQueryList,
    );

    await show(null);

    expect(title()).toBe('Data source');
    expect(panel().textContent).toContain(PLACEHOLDER);
  });

  it('clears immediately when the panel has no transition (no stylesheet in this DOM)', async () => {
    await show(FIRST);
    expect(title()).toBe(FIRST.title);

    await show(null);

    expect(title()).toBe('Data source');
    expect(panel().textContent).toContain(PLACEHOLDER);
  });
});
