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
import type { LineageManifestResponse } from '../../types';

const appMocks = vi.hoisted(() => ({
  drawer: null as DrawerSource | null,
  setDrawer: vi.fn(),
}));

const apiMocks = vi.hoisted(() => ({
  assetMetadata: vi.fn(),
  lineageManifest: vi.fn(),
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
    lineageManifest: apiMocks.lineageManifest,
  },
}));

const WORKSPACE = 'https://dbc-vitest.cloud.databricks.com';

/** Shape mirrors GET /api/lineage/manifest (backend/schemas/lineage.py). */
const MANIFEST: LineageManifestResponse = {
  schema_version: 1,
  manifest_path: 'backend/resources/lineage_manifest.json',
  families: [
    {
      id: 'in_the_money',
      title: 'Refi economics screen',
      description: 'Rate spread + equity screen traced end to end.',
      nodes: [
        {
          id: 'raw_voluntary_lien',
          layer: 'raw_share',
          object_type: 'table',
          fqn: 'cotality_mortgage_data.corelogic.entrada_eval_voluntary_lien_status_marketing_v2',
          label: 'Cotality Voluntary Lien share',
          note: 'Borrower lien rate and balance inputs.',
          catalog_explorer_url: `${WORKSPACE}/explore/data/cotality_mortgage_data/corelogic/entrada_eval_voluntary_lien_status_marketing_v2`,
        },
        {
          id: 'fn_in_the_money',
          layer: 'uc_function',
          object_type: 'function',
          fqn: 'mip.gold.fn_in_the_money',
          label: 'fn_in_the_money',
          note: 'Canonical refi-economics screen.',
          catalog_explorer_url: `${WORKSPACE}/explore/data/functions/mip/gold/fn_in_the_money`,
        },
        {
          id: 'mv_portfolio_headline',
          layer: 'metric_view',
          object_type: 'view',
          fqn: 'mip.semantics.portfolio_headline_metric_view',
          label: 'Portfolio headline metric view',
          note: null,
          catalog_explorer_url: null,
        },
      ],
    },
  ],
};

const MAPPED_SOURCE: DrawerSource = {
  title: 'Refinance economics screen',
  short: 'Rate + equity screen',
  description: 'Refi screen evidence.',
  assetKey: 'borrower_360',
  assetPath: 'mip.gold.borrower_360',
  lineageFamily: 'in_the_money',
  signals: [
    {
      label: 'Lien rate input',
      source:
        'cotality_mortgage_data.corelogic.entrada_eval_voluntary_lien_status_marketing_v2.current_rate',
      value: 'borrower row',
    },
    {
      label: 'Economics screen',
      source: 'mip.gold.fn_in_the_money',
      value: 'reviewed function',
    },
  ],
};

const UNMAPPED_SOURCE: DrawerSource = {
  title: 'Unknown evidence source',
  short: 'unknown',
  description: 'Not in the reviewed destination registry.',
};

const LAKEBASE_SOURCE: DrawerSource = {
  title: 'Campaign assumptions',
  short: 'config',
  description: 'Per-lender campaign config.',
};

async function settle(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
    await new Promise((resolve) => window.setTimeout(resolve, 0));
  });
}

describe('EvidenceDrawer lineage tab', () => {
  let root: Root;
  let queryClient: QueryClient;

  beforeEach(() => {
    document.body.innerHTML = '<div id="root"></div>';
    root = createRoot(document.getElementById('root') as HTMLElement);
    queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: Infinity } },
    });
    appMocks.setDrawer.mockClear();
    apiMocks.assetMetadata.mockResolvedValue({
      row_count: null,
      num_files: null,
      size_label: null,
      delta_last_modified: null,
      freshness: 'fresh',
      status: 'ready',
      lineage: [
        {
          direction: 'upstream',
          asset_path: 'mip.gold.borrower_360',
          label: 'Gold Borrower 360',
          object_type: 'table',
          event_time: '2026-07-10 12:00:00',
          event_count: 2,
          source: 'system.access.table_lineage',
          catalog_explorer_url: `${WORKSPACE}/explore/data/mip/gold/borrower_360`,
        },
      ],
      known_data_gaps: [],
      last_updated: null,
      catalog_explorer_url: null,
    });
    apiMocks.lineageManifest.mockResolvedValue(MANIFEST);
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

  async function openLineageTab(): Promise<void> {
    const tab = document.getElementById('drawer-tab-lineage') as HTMLButtonElement;
    expect(tab).toBeTruthy();
    await act(async () => {
      tab.click();
    });
    await settle();
  }

  it('renders manifest nodes as chips with Catalog Explorer deep links', async () => {
    appMocks.drawer = MAPPED_SOURCE;
    await render();

    // Overview uses the governed manifest immediately and presents its
    // compact asset list rather than DrawerSource.lineage.
    const overviewTab = document.getElementById('drawer-tab-overview');
    expect(overviewTab?.getAttribute('aria-selected')).toBe('true');
    expect(apiMocks.lineageManifest).toHaveBeenCalledTimes(1);
    expect(document.body.textContent).toContain('Governed assets');
    expect(document.body.textContent).toContain('Compact semantics');
    expect(document.body.textContent).toContain(
      'One or more Catalog Explorer links are unavailable because the Databricks workspace host or asset mapping is not configured.',
    );
    expect(document.querySelectorAll('.governed-assets__list .lineage-node__chip')).toHaveLength(3);
    const signalLinks = Array.from(
      document.querySelectorAll<HTMLAnchorElement>('.lineage-node--signal a'),
    );
    expect(signalLinks.map((link) => link.href)).toEqual([
      `${WORKSPACE}/explore/data/cotality_mortgage_data/corelogic/entrada_eval_voluntary_lien_status_marketing_v2`,
      `${WORKSPACE}/explore/data/functions/mip/gold/fn_in_the_money`,
    ]);

    await openLineageTab();
    expect(apiMocks.lineageManifest).toHaveBeenCalledTimes(1);

    const chips = Array.from(
      document.querySelectorAll<HTMLElement>('.lineage-node__chip'),
    );
    expect(chips.map((chip) => chip.textContent?.trim())).toEqual([
      'cotality_mortgage_data.corelogic.entrada_eval_voluntary_lien_status_marketing_v2',
      'mip.gold.fn_in_the_money',
      'mip.semantics.portfolio_headline_metric_view',
    ]);
    expect(chips.every((chip) => chip.classList.contains('chip'))).toBe(true);

    // Acceptance: the UC function and raw Cotality share deep-link to the
    // workspace Catalog Explorer (functions live under /functions/).
    const fnChip = chips[1] as HTMLAnchorElement;
    expect(fnChip.tagName).toBe('A');
    expect(fnChip.href).toBe(`${WORKSPACE}/explore/data/functions/mip/gold/fn_in_the_money`);
    const rawChip = chips[0] as HTMLAnchorElement;
    expect(rawChip.href).toContain('/explore/data/cotality_mortgage_data/corelogic/');

    // A node without a configured workspace host renders as a plain chip,
    // never a dead link.
    expect(chips[2].tagName).toBe('SPAN');

    // Provenance footer cites the committed manifest.
    expect(document.body.textContent).toContain('backend/resources/lineage_manifest.json');

    const observedLink = document.querySelector<HTMLAnchorElement>('.observed-lineage__asset');
    expect(document.body.textContent).toContain(
      'Observed Unity Catalog relationships - supplemental',
    );
    expect(observedLink?.textContent).toContain('Observed upstream');
    expect(observedLink?.textContent).toContain('2 observed event(s)');
    expect(observedLink?.href).toBe(
      `${WORKSPACE}/explore/data/mip/gold/borrower_360`,
    );
  });

  it('shows the explicit not-mapped state without fetching or inventing nodes', async () => {
    appMocks.drawer = UNMAPPED_SOURCE;
    await render();
    await openLineageTab();

    expect(document.body.textContent).toContain('Lineage not mapped');
    expect(document.querySelectorAll('.lineage-node__chip')).toHaveLength(0);
    expect(apiMocks.lineageManifest).not.toHaveBeenCalled();
  });

  it('classifies Lakebase records and routes to the operational query surface without Catalog links', async () => {
    appMocks.drawer = LAKEBASE_SOURCE;
    await render();
    await openLineageTab();

    expect(document.body.textContent).toContain('Lakebase operational records');
    expect(document.body.textContent).toContain('mip_app.campaigns');
    const operationalLink = Array.from(document.querySelectorAll<HTMLAnchorElement>('a')).find(
      (link) => link.textContent?.includes('Open campaign builder'),
    );
    expect(operationalLink?.getAttribute('href')).toBe('/portfolio-builder');
    expect(document.querySelector('a[href*="/explore/data/"]')).toBeNull();
    expect(apiMocks.lineageManifest).not.toHaveBeenCalled();
    expect(apiMocks.assetMetadata).not.toHaveBeenCalled();
  });

  it('keeps observed assets noninteractive when the backend cannot issue a workspace link', async () => {
    apiMocks.assetMetadata.mockResolvedValue({
      row_count: null,
      num_files: null,
      size_label: null,
      delta_last_modified: null,
      freshness: 'fresh',
      status: 'ready',
      lineage: [
        {
          direction: 'upstream',
          asset_path: 'mip.gold.borrower_360',
          label: 'Gold Borrower 360',
          object_type: 'table',
          event_time: null,
          event_count: 1,
          source: 'system.access.table_lineage',
          catalog_explorer_url: null,
        },
      ],
      known_data_gaps: [],
      last_updated: null,
      catalog_explorer_url: null,
    });
    appMocks.drawer = MAPPED_SOURCE;
    await render();
    await openLineageTab();

    const observedAsset = document.querySelector<HTMLElement>('.observed-lineage__asset');
    expect(observedAsset?.tagName).toBe('DIV');
    expect(observedAsset?.getAttribute('title')).toContain(
      'Catalog Explorer link unavailable',
    );
  });

  it('keeps governed explanations visible when metadata and lineage requests fail', async () => {
    apiMocks.assetMetadata.mockRejectedValue(new Error('metadata unavailable'));
    apiMocks.lineageManifest.mockRejectedValue(new Error('manifest unavailable'));
    appMocks.drawer = MAPPED_SOURCE;
    await render();

    expect(document.body.textContent).toContain('Metadata not loaded');
    expect(document.body.textContent).toContain('Governed asset metadata requires admin access');
    expect(document.body.textContent).toContain('Governed assets unavailable; manifest not loaded');

    await openLineageTab();
    expect(document.body.textContent).toContain('governed lineage manifest could not be loaded');
    expect(document.body.textContent).toContain('Observed relationships unavailable');
  });

  it('flags a family id missing from the manifest as drift, not lineage', async () => {
    appMocks.drawer = { ...MAPPED_SOURCE, lineageFamily: 'not_a_family' };
    await render();
    await openLineageTab();

    expect(apiMocks.lineageManifest).toHaveBeenCalledTimes(1);
    expect(document.body.textContent).toContain('Lineage not mapped');
    expect(document.body.textContent).toContain('not_a_family');
    expect(document.querySelectorAll('.lineage-node__chip')).toHaveLength(0);
  });

  it('resets to Overview when a new source opens', async () => {
    appMocks.drawer = MAPPED_SOURCE;
    await render();
    await openLineageTab();
    expect(
      document.getElementById('drawer-tab-lineage')?.getAttribute('aria-selected'),
    ).toBe('true');

    appMocks.drawer = { ...UNMAPPED_SOURCE };
    await render();
    expect(
      document.getElementById('drawer-tab-overview')?.getAttribute('aria-selected'),
    ).toBe('true');
  });

  it('uses roving tab stops and activates tabs with arrow, Home, and End keys', async () => {
    appMocks.drawer = MAPPED_SOURCE;
    await render();

    const overview = document.getElementById('drawer-tab-overview') as HTMLButtonElement;
    const lineage = document.getElementById('drawer-tab-lineage') as HTMLButtonElement;
    expect(overview.tabIndex).toBe(0);
    expect(lineage.tabIndex).toBe(-1);

    overview.focus();
    await act(async () => {
      overview.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
    });
    expect(document.activeElement).toBe(lineage);
    expect(lineage.getAttribute('aria-selected')).toBe('true');
    expect(lineage.tabIndex).toBe(0);
    expect(overview.tabIndex).toBe(-1);

    await act(async () => {
      lineage.dispatchEvent(new KeyboardEvent('keydown', { key: 'Home', bubbles: true }));
    });
    expect(document.activeElement).toBe(overview);
    expect(overview.getAttribute('aria-selected')).toBe('true');

    await act(async () => {
      overview.dispatchEvent(new KeyboardEvent('keydown', { key: 'End', bubbles: true }));
    });
    expect(document.activeElement).toBe(lineage);
    expect(lineage.getAttribute('aria-selected')).toBe('true');
  });
});
