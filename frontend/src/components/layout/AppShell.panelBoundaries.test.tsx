// @vitest-environment happy-dom

import { act, useEffect, type ReactNode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { rootErrorOptions } from '../../lib/clientErrorLog';
import { useApp } from '../AppContext';
import { AppShell } from './AppShell';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

/**
 * Panel crash containment on the REAL AppShell (audit 2026-09-21 states-01,
 * shell-01). The Console, the evidence drawer and the Genie chat are replaced
 * by probes that can throw; everything else (providers, rail, topbar, main,
 * GenieDock, the three ShellPanelBoundaries) is the shipped shell. Before the
 * panel boundaries, any of these throws unmounted the whole shell, and the
 * Genie chat with its in-flight turn.
 *
 * The Console and drawer probes can also crash the way a real panel does: on
 * a malformed payload read through a `staleTime: Infinity` query (the real
 * drawer's lineage manifest read), which stays cached after the crash. The
 * first read answers `{families: null}`, every later read a valid payload.
 * The probes read under the real panels' query keys, because the panel
 * boundaries' Try again and Close drop only those (DRAWER_RESET_SCOPES,
 * CONSOLE_RESET_SCOPES).
 */

/** The probes' payload: `families` is a required array, so `null` is a payload the contract cannot produce. */
interface ProbePayload {
  families: string[];
}

const probes = vi.hoisted(() => ({
  consoleThrows: false,
  drawerThrows: false,
  genieMounts: 0,
  genieUnmounts: 0,
  /** Read the probe payload query (enabled while the panel is open). */
  consoleReadsPayload: false,
  drawerReadsPayload: false,
  consoleFetches: 0,
  drawerFetches: 0,
  /** First read malformed, later reads valid: what a transient bad payload looks like. */
  payload(fetchNumber: number): unknown {
    return fetchNumber === 1 ? { families: null } : { families: ['lead_scoring'] };
  },
}));

vi.mock('./Console', async () => {
  const { useApp: useAppContext } = await import('../AppContext');
  const { useQuery } = await import('@tanstack/react-query');
  return {
    Console: function ConsoleProbe(): ReactNode {
      const { consoleOpen } = useAppContext();
      const payload = useQuery({
        queryKey: ['audit', 'my-events', 'panel-probe'],
        queryFn: async () => {
          probes.consoleFetches += 1;
          return probes.payload(probes.consoleFetches) as ProbePayload;
        },
        enabled: probes.consoleReadsPayload && consoleOpen,
        staleTime: Infinity,
        retry: false,
      });
      if (probes.consoleThrows) throw new RangeError('Console crashed on borrower B-0TESTBORROWER');
      return (
        <aside id="workspace-console" className="tweaks is-open" role="complementary" aria-label="Workspace console">
          <div data-testid="console-probe" data-families={payload.data?.families.length ?? ''} />
        </aside>
      );
    },
  };
});

vi.mock('../mortgage/EvidenceDrawer', async () => {
  const { useApp: useAppContext } = await import('../AppContext');
  const { useQuery } = await import('@tanstack/react-query');
  return {
    EvidenceDrawer: function EvidenceDrawerProbe(): ReactNode {
      const { drawer } = useAppContext();
      // Like the real drawer's lineage manifest read: always mounted,
      // enabled only while open, never stale.
      const payload = useQuery({
        queryKey: ['mip', 'lineage', 'manifest'],
        queryFn: async () => {
          probes.drawerFetches += 1;
          return probes.payload(probes.drawerFetches) as ProbePayload;
        },
        enabled: probes.drawerReadsPayload && drawer !== null,
        staleTime: Infinity,
        retry: false,
      });
      if (drawer && probes.drawerThrows) throw new TypeError("Cannot read properties of null (reading 'find')");
      return (
        <aside
          className={`drawer ${drawer ? 'is-open' : ''}`}
          data-testid="drawer-probe"
          data-families={payload.data?.families.length ?? ''}
        />
      );
    },
  };
});

vi.mock('../mortgage/GenieChat', () => ({
  GenieChat: function GenieChatProbe(): ReactNode {
    useEffect(() => {
      probes.genieMounts += 1;
      return () => {
        probes.genieUnmounts += 1;
      };
    }, []);
    return <div className="genie is-open" role="dialog" aria-label="Genie chat" data-testid="genie-probe" />;
  },
}));

let app: ReturnType<typeof useApp>;

function AppProbe() {
  const context = useApp();
  useEffect(() => {
    app = context;
  }, [context]);
  return <h1 data-testid="route-content">Lead Queue</h1>;
}

// The Console and drawer bodies load lazily; on a loaded CI runner a lazy
// panel can take longer than vi.waitFor's 1 s default to mount its crash
// surface (CI run 36185829440). The assertion is unchanged, only its budget.
const LAZY_PANEL_WAIT = { timeout: 5_000 };

describe('AppShell panel boundaries', () => {
  let container: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;

  beforeEach(() => {
    probes.consoleThrows = false;
    probes.drawerThrows = false;
    probes.genieMounts = 0;
    probes.genieUnmounts = 0;
    probes.consoleReadsPayload = false;
    probes.drawerReadsPayload = false;
    probes.consoleFetches = 0;
    probes.drawerFetches = 0;
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    vi.stubGlobal('fetch', vi.fn(async () => new Response('{"detail":"not found"}', { status: 404 })));
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container, rootErrorOptions());
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    queryClient.clear();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  async function settle(): Promise<void> {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }

  async function mount(): Promise<void> {
    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter initialEntries={['/lead-queue']}>
            <AppShell>
              <AppProbe />
            </AppShell>
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
    await settle();
  }

  async function update(change: () => void): Promise<void> {
    await act(async () => change());
    await settle();
  }

  const surfaces = () => Array.from(container.querySelectorAll<HTMLElement>('[data-testid="error-surface"]'));

  async function openGenieProbe(): Promise<void> {
    await update(() => app.setGenieOpen(true));
    await vi.waitFor(() => expect(container.querySelector('[data-testid="genie-probe"]')).not.toBeNull());
    expect(probes.genieMounts).toBe(1);
  }

  it('contains a Console throw inside the #workspace-console landmark; the route stays mounted', async () => {
    await mount();
    probes.consoleThrows = true;
    await update(() => app.setConsoleOpen(true));

    await vi.waitFor(() => expect(surfaces()).toHaveLength(1), LAZY_PANEL_WAIT);
    const [surface] = surfaces();
    const landmark = container.querySelector('aside#workspace-console');
    expect(landmark?.getAttribute('role')).toBe('complementary');
    expect(landmark?.getAttribute('aria-label')).toBe('Workspace console');
    expect(landmark?.classList.contains('is-open')).toBe(true);
    expect(landmark?.querySelector('.tweaks__body')?.contains(surface)).toBe(true);
    expect(surface.getAttribute('data-error-boundary')).toBe('console');
    expect(container.querySelector('main#main-content [data-testid="route-content"]')).not.toBeNull();
    expect(container.querySelector('nav[aria-label="Primary navigation"]')).not.toBeNull();
    expect(container.innerHTML).not.toContain('B-0TESTBORROWER');

    // The frame's Close only hides the panel.
    await update(() => landmark?.querySelector<HTMLButtonElement>('button[aria-label="Close console"]')?.click());
    expect(app.consoleOpen).toBe(false);
    expect(container.querySelector('aside#workspace-console')?.classList.contains('is-open')).toBe(false);
  });

  it('a Console or drawer throw never unmounts a mounted Genie chat', async () => {
    await mount();
    await openGenieProbe();

    probes.consoleThrows = true;
    await update(() => app.setConsoleOpen(true));
    probes.drawerThrows = true;
    await update(() => app.setDrawer({ title: 'Lineage manifest', lineageFamily: 'lead_scoring' }));

    await vi.waitFor(() => expect(surfaces()).toHaveLength(2));
    expect(surfaces().map((surface) => surface.getAttribute('data-error-boundary')).sort()).toEqual([
      'console',
      'drawer',
    ]);
    expect(container.querySelector('[data-testid="genie-probe"]')).not.toBeNull();
    expect(probes.genieMounts).toBe(1);
    expect(probes.genieUnmounts).toBe(0);
    expect(container.querySelector('[data-testid="route-content"]')).not.toBeNull();
  });

  it('shows a drawer throw inside an open drawer frame; closing resets the boundary so the next open retries', async () => {
    await mount();
    probes.drawerThrows = true;
    await update(() => app.setDrawer({ title: 'Lineage manifest', lineageFamily: 'lead_scoring' }));

    await vi.waitFor(() => expect(surfaces()).toHaveLength(1), LAZY_PANEL_WAIT);
    // The frame is the same native modal <dialog> as the drawer (stack-05).
    const frame = container.querySelector<HTMLDialogElement>('dialog.drawer.is-open');
    expect(frame?.open).toBe(true);
    expect(frame?.hasAttribute('role')).toBe(false);
    expect(frame?.querySelector('.drawer__body')?.contains(surfaces()[0])).toBe(true);
    expect(container.querySelector('[data-testid="drawer-probe"]')).toBeNull();

    // Modal like the drawer it stands in for: focus starts on Close and Escape closes.
    const close = frame?.querySelector<HTMLButtonElement>('button[aria-label="Close drawer"]');
    await vi.waitFor(() => expect(document.activeElement).toBe(close));
    await update(() => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
    });
    expect(app.drawer).toBeNull();
    expect(surfaces()).toHaveLength(0);
    expect(container.querySelector('[data-testid="drawer-probe"]')).not.toBeNull();

    probes.drawerThrows = false;
    await update(() => app.setDrawer({ title: 'Lineage manifest', lineageFamily: 'lead_scoring' }));
    expect(surfaces()).toHaveLength(0);
    expect(container.querySelector('[data-testid="drawer-probe"]')?.classList.contains('is-open')).toBe(true);
  });

  const LINEAGE_SOURCE = { title: 'Lineage manifest', lineageFamily: 'lead_scoring' } as const;
  const drawerProbe = () => container.querySelector<HTMLElement>('[data-testid="drawer-probe"]');
  const consoleProbe = () => container.querySelector<HTMLElement>('[data-testid="console-probe"]');

  async function clickTryAgain(): Promise<void> {
    const [surface] = surfaces();
    const button = Array.from(surface?.querySelectorAll('button') ?? []).find((b) => b.textContent === 'Try again');
    expect(button, 'the render surface offers Try again').toBeDefined();
    await update(() => button?.click());
  }

  async function crashTheDrawerOnItsPayload(): Promise<void> {
    probes.drawerReadsPayload = true;
    await mount();
    await update(() => app.setDrawer(LINEAGE_SOURCE));
    await vi.waitFor(() => expect(surfaces()).toHaveLength(1), LAZY_PANEL_WAIT);
    expect(surfaces()[0].getAttribute('data-error-boundary')).toBe('drawer');
    expect(probes.drawerFetches).toBe(1);
  }

  describe('recovery from a payload crash (a staleTime: Infinity query that stays cached)', () => {
    it("the drawer's Try again re-reads the payload and renders the healthy drawer", async () => {
      await crashTheDrawerOnItsPayload();

      await clickTryAgain();
      await vi.waitFor(() => expect(drawerProbe()?.dataset.families).toBe('1'));
      expect(surfaces()).toHaveLength(0);
      expect(drawerProbe()?.classList.contains('is-open')).toBe(true);
      expect(probes.drawerFetches).toBe(2);
    });

    it("closing the crashed drawer's frame lets the next open re-read the payload", async () => {
      await crashTheDrawerOnItsPayload();

      const frame = container.querySelector('dialog.drawer.is-open');
      await update(() => frame?.querySelector<HTMLButtonElement>('button[aria-label="Close drawer"]')?.click());
      expect(app.drawer).toBeNull();
      expect(surfaces()).toHaveLength(0);
      // Removing the cached payload never fetches: nothing is read until the next open.
      expect(probes.drawerFetches).toBe(1);

      await update(() => app.setDrawer(LINEAGE_SOURCE));
      await vi.waitFor(() => expect(drawerProbe()?.dataset.families).toBe('1'));
      expect(surfaces()).toHaveLength(0);
      expect(drawerProbe()?.classList.contains('is-open')).toBe(true);
      expect(probes.drawerFetches).toBe(2);
    });

    /**
     * Wave-3 error-telemetry review: the panel Try again and the drawer
     * frame's Close used the route boundary's app-wide reset, which dropped
     * every unobserved cached query in the app (other routes' warm caches).
     * Now each panel drops only its own key prefixes.
     */
    it("drops only the panel's own queries: a bystander unobserved ['mip','leads'] cache survives", async () => {
      const bystander = ['mip', 'leads', 'bystander'];
      const assetKey = ['mip', 'asset', 'lead_population'];
      queryClient.setQueryData(bystander, { rows: 1 });
      queryClient.setQueryData(assetKey, { freshness: 'fresh' });
      await crashTheDrawerOnItsPayload();

      await clickTryAgain();
      await vi.waitFor(() => expect(drawerProbe()?.dataset.families).toBe('1'));
      expect(queryClient.getQueryData(bystander), 'drawer Try again keeps the bystander').toEqual({ rows: 1 });
      expect(queryClient.getQueryData(assetKey), "drawer Try again drops the drawer's asset metadata").toBeUndefined();

      // Crash again on a fresh payload read, then Close the frame.
      queryClient.setQueryData(assetKey, { freshness: 'fresh' });
      probes.drawerFetches = 0;
      queryClient.removeQueries({ queryKey: ['mip', 'lineage', 'manifest'] });
      await update(() => app.setDrawer(null));
      await update(() => app.setDrawer(LINEAGE_SOURCE));
      await vi.waitFor(() => expect(surfaces()).toHaveLength(1), LAZY_PANEL_WAIT);
      const frame = container.querySelector('dialog.drawer.is-open');
      await update(() => frame?.querySelector<HTMLButtonElement>('button[aria-label="Close drawer"]')?.click());
      expect(queryClient.getQueryData(bystander), 'drawer Close keeps the bystander').toEqual({ rows: 1 });
      expect(queryClient.getQueryData(assetKey)).toBeUndefined();

      // The Console: its Try again drops its recent activity, nothing else.
      probes.consoleReadsPayload = true;
      await update(() => app.setConsoleOpen(true));
      await vi.waitFor(() => expect(surfaces()).toHaveLength(1), LAZY_PANEL_WAIT);
      await clickTryAgain();
      await vi.waitFor(() => expect(consoleProbe()?.dataset.families).toBe('1'));
      expect(queryClient.getQueryData(bystander), 'Console Try again keeps the bystander').toEqual({ rows: 1 });
    });

    it("the Console's Try again re-reads the payload and renders the healthy Console", async () => {
      probes.consoleReadsPayload = true;
      await mount();
      await update(() => app.setConsoleOpen(true));
      await vi.waitFor(() => expect(surfaces()).toHaveLength(1), LAZY_PANEL_WAIT);
      expect(surfaces()[0].getAttribute('data-error-boundary')).toBe('console');
      expect(probes.consoleFetches).toBe(1);

      await clickTryAgain();
      await vi.waitFor(() => expect(consoleProbe()?.dataset.families).toBe('1'));
      expect(surfaces()).toHaveLength(0);
      expect(container.querySelectorAll('aside#workspace-console')).toHaveLength(1);
      expect(probes.consoleFetches).toBe(2);
    });
  });
});
