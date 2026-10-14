/**
 * @vitest-environment happy-dom
 *
 * "How we got {value}" and "Under the hood" (audit 2026-09-21 flow-06,
 * flow-10). A KPI's drawer leads with the number the user clicked, its
 * definition, its filter chips and its as-of; the server-emitted reproduce
 * SQL, the listing note, the sanitized signals and the administrator's
 * catalog detail sit under the hood. One KPI proof GET per KPI, shared by
 * both tabs, and only while the drawer is open.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { EvidenceDrawer } from './EvidenceDrawer';
import { LazyEvidenceDrawerBody } from './evidenceDrawerBodyLoader';
import { DRAWER_SOURCES } from '../../lib/drawerSources';
import type { DrawerSource } from '../AppContext';
import type { KpiProofResponse } from '../../lib/apiTypes';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const appMocks = vi.hoisted(() => ({ drawer: null as DrawerSource | null, setDrawer: vi.fn(), canAccessAdmin: false }));
const apiMocks = vi.hoisted(() => ({
  assetMetadata: vi.fn(),
  lineageManifest: vi.fn(),
  assetFreshness: vi.fn(),
  kpiProof: vi.fn(),
}));

vi.mock('../AppContext', () => ({
  useApp: () => ({ drawer: appMocks.drawer, setDrawer: appMocks.setDrawer, canAccessAdmin: appMocks.canAccessAdmin }),
}));
vi.mock('../../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../lib/api')>()),
  api: { assetMetadata: apiMocks.assetMetadata, lineageManifest: apiMocks.lineageManifest },
}));
vi.mock('../../lib/apiClients/assets', () => ({ assetsApi: { assetFreshness: apiMocks.assetFreshness } }));
vi.mock('../../lib/apiClients/kpiProof', () => ({ kpiProofApi: { kpiProof: apiMocks.kpiProof } }));

const PROOF: KpiProofResponse = {
  kpi: 'home.in_the_money',
  measure_column: 'high_intent_leads',
  predicates: ['in_the_money = TRUE'],
  sql: 'WITH preview_population AS (SELECT headline.borrower_id FROM mip.semantics.portfolio_headline_metric_view AS headline) SELECT COUNT(*) AS marketable_population FROM preview_population',
  sql_hash: '0123456789abcdef',
  params: [],
  relations: ['mip.gold.borrower_360', 'mip.semantics.portfolio_headline_metric_view'],
  note: 'Column high_intent_leads of this statement is the number on the card.',
  databricks_sql_url: 'https://dbc-unit.cloud.databricks.com/sql/editor',
};

const ITM_KPI: DrawerSource = {
  ...DRAWER_SOURCES.itm,
  value: '12,840',
  asOf: '2026-07-14T08:00:00Z',
  proofKey: 'home.in_the_money',
};

beforeAll(async () => {
  await LazyEvidenceDrawerBody.preload();
}, 60_000);

describe('EvidenceDrawer How we got and Under the hood', () => {
  let root: Root;
  let writeText: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    document.body.innerHTML = '<div id="root"></div>';
    root = createRoot(document.getElementById('root') as HTMLElement);
    appMocks.canAccessAdmin = false;
    apiMocks.lineageManifest.mockResolvedValue({ schema_version: 1, manifest_path: 'm', families: [] });
    apiMocks.assetFreshness.mockResolvedValue({
      asset_key: 'borrower_360', title: 'Gold Borrower 360', freshness: 'fresh', last_updated: null, checked_at: null,
      status: 'live', basis: 'UC Gold Borrower 360', source: 'source_readiness',
    });
    apiMocks.assetMetadata.mockResolvedValue({
      freshness: 'fresh', status: 'live', object_type: 'table', observed_in_unity_catalog: true,
      observation_source: 'system.information_schema.tables', lineage: [], row_count: 12, num_files: 1,
      size_label: '1 MB', delta_last_modified: '2026-07-14T08:00:00Z', catalog_explorer_url: null,
    });
    apiMocks.kpiProof.mockResolvedValue(PROOF);
    writeText = vi.fn(async () => undefined);
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } });
  });

  afterEach(() => {
    act(() => root.unmount());
    document.body.innerHTML = '';
    vi.clearAllMocks();
  });

  async function render(source: DrawerSource, client = new QueryClient({ defaultOptions: { queries: { retry: false } } })) {
    appMocks.drawer = source;
    await act(async () => {
      root.render(
        <QueryClientProvider client={client}>
          <MemoryRouter>
            <EvidenceDrawer />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
    for (let i = 0; i < 6; i += 1) await act(async () => new Promise((resolve) => setTimeout(resolve, 0)));
    return client;
  }

  async function openTab(id: 'overview' | 'lineage' | 'under-the-hood') {
    await act(async () => (document.getElementById(`drawer-tab-${id}`) as HTMLButtonElement).click());
    for (let i = 0; i < 4; i += 1) await act(async () => new Promise((resolve) => setTimeout(resolve, 0)));
  }

  const howWeGot = () => document.querySelector<HTMLElement>('[data-testid="evidence-how-we-got"]');

  it('leads Overview with the number, its definition, the server\'s filter chips and the as-of', async () => {
    await render(ITM_KPI);

    const block = howWeGot();
    expect(block).not.toBeNull();
    const heading = block?.querySelector('h3');
    expect(heading?.textContent).toBe('How we got 12,840');
    expect(heading?.className).toBe('h-4 evidence-proof__title');
    expect(block?.textContent).toContain('Borrowers who clear the refinance economics screen');
    expect([...(block?.querySelectorAll('.chip') ?? [])].map((chip) => chip.textContent)).toEqual(['in_the_money = TRUE']);
    expect(block?.querySelector('time')?.getAttribute('datetime')).toBe('2026-07-14T08:00:00.000Z');
    expect(block?.textContent).toMatch(/^How we got 12,840.*Data as of Jul 14/);
    // How we got comes first on Overview, before the source summary.
    const panel = document.getElementById('drawer-panel-overview');
    expect(panel?.firstElementChild).toBe(block);
    expect(apiMocks.kpiProof).toHaveBeenCalledTimes(1);
    expect(apiMocks.kpiProof).toHaveBeenCalledWith('home.in_the_money', expect.anything());
  });

  it('renders no How-we-got block and asks no proof for a source without a value', async () => {
    await render(DRAWER_SOURCES.itm);

    expect(howWeGot()).toBeNull();
    expect(apiMocks.kpiProof).not.toHaveBeenCalled();
  });

  it('uses the source\'s own predicates first, and names the whole book for an empty list', async () => {
    await render({ ...DRAWER_SOURCES.population, value: '89,553', predicates: [] });

    expect([...(howWeGot()?.querySelectorAll('.chip') ?? [])].map((chip) => chip.textContent)).toEqual([
      'Whole refreshed book (no filter)',
    ]);
    expect(howWeGot()?.textContent).toContain('Every borrower in the refreshed book');
    expect(apiMocks.kpiProof).not.toHaveBeenCalled();
  });

  it('says the filters are loading, then that they did not load, never inventing a chip', async () => {
    let reject: (error: Error) => void = () => undefined;
    apiMocks.kpiProof.mockReturnValue(new Promise((_resolve, rejectProof) => { reject = rejectProof; }));
    await render(ITM_KPI);
    expect(howWeGot()?.textContent).toContain('Loading filters…');
    expect(howWeGot()?.querySelectorAll('.chip')).toHaveLength(0);

    await act(async () => reject(new Error('503')));
    for (let i = 0; i < 4; i += 1) await act(async () => new Promise((resolve) => setTimeout(resolve, 0)));
    expect(howWeGot()?.textContent).toContain('Filters not loaded');
    expect(howWeGot()?.querySelectorAll('.chip')).toHaveLength(0);
  });

  it('puts the reproduce card, the listing note, the signals and the admin detail under the hood, in that order', async () => {
    appMocks.canAccessAdmin = true;
    await render(ITM_KPI);
    await openTab('under-the-hood');

    const panel = document.getElementById('drawer-panel-under-the-hood') as HTMLElement;
    // A focusable tabpanel: it may hold no control, and the drawer body scrolls it.
    expect(panel.getAttribute('role')).toBe('tabpanel');
    expect(panel.tabIndex).toBe(0);
    const text = panel.textContent ?? '';
    const order = [
      'Copy this statement into a Databricks SQL workspace',
      'How these assets are listed',
      'Each governed object behind this source is listed once on Overview.',
      'Sanitized signals',
      'Workspace verification',
      'Modified',
    ].map((needle) => text.indexOf(needle));
    expect(order.every((index) => index >= 0)).toBe(true);
    expect([...order].sort((a, b) => a - b)).toEqual(order);
    expect(panel.querySelector('.proof-sql-card__title')?.textContent).toBe('Refinance economics screen');
    expect(panel.querySelector('.proof-sql-card__hash')?.textContent).toBe('0123456789abcdef');
    expect(panel.querySelector('pre.proof-sql-card__sql')?.textContent).toBe(PROOF.sql);
    expect([...panel.querySelectorAll('.chip')].map((chip) => chip.textContent)).toEqual(PROOF.relations);
    expect(panel.querySelector<HTMLAnchorElement>('a[href$="/sql/editor"]')?.textContent).toBe('Open SQL editor');
    expect(text).not.toContain('Compact semantics');
    // One GET per KPI, shared by How we got and Under the hood.
    expect(apiMocks.kpiProof).toHaveBeenCalledTimes(1);
  });

  it('copies the exact statement and says so', async () => {
    await render(ITM_KPI);
    await openTab('under-the-hood');
    const copy = [...document.querySelectorAll<HTMLButtonElement>('#drawer-panel-under-the-hood button')].find(
      (button) => button.textContent?.includes('Copy SQL'),
    );
    await act(async () => copy?.click());
    expect(writeText).toHaveBeenCalledWith(PROOF.sql);
    expect(copy?.textContent).toContain('Copied');
  });

  it('keeps the admin catalog detail off Overview, and shows a non-admin no admin detail at all', async () => {
    await render(ITM_KPI);
    expect(document.getElementById('drawer-panel-overview')?.textContent).not.toContain('Workspace verification');
    await openTab('under-the-hood');
    expect(document.getElementById('drawer-panel-under-the-hood')?.textContent).not.toContain('Workspace verification');
    expect(apiMocks.assetMetadata).not.toHaveBeenCalled();
  });

  it('asks nothing while the retained drawer is closing', async () => {
    const client = await render(ITM_KPI);
    expect(apiMocks.kpiProof).toHaveBeenCalledTimes(1);
    // Non-vacuity for the freshness gate: the open drawer read it once.
    expect(apiMocks.assetFreshness).toHaveBeenCalledTimes(1);
    client.clear();
    appMocks.drawer = null;
    await render(null as unknown as DrawerSource, client);
    expect(apiMocks.kpiProof).toHaveBeenCalledTimes(1);
    // The closing drawer re-reads neither the proof nor the freshness
    // (EvidenceFreshness is enabled only while `open`).
    expect(apiMocks.assetFreshness).toHaveBeenCalledTimes(1);
  });

  it('starts every open on Overview and marks the dialog for field attribution', async () => {
    await render(ITM_KPI);
    await openTab('under-the-hood');
    await render({ ...ITM_KPI });
    expect(document.getElementById('drawer-tab-overview')?.getAttribute('aria-selected')).toBe('true');
    expect(document.querySelector('dialog.drawer')?.getAttribute('data-rum-target')).toBe('drawer');
  });
});
