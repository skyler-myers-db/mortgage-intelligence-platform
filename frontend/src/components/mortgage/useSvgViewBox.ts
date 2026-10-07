/**
 * useSvgViewBox — pan and zoom on an <svg> viewBox for the ZCTA rung (audit
 * dataviz-01 and motion-10's map-drill continuity; deviation:zcta-level).
 *
 * The live view is a ref, written to the svg's `viewBox` attribute in a
 * requestAnimationFrame: no React render per gesture frame. React state (the
 * `committed` view) changes once, at a gesture's end (pointerup, a wheel gone
 * idle, a button or a key), which is when the rung re-places its labels.
 *
 *  - Zoom is 1x to 12x of `fit`, pan stays inside `bounds` (the state's box).
 *  - ctrl/cmd + wheel, and a trackpad pinch (which arrives as a ctrl wheel),
 *    zoom around the pointer through a NATIVE `{ passive: false }` listener:
 *    React's onWheel is passive, so its preventDefault would silently fail
 *    and the page would zoom. A plain wheel is never taken: the page scrolls.
 *  - A pointer drag pans (pointer capture); a drag past 4px swallows the one
 *    click that ends it, so panning never opens a ZIP's Lead Queue.
 *  - `onKey` answers + (and =), - and 0 for the stage's keydown listener,
 *    which passes only unmodified keys: a ctrl / cmd / alt chord is the
 *    browser's own zoom (WCAG 1.4.4) and is never taken.
 *  - motion-10: the first frame is the national `from` view, tweened to the
 *    fit over --dur-slow; instant under prefers-reduced-motion, and the first
 *    user gesture stops it where it is.
 */
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type RefObject } from 'react';
import { fixedAttr } from '../../lib/fixedPrecision';

export interface ViewBox {
  x: number;
  y: number;
  w: number;
  h: number;
}

export const MAX_ZOOM = 12;
/** One button press or key zooms by this factor. */
export const ZOOM_STEP = 1.5;
/** Past this many pixels a press is a drag, never a click. */
export const DRAG_SLOP_PX = 4;
/** A ctrl/cmd-wheel gesture ends when no wheel event arrives for this long. */
export const WHEEL_IDLE_MS = 160;
const FALLBACK_TWEEN_MS = 360;

export interface SvgViewBoxInput {
  svgRef: RefObject<SVGSVGElement | null>;
  /** The fitted view (zoom 1). */
  fit: ViewBox;
  /** Pan never leaves this box. */
  bounds: ViewBox;
  /** The view the first frame starts from (the national viewBox); null for none. */
  from?: ViewBox | null;
}

export interface SvgViewBox {
  /** The view as of the last gesture's end. */
  committed: ViewBox;
  /** fit.w / committed.w, 1..MAX_ZOOM. */
  zoom: number;
  zoomIn: () => void;
  zoomOut: () => void;
  fitView: () => void;
  /** +, =, - and 0 (true when handled; the caller prevents the default). */
  onKey: (key: string) => boolean;
}

export const viewBoxAttr = (view: ViewBox) =>
  `${fixedAttr(view.x, 3)} ${fixedAttr(view.y, 3)} ${fixedAttr(view.w, 3)} ${fixedAttr(view.h, 3)}`;

/** `view` with its zoom held to 1..MAX_ZOOM of `fit` (fit's aspect) and its origin inside `bounds`. */
export function clampView(view: ViewBox, fit: ViewBox, bounds: ViewBox): ViewBox {
  const w = Math.min(fit.w, Math.max(fit.w / MAX_ZOOM, view.w));
  const h = (w * fit.h) / fit.w;
  const along = (start: number, size: number, low: number, span: number) =>
    size >= span ? low + (span - size) / 2 : Math.min(low + span - size, Math.max(low, start));
  return { x: along(view.x, w, bounds.x, bounds.w), y: along(view.y, h, bounds.y, bounds.h), w, h };
}

/** `view` zoomed by `factor` (above 1 zooms in) around the viewBox point (cx, cy), clamped. */
export function zoomView(view: ViewBox, factor: number, cx: number, cy: number, fit: ViewBox, bounds: ViewBox): ViewBox {
  const w = view.w / factor;
  const ratio = w / view.w;
  return clampView({ x: cx - (cx - view.x) * ratio, y: cy - (cy - view.y) * ratio, w, h: view.h * ratio }, fit, bounds);
}

/** The viewBox point under a client point, for a `preserveAspectRatio="xMidYMid meet"` svg. */
function pointIn(svg: SVGSVGElement, view: ViewBox, clientX: number, clientY: number): [number, number] {
  const rect = svg.getBoundingClientRect();
  const scale = Math.min(rect.width / view.w, rect.height / view.h) || 1;
  const left = rect.left + (rect.width - view.w * scale) / 2;
  const top = rect.top + (rect.height - view.h * scale) / 2;
  return [view.x + (clientX - left) / scale, view.y + (clientY - top) / scale];
}

function tweenMs(svg: SVGSVGElement): number {
  const raw = window.getComputedStyle(svg).getPropertyValue('--dur-slow').trim();
  const amount = Number.parseFloat(raw);
  if (!Number.isFinite(amount)) return FALLBACK_TWEEN_MS;
  return raw.endsWith('ms') ? amount : amount * 1000;
}

const reducedMotion = () => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false;
const same = (a: ViewBox, b: ViewBox) => a.x === b.x && a.y === b.y && a.w === b.w && a.h === b.h;
const lerp = (a: number, b: number, t: number) => a + (b - a) * t;

export function useSvgViewBox({ svgRef, fit, bounds, from = null }: SvgViewBoxInput): SvgViewBox {
  const [committed, setCommitted] = useState<ViewBox>(fit);
  const live = useRef<ViewBox>(fit);
  const frame = useRef(0);
  const tween = useRef(0);
  const limits = useRef({ fit, bounds });
  useLayoutEffect(() => {
    limits.current = { fit, bounds };
  }, [fit, bounds]);

  const paint = useCallback(() => {
    if (frame.current) return;
    frame.current = window.requestAnimationFrame(() => {
      frame.current = 0;
      svgRef.current?.setAttribute('viewBox', viewBoxAttr(live.current));
    });
  }, [svgRef]);
  const stopTween = useCallback(() => {
    if (tween.current) window.cancelAnimationFrame(tween.current);
    tween.current = 0;
  }, []);
  const commit = useCallback(() => {
    const view = { ...live.current };
    setCommitted((previous) => (same(previous, view) ? previous : view));
  }, []);
  /** A whole-step change (a button, a key): applied, painted and committed at once. */
  const settle = useCallback((next: ViewBox) => {
    stopTween();
    live.current = next;
    svgRef.current?.setAttribute('viewBox', viewBoxAttr(next));
    commit();
  }, [commit, stopTween, svgRef]);
  const zoomBy = useCallback((factor: number) => {
    const view = live.current;
    const { fit: f, bounds: b } = limits.current;
    settle(zoomView(view, factor, view.x + view.w / 2, view.y + view.h / 2, f, b));
  }, [settle]);
  const zoomIn = useCallback(() => zoomBy(ZOOM_STEP), [zoomBy]);
  const zoomOut = useCallback(() => zoomBy(1 / ZOOM_STEP), [zoomBy]);
  const fitView = useCallback(() => settle(limits.current.fit), [settle]);
  const onKey = useCallback((key: string) => {
    if (key === '+' || key === '=') zoomIn();
    else if (key === '-') zoomOut();
    else if (key === '0') fitView();
    else return false;
    return true;
  }, [fitView, zoomIn, zoomOut]);

  // motion-10: start on the national view and tween to the fit, before paint.
  // The rung is keyed by state, so this runs once per drill; an unmount
  // mid-tween lands on the fit, never on a half-way frame.
  useLayoutEffect(() => {
    const svg = svgRef.current;
    if (!svg || !from || reducedMotion()) return undefined;
    const target = limits.current.fit;
    const duration = tweenMs(svg);
    live.current = from;
    svg.setAttribute('viewBox', viewBoxAttr(from));
    const started = performance.now();
    const step = (now: number) => {
      const t = Math.min(1, (now - started) / duration);
      const eased = 1 - (1 - t) ** 3;
      live.current = {
        x: lerp(from.x, target.x, eased),
        y: lerp(from.y, target.y, eased),
        w: lerp(from.w, target.w, eased),
        h: lerp(from.h, target.h, eased),
      };
      svg.setAttribute('viewBox', viewBoxAttr(live.current));
      tween.current = t < 1 ? window.requestAnimationFrame(step) : 0;
    };
    tween.current = window.requestAnimationFrame(step);
    return () => {
      if (!tween.current) return;
      stopTween();
      live.current = target;
      svg.setAttribute('viewBox', viewBoxAttr(target));
    };
  }, [from, stopTween, svgRef]);

  // A new fit (the cohort, so the populated set, changed under the drill) refits.
  const fitKey = viewBoxAttr(fit);
  const lastFit = useRef(fitKey);
  useEffect(() => {
    if (lastFit.current === fitKey) return;
    lastFit.current = fitKey;
    settle(limits.current.fit);
  }, [fitKey, settle]);

  // Wheel and drag: native listeners (a passive React onWheel cannot preventDefault).
  useEffect(() => {
    const svg = svgRef.current;
    if (!svg) return undefined;
    let wheelIdle = 0;
    let press: { id: number; x: number; y: number; view: ViewBox; dragging: boolean } | null = null;
    let swallowClick = false;
    const onWheel = (event: WheelEvent) => {
      if (!event.ctrlKey && !event.metaKey) return;
      event.preventDefault();
      stopTween();
      const view = live.current;
      const [cx, cy] = pointIn(svg, view, event.clientX, event.clientY);
      const factor = Math.min(2, Math.max(0.5, Math.exp(-event.deltaY * 0.01)));
      const { fit: f, bounds: b } = limits.current;
      live.current = zoomView(view, factor, cx, cy, f, b);
      paint();
      window.clearTimeout(wheelIdle);
      wheelIdle = window.setTimeout(commit, WHEEL_IDLE_MS);
    };
    const onPointerDown = (event: PointerEvent) => {
      if (event.button !== 0 || !event.isPrimary) return;
      press = { id: event.pointerId, x: event.clientX, y: event.clientY, view: live.current, dragging: false };
    };
    const onPointerMove = (event: PointerEvent) => {
      if (!press || event.pointerId !== press.id) return;
      const dx = event.clientX - press.x;
      const dy = event.clientY - press.y;
      if (!press.dragging) {
        if (Math.hypot(dx, dy) <= DRAG_SLOP_PX) return;
        press.dragging = true;
        swallowClick = true;
        stopTween();
        svg.setPointerCapture?.(event.pointerId);
      }
      const rect = svg.getBoundingClientRect();
      const scale = Math.min(rect.width / press.view.w, rect.height / press.view.h) || 1;
      const { fit: f, bounds: b } = limits.current;
      live.current = clampView({ ...press.view, x: press.view.x - dx / scale, y: press.view.y - dy / scale }, f, b);
      paint();
    };
    const onPointerEnd = (event: PointerEvent) => {
      if (!press || event.pointerId !== press.id) return;
      const dragged = press.dragging;
      press = null;
      if (svg.hasPointerCapture?.(event.pointerId)) svg.releasePointerCapture(event.pointerId);
      if (dragged) commit();
    };
    const onClickCapture = (event: MouseEvent) => {
      if (!swallowClick) return;
      swallowClick = false;
      event.preventDefault();
      event.stopImmediatePropagation();
    };
    svg.addEventListener('wheel', onWheel, { passive: false });
    svg.addEventListener('pointerdown', onPointerDown);
    svg.addEventListener('pointermove', onPointerMove);
    svg.addEventListener('pointerup', onPointerEnd);
    svg.addEventListener('pointercancel', onPointerEnd);
    svg.addEventListener('click', onClickCapture, { capture: true });
    return () => {
      window.clearTimeout(wheelIdle);
      window.cancelAnimationFrame(frame.current);
      frame.current = 0;
      svg.removeEventListener('wheel', onWheel);
      svg.removeEventListener('pointerdown', onPointerDown);
      svg.removeEventListener('pointermove', onPointerMove);
      svg.removeEventListener('pointerup', onPointerEnd);
      svg.removeEventListener('pointercancel', onPointerEnd);
      svg.removeEventListener('click', onClickCapture, { capture: true });
    };
  }, [commit, paint, stopTween, svgRef]);

  return { committed, zoom: fit.w / committed.w, zoomIn, zoomOut, fitView, onKey };
}
