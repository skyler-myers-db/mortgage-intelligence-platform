// @vitest-environment happy-dom

import { act, useEffect, useRef, useState, type ReactNode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, Route, Routes, useNavigate, type NavigateFunction } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { PageShell } from '../components/layout/PageShell';
import { ROUTE_FOCUS_DEADLINE_MS, useRouteAnnouncer } from './useRouteAnnouncer';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

/**
 * useRouteAnnouncer — rendered-DOM contract for "where am I?" after navigation.
 *
 * Audit 2026-09-21 (`critic-v1`, `a11y-03`, `shell-08`): every route shared one
 * `document.title`, focus stayed on the activated link, and nothing was
 * announced. Asserted here at the layer a user or a screen reader observes:
 * `document.title`, `document.activeElement`, and the live region's text. Pages
 * render the real <PageShell>, so the `<h1 tabIndex={-1}>` it must provide is
 * covered too.
 */

let navigate: NavigateFunction;
/** Resolves the "lazy chunk" of <LazyPage>: swaps its fallback for the page. */
let revealLazyPage: () => void;

function NavigateProbe() {
  const routerNavigate = useNavigate();
  useEffect(() => {
    navigate = routerNavigate;
  }, [routerNavigate]);
  return null;
}

function Shell({ children }: { children: ReactNode }) {
  const mainRef = useRef<HTMLElement | null>(null);
  const liveRef = useRef<HTMLDivElement | null>(null);
  useRouteAnnouncer(mainRef, liveRef);
  return (
    <>
      <div ref={liveRef} role="status" aria-live="polite" data-testid="announcer" />
      <main ref={mainRef} id="main-content" tabIndex={-1}>
        <a href="/analytics" data-testid="nav-link">Analytics</a>
        {children}
      </main>
      <div role="dialog" aria-label="Genie">
        <textarea data-testid="genie-input" />
      </div>
    </>
  );
}

function Page({ title }: { title: string }) {
  return (
    <PageShell eyebrow="Eyebrow" title={title}>
      <button type="button" data-testid="page-button">Filter</button>
    </PageShell>
  );
}

function LazyPage() {
  const [ready, setReady] = useState(false);
  useEffect(() => {
    revealLazyPage = () => setReady(true);
  }, []);
  return ready ? <Page title="Rules, data sources, and audit" /> : <div role="status">Loading</div>;
}

function GlossaryPage() {
  return (
    <PageShell title="Mortgage intelligence glossary">
      <article id="clip" tabIndex={-1}>CLIP</article>
      <article id="avm" tabIndex={-1}>AVM</article>
    </PageShell>
  );
}

describe('useRouteAnnouncer', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    document.title = 'Mortgage Intelligence Platform';
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.useRealTimers();
  });

  async function mount(initialEntry: string): Promise<void> {
    await act(async () => {
      root.render(
        <MemoryRouter initialEntries={[initialEntry]}>
          <NavigateProbe />
          <Shell>
            <Routes>
              <Route path="/lead-queue" element={<Page title="Ranked borrowers" />} />
              <Route path="/analytics" element={<Page title="Analytics" />} />
              <Route path="/borrower-360/:id" element={<Page title="Borrower" />} />
              <Route path="/glossary" element={<GlossaryPage />} />
              <Route path="/admin-config" element={<LazyPage />} />
              <Route path="*" element={<Page title="Page not found" />} />
            </Routes>
          </Shell>
        </MemoryRouter>,
      );
    });
  }

  async function go(to: string): Promise<void> {
    await act(async () => {
      await navigate(to);
    });
  }

  const announcer = () => container.querySelector('[data-testid="announcer"]') as HTMLElement;
  const heading = () => container.querySelector('h1') as HTMLElement;

  it('titles the first page but neither announces it nor moves focus on load', async () => {
    await mount('/lead-queue');

    expect(document.title).toBe('Lead Queue · Mortgage Intelligence Platform');
    expect(announcer().textContent).toBe('');
    expect(document.activeElement).toBe(document.body);
  });

  it('on navigation: new title, page name announced, focus on the page heading', async () => {
    await mount('/lead-queue');
    (container.querySelector('[data-testid="nav-link"]') as HTMLElement).focus();

    await go('/analytics');

    expect(document.title).toBe('Analytics · Mortgage Intelligence Platform');
    expect(announcer().textContent).toBe('Analytics');
    expect(heading().textContent).toBe('Analytics');
    expect(heading().getAttribute('tabindex')).toBe('-1');
    expect(document.activeElement).toBe(heading());
  });

  it('puts the masked borrower id in the title and announcement of a detail route', async () => {
    await mount('/lead-queue');

    await go('/borrower-360/B-0123456789ABC');

    expect(document.title).toBe('Borrower 360 · B-0123456789ABC · Mortgage Intelligence Platform');
    expect(announcer().textContent).toBe('Borrower 360 · B-0123456789ABC');
  });

  it('never echoes URL text that is not a masked borrower id', async () => {
    await mount('/lead-queue');

    await go('/borrower-360/jane-doe');

    expect(document.title).toBe('Borrower 360 · Mortgage Intelligence Platform');
    expect(announcer().textContent).toBe('Borrower 360');
  });

  it('re-announces when two consecutive pages share a label', async () => {
    await mount('/lead-queue');
    await go('/borrower-360/first');
    expect(announcer().textContent).toBe('Borrower 360');

    await go('/borrower-360/second');

    // Same words, different text node value: the live region speaks again.
    expect(announcer().textContent).toBe('Borrower 360\u00A0');
    await go('/borrower-360/third');
    expect(announcer().textContent).toBe('Borrower 360');
  });

  it('names an unknown path "Page not found"', async () => {
    await mount('/lead-queue');

    await go('/no-such-page');

    expect(document.title).toBe('Page not found · Mortgage Intelligence Platform');
    expect(announcer().textContent).toBe('Page not found');
  });

  it('waits for a lazy route to render its heading, then focuses it', async () => {
    await mount('/lead-queue');

    await go('/admin-config');
    expect(document.title).toBe('Admin · Mortgage Intelligence Platform');
    expect(announcer().textContent).toBe('Admin');
    expect(container.querySelector('h1')).toBeNull();

    await act(async () => {
      revealLazyPage();
    });
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0)); // MutationObserver delivery
    });

    expect(document.activeElement).toBe(heading());
  });

  it('falls back to <main> when no heading ever renders', async () => {
    await mount('/lead-queue');
    vi.useFakeTimers();

    await go('/admin-config');
    expect(document.activeElement).not.toBe(container.querySelector('main'));
    await act(async () => {
      vi.advanceTimersByTime(ROUTE_FOCUS_DEADLINE_MS);
    });

    expect(document.activeElement).toBe(container.querySelector('main'));
  });

  it('stays silent and leaves focus alone when only the search params change', async () => {
    await mount('/analytics');
    await go('/lead-queue');
    const button = container.querySelector('[data-testid="page-button"]') as HTMLElement;
    button.focus();
    announcer().textContent = '';

    await go('/lead-queue?segment=itm');

    expect(announcer().textContent).toBe('');
    expect(document.activeElement).toBe(button);
  });

  it('does not pull focus out of an open dialog (Genie navigates the page behind it)', async () => {
    await mount('/lead-queue');
    const genieInput = container.querySelector('[data-testid="genie-input"]') as HTMLElement;
    genieInput.focus();

    await go('/analytics');

    expect(document.title).toBe('Analytics · Mortgage Intelligence Platform');
    expect(announcer().textContent).toBe('Analytics');
    expect(document.activeElement).toBe(genieInput);
  });

  it('focuses a focusable #hash target instead of the heading', async () => {
    await mount('/lead-queue');

    await go('/glossary#clip');
    expect(announcer().textContent).toBe('Glossary');
    expect(document.activeElement).toBe(document.getElementById('clip'));

    // A hash-only change moves focus to the new target without re-announcing.
    announcer().textContent = '';
    await go('/glossary#avm');
    expect(document.activeElement).toBe(document.getElementById('avm'));
    expect(announcer().textContent).toBe('');
  });
});
