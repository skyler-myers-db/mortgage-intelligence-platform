import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
} from 'react';
import { useLocation, useNavigate } from 'react-router';
import { useApp } from '../AppContext';
import { ApiError, api, isAbortError, type GenieLiveProgress } from '../../lib/api';
import { GenieLiveError, askGenieLive } from '../../lib/genieAsk';
import type { GenieActionSuggestion, GenieAnswer as GenieAnswerShape } from '../../types';
import { Icon } from '../Icon';
import { Button } from '../Primitives';
import { GOVERNED_ACTION_SOURCE } from './GenieAnswer';
import { genieProgressLabel } from './GenieProgress';
import {
  GENIE_CONVERSATION_RESET_EVENT,
  clearGenieConversationState,
  readGenieConversationId,
  writeGenieConversationId,
} from '../../lib/genieConversation';
import { genieStartersForRoute } from '../../lib/genieContext';
import { consumeGeniePrefill, subscribeGeniePrefill } from '../../lib/genieOpen';
import {
  appendGenieTurn,
  clearGenieTurns,
  getGenieTurns,
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
import { GenieChatBody, type StoppedTurn } from './GenieChatBody';
import { shouldPersistConversation, sourceAssetsFor } from './GenieChat.helpers';

export {
  shouldPersistConversation,
  shouldRenderGenieSourceAssets,
  sourceAssetsFor,
  warningLabelForSource,
} from './GenieChat.helpers';

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
 * Conversational controls (audit 2026-09-21 `genie-03`, client-only slice):
 * Stop abandons the client turn, bumps the generation so a late reply is
 * ignored, gives the question back to the composer and leaves a "Stopped"
 * note in the transcript; Retry / Regenerate re-ask as a NEW turn (a Genie
 * thread cannot rewrite its history); Edit reloads a sent question; ArrowUp
 * in an empty composer recalls the last question. There is no server-side
 * cancel: Genie may still finish a stopped turn, and its reply is discarded.
 *
 * Page context (`genie-04`, phase 1): the empty state shows the starters
 * curated for the current route, and `openGenie({ prompt })` from any surface
 * PREFILLS the composer (lib/genieOpen) -- it never submits.
 *
 * The AI message shape now holds the full GenieAnswer payload so
 * metric_value / table_rows / follow_up_questions all render in the bubble
 * via the shared <GenieAnswer> subcomponent.
 */

const COMPOSER_BUSY_HINT_ID = 'genie-composer-busy';
const ASKING_REASON =
  'Genie is still answering. Keep drafting: Ask unlocks when this answer lands, or press Stop. Closing the panel does not stop it.';
const ACTION_REASON = 'A governed action is running. Ask unlocks when it finishes.';
/** Stopped-turn notes kept in the panel (they are not transcript turns). */
const MAX_STOPPED_TURNS = 20;

export function GenieChat() {
  const { genieOpen, setGenieOpen, lender, refreshWorkspace } = useApp();
  const navigate = useNavigate();
  const { pathname } = useLocation();
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
  // Turns the user stopped, kept in transcript order (see GenieChatBody).
  const [stoppedTurns, setStoppedTurns] = useState<StoppedTurn[]>([]);
  // Abort + generation control for the in-flight live turn (QA M3): a
  // conversation reset, New thread or Stop must stop the poll loop, and a
  // turn that resolves AFTER that must not re-persist the previous actor's
  // conversation id or append an orphan bubble to the cleared thread.
  const askAbortRef = useRef<AbortController | null>(null);
  const askGenerationRef = useRef(0);
  // Synchronous in-flight latch (audit `genie-v2`): two submits in one tick
  // both read `asking === false` from their render's closure. Also read by
  // the open effect below, which must not re-run when a turn starts/settles.
  const askInFlightRef = useRef(false);
  // The most recently sent question (ArrowUp recall); read in handlers only.
  const lastQuestionRef = useRef<string | null>(null);
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
      lastQuestionRef.current = null;
      setConversationId(null);
      // An actor-boundary reset / 403 invalidates the transcript too: the
      // conversation is not resumable and the prior actor's questions must
      // not linger in this tab — including the in-flight question, the
      // stopped notes, the unseen-answer badge and the last announcement.
      clearGenieTurns();
      setPendingQuestion(null);
      setInput('');
      setAsking(false);
      setActionRunning(false);
      setHistoryOpen(false);
      setLiveProgress(null);
      setAskStartedAt(null);
      setStoppedTurns([]);
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

  /** Put `text` in the composer and move the caret to its end. */
  const loadComposer = useCallback((text: string) => {
    setInput(text);
    queueMicrotask(() => {
      const el = inputRef.current;
      if (!el) return;
      el.focus();
      el.setSelectionRange(text.length, text.length);
    });
  }, []);

  /** Focus the composer as it is (a draft stays untouched), caret at its end. */
  const focusComposer = useCallback(() => {
    queueMicrotask(() => {
      const el = inputRef.current;
      if (!el) return;
      el.focus();
      el.setSelectionRange(el.value.length, el.value.length);
    });
  }, []);

  // `openGenie({ prompt })` prefill (genie-04): consumed while the panel is
  // open -- on open for a request made while it was closed or not yet
  // mounted, and at once for one made while it is already open. A prefill
  // replaces the draft (the user just asked for it) and is NEVER sent: only
  // the user's own Ask submits it. Replacing a draft is said out loud, so it
  // never disappears silently.
  useEffect(() => {
    if (!genieOpen) return undefined;
    const apply = () => {
      const prompt = consumeGeniePrefill();
      if (prompt === null) return;
      const draft = inputRef.current?.value.trim() ?? '';
      loadComposer(prompt);
      if (draft !== '' && draft !== prompt.trim()) {
        setAnnouncement('Your Genie draft was replaced by the question you opened. Nothing was sent.');
      }
    };
    apply();
    return subscribeGeniePrefill(apply);
  }, [genieOpen, loadComposer]);

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
    lastQuestionRef.current = trimmed;
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
      // A reset / new thread / Stop while in flight invalidates this turn:
      // never re-persist its conversation id or append it to the thread.
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
      // A superseded turn (reset / teardown / Stop) already had its state
      // cleared by whoever invalidated it, in-flight latch included.
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

  /**
   * Stop the in-flight live turn (genie-03, client-only). The generation bump
   * is what makes a reply that still arrives -- the server keeps working;
   * there is no cancel endpoint yet -- land nowhere: not in the transcript,
   * not in the persisted conversation id. The abort ends the poll loop. The
   * question goes back to the composer unless the user is drafting another
   * one there (a draft is never overwritten; the note's Edit reloads the
   * stopped question), and a "Stopped" note keeps the transcript honest. New
   * thread and History unlock because nothing is in flight any more. Focus
   * always lands in the composer: the Stop button unmounts under the user,
   * and focus left on <body> would also switch off "Escape closes Genie",
   * which keys off focus inside the panel.
   */
  const stopTurn = () => {
    if (!askInFlightRef.current) return;
    askGenerationRef.current += 1;
    askAbortRef.current?.abort();
    askAbortRef.current = null;
    askInFlightRef.current = false;
    const question = pendingQuestion ?? '';
    setAsking(false);
    setPendingQuestion(null);
    setLiveProgress(null);
    setAskStartedAt(null);
    cancelAnchor();
    const restore = question !== '' && input.trim() === '';
    if (question) {
      setStoppedTurns((prev) =>
        [...prev, { atTurnIndex: getGenieTurns().length, question }].slice(-MAX_STOPPED_TURNS),
      );
    }
    if (restore) loadComposer(question);
    else focusComposer();
    setAnnouncement(
      restore
        ? 'Stopped. The question is back in the composer.'
        : 'Stopped. Your draft was kept; Edit reloads the stopped question.',
    );
  };

  const newConversation = () => {
    if (typing) return;
    suppressBootstrapConversationRef.current = true;
    setConversationId(null);
    clearGenieTurns();
    setStoppedTurns([]);
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
    // ArrowUp recalls from the restored thread (its last question), not from
    // the one it replaced.
    lastQuestionRef.current = null;
    setGenieTurns(turns);
    setStoppedTurns([]);
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

  /** ArrowUp in an EMPTY composer recalls the last question (genie-03). */
  const onComposerKeyDown = (e: ReactKeyboardEvent<HTMLInputElement>) => {
    if (e.key !== 'ArrowUp' || input !== '') return;
    const lastFromTranscript = [...msgs].reverse().find((m) => m.who === 'user');
    const last =
      lastQuestionRef.current ??
      (lastFromTranscript && lastFromTranscript.who === 'user' ? lastFromTranscript.text : null);
    if (!last) return;
    e.preventDefault();
    loadComposer(last);
  };

  const busyReason = asking ? ASKING_REASON : actionRunning ? ACTION_REASON : null;
  // Per-route starters replace the identical global set (genie-04); the
  // server's list stays the fallback for routes without curated ones.
  const routeStarters = genieStartersForRoute(pathname);
  const starters = routeStarters.length > 0 ? routeStarters : sampleQuestions;
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
        <GenieChatBody
          open={genieOpen}
          bodyRef={bodyRef}
          lastAnswerRef={lastAnswerRef}
          messages={msgs}
          stoppedTurns={stoppedTurns}
          pendingQuestion={pendingQuestion}
          asking={asking}
          typing={typing}
          liveProgress={liveProgress}
          askStartedAt={askStartedAt}
          busyReason={busyReason}
          starters={starters}
          onAsk={(q, followUpConversationId) => void ask(q, followUpConversationId, Date.now())}
          onAction={runAction}
          onEdit={loadComposer}
          onStop={stopTurn}
        />
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
            onKeyDown={onComposerKeyDown}
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
