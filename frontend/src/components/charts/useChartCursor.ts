/**
 * One cursor for every kit chart (2026-09-21 audit dataviz-07 step 2,
 * dataviz-10, dataviz-v2): the pointer snaps to the nearest point, and the
 * focused plot walks the points with ArrowLeft / ArrowRight / Home / End.
 *
 *  - `index` is what the tooltip shows: the hovered point, else the keyboard
 *    cursor. Focus shows the first point; blur clears the keyboard cursor.
 *  - `liveIndex` changes ONLY on a keyboard move. The frame's aria-live
 *    region reads it, so a pointer sweep never floods a screen reader, and
 *    focus alone announces nothing (the plot's description already speaks).
 *
 * `positions` are the points' x in percent (0-100) of the surface the
 * pointer handlers are attached to.
 */
import { useRef, useState, type FocusEvent, type KeyboardEvent, type PointerEvent, type RefObject } from 'react';

export interface ChartCursorPlotProps {
  tabIndex: 0;
  onKeyDown: (event: KeyboardEvent<HTMLElement>) => void;
  onFocus: (event: FocusEvent<HTMLElement>) => void;
  onBlur: (event: FocusEvent<HTMLElement>) => void;
}

export interface ChartCursorSurfaceProps {
  onPointerMove: (event: PointerEvent<HTMLElement>) => void;
  onPointerLeave: () => void;
}

export interface ChartCursor {
  index: number | null;
  liveIndex: number | null;
  surfaceRef: RefObject<HTMLDivElement | null>;
  surfaceProps: ChartCursorSurfaceProps;
  plotProps: ChartCursorPlotProps;
}

function clampIndex(index: number | null, count: number): number | null {
  if (index === null || count <= 0) return null;
  return Math.min(Math.max(index, 0), count - 1);
}

/** Index of the position nearest `pct`; the first wins a tie. */
export function nearestIndex(positions: readonly number[], pct: number): number {
  let nearest = 0;
  for (let i = 1; i < positions.length; i += 1) {
    if (Math.abs(positions[i] - pct) < Math.abs(positions[nearest] - pct)) nearest = i;
  }
  return nearest;
}

export function useChartCursor(count: number, positions: readonly number[]): ChartCursor {
  const surfaceRef = useRef<HTMLDivElement | null>(null);
  const [hoverIndex, setHoverIndex] = useState<number | null>(null);
  const [keyIndex, setKeyIndex] = useState<number | null>(null);
  const [liveIndex, setLiveIndex] = useState<number | null>(null);

  const onPointerMove = (event: PointerEvent<HTMLElement>) => {
    if (count <= 0 || positions.length === 0) return;
    const rect = (surfaceRef.current ?? event.currentTarget).getBoundingClientRect();
    if (rect.width === 0) return;
    setHoverIndex(nearestIndex(positions, ((event.clientX - rect.left) / rect.width) * 100));
  };

  const onKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    if (count <= 0) return;
    const current = clampIndex(keyIndex, count);
    let next: number;
    if (event.key === 'ArrowRight') next = current === null ? 0 : Math.min(count - 1, current + 1);
    else if (event.key === 'ArrowLeft') next = current === null ? 0 : Math.max(0, current - 1);
    else if (event.key === 'Home') next = 0;
    else if (event.key === 'End') next = count - 1;
    else return;
    event.preventDefault();
    setKeyIndex(next);
    setLiveIndex(next);
  };

  const onFocus = (event: FocusEvent<HTMLElement>) => {
    if (event.target !== event.currentTarget || count <= 0) return;
    setKeyIndex((current) => current ?? 0);
  };

  const onBlur = (event: FocusEvent<HTMLElement>) => {
    if (event.target !== event.currentTarget) return;
    setKeyIndex(null);
    setLiveIndex(null);
  };

  return {
    index: clampIndex(hoverIndex ?? keyIndex, count),
    liveIndex: clampIndex(liveIndex, count),
    surfaceRef,
    surfaceProps: { onPointerMove, onPointerLeave: () => setHoverIndex(null) },
    plotProps: { tabIndex: 0, onKeyDown, onFocus, onBlur },
  };
}
