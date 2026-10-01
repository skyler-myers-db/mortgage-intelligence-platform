/**
 * @vitest-environment happy-dom
 */
/**
 * The evidence drawer's Delta Explainer slot (audit wow-ai-3): it mounts on
 * Overview only for a source that carries an explainer (a supported "since
 * your last login" delta), and only then is the audit-free attribution read
 * issued. Every other source, and the Lineage tab, never reads it.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { EvidenceDrawer } from './EvidenceDrawer';
import { LazyEvidenceDrawerBody } from './evidenceDrawerBodyLoader';
import type { DrawerSource } from '../AppContext';
import type { DeltaExplainerDrawerSource } from '../../lib/deltaExplainerSource';

const appMocks = vi.hoisted(() => ({ drawer: null as DrawerSource | null, setDrawer: vi.fn() }));
const apiMocks = vi.hoisted(() => ({
  assetMetadata: vi.fn(),
  lineageManifest: vi.fn(),
  attribution: vi.fn(),
}));

vi.mock('../AppContext', () => ({
  useApp: () => ({ drawer: appMocks.drawer, setDrawer: appMocks.setDrawer, canAccessAdmin: false }),
}));
vi.mock('../../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../lib/api')>()),
  api: { assetMetadata: apiMocks.assetMetadata, lineageManifest: apiMocks.lineageManifest },
}));
vi.mock('../../lib/apiClients/homeAttribution', () => ({
  homeAttributionApi: { homeSummaryAttribution: apiMocks.attribution },
}));

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const PLAIN: DrawerSource = {
  title: 'Since your last login — refi candidates',
  short: 'portfolio_headline_metric_view.refi_economics_screen',
  assetKey: 'portfolio_headline_metric_view',
  assetPath: 'mip.semantics.portfolio_headline_metric_view',
  description: 'Signed movement.',
};
const EXPLAINED: DeltaExplainerDrawerSource = {
  ...PLAIN,
  deltaExplainer: { measure: 'refi_economics_screen', baselineDate: '2026-09-01', liveDisplay: '+2,250' },
};

beforeAll(async () => {
  await LazyEvidenceDrawerBody.preload();
  await import('./DeltaExplainer');
}, 60_000);

describe('EvidenceDrawer Delta Explainer slot', () => {
  let root: Root;

  beforeEach(() => {
    document.body.innerHTML = '<div id="root"></div>';
    root = createRoot(document.getElementById('root') as HTMLElement);
    apiMocks.lineageManifest.mockResolvedValue({ families: [] });
    apiMocks.attribution.mockResolvedValue({
      data: {
        measure: 'refi_economics_screen', label: 'refi candidates', population: 'addressable',
        requested_baseline_date: '2026-09-01', baseline_snapshot_date: '2026-09-01', current_snapshot_date: '2026-09-30',
        nearest_snapshot: false, baseline_total: 100, current_total: 120, total_change: 20,
        states: [{ state: 'TX', baseline_count: 40, current_count: 60, change: 20 }], unattributed_change: 0,
        rate: { series_id: 'MORTGAGE30US', baseline_week: null, baseline_pct: null, latest_week: null, latest_pct: null },
        offer_rules_last_updated: null, offer_rules_changed_since_baseline: null, sources: [], note: 'n',
      },
      lastGoodAt: null,
    });
  });

  afterEach(() => {
    act(() => root.unmount());
    document.body.innerHTML = '';
    vi.clearAllMocks();
  });

  async function render(source: DrawerSource): Promise<void> {
    appMocks.drawer = source;
    await act(async () => {
      root.render(
        <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
          <MemoryRouter>
            <EvidenceDrawer />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
    for (let i = 0; i < 40; i += 1) {
      await act(async () => new Promise((resolve) => setTimeout(resolve, 5)));
    }
  }

  it('mounts the explainer on Overview for a supported delta, and reads it once', async () => {
    await render(EXPLAINED);
    expect(document.querySelector('[data-testid="delta-explainer"]')).not.toBeNull();
    expect(apiMocks.attribution).toHaveBeenCalledTimes(1);
    expect(apiMocks.attribution.mock.calls[0].slice(0, 2)).toEqual(['refi_economics_screen', '2026-09-01']);
    expect(document.querySelector('[data-testid="delta-explainer-total"]')?.textContent).toBe('+20');
  });

  it('mounts nothing and reads nothing for a source without an explainer', async () => {
    await render(PLAIN);
    expect(document.querySelector('.drawer__body')).not.toBeNull();
    expect(document.querySelector('[data-testid="delta-explainer"]')).toBeNull();
    expect(apiMocks.attribution).not.toHaveBeenCalled();
  });

  it('is not on the Lineage tab', async () => {
    await render(EXPLAINED);
    const lineage = [...document.querySelectorAll<HTMLButtonElement>('.drawer__tab')].find((tab) => tab.textContent?.includes('Lineage'));
    await act(async () => lineage?.click());
    expect(document.querySelector('[data-testid="delta-explainer"]')).toBeNull();
  });
});
