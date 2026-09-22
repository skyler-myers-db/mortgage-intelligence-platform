import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
} from 'react';
import { useNavigate } from 'react-router';
import { useApp } from '../AppContext';
import { ApiError, api, isAbortError, type GenieLiveProgress } from '../../lib/api';
import { GenieLiveError, askGenieLive } from '../../lib/genieAsk';
import type { GenieActionSuggestion, GenieAnswer as GenieAnswerShape } from '../../types';
import { Icon } from '../Icon';
import { Button, Chip, EvidenceChip } from '../Primitives';
import { GenieAnswer, GOVERNED_ACTION_SOURCE } from './GenieAnswer';
import { GenieProgress, genieProgressLabel } from './GenieProgress';
import { drawerForAsset } from '../../lib/drawerSources';
import {
  GENIE_CONVERSATION_RESET_EVENT,
  clearGenieConversationState,
  readGenieConversationId,
  writeGenieConversationId,
} from '../../lib/genieConversation';
import { NON_PERSISTABLE_SOURCES } from '../../lib/pinnedInsights';
import {
  appendGenieTurn,
  clearGenieTurns,
  setGenieTurns,
  type GenieTurn,
} from '../../lib/genieConversationStore';
import {
  GENIE_LAUNCHER_STATUS_ID,
  genieLauncherStateClass,
  genieLauncherStatusText,
  setGenieTurnStatus,
  type GenieTurnStatus,
} from '../../lib/genieTurnStatus';
import { GENIE_POINTER_RESIZE_HANDLES, useGenieWindow } from './useGenieWindow';
import { useGeniePanelDismissal } from './useGeniePanelDismissal';
import { useGenieTranscript } from './useGenieTranscript';
import { useGenieTranscriptScroll } from './useGenieTranscriptScroll';
import { GenieHistoryMenu } from './GenieHistoryMenu';

/**
 * Floating Genie chat panel — `.genie` BEM from the prototype. Fixed
 * bottom-right, reachable from every page. The API path enforces governed
 * SQL/source proof before displaying data-bearing answers. A floating
 * `.genie__fab` is shown when the panel is closed (bottom-right sparkle)
 * so one click anywhere in the app reaches Genie.
 *
 * Survivability (audit 2026-09-21 `runtime-01` / `genie-02`): the shell
 * mounts this component on first open and never unmounts it. Closing only
 * hides the panel (`.genie:not(.is-open)`), so a 30-200 second turn keeps
 * running behind it; the launchers show a running ring, then an answer-ready
 * badge until the panel is opened again. The actor-boundary reset still
 * aborts the turn and clears everything.
 *
 * The AI message shape now holds the full GenieAnswer payload so
 * metric_value / table_rows / follow_up_questions all render in the bubble
 * via the shared <GenieAnswer> subcomponent.
 */

const COMPOSER_BUSY_HINT_ID = 'genie-composer-busy';
const ASKING_REASON =
  'Genie is still answering. Keep drafting: Ask unlocks when this answer lands. Closing the panel does not stop it.';
const ACTION_REASON = 'A governed action is running. Ask unlocks when it finishes.';

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
  // Settled transcript, mirrored from the shared tab-scoped store (the
  // `/ask-genie` route appends to the same list). The question of the turn in
  // flight is NOT in it: it lives in `pendingQuestion` until the turn settles.
  const msgs = useGenieTranscript(sourceAssetsFor);
  const [pendingQuestion, setPendingQuestion] = useState<string | null>(null);
  const [input, setInput] = useState('');
  // Two independent busy sources. They used to share one `typing` flag, so a
  // governed action finishing mid-ask cleared the ask's progress card too.
  const [asking, setAsking] = useState(false);
  const [actionRunning, setActionRunning] = useState(false);
  const typing = asking || actionRunning;
  const [historyOpen, setHistoryOpen] = useState(false);
  // An answer that landed while the panel was closed and has not been opened.
  const [unseenAnswer, setUnseenAnswer] = useState(false);
  // What the persistent screen-reader announcer says once a turn settles.
  const [announcement, setAnnouncement] = useState('');
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
  // Synchronous in-flight latch (audit `genie-v2`): two submits in one tick
  // both read `asking === false` from their render's closure. Also read by
  // the open effect below, which must not re-run when a turn starts/settles.
  const askInFlightRef = useRef(false);
  // The reset listener below is bound once, before the scroll hook exists.
  const cancelAnchorRef = useRef<() => void>(() => undefined);
  const genieOpenRef = useRef(genieOpen);
  useEffect(() => {
    genieOpenRef.current = genieOpen;
  }, [genieOpen]);

  // Closing the panel no longer unmounts this component, so this cleanup runs
  // only when the whole shell goes away. It stays, and it also invalidates the
  // generation: an unmounted panel cannot hear the actor-boundary reset event,
  // so a turn resolving after teardown must never re-persist a conversation
  // id (fail-closed identity boundary).
  useEffect(
    () => () => {
      askGenerationRef.current += 1;
      askAbortRef.current?.abort();
      setGenieTurnStatus('idle');
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
      askAbortRef.current = null;
      askInFlightRef.current = false;
      setConversationId(null);
      // An actor-boundary reset / 403 invalidates the transcript too: the
      // conversation is not resumable and the prior actor's questions must
      // not linger in this tab — including the in-flight question, the
      // unseen-answer badge and the last spoken announcement.
      clearGenieTurns();
      setPendingQuestion(null);
      setInput('');
      setAsking(false);
      setActionRunning(false);
      setHistoryOpen(false);
      setLiveProgress(null);
      setAskStartedAt(null);
      setUnseenAnswer(false);
      setAnnouncement('');
      cancelAnchorRef.current();
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
  // screen-reader + keyboard users are stranded. Deliberately does NOT trap
  // Tab: the floating panel is a non-modal dialog, and the rest of the
  // workspace stays interactive while it is open.
  const inputRef = useRef<HTMLInputElement | null>(null);
  const panelRef = useRef<HTMLDivElement | null>(null);
  const fabRef = useRef<HTMLButtonElement | null>(null);
  const lastAnswerRef = useRef<HTMLDivElement | null>(null);
  const closePanel = useCallback(() => setGenieOpen(false), [setGenieOpen]);
  useGeniePanelDismissal({ open: genieOpen, panelRef, inputRef, fabRef, onClose: closePanel });

  // A settled answer is scrolled to its START; everything else sticks to the
  // bottom (audit `motion-v2`).
  const { anchorNextAnswer, cancelAnchor } = useGenieTranscriptScroll({
    open: genieOpen,
    bodyRef,
    lastAnswerRef,
    messages: msgs,
    pendingQuestion,
    busy: typing,
  });
  useEffect(() => {
    cancelAnchorRef.current = cancelAnchor;
  }, [cancelAnchor]);

  // Opening the panel: the badge has done its job, and the conversation id is
  // re-read because `/ask-genie` may have advanced the shared thread while the
  // panel sat closed (it used to re-read this on every remount). Never while a
  // turn is in flight: that turn owns the id it was submitted with.
  useEffect(() => {
    if (!genieOpen) return;
    setUnseenAnswer(false);
    if (!askInFlightRef.current) setConversationId(readGenieConversationId());
  }, [genieOpen]);

  // Launcher status for the topbar toggle and the FAB. Only meaningful while
  // the panel is closed; an open panel shows its own progress.
  const launcherStatus: GenieTurnStatus = genieOpen
    ? 'idle'
    : typing
      ? 'running'
      : unseenAnswer
        ? 'ready'
        : 'idle';
  useEffect(() => {
    setGenieTurnStatus(launcherStatus);
  }, [launcherStatus]);

  /** One settled bubble: append to the shared transcript, anchor the scroll
   *  to its start, tell screen readers, and badge the launcher if the panel
   *  is closed. */
  const landBubble = (question: string, payload: GenieAnswerShape, spoken: string) => {
    appendGenieTurn(question, payload);
    anchorNextAnswer();
    setAnnouncement(spoken);
    if (!genieOpenRef.current) setUnseenAnswer(true);
  };

  /**
   * Start a turn. `startedAt` is the moment of the user's action and is read
   * (`Date.now()`) at the event site, never here: the React Compiler cannot
   * prove a component-scope function runs only from event handlers, so an
   * impure call inside it is rejected as a render-time call.
   */
  const ask = async (
    q: string,
    followUpConversationId: string | null | undefined,
    startedAt: number,
  ) => {
    const trimmed = q.trim();
    if (!trimmed) return;
    // A second ask NEVER replaces a running turn (audit `genie-v2`). It used
    // to abort the in-flight controller and swallow the abort, which silently
    // destroyed a 200-second deep turn and left its question bubble unanswered.
    // The controls are disabled while busy; this guard is what holds when a
    // keypress or a stale chip gets through anyway. The draft is left intact.
    if (askInFlightRef.current || actionRunning) return;
    askInFlightRef.current = true;
    const activeConversationId = followUpConversationId ?? conversationId;
    if (!activeConversationId) {
      setConversationId(null);
      clearGenieConversationState();
    }
    setPendingQuestion(trimmed);
    setInput('');
    setAsking(true);
    setLiveProgress(null);
    setAskStartedAt(startedAt);
    const generation = ++askGenerationRef.current;
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
      // The only place "Answer ready" is ever said: the governed answer is in
      // hand and renderable (audit `genie-01` phase 0, `a11y-06`).
      landBubble(trimmed, res, 'Answer ready');
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
      landBubble(
        trimmed,
        { answer, source: 'degraded', trusted_assets: [] },
        'Genie could not complete this question.',
      );
    } finally {
      // A superseded turn (reset / teardown) already had its state cleared by
      // whoever invalidated it, in-flight latch included.
      if (isCurrent()) {
        askInFlightRef.current = false;
        askAbortRef.current = null;
        setAsking(false);
        setPendingQuestion(null);
        setLiveProgress(null);
        setAskStartedAt(null);
      }
    }
  };

  const newConversation = () => {
    if (typing) return;
    suppressBootstrapConversationRef.current = true;
    setConversationId(null);
    clearGenieTurns();
    setInput('');
    setHistoryOpen(false);
    clearGenieConversationState({ notify: true });
  };

  /**
   * Restore a past conversation from the History menu. The loaded turns are
   * the same `{question, response}` shape the local store persists, so they
   * render through the identical <GenieAnswer> path. The conversation id is
   * adopted too, so a follow-up continues that Databricks thread rather than
   * opening an orphan one.
   */
  const loadSession = (conversationIdToLoad: string, turns: GenieTurn[]) => {
    if (typing) return;
    askGenerationRef.current += 1;
    askAbortRef.current?.abort();
    suppressBootstrapConversationRef.current = true;
    setGenieTurns(turns);
    cancelAnchor();
    setConversationId(conversationIdToLoad);
    writeGenieConversationId(conversationIdToLoad);
    setHistoryOpen(false);
    setInput('');
  };

  const runAction = async (action: GenieActionSuggestion, payload: GenieAnswerShape) => {
    setActionRunning(true);
    try {
      const result = await api.genieAction({
        ...action,
        conversation_id: payload.conversation_id ?? conversationId,
        message_id: payload.message_id ?? null,
        question_hash: payload.question_hash ?? null,
      });
      if (!result.ok) {
        const failed = `Action failed: ${result.message}`;
        landBubble('', { answer: failed, source: 'degraded', trusted_assets: [] }, failed);
        return;
      }
      if (action.action_type === 'save_borrowers') refreshWorkspace();
      const confirmed = result.audit_event_id
        ? `${result.message} Audit event ${result.audit_event_id}.`
        : result.message;
      landBubble(
        '',
        {
          answer: confirmed,
          source: GOVERNED_ACTION_SOURCE,
          trusted_assets: [],
          conversation_id: payload.conversation_id,
        },
        confirmed,
      );
      if (result.route) navigate(result.route);
    } catch (err) {
      if (err instanceof ApiError && err.status === 403) {
        setConversationId(null);
        clearGenieConversationState({ notify: true });
      }
      const failed = err instanceof Error ? `Action failed: ${err.message}` : 'Action failed.';
      landBubble('', { answer: failed, source: 'degraded', trusted_assets: [] }, failed);
    } finally {
      setActionRunning(false);
    }
  };

  const busyReason = asking ? ASKING_REASON : actionRunning ? ACTION_REASON : null;
  const lastAnswerIndex = msgs.reduce((last, m, i) => (m.who === 'ai' ? i : last), -1);
  // While a turn runs the announcer carries stage CHANGES only (the same
  // label the progress card shows); once it settles, the landing message.
  const announcerText = asking ? genieProgressLabel(liveProgress) : announcement;

  return (
    <>
      {/* Both live OUTSIDE `.genie`: the panel is aria-hidden while closed,
          and these must keep working then. The first is the launchers'
          `aria-describedby` target; the second is the panel's ONE persistent
          polite announcer (audit `a11y-06`): stage changes, then "Answer
          ready" once. The elapsed ticker is nowhere near it. */}
      <span id={GENIE_LAUNCHER_STATUS_ID} className="sr-only">
        {genieLauncherStatusText(launcherStatus)}
      </span>
      <div className="sr-only" role="status" aria-live="polite" data-genie-announcer="panel">
        {announcerText}
      </div>
      <button
        ref={fabRef}
        className={[
          'genie__fab',
          genieOpen ? 'is-hidden' : '',
          genieLauncherStateClass(launcherStatus),
        ].filter(Boolean).join(' ')}
        onClick={() => setGenieOpen(true)}
        aria-label="Open Genie"
        aria-describedby={launcherStatus === 'idle' ? undefined : GENIE_LAUNCHER_STATUS_ID}
        type="button"
      >
        <Icon name="sparkle" size={22} />
      </button>
      <div
        ref={panelRef}
        className={`genie ${genieOpen ? 'is-open' : ''} ${pos ? 'is-undocked' : ''}`}
        // Focusable container: a click on the transcript lands focus inside
        // the panel, which is what "Escape closes Genie" now keys off.
        tabIndex={-1}
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
          <GenieHistoryMenu
            open={historyOpen}
            onToggle={setHistoryOpen}
            onLoad={loadSession}
            disabled={typing}
          />
          {/* "New thread" is now a labeled control, not an icon-only button:
              it clears the persisted transcript, so the destructive intent
              has to be readable. `.btn--ghost .btn--sm` per the design
              system's existing button vocabulary. */}
          <button
            type="button"
            className="btn btn--ghost btn--sm genie__new-thread"
            onClick={(e) => {
              e.stopPropagation();
              newConversation();
            }}
            disabled={typing}
            aria-label="Start a new Genie thread"
            title="New thread"
          >
            <Icon name="chat" size={12} />
            <span className="genie__new-thread-label">New thread</span>
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
                ref={i === lastAnswerIndex ? lastAnswerRef : undefined}
                className="genie__msg genie__msg--ai"
              >
                <div className="bubble">
                  <GenieAnswer
                    payload={m.payload}
                    question={(() => {
                      const prev = msgs[i - 1];
                      return prev && prev.who === 'user' ? prev.text : undefined;
                    })()}
                    onFollowUp={(q, followUpConversationId) =>
                      void ask(q, followUpConversationId, Date.now())
                    }
                    followUpDisabledReason={busyReason}
                    announce={false}
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
          {pendingQuestion && (
            <div className="genie__msg genie__msg--user">{pendingQuestion}</div>
          )}
          {typing && (
            <div className="genie__msg genie__msg--ai">
              <div className="bubble">
                <GenieProgress
                  dense
                  progress={liveProgress}
                  startedAt={askStartedAt}
                  announce={false}
                />
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
                <button
                  key={s}
                  className="filter genie-chat__sample"
                  onClick={() => void ask(s, undefined, Date.now())}
                  type="button"
                >
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
            void ask(input, undefined, Date.now());
          }}
        >
          {/* The input stays editable mid-turn so the next question can be
              drafted; only sending is held (audit `genie-v2`). */}
          <input
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Ask about borrowers, segments, triggers…"
            aria-label="Ask Genie"
            aria-describedby={busyReason ? COMPOSER_BUSY_HINT_ID : undefined}
          />
          <Button
            variant="primary"
            size="sm"
            type="submit"
            icon="send"
            aria-label="Ask"
            disabled={busyReason !== null}
            title={busyReason ?? undefined}
          >
            Ask
          </Button>
        </form>
        {busyReason && (
          <p id={COMPOSER_BUSY_HINT_ID} className="genie__input-hint">
            {busyReason}
          </p>
        )}
      </div>
    </>
  );
}
