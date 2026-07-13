/**
 * @vitest-environment happy-dom
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
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
}));

vi.mock('../lib/useWarmingUpRetry', () => ({
  useWarmingUpRetry: (
    _loader: unknown,
    _dependencies: unknown,
    options?: { queryKey?: readonly unknown[] },
  ) => {
    const key = options?.queryKey ?? [];
    apiMocks.hookKeys.push(key);
    const data = key.includes('explorer') || key.includes('latest')
      ? [fixtures.auditEvent]
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
  DataOperationsPanel: () => null,
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
vi.mock('../lib/api', () => ({
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

async function renderAdmin(): Promise<void> {
  await act(async () => {
    root.render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/admin-config']}>
          <AdminConfig />
        </MemoryRouter>
      </QueryClientProvider>,
    );
  });
  await settle();
}

function buttonByLabel(label: RegExp): HTMLButtonElement {
  const button = [...document.querySelectorAll('button')].find((candidate) => (
    label.test(candidate.getAttribute('aria-label') ?? candidate.textContent ?? '')
  ));
  if (!(button instanceof HTMLButtonElement)) throw new Error(`Button not found: ${label}`);
  return button;
}

function setNativeValue(input: HTMLInputElement, value: string): void {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
  setter?.call(input, value);
  input.dispatchEvent(new Event('input', { bubbles: true }));
}

describe('AdminConfig audit explorer', () => {
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

  it('expands a row into accessible forensic details from the audit event contract', async () => {
    await renderAdmin();

    expect(document.getElementById('audit')).not.toBeNull();

    const expand = buttonByLabel(/^Expand audit event evt-8ecf7294$/);
    const detailId = expand.getAttribute('aria-controls');
    expect(expand.getAttribute('aria-expanded')).toBe('false');
    expect(detailId).toBeTruthy();
    expect(document.getElementById(detailId as string)).toBeNull();

    const table = document.querySelector('table[aria-label="Audit events"]');
    expect(table?.textContent).toContain('outreach.approve');
    expect(table?.textContent).toContain('approval-42');
    expect(table?.textContent).toContain('vera@summit.example');
    expect(table?.querySelector('time')?.getAttribute('datetime')).toBe('2026-07-13T14:30:00Z');

    act(() => expand.click());

    const details = document.getElementById(detailId as string);
    expect(expand.getAttribute('aria-expanded')).toBe('true');
    expect(details?.textContent).toContain('evt-8ecf7294');
    expect(details?.textContent).toContain('req-approval-42');
    expect(details?.textContent).toContain('corr-admin-audit-42');
    expect(details?.textContent).toContain('Masked subject reference');
    expect(details?.textContent).toContain('ev-001');
    expect(details?.textContent).toContain('ev-002');
    expect(details?.textContent).toContain('offer_code');
    expect(details?.textContent).toContain('minimum_score');
    expect(details?.querySelector('a')).toBeNull();

    act(() => expand.click());
    expect(expand.getAttribute('aria-expanded')).toBe('false');
    expect(document.getElementById(detailId as string)).toBeNull();
  });

  it('applies borrower, action, and event filters to the audit query key', async () => {
    await renderAdmin();
    const entity = document.querySelector<HTMLInputElement>('input[placeholder="B-... or approval UUID"]');
    const action = document.querySelector<HTMLInputElement>('input[placeholder="outreach.approve"]');
    const eventType = document.querySelector<HTMLInputElement>('input[placeholder="APPROVE"]');
    expect(entity && action && eventType).toBeTruthy();

    act(() => {
      setNativeValue(entity!, 'B-ABC123');
      setNativeValue(action!, 'outreach.approve');
      setNativeValue(eventType!, 'APPROVE');
    });
    act(() => buttonByLabel(/^Apply filters$/).click());

    const explorerKeys = apiMocks.hookKeys.filter((key) => key.includes('explorer'));
    expect(explorerKeys[explorerKeys.length - 1]).toEqual(expect.arrayContaining([
      'B-ABC123',
      true,
      'outreach.approve',
      'APPROVE',
    ]));
    expect(document.body.textContent).toContain('entity = B-ABC123');
    expect(document.body.textContent).toContain('action = outreach.approve');
    expect(document.body.textContent).toContain('event = APPROVE');
  });

  it('copies the real table context and an exact record query without creating a row URL', async () => {
    await renderAdmin();

    await act(async () => buttonByLabel(/^Copy Lakebase audit table context$/).click());
    expect(writeText).toHaveBeenLastCalledWith('mip_app.action_audit');
    expect(document.body.textContent).toContain('Table context copied');

    act(() => buttonByLabel(/^Expand audit event evt-8ecf7294$/).click());
    await act(async () => buttonByLabel(/^Copy event ID evt-8ecf7294$/).click());

    expect(writeText).toHaveBeenLastCalledWith('evt-8ecf7294');
    expect(document.body.textContent).toContain('Event ID copied');

    await act(async () => buttonByLabel(/^Copy Lakebase query for audit event evt-8ecf7294$/).click());
    expect(writeText).toHaveBeenLastCalledWith(
      "SELECT * FROM mip_app.action_audit WHERE event_id = 'evt-8ecf7294';",
    );
    expect(document.body.textContent).toContain('Lakebase record query copied');
    expect(document.querySelector('a[href*="lakebase" i]')).toBeNull();
  });
});
