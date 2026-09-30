/**
 * @vitest-environment happy-dom
 *
 * Data source readiness rows (App SQL is gold-only, 2026-09-30): the admin
 * sources endpoint reads only gold.source_readiness, and a source that
 * summary has no row for comes back `unavailable`. It must read "readiness
 * unavailable" with a warn dot -- before the status joined the union it fell
 * through to "roadmap", which misstated a readiness gap as a product plan.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const SOURCES = [
  { name: 'Voluntary Lien', status: 'live', rows: 1200, last_updated: '2026-09-29 06:00:00', note: 'ok' },
  {
    name: 'MLS Listings',
    status: 'unavailable',
    rows: null,
    last_updated: null,
    note: 'Cotality MLS listing feed · readiness summary has no row for this source',
  },
  { name: 'Building Permits', status: 'roadmap', rows: null, last_updated: null, note: 'Contracted' },
];

const apiMocks = vi.hoisted(() => ({
  dataEstate: vi.fn(),
  growthAgentCapabilities: vi.fn(),
}));

// Only the sources tile gets data; rules and audit stay in their loading state.
vi.mock('../lib/useWarmingUpRetry', () => ({
  useWarmingUpRetry: (_fetcher: unknown, options: { queryKey: readonly string[] }) => ({
    data: options.queryKey.join('/') === 'mip/admin/sources' ? SOURCES : null,
    warmingUp: null,
    error: null,
    isFetching: false,
    isPlaceholderData: false,
  }),
}));

vi.mock('../components/AppContext', () => ({
  useApp: () => ({
    theme: 'dark',
    setTheme: vi.fn(),
    accent: 'bright',
    setAccent: vi.fn(),
    density: 'comfortable',
    setDensity: vi.fn(),
    lender: 'Summit Mortgage',
    showEvidence: true,
    setShowEvidence: vi.fn(),
    showConfidence: true,
    setShowConfidence: vi.fn(),
    setDrawer: vi.fn(),
  }),
}));

vi.mock('../components/admin/DataOperationsPanel', () => ({
  DataOperationsPanel: () => <div data-testid="data-operations-panel" />,
}));
vi.mock('../components/admin/BuyerReadinessPanel', () => ({
  BuyerReadinessPanel: () => <div data-testid="buyer-readiness-panel" />,
}));
vi.mock('../components/admin/CapabilityPanel', () => ({
  CapabilityPanel: () => <div data-testid="capability-panel" />,
}));
vi.mock('../components/activation/ActivationLoopPanel', () => ({
  ActivationOperationsPanel: () => <div data-testid="activation-panel" />,
}));

vi.mock('../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/api')>()),
  api: apiMocks,
}));

import AdminConfig from './admin-config';

let root: Root;
let queryClient: QueryClient;

function sourceRow(name: string): HTMLElement {
  const row = [...document.querySelectorAll<HTMLElement>('.source-status-row')].find((el) =>
    el.querySelector('.source-status-main')?.textContent?.startsWith(name),
  );
  if (!row) throw new Error(`no source row for ${name}`);
  return row;
}

describe('AdminConfig data source readiness', () => {
  beforeEach(() => {
    document.body.innerHTML = '<div id="root"></div>';
    root = createRoot(document.getElementById('root') as HTMLElement);
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
    apiMocks.dataEstate.mockReturnValue(new Promise(() => {}));
    apiMocks.growthAgentCapabilities.mockResolvedValue({ workflows: [], monitors: [], capabilities: [] });
  });

  afterEach(() => {
    act(() => root.unmount());
    queryClient.clear();
    document.body.innerHTML = '';
    vi.clearAllMocks();
  });

  it('labels an unavailable source "readiness unavailable" with a warn dot', async () => {
    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter initialEntries={['/admin-config']}>
            <AdminConfig />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });

    const unavailable = sourceRow('MLS Listings');
    expect(unavailable.querySelector('.source-status-meta')?.textContent).toBe('readiness unavailable');
    expect(unavailable.querySelector('.status-dot')?.classList.contains('status-dot--warn')).toBe(true);

    // The neighbours keep their own vocabulary.
    expect(sourceRow('Building Permits').querySelector('.source-status-meta')?.textContent).toBe('roadmap');
    expect(sourceRow('Voluntary Lien').querySelector('.status-dot')?.classList.contains('status-dot--ok')).toBe(
      true,
    );
  });
});
