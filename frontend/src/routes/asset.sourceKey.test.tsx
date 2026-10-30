/**
 * @vitest-environment happy-dom
 *
 * Observed lineage links a Cotality share table to its asset page on every
 * install (M3-PR2). An install that mounts the share under other names
 * reports the table at its configured `asset_path`; the canonical
 * `source_key` the API adds is what the asset map knows, so the upstream
 * share node still links to `/data-estate/assets/entrada_eval_*`. A node
 * whose location the map does not know, and that carries no `source_key`,
 * stays an unlinked node.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, Route, Routes } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { api } from '../lib/api';
import type { AssetLineageNode, AssetMetadataResponse } from '../types';
import AssetRoute from './asset';

async function settle(): Promise<void> {
  for (let i = 0; i < 4; i += 1) {
    await act(async () => {
      await Promise.resolve();
      await new Promise((resolve) => window.setTimeout(resolve, 0));
    });
  }
}

function lineageNode(overrides: Partial<AssetLineageNode>): AssetLineageNode {
  return {
    direction: 'upstream',
    asset_path: 'acme_share.mortgage.property_domain_v3',
    label: 'Cotality Property Domain Share',
    object_type: 'table',
    event_time: '2026-10-01 00:00:00',
    event_count: 3,
    source: 'audit.asset_lineage (view over system.access.table_lineage)',
    catalog_explorer_url: null,
    source_key: null,
    ...overrides,
  };
}

function assetMetadata(lineage: AssetLineageNode[]): AssetMetadataResponse {
  return {
    asset_path: 'mip.silver.property_master',
    title: 'Silver Property Master',
    description: 'PII-minimized CLIP-grain property lift from the Cotality property share.',
    object_type: 'table',
    status: 'live',
    freshness: 'fresh',
    catalog: 'mip',
    schema_name: 'silver',
    object_name: 'property_master',
    uc_object: 'mip.silver.property_master',
    generated_at: '2026-10-01T00:00:00Z',
    lineage,
    tags: [],
    properties: [],
    columns: [],
    known_data_gaps: [],
  } as unknown as AssetMetadataResponse;
}

describe('Asset detail observed lineage keys share tables by source_key', () => {
  let root: Root;
  let container: HTMLElement;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    vi.restoreAllMocks();
    await act(async () => root.unmount());
    container.remove();
  });

  async function render(lineage: AssetLineageNode[]): Promise<void> {
    vi.spyOn(api, 'assetMetadata').mockResolvedValue(assetMetadata(lineage));
    await act(async () => {
      root.render(
        <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
          <MemoryRouter initialEntries={['/data-estate/assets/property_master']}>
            <Routes>
              <Route path="/data-estate/assets/:assetKey" element={<AssetRoute />} />
            </Routes>
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
    await settle();
  }

  const lineageLinks = () =>
    [...container.querySelectorAll<HTMLAnchorElement>('.asset-lineage a.lineage-node--link')].map((link) =>
      link.getAttribute('href'),
    );

  it('links a configured share table through its canonical source_key', async () => {
    await render([
      lineageNode({ source_key: 'cotality_mortgage_data.corelogic.entrada_eval_property_domain_v3' }),
    ]);

    expect(container.textContent).toContain('Cotality Property Domain Share');
    expect(lineageLinks()).toEqual(['/data-estate/assets/entrada_eval_property_domain_v3']);
  });

  it('leaves a configured location with no source_key unlinked', async () => {
    await render([lineageNode({ source_key: null })]);

    expect(container.textContent).toContain('Cotality Property Domain Share');
    expect(lineageLinks()).toEqual([]);
  });

  it('links a canonical install exactly as its asset_path did', async () => {
    const canonical = 'cotality_mortgage_data.corelogic.entrada_eval_property_domain_v3';
    await render([
      lineageNode({ asset_path: canonical, source_key: canonical }),
      lineageNode({
        direction: 'downstream',
        asset_path: 'mip.gold.borrower_360',
        label: 'Gold Borrower 360',
        source_key: null,
      }),
    ]);

    expect(lineageLinks()).toEqual([
      '/data-estate/assets/entrada_eval_property_domain_v3',
      '/data-estate/assets/borrower_360',
    ]);
  });
});
