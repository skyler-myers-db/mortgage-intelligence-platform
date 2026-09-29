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
 * pointer handlers are attached to (the tooltip layer, which spans the plot).
 */
import { useState, type Dispatch, type FocusEvent, type KeyboardEvent, type PointerEvent, type SetStateAction } from 'react';

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

/** The keyboard cursor, and whether a keyboard move (not focus) put it there. */
type KeyCursor = { index: number; moved: boolean } | null;

const CURSOR_KEYS: ReadonlySet<string> = new Set(['ArrowLeft', 'ArrowRight', 'Home', 'End']);

/** The index a cursor key moves to from `current` (null: no keyboard cursor yet). */
function stepIndex(key: string, current: number | null, count: number): number {
  if (key === 'Home') return 0;
  if (key === 'End') return count - 1;
  if (current === null) return 0;
  return key === 'ArrowLeft' ? Math.max(0, current - 1) : Math.min(count - 1, current + 1);
}

/**
 * The cursor's handlers. The key handler reads the keyboard cursor through a
 * functional update, so the handlers depend only on the points and the hook
 * memoizes them as one unit (see "Compiled shells" in CountChart.tsx).
 */
function cursorHandlers(
  count: number,
  positions: readonly number[],
  setHover: Dispatch<SetStateAction<number | null>>,
  setKey: Dispatch<SetStateAction<KeyCursor>>,
): Pick<ChartCursor, 'surfaceProps' | 'plotProps'> {
  return {
    surfaceProps: {
      onPointerMove: (event) => {
        const rect = event.currentTarget.getBoundingClientRect();
        if (count > 0 && rect.width > 0) setHover(nearestIndex(positions, ((event.clientX - rect.left) / rect.width) * 100));
      },
      onPointerLeave: () => setHover(null),
    },
    plotProps: {
      tabIndex: 0,
      onKeyDown: (event) => {
        const { key } = event;
        if (count <= 0 || !CURSOR_KEYS.has(key)) return;
        event.preventDefault();
        setKey((current) => ({ index: stepIndex(key, clampIndex(current?.index ?? null, count), count), moved: true }));
      },
      // The plot holds no focusable descendant, so its focus and blur are its own.
      onFocus: () => {
        if (count > 0) setKey((current) => current ?? { index: 0, moved: false });
      },
      onBlur: () => setKey(null),
    },
  };
}

export function useChartCursor(count: number, positions: readonly number[]): ChartCursor {
  const [hoverIndex, setHoverIndex] = useState<number | null>(null);
  const [key, setKey] = useState<KeyCursor>(null);
  const { surfaceProps, plotProps } = cursorHandlers(count, positions, setHoverIndex, setKey);
  return {
    index: clampIndex(hoverIndex ?? key?.index ?? null, count),
    liveIndex: key?.moved ? clampIndex(key.index, count) : null,
    surfaceProps,
    plotProps,
  };
}
