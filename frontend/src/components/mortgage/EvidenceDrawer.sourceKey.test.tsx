/**
 * @vitest-environment happy-dom
 *
 * The evidence drawer links a sanitized signal to its Cotality share table on
 * every install (M3-PR2). A signal names the share table canonically (the
 * drawer registry is written in canonical names); an install that mounts the
 * share under other names serves the raw-share manifest node at its configured
 * `fqn` with the canonical `source_key`. The drawer matches the node through
 * `source_key`, so the signal opens the configured table in Catalog Explorer.
 * A node that carries no `source_key` at a location the asset map does not
 * know stays unmatched, and the signal renders as plain text.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { EvidenceDrawer } from './EvidenceDrawer';
import { LazyEvidenceDrawerBody } from './evidenceDrawerBodyLoader';
import type { DrawerSource } from '../AppContext';
import type { LineageManifestNode, LineageManifestResponse } from '../../types';

const appMocks = vi.hoisted(() => ({
  drawer: null as DrawerSource | null,
  setDrawer: vi.fn(),
}));

const apiMocks = vi.hoisted(() => ({
  assetMetadata: vi.fn(),
  lineageManifest: vi.fn(),
  assetFreshness: vi.fn(),
}));

vi.mock('../AppContext', () => ({
  useApp: () => ({
    drawer: appMocks.drawer,
    setDrawer: appMocks.setDrawer,
    canAccessAdmin: false,
  }),
}));

vi.mock('../../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../lib/api')>()),
  api: {
    assetMetadata: apiMocks.assetMetadata,
    lineageManifest: apiMocks.lineageManifest,
  },
}));

vi.mock('../../lib/apiClients/assets', () => ({
  assetsApi: { assetFreshness: apiMocks.assetFreshness },
}));

const WORKSPACE = 'https://dbc-vitest.cloud.databricks.com';
const CANONICAL_LIEN = 'cotality_mortgage_data.corelogic.entrada_eval_voluntary_lien_status_marketing_v2';
const CONFIGURED_LIEN = 'acme_share.mortgage.acme_voluntary_lien_status_marketing_v2';
const CONFIGURED_LIEN_URL = `${WORKSPACE}/explore/data/acme_share/mortgage/acme_voluntary_lien_status_marketing_v2`;
const FUNCTION_URL = `${WORKSPACE}/explore/data/functions/mip/gold/fn_in_the_money`;

function manifest(shareNode: Partial<LineageManifestNode>): LineageManifestResponse {
  return {
    schema_version: 1,
    manifest_path: 'backend/resources/lineage_manifest.json',
    families: [
      {
        id: 'in_the_money',
        title: 'In the Money rule',
        description: 'Rate spread + equity screen traced end to end.',
        nodes: [
          {
            id: 'raw_voluntary_lien',
            layer: 'raw_share',
            object_type: 'table',
            fqn: CONFIGURED_LIEN,
            label: 'Cotality Voluntary Lien share',
            note: 'Borrower lien rate and balance inputs.',
            catalog_explorer_url: CONFIGURED_LIEN_URL,
            source_key: CANONICAL_LIEN,
            ...shareNode,
          },
          {
            id: 'fn_in_the_money',
            layer: 'uc_function',
            object_type: 'function',
            fqn: 'mip.gold.fn_in_the_money',
            label: 'fn_in_the_money',
            note: 'Canonical In the Money rule.',
            catalog_explorer_url: FUNCTION_URL,
            source_key: null,
          },
        ],
      },
    ],
  };
}

const SOURCE: DrawerSource = {
  title: 'In the Money rule',
  short: 'Rate + equity screen',
  description: 'In the Money rule evidence.',
  lineageFamily: 'in_the_money',
  signals: [
    { label: 'Lien rate input', source: `${CANONICAL_LIEN}.current_rate`, value: 'borrower row' },
    { label: 'Economics screen', source: 'mip.gold.fn_in_the_money', value: 'reviewed function' },
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

describe('EvidenceDrawer signals key share tables by source_key', () => {
  let root: Root;
  let queryClient: QueryClient;

  beforeEach(() => {
    document.body.innerHTML = '<div id="root"></div>';
    root = createRoot(document.getElementById('root') as HTMLElement);
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
    appMocks.drawer = SOURCE;
  });

  afterEach(() => {
    act(() => root.unmount());
    queryClient.clear();
    document.body.innerHTML = '';
    vi.clearAllMocks();
  });

  async function renderUnderTheHood(response: LineageManifestResponse): Promise<void> {
    apiMocks.lineageManifest.mockResolvedValue(response);
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
    await act(async () => {
      (document.getElementById('drawer-tab-under-the-hood') as HTMLButtonElement).click();
    });
    await settle();
  }

  const signalLinks = () =>
    Array.from(document.querySelectorAll<HTMLAnchorElement>('.lineage-node--signal a')).map((link) => ({
      text: link.textContent?.trim(),
      href: link.href,
    }));

  it('opens a configured share table through its canonical source_key', async () => {
    await renderUnderTheHood(manifest({}));

    expect(apiMocks.lineageManifest).toHaveBeenCalledTimes(1);
    expect(signalLinks()).toEqual([
      { text: `${CANONICAL_LIEN}.current_rate`, href: CONFIGURED_LIEN_URL },
      { text: 'mip.gold.fn_in_the_money', href: FUNCTION_URL },
    ]);
  });

  it('leaves the signal unlinked when the configured node carries no source_key', async () => {
    await renderUnderTheHood(manifest({ source_key: null }));

    expect(signalLinks()).toEqual([{ text: 'mip.gold.fn_in_the_money', href: FUNCTION_URL }]);
    const signals = Array.from(document.querySelectorAll('.lineage-node--signal'));
    expect(signals[0]?.textContent).toContain(`${CANONICAL_LIEN}.current_rate`);
  });

  it.each([CANONICAL_LIEN, null])('links a canonical install exactly as its fqn did (source_key %s)', async (sourceKey) => {
    const canonicalUrl = `${WORKSPACE}/explore/data/cotality_mortgage_data/corelogic/entrada_eval_voluntary_lien_status_marketing_v2`;
    await renderUnderTheHood(
      manifest({ fqn: CANONICAL_LIEN, catalog_explorer_url: canonicalUrl, source_key: sourceKey }),
    );

    expect(signalLinks()).toEqual([
      { text: `${CANONICAL_LIEN}.current_rate`, href: canonicalUrl },
      { text: 'mip.gold.fn_in_the_money', href: FUNCTION_URL },
    ]);
  });
});
