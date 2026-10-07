/**
 * @vitest-environment happy-dom
 */
import { QueryClientProvider, focusManager, type QueryClient } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { DEFAULT_QUERY_STALE_MS, createMipQueryClient } from './queryClient';
import { useWarmingUpRetry } from './useWarmingUpRetry';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

/**
 * No passive refetch on window focus (2026-09-21 audit runtime-08; the
 * D-audit-reads-c3 rule that a ledger read is never polled or prefetched).
 *
 * The app's QueryClient defaults `refetchOnWindowFocus` to false because many
 * reads write audit rows (GET /api/leads -> VIEW_LEADS, the audit explorer ->
 * VIEW_AUDIT_LEDGER). useWarmingUpRetry used to pass
 * `refetchOnWindowFocus: opts.refetchOnWindowFocus` even when the caller set
 * nothing: TanStack spreads a query's own options over the defaults, so the
 * explicit `undefined` replaced the default `false`, and an undefined value
 * means "refetch when stale". Every stale useWarmingUpRetry read re-ran, and
 * re-audited, on the next focus. These run against the real
 * createMipQueryClient, the layer the default lives in.
 */

let root: Root;
let queryClient: QueryClient;
let calls: number;

function Probe({ refetchOnWindowFocus }: { refetchOnWindowFocus?: boolean }) {
  const { data } = useWarmingUpRetry<number>(
    async () => {
      calls += 1;
      return calls;
    },
    {
      queryKey: ['focus-probe', refetchOnWindowFocus ?? 'unset'],
      ...(refetchOnWindowFocus !== undefined && { refetchOnWindowFocus }),
    },
  );
  return <div data-testid="value">{data ?? ''}</div>;
}

async function advance(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

async function renderProbe(refetchOnWindowFocus?: boolean) {
  await act(async () => {
    root.render(
      <QueryClientProvider client={queryClient}>
        <Probe refetchOnWindowFocus={refetchOnWindowFocus} />
      </QueryClientProvider>,
    );
  });
  await advance(1);
}

async function focusAfterGoingStale() {
  await advance(DEFAULT_QUERY_STALE_MS + 1_000);
  await act(async () => {
    focusManager.setFocused(false);
    focusManager.setFocused(true);
  });
  await advance(1);
}

beforeEach(() => {
  vi.useFakeTimers();
  calls = 0;
  document.body.innerHTML = '<div id="root"></div>';
  root = createRoot(document.getElementById('root') as HTMLElement);
  queryClient = createMipQueryClient();
});

afterEach(() => {
  act(() => root.unmount());
  queryClient.clear();
  focusManager.setFocused(undefined);
  vi.useRealTimers();
  document.body.innerHTML = '';
});

describe('useWarmingUpRetry window focus', () => {
  it('does not refetch a stale read on window focus when the caller sets no focus option', async () => {
    await renderProbe();
    expect(calls).toBe(1);

    await focusAfterGoingStale();

    expect(calls, 'a focus must not re-run (and re-audit) the read').toBe(1);
    expect(document.querySelector('[data-testid="value"]')?.textContent).toBe('1');
  });

  it('still refetches on focus when a caller opts in explicitly', async () => {
    await renderProbe(true);
    expect(calls).toBe(1);

    await focusAfterGoingStale();

    expect(calls).toBe(2);
  });
});
