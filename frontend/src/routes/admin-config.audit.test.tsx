/**
 * @vitest-environment happy-dom
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const fixtures = vi.hoisted(() => ({
  auditEvent: {
    event_id: 'evt-8ecf7294',
    actor: 'vera@summit.example',
    action: 'outreach.approve',
    entity_type: 'approval',
    entity_id: 'approval-42',
    payload_json: {
      channel: 'email',
      offer_code: 'refi',
      thresholds_applied: { minimum_score: 80 },
    },
    evidence_ids: ['ev-001', 'ev-002'],
    created_at: '2026-07-13T14:30:00Z',
    event_type: 'APPROVE',
    subject_clip: 'clip_ref_abc123def456',
    subject_segment: 'itm',
    request_id: 'req-approval-42',
    correlation_id: 'corr-admin-audit-42',
  },
}));

const apiMocks = vi.hoisted(() => ({
  dataEstate: vi.fn(),
  hookKeys: [] as ReadonlyArray<unknown>[],
  auditRows: [] as Array<typeof fixtures.auditEvent>,
}));

vi.mock('../lib/useWarmingUpRetry', () => ({
  useWarmingUpRetry: (
    _loader: unknown,
    options: { queryKey: readonly unknown[] },
  ) => {
    const key = options.queryKey;
    apiMocks.hookKeys.push(key);
    const data = key.includes('explorer')
      ? {
          items: key.includes('cursor-page-2')
            ? apiMocks.auditRows.slice(25)
            : apiMocks.auditRows.slice(0, 25),
          next_cursor: apiMocks.auditRows.length > 25 && !key.includes('cursor-page-2')
            ? 'cursor-page-2'
            : null,
        }
      : key.includes('rollups')
        ? []
        : null;
    return {
      data,
      warmingUp: null,
      error: null,
      isFetching: false,
      isPlaceholderData: false,
      manualRetry: vi.fn(),
    };
  },
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
  DataOperationsPanel: () => (
    <div id="data-operations" tabIndex={-1} aria-labelledby="data-operations-title">
      <div id="data-operations-title">Data operations</div>
    </div>
  ),
}));
vi.mock('../components/admin/BuyerReadinessPanel', () => ({
  BuyerReadinessPanel: () => null,
}));
vi.mock('../components/admin/CapabilityPanel', () => ({
  CapabilityPanel: () => null,
}));
vi.mock('../components/activation/ActivationLoopPanel', () => ({
  ActivationOperationsPanel: () => null,
}));
vi.mock('../components/admin/PlatformCapabilitiesPanel', () => ({
  PlatformCapabilitiesPanel: () => null,
}));
vi.mock('../components/mortgage/DataEstatePanel', () => ({
  DataEstatePanel: () => null,
  DataEstatePanelSkeleton: () => null,
}));
// Spread the real module so every other export (ApiError, which lazily
// preloaded route modules import) stays real; only `api` is mocked.
vi.mock('../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/api')>()),
  api: apiMocks,
}));

import AdminConfig from './admin-config';

let root: Root;
let queryClient: QueryClient;
let writeText: ReturnType<typeof vi.fn>;

async function settle(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
    await new Promise((resolve) => window.setTimeout(resolve, 0));
  });
}

async function renderAdmin(entry = '/admin-config'): Promise<void> {
  await act(async () => {
    root.render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={[entry]}>
          <AdminConfig />
        </MemoryRouter>
      </QueryClientProvider>,
    );
  });
  await settle();
}

describe('AdminConfig audit ledger card', () => {
  beforeEach(() => {
    document.body.innerHTML = '<div id="root"></div>';
    root = createRoot(document.getElementById('root') as HTMLElement);
    queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: Infinity } },
    });
    apiMocks.dataEstate.mockResolvedValue({
      generated_at: '2026-07-13T14:30:00Z',
      lender_name: 'Summit Mortgage',
      public_demo_masking: true,
      lanes: [],
      known_data_gaps: [],
      proof_assets: [],
    });
    apiMocks.hookKeys.length = 0;
    apiMocks.auditRows = [fixtures.auditEvent];
    writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    });
  });

  afterEach(() => {
    act(() => root.unmount());
    queryClient.clear();
    document.body.innerHTML = '';
    vi.clearAllMocks();
  });

  it('replaces the explorer with an "Audit ledger" link card and reads nothing from the ledger', async () => {
    await renderAdmin();

    const card = document.getElementById('audit');
    expect(card?.tabIndex).toBe(-1);
    expect(card?.querySelector('h2, h3')?.textContent).toBe('Audit ledger');
    expect(card?.textContent).toContain('Reading it is itself recorded.');
    const link = card?.querySelector<HTMLAnchorElement>('a.btn');
    expect(link?.textContent).toBe('Open audit ledger');
    expect(link?.getAttribute('href')).toBe('/audit-ledger');
    expect(document.querySelector('table[aria-label="Audit events"]')).toBeNull();
    // D-audit-reads-c3: every ledger read is recorded, so opening Admin
    // issues none (the old "last event" probe is gone with the explorer).
    expect(apiMocks.hookKeys.some((key) => key.includes('audit'))).toBe(false);
    expect(document.body.textContent).not.toContain('Last event');
  });

  it('scrolls and moves focus to the ledger card for the #audit deep link', async () => {
    const scrollIntoView = vi.fn();
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', {
      configurable: true,
      value: scrollIntoView,
    });

    await renderAdmin('/admin-config#audit');

    const audit = document.getElementById('audit');
    expect(audit?.tabIndex).toBe(-1);
    expect(audit?.getAttribute('aria-labelledby')).toBe('admin-audit-ledger-title');
    expect(scrollIntoView).toHaveBeenCalledWith({ block: 'start' });
    expect(document.activeElement).toBe(audit);
  });
  it('scrolls and moves focus to data operations for its deep link', async () => {
    const scrollIntoView = vi.fn();
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', {
      configurable: true,
      value: scrollIntoView,
    });

    await renderAdmin('/admin-config#data-operations');

    const operations = document.getElementById('data-operations');
    expect(operations?.tabIndex).toBe(-1);
    expect(operations?.getAttribute('aria-labelledby')).toBe('data-operations-title');
    expect(scrollIntoView).toHaveBeenCalledWith({ block: 'start' });
    expect(document.activeElement).toBe(operations);
  });

});
