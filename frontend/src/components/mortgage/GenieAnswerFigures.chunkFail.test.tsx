/**
 * @vitest-environment happy-dom
 *
 * The figure mark chunk fails soft (genie-10 phase 2, W5d fix round 1). The
 * mark is decorative: when its lazy chunk cannot load (a tab opened before a
 * redeploy, a network blip) or resolves to a module without a default (the
 * stale-chunk listener cancelling vite:preloadError makes Vite's preload
 * helper resolve the import to undefined), the verified answer must still
 * render in full, with the figure as plain text, and nothing may reach the
 * route's or the floating panel's error boundary.
 *
 * Its own file because the chunk mock applies to the whole module graph. Each
 * case registers its chunk with vi.doMock and re-imports the renderer after
 * vi.resetModules, so the lazy component is created afresh against that
 * case's chunk. The control case proves the
 * harness waits until the chunk has loaded and rendered: a mark that throws
 * once loaded DOES reach the boundary.
 */
import { Component, act, type ReactNode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { GenieVerifiedClaim } from '../../types';

type ChunkMode = 'rejects' | 'undefinedDefault' | 'throwsOnRender';

/** Factory runs: proves each case really requested ITS chunk. */
const chunk = { loads: 0 };

/**
 * Registers this case's chunk. vi.doMock (not the hoisted vi.mock) because a
 * resolved mock factory is cached across vi.resetModules: re-registering it
 * per case is what makes the next import run this case's factory.
 */
function mockMarkChunk(mode: ChunkMode): void {
  vi.doMock('./GenieFigureMark', () => {
    chunk.loads += 1;
    if (mode === 'rejects') {
      throw new Error('Failed to fetch dynamically imported module: /assets/GenieFigureMark-stale.js');
    }
    if (mode === 'undefinedDefault') return { default: undefined };
    return {
      default: () => {
        throw new Error('the figure mark failed to render');
      },
    };
  });
}

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

/** The first import transforms the renderer's module graph: allow for a loaded machine. */
vi.setConfig({ testTimeout: 30_000 });

const TEXT = 'Texas has 250 borrowers and an average of 185.';
const ITEMS: GenieVerifiedClaim[] = [
  { token: '250', kind: 'number', derivation: 'returned_value', support: 'cell', column: 'borrowers', occurrences: 1, section: null },
];
const BOUNDARY_COPY = 'This panel hit an error.';

class TestBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError(): { failed: boolean } {
    return { failed: true };
  }

  render(): ReactNode {
    return this.state.failed ? <p data-testid="boundary">{BOUNDARY_COPY}</p> : this.props.children;
  }
}

describe('the figure mark chunk', () => {
  let container: HTMLDivElement;
  let root: Root;
  const errors: unknown[] = [];

  beforeEach(() => {
    vi.resetModules();
    errors.length = 0;
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container, {
      onUncaughtError: (error) => errors.push(error),
      onCaughtError: (error) => errors.push(error),
    });
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.doUnmock('./GenieFigureMark');
  });

  /** Renders the scoped answer, then waits for the chunk import and React's retry. */
  async function renderScopedAnswer(mode: ChunkMode): Promise<number> {
    mockMarkChunk(mode);
    const loadsBefore = chunk.loads;
    const { MarkdownAnswer } = await import('./GenieAnswer.markdown');
    const { GenieFigureScopes } = await import('./GenieAnswerFigures');
    await act(async () => {
      root.render(
        <TestBoundary>
          <GenieFigureScopes value={new Map([[TEXT, ITEMS]])}>
            <MarkdownAnswer text={TEXT} workspaceHost={null} />
          </GenieFigureScopes>
        </TestBoundary>,
      );
    });
    await act(async () => {
      await import('./GenieFigureMark').then(
        () => undefined,
        () => undefined,
      );
    });
    for (let tick = 0; tick < 20; tick += 1) {
      await act(async () => {
        await new Promise((resolve) => window.setTimeout(resolve, 5));
      });
    }
    return chunk.loads - loadsBefore;
  }

  function expectPlainVerifiedAnswer(loads: number): void {
    expect(loads, 'the mark chunk was requested').toBeGreaterThan(0);
    expect(container.querySelector('[data-testid="boundary"]'), 'no error boundary tripped').toBeNull();
    expect(errors, 'no render error reached a boundary').toEqual([]);
    expect(container.querySelector('p')?.textContent).toBe(TEXT);
    expect(container.querySelector('mark'), 'the figure stays plain text').toBeNull();
  }

  it('renders the verified answer with plain figures when the chunk fails to load', async () => {
    expectPlainVerifiedAnswer(await renderScopedAnswer('rejects'));
  });

  it('renders the verified answer with plain figures when the chunk resolves without a default', async () => {
    expectPlainVerifiedAnswer(await renderScopedAnswer('undefinedDefault'));
  });

  it('control: a mark that throws once loaded reaches the boundary (the harness waits for the chunk)', async () => {
    const loads = await renderScopedAnswer('throwsOnRender');
    expect(loads).toBeGreaterThan(0);
    expect(container.querySelector('[data-testid="boundary"]')?.textContent).toBe(BOUNDARY_COPY);
    expect(errors.length).toBeGreaterThan(0);
  });
});
