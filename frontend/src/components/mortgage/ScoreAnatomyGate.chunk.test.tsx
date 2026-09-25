/**
 * @vitest-environment happy-dom
 *
 * ScoreAnatomyGate with a chunk that will not load (a stale deploy): the
 * click shows the plain "could not load" note, never a route-boundary throw,
 * and never enables the audited /proof read. Its own file because the chunk
 * mock applies to the whole module graph of the test file.
 *
 * The read lives in the chunk (ScoreAnatomy.tsx), so it cannot start
 * without it; this pins that no path around the chunk reads either.
 */
import type { QueryClient } from '@tanstack/react-query';
import { QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest';
import { api } from '../../lib/api';
import { createMipQueryClient } from '../../lib/queryClient';
import { sampleProof } from '../../mocks/scoreAnatomyProof';
import { ScoreAnatomyGate } from './ScoreAnatomyGate';
import { SCORE_ANATOMY_COPY } from './scoreAnatomy.copy';

vi.mock('./ScoreAnatomy', () => {
  throw new Error('Failed to fetch dynamically imported module: /assets/ScoreAnatomy-stale.js');
});

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

/** The first import of the Score anatomy chunk transforms its module graph: allow for a loaded machine. */
const WAIT = { timeout: 15_000 };
vi.setConfig({ testTimeout: 30_000 });

describe('ScoreAnatomyGate chunk failure', () => {
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
  });

  it('a chunk that fails to load reads nothing and says to reload', async () => {
    await act(async () => {
      root.render(
        <QueryClientProvider client={client}>
          <ScoreAnatomyGate borrowerId="B-AAAAAAAAAAAAA" variant="spine" />
        </QueryClientProvider>,
      );
    });
    await act(async () => {
      container.querySelector<HTMLButtonElement>('button[aria-controls]')?.click();
    });
    await vi.waitFor(() => expect(container.textContent).toContain(SCORE_ANATOMY_COPY.chunkFailed), WAIT);
    for (let i = 0; i < 10; i += 1) {
      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 0));
      });
    }
    expect(proofSpy).not.toHaveBeenCalled();
    expect(container.querySelector('button[aria-controls]')?.getAttribute('aria-expanded')).toBe('true');
  });
});
