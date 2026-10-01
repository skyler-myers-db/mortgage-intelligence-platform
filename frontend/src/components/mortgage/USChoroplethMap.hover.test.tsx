/**
 * @vitest-environment happy-dom
 */
/**
 * Hover cost on the hero map (audit runtime-07, D-dataviz-geo-d1), in the
 * rendered DOM under the production React Compiler configuration
 * (vite.config's babel preset runs in vitest too).
 *
 * The only React state is the hovered unit's id: a pointermove inside one
 * state renders nothing at all (not the map, not the 51-path stage, not the
 * card body) and the tip follows the pointer through `left` / `top` written
 * in a frame. Moving to another state renders the map once and the card body
 * once; the stage never. The stage only stays memoized while every read the
 * map passes down is referentially stable across renders the query did not
 * cause, which is what useWarmingUpRetry's stable `manualRetry` guarantees.
 */
import { Profiler, act, type ComponentProps, type ReactNode } from 'react';
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
  stageRenders: 0,
  bodyRenders: 0,
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
// Count renders of the state stage and of the card body, rendering the real components.
vi.mock('./USChoroplethMapStates', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./USChoroplethMapStates')>();
  return {
    USChoroplethMapStates: (props: ComponentProps<typeof actual.USChoroplethMapStates>) => {
      mocks.stageRenders += 1;
      return actual.USChoroplethMapStates(props);
    },
  };
});
vi.mock('./USChoroplethMapTipBody', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./USChoroplethMapTipBody')>();
  return {
    MapTipBody: (props: ComponentProps<typeof actual.MapTipBody>) => {
      mocks.bodyRenders += 1;
      return actual.MapTipBody(props);
    },
  };
});

const client = createMipQueryClient();
let mapCommits = 0;
function Providers({ children }: { children: ReactNode }) {
  return (
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <Profiler id="map" onRender={() => { mapCommits += 1; }}>{children}</Profiler>
      </MemoryRouter>
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

// requestAnimationFrame, stubbed: frames run only when the test flushes them.
let frames: FrameRequestCallback[] = [];
function flushFrames(): void {
  const pending = frames;
  frames = [];
  for (const callback of pending) callback(0);
}

const path = (id: string) => document.querySelector<SVGPathElement>(`path[data-map-unit="${id}"]`);
const tip = () => document.querySelector<HTMLElement>('.map-tip');
const pointer = (type: string, target: Element, x: number, y = 200, init: PointerEventInit = {}) =>
  act(async () => {
    target.dispatchEvent(new PointerEvent(type, { bubbles: true, clientX: x, clientY: y, ...init }));
  });

describe('USChoroplethMap delegated hover (runtime-07, D-dataviz-geo-d1)', () => {
  let root: Root;
  const proto = HTMLElement.prototype as HTMLElement & { showPopover?: () => void };
  const hadShowPopover = Object.prototype.hasOwnProperty.call(proto, 'showPopover');
  const originalShowPopover = proto.showPopover;
  let popoverLefts: string[];

  beforeEach(() => {
    document.body.innerHTML = '<div id="root"></div>';
    root = createRoot(document.getElementById('root') as HTMLElement);
    mocks.stateRollups.mockResolvedValue(ROLLUPS);
    mocks.stageRenders = 0;
    mocks.bodyRenders = 0;
    mapCommits = 0;
    frames = [];
    vi.spyOn(window, 'requestAnimationFrame').mockImplementation((callback) => {
      frames.push(callback);
      return frames.length;
    });
    popoverLefts = [];
    proto.showPopover = vi.fn(function showPopover(this: HTMLElement) {
      popoverLefts.push(this.style.left);
    });
  });

  afterEach(() => {
    act(() => root.unmount());
    document.body.innerHTML = '';
    client.clear();
    vi.restoreAllMocks();
    vi.clearAllMocks();
    if (hadShowPopover) proto.showPopover = originalShowPopover;
    else Reflect.deleteProperty(proto, 'showPopover');
  });

  async function renderMap(props: ComponentProps<typeof USChoroplethMap> = {}) {
    await act(async () => root.render(<Providers><USChoroplethMap {...props} /></Providers>));
    for (let i = 0; i < 300 && !path('tx')?.classList.contains('has-data'); i += 1) await settle();
    expect(path('tx')?.classList.contains('has-data')).toBe(true);
  }

  it('a pointermove inside one state renders nothing and moves the tip in a frame', async () => {
    await renderMap();
    const texas = path('tx') as SVGPathElement;
    await pointer('pointerover', texas, 300);
    flushFrames();
    expect(tip()?.textContent).toContain('Texas');
    expect(tip()?.style.left).toBe('300px');
    expect(tip()?.style.top).toBe('196px');
    const before = { map: mapCommits, stage: mocks.stageRenders, body: mocks.bodyRenders };

    const lefts: string[] = [];
    for (let i = 1; i <= 20; i += 1) {
      await pointer('pointermove', texas, 300 + i * 5);
      if (i % 5 === 0) {
        flushFrames();
        lefts.push(tip()?.style.left ?? '');
      }
    }
    // The tip follows the pointer after each frame...
    expect(lefts).toEqual(['325px', '350px', '375px', '400px']);
    // ...and nothing re-rendered for it: not the map, the stage or the card.
    expect({ map: mapCommits, stage: mocks.stageRenders, body: mocks.bodyRenders }).toEqual(before);
  });

  it('moving to a second state renders the map and the card once, never the stage', async () => {
    await renderMap();
    await pointer('pointerover', path('tx') as SVGPathElement, 300);
    flushFrames();
    const before = { map: mapCommits, stage: mocks.stageRenders, body: mocks.bodyRenders };
    await pointer('pointerover', path('il') as SVGPathElement, 120);
    flushFrames();
    expect(tip()?.textContent).toContain('Illinois');
    expect(tip()?.style.left).toBe('160px');
    expect({
      map: mapCommits - before.map,
      stage: mocks.stageRenders - before.stage,
      body: mocks.bodyRenders - before.body,
    }).toEqual({ map: 1, stage: 0, body: 1 });
  });

  it('hides the card over the stage background and when the pointer leaves the stage', async () => {
    await renderMap();
    const svg = document.querySelector('svg.map-svg-stage') as SVGSVGElement;
    await pointer('pointerover', path('tx') as SVGPathElement, 300);
    expect(tip()).not.toBeNull();
    await pointer('pointerover', svg, 310);
    expect(tip()).toBeNull();

    await pointer('pointerover', path('il') as SVGPathElement, 300);
    expect(tip()).not.toBeNull();
    await pointer('pointerout', path('il') as SVGPathElement, 300, 200, { relatedTarget: document.body });
    expect(tip()).toBeNull();
  });

  it('opens the card on focus at the clamped anchor before it is promoted, and Escape hides it', async () => {
    await renderMap();
    const texas = path('tx') as SVGPathElement;
    Object.defineProperty(texas, 'getBoundingClientRect', {
      value: () => ({ left: 500, top: 300, width: 40, height: 20, right: 540, bottom: 320, x: 500, y: 300 }),
    });
    await act(async () => texas.focus());
    expect(tip()?.textContent).toContain('Texas');
    // Placed in the same layout pass, before showPopover: the first left the
    // top layer ever sees is the anchor's centre, not the previous point.
    expect(popoverLefts).toEqual(['520px']);
    expect(tip()?.style.top).toBe('296px');
    // The next-frame re-anchor moves the tip without a render.
    const before = mapCommits;
    flushFrames();
    expect(mapCommits).toBe(before);
    expect(tip()?.style.left).toBe('520px');

    await act(async () => {
      texas.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    });
    expect(tip()).toBeNull();
  });

  it('keeps the roving tab stop across a forced parent re-render', async () => {
    await renderMap();
    await act(async () => path('tx')?.focus());
    expect(path('tx')?.getAttribute('tabindex')).toBe('0');
    expect(path('il')?.getAttribute('tabindex')).toBe('-1');
    // A new height prop re-renders the map but not the stage's memory.
    await act(async () => root.render(<Providers><USChoroplethMap height={480} /></Providers>));
    expect(path('tx')?.getAttribute('tabindex')).toBe('0');
    expect(path('il')?.getAttribute('tabindex')).toBe('-1');
  });
});
