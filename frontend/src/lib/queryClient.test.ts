import { describe, expect, it, vi } from 'vitest';
import { QueryClient } from '@tanstack/react-query';
import {
  createMipQueryClient,
  DEFAULT_QUERY_GC_MS,
  DEFAULT_QUERY_STALE_MS,
} from './queryClient';
import { invalidateOperationalQueries, queryKeys } from './queryKeys';
import { ApiError } from './api';

describe('createMipQueryClient', () => {
  it('uses bounded freshness defaults and disables automatic focus refetch', () => {
    const client = createMipQueryClient();
    const queries = client.getDefaultOptions().queries;

    expect(queries?.staleTime).toBe(DEFAULT_QUERY_STALE_MS);
    expect(queries?.gcTime).toBe(DEFAULT_QUERY_GC_MS);
    expect(queries?.refetchOnWindowFocus).toBe(false);
    expect(client.getDefaultOptions().mutations?.retry).toBe(false);
  });

  it('retries warming-up and breaker-open dependency states, but not exhausted retries', () => {
    const client = createMipQueryClient();
    const retry = client.getDefaultOptions().queries?.retry;
    expect(typeof retry).toBe('function');

    const warming = new ApiError('warming', {
      path: '/api/segments',
      status: 503,
      retryable: true,
      dependency: 'warehouse',
      reason: 'warming_up',
    });
    const breaker = new ApiError('cooling', {
      path: '/api/segments',
      status: 503,
      retryable: true,
      dependency: 'warehouse',
      reason: 'breaker_open',
    });
    const exhausted = new ApiError('exhausted', {
      path: '/api/segments',
      status: 503,
      retryable: true,
      dependency: 'warehouse',
      reason: 'retries_exhausted',
    });
    const rateLimited = new ApiError('rate limited', {
      path: '/api/borrowers/B-TEST',
      status: 429,
      retryable: true,
      dependency: 'warehouse',
      reason: 'rate_limited',
    });

    expect((retry as (count: number, error: Error) => boolean)(0, warming)).toBe(true);
    expect((retry as (count: number, error: Error) => boolean)(5, warming)).toBe(false);
    expect((retry as (count: number, error: Error) => boolean)(0, breaker)).toBe(true);
    expect((retry as (count: number, error: Error) => boolean)(1, breaker)).toBe(false);
    expect((retry as (count: number, error: Error) => boolean)(0, exhausted)).toBe(false);
    expect((retry as (count: number, error: Error) => boolean)(0, rateLimited)).toBe(false);
  });
});

describe('invalidateOperationalQueries', () => {
  it('invalidates only operational data families, not static config or Genie answers', async () => {
    const client = new QueryClient();
    const invalidate = vi
      .spyOn(client, 'invalidateQueries')
      .mockImplementation(() => Promise.resolve());

    await invalidateOperationalQueries(client);

    expect(invalidate.mock.calls.map(([arg]) => arg)).toEqual([
      { queryKey: ['mip', 'leads'], refetchType: 'none' },
      { queryKey: ['mip', 'borrower'], refetchType: 'none' },
      { queryKey: ['mip', 'sales'], refetchType: 'none' },
      { queryKey: ['mip', 'audit'], refetchType: 'none' },
      { queryKey: ['mip', 'portfolio'], refetchType: 'none' },
      { queryKey: ['mip', 'segments'], refetchType: 'none' },
      { queryKey: ['mip', 'analytics'], refetchType: 'none' },
    ]);

    const invalidated = invalidate.mock.calls
      .map(([arg]) => arg?.queryKey)
      .filter((key): key is NonNullable<typeof key> => key !== undefined);
    expect(invalidated).not.toContainEqual(queryKeys.footprint());
    expect(invalidated).not.toContainEqual(queryKeys.configOptions());
    expect(invalidated).not.toContainEqual(queryKeys.campaigns());
    expect(invalidated).not.toContainEqual(queryKeys.workspace());
    expect(invalidated).not.toContainEqual(queryKeys.genieStart());
  });
});
