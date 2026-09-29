import {
  useCallback,
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
export function genieRightInset(consoleOpen: boolean, viewport: GenieSize, consoleW = consoleWidth()): number {
  return consoleOpen && viewport.w >= CONSOLE_DOCK_MIN_VIEWPORT ? consoleW + VIEWPORT_GUTTER * 2 : 0;
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

/** One pointer gesture on the panel: where it started and where it is now. */
interface GenieGesture {
  kind: 'drag' | 'resize';
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

/**
 * One frame of a gesture, written straight onto the panel. The offset uses
 * the CSS `translate` property: it composes with the open/close `transform`
 * (which carries a --dur-slow transition) instead of replacing it, so the
 * panel tracks the pointer with no transition, and it never touches
 * visibility, aria-hidden or the docked `right` offset.
 */
function applyGestureFrame(panel: HTMLElement, gesture: GenieGesture): void {
  if (gesture.kind === 'resize') {
    panel.style.width = `${gesture.w}px`;
    panel.style.height = `${gesture.h}px`;
    panel.style.maxHeight = `${gesture.h}px`;
  }
  const dx = gesture.x - gesture.startX;
  const dy = gesture.y - gesture.startY;
  if (dx === 0 && dy === 0) panel.style.removeProperty('translate');
  else panel.style.setProperty('translate', `${dx}px ${dy}px`);
}

function releasePointer(event: PointerEvent<HTMLElement>): void {
  if (event.currentTarget.hasPointerCapture?.(event.pointerId)) {
    event.currentTarget.releasePointerCapture(event.pointerId);
  }
}

function samePosition(a: GeniePosition, b: GeniePosition): boolean {
  return a.x === b.x && a.y === b.y;
}

/**
 * The floating Genie panel's size and position (audit 2026-09-21
 * runtime-v1, responsive-v1 item 2, runtime-03).
 *
 * A drag or a resize never re-renders the chat while the pointer moves: each
 * pointermove stores its clamped target in a ref and schedules at most one
 * requestAnimationFrame, which writes the panel's offset (drag) or its size
 * (resize) directly. The gesture is committed to React state and
 * localStorage ONCE, on pointerup / pointercancel, after its last frame is
 * written synchronously; a layout effect after that commit drops the offset,
 * so no frame shows both the offset and the new left / top, or neither.
 * Keyboard resize stays on state. A drag starts from the panel's rendered
 * box, so undocking with the Console open does not jump.
 *
 * While the Console is open as a right-edge card the panel keeps clear of it
 * (`genieRightInset`): drags are clamped and snapped to the Console's left
 * edge, and opening the Console re-clamps an undocked panel. Otherwise a
 * position the user chose is kept.
 */
export function useGenieWindow({ open, panelRef, consoleOpen = false }: UseGenieWindowOptions) {
  const [size, setSize] = useState<GenieSize>(() => loadGenieSize());
  const [viewport, setViewport] = useState<GenieSize>(() => ({
    w: typeof window === 'undefined'
      ? DEFAULT_SIZE.w + VIEWPORT_GUTTER * 2
      : window.innerWidth,
    h: typeof window === 'undefined'
      ? DEFAULT_SIZE.h + VIEWPORT_GUTTER * 2
      : window.innerHeight,
  }));
  const effectiveSize = fitGenieSizeToViewport(size, viewport);
  const rightInset = genieRightInset(consoleOpen, viewport);
  const [position, setPosition] = useState<GeniePosition | null>(() => {
    const restored = loadGeniePosition();
    return restored
      ? clampGeniePosition(restored, effectiveSize.w, effectiveSize.h, viewport, rightInset)
      : null;
  });
  const gestureRef = useRef<GenieGesture | null>(null);
  const frameRef = useRef<number | null>(null);
  /** Bumped by each committed gesture: its layout effect drops the offset. */
  const [committedGestures, setCommittedGestures] = useState(0);

  useEffect(() => {
    function onResize() {
      const nextViewport = { w: window.innerWidth, h: window.innerHeight };
      const nextSize = fitGenieSizeToViewport(size, nextViewport);
      const nextInset = genieRightInset(consoleOpen, nextViewport);
      setViewport(nextViewport);
      setPosition((current) => (
        current ? clampGeniePosition(current, nextSize.w, nextSize.h, nextViewport, nextInset) : current
      ));
    }
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, [consoleOpen, size]);

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
    if (committedGestures === 0) return;
    panelRef.current?.style.removeProperty('translate');
  }, [committedGestures, panelRef]);

  useEffect(() => () => {
    if (frameRef.current !== null) cancelAnimationFrame(frameRef.current);
  }, []);

  const scheduleFrame = useCallback(() => {
    if (frameRef.current !== null) return;
    frameRef.current = requestAnimationFrame(() => {
      frameRef.current = null;
      const panel = panelRef.current;
      const gesture = gestureRef.current;
      if (panel && gesture) applyGestureFrame(panel, gesture);
    });
  }, [panelRef]);

  const finishGesture = useCallback(
    (event: PointerEvent<HTMLElement>) => {
      const gesture = gestureRef.current;
      if (!gesture) return;
      gestureRef.current = null;
      releasePointer(event);
      if (frameRef.current !== null) {
        cancelAnimationFrame(frameRef.current);
        frameRef.current = null;
      }
      const panel = panelRef.current;
      if (!gesture.didMove) {
        panel?.style.removeProperty('translate');
        return;
      }
      // The last frame, now: the DOM already shows what React commits next.
      if (panel) applyGestureFrame(panel, gesture);
      const nextSize = gesture.kind === 'resize' ? { w: gesture.w, h: gesture.h } : size;
      const nextPosition = gesture.kind === 'drag'
        ? snapGeniePosition({ x: gesture.x, y: gesture.y }, gesture.w, gesture.h, currentViewport(), rightInset)
        : gesture.undocked
          ? { x: gesture.x, y: gesture.y }
          : null;
      setSize(nextSize);
      setPosition(nextPosition);
      setCommittedGestures((count) => count + 1);
      saveGenieSize(nextSize);
      saveGeniePosition(nextPosition);
    },
    [panelRef, rightInset, size],
  );

  const onDragPointerDown = useCallback(
    (event: PointerEvent<HTMLDivElement>) => {
      if (event.target !== event.currentTarget) return;
      const panel = panelRef.current;
      if (!panel) return;
      event.preventDefault();
      event.currentTarget.setPointerCapture(event.pointerId);
      // The rendered box: a docked panel sits left of an open Console.
      const box = panel.getBoundingClientRect();
      gestureRef.current = {
        kind: 'drag',
        handle: null,
        pointerX: event.clientX,
        pointerY: event.clientY,
        startX: box.left,
        startY: box.top,
        startW: effectiveSize.w,
        startH: effectiveSize.h,
        undocked: position !== null,
        didMove: false,
        x: box.left,
        y: box.top,
        w: effectiveSize.w,
        h: effectiveSize.h,
      };
    },
    [effectiveSize.h, effectiveSize.w, panelRef, position],
  );

  const onDragPointerMove = useCallback(
    (event: PointerEvent<HTMLDivElement>) => {
      const gesture = gestureRef.current;
      if (!gesture || gesture.kind !== 'drag') return;
      const dx = event.clientX - gesture.pointerX;
      const dy = event.clientY - gesture.pointerY;
      if (Math.abs(dx) < 3 && Math.abs(dy) < 3 && !gesture.didMove) return;
      gesture.didMove = true;
      const target = clampGeniePosition(
        { x: gesture.startX + dx, y: gesture.startY + dy },
        gesture.w,
        gesture.h,
        currentViewport(),
        rightInset,
      );
      gesture.x = target.x;
      gesture.y = target.y;
      scheduleFrame();
    },
    [rightInset, scheduleFrame],
  );

  const redock = useCallback(() => {
    setPosition(null);
    saveGeniePosition(null);
  }, []);

  const beginResize = useCallback(
    (handle: ResizeHandle) => (event: PointerEvent<HTMLDivElement>) => {
      event.preventDefault();
      event.stopPropagation();
      event.currentTarget.setPointerCapture(event.pointerId);
      const startX = position?.x ?? 0;
      const startY = position?.y ?? 0;
      gestureRef.current = {
        kind: 'resize',
        handle,
        pointerX: event.clientX,
        pointerY: event.clientY,
        startX,
        startY,
        startW: effectiveSize.w,
        startH: effectiveSize.h,
        undocked: position !== null,
        didMove: false,
        x: startX,
        y: startY,
        w: effectiveSize.w,
        h: effectiveSize.h,
      };
    },
    [effectiveSize.h, effectiveSize.w, position],
  );

  const moveResize = useCallback(
    (event: PointerEvent<HTMLDivElement>) => {
      const gesture = gestureRef.current;
      if (!gesture || gesture.kind !== 'resize' || !gesture.handle) return;
      const signature = RESIZE_MATRIX[gesture.handle];
      const dx = event.clientX - gesture.pointerX;
      const dy = event.clientY - gesture.pointerY;
      const width = Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, gesture.startW + signature.wSign * dx));
      const height = Math.min(MAX_HEIGHT, Math.max(MIN_HEIGHT, gesture.startH + signature.hSign * dy));
      gesture.didMove = true;
      gesture.w = width;
      gesture.h = height;
      if (gesture.undocked) {
        const target = clampGeniePosition(
          {
            x: gesture.startX - signature.xSign * (width - gesture.startW),
            y: gesture.startY - signature.ySign * (height - gesture.startH),
          },
          width,
          height,
          currentViewport(),
          rightInset,
        );
        gesture.x = target.x;
        gesture.y = target.y;
      }
      scheduleFrame();
    },
    [rightInset, scheduleFrame],
  );

  const onResizeKeyDown = useCallback(
    (event: KeyboardEvent<HTMLButtonElement>) => {
      const step = event.shiftKey ? 96 : 24;
      let widthDelta = 0;
      let heightDelta = 0;
      if (event.key === 'ArrowLeft') widthDelta = step;
      else if (event.key === 'ArrowRight') widthDelta = -step;
      else if (event.key === 'ArrowUp') heightDelta = step;
      else if (event.key === 'ArrowDown') heightDelta = -step;
      else return;
      event.preventDefault();
      const next = {
        w: Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, size.w + widthDelta)),
        h: Math.min(MAX_HEIGHT, Math.max(MIN_HEIGHT, size.h + heightDelta)),
      };
      setSize(next);
      saveGenieSize(next);
    },
    [size],
  );

  return {
    effectiveSize,
    position,
    beginResize,
    moveResize,
    endResize: finishGesture,
    onResizeKeyDown,
    onDragPointerDown,
    onDragPointerMove,
    onDragPointerUp: finishGesture,
    redock,
  };
}
