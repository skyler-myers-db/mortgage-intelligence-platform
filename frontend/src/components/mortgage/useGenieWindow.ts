import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type PointerEvent,
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

export function clampGeniePosition(
  position: GeniePosition,
  width: number,
  height: number,
  viewport = currentViewport(),
): GeniePosition {
  if (!viewport) return position;
  const maxX = Math.max(VIEWPORT_GUTTER, viewport.w - width - VIEWPORT_GUTTER);
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
): GeniePosition | null {
  if (!viewport) return position;
  const nearLeft = position.x < SNAP_DISTANCE;
  const nearRight = position.x + width > viewport.w - SNAP_DISTANCE;
  const nearTop = position.y < SNAP_DISTANCE;
  const nearBottom = position.y + height > viewport.h - SNAP_DISTANCE;
  if (nearRight && nearBottom) return null;
  if (nearLeft && nearTop) return { x: VIEWPORT_GUTTER, y: VIEWPORT_GUTTER };
  if (nearRight && nearTop) {
    return { x: viewport.w - width - VIEWPORT_GUTTER, y: VIEWPORT_GUTTER };
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
}

export function useGenieWindow({ open }: UseGenieWindowOptions) {
  'use no memo';
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
  const [position, setPosition] = useState<GeniePosition | null>(() => {
    const restored = loadGeniePosition();
    return restored
      ? clampGeniePosition(restored, effectiveSize.w, effectiveSize.h)
      : null;
  });
  const resizeOriginRef = useRef<{
    startX: number;
    startY: number;
    startW: number;
    startH: number;
    startPosX: number | null;
    startPosY: number | null;
    handle: ResizeHandle;
  } | null>(null);
  const dragOriginRef = useRef<{
    pointerX: number;
    pointerY: number;
    startX: number;
    startY: number;
    didMove: boolean;
  } | null>(null);

  useEffect(() => {
    function onResize() {
      const nextViewport = { w: window.innerWidth, h: window.innerHeight };
      const nextSize = fitGenieSizeToViewport(size, nextViewport);
      setViewport(nextViewport);
      setPosition((current) => (
        current ? clampGeniePosition(current, nextSize.w, nextSize.h, nextViewport) : current
      ));
    }
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, [size]);

  useEffect(() => {
    if (!open) return;
    setPosition((current) => (
      current ? clampGeniePosition(current, effectiveSize.w, effectiveSize.h) : current
    ));
  }, [effectiveSize.h, effectiveSize.w, open]);

  const onDragPointerDown = useCallback(
    (event: PointerEvent<HTMLDivElement>) => {
      if (event.target !== event.currentTarget) return;
      event.preventDefault();
      event.currentTarget.setPointerCapture(event.pointerId);
      const startPosition = position ?? (
        typeof window !== 'undefined'
          ? {
              x: window.innerWidth - effectiveSize.w - VIEWPORT_GUTTER,
              y: window.innerHeight - effectiveSize.h - VIEWPORT_GUTTER,
            }
          : { x: 0, y: 0 }
      );
      dragOriginRef.current = {
        pointerX: event.clientX,
        pointerY: event.clientY,
        startX: startPosition.x,
        startY: startPosition.y,
        didMove: false,
      };
    },
    [effectiveSize.h, effectiveSize.w, position],
  );

  const onDragPointerMove = useCallback(
    (event: PointerEvent<HTMLDivElement>) => {
      const origin = dragOriginRef.current;
      if (!origin) return;
      const dx = event.clientX - origin.pointerX;
      const dy = event.clientY - origin.pointerY;
      if (Math.abs(dx) < 3 && Math.abs(dy) < 3 && !origin.didMove) return;
      origin.didMove = true;
      setPosition(clampGeniePosition(
        { x: origin.startX + dx, y: origin.startY + dy },
        effectiveSize.w,
        effectiveSize.h,
      ));
    },
    [effectiveSize.h, effectiveSize.w],
  );

  const onDragPointerUp = useCallback(
    (event: PointerEvent<HTMLDivElement>) => {
      const origin = dragOriginRef.current;
      if (!origin) return;
      event.currentTarget.releasePointerCapture(event.pointerId);
      dragOriginRef.current = null;
      if (!origin.didMove) return;
      setPosition((current) => {
        if (!current) return current;
        const snapped = snapGeniePosition(current, effectiveSize.w, effectiveSize.h);
        saveGeniePosition(snapped);
        return snapped;
      });
    },
    [effectiveSize.h, effectiveSize.w],
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
      resizeOriginRef.current = {
        startX: event.clientX,
        startY: event.clientY,
        startW: effectiveSize.w,
        startH: effectiveSize.h,
        startPosX: position?.x ?? null,
        startPosY: position?.y ?? null,
        handle,
      };
    },
    [effectiveSize.h, effectiveSize.w, position],
  );

  const moveResize = useCallback((event: PointerEvent<HTMLDivElement>) => {
    const origin = resizeOriginRef.current;
    if (!origin) return;
    const signature = RESIZE_MATRIX[origin.handle];
    const dx = event.clientX - origin.startX;
    const dy = event.clientY - origin.startY;
    const width = Math.min(
      MAX_WIDTH,
      Math.max(MIN_WIDTH, origin.startW + signature.wSign * dx),
    );
    const height = Math.min(
      MAX_HEIGHT,
      Math.max(MIN_HEIGHT, origin.startH + signature.hSign * dy),
    );
    setSize({ w: width, h: height });
    if (origin.startPosX !== null && origin.startPosY !== null) {
      const actualWidthDelta = width - origin.startW;
      const actualHeightDelta = height - origin.startH;
      setPosition(clampGeniePosition(
        {
          x: origin.startPosX - signature.xSign * actualWidthDelta,
          y: origin.startPosY - signature.ySign * actualHeightDelta,
        },
        width,
        height,
      ));
    }
  }, []);

  const endResize = useCallback(
    (event: PointerEvent<HTMLDivElement>) => {
      if (!resizeOriginRef.current) return;
      event.currentTarget.releasePointerCapture(event.pointerId);
      resizeOriginRef.current = null;
      saveGenieSize(size);
      if (position) saveGeniePosition(position);
    },
    [position, size],
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
    endResize,
    onResizeKeyDown,
    onDragPointerDown,
    onDragPointerMove,
    onDragPointerUp,
    redock,
  };
}
