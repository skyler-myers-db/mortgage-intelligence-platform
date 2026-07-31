import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react';
import { useNavigate } from 'react-router';
import { useApp } from '../AppContext';
import { ApiError, api, isAbortError, type GenieLiveProgress } from '../../lib/api';
import { GenieLiveError, askGenieLive } from '../../lib/genieAsk';
import type { GenieActionSuggestion, GenieAnswer as GenieAnswerShape } from '../../types';
import { Icon } from '../Icon';
import { Button, Chip, EvidenceChip } from '../Primitives';
import { GenieAnswer, GOVERNED_ACTION_SOURCE } from './GenieAnswer';
import { GenieProgress } from './GenieProgress';
import { drawerForAsset } from '../../lib/drawerSources';
import {
  GENIE_CONVERSATION_RESET_EVENT,
  clearGenieConversationState,
  readGenieConversationId,
  writeGenieConversationId,
} from '../../lib/genieConversation';
import { NON_PERSISTABLE_SOURCES } from '../../lib/pinnedInsights';
import { GENIE_POINTER_RESIZE_HANDLES, useGenieWindow } from './useGenieWindow';

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

export function sourceAssetsFor(payload: GenieAnswerShape): string[] {
  const seen = new Set<string>();
  const assets = [
    ...(payload.proof?.source_assets ?? []),
    ...(payload.trusted_assets ?? []),
  ];
  for (const raw of assets) {
    const asset = typeof raw === 'string' ? raw.trim() : '';
    if (asset) seen.add(asset);
  }
  return Array.from(seen).slice(0, 4);
}

export function shouldPersistConversation(payload: GenieAnswerShape): boolean {
  return Boolean(payload.conversation_id && !NON_PERSISTABLE_SOURCES.has(String(payload.source ?? '')));
}

export function warningLabelForSource(source: string | undefined): string | null {
  if (source === 'degraded') return 'Genie reconnecting';
  if (source === 'policy_blocked' || source === 'refused') return 'Governed refusal';
  if (source === 'data_gap') return 'Pending source feed';
  if (source === 'out_of_footprint') return 'Outside footprint';
  return null;
}

export function shouldRenderGenieSourceAssets(payload: GenieAnswerShape): boolean {
  return warningLabelForSource(payload.source) === null && sourceAssetsFor(payload).length > 0;
}

export function GenieChat() {
  const { genieOpen, setGenieOpen, lender, refreshWorkspace } = useApp();
  const navigate = useNavigate();
  const [msgs, setMsgs] = useState<ChatMsg[]>([]);
  const [input, setInput] = useState('');
  const [typing, setTyping] = useState(false);
  // Live lifecycle telemetry for the in-flight turn (stage, public process
  // steps, generated SQL) driven by the submit → progress → complete flow.
  const [liveProgress, setLiveProgress] = useState<GenieLiveProgress | null>(null);
  const [askStartedAt, setAskStartedAt] = useState<number | null>(null);
  // Abort + generation control for the in-flight live turn (QA M3): a
  // conversation reset or New thread must stop the poll loop, and a turn
  // that resolves AFTER a reset must not re-persist the previous actor's
  // conversation id or append an orphan bubble to the cleared thread.
  const askAbortRef = useRef<AbortController | null>(null);
  const askGenerationRef = useRef(0);

  useEffect(
    () => () => {
      askAbortRef.current?.abort();
    },
    [],
  );
  const [sampleQuestions, setSampleQuestions] = useState<string[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(() => readGenieConversationId());
  const bodyRef = useRef<HTMLDivElement>(null);
  const suppressBootstrapConversationRef = useRef(false);

  useEffect(() => {
    const controller = new AbortController();
    api.genieStart(controller.signal)
      .then((result) => {
        setSampleQuestions(Array.isArray(result.sample_questions) ? result.sample_questions : []);
        const startConversationId = result.conversation_id;
        if (!startConversationId || suppressBootstrapConversationRef.current) return;
        setConversationId((current) => {
          if (current) return current;
          writeGenieConversationId(startConversationId);
          return startConversationId;
        });
      })
      .catch(() => {
        // Asking a question will start a fresh Databricks Genie conversation.
      });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    const onActorBoundaryReset = () => {
      suppressBootstrapConversationRef.current = true;
      // Invalidate + stop any in-flight turn so a late resolution cannot
      // re-persist the previous actor's conversation id (QA M3).
      askGenerationRef.current += 1;
      askAbortRef.current?.abort();
      setConversationId(null);
      setMsgs([]);
      setInput('');
      setTyping(false);
      setLiveProgress(null);
      setAskStartedAt(null);
    };
    window.addEventListener(GENIE_CONVERSATION_RESET_EVENT, onActorBoundaryReset);
    return () => {
      window.removeEventListener(GENIE_CONVERSATION_RESET_EVENT, onActorBoundaryReset);
    };
  }, []);

  const {
    effectiveSize,
    position: pos,
    beginResize,
    moveResize,
    endResize,
    onResizeKeyDown,
    onDragPointerDown,
    onDragPointerMove,
    onDragPointerUp,
    redock: onDragDoubleClick,
  } = useGenieWindow({ open: genieOpen });

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

  // R5-12: ESC closes + initial focus + focus restore. Deliberately do
  // NOT trap Tab: the floating Genie panel is a non-modal dialog, and the
  // rest of the workspace stays interactive while it is open.
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

  const ask = async (q: string, followUpConversationId?: string | null) => {
    const trimmed = q.trim();
    if (!trimmed) return;
    const activeConversationId = followUpConversationId ?? conversationId;
    if (!activeConversationId) {
      setConversationId(null);
      clearGenieConversationState();
    }
    setMsgs((m) => [...m, { who: 'user', text: trimmed }]);
    setInput('');
    setTyping(true);
    setLiveProgress(null);
    setAskStartedAt(Date.now());
    const generation = ++askGenerationRef.current;
    askAbortRef.current?.abort();
    const controller = new AbortController();
    askAbortRef.current = controller;
    const isCurrent = () => askGenerationRef.current === generation;
    try {
      const res = (await askGenieLive(trimmed, activeConversationId, {
        signal: controller.signal,
        onProgress: (p) => {
          if (isCurrent()) setLiveProgress(p);
        },
      })) as GenieAnswerShape;
      // A reset/new-thread while in flight invalidates this turn: never
      // re-persist its conversation id or append to the cleared thread.
      if (!isCurrent()) return;
      const returnedConversationId = res.conversation_id ?? null;
      if (returnedConversationId && shouldPersistConversation(res)) {
        setConversationId(returnedConversationId);
        writeGenieConversationId(returnedConversationId);
      }
      setMsgs((m) => [...m, { who: 'ai', payload: res, sources: sourceAssetsFor(res) }]);
    } catch (err) {
      if (!isCurrent() || isAbortError(err)) return;
      if (err instanceof ApiError && err.status === 403) {
        setConversationId(null);
        clearGenieConversationState({ notify: true });
      }
      const answer =
        err instanceof GenieLiveError
          ? err.message
          : err instanceof Error
            ? `Genie session reset: ${err.message}`
            : 'Genie session reset.';
      setMsgs((m) => [
        ...m,
        {
          who: 'ai',
          payload: {
            answer,
            source: 'degraded',
            trusted_assets: [],
          },
          sources: [],
        },
      ]);
    } finally {
      if (isCurrent()) {
        setTyping(false);
        setLiveProgress(null);
        setAskStartedAt(null);
      }
    }
  };

  const newConversation = () => {
    if (typing) return;
    suppressBootstrapConversationRef.current = true;
    setConversationId(null);
    setMsgs([]);
    setInput('');
    clearGenieConversationState({ notify: true });
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
            source: GOVERNED_ACTION_SOURCE,
            trusted_assets: [],
            conversation_id: payload.conversation_id,
          },
          sources: [],
        },
      ]);
      if (result.route) navigate(result.route);
    } catch (err) {
      if (err instanceof ApiError && err.status === 403) {
        setConversationId(null);
        clearGenieConversationState({ notify: true });
      }
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
        // 2026-06-11 audit P3 a11y: NO aria-modal here. The floating panel
        // is a NON-modal dialog — no focus trap, no scrim, the page behind
        // stays fully interactive. aria-modal="true" told screen readers
        // the rest of the app was inert, which was a lie.
        aria-label="Genie chat"
        aria-keyshortcuts="Escape"
        aria-hidden={!genieOpen}
        style={{
          // FIX Δ2 (size) + FIX ε2 (position). Inline size always wins
          // over the .genie static defaults. When pos is non-null the
          // panel is undocked: we override the CSS bottom/right
          // anchoring with explicit left/top so it floats wherever the
          // user dragged it. When pos is null we fall back to the CSS
          // bottom-right anchor (no inline left/top set).
          width: effectiveSize.w,
          height: effectiveSize.h,
          maxHeight: effectiveSize.h,
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
              aria-label={`Resize Genie panel (currently ${effectiveSize.w} by ${effectiveSize.h} pixels). Drag any edge or corner, or use arrow keys.`}
              onPointerDown={(e) => beginResize('nw')(e as unknown as ReactPointerEvent<HTMLDivElement>)}
              onPointerMove={(e) => moveResize(e as unknown as ReactPointerEvent<HTMLDivElement>)}
              onPointerUp={(e) => endResize(e as unknown as ReactPointerEvent<HTMLDivElement>)}
              onPointerCancel={(e) => endResize(e as unknown as ReactPointerEvent<HTMLDivElement>)}
              onKeyDown={onResizeKeyDown}
            >
              <span aria-hidden="true">⇲</span>
            </button>
            {GENIE_POINTER_RESIZE_HANDLES.map((h) => (
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
              Trusted Unity Catalog assets · {lender}
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
            type="button"
            className="drawer__close"
            onClick={(e) => {
              e.stopPropagation();
              newConversation();
            }}
            disabled={typing}
            aria-label="Start a new Genie thread"
            title="New thread"
          >
            <Icon name="chat" size={14} />
          </button>
          <button
            className="drawer__close"
            onClick={(e) => {
              e.stopPropagation();
              setGenieOpen(false);
            }}
            aria-label="Close Genie"
            title="Close (Esc)"
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
              <div
                key={i}
                className="genie__msg genie__msg--ai"
              >
                <div className="bubble">
                  <GenieAnswer
                    payload={m.payload}
                    question={(() => {
                      const prev = msgs[i - 1];
                      return prev && prev.who === 'user' ? prev.text : undefined;
                    })()}
                    onFollowUp={ask}
                    onAction={(action) => runAction(action, m.payload)}
                    dense
                  />
                </div>
                {/* Source chip row. The backend emits "genie" (live)
                    or governed refusal/degraded source values. Warning
                    chips never pretend to be data-bearing answers. */}
                {warningLabelForSource(m.payload.source) && (
                  <div className="sources">
                    <Chip
                      variant="warning"
                      icon="info"
                      title={
                        m.payload.source === 'degraded'
                          ? 'The Genie answer path is temporarily unavailable. Live answers will resume after health recovers.'
                          : 'This answer intentionally stopped before displaying a live result.'
                      }
                    >
                      {warningLabelForSource(m.payload.source)}
                    </Chip>
                  </div>
                )}
                {shouldRenderGenieSourceAssets(m.payload) && (
                  <div className="sources">
                    {(m.sources && m.sources.length > 0 ? m.sources : sourceAssetsFor(m.payload)).map((s, j) => {
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
              <div className="bubble">
                <GenieProgress dense progress={liveProgress} startedAt={askStartedAt} />
              </div>
            </div>
          )}
          {msgs.length === 0 && !typing && (
            <div className="genie-chat__samples">
              <div className="surface surface--inset">
                <div className="surface__body genie-empty">
                  <div className="genie-empty__icon">
                    <Icon name="sparkle" size={16} />
                  </div>
                  <div>
                    <div className="genie-empty__title">Ask about your book — coverage, segments, borrowers, market shifts.</div>
                    <p className="genie-empty__copy">
                      Data-bearing answers appear only after Genie returns trusted SQL, source assets, and proof.
                    </p>
                  </div>
                </div>
              </div>
              {sampleQuestions.map((s) => (
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
