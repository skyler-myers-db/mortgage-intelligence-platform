/**
 * @vitest-environment happy-dom
 *
 * useBorrowerProof (critic fix 16): every /proof read writes a
 * VIEW_BORROWER_PROOF audit row, so after the one explicit read nothing
 * passive may reach the network again: not a remount 31 s later, not the
 * invalidation every approve applies, not a reconnect, not an enable flip,
 * not a borrower A -> B -> A round trip. Runs on the app's own query client
 * (createMipQueryClient), whose refetchOnReconnect is still the default.
 *
 * Mutation checks (lane report): staleTime Infinity turns the enable-flip and
 * A -> B -> A cases red (an invalidated query is stale at any finite or
 * infinite staleTime); staleTime 30_000 turns the remount case red.
 */
import { onlineManager, type QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest';
import { api } from '../../lib/api';
import { createMipQueryClient } from '../../lib/queryClient';
import { invalidateOperationalQueries, queryKeys } from '../../lib/queryKeys';
import type { BorrowerProof } from '../../types';
import { useBorrowerProof } from './useBorrowerProof';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function proofFor(borrowerId: string): BorrowerProof {
  const formula = { label: 'f', expression: 'e', result: 'r', source: null };
  return {
    borrower_id: borrowerId,
    trusted: true,
    known_data_gaps: [],
    generated_from: 'mip.gold.borrower_dossier + mip.gold.lead_scores',
    source_refresh_at: null,
    opportunity_score: 88,
    signal_strength: 80,
    signal_strength_note: 'n',
    evidence_confidence_note: 'n',
    score_components: [],
    score_formula: formula,
    signal_strength_formula: formula,
    rate_spread_formula: formula,
    equity_formula: formula,
    ltv_formula: formula,
    offer_code: 'refi',
    offer_label: 'Refinance review',
    offer_branches: [],
    evidence_rows: [],
    source_assets: [],
    reproduce: [],
  };
}

function Probe({ borrowerId, enabled }: { borrowerId: string; enabled: boolean }) {
  const query = useBorrowerProof(borrowerId, enabled);
  return <output data-testid="probe">{query.data?.borrower_id ?? 'none'}</output>;
}

describe('useBorrowerProof', () => {
  let container: HTMLDivElement;
  let root: Root;
  let client: QueryClient;
  let proofSpy: MockInstance<typeof api.borrowerProof>;

  beforeEach(() => {
    vi.useFakeTimers();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    client = createMipQueryClient();
    proofSpy = vi.spyOn(api, 'borrowerProof').mockImplementation(async (id: string) => proofFor(id));
  });

  afterEach(() => {
    act(() => root.unmount());
    client.clear();
    container.remove();
    onlineManager.setOnline(true);
    proofSpy.mockRestore();
    vi.useRealTimers();
  });

  async function render(borrowerId: string, enabled: boolean) {
    await act(async () => {
      root.render(
        <QueryClientProvider client={client}>
          <Probe borrowerId={borrowerId} enabled={enabled} />
        </QueryClientProvider>,
      );
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
  }

  function remountRoot() {
    act(() => root.unmount());
    root = createRoot(container);
  }

  async function approveElsewhere(advanceMs = 31_000) {
    await act(async () => {
      await invalidateOperationalQueries(client);
      await vi.advanceTimersByTimeAsync(advanceMs);
    });
  }

  function shown(): string {
    return container.querySelector('[data-testid="probe"]')?.textContent ?? '';
  }

  it('an enabled mount reads once; an invalidated remount 31 s later reads nothing, enabled or not', async () => {
    await render('B-A', true);
    expect(proofSpy).toHaveBeenCalledTimes(1);
    expect(shown()).toBe('B-A');
    expect(client.getQueryState(queryKeys.borrowerProof('B-A'))?.isInvalidated).toBe(false);

    await approveElsewhere();
    expect(client.getQueryState(queryKeys.borrowerProof('B-A'))?.isInvalidated).toBe(true);

    remountRoot();
    await render('B-A', true);
    remountRoot();
    await render('B-A', false);

    expect(proofSpy).toHaveBeenCalledTimes(1);
    expect(shown()).toBe('B-A');
  });

  it('a reconnect with an enabled observer reads nothing', async () => {
    await render('B-A', true);
    await approveElsewhere();
    await act(async () => {
      onlineManager.setOnline(false);
      await vi.advanceTimersByTimeAsync(1_000);
      onlineManager.setOnline(true);
      await vi.advanceTimersByTimeAsync(1_000);
    });
    expect(proofSpy).toHaveBeenCalledTimes(1);
  });

  it('a disabled observer on a cold cache never fetches', async () => {
    await render('B-A', false);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });
    expect(proofSpy).not.toHaveBeenCalled();
    expect(shown()).toBe('none');
  });

  it('(i) the same observer flipping enabled false -> true over an invalidated cache reads nothing', async () => {
    await render('B-A', true);
    await render('B-A', false);
    await approveElsewhere();
    await render('B-A', true);
    expect(proofSpy).toHaveBeenCalledTimes(1);
    expect(shown()).toBe('B-A');
  });

  it('(ii) borrower A -> B -> A with enabled true reads A once', async () => {
    await render('B-A', true);
    await render('B-B', true);
    expect(proofSpy).toHaveBeenCalledTimes(2);
    await approveElsewhere();
    await render('B-A', true);
    expect(proofSpy).toHaveBeenCalledTimes(2);
    expect(proofSpy.mock.calls.filter(([id]) => id === 'B-A')).toHaveLength(1);
    expect(shown()).toBe('B-A');
  });

  it('a client-wide refetchQueries skips the static proof', async () => {
    await render('B-A', true);
    await approveElsewhere();
    await act(async () => {
      await client.refetchQueries({ queryKey: queryKeys.borrowerProof('B-A') });
    });
    // refetchQueries skips static queries, so only an observer's explicit
    // refetch() (the Try again button) can re-read: the passive path is shut.
    expect(proofSpy).toHaveBeenCalledTimes(1);
  });
});
