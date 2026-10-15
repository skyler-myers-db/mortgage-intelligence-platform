/**
 * @vitest-environment happy-dom
 *
 * The Offer's campaign binding through the queue pager and Next in queue
 * (2026-09-21 audit shell-04 / flow-09; D-approval-flow-a2): verified only
 * when the campaign read confirms it AND the queue carries the same binding;
 * an unverified binding hides the pager (fail closed). The rule is the Lead
 * Queue's, restated: one table feeds both functions.
 */
import { act, useEffect } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { verifiedCampaignBinding } from '../components/mortgage/LeadTable.logic';
import type { QueueContext } from '../lib/queueContext';
import { queryKeys } from '../lib/queryKeys';
import { mount, type Mounted } from '../test/render';
import type { CampaignSummary } from '../types';

const campaignRead = vi.hoisted(() => vi.fn());
vi.mock('../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/api')>()),
  api: { campaign: (...args: unknown[]) => campaignRead(...args) },
}));

import {
  boundOfferPath,
  campaignConfirmsBinding,
  queueCarriesBinding,
  useVerifiedOfferBinding,
  type VerifiedOfferBinding,
} from './offer-orchestrator.binding';

const BINDING = { campaign_id: 'cmp-0001', variant_name: 'Variant A' };
const campaign = (overrides: Partial<CampaignSummary> = {}): CampaignSummary => ({
  campaign_id: 'cmp-0001',
  message_variants: [{ variant_name: 'Variant A', channel: 'email' }],
  ...overrides,
}) as CampaignSummary;
const queue = (search: string): QueueContext => ({ epoch: 'e1', search, label: 'IL', ids: ['B-0000000000001'] });
const BOUND_SEARCH = `?${new URLSearchParams({ state: 'IL', ...BINDING }).toString()}`;

describe('the binding rule (parity with the Lead Queue)', () => {
  const table: ReadonlyArray<[string, CampaignSummary | null]> = [
    ['the same campaign and email variant', campaign()],
    ['another campaign', campaign({ campaign_id: 'cmp-0002' })],
    ['an sms variant of that name', campaign({ message_variants: [{ variant_name: 'Variant A', channel: 'sms' }] })],
    ['another variant name', campaign({ message_variants: [{ variant_name: 'Variant B', channel: 'email' }] })],
    ['no variants', campaign({ message_variants: undefined })],
    ['no campaign', null],
  ];
  for (const [name, row] of table) {
    it(`agrees with verifiedCampaignBinding: ${name}`, () => {
      expect(campaignConfirmsBinding(row, BINDING)).toBe(verifiedCampaignBinding(row, BINDING) !== null);
    });
  }

  it('reads the queue binding from its own search, trimmed and exact', () => {
    expect(queueCarriesBinding(queue(BOUND_SEARCH), BINDING)).toBe(true);
    expect(queueCarriesBinding(queue('?campaign_id=cmp-0001'), BINDING)).toBe(false);
    expect(queueCarriesBinding(queue('?campaign_id=cmp-0001&variant_name=Variant%20B'), BINDING)).toBe(false);
    expect(queueCarriesBinding(queue(''), BINDING)).toBe(false);
    expect(queueCarriesBinding(null, BINDING)).toBe(false);
  });

  it('a verified path carries both params', () => {
    expect(boundOfferPath(BINDING)('B-0000000000002')).toBe(
      '/offer-orchestrator/B-0000000000002?campaign_id=cmp-0001&variant_name=Variant+A',
    );
  });
});

describe('useVerifiedOfferBinding outcomes', () => {
  let latest: VerifiedOfferBinding | null = null;
  let mounted: Mounted | null = null;
  let client: QueryClient;

  function Probe(props: { binding: typeof BINDING | null; error?: boolean; queue: QueueContext | null }) {
    const result = useVerifiedOfferBinding(props.binding, props.error ?? false, props.queue);
    useEffect(() => {
      latest = result;
    });
    return null;
  }

  async function render(props: { binding: typeof BINDING | null; error?: boolean; queue: QueueContext | null }) {
    mounted = await mount(<QueryClientProvider client={client}><Probe {...props} /></QueryClientProvider>);
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
  }

  beforeEach(() => {
    client = new QueryClient();
    campaignRead.mockReset();
    campaignRead.mockResolvedValue(campaign());
    latest = null;
  });
  afterEach(() => {
    mounted?.unmount();
    mounted = null;
  });

  it('an unbound offer is unchanged and reads no campaign', async () => {
    await render({ binding: null, queue: queue('?state=IL') });
    expect(latest?.state).toBe('absent');
    expect(latest?.pathFor('B-0000000000002')).toBe('/offer-orchestrator/B-0000000000002');
    expect(campaignRead).not.toHaveBeenCalled();
  });

  it('verified: the campaign confirms it and the queue carries it; the read is the Lead Queue entry', async () => {
    await render({ binding: BINDING, queue: queue(BOUND_SEARCH) });
    expect(latest?.state).toBe('verified');
    expect(latest?.pathFor('B-0000000000002')).toContain('?campaign_id=cmp-0001&variant_name=Variant+A');
    expect(campaignRead).toHaveBeenCalledTimes(1);
    expect(client.getQueryData(queryKeys.campaign('cmp-0001'))).toEqual(campaign());
  });

  it('validating while the campaign read runs', async () => {
    campaignRead.mockReturnValue(new Promise(() => undefined));
    await render({ binding: BINDING, queue: queue(BOUND_SEARCH) });
    expect(latest?.state).toBe('validating');
    expect(latest?.pathFor('B-0000000000002')).toBe('/offer-orchestrator/B-0000000000002');
  });

  it('unverified: a queue that is not bound to the same campaign, without reading it', async () => {
    await render({ binding: BINDING, queue: queue('?state=IL') });
    expect(latest?.state).toBe('unverified');
    expect(campaignRead).not.toHaveBeenCalled();
  });

  it('unverified: the campaign does not confirm the variant, or the read fails', async () => {
    campaignRead.mockResolvedValue(campaign({ message_variants: [{ variant_name: 'Variant B', channel: 'email' }] }));
    await render({ binding: BINDING, queue: queue(BOUND_SEARCH) });
    expect(latest?.state).toBe('unverified');
    mounted?.unmount();
    client = new QueryClient();
    campaignRead.mockRejectedValue(new Error('down'));
    await render({ binding: BINDING, queue: queue(BOUND_SEARCH) });
    expect(latest?.state).toBe('unverified');
  });

  it('unverified: a half binding fails closed', async () => {
    await render({ binding: null, error: true, queue: queue(BOUND_SEARCH) });
    expect(latest?.state).toBe('unverified');
  });
});
