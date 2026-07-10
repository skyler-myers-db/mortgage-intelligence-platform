/**
 * @vitest-environment happy-dom
 *
 * Pins the Data-estate proof surface to the Administration console (relocated
 * from Home 2026-07-10). Asserts the skeleton-then-panel load and that the
 * panel sits above the "Data source readiness" card it is thematically
 * adjacent to.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const apiMocks = vi.hoisted(() => ({
  dataEstate: vi.fn(),
}));

// Admin-config's rules / sources / audit tiles use useWarmingUpRetry; stub it
// to an empty state so they render loading placeholders without network. The
// relocated Data-estate panel is driven by the REAL useQuery, not this hook.
vi.mock('../lib/useWarmingUpRetry', () => ({
  useWarmingUpRetry: () => ({
    data: null,
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

vi.mock('../lib/api', () => ({
  api: apiMocks,
}));

import AdminConfig from './admin-config';

const ESTATE_FIXTURE = {
  generated_at: '2026-07-10T00:00:00Z',
  lender_name: 'Summit Mortgage',
  public_demo_masking: true,
  lanes: [
    {
      id: 'first_party',
      title: 'First-party servicing',
      description: 'Loan book of record.',
      status: 'live',
      assets: [
        {
          name: 'Servicing ledger',
          label: 'mip.gold.borrower_360',
          status: 'live',
          row_count: 1234,
          note: 'Governed servicing rows.',
          uc_object: 'mip.gold.borrower_360',
        },
      ],
    },
  ],
  known_data_gaps: [],
  proof_assets: ['mip.gold.borrower_360'],
};

async function settle(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
    await new Promise((resolve) => window.setTimeout(resolve, 0));
  });
}

function renderAdmin(): void {
  root.render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/admin-config']}>
        <AdminConfig />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

let root: Root;
let queryClient: QueryClient;

describe('AdminConfig data estate relocation', () => {
  beforeEach(() => {
    document.body.innerHTML = '<div id="root"></div>';
    root = createRoot(document.getElementById('root') as HTMLElement);
    queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: Infinity } },
    });
    apiMocks.dataEstate.mockResolvedValue(ESTATE_FIXTURE);
  });

  afterEach(() => {
    act(() => root.unmount());
    queryClient.clear();
    document.body.innerHTML = '';
    vi.clearAllMocks();
  });

  it('shows the data-estate skeleton while the proof surface is still loading', async () => {
    // A never-resolving promise keeps the query pending so the skeleton stays.
    apiMocks.dataEstate.mockReturnValueOnce(new Promise(() => {}));

    await act(async () => {
      renderAdmin();
    });

    const skeleton = document.querySelector('.data-estate[aria-busy="true"]');
    expect(skeleton).toBeTruthy();
    expect(skeleton?.getAttribute('role')).toBe('status');
    expect(document.body.textContent).toContain('Data estate under the hood');
    // The resolved (non-busy) panel is not on screen yet.
    expect(document.querySelector('.data-estate:not([aria-busy="true"])')).toBeNull();
  });

  it('renders the resolved data-estate proof panel above the data source readiness card', async () => {
    await act(async () => {
      renderAdmin();
    });
    await settle();

    const estatePanel = document.querySelector('.data-estate:not([aria-busy="true"])');
    expect(estatePanel).toBeTruthy();
    expect(document.body.textContent).toContain('First-party servicing');

    // Data-estate proof must precede the "Data source readiness" card in the DOM.
    const readiness = [...document.querySelectorAll('.h-4')].find(
      (el) => el.textContent === 'Data source readiness',
    );
    expect(readiness).toBeTruthy();
    const order = [...document.querySelectorAll('*')];
    expect(order.indexOf(estatePanel as Element)).toBeLessThan(order.indexOf(readiness as Element));
  });
});
