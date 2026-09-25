/**
 * @vitest-environment happy-dom
 *
 * The evidence drawer's frame / body split (audit 2026-09-21 bundle-04):
 * the frame is in the initial closure, the panels are a lazy chunk. While
 * the chunk is still on its way, a chip click opens the drawer with its
 * title, subtitle, tablist and a loading status; nothing is read until the
 * body (which owns both governed reads) has mounted. The rendered-layer
 * proof (a held and a 404 chunk on the real build) is in
 * tests/e2e/fixture/overlays.fixture.spec.ts.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { DrawerSource } from '../AppContext';

const appMocks = vi.hoisted(() => ({
  drawer: null as DrawerSource | null,
  setDrawer: vi.fn(),
}));
const apiMocks = vi.hoisted(() => ({ assetMetadata: vi.fn(), lineageManifest: vi.fn() }));

vi.mock('../AppContext', () => ({
  useApp: () => ({ drawer: appMocks.drawer, setDrawer: appMocks.setDrawer, canAccessAdmin: true }),
}));
vi.mock('../../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../lib/api')>()),
  api: apiMocks,
}));
// The body chunk is "held": its import never settles during this file.
vi.mock('./EvidenceDrawerBody', () => new Promise(() => undefined));

import { EvidenceDrawer } from './EvidenceDrawer';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const SOURCE: DrawerSource = {
  title: 'Lead population',
  short: 'mip.gold.lead_population',
  description: 'Governed borrower candidates used by Module 0 lead ranking.',
  assetKey: 'lead_population',
  lineageFamily: 'lead_scoring',
  eventDate: '2026-04-20T06:12:00Z',
};

describe('EvidenceDrawer frame before its body chunk arrives', () => {
  let root: Root;

  beforeEach(() => {
    document.body.innerHTML = '<div id="root"></div>';
    root = createRoot(document.getElementById('root') as HTMLElement);
    appMocks.drawer = null;
  });

  afterEach(() => {
    act(() => root.unmount());
    document.body.innerHTML = '';
    vi.clearAllMocks();
  });

  async function render(): Promise<void> {
    await act(async () => {
      root.render(
        <QueryClientProvider client={new QueryClient()}>
          <MemoryRouter>
            <EvidenceDrawer />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
  }

  it('renders the closed frame with no body at all, so the chunk is not requested by a render', async () => {
    await render();
    const dialog = document.querySelector<HTMLDialogElement>('dialog.drawer')!;
    expect(dialog.open).toBe(false);
    expect(dialog.querySelector('.drawer__body')?.textContent).toContain('Tap any evidence chip');
    expect(dialog.querySelector('[role="status"]')).toBeNull();
  });

  it('opens with the title, the tablist and a loading status, and reads nothing yet', async () => {
    appMocks.drawer = SOURCE;
    await render();
    const dialog = document.querySelector<HTMLDialogElement>('dialog.drawer')!;
    expect(dialog.open).toBe(true);
    expect(dialog.querySelector('#evidence-drawer-title')?.textContent).toBe('Lead population');
    expect(dialog.querySelector('.drawer__subtitle')?.textContent).toBe('mip.gold.lead_population');
    expect(dialog.querySelectorAll('[role="tab"]')).toHaveLength(2);
    const status = dialog.querySelector('.drawer__body [role="status"]');
    expect(status?.textContent).toBe('Loading evidence…');
    expect(apiMocks.assetMetadata).not.toHaveBeenCalled();
    expect(apiMocks.lineageManifest).not.toHaveBeenCalled();
    // Focus is already inside the drawer, on Close.
    await act(async () => {
      await Promise.resolve();
    });
    expect(document.activeElement?.getAttribute('aria-label')).toBe('Close drawer');
  });
});
