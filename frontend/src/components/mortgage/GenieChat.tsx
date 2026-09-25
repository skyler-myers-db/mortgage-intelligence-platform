import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
} from 'react';
import { useLocation, useNavigate } from 'react-router';
import { useApp } from '../AppContext';
import { api } from '../../lib/api';
import type { GenieActionSuggestion, GenieAnswer as GenieAnswerShape } from '../../types';
import { Icon } from '../Icon';
import { Button } from '../Primitives';
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
  getGenieTurnsServerSnapshot,
  setGenieTurns,
  subscribeGenieTurns,
  turnsToMessages,
  type GenieTurn,
} from '../../lib/genieConversationStore';
import {
  GENIE_BUSY_REASON,
  GOVERNED_ACTION_SOURCE,
  announceGenie,
  clearGenieTurnNotes,
  getGenieTurnSnapshot,
  resumeGenieTurnFromSession,
  startGenieTurn,
  stopGenieTurn,
  subscribeGenieTurnSettled,
  useGenieTurn,
  type GenieTurnOutcome,
} from '../../lib/genieInFlightTurn';
import {
  GENIE_LAUNCHER_STATUS_ID,
  genieLauncherStateClass,
  genieLauncherStatusText,
  setGenieTurnStatus,
  type GenieTurnStatus,
} from '../../lib/genieTurnStatus';
import { GENIE_POINTER_RESIZE_HANDLES, useGenieWindow } from './useGenieWindow';
import { useGeniePanelDismissal } from './useGeniePanelDismissal';
import { useGenieTranscriptScroll } from './useGenieTranscriptScroll';
import { useGenieMessageEntrance } from './useGenieMessageEntrance';
import { GenieAnnouncerRegion } from './GenieAnnouncerRegion';
import { isGenieRouteAskVisible } from './useGenieAnnouncer';
import { GenieHistoryMenu } from './GenieHistoryMenu';
import { GenieChatBody } from './GenieChatBody';
import { genieActionConfirmation, runGenieActionRequest, sourceAssetsFor } from './GenieChat.helpers';

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
 * The turn is NOT the panel's (audit 2026-09-21 wave 2 `runtime-01`): the
 * tab's one in-flight turn lives in lib/genieInFlightTurn, which both this
 * panel and `/ask-genie` read. The panel renders the store's pending turn and
 * notes whichever surface started it, holds every way to start a second one
 * while it runs (`genie-v2`), and never aborts it on unmount: the shell keeps
 * the panel mounted after its first open, and a boundary that remounts it
 * finds the same turn. A reload resumes a turn that was still polling (the
 * first Genie surface to mount asks the store to). The actor-boundary reset
 * still aborts the turn and clears everything, from the store's own listener.
 *
 * Conversational controls (audit 2026-09-21 `genie-03`, client-only slice):
 * Stop stops waiting (the store bumps the generation so a late reply is
 * ignored), gives the question back to the composer and leaves a "Stopped"
 * note; Retry / Regenerate re-ask as a NEW turn (a Genie thread cannot
 * rewrite its history); Edit reloads a sent question; ArrowUp in an empty
 * composer recalls the last question. There is no server-side cancel.
 *
 * One announcer (`a11y-06`): the persistent region below speaks through
 * useGenieAnnouncer, which gives the floor to /ask-genie's own region while
 * the panel is closed over that route.
 *
 * Page context (`genie-04`, phase 1): the empty state shows the starters
 * curated for the current route, and `openGenie({ prompt })` from any surface
 * PREFILLS the composer (lib/genieOpen) -- it never submits.
 */

const COMPOSER_BUSY_HINT_ID = 'genie-composer-busy';
const ACTION_REASON = 'A governed action is running. Ask unlocks when it finishes.';

export function GenieChat() {
  const { genieOpen, setGenieOpen, lender, refreshWorkspace } = useApp();
  const navigate = useNavigate();
  const { pathname } = useLocation();
  // The settled transcript and the in-flight turn are two external stores
  // read the same way: a landing turn (append, then the in-flight turn
  // cleared, in one task) commits in ONE render, so no frame shows the
  // pending question without its answer, or the two side by side.
  const turns = useSyncExternalStore(subscribeGenieTurns, getGenieTurns, getGenieTurnsServerSnapshot);
  const msgs = useMemo(() => turnsToMessages(turns, sourceAssetsFor), [turns]);
  const { inFlight, notes } = useGenieTurn();
  const [input, setInput] = useState('');
  // Two independent busy sources: a turn (either surface's) and a governed
  // action started from this panel.
  const [actionRunning, setActionRunning] = useState(false);
  const typing = inFlight !== null || actionRunning;
  const [historyOpen, setHistoryOpen] = useState(false);
  // How the turn that landed while the panel was closed ended, until opened.
  const [unseen, setUnseen] = useState<GenieTurnOutcome | null>(null);
  // The most recently sent question (ArrowUp recall); read in handlers only.
  const lastQuestionRef = useRef<string | null>(null);
  // The reset listener below is bound once, before the scroll hook exists.
  const cancelAnchorRef = useRef<() => void>(() => undefined);
  const genieOpenRef = useRef(genieOpen);
  useEffect(() => {
    genieOpenRef.current = genieOpen;
  }, [genieOpen]);

  // The store owns the turn, so unmounting stops nothing; only the launcher
  // signal this panel drives goes back to idle.
  useEffect(() => () => setGenieTurnStatus('idle'), []);
  // A reload may have interrupted a turn: resume it, once per page.
  useEffect(() => {
    resumeGenieTurnFromSession();
  }, []);

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
    // The turn store aborts the turn and clears its notes and announcement on
    // this event itself; this clears what the panel holds.
    const onActorBoundaryReset = () => {
      suppressBootstrapConversationRef.current = true;
      lastQuestionRef.current = null;
      setConversationId(null);
      // The conversation is not resumable and the prior actor's questions
      // must not linger in this tab.
      clearGenieTurns();
      setInput('');
      setActionRunning(false);
      setHistoryOpen(false);
      setUnseen(null);
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
  // bottom (audit `motion-v2`), but only while the reader follows the
  // transcript: reading an earlier turn is never interrupted (`genie-08`).
  const { anchorNextAnswer, cancelAnchor, markSent, newAnswer, jumpToNewAnswer } = useGenieTranscriptScroll({
    open: genieOpen,
    bodyRef,
    lastAnswerRef,
    messages: msgs,
    pendingQuestion: inFlight?.revealed ? inFlight.question : null,
    busy: typing,
  });
  useEffect(() => {
    cancelAnchorRef.current = cancelAnchor;
  }, [cancelAnchor]);
  // New messages enter once, and only while the panel is open (motion-v2).
  const entrance = useGenieMessageEntrance({ isVisible: () => genieOpen, inFlight, notes });

  // A turn settled (either surface's, even with this panel closed): scroll to
  // its answer, follow its conversation, and badge the launcher unless the
  // user can already see it (panel open, or /ask-genie's Ask tab shown).
  useEffect(
    () =>
      subscribeGenieTurnSettled((event) => {
        if (event.persistedConversationId) setConversationId(event.persistedConversationId);
        anchorNextAnswer();
        if (!genieOpenRef.current && !isGenieRouteAskVisible()) setUnseen(event.outcome);
      }),
    [anchorNextAnswer],
  );

  // Opening the panel: the badge has done its job, and the conversation id is
  // re-read because `/ask-genie` may have advanced the shared thread while the
  // panel sat closed. Never while a turn is in flight: that turn owns the id
  // it was submitted with.
  useEffect(() => {
    if (!genieOpen) return;
    setUnseen(null);
    if (getGenieTurnSnapshot().inFlight === null) setConversationId(readGenieConversationId());
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
        announceGenie('Your Genie draft was replaced by the question you opened. Nothing was sent.');
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
      : unseen
        ? 'ready'
        : 'idle';
  useEffect(() => {
    setGenieTurnStatus(launcherStatus);
  }, [launcherStatus]);

  /**
   * Start a turn. `startedAt` is the moment of the user's action and is read
   * (`Date.now()`) at the event site, never here: the React Compiler cannot
   * prove a component-scope function runs only from event handlers, so an
   * impure call inside it is rejected as a render-time call.
   */
  const ask = (q: string, followUpConversationId: string | null | undefined, startedAt: number) => {
    const trimmed = q.trim();
    if (!trimmed || actionRunning) return;
    const activeConversationId = followUpConversationId ?? conversationId;
    // A second ask NEVER replaces a running turn (audit `genie-v2`): the
    // store's synchronous latch refuses it whichever surface holds the turn.
    // The controls are disabled while busy; this is what holds when a
    // keypress or a stale chip gets through anyway. The draft is left intact.
    if (!startGenieTurn({ question: trimmed, conversationId: activeConversationId, surface: 'panel', startedAt })) {
      return;
    }
    lastQuestionRef.current = trimmed;
    markSent();
    if (!activeConversationId) {
      setConversationId(null);
      clearGenieConversationState();
    }
    setInput('');
  };

  /**
   * Stop waiting for the in-flight turn (genie-03, client-only; there is no
   * cancel endpoint yet, so Genie may still finish it and its reply is
   * discarded). The question goes back to the composer unless the user is
   * drafting another one there (a draft is never overwritten; the note's Edit
   * reloads the stopped question). Focus always lands in the composer: the
   * Stop button unmounts under the user, and focus left on <body> would also
   * switch off "Escape closes Genie", which keys off focus inside the panel.
   */
  const stopTurn = () => {
    if (!inFlight) return;
    const question = stopGenieTurn() ?? '';
    cancelAnchor();
    const restore = question !== '' && input.trim() === '';
    if (restore) loadComposer(question);
    else focusComposer();
    announceGenie(
      restore
        ? 'Stopped. The question is back in the composer.'
        : question
          ? 'Stopped. Your draft was kept; Edit reloads the stopped question.'
          : 'Stopped.',
    );
  };

  const newConversation = () => {
    if (typing) return;
    suppressBootstrapConversationRef.current = true;
    setConversationId(null);
    clearGenieTurns();
    clearGenieTurnNotes();
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
  const loadSession = (conversationIdToLoad: string, loaded: GenieTurn[]) => {
    if (typing) return;
    suppressBootstrapConversationRef.current = true;
    // ArrowUp recalls from the restored thread (its last question), not from
    // the one it replaced.
    lastQuestionRef.current = null;
    setGenieTurns(loaded);
    clearGenieTurnNotes();
    cancelAnchor();
    setConversationId(conversationIdToLoad);
    writeGenieConversationId(conversationIdToLoad);
    setHistoryOpen(false);
    setInput('');
  };

  /** A governed action's result bubble: appended, anchored, spoken, badged
   *  (a failed action badges as a result to see, never as an answer). */
  const landActionBubble = (payload: GenieAnswerShape, spoken: string) => {
    appendGenieTurn('', payload);
    if (genieOpenRef.current) entrance.mark(payload);
    anchorNextAnswer();
    announceGenie(spoken);
    if (!genieOpenRef.current) setUnseen(payload.source === 'degraded' ? 'failed' : 'answered');
  };

  const runAction = (action: GenieActionSuggestion, payload: GenieAnswerShape) => {
    markSent();
    setActionRunning(true);
    return runGenieActionRequest(action, payload, conversationId)
      .then((outcome) => {
        if (outcome.kind !== 'ok') {
          landActionBubble({ answer: outcome.message, source: 'degraded', trusted_assets: [] }, outcome.message);
          return;
        }
        if (action.action_type === 'save_borrowers') refreshWorkspace();
        const confirmed = genieActionConfirmation(outcome.result);
        landActionBubble(
          {
            answer: confirmed,
            source: GOVERNED_ACTION_SOURCE,
            trusted_assets: [],
            conversation_id: payload.conversation_id,
          },
          confirmed,
        );
        if (outcome.result.route) navigate(outcome.result.route);
      })
      .finally(() => setActionRunning(false));
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

  const busyReason = inFlight ? GENIE_BUSY_REASON : actionRunning ? ACTION_REASON : null;
  // Per-route starters replace the identical global set (genie-04); the
  // server's list stays the fallback for routes without curated ones.
  const routeStarters = genieStartersForRoute(pathname);
  const starters = routeStarters.length > 0 ? routeStarters : sampleQuestions;

  return (
    <>
      {/* Both live OUTSIDE `.genie`: the panel is aria-hidden while closed,
          and these must keep working then. The first is the launchers'
          `aria-describedby` target; the second is the panel's ONE persistent
          polite announcer (audit `a11y-06`). The elapsed ticker is nowhere
          near it. */}
      <span id={GENIE_LAUNCHER_STATUS_ID} className="sr-only">
        {genieLauncherStatusText(launcherStatus, unseen ?? 'answered')}
      </span>
      <GenieAnnouncerRegion surface="panel" visible={genieOpen} />
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
          notes={notes}
          inFlight={inFlight}
          typing={typing}
          busyReason={busyReason}
          starters={starters}
          onAsk={(q, followUpConversationId) => ask(q, followUpConversationId, Date.now())}
          onAction={runAction}
          onEdit={loadComposer}
          onStop={stopTurn}
          onAnnounce={announceGenie}
          newAnswer={newAnswer}
          onJumpToNewAnswer={jumpToNewAnswer}
          entrance={entrance}
        />
        <form
          className="genie__input"
          onSubmit={(e) => {
            e.preventDefault();
            ask(input, undefined, Date.now());
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
