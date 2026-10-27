// @vitest-environment happy-dom
/**
 * Virtual screen-reader pass: the evidence drawer (a native modal dialog with
 * APG tabs; WCAG 1.3.1, 2.1.1, 2.4.3, 4.1.2; docs/accessibility/VPAT.md).
 * Harness as EvidenceDrawer.test.tsx: the governed metadata and freshness
 * reads are mocked with synthetic values.
 *
 * Not modelled (virtualReader.ts): showModal()'s inert page. The reader
 * announces the dialog on entry, but its cursor could still leave it; the
 * browser's top layer and focus trap are proven by
 * tests/e2e/fixture/evidence-drawer.fixture.spec.ts and the drawer's Vitest
 * focus suite.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import type { DrawerSource } from '../../components/AppContext';
import { mount, type Mounted } from '../render';
import { expectTranscript, nextUntil, registerReaderHooks, settle, startReader } from './virtualReader';

const appMocks = vi.hoisted(() => ({
  drawer: null as DrawerSource | null,
  setDrawer: vi.fn(),
}));
const apiMocks = vi.hoisted(() => ({
  assetMetadata: vi.fn(),
  lineageManifest: vi.fn(),
  assetFreshness: vi.fn(),
}));

vi.mock('../../components/AppContext', () => ({
  useApp: () => ({ drawer: appMocks.drawer, setDrawer: appMocks.setDrawer, canAccessAdmin: false }),
}));
vi.mock('../../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../lib/api')>()),
  api: { assetMetadata: apiMocks.assetMetadata, lineageManifest: apiMocks.lineageManifest },
}));
vi.mock('../../lib/apiClients/assets', () => ({
  assetsApi: { assetFreshness: apiMocks.assetFreshness },
}));

import { EvidenceDrawer } from '../../components/mortgage/EvidenceDrawer';
import { LazyEvidenceDrawerBody } from '../../components/mortgage/evidenceDrawerBodyLoader';

registerReaderHooks();

const SOURCE: DrawerSource = {
  title: 'Lead population',
  short: 'mip.gold.lead_population',
  description: 'Governed borrower candidates used by Module 0 lead ranking.',
  assetKey: 'lead_population',
  assetPath: 'mip.gold.lead_population',
  usedIn: ['Ranked borrower table'],
};

beforeAll(async () => {
  await LazyEvidenceDrawerBody.preload();
}, 60_000);

let queryClient: QueryClient;

beforeEach(() => {
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  appMocks.drawer = SOURCE;
  apiMocks.assetFreshness.mockResolvedValue({
    asset_key: 'lead_population',
    title: 'Gold Lead Queue Population',
    freshness: 'fresh',
    last_updated: null,
    checked_at: null,
    status: 'live',
    basis: 'UC Gold Lead Population',
    source: 'source_readiness',
  });
});

afterEach(() => {
  queryClient.clear();
  vi.clearAllMocks();
});

async function render(into?: Mounted): Promise<Mounted> {
  const ui = (
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <EvidenceDrawer />
      </MemoryRouter>
    </QueryClientProvider>
  );
  if (into) await into.rerender(ui);
  const mounted = into ?? await mount(ui);
  await settle(6);
  return mounted;
}

describe('virtual screen reader: evidence drawer', () => {
  it('announces the dialog, lands on Close, reads the evidence and its tabs, and hands focus back', async () => {
    const launcher = document.body.appendChild(document.createElement('button'));
    launcher.textContent = 'Lead population evidence';
    launcher.focus();
    appMocks.drawer = null;
    const mounted = await render();
    const reader = await startReader();

    // The drawer opens from AppContext's drawer source (mocked here); a plain
    // button stands in for the evidence chip that would set it, so focus has
    // an opener to return to. Opening from a real chip and focus returning to
    // it: tests/e2e/fixture/overlays.fixture.spec.ts.
    const open = await reader.step('Open the drawer (source set, a stand-in opener button focused)', async () => {
      appMocks.drawer = SOURCE;
      await render(mounted);
    });
    expect(document.querySelector<HTMLDialogElement>('dialog.drawer')?.open).toBe(true);
    expect(document.activeElement?.classList.contains('drawer__close')).toBe(true);
    expect(open[0]).toMatch(/^dialog, /);
    expect(open.at(-1)).toMatch(/^button, Close/);

    const body = await reader.step('Read the drawer with the virtual cursor', async () => {
      await nextUntil(/^end of dialog/);
    });
    expect(body.some((phrase) => /^tablist/.test(phrase))).toBe(true);
    expect(body.some((phrase) => /^tab, .*, selected/.test(phrase))).toBe(true);
    expect(body).toContain('Governed borrower candidates used by Module 0 lead ranking.');

    const tabs = await reader.step('ArrowRight on the selected tab moves to the next tab', async () => {
      const selected = document.querySelector<HTMLButtonElement>('[role="tab"][aria-selected="true"]')!;
      await act(async () => selected.focus());
      await settle();
      await act(async () => {
        selected.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true, cancelable: true }));
      });
      await settle(6);
    });
    const now = document.activeElement as HTMLElement;
    expect(now.getAttribute('role')).toBe('tab');
    expect(now.getAttribute('aria-selected')).toBe('true');
    expect(tabs.at(-1)).toMatch(/^tab, .*, selected/);

    const close = await reader.step('Escape closes the drawer; focus returns to the stand-in opener', async () => {
      await act(async () => {
        window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
      });
      expect(appMocks.setDrawer).toHaveBeenCalledWith(null);
      appMocks.drawer = null;
      await render(mounted);
    });
    expect(document.activeElement).toBe(launcher);
    expect(close.at(-1)).toBe('button, Lead population evidence');

    await expectTranscript('evidence-drawer', reader);
    launcher.remove();
  }, 30_000);
});
