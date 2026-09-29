/**
 * @vitest-environment happy-dom
 *
 * Portfolio Builder's governed campaign writes (wave 4b, stack-09 item 2,
 * runtime-06 (e), states-07 item 3) at the layer the invariants live: a real
 * QueryClient and real TanStack mutation machinery, only the network mocked.
 */
import { onlineManager, QueryClient, QueryClientProvider, QueryObserver } from '@tanstack/react-query';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const apiMocks = vi.hoisted(() => ({
  portfolioCreate: vi.fn(),
  campaignStatus: vi.fn(),
  campaigns: vi.fn(),
}));

vi.mock('../api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api')>()),
  api: apiMocks,
}));

import { ApiError } from '../apiTransport';
import { queryKeys } from '../queryKeys';
import {
  CAMPAIGN_ARCHIVE_RATIONALE,
  campaignMutationKeys,
  useArchiveCampaign,
  useCreateCampaign,
  type CreateCampaignVariables,
} from './campaigns';

const CAMPAIGN_ID = '11111111-1111-4111-8111-111111111111';

function createVars(): CreateCampaignVariables {
  return {
    name: 'Illinois refinance cohort',
    criteria: { states: ['IL'] },
    config: {
      suppression_policy: { default: 'eligible_only', frequency_cap_days: 30 },
      message_variants: [],
      channel_cascade: [{ channel: 'email', step: 1 }],
      send_window: { days: ['Tuesday'], start_local: '09:00', end_local: '16:00' },
      holdout: { method: 'hash_modulo', size_pct: 10 },
      roi_assumptions: { budget_usd: null, cost_per_contact_usd: {}, source: 'operator_configured' },
      household_dedup: { enabled: false, dedupe_unit: 'borrower', primary_contact_strategy: 'highest_opportunity_eligible' },
    },
    requestId: 'req-create-1',
  };
}

let root: Root;
let client: QueryClient;
let unsubscribeList: () => void = () => undefined;

function mountHook<T>(useHook: () => T): () => T {
  let value: T | undefined;
  function Probe() {
    value = useHook();
    return null;
  }
  act(() => root.render(createElement(QueryClientProvider, { client }, createElement(Probe))));
  return () => {
    if (value === undefined) throw new Error('hook did not render');
    return value;
  };
}

async function flush(): Promise<void> {
  await act(async () => {
    await new Promise((resolve) => window.setTimeout(resolve, 0));
    await new Promise((resolve) => window.setTimeout(resolve, 0));
  });
}

/** An active saved-campaign list, like the page's useQuery on queryKeys.campaigns(). */
async function observeList(): Promise<void> {
  const observer = new QueryObserver(client, {
    queryKey: queryKeys.campaigns(),
    queryFn: () => apiMocks.campaigns(),
    retry: false,
  });
  unsubscribeList = observer.subscribe(() => undefined);
  await flush();
}

beforeEach(() => {
  vi.clearAllMocks();
  document.body.innerHTML = '<div id="root"></div>';
  root = createRoot(document.getElementById('root') as HTMLElement);
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  apiMocks.portfolioCreate.mockResolvedValue({ portfolio_id: 'p-1', campaign_id: CAMPAIGN_ID, audit_event_id: 'audit-save-1' });
  apiMocks.campaignStatus.mockResolvedValue({ campaign_id: CAMPAIGN_ID, status: 'archived' });
  apiMocks.campaigns.mockResolvedValue({ campaigns: [] });
});

afterEach(() => {
  unsubscribeList();
  unsubscribeList = () => undefined;
  act(() => root.unmount());
  client.clear();
  onlineManager.setOnline(true);
  document.body.innerHTML = '';
});

describe('campaign write mutations', () => {
  it('own the create / archive keys, with the governed options and no onMutate', async () => {
    const latest = mountHook(() => ({ create: useCreateCampaign(client), archive: useArchiveCampaign(client) }));
    await act(async () => {
      await latest().create.mutateAsync(createVars());
      await latest().archive.mutateAsync({ campaignId: CAMPAIGN_ID, name: 'Legacy' });
    });
    const [create, archive] = client.getMutationCache().getAll();
    expect(create.options.mutationKey).toEqual(campaignMutationKeys.create);
    expect(archive.options.mutationKey).toEqual(campaignMutationKeys.archive);
    expect(campaignMutationKeys).toEqual({
      create: ['mip', 'campaigns', 'create'],
      archive: ['mip', 'campaigns', 'archive'],
    });
    for (const mutation of [create, archive]) {
      expect(mutation.options.networkMode).toBe('always');
      expect(mutation.options.retry).toBe(false);
      expect(mutation.options.onMutate).toBeUndefined();
    }
  });

  it('create sends the name, criteria and config with the intent request_id as the idempotency key', async () => {
    const latest = mountHook(() => useCreateCampaign(client));
    await act(async () => {
      await latest().mutateAsync(createVars());
    });
    const vars = createVars();
    expect(apiMocks.portfolioCreate).toHaveBeenCalledWith(vars.name, vars.criteria, { ...vars.config, request_id: 'req-create-1' });
    expect(apiMocks.portfolioCreate.mock.calls[0]).toHaveLength(3);
  });

  it('create resolves only after the saved-campaign list is re-read, and writes no cache entry', async () => {
    await observeList();
    expect(apiMocks.campaigns).toHaveBeenCalledTimes(1);
    let releaseList: (value: unknown) => void = () => undefined;
    apiMocks.campaigns.mockReturnValueOnce(new Promise((resolve) => {
      releaseList = resolve;
    }));
    const setQueryData = vi.spyOn(client, 'setQueryData');
    const latest = mountHook(() => useCreateCampaign(client));
    let resolved = false;
    await act(async () => {
      void latest().mutateAsync(createVars()).then(() => {
        resolved = true;
      });
    });
    await flush();
    // The POST returned and the list re-read is on the wire: not saved yet.
    expect(apiMocks.campaigns).toHaveBeenCalledTimes(2);
    expect(resolved).toBe(false);
    await act(async () => {
      releaseList({ campaigns: [] });
    });
    await flush();
    expect(resolved).toBe(true);
    expect(setQueryData).not.toHaveBeenCalled();
  });

  it('a failed create re-reads nothing', async () => {
    await observeList();
    apiMocks.portfolioCreate.mockRejectedValueOnce(new Error('Lakebase unavailable'));
    const latest = mountHook(() => useCreateCampaign(client));
    await act(async () => {
      await latest().mutateAsync(createVars()).catch(() => undefined);
    });
    await flush();
    expect(apiMocks.campaigns).toHaveBeenCalledTimes(1);
    expect(latest().error?.message).toBe('Lakebase unavailable');
  });

  it('create started offline fails at once and never fires on reconnect (networkMode always)', async () => {
    onlineManager.setOnline(false);
    apiMocks.portfolioCreate.mockRejectedValue(new ApiError('Network unreachable', { path: '/api/portfolio/create', status: null }));
    const latest = mountHook(() => useCreateCampaign(client));
    let outcome = 'pending';
    await act(async () => {
      void latest().mutateAsync(createVars()).then(
        () => { outcome = 'resolved'; },
        () => { outcome = 'rejected'; },
      );
    });
    await flush();
    expect(outcome).toBe('rejected');
    await act(async () => {
      onlineManager.setOnline(true);
    });
    await flush();
    expect(apiMocks.portfolioCreate).toHaveBeenCalledTimes(1);
  });

  it('archive sends the reviewed rationale, refreshes the list on success and is pending until the PATCH returns', async () => {
    await observeList();
    let release: (value: unknown) => void = () => undefined;
    apiMocks.campaignStatus.mockReturnValueOnce(new Promise((resolve) => {
      release = resolve;
    }));
    const latest = mountHook(() => useArchiveCampaign(client));
    act(() => latest().mutate({ campaignId: CAMPAIGN_ID, name: 'Legacy' }));
    // Synchronous: the latch the panel checks holds before any re-render.
    expect(client.isMutating({ mutationKey: campaignMutationKeys.archive })).toBe(1);
    await flush();
    expect(latest().isPending).toBe(true);
    expect(apiMocks.campaignStatus).toHaveBeenCalledWith(CAMPAIGN_ID, 'archived', CAMPAIGN_ARCHIVE_RATIONALE);
    expect(apiMocks.campaignStatus.mock.calls[0]).toHaveLength(3);
    expect(apiMocks.campaigns).toHaveBeenCalledTimes(1);
    await act(async () => {
      release({ campaign_id: CAMPAIGN_ID, status: 'archived' });
    });
    await flush();
    expect(latest().isSuccess).toBe(true);
    expect(apiMocks.campaigns).toHaveBeenCalledTimes(2);
  });

  it('a failed archive keeps the list as it is', async () => {
    await observeList();
    apiMocks.campaignStatus.mockRejectedValueOnce(new Error('Lakebase unavailable'));
    const latest = mountHook(() => useArchiveCampaign(client));
    act(() => latest().mutate({ campaignId: CAMPAIGN_ID, name: 'Legacy' }));
    await flush();
    expect(latest().isError).toBe(true);
    expect(apiMocks.campaigns).toHaveBeenCalledTimes(1);
  });
});
