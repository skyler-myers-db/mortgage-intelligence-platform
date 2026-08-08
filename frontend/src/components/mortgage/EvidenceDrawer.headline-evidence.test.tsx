/**
 * @vitest-environment happy-dom
 *
 * Unit guard for the CLAUDE.md evidence guarantee behind the live spec
 * `home: every headline KPI opens an evidence drawer citing the headline
 * metric view` (frontend/tests/e2e/real_data.spec.ts).
 *
 * Each of the four home KPI drawers must, on its DEFAULT tab, cite
 * `mip.semantics.portfolio_headline_metric_view` in a `.lineage-node__name`
 * — the prototype's BEM class for a governed asset name
 * (design_files/index.html:698) — spelled out fully qualified, never as a
 * bare object name.
 *
 * This runs against the real committed manifest, so a family losing its
 * metric_view node fails here, in milliseconds, instead of in the live run.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
// @ts-expect-error Frontend app types intentionally exclude Node globals; this
// unit test reads the committed manifest under Vitest only.
import { readFileSync } from 'node:fs';
// @ts-expect-error see node:fs note above.
import { join } from 'node:path';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { EvidenceDrawer } from './EvidenceDrawer';
import type { DrawerSource } from '../AppContext';
import { DRAWER_SOURCES } from '../../lib/drawerSources';
import type { LineageLayer, LineageManifestResponse } from '../../types';

declare const process: { cwd(): string };

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const appMocks = vi.hoisted(() => ({
  drawer: null as DrawerSource | null,
  setDrawer: vi.fn(),
}));

const apiMocks = vi.hoisted(() => ({
  assetMetadata: vi.fn(),
  lineageManifest: vi.fn(),
}));

vi.mock('../AppContext', () => ({
  useApp: () => ({ drawer: appMocks.drawer, setDrawer: appMocks.setDrawer, canAccessAdmin: true }),
}));

vi.mock('../../lib/api', () => ({
  api: { assetMetadata: apiMocks.assetMetadata, lineageManifest: apiMocks.lineageManifest },
}));

const WORKSPACE = 'https://dbc-vitest.cloud.databricks.com';
const HEADLINE_VIEW = 'mip.semantics.portfolio_headline_metric_view';

interface ManifestFile {
  schema_version: number;
  families: Array<{
    id: string;
    title: string;
    description: string;
    nodes: Array<{
      id: string;
      layer: LineageLayer;
      object_type: 'table' | 'view' | 'function';
      catalog: string | null;
      schema: string;
      object: string;
      label: string;
      note?: string | null;
    }>;
  }>;
}

const MANIFEST_FILE = JSON.parse(readFileSync(
  join(process.cwd(), '..', 'backend', 'resources', 'lineage_manifest.json'),
  'utf8',
)) as ManifestFile;

/** Mirrors backend/services/lineage_manifest.resolve_node_fqn. */
const MANIFEST: LineageManifestResponse = {
  schema_version: MANIFEST_FILE.schema_version,
  manifest_path: 'backend/resources/lineage_manifest.json',
  families: MANIFEST_FILE.families.map((family) => ({
    id: family.id,
    title: family.title,
    description: family.description,
    nodes: family.nodes.map((node) => {
      const catalog = node.catalog ?? 'mip';
      const functionPath = node.object_type === 'function' ? 'functions/' : '';
      return {
        id: node.id,
        layer: node.layer,
        object_type: node.object_type,
        fqn: `${catalog}.${node.schema}.${node.object}`,
        label: node.label,
        note: node.note ?? null,
        catalog_explorer_url: `${WORKSPACE}/explore/data/${functionPath}${catalog}/${node.schema}/${node.object}`,
      };
    }),
  })),
};

/**
 * The four home KPI cards, in render order, keyed to the registry entry each
 * `<KpiCard source=…>` passes (frontend/src/routes/home.tsx), paired with the
 * `.drawer__subtitle` the live spec asserts.
 */
const HOME_KPI_SOURCES: Array<[string, DrawerSource]> = [
  ['Addressable population', DRAWER_SOURCES.population],
  ['Rate + equity screen', DRAWER_SOURCES.itm],
  ['Opportunity score', DRAWER_SOURCES.leadScore],
  ['How the offer path was selected', DRAWER_SOURCES.nbo],
];

async function settle(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
    await new Promise((resolve) => window.setTimeout(resolve, 0));
  });
}

describe('EvidenceDrawer headline KPI evidence', () => {
  let root: Root;
  let queryClient: QueryClient;

  beforeEach(() => {
    document.body.innerHTML = '<div id="root"></div>';
    root = createRoot(document.getElementById('root') as HTMLElement);
    queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: Infinity } },
    });
    // The governed metadata read is admin-gated and 403s for non-admins in
    // the live app; the evidence guarantee must not depend on it.
    apiMocks.assetMetadata.mockRejectedValue(new Error('forbidden'));
    apiMocks.lineageManifest.mockResolvedValue(MANIFEST);
  });

  afterEach(() => {
    act(() => root.unmount());
    queryClient.clear();
    document.body.innerHTML = '';
    vi.clearAllMocks();
  });

  async function render(source: DrawerSource): Promise<void> {
    appMocks.drawer = source;
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

  function lineageNodeNames(): string[] {
    return Array.from(document.querySelectorAll<HTMLElement>('.lineage-node__name')).map((node) =>
      (node.textContent ?? '').trim(),
    );
  }

  it.each(HOME_KPI_SOURCES)(
    '%s cites the headline metric view in .lineage-node__name on the default tab',
    async (subtitle, source) => {
      await render(source);

      // Default tab is Overview — the live spec never clicks into Lineage.
      expect(document.getElementById('drawer-tab-overview')?.getAttribute('aria-selected')).toBe(
        'true',
      );
      expect(document.querySelector('.drawer__subtitle')?.textContent).toBe(subtitle);
      expect(lineageNodeNames()).toContain(HEADLINE_VIEW);
    },
  );

  it.each(HOME_KPI_SOURCES)(
    '%s cites the metric view exactly once, so the live locator stays strict-mode safe',
    async (_subtitle, source) => {
      await render(source);

      // Playwright locator assertions are strict: `expect(drawer.locator(
      // '.lineage-node__name', { hasText: HEADLINE_VIEW })).toBeVisible()`
      // throws if the selector resolves to more than one element. The
      // governed-assets row is the single place this FQN may appear on
      // Overview — a signal row rendering it too would break the live spec
      // even though the evidence itself is present.
      const matches = lineageNodeNames().filter((name) => name.includes(HEADLINE_VIEW));
      expect(matches).toHaveLength(1);
    },
  );

  it('qualifies every governed asset with catalog and schema', async () => {
    for (const [subtitle, source] of HOME_KPI_SOURCES) {
      await render(source);

      // Scoped to the governed-asset row on purpose. A signal row names the
      // column or measure it read (`…metric_view.in_the_money`, or a bare
      // `portfolio_headline_metric_view` for the COUNT(*) measure), which is
      // authored evidence copy, not a UC object reference. The governed-asset
      // row is the one that claims "this KPI traces to this object", so it is
      // the one that must always be fully qualified.
      const governed = Array.from(
        document.querySelectorAll<HTMLElement>('.governed-assets__list .lineage-node__name'),
      ).map((node) => (node.textContent ?? '').trim());

      expect(governed, subtitle).toContain(HEADLINE_VIEW);
      for (const name of governed) {
        expect(name.split('.').length, `${subtitle} → ${name}`).toBeGreaterThanOrEqual(3);
      }
    }
  });
});
