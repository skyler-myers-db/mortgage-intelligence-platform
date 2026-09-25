/**
 * @vitest-environment happy-dom
 */
/**
 * The hero map keeps the previous cohort, visibly (audit runtime-06, map
 * slice), proven in the rendered DOM: while a changed cohort reads, the
 * previous fill stays painted under an "Updating…" pill (the stage and the
 * legend carry `.stable-refresh-region.is-updating`, `.map-status` says so
 * once), no state flips to is-loading, and the new classes land when the
 * read resolves.
 */
import { act, type ReactNode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createMipQueryClient } from '../../lib/queryClient';
import type { StateRollupResponse } from '../../types';
import { USChoroplethMap } from './USChoroplethMap';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const mocks = vi.hoisted(() => ({
  stateRollups: vi.fn(),
  zipRollups: vi.fn(),
  assignmentOverlay: vi.fn(),
}));

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

/** Router + one query cache per test (the rollups are react-query reads). */
let client = createMipQueryClient();
function Providers({ children }: { children: ReactNode }) {
  return (
    <QueryClientProvider client={client}>
      <MemoryRouter>{children}</MemoryRouter>
    </QueryClientProvider>
  );
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

async function settle(): Promise<void> {
  await act(async () => {
    await new Promise((resolve) => window.setTimeout(resolve, 5));
  });
}

async function until(check: () => boolean): Promise<void> {
  for (let i = 0; i < 300 && !check(); i += 1) await settle();
  expect(check()).toBe(true);
}

function rollups(il: number, tx: number): StateRollupResponse {
  return {
    rollups: [
      { state: 'IL', addressable: il, in_the_money: 1, top_tier_opportunities: 1, avg_score: 80 },
      { state: 'TX', addressable: tx, in_the_money: 1, top_tier_opportunities: 1, avg_score: 70 },
    ],
    snapshot_date: '2026-07-14',
  };
}

const cls = (id: string) => document.querySelector(`path[data-map-unit="${id}"]`)?.getAttribute('data-map-class');

describe('USChoroplethMap cohort refresh', () => {
  let root: Root;

  beforeEach(() => {
    document.body.innerHTML = '<div id="root"></div>';
    root = createRoot(document.getElementById('root') as HTMLElement);
    client = createMipQueryClient();
    mocks.stateRollups.mockResolvedValue(rollups(9_000, 1_000));
  });

  afterEach(() => {
    act(() => root.unmount());
    document.body.innerHTML = '';
    vi.clearAllMocks();
  });

  it('keeps the previous cohort painted under an Updating pill while the new one reads', async () => {
    const rerender = (filter?: string[]) =>
      act(async () => root.render(<Providers><USChoroplethMap segmentFilter={filter} /></Providers>));
    await rerender();
    await until(() => cls('tx') === '2');
    const pending = deferred<StateRollupResponse>();
    mocks.stateRollups.mockReturnValueOnce(pending.promise);

    await rerender(['itm']);
    await until(() => document.querySelector('.map-levels.is-updating') !== null);
    expect(document.querySelector('.stable-refresh-status')?.textContent).toBe('Updating…');
    expect(document.querySelector('.map-legend')?.classList.contains('is-updating')).toBe(true);
    expect(document.querySelector('.map-status')?.textContent).toBe('Updating state borrower rollups.');
    expect(document.querySelectorAll('path.is-loading')).toHaveLength(0);
    expect(cls('tx')).toBe('2');

    await act(async () => pending.resolve(rollups(1_000, 9_000)));
    await until(() => cls('tx') === '4');
    expect(document.querySelector('.map-levels.is-updating')).toBeNull();
    expect(document.querySelector('.stable-refresh-status')).toBeNull();
  });
});
