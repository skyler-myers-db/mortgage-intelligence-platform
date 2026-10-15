/**
 * One cursor for every kit chart (2026-09-21 audit dataviz-07 step 2,
 * dataviz-10, dataviz-v2): the pointer snaps to the nearest point, and the
 * focused plot walks the points with ArrowLeft / ArrowRight / Home / End.
 *
 *  - `index` is what the tooltip shows: the hovered point, else the keyboard
 *    cursor. Focus shows the first point; blur clears the keyboard cursor.
 *    A cursor that focus made from a POINTER PRESS on the plot (no keyboard
 *    move since) goes with the pointer: pointerleave clears it, so the
 *    index-0 tip no longer stays up after a click and a move away. Tab focus
 *    and a keyboard-moved cursor survive pointerleave. Never gated on
 *    :focus-visible.
 *  - `liveIndex` changes ONLY on a keyboard move. The frame's aria-live
 *    region reads it, so a pointer sweep never floods a screen reader, and
 *    focus alone announces nothing (the plot's description already speaks).
 *
 * `positions` are the points' x in percent (0-100) of the surface the
 * pointer handlers are attached to (the tooltip layer, which spans the plot).
 */
import {
  useState,
  type Dispatch,
  type FocusEvent,
  type KeyboardEvent,
  type PointerEvent,
  type SetStateAction,
} from 'react';

export interface ChartCursorPlotProps {
  tabIndex: 0;
  onKeyDown: (event: KeyboardEvent<HTMLElement>) => void;
  onFocus: (event: FocusEvent<HTMLElement>) => void;
  onBlur: (event: FocusEvent<HTMLElement>) => void;
  /** A press on the plot (it bubbles from the tooltip layer): the focus it makes follows the pointer. */
  onPointerDown?: () => void;
  onPointerUp?: () => void;
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

/**
 * The keyboard cursor: whether a keyboard move (not focus) put it there, and
 * whether the focus that made it came from a pointer press on the plot.
 */
type KeyCursor = { index: number; moved: boolean; fromPointer: boolean } | null;

/**
 * The keyboard cursor, and a pointer press on the plot that no focus has
 * consumed yet. One state, so the focus a press causes reads the press in
 * the same queue of functional updates (no ref read during render).
 */
interface KeyState {
  cursor: KeyCursor;
  pressed: boolean;
}

const IDLE: KeyState = { cursor: null, pressed: false };

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
 * functional update, so the handlers depend only on the points: the compiled
 * hook memoizes them as one unit, and a cursor move hands the plot and the
 * tooltip the same handler objects.
 */
function cursorHandlers(
  count: number,
  positions: readonly number[],
  setHover: Dispatch<SetStateAction<number | null>>,
  setKey: Dispatch<SetStateAction<KeyState>>,
): Pick<ChartCursor, 'surfaceProps' | 'plotProps'> {
  return {
    surfaceProps: {
      onPointerMove: (event) => {
        const rect = event.currentTarget.getBoundingClientRect();
        if (count > 0 && rect.width > 0) setHover(nearestIndex(positions, ((event.clientX - rect.left) / rect.width) * 100));
      },
      onPointerLeave: () => {
        setHover(null);
        // A cursor a pointer press made, never moved by a key, leaves with the pointer.
        setKey((state) => (state.cursor?.fromPointer && !state.cursor.moved ? { ...state, cursor: null } : state));
      },
    },
    plotProps: {
      tabIndex: 0,
      onKeyDown: (event) => {
        const { key } = event;
        if (count <= 0 || !CURSOR_KEYS.has(key)) return;
        event.preventDefault();
        setKey((state) => ({
          cursor: { index: stepIndex(key, clampIndex(state.cursor?.index ?? null, count), count), moved: true, fromPointer: false },
          pressed: false,
        }));
      },
      // The plot holds no focusable descendant, so its focus and blur are its own.
      onFocus: () => {
        setKey((state) => (state.cursor !== null && !state.pressed
          ? state
          : {
              cursor: state.cursor ?? (count > 0 ? { index: 0, moved: false, fromPointer: state.pressed } : null),
              pressed: false,
            }));
      },
      onBlur: () => setKey(IDLE),
      // pointerdown runs before the focus it causes, pointerup after it.
      onPointerDown: () => setKey((state) => (state.pressed ? state : { ...state, pressed: true })),
      onPointerUp: () => setKey((state) => (state.pressed ? { ...state, pressed: false } : state)),
    },
  };
}

export function useChartCursor(count: number, positions: readonly number[]): ChartCursor {
  const [hoverIndex, setHoverIndex] = useState<number | null>(null);
  const [{ cursor: key }, setKey] = useState<KeyState>(IDLE);
  const { surfaceProps, plotProps } = cursorHandlers(count, positions, setHoverIndex, setKey);
  return {
    index: clampIndex(hoverIndex ?? key?.index ?? null, count),
    liveIndex: key?.moved ? clampIndex(key.index, count) : null,
    surfaceProps,
    plotProps,
  };
}
