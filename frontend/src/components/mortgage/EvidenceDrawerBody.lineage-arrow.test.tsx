/**
 * @vitest-environment happy-dom
 *
 * The lineage chain's '↓' glyphs are decoration between DOM-ordered nodes
 * (2026-09-21 audit a11y-01, wave 4b). tokenUsage.test.ts lets `.lineage-arrow`
 * paint --text-4 only because it is decorative, so it must also be hidden from
 * assistive tech: a screen reader reads the nodes in order, never "down arrow".
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import type { DrawerSource } from '../AppContext';
import type { LineageManifestResponse } from '../../types';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const SOURCE: DrawerSource = {
  title: 'Refinance economics screen',
  short: 'Rate + equity screen',
  description: 'Refi screen evidence.',
  assetKey: 'borrower_360',
  assetPath: 'mip.gold.borrower_360',
  lineageFamily: 'in_the_money',
};

const apiMocks = vi.hoisted(() => ({
  assetMetadata: vi.fn(),
  lineageManifest: vi.fn(),
}));

vi.mock('../AppContext', () => ({
  useApp: () => ({ drawer: SOURCE, setDrawer: vi.fn(), canAccessAdmin: true }),
}));

vi.mock('../../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../lib/api')>()),
  api: apiMocks,
}));
// The every-user freshness read (critic-03): a lazy-only client, answered here.
vi.mock('../../lib/apiClients/assets', () => ({
  assetsApi: {
    assetFreshness: vi.fn(async () => ({
      asset_key: 'borrower_360', title: 'Gold Borrower 360', freshness: 'fresh', last_updated: null,
      checked_at: null, status: 'live', basis: 'UC Gold Borrower 360', source: 'source_readiness',
    })),
  },
}));

import { EvidenceDrawer } from './EvidenceDrawer';
import { LazyEvidenceDrawerBody } from './evidenceDrawerBodyLoader';

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
          note: null,
          catalog_explorer_url: null,
        },
        {
          id: 'fn_in_the_money',
          layer: 'uc_function',
          object_type: 'function',
          fqn: 'mip.gold.fn_in_the_money',
          label: 'fn_in_the_money',
          note: null,
          catalog_explorer_url: null,
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

async function settle(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
    await new Promise((resolve) => window.setTimeout(resolve, 0));
  });
}

beforeAll(async () => {
  await LazyEvidenceDrawerBody.preload();
}, 60_000);

describe('EvidenceDrawer lineage chain arrows', () => {
  let root: Root;
  let queryClient: QueryClient;

  beforeEach(() => {
    document.body.innerHTML = '<div id="root"></div>';
    root = createRoot(document.getElementById('root') as HTMLElement);
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
    apiMocks.assetMetadata.mockResolvedValue({
      row_count: null,
      num_files: null,
      size_label: null,
      delta_last_modified: null,
      freshness: 'fresh',
      status: 'ready',
      lineage: [],
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

  it('hides every decorative arrow from assistive tech and keeps the nodes in reading order', async () => {
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
    const tab = document.getElementById('drawer-tab-lineage') as HTMLButtonElement | null;
    expect(tab).toBeTruthy();
    await act(async () => {
      tab?.click();
    });
    await settle();

    const arrows = [...document.querySelectorAll<HTMLElement>('.lineage-arrow')];
    // Three nodes, two arrows between them (non-vacuity: the chain rendered).
    expect(document.querySelectorAll('.lineage-node__chip')).toHaveLength(3);
    expect(arrows).toHaveLength(2);
    expect(arrows.map((arrow) => arrow.getAttribute('aria-hidden'))).toEqual(['true', 'true']);
  });
});
