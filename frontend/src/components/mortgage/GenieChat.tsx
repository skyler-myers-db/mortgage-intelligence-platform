import { useCallback, useEffect, useRef, useState } from 'react';
import { useApp } from '../AppContext';
import { api } from '../../lib/api';
import type { GenieActionSuggestion, GenieAnswer as GenieAnswerShape } from '../../types';
import { Icon } from '../Icon';
import { Button, Chip, EvidenceChip } from '../Primitives';
import { GenieAnswer } from './GenieAnswer';
import { GenieProgress } from './GenieProgress';
import { DRAWER_SOURCES } from '../../lib/drawerSources';
import { isGenieFollowUpQuestion } from '../../lib/genieSession';

// 2026-05-04 (FIX Δ2): persisted size for the floating panel. The
// ranges below cap at "still feels like a chat panel" — bigger than
// the default 420×640 default (so users can read longer answers
// without scrolling) but smaller than a full-screen takeover (which
// would conflict with the /ask-genie deep-dive route). Persisted
// per-browser via localStorage so a user's preferred size sticks
// across sessions. Storage key is namespaced under mip- to avoid
// collisions with sibling apps in the workspace.
const SIZE_STORAGE_KEY = 'mip-genie-chat-size-v1';
const MIN_W = 360;
const MAX_W = 900;
const MIN_H = 400;
const MAX_H = 900;
const DEFAULT_SIZE = { w: 420, h: 640 };

interface GenieSize {
  w: number;
  h: number;
}

function loadGenieSize(): GenieSize {
  try {
    const raw = localStorage.getItem(SIZE_STORAGE_KEY);
    if (!raw) return DEFAULT_SIZE;
    const parsed = JSON.parse(raw) as Partial<GenieSize>;
    const w = Math.min(MAX_W, Math.max(MIN_W, Number(parsed.w) || DEFAULT_SIZE.w));
    const h = Math.min(MAX_H, Math.max(MIN_H, Number(parsed.h) || DEFAULT_SIZE.h));
    return { w, h };
  } catch {
    return DEFAULT_SIZE;
  }
}

function saveGenieSize(size: GenieSize): void {
  try {
    localStorage.setItem(SIZE_STORAGE_KEY, JSON.stringify(size));
  } catch {
    // localStorage unavailable (private mode, full storage). Drop on
    // the floor — non-persistence is a degraded UX, not broken.
  }
}

// 2026-05-04 (FIX ε2): undock + drag-to-position support.
//
// Position model: when undocked the panel is anchored top-left of
// the viewport at ({x, y}). When docked (default) it stays glued
// to bottom-right and `pos` is null. Persisted under its own key
// so toggling between docked/undocked across sessions feels right.
//
// Snap rule: while dragging, if the panel's pointer-anchored top-
// left ends up within SNAP_PX of any viewport corner, the position
// snaps to that corner. The bottom-right corner snap re-DOCKS
// (collapses pos to null) so a user who liked the bottom-right
// origin can return to it without remembering keyboard shortcuts.
const POS_STORAGE_KEY = 'mip-genie-chat-pos-v1';
const CONVERSATION_STORAGE_KEY = 'mip.genie.conversationId';
const SNAP_PX = 24;

interface GeniePos {
  x: number;
  y: number;
}

type GeniePersist = { pos: GeniePos | null };

function loadGeniePos(): GeniePos | null {
  try {
    const raw = localStorage.getItem(POS_STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<GeniePersist>;
    if (!parsed.pos) return null;
    const x = Number(parsed.pos.x);
    const y = Number(parsed.pos.y);
    if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
    return { x, y };
  } catch {
    return null;
  }
}

function saveGeniePos(pos: GeniePos | null): void {
  try {
    if (pos === null) {
      localStorage.removeItem(POS_STORAGE_KEY);
    } else {
      localStorage.setItem(POS_STORAGE_KEY, JSON.stringify({ pos }));
    }
  } catch {
    // private mode / quota — degrade silently like saveGenieSize.
  }
}

/** Clamp (x,y) so the panel stays at least 32 px on screen on each
 *  axis (a stranded panel that's mostly off-screen is the worst
 *  bug in this UX — the user can't grab it back). The h param is
 *  kept in the signature for symmetry / future extension (we may
 *  want to clamp differently per axis later) but the y-axis floor
 *  is currently 0 — the title bar (drag handle) has to stay
 *  visible at the top, so we don't allow it to slide above the
 *  viewport top edge regardless of panel height. */
function clampPos(pos: GeniePos, w: number, _h: number): GeniePos {
  if (typeof window === 'undefined') return pos;
  const minVisible = 32;
  const maxX = window.innerWidth - minVisible;
  const maxY = window.innerHeight - minVisible;
  const minX = -(w - minVisible);
  const minY = 0;
  return {
    x: Math.min(maxX, Math.max(minX, pos.x)),
    y: Math.min(maxY, Math.max(minY, pos.y)),
  };
}

// FIX ζ2: per-handle signature for the 8-direction resize.
// `wSign` / `hSign` map a pointer dx/dy into a size delta. `xSign`
// / `ySign` are how the panel position shifts when the user grabs a
// LEFT or TOP edge while undocked (so growing leftward keeps the
// right edge fixed in viewport space). When docked, position deltas
// are ignored — the bottom-right anchor handles "growth toward the
// origin" implicitly.
type ResizeHandle = 'nw' | 'n' | 'ne' | 'e' | 'se' | 's' | 'sw' | 'w';
const RESIZE_MATRIX: Record<
  ResizeHandle,
  { wSign: number; hSign: number; xSign: number; ySign: number; cursor: string }
> = {
  nw: { wSign: -1, hSign: -1, xSign: 1, ySign: 1, cursor: 'nwse-resize' },
  n:  { wSign: 0,  hSign: -1, xSign: 0, ySign: 1, cursor: 'ns-resize' },
  ne: { wSign: 1,  hSign: -1, xSign: 0, ySign: 1, cursor: 'nesw-resize' },
  e:  { wSign: 1,  hSign: 0,  xSign: 0, ySign: 0, cursor: 'ew-resize' },
  se: { wSign: 1,  hSign: 1,  xSign: 0, ySign: 0, cursor: 'nwse-resize' },
  s:  { wSign: 0,  hSign: 1,  xSign: 0, ySign: 0, cursor: 'ns-resize' },
  sw: { wSign: -1, hSign: 1,  xSign: 1, ySign: 0, cursor: 'nesw-resize' },
  w:  { wSign: -1, hSign: 0,  xSign: 1, ySign: 0, cursor: 'ew-resize' },
};

/** Snap a candidate (x,y) to a viewport corner if within SNAP_PX of
 *  one. Bottom-right snap returns null (re-dock); the other corners
 *  return the corner coordinates so the panel stays undocked but
 *  anchored. */
function snapPos(
  pos: GeniePos,
  w: number,
  h: number,
): GeniePos | null {
  if (typeof window === 'undefined') return pos;
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const nearLeft = pos.x < SNAP_PX;
  const nearRight = pos.x + w > vw - SNAP_PX;
  const nearTop = pos.y < SNAP_PX;
  const nearBottom = pos.y + h > vh - SNAP_PX;
  if (nearRight && nearBottom) return null; // re-dock
  if (nearLeft && nearTop) return { x: 16, y: 16 };
  if (nearRight && nearTop) return { x: vw - w - 16, y: 16 };
  if (nearLeft && nearBottom) return { x: 16, y: vh - h - 16 };
  return pos;
}

/**
 * Map a free-form trusted-asset string (UC path or ruleset id) to the
 * best-fitting drawer entry. Returns null when no specific match exists
 * — the caller renders an inert chip so clicking a generic source like
 * "UC.metrics" or "Databricks Genie API" does NOT open the wrong (NBO)
 * drawer. 2026-05-04 user feedback: clicking what looked like a status
 * chip was opening Next-Best-Offer logic, which was misleading.
 */
function drawerForAsset(asset: string) {
  if (/itm|rules/i.test(asset)) return DRAWER_SOURCES.itm;
  if (/lead.?score|lead_scores|fn_lead_score/i.test(asset)) return DRAWER_SOURCES.leadScore;
  if (/permit/i.test(asset)) return DRAWER_SOURCES.permit;
  if (/lead_population|population|borrower_360/i.test(asset)) return DRAWER_SOURCES.population;
  if (/next.?best|nbo/i.test(asset)) return DRAWER_SOURCES.nbo;
  if (/config/i.test(asset)) return DRAWER_SOURCES.config;
  return null;
}

/**
 * Floating Genie chat panel — `.genie` BEM from the prototype. Fixed
 * bottom-right, reachable from every page. The API path enforces governed
 * SQL/source proof before displaying data-bearing answers. A floating
 * `.genie__fab` is shown when the panel is closed (bottom-right sparkle)
 * so one click anywhere in the app reaches Genie.
 *
 * The AI message shape now holds the full GenieAnswer payload so
 * metric_value / table_rows / follow_up_questions all render in the bubble
 * via the shared <GenieAnswer> subcomponent.
 */

type ChatMsg =
  | { who: 'user'; text: string }
  | { who: 'ai'; payload: GenieAnswerShape; sources?: string[] };

const SAMPLE_QUESTIONS = [
  'How many in-the-money borrowers in Travis County?',
  'Top 10 borrowers by score',
  'Show HELOC candidates by state.',
];

export function GenieChat() {
  const { genieOpen, setGenieOpen, lender, refreshWorkspace } = useApp();
  // Seed greeting carries no source chips — the prior placeholder
  // ("UC.metrics") wasn't a real UC path and clicking it routed into
  // the wrong drawer (NBO logic), which was misleading per 2026-05-04
  // user feedback. The greeting is editorial copy only; real source
  // chips appear once the user asks a question.
  const [msgs, setMsgs] = useState<ChatMsg[]>([
    {
      who: 'ai',
      payload: {
        answer: `Ask about ${lender}'s borrower segments, triggers, or evidence. Answers run against Unity Catalog metric views.`,
        trusted_assets: [],
      },
    },
  ]);
  const [input, setInput] = useState('');
  const [typing, setTyping] = useState(false);
  const [conversationId, setConversationId] = useState<string | null>(() => {
    try {
      return localStorage.getItem(CONVERSATION_STORAGE_KEY);
    } catch {
      return null;
    }
  });
  const bodyRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (conversationId) return undefined;
    const controller = new AbortController();
    api.genieStart(controller.signal)
      .then((result) => {
        if (!result.conversation_id) return;
        setConversationId(result.conversation_id);
        try {
          localStorage.setItem(CONVERSATION_STORAGE_KEY, result.conversation_id);
        } catch {
          // ignore
        }
      })
      .catch(() => {
        // Asking a question will start a fresh Databricks Genie conversation.
      });
    return () => controller.abort();
  }, [conversationId]);

  // FIX Δ2: persisted resize state. Reads from localStorage on mount
  // (defaults to 420×640 — the prior fixed dimensions). Drag handle
  // in the top-left corner adjusts both axes so the panel can grow
  // up + left from the bottom-right anchor. Bounded to [MIN, MAX] so
  // the panel can't shrink past unusable or grow past viewport-eating.
  //
  // FIX ζ2 (2026-05-04): widened from one corner handle to 8
  // directional handles (4 corners + 4 edges) so the user can grow
  // / shrink the panel from any side. Each handle carries a small
  // signature `{ wSign, hSign, xSign, ySign }`:
  //   wSign / hSign  — how a pointer-delta maps to width / height
  //                    (-1 means dragging RIGHT shrinks W; +1 grows)
  //   xSign / ySign  — how a pointer-delta maps to position when
  //                    UN-DOCKED (only meaningful for left + top
  //                    edges; bottom-right corner is the implicit
  //                    docked anchor and never moves the panel)
  // The matrix below covers every cursor: nw / n / ne / e / se / s / sw / w.
  const [size, setSize] = useState<GenieSize>(() => loadGenieSize());
  const resizeOriginRef = useRef<{
    startX: number;
    startY: number;
    startW: number;
    startH: number;
    startPosX: number | null;
    startPosY: number | null;
    handle: ResizeHandle;
  } | null>(null);

  // FIX ε2: persisted position. null = docked (bottom-right, default).
  // Non-null = undocked at viewport ({x, y}). On window resize we
  // re-clamp so a panel that was undocked at (1900, 800) on a wide
  // monitor doesn't end up off-screen on a 1280-wide viewport.
  const [pos, setPos] = useState<GeniePos | null>(() => loadGeniePos());
  const dragOriginRef = useRef<{
    pointerX: number;
    pointerY: number;
    startX: number;
    startY: number;
    didMove: boolean;
  } | null>(null);

  useEffect(() => {
    function onResize() {
      setPos((cur) => (cur ? clampPos(cur, size.w, size.h) : cur));
    }
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, [size.w, size.h]);

  const onDragPointerDown = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      // Only start a drag from the header background — not when the
      // pointer originated on the avatar / title text / close button.
      // Cheap check: the target must BE the header div itself.
      if (e.target !== e.currentTarget) return;
      e.preventDefault();
      e.currentTarget.setPointerCapture(e.pointerId);
      // Where the panel currently sits in viewport coords. If docked,
      // compute the equivalent (top-left) position from window dims +
      // current size + 16 px gutter so the drag picks up smoothly.
      const startPos =
        pos ??
        (typeof window !== 'undefined'
          ? {
              x: window.innerWidth - size.w - 16,
              y: window.innerHeight - size.h - 16,
            }
          : { x: 0, y: 0 });
      dragOriginRef.current = {
        pointerX: e.clientX,
        pointerY: e.clientY,
        startX: startPos.x,
        startY: startPos.y,
        didMove: false,
      };
    },
    [pos, size.w, size.h],
  );

  const onDragPointerMove = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      const origin = dragOriginRef.current;
      if (!origin) return;
      const dx = e.clientX - origin.pointerX;
      const dy = e.clientY - origin.pointerY;
      if (Math.abs(dx) < 3 && Math.abs(dy) < 3 && !origin.didMove) return;
      origin.didMove = true;
      setPos(
        clampPos({ x: origin.startX + dx, y: origin.startY + dy }, size.w, size.h),
      );
    },
    [size.w, size.h],
  );

  const onDragPointerUp = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      const origin = dragOriginRef.current;
      if (!origin) return;
      e.currentTarget.releasePointerCapture(e.pointerId);
      dragOriginRef.current = null;
      // No movement = treat as a click on the header (no-op for
      // close/avatar; user can still click those directly).
      if (!origin.didMove) return;
      // On release, run the snap rule. Snap to bottom-right re-docks.
      setPos((cur) => {
        if (!cur) return cur;
        const snapped = snapPos(cur, size.w, size.h);
        saveGeniePos(snapped);
        return snapped;
      });
    },
    [size.w, size.h],
  );

  const onDragDoubleClick = useCallback(() => {
    // Double-click the header to re-dock immediately.
    setPos(null);
    saveGeniePos(null);
  }, []);

  const beginResize = useCallback(
    (handle: ResizeHandle) =>
      (e: React.PointerEvent<HTMLDivElement>) => {
        // Capture the gesture so the pointer keeps streaming events
        // even when it leaves the handle (Chrome / Safari behave the
        // same). preventDefault stops text selection while dragging.
        e.preventDefault();
        e.stopPropagation();
        e.currentTarget.setPointerCapture(e.pointerId);
        resizeOriginRef.current = {
          startX: e.clientX,
          startY: e.clientY,
          startW: size.w,
          startH: size.h,
          startPosX: pos?.x ?? null,
          startPosY: pos?.y ?? null,
          handle,
        };
      },
    [size.w, size.h, pos],
  );

  const moveResize = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      const origin = resizeOriginRef.current;
      if (!origin) return;
      const sig = RESIZE_MATRIX[origin.handle];
      const dx = e.clientX - origin.startX;
      const dy = e.clientY - origin.startY;
      // Width/height delta. wSign tells us "+1 → grow when dragging
      // right" (right edge), "-1 → grow when dragging left" (left
      // edge). Same idea for h on top/bottom edges. 0 axis means
      // this handle doesn't touch that axis (e.g. north handle is
      // height-only).
      const dw = sig.wSign * dx;
      const dh = sig.hSign * dy;
      const w = Math.min(MAX_W, Math.max(MIN_W, origin.startW + dw));
      const h = Math.min(MAX_H, Math.max(MIN_H, origin.startH + dh));
      setSize({ w, h });
      // Position delta. Only meaningful when undocked AND we're
      // grabbing a left/top edge — those edges' "growth" pulls the
      // panel's anchor in the opposite direction so the OPPOSITE
      // edge stays fixed in viewport space. xSign / ySign in the
      // matrix are 1 for left/top, 0 elsewhere.
      if (origin.startPosX !== null && origin.startPosY !== null) {
        // We must clamp the resulting size BEFORE computing the
        // position shift, otherwise hitting MIN_W on the left edge
        // would let the position keep sliding leftward into space.
        const actualDw = w - origin.startW;
        const actualDh = h - origin.startH;
        const newX = origin.startPosX - sig.xSign * actualDw;
        const newY = origin.startPosY - sig.ySign * actualDh;
        setPos(clampPos({ x: newX, y: newY }, w, h));
      }
    },
    [],
  );

  const endResize = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      if (!resizeOriginRef.current) return;
      e.currentTarget.releasePointerCapture(e.pointerId);
      resizeOriginRef.current = null;
      // Persist on release only — pointermove fires hundreds of
      // times per drag and we don't want to hammer localStorage.
      saveGenieSize(size);
      if (pos) saveGeniePos(pos);
    },
    [size, pos],
  );

  // Keyboard accessibility — keep the corner handle interactive
  // for screen-reader / no-mouse users. Arrow keys nudge ±24, Shift
  // ±96. The 8 mouse handles are pure pointer affordances; a
  // keyboard user only needs ONE handle to resize, so we keep the
  // focusable corner button and leave the edge handles aria-hidden.
  const onResizeKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLButtonElement>) => {
      const step = e.shiftKey ? 96 : 24;
      let dw = 0;
      let dh = 0;
      if (e.key === 'ArrowLeft') dw = step;
      else if (e.key === 'ArrowRight') dw = -step;
      else if (e.key === 'ArrowUp') dh = step;
      else if (e.key === 'ArrowDown') dh = -step;
      else return;
      e.preventDefault();
      const next = {
        w: Math.min(MAX_W, Math.max(MIN_W, size.w + dw)),
        h: Math.min(MAX_H, Math.max(MIN_H, size.h + dh)),
      };
      setSize(next);
      saveGenieSize(next);
    },
    [size],
  );
  // R5-12 (2026-04-23): dialog a11y. Mirrors the EvidenceDrawer pattern
  // — initial focus lands on the input, ESC closes, focus restores to
  // the FAB (or whatever opened the panel) on close. Without these
  // screen-reader + keyboard users are stranded.
  const inputRef = useRef<HTMLInputElement | null>(null);
  const panelRef = useRef<HTMLDivElement | null>(null);
  const lastFocusedRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (bodyRef.current) bodyRef.current.scrollTop = bodyRef.current.scrollHeight;
  }, [msgs, typing, genieOpen]);

  // R5-12: ESC closes + initial focus + focus restore + lightweight
  // focus trap via Tab cycling within the panel.
  useEffect(() => {
    if (genieOpen) {
      lastFocusedRef.current = document.activeElement as HTMLElement | null;
      queueMicrotask(() => inputRef.current?.focus());
      const onKey = (e: KeyboardEvent) => {
        if (e.key === 'Escape') {
          e.preventDefault();
          setGenieOpen(false);
          return;
        }
        if (e.key === 'Tab' && panelRef.current) {
          // Trap focus inside the panel. Query focusable descendants
          // fresh on every Tab so late-rendered sample question
          // buttons are part of the cycle.
          const focusables = panelRef.current.querySelectorAll<HTMLElement>(
            'button:not([disabled]), [href], input:not([disabled]), textarea:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])',
          );
          if (focusables.length === 0) return;
          const first = focusables[0];
          const last = focusables[focusables.length - 1];
          const active = document.activeElement as HTMLElement | null;
          if (e.shiftKey && active === first) {
            e.preventDefault();
            last.focus();
          } else if (!e.shiftKey && active === last) {
            e.preventDefault();
            first.focus();
          }
        }
      };
      window.addEventListener('keydown', onKey);
      return () => window.removeEventListener('keydown', onKey);
    }
    // On close, return focus to whatever opened the panel (the FAB or
    // the topbar Genie toggle). Guard against the element being gone.
    if (lastFocusedRef.current && typeof lastFocusedRef.current.focus === 'function') {
      lastFocusedRef.current.focus();
      lastFocusedRef.current = null;
    }
    return undefined;
  }, [genieOpen, setGenieOpen]);

  const ask = async (q: string) => {
    const trimmed = q.trim();
    if (!trimmed) return;
    const nextConversationId = isGenieFollowUpQuestion(trimmed) ? conversationId : null;
    if (!nextConversationId) {
      setConversationId(null);
      try {
        localStorage.removeItem(CONVERSATION_STORAGE_KEY);
      } catch {
        // ignore
      }
    }
    setMsgs((m) => [...m, { who: 'user', text: trimmed }]);
    setInput('');
    setTyping(true);
    try {
      const res = (await api.genie(trimmed, nextConversationId)) as GenieAnswerShape;
      if (res.conversation_id) {
        setConversationId(res.conversation_id);
        try {
          localStorage.setItem(CONVERSATION_STORAGE_KEY, res.conversation_id);
        } catch {
          // ignore
        }
      }
      const trusted = res.trusted_assets ?? [];
      setMsgs((m) => [...m, { who: 'ai', payload: res, sources: trusted.slice(0, 4) }]);
    } finally {
      setTyping(false);
    }
  };

  const runAction = async (action: GenieActionSuggestion, payload: GenieAnswerShape) => {
    setTyping(true);
    try {
      const result = await api.genieAction({
        ...action,
        conversation_id: payload.conversation_id ?? conversationId,
        message_id: payload.message_id ?? null,
        question_hash: payload.question_hash ?? null,
      });
      if (!result.ok) {
        setMsgs((m) => [
          ...m,
          {
            who: 'ai',
            payload: {
              answer: `Action failed: ${result.message}`,
              source: 'degraded',
              trusted_assets: [],
            },
            sources: [],
          },
        ]);
        return;
      }
      if (action.action_type === 'save_borrowers') refreshWorkspace();
      setMsgs((m) => [
        ...m,
        {
          who: 'ai',
          payload: {
            answer: result.audit_event_id
              ? `${result.message} Audit event ${result.audit_event_id}.`
              : result.message,
            source: 'genie',
            trusted_assets: [],
            conversation_id: payload.conversation_id,
          },
          sources: [],
        },
      ]);
      if (result.route) window.location.href = result.route;
    } catch (err) {
      setMsgs((m) => [
        ...m,
        {
          who: 'ai',
          payload: {
            answer: err instanceof Error ? `Action failed: ${err.message}` : 'Action failed.',
            source: 'degraded',
            trusted_assets: [],
          },
          sources: [],
        },
      ]);
    } finally {
      setTyping(false);
    }
  };

  return (
    <>
      <button
        className={`genie__fab ${genieOpen ? 'is-hidden' : ''}`}
        onClick={() => setGenieOpen(true)}
        aria-label="Open Genie"
        type="button"
      >
        <Icon name="sparkle" size={22} />
      </button>
      <div
        ref={panelRef}
        className={`genie ${genieOpen ? 'is-open' : ''} ${pos ? 'is-undocked' : ''}`}
        role="dialog"
        aria-modal="true"
        aria-label="Genie chat"
        aria-hidden={!genieOpen}
        style={{
          // FIX Δ2 (size) + FIX ε2 (position). Inline size always wins
          // over the .genie static defaults. When pos is non-null the
          // panel is undocked: we override the CSS bottom/right
          // anchoring with explicit left/top so it floats wherever the
          // user dragged it. When pos is null we fall back to the CSS
          // bottom-right anchor (no inline left/top set).
          width: size.w,
          height: size.h,
          maxHeight: size.h,
          ...(pos
            ? { left: pos.x, top: pos.y, right: 'auto', bottom: 'auto' }
            : {}),
        }}
      >
        {/* FIX ζ2: 8-direction resize. Each `.genie__resize-edge--*`
            div catches the pointer in its corner / edge band and
            dispatches through `beginResize(handle)`. The keyboard-
            accessible button (top-left corner) is kept for screen-
            reader / no-mouse users — they only need one focusable
            handle to resize via arrow keys. The 7 other divs are
            aria-hidden because they're pure pointer affordances. */}
        {genieOpen && (
          <>
            <button
              type="button"
              className="genie__resize genie__resize-edge--nw-button"
              aria-label={`Resize Genie panel (currently ${size.w} by ${size.h} pixels). Drag any edge or corner, or use arrow keys.`}
              onPointerDown={(e) => beginResize('nw')(e as unknown as React.PointerEvent<HTMLDivElement>)}
              onPointerMove={(e) => moveResize(e as unknown as React.PointerEvent<HTMLDivElement>)}
              onPointerUp={(e) => endResize(e as unknown as React.PointerEvent<HTMLDivElement>)}
              onPointerCancel={(e) => endResize(e as unknown as React.PointerEvent<HTMLDivElement>)}
              onKeyDown={onResizeKeyDown}
            >
              <span aria-hidden="true">⇲</span>
            </button>
            {(['n', 'ne', 'e', 'se', 's', 'sw', 'w'] as const).map((h) => (
              <div
                key={h}
                className={`genie__resize-edge genie__resize-edge--${h}`}
                aria-hidden="true"
                onPointerDown={beginResize(h)}
                onPointerMove={moveResize}
                onPointerUp={endResize}
                onPointerCancel={endResize}
              />
            ))}
          </>
        )}
        {/* FIX ε2: header is the drag handle. Pointer events on the
            header background start the drag; double-click re-docks.
            Children (avatar, title, close button) intercept clicks
            normally because the move guard checks e.target === header. */}
        <div
          className={`genie__hdr ${pos ? 'genie__hdr--dragging' : ''}`}
          onPointerDown={onDragPointerDown}
          onPointerMove={onDragPointerMove}
          onPointerUp={onDragPointerUp}
          onPointerCancel={onDragPointerUp}
          onDoubleClick={onDragDoubleClick}
          title={
            pos
              ? 'Drag to move · double-click to re-dock'
              : 'Drag to undock · double-click to reset'
          }
        >
          <div className="genie__avatar" />
          <div className="genie-chat__drag-title">
            <div className="genie__title">Ask Genie</div>
            <div className="genie__sub">
              Unity Catalog metric views · {lender}
              {pos ? ' · undocked' : ''}
            </div>
          </div>
          {/* Re-dock button — only visible when the panel is undocked.
              Gives a discoverable affordance for users who haven't
              learned the double-click shortcut. */}
          {pos && (
            <button
              type="button"
              className="drawer__close"
              onClick={(e) => {
                e.stopPropagation();
                onDragDoubleClick();
              }}
              aria-label="Re-dock Genie panel to bottom-right"
              title="Re-dock"
            >
              <Icon name="db" size={14} />
            </button>
          )}
          <button
            className="drawer__close"
            onClick={(e) => {
              e.stopPropagation();
              setGenieOpen(false);
            }}
            aria-label="Close Genie"
            type="button"
          >
            <Icon name="close" size={14} />
          </button>
        </div>
        <div className="genie__body" ref={bodyRef}>
          {msgs.map((m, i) =>
            m.who === 'user' ? (
              <div key={i} className="genie__msg genie__msg--user">{m.text}</div>
            ) : (
              <div key={i} className="genie__msg genie__msg--ai">
                <div className="bubble">
                  <GenieAnswer
                    payload={m.payload}
                    onFollowUp={ask}
                    onAction={(action) => runAction(action, m.payload)}
                    dense
                  />
                </div>
                {/* Source chip row. The backend emits "genie" (live)
                    or "degraded" (warming-up). Degraded gets a warning
                    chip + tooltip; live answers render their trusted-
                    asset lineage. 2026-05-04: removed the
                    "computed_fallback" path entirely per user feedback
                    ("we don't want this app to be gimmicky at all"). */}
                {m.payload.source === 'degraded' && (
                  <div className="sources">
                    <Chip
                      variant="warning"
                      icon="info"
                      title="The Genie space is warming up. Live answers will resume shortly."
                    >
                      Genie reconnecting
                    </Chip>
                  </div>
                )}
                {m.payload.source !== 'degraded' &&
                  m.sources && m.sources.length > 0 && (
                  <div className="sources">
                    {m.sources.map((s, j) => {
                      const drawer = drawerForAsset(s);
                      if (drawer === null) {
                        // Source string doesn't map to a specific drawer
                        // entry — render an inert neutral chip so the
                        // user can read the source label without being
                        // misled into the wrong drawer (the prior
                        // "default to NBO" routing was confusing per
                        // 2026-05-04 user feedback).
                        return (
                          <Chip key={j} variant="neutral" title={`Source: ${s}`}>
                            {s}
                          </Chip>
                        );
                      }
                      return (
                        <EvidenceChip key={j} source={drawer} title={`Source: ${s}`}>
                          {s}
                        </EvidenceChip>
                      );
                    })}
                  </div>
                )}
              </div>
            )
          )}
          {typing && (
            <div className="genie__msg genie__msg--ai">
              <div className="bubble"><GenieProgress dense /></div>
            </div>
          )}
          {msgs.length <= 1 && (
            <div className="genie-chat__samples">
              {SAMPLE_QUESTIONS.map((s) => (
                <button key={s} className="filter genie-chat__sample" onClick={() => ask(s)} type="button">
                  <Icon name="sparkle" size={11} /> {s}
                </button>
              ))}
            </div>
          )}
        </div>
        <form
          className="genie__input"
          onSubmit={(e) => {
            e.preventDefault();
            ask(input);
          }}
        >
          <input
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Ask about borrowers, segments, triggers…"
            aria-label="Ask Genie"
          />
          <Button variant="primary" size="sm" type="submit" icon="send" aria-label="Ask">Ask</Button>
        </form>
      </div>
    </>
  );
}
