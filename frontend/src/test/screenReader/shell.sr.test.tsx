// @vitest-environment happy-dom
/**
 * Virtual screen-reader pass: the app shell's skip link, landmarks and route
 * change (WCAG 2.4.1, 1.3.1, 4.1.2, 4.1.3; docs/accessibility/VPAT.md).
 *
 * The REAL AppShell renders over an unreachable backend (every fetch 404s),
 * as AppShell.continuity.test.tsx does: there is no mock data path. The walk
 * records what @guidepup/virtual-screen-reader speaks in
 * transcripts/shell.txt and pins the phrases a screen-reader user needs.
 *
 * Not modelled here (see virtualReader.ts): happy-dom performs no fragment
 * navigation, so following the skip link does not move focus by itself. The
 * browser does (tabindex=-1 on <main>; proven live by
 * tests/e2e/accessibility_procurement.spec.ts "skip link moves keyboard focus
 * into the main landmark"). The step below follows the link and then focuses
 * its fragment target the way a browser does, so what is recorded is the
 * reader's announcement of that focus, not proof of the browser's move.
 */
import { act, useEffect } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes, useNavigate, type NavigateFunction } from 'react-router';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { AppShell } from '../../components/layout/AppShell';
import { PageShell } from '../../components/layout/PageShell';
import { mount } from '../render';
import { expectTranscript, press, readToEnd, registerReaderHooks, reportFocus, settle, startReader, walkLandmarks } from './virtualReader';

registerReaderHooks();

let navigate: NavigateFunction;

function NavigateProbe() {
  const routerNavigate = useNavigate();
  useEffect(() => {
    navigate = routerNavigate;
  }, [routerNavigate]);
  return null;
}

let queryClient: QueryClient;

// The shell fetches the Toaster chunk as soon as it mounts (ShellToaster), so
// the app at rest has its "Notifications" region. Transform the chunk once up
// front and wait for the region below, or the walk would race the lazy load
// (it did: a full-suite run, with the chunk already transformed, read the
// region and an isolated run did not).
beforeAll(async () => {
  await import('../../components/feedback/Toaster');
}, 60_000);

beforeEach(() => {
  window.sessionStorage.clear();
  document.title = 'Mortgage Intelligence Platform';
  vi.stubGlobal('fetch', vi.fn(async () => new Response('{"detail":"not found"}', { status: 404 })));
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
});

afterEach(() => {
  queryClient.clear();
  window.sessionStorage.clear();
  vi.unstubAllGlobals();
});

async function renderShell() {
  const mounted = await mount(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/lead-queue']}>
        <NavigateProbe />
        <AppShell>
          <Routes>
            <Route path="/lead-queue" element={<PageShell title="Ranked borrowers">queue</PageShell>} />
            <Route path="/borrower-360/:id" element={<PageShell title="Borrower">dossier</PageShell>} />
          </Routes>
        </AppShell>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  await vi.waitFor(async () => {
    await settle(1);
    expect(document.querySelector('section.toast-region[aria-label="Notifications"]'), 'the shell toast region').not.toBeNull();
  }, { timeout: 10_000 });
  await settle();
  return mounted;
}

describe('virtual screen reader: app shell', () => {
  it('reaches the skip link first, lists the landmarks and announces a route change', async () => {
    await renderShell();
    const reader = await startReader();

    const tab = await reader.step('Tab from the top of the page', async () => {
      await press('Tab');
    });
    expect(tab).toEqual(['link, Skip to main content']);
    expect(document.activeElement?.textContent).toBe('Skip to main content');

    const skip = await reader.step('Enter on the skip link (the browser then focuses #main-content)', async () => {
      await press('Enter');
      const target = document.getElementById(new URL((document.activeElement as HTMLAnchorElement).href).hash.slice(1));
      expect(target?.tagName).toBe('MAIN');
      expect(target?.getAttribute('tabindex')).toBe('-1');
      await act(async () => target?.focus());
    });
    expect(skip).toEqual(['main']);
    expect(document.activeElement?.id).toBe('main-content');

    const landmarks = await reader.step('Landmarks from the top of the page (NVDA D / VoiceOver landmark rotor)', async () => {
      await reader.restart();
      await walkLandmarks();
    });
    expect(landmarks).toContain('navigation, Primary navigation');
    expect(landmarks).toContain('banner');
    expect(landmarks).toContain('main');
    // The closed workspace console is aria-hidden: never a landmark stop.
    expect(landmarks.join('\n')).not.toContain('Workspace console');

    const route = await reader.step('Navigate to a borrower (route announcer + h1 focus)', async () => {
      await act(async () => {
        await navigate('/borrower-360/B-0123456789ABC');
      });
      await settle(8);
      await reportFocus();
    });
    expect(route).toContain('polite: Borrower 360 · B-0123456789ABC');
    expect(route).toContain('heading, Borrower, level 1');
    expect(document.activeElement?.tagName).toBe('H1');

    const page = await reader.step('Say-all of the borrower page from the heading', async () => {
      await readToEnd();
    });
    expect(page).toContain('end of document');
    // The page footer sits inside <main>, where it is a generic element: it is
    // read for its content, never by a name (ARIA 1.2 prohibits one there).
    expect(page).toContain('image, Entrada');
    expect(page.some((phrase) => phrase.includes('Product footer'))).toBe(false);

    await expectTranscript('shell', reader);
  }, 30_000);
});
