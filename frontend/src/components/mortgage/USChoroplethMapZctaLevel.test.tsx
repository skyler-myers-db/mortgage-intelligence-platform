/**
 * @vitest-environment happy-dom
 */
/**
 * The ZCTA rung (audit dataviz-01 / visual-09; deviation:zcta-level) on a
 * synthetic geometry with the loader mocked: populated versus context ZCTAs,
 * roving over populated units only, labels by on-screen width, one deep link
 * per click / Enter, the tile fallback with its status line (a missing file
 * and a failed load; the chunk failure is USChoroplethMap.render.test), the
 * reconcile note, the outline painted above the fills, the zoom controls
 * registered once, and zero React commits across a pointermove sweep. The
 * fallback cases run through Segment Intelligence's stage (ZIP_AREAS), which
 * draws the tiles when the rung reports them, and its reported view.
 */
import { Profiler, act, type ProfilerOnRenderCallback, type ReactNode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
// @ts-expect-error Frontend app types intentionally exclude Node globals; this
// test reads one stylesheet's source under Vitest only.
import { readFileSync } from 'node:fs';
// @ts-expect-error see node:fs note above.
import { join } from 'node:path';
import { createMipQueryClient } from '../../lib/queryClient';
import type { StateRollup, ZipRollup } from '../../types';
import { claimDrillFocus, moveRovingFocus, zipAriaLabel } from './USChoroplethMap.a11y';
import { buildChoroplethScale } from './USChoroplethMap.scale';
import type { ZipStageView } from './USChoroplethMap.zipStage';
import { useMapHover } from './useMapHover';
import type { ZctaArea, ZctaGeometry } from './zctaGeometry';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
declare const process: { cwd(): string };

const geo = vi.hoisted(() => ({ has: vi.fn(), load: vi.fn() }));
vi.mock('./zctaGeometry', () => ({
  hasZctaGeometry: geo.has,
  loadZctaGeometry: geo.load,
  zctaGeometryKey: (usps: string) => ['mip', 'geo', 'zcta-geometry', usps],
}));

const { USChoroplethMapZctaLevel } = await import('./USChoroplethMapZctaLevel');
const { ZIP_AREAS, ZCTA_CAPTION, ZCTA_TILES_STATUS } = await import('./USChoroplethMapZipAreas');

const square = (zip: string, x: number, y: number, size: number): ZctaArea => ({
  zip,
  d: `M${x},${y}L${x + size},${y}L${x + size},${y + size}L${x},${y + size}Z`,
  box: [x, y, x + size, y + size],
  labelAt: [x + size / 2, y + size / 2],
});

/** 60611 (10 units wide) and 60612 (half a unit) have borrowers; 60613 and 60614 are context. */
const AREAS = [square('60611', 10, 10, 10), square('60612', 30, 10, 0.5), square('60613', 50, 50, 10), square('60614', 70, 20, 5)];
const GEOMETRY: ZctaGeometry = {
  usps: 'IL',
  areas: AREAS,
  byZip: new Map(AREAS.map((area) => [area.zip, area])),
  outline: 'M0,0L100,0L100,100L0,100Z',
  stateBox: [0, 0, 100, 100],
};

const rollup = (zip: string, count: number): ZipRollup => ({
  zip,
  state: 'IL',
  addressable_borrowers: count,
  avg_opportunity_score: 71,
  top_segment_code: 'itm',
});
/** 60699 has borrowers and no ZCTA (a PO-box ZIP); 60614 is in the rollup with none. */
const BY_ZIP: Record<string, ZipRollup> = {
  '60611': rollup('60611', 50),
  '60612': rollup('60612', 5),
  '60614': rollup('60614', 0),
  '60699': rollup('60699', 7),
};
const STATE: StateRollup = {
  state: 'IL', addressable: 900, in_the_money: 10, top_tier_opportunities: 2, avg_score: 70, zip_unassigned_count: 12,
};

let root: Root;
let commits = 0;
const onRender: ProfilerOnRenderCallback = () => {
  commits += 1;
};
const handlers = {
  onRung: vi.fn(),
  onZoomControls: vi.fn(),
  onSelectZip: vi.fn(),
  onOpenStateQueue: vi.fn(),
  onView: vi.fn<(view: ZipStageView) => void>(),
};
const KIT = { claimDrillFocus, moveRovingFocus, zipAriaLabel };

/** What a case changes from the Illinois drill above. */
interface DrillOverrides {
  byZip?: Record<string, ZipRollup>;
  /** undefined: the drilled state has no state rollup either. */
  stateFacts?: StateRollup | undefined;
  autoFocus?: boolean;
}

/** The rung alone, or (`stage`) through Segment Intelligence's ZIP_AREAS stage. */
function Harness({ stage = false, drill = {}, children }: { stage?: boolean; drill?: DrillOverrides; children?: ReactNode }) {
  const { stage: hover } = useMapHover();
  const tiles = {
    usps: 'IL',
    nationalViewBox: '0 0 975 610',
    drillStateName: 'Illinois',
    byZip: BY_ZIP,
    stateFacts: STATE,
    scale: buildChoroplethScale([50, 5, 7]),
    overlayActive: false,
    overlayByUnit: {},
    selectedZip: null,
    hover,
    onSelectZip: handlers.onSelectZip,
    onOpenStateQueue: handlers.onOpenStateQueue,
    ...drill,
  };
  return (
    <>
      <Profiler id="rung" onRender={onRender}>
        {stage ? (
          <ZIP_AREAS.Stage {...tiles} onView={handlers.onView} />
        ) : (
          <USChoroplethMapZctaLevel {...tiles} kit={KIT} onRung={handlers.onRung} onZoomControls={handlers.onZoomControls} />
        )}
      </Profiler>
      {children}
    </>
  );
}

async function settle(): Promise<void> {
  await act(async () => {
    await new Promise((resolve) => window.setTimeout(resolve, 20));
  });
}

async function until(check: () => boolean): Promise<void> {
  for (let i = 0; i < 200 && !check(); i += 1) await settle();
  expect(check()).toBe(true);
}

async function render(stage = false, drill: DrillOverrides = {}): Promise<void> {
  const client = createMipQueryClient();
  await act(async () => {
    root.render(
      <QueryClientProvider client={client}>
        <Harness stage={stage} drill={drill} />
      </QueryClientProvider>,
    );
  });
}

const svg = () => document.querySelector('svg.map-zcta') as SVGSVGElement | null;
const unit = (zip: string) => document.querySelector(`path[data-map-unit="${zip}"]`) as SVGPathElement | null;

describe('USChoroplethMapZctaLevel', () => {
  beforeEach(() => {
    document.body.innerHTML = '<div id="root"></div>';
    root = createRoot(document.getElementById('root') as HTMLElement);
    commits = 0;
    Object.values(handlers).forEach((handler) => handler.mockReset());
    geo.has.mockReset().mockReturnValue(true);
    geo.load.mockReset().mockResolvedValue(GEOMETRY);
    vi.spyOn(window, 'matchMedia').mockReturnValue({ matches: true } as MediaQueryList);
    // The svg's on-screen size and the --fs-11 token the labels counter-scale to.
    vi.spyOn(SVGElement.prototype, 'getBoundingClientRect').mockReturnValue({
      x: 0, y: 0, left: 0, top: 0, right: 400, bottom: 300, width: 400, height: 300, toJSON: () => ({}),
    } as DOMRect);
    const computed = window.getComputedStyle.bind(window);
    vi.spyOn(window, 'getComputedStyle').mockImplementation((element) => {
      const style = computed(element);
      return new Proxy(style, {
        get: (target, key) => (key === 'getPropertyValue'
          ? (name: string) => (name === '--fs-11' ? '11px' : target.getPropertyValue(name))
          : Reflect.get(target, key)),
      });
    });
  });
  afterEach(() => {
    act(() => root.unmount());
    vi.restoreAllMocks();
  });

  it('paints populated ZCTAs as controls and the rest as muted context, fills first and the outline above them', async () => {
    await render();
    await until(() => svg() !== null);
    const populated = [...document.querySelectorAll('path[data-populated]')];
    expect(populated.map((path) => path.getAttribute('data-map-unit'))).toEqual(['60611', '60612']);
    for (const path of populated) {
      expect(path.getAttribute('role')).toBe('button');
      expect(path.getAttribute('class')).toMatch(/^map-region has-data lvl-[1-4]$/);
    }
    expect(unit('60611')?.getAttribute('aria-label')).toBe(
      'ZIP 60611: 50 borrowers, average opportunity score 71, top segment Prime Refi Candidates',
    );
    const context = [...document.querySelectorAll('path.map-region--context')];
    expect(context).toHaveLength(2);
    for (const path of context) {
      expect(path.getAttribute('class')).toBe('map-region is-empty map-region--context');
      expect(path.getAttribute('aria-hidden')).toBe('true');
      expect(path.hasAttribute('tabindex')).toBe(false);
      expect(path.hasAttribute('role')).toBe(false);
    }
    const paths = [...(svg()?.children ?? [])].map((child) => child.getAttribute('class'));
    expect(paths.indexOf('map-zcta__outline')).toBe(AREAS.length);
    expect(paths[paths.length - 1]).toBe('map-labels');
    expect(handlers.onRung).toHaveBeenCalledWith('polygons');
  });

  it('roves over populated ZCTAs only, and Enter opens one ZIP once', async () => {
    await render();
    await until(() => svg() !== null);
    const stops = [...document.querySelectorAll('path[tabindex="0"]')];
    expect(stops.map((path) => path.getAttribute('data-map-unit'))).toEqual(['60611']);
    const first = unit('60611') as SVGPathElement;
    act(() => first.focus());
    act(() => {
      first.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true, cancelable: true }));
    });
    expect(document.activeElement?.getAttribute('data-map-unit')).toBe('60612');
    act(() => {
      document.activeElement?.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true, cancelable: true }));
    });
    // Wraps past the context ZCTAs back to the first populated one.
    expect(document.activeElement?.getAttribute('data-map-unit')).toBe('60611');
    act(() => {
      document.activeElement?.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }));
    });
    expect(handlers.onSelectZip).toHaveBeenCalledTimes(1);
    expect(handlers.onSelectZip).toHaveBeenCalledWith('60611');
  });

  it('deep-links once per click on a populated ZCTA and never from context', async () => {
    await render();
    await until(() => svg() !== null);
    act(() => {
      unit('60612')?.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
    });
    act(() => {
      document.querySelector('path.map-region--context')?.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
    });
    expect(handlers.onSelectZip).toHaveBeenCalledTimes(1);
    expect(handlers.onSelectZip).toHaveBeenCalledWith('60612');
  });

  it('labels only the populated ZCTAs at least 36px wide at the committed zoom', async () => {
    await render();
    await until(() => document.querySelectorAll('.map-labels text').length > 0);
    const labels = [...document.querySelectorAll('.map-labels text')].map((text) => text.textContent);
    // 60611 is ~177px wide on screen, 60612 ~9px, the context ZCTAs carry no label.
    expect(labels).toEqual(['60611']);
    const group = document.querySelector('.map-labels') as SVGGElement;
    expect(Number(group.getAttribute('font-size'))).toBeGreaterThan(0);
    expect(Number(group.getAttribute('font-size'))).toBeLessThan(11);
  });

  it('notes the ZIPs with borrowers and no ZIP-area boundary, and the borrowers with no ZIP', async () => {
    await render();
    await until(() => document.querySelector('[role="note"]') !== null);
    const note = document.querySelector('[role="note"]')?.textContent ?? '';
    expect(note).toContain('1 ZIP (7 borrowers) has no Census ZIP-area boundary (PO box or unique ZIP); the table view lists it.');
    expect(note).toContain('12 borrowers in Illinois carry no ZIP and appear in no ZIP area.');
    expect(note).toContain('Open all 900 in Lead Queue');
  });

  it('registers the zoom buttons once and withdraws them on unmount', async () => {
    await render();
    await until(() => svg() !== null);
    await settle();
    const registrations = handlers.onZoomControls.mock.calls.filter(([controls]) => controls !== null);
    expect(registrations).toHaveLength(1);
    act(() => root.unmount());
    expect(handlers.onZoomControls).toHaveBeenLastCalledWith(null);
    root = createRoot(document.getElementById('root') as HTMLElement);
  });

  it('commits nothing across a pointermove sweep over a ZCTA', async () => {
    await render();
    await until(() => svg() !== null);
    await settle();
    const target = unit('60611') as SVGPathElement;
    act(() => {
      target.dispatchEvent(new PointerEvent('pointerover', { bubbles: true, clientX: 50, clientY: 50 }));
    });
    await settle();
    const before = commits;
    for (let step = 0; step < 25; step += 1) {
      target.dispatchEvent(new PointerEvent('pointermove', { bubbles: true, clientX: 50 + step, clientY: 50 }));
    }
    await settle();
    expect(commits).toBe(before);
  });

  it('reports tiles and draws nothing itself when the state has no committed file', async () => {
    geo.has.mockReturnValue(false);
    await render();
    await until(() => handlers.onRung.mock.calls.length > 0);
    expect(handlers.onRung).toHaveBeenCalledWith('tiles');
    expect(svg()).toBeNull();
    expect(geo.load).not.toHaveBeenCalled();
  });

  it('through ZIP_AREAS: the densest-ZIP tiles with their status line when the state has no committed file', async () => {
    geo.has.mockReturnValue(false);
    await render(true);
    await until(() => document.querySelector('.zip-tiles') !== null);
    expect(document.querySelector('.zip-tiles__status[role="status"]')?.textContent).toBe(ZCTA_TILES_STATUS);
    expect(svg()).toBeNull();
    expect(geo.load).not.toHaveBeenCalled();
    expect(handlers.onView).toHaveBeenLastCalledWith({ polygons: false, busy: false, controls: null, caption: null, cardNote: null });
  });

  it('through ZIP_AREAS: the tiles after the geometry read fails its retries', async () => {
    geo.load.mockRejectedValue(new Error('ZIP-area geometry answered 404'));
    await render(true);
    await until(() => document.querySelector('.zip-tiles') !== null);
    expect(document.querySelector('.zip-tiles__status')?.textContent).toBe(ZCTA_TILES_STATUS);
    expect(geo.load).toHaveBeenCalledTimes(3);
    expect(handlers.onView).toHaveBeenLastCalledWith({ polygons: false, busy: false, controls: null, caption: null, cardNote: null });
  });

  it('through ZIP_AREAS: an empty ZIP rollup keeps the tiles\' empty card, its Lead Queue action takes the drill focus, and nothing loads', async () => {
    await render(true, { byZip: {}, stateFacts: undefined, autoFocus: true });
    await until(() => document.activeElement?.textContent === 'Open Lead Queue for Illinois');
    expect(document.body.textContent).toContain('No ZIP-level rollup for Illinois.');
    expect(document.querySelector('.zip-tiles__status')).toBeNull();
    expect(document.body.textContent).not.toContain('Loading ZIP areas');
    expect(svg()).toBeNull();
    // The rung never mounted: no geometry lookup, no geometry read.
    expect(geo.has).not.toHaveBeenCalled();
    expect(geo.load).not.toHaveBeenCalled();
    expect(handlers.onView).toHaveBeenLastCalledWith({ polygons: false, busy: false, controls: null, caption: null, cardNote: null });
  });

  it('through ZIP_AREAS: busy while loading, then polygons with the caption and the zoom buttons', async () => {
    await render(true);
    expect(handlers.onView.mock.calls[0]?.[0]).toMatchObject({ polygons: false, busy: true, controls: null, caption: null });
    await until(() => svg() !== null);
    await settle();
    const last = handlers.onView.mock.calls[handlers.onView.mock.calls.length - 1]?.[0];
    expect(last).toMatchObject({ polygons: true, busy: false, caption: ZCTA_CAPTION });
    expect(last?.controls).not.toBeNull();
    expect(last?.cardNote).toBe('Counts are Illinois borrowers in this ZIP area.');
  });

  it('opts the drill out of the level settle from the loading stage on, so only the viewBox tween moves (motion-10)', () => {
    const css = (readFileSync(join(process.cwd(), 'src/components/mortgage/USChoroplethMapZipAreas.css'), 'utf8') as string)
      .replace(/\/\*[\s\S]*?\*\//g, '');
    expect(css).toMatch(/\.map-levels:has\(> \.map-zcta, > \.map-stage--zcta\) \{ animation: none; \}/);
  });
});
