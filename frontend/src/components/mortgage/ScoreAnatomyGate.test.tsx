/**
 * @vitest-environment happy-dom
 *
 * ScoreAnatomyGate: the /proof read writes a VIEW_BORROWER_PROOF audit row,
 * so the gate must read only on an explicit click, and only once its chunk
 * has loaded. Counted with a spy on api.borrowerProof over the app's own
 * query client.
 *
 * Mutation checks (lane report): enabling the gate's cache observer
 * (`useBorrowerProof(borrowerId, false)` -> `true`) fails "collapsed over a
 * cold cache reads nothing and loads nothing"; dropping the click condition
 * from the chunk's observer (`fetchEnabled={choice === true}` ->
 * `fetchEnabled`) fails "a warm, invalidated cache renders expanded without a
 * click, with 0 reads" on its disabled-observer assertion (with staleTime
 * 'static' the extra observer would not read, so the call count alone cannot
 * see it; the integrator correction asks for the query to stay disabled).
 */
import type { QueryClient } from '@tanstack/react-query';
import { QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest';
import { api } from '../../lib/api';
import { createMipQueryClient } from '../../lib/queryClient';
import { invalidateOperationalQueries, queryKeys } from '../../lib/queryKeys';
import { sampleProof } from '../../mocks/scoreAnatomyProof';
import type { ProofScoreComponentKey } from '../../types';
import { ScoreAnatomyGate } from './ScoreAnatomyGate';
import { SCORE_ANATOMY_COPY } from './scoreAnatomy.copy';
import { SCORE_SPINE_COPY } from './scoreSpine.copy';

vi.mock('../AppContext', () => ({
  useApp: () => ({ setDrawer: vi.fn(), showEvidence: true, showConfidence: true }),
}));

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

/** The first import of the Score anatomy chunk transforms its module graph: allow for a loaded machine. */
const WAIT = { timeout: 15_000 };
vi.setConfig({ testTimeout: 30_000 });

const A = 'B-AAAAAAAAAAAAA';
const B = 'B-BBBBBBBBBBBBB';

async function flush(rounds = 6) {
  for (let i = 0; i < rounds; i += 1) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
}

describe('ScoreAnatomyGate', () => {
  let container: HTMLDivElement;
  let root: Root;
  let client: QueryClient;
  let proofSpy: MockInstance<typeof api.borrowerProof>;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    client = createMipQueryClient();
    proofSpy = vi.spyOn(api, 'borrowerProof').mockImplementation(async (id: string) => sampleProof({ borrower_id: id }));
  });

  afterEach(() => {
    act(() => root.unmount());
    client.clear();
    container.remove();
    proofSpy.mockRestore();
    document.querySelectorAll('.drawer-scrim, .proof-drawer').forEach((node) => node.remove());
  });

  async function render(
    borrowerId: string,
    variant: 'spine' | 'margins' = 'spine',
    onOpenComponent?: (key: ProofScoreComponentKey) => void,
  ) {
    await act(async () => {
      root.render(
        <QueryClientProvider client={client}>
          <MemoryRouter>
            <ScoreAnatomyGate borrowerId={borrowerId} variant={variant} onOpenComponent={onOpenComponent} />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
    await flush();
  }

  const toggle = () => container.querySelector<HTMLButtonElement>('button[aria-controls]');
  const region = () => container.querySelector<HTMLElement>('[data-testid="score-anatomy-region"]');

  async function click(element: HTMLElement | null) {
    await act(async () => {
      element?.click();
    });
    await flush();
  }

  it('collapsed over a cold cache reads nothing and loads nothing', async () => {
    await render(A);
    expect(toggle()?.textContent).toBe(SCORE_ANATOMY_COPY.spineToggle);
    expect(toggle()?.getAttribute('aria-expanded')).toBe('false');
    expect(toggle()?.getAttribute('aria-controls')).toBe(region()?.id);
    expect(region()?.hidden).toBe(true);
    expect(region()?.childElementCount).toBe(0);
    await flush(10);
    expect(proofSpy).not.toHaveBeenCalled();
  });

  it('a click reads once, after the chunk loads, and renders the spine and margins', async () => {
    await render(A);
    await click(toggle());
    await vi.waitFor(() => expect(container.querySelector('[data-testid="score-spine"]')).not.toBeNull(), WAIT);
    expect(proofSpy).toHaveBeenCalledTimes(1);
    expect(proofSpy).toHaveBeenCalledWith(A, expect.any(AbortSignal));
    expect(toggle()?.getAttribute('aria-expanded')).toBe('true');
    expect(region()?.hidden).toBe(false);
    expect(container.querySelectorAll('button.score-spine__seg')).toHaveLength(5);
    expect(container.querySelector('[data-testid="score-margins"]')).not.toBeNull();

    // Collapse and re-open: the disclosure never unmounts and reads nothing more.
    await click(toggle());
    expect(toggle()?.getAttribute('aria-expanded')).toBe('false');
    await click(toggle());
    expect(container.querySelector('[data-testid="score-spine"]')).not.toBeNull();
    expect(proofSpy).toHaveBeenCalledTimes(1);
  });

  it('a warm, invalidated cache renders expanded without a click, with 0 reads', async () => {
    client.setQueryData(queryKeys.borrowerProof(A), sampleProof({ borrower_id: A }));
    await act(async () => {
      await invalidateOperationalQueries(client);
    });
    await render(A);
    await vi.waitFor(() => expect(container.querySelector('[data-testid="score-spine"]')).not.toBeNull(), WAIT);
    expect(toggle()?.getAttribute('aria-expanded')).toBe('true');
    await flush(10);
    expect(proofSpy).not.toHaveBeenCalled();
    // Integrator correction: the chunk loading is not a click. Every observer
    // of the proof stays DISABLED until the reviewer clicks.
    const enabledObservers = () =>
      client.getQueryCache().find({ queryKey: queryKeys.borrowerProof(A) })?.observers
        .filter((observer) => observer.options.enabled !== false).length;
    expect(enabledObservers()).toBe(0);

    // Collapse then an explicit re-open: now enabled, and still a cache read.
    await click(toggle());
    await click(toggle());
    await vi.waitFor(() => expect(container.querySelector('[data-testid="score-spine"]')).not.toBeNull(), WAIT);
    expect(enabledObservers()).toBe(1);
    expect(proofSpy).not.toHaveBeenCalled();
  });

  it('an auto-expanded region never reads, even when its cache entry is reset under it', async () => {
    client.setQueryData(queryKeys.borrowerProof(A), sampleProof({ borrower_id: A }));
    await render(A);
    await vi.waitFor(() => expect(container.querySelector('[data-testid="score-spine"]')).not.toBeNull(), WAIT);
    await act(async () => {
      await client.resetQueries({ queryKey: queryKeys.borrowerProof(A) });
    });
    await flush(10);
    expect(proofSpy).not.toHaveBeenCalled();
    expect(toggle()?.getAttribute('aria-expanded')).toBe('false');
  });

  it('the click latch is per borrower: moving to another borrower starts collapsed and cold', async () => {
    await render(A);
    await click(toggle());
    await vi.waitFor(() => expect(proofSpy).toHaveBeenCalledTimes(1), WAIT);
    await render(B);
    expect(toggle()?.getAttribute('aria-expanded')).toBe('false');
    await flush(10);
    expect(proofSpy).toHaveBeenCalledTimes(1);
    // Back to A: its cached proof renders expanded, with no read.
    await render(A);
    await vi.waitFor(() => expect(container.querySelector('[data-testid="score-spine"]')).not.toBeNull(), WAIT);
    expect(proofSpy).toHaveBeenCalledTimes(1);
  });

  it('an error offers Try again, which is the explicit re-read', async () => {
    proofSpy.mockRejectedValueOnce(new Error('proof endpoint down'));
    await render(A);
    await click(toggle());
    await vi.waitFor(() => expect(container.textContent).toContain(SCORE_SPINE_COPY.unavailableTitle), WAIT);
    expect(proofSpy).toHaveBeenCalledTimes(1);
    const retry = [...container.querySelectorAll('button')].find((button) => button.textContent === SCORE_SPINE_COPY.retry);
    await click(retry ?? null);
    await vi.waitFor(() => expect(container.querySelector('[data-testid="score-spine"]')).not.toBeNull(), WAIT);
    expect(proofSpy).toHaveBeenCalledTimes(2);
  });

  it('the margins variant reads the same way and renders the margins only', async () => {
    await render(A, 'margins');
    expect(toggle()?.textContent).toBe(SCORE_ANATOMY_COPY.marginsToggle);
    await click(toggle());
    await vi.waitFor(() => expect(container.querySelector('[data-testid="score-margins"]')).not.toBeNull(), WAIT);
    expect(container.querySelector('[data-testid="score-spine"]')).toBeNull();
    expect(container.querySelector('[data-testid="score-margins-note"]')?.textContent).toBe(SCORE_SPINE_COPY.marginsNote);
    expect(proofSpy).toHaveBeenCalledTimes(1);
  });

  it("a page's own drawer gets the segment; without one the chunk opens its own, over the cache", async () => {
    const onOpen = vi.fn();
    await render(A, 'spine', onOpen);
    await click(toggle());
    await vi.waitFor(() => expect(container.querySelector('button[data-part="fit"]')).not.toBeNull(), WAIT);
    await click(container.querySelector<HTMLButtonElement>('button[data-part="fit"]'));
    expect(onOpen).toHaveBeenCalledWith('fit');
    expect(document.querySelector('.proof-drawer')).toBeNull();

    await render(A);
    await click(container.querySelector<HTMLButtonElement>('button[data-part="relationship"]'));
    const drawer = document.querySelector('.proof-drawer');
    expect(drawer?.classList.contains('is-open')).toBe(true);
    await vi.waitFor(() =>
      expect(document.activeElement?.getAttribute('data-component-key')).toBe('relationship'), WAIT);
    expect(proofSpy).toHaveBeenCalledTimes(1);
  });
});
