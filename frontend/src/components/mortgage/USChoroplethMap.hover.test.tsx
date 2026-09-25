/**
 * @vitest-environment happy-dom
 */
/**
 * Hover cost on the hero map (audit runtime-07), in the rendered DOM under
 * the production React Compiler configuration (vite.config's babel preset
 * runs in vitest too).
 *
 * A mousemove updates the hover card, so the map re-renders; the state
 * stage (51 paths) must NOT. It only stays memoized while every read the
 * map passes down is referentially stable across renders the query did not
 * cause, which is what useWarmingUpRetry's stable `manualRetry` guarantees
 * (react-query hands back a fresh result object on every render).
 */
import { act, type ComponentProps, type ReactNode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createMipQueryClient } from '../../lib/queryClient';
import type { StateRollupResponse } from '../../types';
import { USChoroplethMap } from './USChoroplethMap';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const mocks = vi.hoisted(() => ({ stateRollups: vi.fn(), zipRollups: vi.fn(), assignmentOverlay: vi.fn(), stageRenders: 0 }));

vi.mock('../../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../lib/api')>()),
  api: { stateRollups: mocks.stateRollups, zipRollups: mocks.zipRollups, assignmentOverlay: mocks.assignmentOverlay },
}));
vi.mock('./USStateMapData', () => ({
  loadUsaStateMap: () =>
    Promise.resolve({
      label: 'United States',
      viewBox: '0 0 100 100',
      locations: [
        { id: 'il', name: 'Illinois', path: 'M0,0L20,0L20,20Z' },
        { id: 'tx', name: 'Texas', path: 'M30,30L50,30L50,50Z' },
      ],
    }),
}));
// Count renders of the state stage, rendering the real component.
vi.mock('./USChoroplethMapStates', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./USChoroplethMapStates')>();
  return {
    USChoroplethMapStates: (props: ComponentProps<typeof actual.USChoroplethMapStates>) => {
      mocks.stageRenders += 1;
      return actual.USChoroplethMapStates(props);
    },
  };
});

const client = createMipQueryClient();
function Providers({ children }: { children: ReactNode }) {
  return (
    <QueryClientProvider client={client}>
      <MemoryRouter>{children}</MemoryRouter>
    </QueryClientProvider>
  );
}

const ROLLUPS: StateRollupResponse = {
  rollups: [
    { state: 'IL', addressable: 9_000, in_the_money: 1, top_tier_opportunities: 1, avg_score: 80 },
    { state: 'TX', addressable: 1_000, in_the_money: 1, top_tier_opportunities: 1, avg_score: 70 },
  ],
  snapshot_date: '2026-07-14',
};

async function settle(): Promise<void> {
  await act(async () => {
    await new Promise((resolve) => window.setTimeout(resolve, 5));
  });
}

describe('USChoroplethMap hover (runtime-07)', () => {
  let root: Root;

  beforeEach(() => {
    document.body.innerHTML = '<div id="root"></div>';
    root = createRoot(document.getElementById('root') as HTMLElement);
    mocks.stateRollups.mockResolvedValue(ROLLUPS);
    mocks.stageRenders = 0;
  });

  afterEach(() => {
    act(() => root.unmount());
    document.body.innerHTML = '';
    client.clear();
    vi.clearAllMocks();
  });

  it('a mousemove moves the card without re-rendering the state stage', async () => {
    await act(async () => root.render(<Providers><USChoroplethMap /></Providers>));
    for (let i = 0; i < 300 && !document.querySelector('path[data-map-unit="tx"].has-data'); i += 1) await settle();
    const texas = document.querySelector<SVGPathElement>('path[data-map-unit="tx"]');
    expect(texas?.classList.contains('has-data')).toBe(true);

    await act(async () => {
      texas?.dispatchEvent(new MouseEvent('mouseover', { bubbles: true, clientX: 300, clientY: 200 }));
    });
    const tip = () => document.querySelector<HTMLElement>('.map-tip');
    expect(tip()?.textContent).toContain('Texas');
    const rendersWithCardOpen = mocks.stageRenders;

    const lefts: string[] = [];
    for (let x = 310; x <= 350; x += 10) {
      await act(async () => {
        texas?.dispatchEvent(new MouseEvent('mousemove', { bubbles: true, clientX: x, clientY: 200 }));
      });
      lefts.push(tip()?.style.left ?? '');
    }
    // The card follows the pointer...
    expect(new Set(lefts).size).toBe(lefts.length);
    // ...and the 51-path stage is not rendered again for it.
    expect(mocks.stageRenders).toBe(rendersWithCardOpen);
  });
});
