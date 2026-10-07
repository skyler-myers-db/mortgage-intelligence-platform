/**
 * @vitest-environment happy-dom
 */
/**
 * useSvgViewBox (audit dataviz-01 / motion-10; deviation:zcta-level) in a
 * rendered svg: the zoom range and Fit, the + = - 0 keys, the native
 * passive:false wheel (ctrl/cmd zooms and prevents the page zoom, a plain
 * wheel scrolls), a drag past 4px swallowing its click, and the render
 * budget: no React render while a gesture runs, one commit when it ends.
 */
import { Profiler, act, useLayoutEffect, useRef, type ProfilerOnRenderCallback } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  DRAG_SLOP_PX,
  MAX_ZOOM,
  WHEEL_IDLE_MS,
  clampView,
  useSvgViewBox,
  zoomView,
  type SvgViewBox,
  type ViewBox,
} from './useSvgViewBox';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const FIT: ViewBox = { x: 100, y: 100, w: 40, h: 30 };
const BOUNDS: ViewBox = { x: 0, y: 0, w: 400, h: 300 };

let api: SvgViewBox | null = null;
let commits = 0;
const onRender: ProfilerOnRenderCallback = () => {
  commits += 1;
};

function Stage({ from = null }: { from?: ViewBox | null }) {
  const svgRef = useRef<SVGSVGElement | null>(null);
  const view = useSvgViewBox({ svgRef, fit: FIT, bounds: BOUNDS, from });
  // Handed to the test after each commit (a render must not write outside itself).
  useLayoutEffect(() => {
    api = view;
  });
  return <svg ref={svgRef} viewBox={`${view.committed.x} ${view.committed.y} ${view.committed.w} ${view.committed.h}`} />;
}

let root: Root;
const svg = () => document.querySelector('svg') as SVGSVGElement;
const frames = () => act(async () => {
  await new Promise((resolve) => window.setTimeout(resolve, 40));
});
const attr = () => svg().getAttribute('viewBox')?.split(' ').map(Number) ?? [];

async function mount(from: ViewBox | null = null) {
  await act(async () => {
    root.render(
      <Profiler id="stage" onRender={onRender}>
        <Stage from={from} />
      </Profiler>,
    );
  });
  vi.spyOn(svg(), 'getBoundingClientRect').mockReturnValue({
    x: 0, y: 0, left: 0, top: 0, right: 400, bottom: 300, width: 400, height: 300, toJSON: () => ({}),
  } as DOMRect);
}

/** A wheel event with its modifiers set (happy-dom's WheelEvent init drops ctrlKey and metaKey). */
function wheel(deltaY: number, modifiers: { ctrlKey?: boolean; metaKey?: boolean } = {}): WheelEvent {
  const event = new WheelEvent('wheel', { bubbles: true, cancelable: true, deltaY, clientX: 200, clientY: 150 });
  Object.defineProperty(event, 'ctrlKey', { value: modifiers.ctrlKey ?? false });
  Object.defineProperty(event, 'metaKey', { value: modifiers.metaKey ?? false });
  return event;
}

const pointer = (type: string, x: number, y: number) =>
  new PointerEvent(type, { bubbles: true, cancelable: true, clientX: x, clientY: y, button: 0, pointerId: 1, isPrimary: true });

describe('useSvgViewBox', () => {
  beforeEach(() => {
    document.body.innerHTML = '<div id="root"></div>';
    root = createRoot(document.getElementById('root') as HTMLElement);
    commits = 0;
    api = null;
  });
  afterEach(() => {
    act(() => root.unmount());
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it('clamps zoom to 1x-12x of the fit and keeps the view inside the bounds', () => {
    let view = FIT;
    for (let i = 0; i < 20; i += 1) view = zoomView(view, 1.5, 120, 115, FIT, BOUNDS);
    expect(view.w).toBeCloseTo(FIT.w / MAX_ZOOM);
    expect(view.h / view.w).toBeCloseTo(FIT.h / FIT.w);
    for (let i = 0; i < 20; i += 1) view = zoomView(view, 1 / 1.5, 120, 115, FIT, BOUNDS);
    expect(view.w).toBeCloseTo(FIT.w);
    expect(clampView({ ...FIT, x: -500, y: 9999 }, FIT, BOUNDS)).toEqual({ x: 0, y: 270, w: 40, h: 30 });
  });

  it('zooms with the buttons and the + = - 0 keys, Fit returns to the fit, and other keys pass', async () => {
    await mount();
    act(() => api?.zoomIn());
    expect(api?.zoom).toBeCloseTo(1.5);
    act(() => {
      api?.onKey('=');
    });
    expect(api?.zoom).toBeCloseTo(2.25);
    act(() => {
      api?.onKey('-');
    });
    expect(api?.zoom).toBeCloseTo(1.5);
    act(() => {
      api?.onKey('+');
    });
    act(() => {
      api?.onKey('0');
    });
    expect(api?.committed).toEqual(FIT);
    expect(api?.onKey('ArrowLeft')).toBe(false);
    for (let i = 0; i < 30; i += 1) act(() => api?.zoomIn());
    expect(api?.zoom).toBeCloseTo(MAX_ZOOM);
  });

  it('registers the wheel listener with passive:false; ctrl/cmd-wheel prevents the page zoom, a plain wheel does not', async () => {
    const added = vi.spyOn(SVGElement.prototype, 'addEventListener');
    await mount();
    const registration = added.mock.calls.find(([type]) => type === 'wheel');
    expect(registration?.[2]).toEqual({ passive: false });
    const plain = wheel(100);
    svg().dispatchEvent(plain);
    expect(plain.defaultPrevented).toBe(false);
    const pinch = wheel(-40, { ctrlKey: true });
    svg().dispatchEvent(pinch);
    expect(pinch.defaultPrevented).toBe(true);
    const command = wheel(-40, { metaKey: true });
    svg().dispatchEvent(command);
    expect(command.defaultPrevented).toBe(true);
  });

  it('pans on a drag with no render per frame, commits once at pointerup, and swallows the click that ends it', async () => {
    await mount();
    act(() => api?.zoomIn());
    act(() => api?.zoomIn());
    const before = commits;
    const zoomed = api?.committed as ViewBox;
    const clicks = vi.fn();
    svg().addEventListener('click', clicks);
    svg().dispatchEvent(pointer('pointerdown', 200, 150));
    for (let step = 1; step <= 6; step += 1) svg().dispatchEvent(pointer('pointermove', 200 + step * 10, 150));
    await frames();
    expect(commits).toBe(before);
    expect(attr()[0]).toBeLessThan(zoomed.x);
    svg().dispatchEvent(pointer('pointerup', 260, 150));
    await frames();
    expect(commits).toBe(before + 1);
    expect(api?.committed.x).toBeLessThan(zoomed.x);
    svg().dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
    expect(clicks).not.toHaveBeenCalled();
    // A press that stays inside the slop is a click.
    svg().dispatchEvent(pointer('pointerdown', 200, 150));
    svg().dispatchEvent(pointer('pointermove', 200 + DRAG_SLOP_PX - 1, 150));
    svg().dispatchEvent(pointer('pointerup', 200 + DRAG_SLOP_PX - 1, 150));
    svg().dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
    expect(clicks).toHaveBeenCalledTimes(1);
  });

  it('reveal centres a target whose in-bounds centre is out of view, keeping the zoom, and is a no-op otherwise', async () => {
    await mount();
    act(() => api?.reveal({ x: 110, y: 110, w: 5, h: 5 }));
    expect(api?.committed).toEqual(FIT);
    // Its raw centre (75, 115) is out of the fit, its in-bounds part's (125, 115) is not.
    act(() => api?.reveal({ x: -100, y: 100, w: 350, h: 30 }));
    expect(api?.committed).toEqual(FIT);
    act(() => api?.reveal({ x: 300, y: 200, w: 10, h: 10 }));
    expect(api?.committed).toEqual({ x: 285, y: 190, w: 40, h: 30 });
    expect(attr()).toEqual([285, 190, 40, 30]);
  });

  it('a drag that ends with no click (pointercancel) never swallows the next press\'s click', async () => {
    await mount();
    act(() => api?.zoomIn());
    const clicks = vi.fn();
    svg().addEventListener('click', clicks);
    svg().dispatchEvent(pointer('pointerdown', 200, 150));
    for (let step = 1; step <= 3; step += 1) svg().dispatchEvent(pointer('pointermove', 200 + step * 10, 150));
    // The browser took the gesture over: no pointerup, no click.
    svg().dispatchEvent(pointer('pointercancel', 230, 150));
    await frames();
    // The next press is a plain click on a ZIP area: it reaches the stage.
    svg().dispatchEvent(pointer('pointerdown', 120, 150));
    svg().dispatchEvent(pointer('pointerup', 120, 150));
    svg().dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
    expect(clicks).toHaveBeenCalledTimes(1);
  });

  it('zooms a ctrl-wheel burst with no render per event and commits once when the wheel goes idle', async () => {
    await mount();
    const before = commits;
    for (let i = 0; i < 5; i += 1) {
      svg().dispatchEvent(wheel(-30, { ctrlKey: true }));
    }
    await frames();
    expect(commits).toBe(before);
    expect(attr()[2]).toBeLessThan(FIT.w);
    await act(async () => {
      await new Promise((resolve) => window.setTimeout(resolve, WHEEL_IDLE_MS + 60));
    });
    expect(commits).toBe(before + 1);
    expect(api?.zoom).toBeGreaterThan(1);
  });

  it('tweens from the national view to the fit, and is instant under reduced motion', async () => {
    const national: ViewBox = { x: 0, y: 0, w: 975, h: 610 };
    vi.spyOn(window, 'matchMedia').mockReturnValue({ matches: true } as MediaQueryList);
    await mount(national);
    expect(attr()).toEqual([FIT.x, FIT.y, FIT.w, FIT.h]);
    act(() => root.unmount());
    document.body.innerHTML = '<div id="root"></div>';
    root = createRoot(document.getElementById('root') as HTMLElement);
    vi.spyOn(window, 'matchMedia').mockReturnValue({ matches: false } as MediaQueryList);
    await mount(national);
    // The first frame is the national view (set before paint), then the fit.
    expect(attr()[2]).toBeGreaterThan(FIT.w);
    await act(async () => {
      await new Promise((resolve) => window.setTimeout(resolve, 600));
    });
    expect(attr()).toEqual([FIT.x, FIT.y, FIT.w, FIT.h]);
  });
});
