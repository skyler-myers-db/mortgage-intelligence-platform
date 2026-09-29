import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type PointerEvent,
  type RefObject,
} from 'react';

const SIZE_STORAGE_KEY = 'mip-genie-chat-size-v1';
const POSITION_STORAGE_KEY = 'mip-genie-chat-pos-v1';
const MIN_WIDTH = 360;
const MAX_WIDTH = 900;
const MIN_HEIGHT = 400;
const MAX_HEIGHT = 900;
const DEFAULT_SIZE = { w: 420, h: 640 };
const VIEWPORT_GUTTER = 16;
const SNAP_DISTANCE = 24;
/** The docked Console's width when --console-w cannot be read (09-console-and-layout.css). */
const DEFAULT_CONSOLE_W = 300;
/** Below this width the Console is a bottom sheet, not a right-edge card (12-motion-and-viewport.css). */
const CONSOLE_DOCK_MIN_VIEWPORT = 1280;

export interface GenieSize {
  w: number;
  h: number;
}

export interface GeniePosition {
  x: number;
  y: number;
}

type ResizeHandle = 'nw' | 'n' | 'ne' | 'e' | 'se' | 's' | 'sw' | 'w';

export const GENIE_POINTER_RESIZE_HANDLES = ['n', 'ne', 'e', 'se', 's', 'sw', 'w'] as const;

const RESIZE_MATRIX: Record<
  ResizeHandle,
  { wSign: number; hSign: number; xSign: number; ySign: number }
> = {
  nw: { wSign: -1, hSign: -1, xSign: 1, ySign: 1 },
  n: { wSign: 0, hSign: -1, xSign: 0, ySign: 1 },
  ne: { wSign: 1, hSign: -1, xSign: 0, ySign: 1 },
  e: { wSign: 1, hSign: 0, xSign: 0, ySign: 0 },
  se: { wSign: 1, hSign: 1, xSign: 0, ySign: 0 },
  s: { wSign: 0, hSign: 1, xSign: 0, ySign: 0 },
  sw: { wSign: -1, hSign: 1, xSign: 1, ySign: 0 },
  w: { wSign: -1, hSign: 0, xSign: 1, ySign: 0 },
};

function currentViewport(): GenieSize | null {
  if (typeof window === 'undefined') return null;
  return { w: window.innerWidth, h: window.innerHeight };
}

export function fitGenieSizeToViewport(size: GenieSize, viewport: GenieSize): GenieSize {
  return {
    w: Math.min(size.w, Math.max(0, viewport.w - VIEWPORT_GUTTER * 2)),
    h: Math.min(size.h, Math.max(0, viewport.h - VIEWPORT_GUTTER * 2)),
  };
}

/** The docked Console's width in px (`--console-w`; 340 at >= 2560px). */
function consoleWidth(): number {
  if (typeof document === 'undefined' || typeof getComputedStyle !== 'function') return DEFAULT_CONSOLE_W;
  const parsed = Number.parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--console-w'));
  return Number.isFinite(parsed) && parsed > 0 ? parsed : DEFAULT_CONSOLE_W;
}

/**
 * How far from the viewport's right edge the panel must stay (audit
 * 2026-09-21 responsive-v1 item 2): with the Console open as a right-edge
 * card (>= 1280px) it is the Console's width plus a gutter each side, the
 * offset the DOCKED panel already takes in CSS
 * (`[data-console="open"] .genie:not(.is-undocked)`); 0 otherwise.
 */
export function genieRightInset(consoleOpen: boolean, viewport: GenieSize, consoleW?: number): number {
  // --console-w is read only while the Console is docked: never on a closed-Console render.
  return consoleOpen && viewport.w >= CONSOLE_DOCK_MIN_VIEWPORT ? (consoleW ?? consoleWidth()) + VIEWPORT_GUTTER * 2 : 0;
}

/** The x the panel treats as the viewport's right edge: the Console's left edge while it is open. */
function usableRight(viewport: GenieSize, rightInset: number): number {
  return viewport.w - Math.max(0, rightInset - VIEWPORT_GUTTER);
}

export function clampGeniePosition(
  position: GeniePosition,
  width: number,
  height: number,
  viewport = currentViewport(),
  rightInset = 0,
): GeniePosition {
  if (!viewport) return position;
  const maxX = Math.max(VIEWPORT_GUTTER, usableRight(viewport, rightInset) - width - VIEWPORT_GUTTER);
  const maxY = Math.max(VIEWPORT_GUTTER, viewport.h - height - VIEWPORT_GUTTER);
  return {
    x: Math.min(maxX, Math.max(VIEWPORT_GUTTER, position.x)),
    y: Math.min(maxY, Math.max(VIEWPORT_GUTTER, position.y)),
  };
}

export function snapGeniePosition(
  position: GeniePosition,
  width: number,
  height: number,
  viewport = currentViewport(),
  rightInset = 0,
): GeniePosition | null {
  if (!viewport) return position;
  const right = usableRight(viewport, rightInset);
  const nearLeft = position.x < SNAP_DISTANCE;
  const nearRight = position.x + width > right - SNAP_DISTANCE;
  const nearTop = position.y < SNAP_DISTANCE;
  const nearBottom = position.y + height > viewport.h - SNAP_DISTANCE;
  if (nearRight && nearBottom) return null;
  if (nearLeft && nearTop) return { x: VIEWPORT_GUTTER, y: VIEWPORT_GUTTER };
  if (nearRight && nearTop) {
    return { x: right - width - VIEWPORT_GUTTER, y: VIEWPORT_GUTTER };
  }
  if (nearLeft && nearBottom) {
    return { x: VIEWPORT_GUTTER, y: viewport.h - height - VIEWPORT_GUTTER };
  }
  return position;
}

function loadGenieSize(): GenieSize {
  try {
    const raw = localStorage.getItem(SIZE_STORAGE_KEY);
    if (!raw) return DEFAULT_SIZE;
    const parsed = JSON.parse(raw) as Partial<GenieSize>;
    return {
      w: Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, Number(parsed.w) || DEFAULT_SIZE.w)),
      h: Math.min(MAX_HEIGHT, Math.max(MIN_HEIGHT, Number(parsed.h) || DEFAULT_SIZE.h)),
    };
  } catch {
    return DEFAULT_SIZE;
  }
}

function saveGenieSize(size: GenieSize): void {
  try {
    localStorage.setItem(SIZE_STORAGE_KEY, JSON.stringify(size));
  } catch {
    // A non-persisted preference is an acceptable storage-degraded state.
  }
}

function loadGeniePosition(): GeniePosition | null {
  try {
    const raw = localStorage.getItem(POSITION_STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as { pos?: Partial<GeniePosition> | null };
    if (!parsed.pos) return null;
    const x = Number(parsed.pos.x);
    const y = Number(parsed.pos.y);
    return Number.isFinite(x) && Number.isFinite(y) ? { x, y } : null;
  } catch {
    return null;
  }
}

function saveGeniePosition(position: GeniePosition | null): void {
  try {
    if (position === null) {
      localStorage.removeItem(POSITION_STORAGE_KEY);
    } else {
      localStorage.setItem(POSITION_STORAGE_KEY, JSON.stringify({ pos: position }));
    }
  } catch {
    // A non-persisted preference is an acceptable storage-degraded state.
  }
}


interface UseGenieWindowOptions {
  open: boolean;
  /** The panel element: a gesture moves it directly, outside React. */
  panelRef: RefObject<HTMLElement | null>;
  /** The Console is open (AppContext): the panel keeps clear of it. */
  consoleOpen?: boolean;
}

/** The hook's latest render values, read by the gesture handlers. */
interface GenieWindowLatest {
  size: GenieSize;
  effectiveSize: GenieSize;
  position: GeniePosition | null;
  rightInset: number;
  consoleOpen: boolean;
}

/** One pointer gesture on the panel: where it started and where it is now. */
interface GenieGesture {
  handle: ResizeHandle | null;
  pointerX: number;
  pointerY: number;
  /** The panel's left / top when the gesture began. */
  startX: number;
  startY: number;
  startW: number;
  startH: number;
  /** A resize of an undocked panel moves its left / top as well. */
  undocked: boolean;
  didMove: boolean;
  /** The latest target: left / top and size. */
  x: number;
  y: number;
  w: number;
  h: number;
}

/** The gesture in progress and its pending animation frame. */
interface GestureRuntime {
  gesture: GenieGesture | null;
  frame: number | null;
}

/** What a finished gesture commits: once, to state and to storage. */
interface GestureCommit {
  size: GenieSize;
  position: GeniePosition | null;
}

/**
 * The size a gesture's frame shows: its preferred size fitted to the
 * viewport, exactly what React renders once the gesture commits. React diffs
 * style props, not the DOM, so an unfitted frame size would outlive a commit
 * whose fitted size did not change (a panel taller than the viewport).
 */
function renderedGestureSize(gesture: GenieGesture): GenieSize {
  const viewport = currentViewport();
  return viewport ? fitGenieSizeToViewport(gesture, viewport) : gesture;
}

/**
 * One frame of a gesture, written straight onto the panel. The offset uses
 * the CSS `translate` property: it composes with the open/close `transform`
 * (which carries a --dur-slow transition) instead of replacing it, so the
 * panel tracks the pointer with no transition, and it never touches
 * visibility, aria-hidden or the docked `right` offset.
 */
function applyGestureFrame(panel: HTMLElement, gesture: GenieGesture): void {
  if (gesture.handle) {
    const { w, h } = renderedGestureSize(gesture);
    panel.style.width = `${w}px`;
    panel.style.height = `${h}px`;
    panel.style.maxHeight = `${h}px`;
  }
  const dx = gesture.x - gesture.startX;
  const dy = gesture.y - gesture.startY;
  if (dx === 0 && dy === 0) panel.style.removeProperty('translate');
  else panel.style.setProperty('translate', `${dx}px ${dy}px`);
}

/** At most one frame per batch of pointermoves. */
function scheduleGestureFrame(runtime: GestureRuntime, panelRef: RefObject<HTMLElement | null>): void {
  if (runtime.frame !== null) return;
  runtime.frame = requestAnimationFrame(() => {
    runtime.frame = null;
    const panel = panelRef.current;
    if (panel && runtime.gesture) applyGestureFrame(panel, runtime.gesture);
  });
}

function cancelGestureFrame(runtime: GestureRuntime): void {
  if (runtime.frame === null) return;
  cancelAnimationFrame(runtime.frame);
  runtime.frame = null;
}

function beginGesture(
  event: PointerEvent<HTMLElement>,
  runtime: GestureRuntime,
  latest: GenieWindowLatest,
  handle: ResizeHandle | null,
  start: GeniePosition,
): void {
  event.preventDefault();
  event.currentTarget.setPointerCapture(event.pointerId);
  const { w, h } = latest.effectiveSize;
  runtime.gesture = {
    handle,
    pointerX: event.clientX,
    pointerY: event.clientY,
    startX: start.x,
    startY: start.y,
    startW: w,
    startH: h,
    undocked: latest.position !== null,
    didMove: false,
    x: start.x,
    y: start.y,
    w,
    h,
  };
}

/** A header drag starts from the panel's rendered box: a docked panel sits left of an open Console. */
function beginDrag(event: PointerEvent<HTMLElement>, panel: HTMLElement | null, runtime: GestureRuntime, latest: GenieWindowLatest): void {
  if (event.target !== event.currentTarget || !panel) return;
  const box = panel.getBoundingClientRect();
  beginGesture(event, runtime, latest, null, { x: box.left, y: box.top });
}

function beginResize(event: PointerEvent<HTMLElement>, handle: ResizeHandle, runtime: GestureRuntime, latest: GenieWindowLatest): void {
  event.stopPropagation();
  beginGesture(event, runtime, latest, handle, latest.position ?? { x: 0, y: 0 });
}

/** Store the pointer's clamped target and schedule its frame; never React state. */
function moveGesture(
  event: PointerEvent<HTMLElement>,
  runtime: GestureRuntime,
  latest: GenieWindowLatest,
  panelRef: RefObject<HTMLElement | null>,
): void {
  const gesture = runtime.gesture;
  if (!gesture) return;
  const dx = event.clientX - gesture.pointerX;
  const dy = event.clientY - gesture.pointerY;
  const signature = gesture.handle ? RESIZE_MATRIX[gesture.handle] : null;
  if (signature) {
    gesture.w = Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, gesture.startW + signature.wSign * dx));
    gesture.h = Math.min(MAX_HEIGHT, Math.max(MIN_HEIGHT, gesture.startH + signature.hSign * dy));
  } else if (Math.abs(dx) < 3 && Math.abs(dy) < 3 && !gesture.didMove) {
    return;
  }
  gesture.didMove = true;
  if (!signature || gesture.undocked) {
    // Left / top follow the size the panel shows, not the preferred size.
    const shown = renderedGestureSize(gesture);
    const target = signature
      ? { x: gesture.startX - signature.xSign * (shown.w - gesture.startW), y: gesture.startY - signature.ySign * (shown.h - gesture.startH) }
      : { x: gesture.startX + dx, y: gesture.startY + dy };
    const clamped = clampGeniePosition(target, shown.w, shown.h, currentViewport(), latest.rightInset);
    gesture.x = clamped.x;
    gesture.y = clamped.y;
  }
  scheduleGestureFrame(runtime, panelRef);
}

/**
 * pointerup / pointercancel: cancel the pending frame and write the last one
 * now, so the DOM already shows what React commits next. Returns the commit,
 * or null for a press that never moved.
 */
function endGesture(
  event: PointerEvent<HTMLElement>,
  runtime: GestureRuntime,
  latest: GenieWindowLatest,
  panel: HTMLElement | null,
): GestureCommit | null {
  const gesture = runtime.gesture;
  if (!gesture) return null;
  runtime.gesture = null;
  if (event.currentTarget.hasPointerCapture?.(event.pointerId)) {
    event.currentTarget.releasePointerCapture(event.pointerId);
  }
  cancelGestureFrame(runtime);
  if (!gesture.didMove) {
    panel?.style.removeProperty('translate');
    return null;
  }
  if (panel) applyGestureFrame(panel, gesture);
  if (!gesture.handle) {
    return {
      size: latest.size,
      position: snapGeniePosition({ x: gesture.x, y: gesture.y }, gesture.w, gesture.h, currentViewport(), latest.rightInset),
    };
  }
  return { size: { w: gesture.w, h: gesture.h }, position: gesture.undocked ? { x: gesture.x, y: gesture.y } : null };
}

/** Arrow keys on the resize button: the next size, or null for another key. */
function keyboardResize(event: KeyboardEvent<HTMLButtonElement>, size: GenieSize): GenieSize | null {
  const step = event.shiftKey ? 96 : 24;
  let widthDelta = 0;
  let heightDelta = 0;
  if (event.key === 'ArrowLeft') widthDelta = step;
  else if (event.key === 'ArrowRight') widthDelta = -step;
  else if (event.key === 'ArrowUp') heightDelta = step;
  else if (event.key === 'ArrowDown') heightDelta = -step;
  else return null;
  event.preventDefault();
  return {
    w: Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, size.w + widthDelta)),
    h: Math.min(MAX_HEIGHT, Math.max(MIN_HEIGHT, size.h + heightDelta)),
  };
}

function samePosition(a: GeniePosition, b: GeniePosition): boolean {
  return a.x === b.x && a.y === b.y;
}

function initialViewport(): GenieSize {
  return currentViewport() ?? { w: DEFAULT_SIZE.w + VIEWPORT_GUTTER * 2, h: DEFAULT_SIZE.h + VIEWPORT_GUTTER * 2 };
}

/**
 * The floating Genie panel's size and position (audit 2026-09-21
 * runtime-v1, responsive-v1 item 2, runtime-03).
 *
 * A drag or a resize never re-renders the chat while the pointer moves: each
 * pointermove stores its clamped target and schedules at most one
 * requestAnimationFrame, which writes the panel's offset (drag) or its size
 * (resize) directly. The gesture is committed to React state and
 * localStorage ONCE, on pointerup / pointercancel, after its last frame is
 * written synchronously; a layout effect after that commit drops the offset,
 * so no frame shows both the offset and the new left / top, or neither.
 * Keyboard resize stays on state. The gesture logic is module-level and the
 * handlers read the latest render through a ref, so the compiled hook stays
 * small and its handlers never change identity.
 *
 * While the Console is open as a right-edge card the panel keeps clear of it
 * (`genieRightInset`): drags are clamped and snapped to the Console's left
 * edge, and opening the Console re-clamps an undocked panel. Otherwise a
 * position the user chose is kept.
 */
export function useGenieWindow({ open, panelRef, consoleOpen = false }: UseGenieWindowOptions) {
  const [size, setSize] = useState<GenieSize>(loadGenieSize);
  const [viewport, setViewport] = useState<GenieSize>(initialViewport);
  const effectiveSize = fitGenieSizeToViewport(size, viewport);
  const rightInset = genieRightInset(consoleOpen, viewport);
  const [position, setPosition] = useState<GeniePosition | null>(() => {
    const restored = loadGeniePosition();
    return restored ? clampGeniePosition(restored, effectiveSize.w, effectiveSize.h, viewport, rightInset) : null;
  });
  /** Bumped by each committed gesture: its layout effect drops the offset. */
  const [committedGestures, setCommittedGestures] = useState(0);
  const runtimeRef = useRef<GestureRuntime>({ gesture: null, frame: null });
  const latestRef = useRef<GenieWindowLatest>({ size, effectiveSize, position, rightInset, consoleOpen });

  useLayoutEffect(() => {
    latestRef.current = { size, effectiveSize, position, rightInset, consoleOpen };
  });

  useEffect(() => {
    function onResize() {
      const { size: preferred, consoleOpen: open } = latestRef.current;
      const nextViewport = { w: window.innerWidth, h: window.innerHeight };
      const nextSize = fitGenieSizeToViewport(preferred, nextViewport);
      const nextInset = genieRightInset(open, nextViewport);
      setViewport(nextViewport);
      setPosition((current) => (
        current ? clampGeniePosition(current, nextSize.w, nextSize.h, nextViewport, nextInset) : current
      ));
    }
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);

  // Opening the panel, a new size, or the Console opening re-clamps an
  // undocked panel; nothing else moves a position the user chose.
  // (A no-op clamp sets nothing, so a committed gesture stays ONE commit.)
  useEffect(() => {
    if (!open || !position) return;
    const clamped = clampGeniePosition(position, effectiveSize.w, effectiveSize.h, currentViewport(), rightInset);
    if (!samePosition(clamped, position)) setPosition(clamped);
  }, [effectiveSize.h, effectiveSize.w, open, position, rightInset]);

  // After a gesture's commit the panel's left / top / size are React's
  // again: drop the gesture's offset before the browser paints.
  useLayoutEffect(() => {
    if (committedGestures > 0) panelRef.current?.style.removeProperty('translate');
  }, [committedGestures, panelRef]);

  useEffect(() => {
    const runtime = runtimeRef.current;
    return () => cancelGestureFrame(runtime);
  }, []);

  const finish = (event: PointerEvent<HTMLElement>) => {
    const next = endGesture(event, runtimeRef.current, latestRef.current, panelRef.current);
    if (!next) return;
    setSize(next.size);
    setPosition(next.position);
    setCommittedGestures((count) => count + 1);
    saveGenieSize(next.size);
    saveGeniePosition(next.position);
  };

  return {
    effectiveSize,
    position,
    beginResize: (handle: ResizeHandle) => (event: PointerEvent<HTMLDivElement>) =>
      beginResize(event, handle, runtimeRef.current, latestRef.current),
    moveResize: (event: PointerEvent<HTMLDivElement>) => moveGesture(event, runtimeRef.current, latestRef.current, panelRef),
    endResize: finish,
    onResizeKeyDown: (event: KeyboardEvent<HTMLButtonElement>) => {
      const next = keyboardResize(event, latestRef.current.size);
      if (!next) return;
      setSize(next);
      saveGenieSize(next);
    },
    onDragPointerDown: (event: PointerEvent<HTMLDivElement>) =>
      beginDrag(event, panelRef.current, runtimeRef.current, latestRef.current),
    onDragPointerMove: (event: PointerEvent<HTMLDivElement>) =>
      moveGesture(event, runtimeRef.current, latestRef.current, panelRef),
    onDragPointerUp: finish,
    redock: () => {
      setPosition(null);
      saveGeniePosition(null);
    },
  };
}
