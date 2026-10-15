/**
 * @vitest-environment happy-dom
 *
 * The sales-loop mutations (W5d w5-sales-loop). The transport's postJson is
 * the only thing mocked, so the request BODIES are the ones the real
 * salesLoopApi builds. Pinned: the releases run on a bare MutationObserver
 * under ['mip','sales','release'] / ['mip','sales','release-distribution']
 * with networkMode 'always' and retry false; the Board's dry run is keyed
 * OUTSIDE ['mip','sales'] (no own write, no salesBusy) and carries no
 * request id; the commit carries the fingerprint and the intent's request id.
 */
import { QueryClient } from '@tanstack/react-query';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const postJson = vi.hoisted(() => vi.fn());

vi.mock('../apiTransport', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../apiTransport')>();
  return { ...actual, postJson };
});

import {
  distributionPreviewKey,
  releaseAssignmentWrite,
  releaseDistributionWrite,
  useCommitDistribution,
  useDistributionPreview,
} from './salesLoop';
import { salesMutationKeys } from './sales';

let root: Root;
let client: QueryClient;

function mountHook<T>(useHook: () => T): () => T {
  let value: T | undefined;
  function Probe() {
    value = useHook();
    return null;
  }
  act(() => root.render(createElement(Probe)));
  return () => {
    if (value === undefined) throw new Error('hook did not render');
    return value;
  };
}

function mutationOptions(key: readonly string[]) {
  const [mutation] = client.getMutationCache().findAll({ mutationKey: key, exact: true });
  return mutation?.options;
}

beforeEach(() => {
  vi.clearAllMocks();
  document.body.innerHTML = '<div id="root"></div>';
  root = createRoot(document.getElementById('root') as HTMLElement);
  client = new QueryClient();
  postJson.mockResolvedValue({ ok: true });
});

afterEach(() => {
  act(() => root.unmount());
  client.clear();
  document.body.innerHTML = '';
});

describe('the release writes', () => {
  it('post one release under the release key, offline-safe and never retried by TanStack', async () => {
    await releaseAssignmentWrite(client, { borrowerId: 'B-AAAAAAAAAAAA1', assignmentId: 'asg-1', requestId: 'req-1' });

    expect(postJson).toHaveBeenCalledTimes(1);
    expect(postJson).toHaveBeenCalledWith(
      '/api/leads/B-AAAAAAAAAAAA1/assignment/release',
      { assignment_id: 'asg-1', request_id: 'req-1' },
      undefined,
    );
    const options = mutationOptions(salesMutationKeys.release);
    expect(options?.networkMode).toBe('always');
    expect(options?.retry).toBe(false);
  });

  it('posts the distribution release with the run id under its own sales key', async () => {
    await releaseDistributionWrite(client, { distributionRequestId: 'run-1', requestId: 'req-2' });

    expect(postJson).toHaveBeenCalledWith(
      '/api/sales/distribute/release',
      { distribution_request_id: 'run-1', request_id: 'req-2' },
      undefined,
    );
    expect(salesMutationKeys.releaseDistribution).toEqual(['mip', 'sales', 'release-distribution']);
    expect(mutationOptions(salesMutationKeys.releaseDistribution)?.retry).toBe(false);
  });

  it('rejects with the server error so the toast can read its reason', async () => {
    postJson.mockRejectedValueOnce(new Error('refused'));
    await expect(
      releaseAssignmentWrite(client, { borrowerId: 'B-AAAAAAAAAAAA1', assignmentId: 'asg-1', requestId: 'req-3' }),
    ).rejects.toThrow('refused');
  });
});

describe('the Board', () => {
  it('previews outside the sales keys with no request id', async () => {
    const preview = mountHook(() => useDistributionPreview(client));
    await act(async () => {
      await preview().mutateAsync({ borrowerIds: [], loEmails: ['lo01@summit.example'] });
    });

    expect(distributionPreviewKey).toEqual(['mip', 'distribution-preview']);
    expect(distributionPreviewKey.slice(0, 2)).not.toEqual(salesMutationKeys.all);
    expect(client.isMutating({ mutationKey: salesMutationKeys.all })).toBe(0);
    const [, body] = postJson.mock.calls[0] as [string, Record<string, unknown>];
    expect(body).toEqual({
      borrower_ids: [],
      lo_emails: ['lo01@summit.example'],
      strategy: 'score_balanced',
      expires_in_hours: 24,
      dry_run: true,
    });
    expect(body).not.toHaveProperty('request_id');
  });

  it('commits under the distribute key with the fingerprint and the request id', async () => {
    const commit = mountHook(() => useCommitDistribution(client));
    await act(async () => {
      await commit().mutateAsync({
        borrowerIds: [],
        loEmails: ['lo01@summit.example'],
        fingerprint: 'f'.repeat(64),
        requestId: 'req-commit',
      });
    });

    const [path, body] = postJson.mock.calls[0] as [string, Record<string, unknown>];
    expect(path).toBe('/api/sales/distribute');
    expect(body).toMatchObject({ strategy: 'score_balanced', expected_plan_fingerprint: 'f'.repeat(64), request_id: 'req-commit' });
    expect(body).not.toHaveProperty('dry_run');
    const options = mutationOptions(salesMutationKeys.distribute);
    expect(options?.networkMode).toBe('always');
    expect(options?.retry).toBe(false);
  });
});
