/**
 * @vitest-environment happy-dom
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ActivationLoopPanel, ActivationOperationsPanel } from './ActivationLoopPanel';
import type { ActivationDestination, ActivationOutboxItem } from '../../types';

const apiMocks = vi.hoisted(() => ({
  activationDestinations: vi.fn(),
  activationOutbox: vi.fn(),
  activationSummary: vi.fn(),
  stageActivation: vi.fn(),
}));

vi.mock('../../lib/api', async () => {
  const actual = await vi.importActual<typeof import('../../lib/api')>('../../lib/api');
  return {
    ...actual,
    api: {
      ...actual.api,
      activationDestinations: apiMocks.activationDestinations,
      activationOutbox: apiMocks.activationOutbox,
      activationSummary: apiMocks.activationSummary,
      stageActivation: apiMocks.stageActivation,
    },
  };
});

const APPROVAL_ID = '11111111-1111-4111-8111-111111111111';
const REQUEST_ID = '22222222-2222-4222-8222-222222222222';

const DESTINATION: ActivationDestination = {
  destination_key: 'salesforce_crm',
  destination_type: 'salesforce',
  display_name: 'Salesforce CRM',
  status: 'not_configured',
  allowed_actions: ['stage_lead'],
  updated_at: '2026-06-01T00:00:00Z',
};

function outboxItem(overrides: Partial<ActivationOutboxItem> = {}): ActivationOutboxItem {
  return {
    activation_id: '33333333-3333-4333-8333-333333333333',
    destination_key: 'salesforce_crm',
    destination_type: 'salesforce',
    destination_display_name: 'Salesforce CRM',
    destination_status: 'not_configured',
    entity_type: 'borrower',
    entity_id: 'B-48291',
    borrower_id: 'B-48291',
    campaign_id: null,
    approval_id: APPROVAL_ID,
    offer_code: 'refi',
    channel: 'email',
    status: 'dry_run',
    request_id: REQUEST_ID,
    created_by: 'skyler@entrada.ai',
    created_at: '2026-06-01T00:00:00Z',
    updated_at: '2026-06-01T00:00:00Z',
    ...overrides,
  };
}

async function settle(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
    await new Promise((resolve) => window.setTimeout(resolve, 0));
  });
}

describe('ActivationLoopPanel', () => {
  let root: Root;
  let queryClient: QueryClient;

  beforeEach(() => {
    document.body.innerHTML = '<div id="root"></div>';
    root = createRoot(document.getElementById('root') as HTMLElement);
    queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: Infinity } },
    });
    apiMocks.activationDestinations.mockResolvedValue([DESTINATION]);
    apiMocks.activationOutbox.mockResolvedValue([]);
    apiMocks.activationSummary.mockResolvedValue({
      destinations: [DESTINATION],
      recent_outbox: [],
    });
    apiMocks.stageActivation.mockResolvedValue({
      staged: true,
      activation: outboxItem({ activation_id: '44444444-4444-4444-8444-444444444444' }),
      audit_event_id: '55555555-5555-4555-8555-555555555555',
    });
  });

  afterEach(() => {
    act(() => root.unmount());
    queryClient.clear();
    document.body.innerHTML = '';
    vi.clearAllMocks();
  });

  async function render(approvalId: string | null = APPROVAL_ID): Promise<void> {
    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <ActivationLoopPanel
            borrowerId="B-48291"
            offerCode="refi"
            approvalId={approvalId}
            approved
          />
        </QueryClientProvider>,
      );
    });
    await settle();
  }

  function stageButton(): HTMLButtonElement {
    const button = Array.from(document.querySelectorAll('button')).find((candidate) =>
      candidate.textContent?.includes('Stage')
      || candidate.textContent?.includes('Staged')
      || candidate.textContent?.includes('Retry')
    );
    expect(button).toBeTruthy();
    return button as HTMLButtonElement;
  }

  it('stages an approved borrower with the durable approval id', async () => {
    await render();

    await act(async () => {
      stageButton().click();
    });
    await settle();

    expect(apiMocks.stageActivation).toHaveBeenCalledWith(expect.objectContaining({
      borrower_id: 'B-48291',
      destination_key: 'salesforce_crm',
      offer_code: 'refi',
      channel: 'email',
      approval_id: APPROVAL_ID,
    }));
    expect(document.body.textContent).toContain('44444444-4444-4444-8444-444444444444');
  });

  it('disables staging when the durable approval id is missing', async () => {
    await render(null);

    expect(stageButton().disabled).toBe(true);
    expect(document.body.textContent).toContain('durable approval ID');
    expect(apiMocks.stageActivation).not.toHaveBeenCalled();
  });

  it('disables repeat staging when a matching outbox row already exists', async () => {
    apiMocks.activationOutbox.mockResolvedValue([outboxItem()]);

    await render();

    const button = stageButton();
    expect(button.disabled).toBe(true);
    expect(button.textContent).toContain('Staged');
    expect(apiMocks.stageActivation).not.toHaveBeenCalled();
  });

  it('retries a failed activation through the business-key-safe stage endpoint', async () => {
    apiMocks.activationOutbox.mockResolvedValue([outboxItem({ status: 'failed' })]);

    await render();

    const button = stageButton();
    expect(button.disabled).toBe(false);
    expect(button.textContent).toContain('Retry');

    await act(async () => {
      button.click();
    });
    await settle();

    expect(apiMocks.stageActivation).toHaveBeenCalledWith(expect.objectContaining({
      borrower_id: 'B-48291',
      destination_key: 'salesforce_crm',
      approval_id: APPROVAL_ID,
    }));
  });

  it('marks activation operations unavailable when the registry cannot be read', async () => {
    apiMocks.activationSummary.mockRejectedValue(new Error('registry down'));

    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <ActivationOperationsPanel />
        </QueryClientProvider>,
      );
    });
    await settle();

    expect(document.body.textContent).toContain('Activation destinations');
    expect(document.body.textContent).toContain('unavailable');
    expect(document.body.textContent).toContain('Activation status unavailable: registry down');
    expect(document.body.textContent).not.toContain('loading');
  });
});
