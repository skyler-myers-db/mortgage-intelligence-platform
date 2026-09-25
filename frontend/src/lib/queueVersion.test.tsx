/**
 * @vitest-environment happy-dom
 */
import { QueryClient, QueryClientProvider, focusManager } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { QUEUE_VERSION_KEY, fetchQueueVersion, useQueueVersion } from './queueVersion';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

/**
 * The audit-free queue-version poll (audit states-09): once a minute while
 * enabled, paused while the document is hidden, no retry, no focus refetch,
 * and never a request to /api/leads (which writes a VIEW_LEADS audit row).
 */

const VERSION_PATH = '/api/v1/workspace/queue-version';

let root: Root;
let queryClient: QueryClient;
let paths: string[];
let status = 200;

function Probe({ enabled }: { enabled: boolean }) {
  const query = useQueueVersion({ enabled });
  return <div data-testid="version">{query.data ?? ''}</div>;
}

beforeEach(() => {
  vi.useFakeTimers();
  paths = [];
  status = 200;
  vi.stubGlobal('fetch', async (path: string) => {
    paths.push(path);
    return new Response(JSON.stringify(status === 200 ? { version: 'a'.repeat(32) } : { detail: 'x', retryable: false }), {
      status,
      headers: { 'Content-Type': 'application/json' },
    });
  });
  document.body.innerHTML = '<div id="root"></div>';
  root = createRoot(document.getElementById('root') as HTMLElement);
  queryClient = new QueryClient();
});

afterEach(() => {
  act(() => root.unmount());
  queryClient.clear();
  focusManager.setFocused(undefined);
  vi.unstubAllGlobals();
  vi.useRealTimers();
  document.body.innerHTML = '';
});

async function render(enabled: boolean) {
  await act(async () => {
    root.render(
      <QueryClientProvider client={queryClient}>
        <Probe enabled={enabled} />
      </QueryClientProvider>,
    );
  });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1);
  });
}

async function advance(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

describe('queue version', () => {
  it('reads the audit-free version route, never /api/leads', async () => {
    await expect(fetchQueueVersion()).resolves.toBe('a'.repeat(32));
    expect(paths).toEqual([VERSION_PATH]);
    expect(QUEUE_VERSION_KEY).toEqual(['mip', 'queue-version']);
  });

  it('polls once a minute while enabled', async () => {
    await render(true);
    expect(paths).toEqual([VERSION_PATH]);
    expect(document.querySelector('[data-testid="version"]')?.textContent).toBe('a'.repeat(32));
    await advance(59_000);
    expect(paths).toHaveLength(1);
    await advance(1_000);
    expect(paths).toHaveLength(2);
    await advance(120_000);
    expect(paths).toHaveLength(4);
    expect(paths.every((path) => path === VERSION_PATH)).toBe(true);
  });

  it('makes no request while disabled', async () => {
    await render(false);
    await advance(180_000);
    expect(paths).toEqual([]);
  });

  it('pauses while the document is hidden', async () => {
    await render(true);
    expect(paths).toHaveLength(1);
    await act(async () => {
      focusManager.setFocused(false);
    });
    await advance(180_000);
    expect(paths).toHaveLength(1);
  });

  it('a re-read while a poll is in flight discards the stale answer and aborts no request', async () => {
    // An own write re-reads the version (refetch, cancelRefetch). The poll in
    // flight must not become an aborted request (the bulk-run e2e invariant:
    // no request fails), and its pre-write answer must never show.
    const answers: Array<(version: string) => void> = [];
    const signals: Array<AbortSignal | null> = [];
    vi.stubGlobal('fetch', (path: string, init?: RequestInit) => {
      paths.push(path);
      signals.push(init?.signal ?? null);
      return new Promise<Response>((resolve) => {
        answers.push((version) => resolve(new Response(JSON.stringify({ version }), { status: 200, headers: { 'Content-Type': 'application/json' } })));
      });
    });
    function Rereader() {
      const query = useQueueVersion({ enabled: true });
      return (
        <>
          <div data-testid="version">{query.data ?? ''}</div>
          <button type="button" data-testid="reread" onClick={() => void query.refetch()} />
        </>
      );
    }
    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <Rereader />
        </QueryClientProvider>,
      );
    });
    await act(async () => answers[0]('a'.repeat(32)));
    await advance(60_000);
    expect(paths).toHaveLength(2);

    await act(async () => document.querySelector<HTMLButtonElement>('[data-testid="reread"]')?.click());
    expect(paths).toHaveLength(3);
    await act(async () => answers[2]('c'.repeat(32)));
    await act(async () => answers[1]('b'.repeat(32)));
    await advance(1);

    expect(document.querySelector('[data-testid="version"]')?.textContent).toBe('c'.repeat(32));
    expect(signals.some((signal) => signal?.aborted)).toBe(false);
  });

  it('never retries a failed read and says nothing', async () => {
    status = 503;
    await render(true);
    await advance(10_000);
    expect(paths).toHaveLength(1);
    expect(document.querySelector('[data-testid="version"]')?.textContent).toBe('');
  });
});
