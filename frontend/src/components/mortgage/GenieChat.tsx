import { useCallback, useEffect, useRef, useState } from 'react';
import { useApp } from '../AppContext';
import { api } from '../../lib/api';
import type { GenieAnswer as GenieAnswerShape } from '../../types';
import { Icon } from '../Icon';
import { Button, Chip, EvidenceChip } from '../Primitives';
import { GenieAnswer } from './GenieAnswer';
import { DRAWER_SOURCES } from '../../lib/drawerSources';

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
  if (/permit/i.test(asset)) return DRAWER_SOURCES.permit;
  if (/lead_population|population|borrower_360/i.test(asset)) return DRAWER_SOURCES.population;
  if (/next.?best|nbo/i.test(asset)) return DRAWER_SOURCES.nbo;
  if (/config/i.test(asset)) return DRAWER_SOURCES.config;
  return null;
}

/**
 * Floating Genie chat panel — `.genie` BEM from the prototype. Fixed
 * bottom-right, reachable from every page. Falls back to api.genie() which
 * already has a deterministic fallback wired. A floating `.genie__fab` is
 * shown when the panel is closed (bottom-right sparkle) so one click anywhere
 * in the app reaches Genie.
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
  const { genieOpen, setGenieOpen, lender } = useApp();
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
  const bodyRef = useRef<HTMLDivElement>(null);

  // FIX Δ2: persisted resize state. Reads from localStorage on mount
  // (defaults to 420×640 — the prior fixed dimensions). Drag handle
  // in the top-left corner adjusts both axes so the panel can grow
  // up + left from the bottom-right anchor. Bounded to [MIN, MAX] so
  // the panel can't shrink past unusable or grow past viewport-eating.
  const [size, setSize] = useState<GenieSize>(() => loadGenieSize());
  const resizeOriginRef = useRef<{
    startX: number;
    startY: number;
    startW: number;
    startH: number;
  } | null>(null);

  const onResizePointerDown = useCallback(
    (e: React.PointerEvent<HTMLButtonElement>) => {
      // Capture the gesture so the pointer keeps streaming events
      // even when it leaves the handle (Chrome / Safari behave the
      // same). preventDefault stops text selection while dragging.
      e.preventDefault();
      e.currentTarget.setPointerCapture(e.pointerId);
      resizeOriginRef.current = {
        startX: e.clientX,
        startY: e.clientY,
        startW: size.w,
        startH: size.h,
      };
    },
    [size.w, size.h],
  );

  const onResizePointerMove = useCallback(
    (e: React.PointerEvent<HTMLButtonElement>) => {
      const origin = resizeOriginRef.current;
      if (!origin) return;
      // The panel is anchored bottom-right and the handle is top-left,
      // so dragging UP/LEFT grows the panel — invert the delta sign.
      const dx = origin.startX - e.clientX;
      const dy = origin.startY - e.clientY;
      const w = Math.min(MAX_W, Math.max(MIN_W, origin.startW + dx));
      const h = Math.min(MAX_H, Math.max(MIN_H, origin.startH + dy));
      setSize({ w, h });
    },
    [],
  );

  const onResizePointerUp = useCallback(
    (e: React.PointerEvent<HTMLButtonElement>) => {
      if (!resizeOriginRef.current) return;
      e.currentTarget.releasePointerCapture(e.pointerId);
      resizeOriginRef.current = null;
      // Persist final size — only on pointerup so we don't pound
      // localStorage during the drag.
      saveGenieSize(size);
    },
    [size],
  );

  // Keyboard accessibility for the resize handle: Arrow keys nudge
  // by 24 px; Shift+Arrow nudges by 96 px. Persisted on each press.
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
    if (!q.trim()) return;
    setMsgs((m) => [...m, { who: 'user', text: q }]);
    setInput('');
    setTyping(true);
    try {
      const res = (await api.genie(q)) as GenieAnswerShape;
      const trusted = res.trusted_assets ?? [];
      setMsgs((m) => [...m, { who: 'ai', payload: res, sources: trusted.slice(0, 4) }]);
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
        className={`genie ${genieOpen ? 'is-open' : ''}`}
        role="dialog"
        aria-modal="true"
        aria-label="Genie chat"
        aria-hidden={!genieOpen}
        style={{
          // FIX Δ2: inline size overrides win over the .genie static
          // 420×640 from components.css. max-height is set explicitly
          // here too because the CSS rule uses max-height (not
          // height) and the panel needs to actually grow vertically.
          width: size.w,
          height: size.h,
          maxHeight: size.h,
        }}
      >
        {/* FIX Δ2: resize handle. Anchored top-left because the
            panel itself is anchored bottom-right (dragging top-left
            "outward" grows the panel). It's a real <button> so screen
            readers can announce it; Arrow keys + Shift+Arrow give
            keyboard users a way to resize without a mouse. The CSS
            handle styling lives in components.css under .genie__resize. */}
        {genieOpen && (
          <button
            type="button"
            className="genie__resize"
            aria-label={`Resize Genie panel (currently ${size.w} by ${size.h} pixels). Drag, or use arrow keys.`}
            onPointerDown={onResizePointerDown}
            onPointerMove={onResizePointerMove}
            onPointerUp={onResizePointerUp}
            onPointerCancel={onResizePointerUp}
            onKeyDown={onResizeKeyDown}
          >
            <span aria-hidden="true">⇲</span>
          </button>
        )}
        <div className="genie__hdr">
          <div className="genie__avatar" />
          <div style={{ flex: 1 }}>
            <div className="genie__title">Ask Genie</div>
            <div className="genie__sub">Unity Catalog metric views · {lender}</div>
          </div>
          <button
            className="drawer__close"
            onClick={() => setGenieOpen(false)}
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
                  <GenieAnswer payload={m.payload} onFollowUp={ask} dense />
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
              <div className="bubble"><div className="typing-dots"><span/><span/><span/></div></div>
            </div>
          )}
          {msgs.length <= 1 && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginTop: 4 }}>
              {SAMPLE_QUESTIONS.map((s) => (
                <button key={s} className="filter" style={{ alignSelf: 'flex-start', textAlign: 'left' }} onClick={() => ask(s)} type="button">
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
