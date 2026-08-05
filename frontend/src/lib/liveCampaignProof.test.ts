import { describe, expect, it, vi } from 'vitest';

import {
  archiveLiveCampaign,
  reconcileGenieCampaignAction,
  type LiveCampaignRequest,
  type LiveCampaignResponse,
} from './liveCampaignProof';

function response(status: number, body: unknown, unreadable = false): LiveCampaignResponse {
  return {
    status: () => status,
    json: async () => {
      if (unreadable) throw new Error('truncated response');
      return body;
    },
  };
}

function request(overrides: Partial<LiveCampaignRequest>): LiveCampaignRequest {
  const unexpected = async (): Promise<LiveCampaignResponse> => {
    throw new Error('unexpected request');
  };
  return {
    get: unexpected,
    patch: unexpected,
    post: unexpected,
    ...overrides,
  };
}

describe('live campaign proof recovery', () => {
  it('recovers the exact campaign ID on the final replay after loss, 5xx, and malformed 200s', async () => {
    const post = vi.fn()
      .mockRejectedValueOnce(new Error('response lost'))
      .mockResolvedValueOnce(response(503, { detail: 'temporary' }))
      .mockResolvedValueOnce(response(200, {}, true))
      .mockResolvedValueOnce(response(200, 'scalar'))
      .mockResolvedValueOnce(response(200, { campaign_id: 'campaign-final' }));
    const sleep = vi.fn(async () => undefined);

    await expect(reconcileGenieCampaignAction(request({ post }), {
      apiUrl: 'https://app.example',
      authHeaders: { Authorization: 'Bearer owner' },
      submittedPayload: { request_id: 'signed-request' },
      attempts: 5,
      sleep,
    })).resolves.toBe('campaign-final');

    expect(post).toHaveBeenCalledTimes(5);
    expect(sleep).toHaveBeenCalledTimes(4);
    expect(post.mock.calls[4]?.[1]?.data).toEqual({ request_id: 'signed-request' });
  });

  it('confirms a scalar final PATCH success with a same-attempt GET', async () => {
    const get = vi.fn()
      .mockResolvedValueOnce(response(200, { name: 'Genie strategy draft ghabcdearf', status: 'draft' }))
      .mockResolvedValueOnce(response(200, { name: 'Genie strategy draft ghabcdearf', status: 'archived' }));
    const patch = vi.fn().mockResolvedValueOnce(response(200, 'scalar'));

    await expect(archiveLiveCampaign(request({ get, patch }), {
      adminBearer: 'admin',
      apiUrl: 'https://app.example',
      campaignId: 'campaign-id',
      expectedName: 'Genie strategy draft ghabcdearf',
      attempts: 1,
      sleep: async () => undefined,
    })).resolves.toBeUndefined();

    expect(get).toHaveBeenCalledTimes(2);
    expect(patch).toHaveBeenCalledTimes(1);
  });

  it('reconciles a committed final PATCH when its response is lost', async () => {
    const get = vi.fn()
      .mockResolvedValueOnce(response(200, { name: 'Genie strategy draft ghabcdearf', status: 'draft' }))
      .mockResolvedValueOnce(response(200, { name: 'Genie strategy draft ghabcdearf', status: 'archived' }));
    const patch = vi.fn().mockRejectedValueOnce(new Error('response lost after commit'));

    await expect(archiveLiveCampaign(request({ get, patch }), {
      adminBearer: 'admin',
      apiUrl: 'https://app.example',
      campaignId: 'campaign-id',
      expectedName: 'Genie strategy draft ghabcdearf',
      attempts: 1,
      sleep: async () => undefined,
    })).resolves.toBeUndefined();

    expect(get).toHaveBeenCalledTimes(2);
    expect(patch).toHaveBeenCalledTimes(1);
  });

  it('archives the exact ID before surfacing a missing persisted run marker', async () => {
    const get = vi.fn()
      .mockResolvedValueOnce(response(200, { name: 'Genie strategy draft', status: 'draft' }))
      .mockResolvedValueOnce(response(200, { name: 'Genie strategy draft', status: 'archived' }));
    const patch = vi.fn().mockRejectedValueOnce(new Error('response lost after commit'));

    await expect(archiveLiveCampaign(request({ get, patch }), {
      adminBearer: 'admin',
      apiUrl: 'https://app.example',
      campaignId: 'campaign-id',
      expectedName: 'Genie strategy draft ghabcdearf',
      attempts: 1,
      sleep: async () => undefined,
    })).rejects.toThrow('live campaign marker was not persisted');

    expect(patch).toHaveBeenCalledTimes(1);
    expect(get).toHaveBeenCalledTimes(2);
  });
});
