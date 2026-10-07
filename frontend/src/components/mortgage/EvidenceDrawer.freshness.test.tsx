/**
 * @vitest-environment happy-dom
 *
 * Freshness for every user (audit 2026-09-21 critic-03, D-audit-reads-c1).
 * The evidence drawer's freshness chip, its status chip and its one refresh
 * fact come from GET /api/assets/{key}/freshness for EVERY session, an
 * administrator's included; the admin metadata read never drives the chip.
 * A failed read says so and offers a Retry; it never renders as "Stale".
 */
// @ts-expect-error Frontend app types intentionally exclude Node globals; this
// source gate reads the tree under Vitest only.
import { readdirSync, readFileSync } from 'node:fs';
// @ts-expect-error see node:fs note above.
import { join } from 'node:path';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { EvidenceDrawer } from './EvidenceDrawer';
import { LazyEvidenceDrawerBody } from './evidenceDrawerBodyLoader';
import { DRAWER_SOURCES } from '../../lib/drawerSources';
import type { DrawerSource } from '../AppContext';
import type { AssetFreshnessResponse } from '../../lib/apiTypes';

declare const process: { cwd(): string };

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const appMocks = vi.hoisted(() => ({ drawer: null as DrawerSource | null, setDrawer: vi.fn(), canAccessAdmin: false }));
const apiMocks = vi.hoisted(() => ({ assetMetadata: vi.fn(), lineageManifest: vi.fn(), assetFreshness: vi.fn() }));

vi.mock('../AppContext', () => ({
  useApp: () => ({ drawer: appMocks.drawer, setDrawer: appMocks.setDrawer, canAccessAdmin: appMocks.canAccessAdmin }),
}));
vi.mock('../../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../lib/api')>()),
  api: { assetMetadata: apiMocks.assetMetadata, lineageManifest: apiMocks.lineageManifest },
}));
vi.mock('../../lib/apiClients/assets', () => ({ assetsApi: { assetFreshness: apiMocks.assetFreshness } }));

const RECENT = new Date(Date.now() - 2 * 3_600_000).toISOString();

function freshness(overrides: Partial<AssetFreshnessResponse> = {}): AssetFreshnessResponse {
  return {
    asset_key: 'portfolio_headline_metric_view',
    title: 'Portfolio Headline Metric View',
    freshness: 'fresh',
    last_updated: RECENT,
    checked_at: RECENT,
    status: 'live',
    basis: 'UC Gold Borrower 360',
    source: 'source_readiness',
    ...overrides,
  };
}

beforeAll(async () => {
  await LazyEvidenceDrawerBody.preload();
}, 60_000);

describe('EvidenceDrawer freshness for every user', () => {
  let root: Root;

  beforeEach(() => {
    document.body.innerHTML = '<div id="root"></div>';
    root = createRoot(document.getElementById('root') as HTMLElement);
    appMocks.canAccessAdmin = false;
    apiMocks.lineageManifest.mockResolvedValue({ schema_version: 1, manifest_path: 'm', families: [] });
    apiMocks.assetFreshness.mockResolvedValue(freshness());
    // A metadata payload that DISAGREES with the freshness read: the chip must not follow it.
    apiMocks.assetMetadata.mockResolvedValue({
      freshness: 'stale',
      status: 'error',
      object_type: 'view',
      observed_in_unity_catalog: true,
      observation_source: 'system.information_schema.tables',
      lineage: [],
      last_updated: '2020-01-01 00:00:00',
      catalog_explorer_url: null,
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
    for (let i = 0; i < 6; i += 1) {
      await act(async () => new Promise((resolve) => setTimeout(resolve, 0)));
    }
  }

  const chip = () => document.querySelector<HTMLElement>('.source-freshness');
  const foot = () => document.querySelector('[data-testid="evidence-freshness-foot"]')?.textContent ?? null;

  it('shows a non-admin the band, the status and one refresh fact with its basis, and never asks for admin metadata', async () => {
    await render(DRAWER_SOURCES.portfolioHeadlineView);

    expect(apiMocks.assetFreshness).toHaveBeenCalledTimes(1);
    expect(apiMocks.assetFreshness).toHaveBeenCalledWith('portfolio_headline_metric_view', expect.anything());
    expect(apiMocks.assetMetadata).not.toHaveBeenCalled();
    expect(chip()?.textContent).toBe('Fresh');
    expect(chip()?.className).toContain('source-freshness--fresh');
    expect([...document.querySelectorAll('.source-summary__top .chip')].map((node) => node.textContent)).toEqual([
      'live',
      'Unity Catalog',
    ]);
    expect(foot()).toBe('Last refresh 2 hours ago · via UC Gold Borrower 360');
    expect(document.querySelector('[data-testid="evidence-freshness-foot"] time')?.getAttribute('datetime')).toBe(RECENT);
    expect(document.body.textContent).toContain('Updated within 7 days.');
    expect(document.body.textContent).not.toContain('Business refresh');
  });

  it('drives an administrator\'s chip from the same read, never from the metadata payload', async () => {
    appMocks.canAccessAdmin = true;
    await render(DRAWER_SOURCES.portfolioHeadlineView);

    expect(apiMocks.assetMetadata).toHaveBeenCalledTimes(1);
    expect(apiMocks.assetFreshness).toHaveBeenCalledTimes(1);
    expect(chip()?.textContent).toBe('Fresh');
    expect(document.body.textContent).not.toContain('Stale');
    expect([...document.querySelectorAll('.source-summary__top .chip')].map((node) => node.textContent)).toEqual([
      'live',
      'Unity Catalog',
    ]);
    expect(foot()).toBe('Last refresh 2 hours ago · via UC Gold Borrower 360');
  });

  it('says a not-tracked asset is not tracked, with no refresh line', async () => {
    apiMocks.assetFreshness.mockResolvedValue(
      freshness({ freshness: 'unavailable', last_updated: null, checked_at: null, status: 'unknown', basis: null, source: 'not_tracked' }),
    );
    await render({ ...DRAWER_SOURCES.lien, assetKey: 'fn_estimated_upb', assetPath: 'mip.gold.fn_estimated_upb' });

    expect(chip()?.textContent).toBe('Freshness not tracked');
    expect(chip()?.className).toContain('source-freshness--not-tracked');
    expect(document.body.textContent).toContain('This asset has no source-readiness row; its freshness is not tracked.');
    expect(foot()).toBeNull();
  });

  it('says no readiness row is available yet for an unavailable answer', async () => {
    apiMocks.assetFreshness.mockResolvedValue(
      freshness({ freshness: 'unavailable', last_updated: null, checked_at: null, status: 'unknown', source: 'unavailable' }),
    );
    await render(DRAWER_SOURCES.leadScore);

    expect(chip()?.textContent).toBe('Freshness unavailable');
    expect(document.body.textContent).toContain('No source-readiness row is available for this asset yet.');
    expect(foot()).toBeNull();
  });

  it('says a failed read could not be read, never "Stale", and retries only on a click', async () => {
    apiMocks.assetFreshness.mockRejectedValue(new Error('503'));
    await render(DRAWER_SOURCES.leadScore);

    expect(chip()?.textContent).toBe('Freshness not loaded');
    expect(chip()?.className).toContain('source-freshness--error');
    expect(document.body.textContent).toContain(
      'Freshness could not be read just now. This does not mean the source is stale.',
    );
    expect(document.body.textContent).not.toContain('Stale');
    expect(document.body.textContent).not.toContain('Freshness unavailable');
    expect(apiMocks.assetFreshness).toHaveBeenCalledTimes(1);

    apiMocks.assetFreshness.mockResolvedValue(freshness({ asset_key: 'lead_scores', basis: 'UC Gold Lead Scores' }));
    const retry = [...document.querySelectorAll<HTMLButtonElement>('.source-summary button')].find(
      (button) => button.textContent === 'Retry',
    );
    await act(async () => retry?.click());
    for (let i = 0; i < 4; i += 1) await act(async () => new Promise((resolve) => setTimeout(resolve, 0)));
    expect(apiMocks.assetFreshness).toHaveBeenCalledTimes(2);
    expect(chip()?.textContent).toBe('Fresh');
    expect(foot()).toBe('Last refresh 2 hours ago · via UC Gold Lead Scores');
  });

  it('asks nothing for a Lakebase source, which carries no freshness chip', async () => {
    await render(DRAWER_SOURCES.callDispositions);

    expect(apiMocks.assetFreshness).not.toHaveBeenCalled();
    expect(chip()).toBeNull();
  });

  it('has no "Admin-only freshness" state anywhere in frontend/src', () => {
    const root = join(process.cwd(), 'src');
    const offenders = (readdirSync(root, { recursive: true }) as string[])
      .map((entry) => entry.split('\\').join('/'))
      .filter((entry) => /\.(ts|tsx)$/.test(entry) && !entry.endsWith('EvidenceDrawer.freshness.test.tsx'))
      .filter((entry) => (readFileSync(join(root, entry), 'utf8') as string).includes('Admin-only freshness'));
    expect(offenders).toEqual([]);
  });
});
