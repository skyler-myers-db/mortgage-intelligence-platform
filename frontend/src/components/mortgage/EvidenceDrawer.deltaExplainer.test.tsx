/**
 * @vitest-environment happy-dom
 */
/**
 * The evidence drawer's Delta Explainer slot (audit wow-ai-3): it mounts on
 * Overview only for a source that carries an explainer (a supported "since
 * your last login" delta), and only then is the audit-free attribution read
 * issued. Every other source, and the Lineage tab, never reads it. W5c folded
 * the explainer into DrawerSource: the reconcile line's live figure is the
 * source's own `value`, and the not-snapshotted note keys on the route's
 * `snapshotted: false` alone.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { EvidenceDrawer } from './EvidenceDrawer';
import { LazyEvidenceDrawerBody } from './evidenceDrawerBodyLoader';
import type { DrawerSource } from '../AppContext';

const appMocks = vi.hoisted(() => ({ drawer: null as DrawerSource | null, setDrawer: vi.fn() }));
const apiMocks = vi.hoisted(() => ({
  assetMetadata: vi.fn(),
  lineageManifest: vi.fn(),
  attribution: vi.fn(),
  freshness: vi.fn(),
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
vi.mock('../../lib/apiClients/assets', () => ({
  assetsApi: { assetFreshness: apiMocks.freshness },
}));

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const PLAIN: DrawerSource = {
  title: 'Since your last login — refi candidates',
  short: 'portfolio_headline_metric_view.refi_economics_screen',
  assetKey: 'portfolio_headline_metric_view',
  assetPath: 'mip.semantics.portfolio_headline_metric_view',
  description: 'Signed movement.',
};
const EXPLAINED: DrawerSource = {
  ...PLAIN,
  value: '+2,250',
  deltaExplainer: { measure: 'refi_economics_screen', baselineDate: '2026-09-01' },
};
const COMPETITOR_LIEN: DrawerSource = {
  ...PLAIN,
  title: 'Since your last login — competitor liens',
  value: '-31',
  deltaExplainer: { measure: 'competitor_lien', baselineDate: '2026-09-01' },
};

function notSnapshotted() {
  return {
    data: {
      measure: 'competitor_lien', label: 'competitor liens', population: 'addressable',
      requested_baseline_date: '2026-09-01', baseline_snapshot_date: null, current_snapshot_date: null,
      nearest_snapshot: false, baseline_total: null, current_total: null, total_change: null,
      states: [], unattributed_change: null,
      rate: { series_id: 'MORTGAGE30US', baseline_week: null, baseline_pct: null, latest_week: null, latest_pct: null },
      offer_rules_last_updated: null, offer_rules_changed_since_baseline: null, sources: [], note: 'n',
      snapshotted: false,
    },
    lastGoodAt: null,
  };
}

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
    apiMocks.freshness.mockResolvedValue({
      asset_key: 'portfolio_headline_metric_view', title: 'Portfolio Headline Metric View', freshness: 'fresh',
      last_updated: '2026-09-30 06:00:00', checked_at: null, status: 'live', basis: 'UC Gold Borrower 360',
      source: 'source_readiness',
    });
    apiMocks.attribution.mockResolvedValue({
      data: {
        measure: 'refi_economics_screen', label: 'refi candidates', population: 'addressable',
        requested_baseline_date: '2026-09-01', baseline_snapshot_date: '2026-09-01', current_snapshot_date: '2026-09-30',
        nearest_snapshot: false, baseline_total: 100, current_total: 120, total_change: 20,
        states: [{ state: 'TX', baseline_count: 40, current_count: 60, change: 20 }], unattributed_change: 0,
        rate: { series_id: 'MORTGAGE30US', baseline_week: null, baseline_pct: null, latest_week: null, latest_pct: null },
        offer_rules_last_updated: null, offer_rules_changed_since_baseline: null, sources: [], note: 'n',
        snapshotted: true,
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
    // The live figure is the clicked number, the source's own value.
    expect(document.querySelector('[data-testid="delta-explainer-reconcile"]')?.textContent).toContain('+2,250');
    expect(document.querySelector('[data-testid="evidence-how-we-got"]')?.textContent).toContain('How we got +2,250');
  });

  it('says a measure the snapshot does not attribute is not snapshotted, from the route alone', async () => {
    apiMocks.attribution.mockResolvedValue(notSnapshotted());
    await render(COMPETITOR_LIEN);
    expect(apiMocks.attribution.mock.calls[0].slice(0, 2)).toEqual(['competitor_lien', '2026-09-01']);
    expect(document.querySelector('[data-testid="delta-explainer-not-snapshotted"]')?.textContent).toBe(
      'Per-state attribution is not snapshotted for this measure yet.',
    );
    expect(document.body.textContent).not.toContain('No daily funnel snapshot covers this period yet');
  });

  it('keys the note on snapshotted, never the measure: a snapshotted competitor_lien shows the generic empty state', async () => {
    const empty = notSnapshotted();
    apiMocks.attribution.mockResolvedValue({ ...empty, data: { ...empty.data, snapshotted: true } });
    await render(COMPETITOR_LIEN);
    expect(document.querySelector('[data-testid="delta-explainer-not-snapshotted"]')).toBeNull();
    expect(document.body.textContent).toContain('No daily funnel snapshot covers this period yet');
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
