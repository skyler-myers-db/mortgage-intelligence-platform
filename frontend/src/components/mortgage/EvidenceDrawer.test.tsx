/**
 * @vitest-environment happy-dom
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { EvidenceDrawer } from './EvidenceDrawer';
import type { DrawerSource } from '../AppContext';

const appMocks = vi.hoisted(() => ({
  drawer: null as DrawerSource | null,
  setDrawer: vi.fn(),
}));

const apiMocks = vi.hoisted(() => ({
  assetMetadata: vi.fn(),
}));

vi.mock('../AppContext', () => ({
  useApp: () => ({
    drawer: appMocks.drawer,
    setDrawer: appMocks.setDrawer,
  }),
}));

vi.mock('../../lib/api', () => ({
  api: {
    assetMetadata: apiMocks.assetMetadata,
  },
}));

const SOURCE: DrawerSource = {
  title: 'Lead population',
  short: 'mip.gold.lead_population',
  description: 'Governed borrower candidates used by Module 0 lead ranking.',
  assetKey: 'lead_population',
  assetPath: 'mip.gold.lead_population',
  usedIn: ['Ranked borrower table'],
  lineage: [
    { layer: 'Gold', name: 'mip.gold.lead_population' },
    { layer: 'Metric view', name: 'mip.semantics.lead_generation_metric_view' },
  ],
};

async function settle(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
    await new Promise((resolve) => window.setTimeout(resolve, 0));
  });
}

describe('EvidenceDrawer accessibility', () => {
  let root: Root;
  let queryClient: QueryClient;

  beforeEach(() => {
    document.body.innerHTML = '<button id="launcher">Open evidence</button><div id="root"></div><button id="outside">Outside</button>';
    root = createRoot(document.getElementById('root') as HTMLElement);
    queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: Infinity } },
    });
    appMocks.drawer = SOURCE;
    appMocks.setDrawer.mockClear();
    apiMocks.assetMetadata.mockResolvedValue({
      row_count: 1200,
      num_files: 3,
      size_label: '24 MB',
      delta_last_modified: '2026-05-20T10:00:00Z',
      freshness: 'fresh',
      status: 'ready',
      object_type: 'table',
      observed_in_unity_catalog: true,
      observation_source: 'system.information_schema.tables',
      lineage: [],
      last_updated: '2026-05-20',
      catalog_explorer_url: null,
    });
  });

  afterEach(() => {
    act(() => root.unmount());
    queryClient.clear();
    document.body.innerHTML = '';
    vi.clearAllMocks();
  });

  async function render(): Promise<void> {
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

  it('lands focus in the drawer, traps Tab, closes on Escape, restores focus, and dismisses from the backdrop', async () => {
    const launcher = document.getElementById('launcher') as HTMLButtonElement;
    launcher.focus();

    await render();

    const dialog = document.querySelector('.drawer');
    expect(dialog?.getAttribute('role')).toBe('dialog');
    expect(dialog?.getAttribute('aria-modal')).toBe('true');

    const close = document.querySelector<HTMLButtonElement>('.drawer__close');
    const assetLink = Array.from(document.querySelectorAll<HTMLAnchorElement>('a')).find((link) =>
      link.textContent?.includes('View asset details'),
    );
    expect(document.activeElement).toBe(close);
    expect(assetLink).toBeTruthy();
    expect(document.body.textContent).toContain('Observed as a table in this workspace.');
    expect(document.body.textContent).toContain(
      'Verified through system.information_schema.tables.',
    );

    assetLink?.focus();
    await act(async () => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab' }));
    });
    expect(document.activeElement).toBe(close);

    close?.focus();
    await act(async () => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', shiftKey: true }));
    });
    expect(document.activeElement).toBe(assetLink);

    const outside = document.getElementById('outside') as HTMLButtonElement;
    outside.focus();
    await act(async () => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab' }));
    });
    expect(document.activeElement).toBe(close);

    await act(async () => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    });
    expect(appMocks.setDrawer).toHaveBeenCalledWith(null);

    appMocks.drawer = null;
    await render();
    expect(document.activeElement).toBe(launcher);

    appMocks.drawer = SOURCE;
    await render();
    await act(async () => {
      document.querySelector<HTMLElement>('.drawer-scrim')?.click();
    });
    expect(appMocks.setDrawer).toHaveBeenCalledWith(null);
  }, 15_000);
});
