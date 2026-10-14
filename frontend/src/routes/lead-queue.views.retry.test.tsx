/**
 * @vitest-environment happy-dom
 *
 * The Saved views chunk fails ONCE, then loads (tables-06 nit; W5e). The
 * placeholder says "Saved views unavailable" with a Retry; Retry remounts
 * the loader, whose fresh useLazyModule instance re-attempts the import
 * (lazyModule clears its pending promise after a failure, useLazyModule.ts),
 * and focus stays on Retry until the panel renders, then returns to the
 * trigger. Also pinned on the hook itself, so a rewrite of useLazyModule
 * that makes a failure stick shows here.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { lazyModule, useLazyModule } from '../components/mortgage/useLazyModule';
import { mount } from '../test/render';

const attempts = vi.hoisted(() => ({ count: 0 }));
vi.mock('./lead-queue.savedViews', async (importOriginal) => {
  attempts.count += 1;
  if (attempts.count === 1) throw new Error('Chunk unavailable');
  return importOriginal();
});

vi.mock('../lib/configOptionsQuery', () => {
  const STABLE = { data: { target_lender_refs: ['All'] }, isError: false };
  return { useConfigOptionsQuery: () => STABLE };
});

import { LeadQueueViews } from './lead-queue.views';

async function settle() {
  await act(async () => {
    for (let i = 0; i < 8; i += 1) await new Promise((resolve) => window.setTimeout(resolve, 0));
  });
}

beforeEach(() => {
  vi.stubGlobal('fetch', async () => new Response(JSON.stringify({ saved_views: [] }), {
    status: 200, headers: { 'Content-Type': 'application/json' },
  }));
});
afterEach(() => {
  vi.unstubAllGlobals();
});

const byTestId = (id: string) => document.querySelector<HTMLElement>(`[data-testid="${id}"]`);

describe('Saved views chunk failure', () => {
  it('offers Retry, re-attempts the import and keeps focus on Retry until the panel renders', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    await mount(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={['/lead-queue?state=IL']}>
          <LeadQueueViews searchParams={new URLSearchParams('state=IL')} showAssignedToMe={false} onCopyLink={() => undefined} />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    const trigger = byTestId('lead-queue-saved-views')!;
    await act(async () => trigger.click());
    await settle();
    expect(document.body.textContent).toContain('Saved views unavailable');
    const retry = byTestId('lead-queue-saved-views-retry') as HTMLButtonElement;
    expect(retry).not.toBeNull();
    retry.focus();

    await act(async () => retry.click());
    // The reloading slot's own Retry holds focus while the chunk loads.
    const reloading = byTestId('lead-queue-saved-views-retry');
    expect(reloading).not.toBeNull();
    expect(document.activeElement).toBe(reloading);
    expect(reloading!.getAttribute('aria-disabled')).toBe('true');
    await vi.waitFor(async () => {
      await settle();
      expect(byTestId('lead-queue-saved-views-panel'), document.body.textContent ?? '').not.toBeNull();
    }, { timeout: 10_000 });
    expect(attempts.count).toBe(2);
    expect(byTestId('lead-queue-saved-views-retry')).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });
});

describe('useLazyModule after a failure', () => {
  it('a fresh instance re-attempts the import', async () => {
    let calls = 0;
    const source = lazyModule(async () => {
      calls += 1;
      if (calls === 1) throw new Error('Chunk unavailable');
      return { value: 'loaded' };
    });
    function Probe() {
      const { module, failed } = useLazyModule(source, true);
      return <span data-testid="probe">{module ? module.value : failed ? 'failed' : 'loading'}</span>;
    }
    const mounted = await mount(<Probe key="first" />);
    await settle();
    expect(byTestId('probe')?.textContent).toBe('failed');
    await mounted.rerender(<Probe key="second" />);
    await settle();
    expect(calls).toBe(2);
    expect(byTestId('probe')?.textContent).toBe('loaded');
  });
});
