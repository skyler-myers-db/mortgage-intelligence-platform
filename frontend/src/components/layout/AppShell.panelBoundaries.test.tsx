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
 */

const probes = vi.hoisted(() => ({
  consoleThrows: false,
  drawerThrows: false,
  genieMounts: 0,
  genieUnmounts: 0,
}));

vi.mock('./Console', () => ({
  Console: function ConsoleProbe(): ReactNode {
    if (probes.consoleThrows) throw new RangeError('Console crashed on borrower B-0TESTBORROWER');
    return (
      <aside id="workspace-console" className="tweaks is-open" role="complementary" aria-label="Workspace console">
        <div data-testid="console-probe" />
      </aside>
    );
  },
}));

vi.mock('../mortgage/EvidenceDrawer', async () => {
  const { useApp: useAppContext } = await import('../AppContext');
  return {
    EvidenceDrawer: function EvidenceDrawerProbe(): ReactNode {
      const { drawer } = useAppContext();
      if (drawer && probes.drawerThrows) throw new TypeError("Cannot read properties of null (reading 'find')");
      return <aside className={`drawer ${drawer ? 'is-open' : ''}`} data-testid="drawer-probe" />;
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

describe('AppShell panel boundaries', () => {
  let container: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;

  beforeEach(() => {
    probes.consoleThrows = false;
    probes.drawerThrows = false;
    probes.genieMounts = 0;
    probes.genieUnmounts = 0;
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

    await vi.waitFor(() => expect(surfaces()).toHaveLength(1));
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

    await vi.waitFor(() => expect(surfaces()).toHaveLength(1));
    const frame = container.querySelector('aside.drawer.is-open[role="dialog"][aria-modal="true"]');
    expect(frame?.querySelector('.drawer__body')?.contains(surfaces()[0])).toBe(true);
    expect(container.querySelector('.drawer-scrim.is-open')).not.toBeNull();
    expect(container.querySelector('[data-testid="drawer-probe"]')).toBeNull();

    await update(() => frame?.querySelector<HTMLButtonElement>('button[aria-label="Close drawer"]')?.click());
    expect(app.drawer).toBeNull();
    expect(surfaces()).toHaveLength(0);
    expect(container.querySelector('[data-testid="drawer-probe"]')).not.toBeNull();

    probes.drawerThrows = false;
    await update(() => app.setDrawer({ title: 'Lineage manifest', lineageFamily: 'lead_scoring' }));
    expect(surfaces()).toHaveLength(0);
    expect(container.querySelector('[data-testid="drawer-probe"]')?.classList.contains('is-open')).toBe(true);
  });
});
