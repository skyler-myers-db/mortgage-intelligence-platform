/**
 * @vitest-environment happy-dom
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { useWarmingUpRetry } from './useWarmingUpRetry';
import { ApiError } from './api';

type Resolver = (value: string) => void;

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function makeClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        gcTime: Infinity,
      },
    },
  });
}

function Probe({
  queryId,
  resolvers,
}: {
  queryId: string;
  resolvers: Record<string, Resolver>;
}) {
  const result = useWarmingUpRetry<string>(
    () =>
      new Promise<string>((resolve) => {
        resolvers[queryId] = resolve;
      }),
    [queryId],
    {
      queryKey: ['keep-previous-probe', queryId],
      keepPreviousData: true,
    },
  );

  return (
    <div data-placeholder={result.isPlaceholderData ? 'true' : 'false'}>
      {result.data ?? 'empty'}
    </div>
  );
}

/** The function form: keep only when the previous key's group matches this one's. */
function KeyAwareProbe({
  group,
  queryId,
  resolvers,
}: {
  group: string;
  queryId: string;
  resolvers: Record<string, Resolver>;
}) {
  const result = useWarmingUpRetry<string>(
    () =>
      new Promise<string>((resolve) => {
        resolvers[`${group}:${queryId}`] = resolve;
      }),
    [],
    {
      queryKey: ['key-aware-probe', group, queryId],
      keepPreviousWhen: (previousKey) => previousKey[1] === group,
    },
  );
  return (
    <div data-placeholder={result.isPlaceholderData ? 'true' : 'false'}>
      {result.data ?? 'empty'}
    </div>
  );
}

function WarmingProbe({ queryId }: { queryId: 'first' | 'second' }) {
  const result = useWarmingUpRetry<string>(
    () => {
      if (queryId === 'first') return Promise.resolve('first payload');
      return Promise.reject(
        new ApiError('warehouse warming', {
          path: '/api/segments',
          status: 503,
          retryable: true,
          dependency: 'warehouse',
          reason: 'warming_up',
        }),
      );
    },
    [queryId],
    {
      queryKey: ['keep-previous-warming-probe', queryId],
      keepPreviousData: true,
      maxAttempts: 2,
    },
  );

  return (
    <div data-warming={result.warmingUp?.label ?? ''}>
      {result.data ?? 'empty'}
    </div>
  );
}

async function waitFor(assertion: () => void) {
  let lastError: unknown = null;
  for (let i = 0; i < 20; i += 1) {
    try {
      assertion();
      return;
    } catch (err) {
      lastError = err;
      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 0));
      });
    }
  }
  throw lastError;
}

describe('useWarmingUpRetry keepPreviousData', () => {
  let container: HTMLDivElement;
  let root: Root;
  let client: QueryClient;
  let resolvers: Record<string, Resolver>;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    client = makeClient();
    resolvers = {};
  });

  afterEach(() => {
    act(() => root.unmount());
    client.clear();
    container.remove();
  });

  it('keeps the previous payload mounted while a changed key is still fetching', async () => {
    await act(async () => {
      root.render(
        <QueryClientProvider client={client}>
          <Probe queryId="first" resolvers={resolvers} />
        </QueryClientProvider>,
      );
    });
    await waitFor(() => {
      expect(typeof resolvers.first).toBe('function');
    });

    await act(async () => {
      resolvers.first('first payload');
      await Promise.resolve();
    });
    await waitFor(() => {
      expect(container.textContent).toBe('first payload');
    });
    expect(container.firstElementChild?.getAttribute('data-placeholder')).toBe('false');

    await act(async () => {
      root.render(
        <QueryClientProvider client={client}>
          <Probe queryId="second" resolvers={resolvers} />
        </QueryClientProvider>,
      );
    });
    await waitFor(() => {
      expect(typeof resolvers.second).toBe('function');
    });

    expect(container.textContent).toBe('first payload');
    expect(container.firstElementChild?.getAttribute('data-placeholder')).toBe('true');

    await act(async () => {
      resolvers.second('second payload');
      await Promise.resolve();
    });
    await waitFor(() => {
      expect(container.textContent).toBe('second payload');
    });
    expect(container.firstElementChild?.getAttribute('data-placeholder')).toBe('false');
  });

  it('function form keeps the previous payload only when the predicate accepts its key', async () => {
    const render = (group: string, queryId: string) =>
      act(async () => {
        root.render(
          <QueryClientProvider client={client}>
            <KeyAwareProbe group={group} queryId={queryId} resolvers={resolvers} />
          </QueryClientProvider>,
        );
      });
    const probe = () => container.firstElementChild;

    await render('tx', 'cohort-1');
    await waitFor(() => expect(typeof resolvers['tx:cohort-1']).toBe('function'));
    await act(async () => {
      resolvers['tx:cohort-1']('TX tiles, cohort 1');
      await Promise.resolve();
    });
    await waitFor(() => expect(container.textContent).toBe('TX tiles, cohort 1'));

    // Same group, new cohort: the previous payload stays up as a placeholder.
    await render('tx', 'cohort-2');
    await waitFor(() => expect(typeof resolvers['tx:cohort-2']).toBe('function'));
    expect(container.textContent).toBe('TX tiles, cohort 1');
    expect(probe()?.getAttribute('data-placeholder')).toBe('true');
    await act(async () => {
      resolvers['tx:cohort-2']('TX tiles, cohort 2');
      await Promise.resolve();
    });
    await waitFor(() => expect(container.textContent).toBe('TX tiles, cohort 2'));

    // Another group: the predicate rejects the previous key, so nothing of
    // the other group's payload is ever painted under this one.
    await render('ca', 'cohort-2');
    await waitFor(() => expect(typeof resolvers['ca:cohort-2']).toBe('function'));
    expect(container.textContent).toBe('empty');
    expect(probe()?.getAttribute('data-placeholder')).toBe('false');
  });

  it('keeps previous payload and still exposes warming state when the changed key returns retryable 503', async () => {
    await act(async () => {
      root.render(
        <QueryClientProvider client={client}>
          <WarmingProbe queryId="first" />
        </QueryClientProvider>,
      );
    });
    await waitFor(() => {
      expect(container.textContent).toBe('first payload');
    });
    expect(container.firstElementChild?.getAttribute('data-warming')).toBe('');

    await act(async () => {
      root.render(
        <QueryClientProvider client={client}>
          <WarmingProbe queryId="second" />
        </QueryClientProvider>,
      );
    });

    await waitFor(() => {
      expect(container.textContent).toBe('first payload');
      expect(container.firstElementChild?.getAttribute('data-warming')?.toLowerCase()).toContain('warming');
    });
  });
});
