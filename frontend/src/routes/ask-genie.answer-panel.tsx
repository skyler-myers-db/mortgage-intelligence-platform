import { Fragment, useEffect, useRef, useState, useSyncExternalStore, type RefObject } from 'react';
import type { GenieActionSuggestion, GenieAnswer as GenieAnswerShape } from '../types';
import type { GenieLiveProgress } from '../lib/api';
import type { WarmingUpState } from '../lib/useWarmingUpRetry';
import { Button, Chip, EvidenceChip } from '../components/Primitives';
import { Icon } from '../components/Icon';
import { GenieAnswer } from '../components/mortgage/GenieAnswer';
import { GenieHistoryMenu } from '../components/mortgage/GenieHistoryMenu';
import { GenieProgress } from '../components/mortgage/GenieProgress';
import { GenieTurnActions } from '../components/mortgage/GenieTurnActions';
import { WarmingUpBlock } from '../components/ui/WarmingUpBlock';
import { friendlyAssetLabel } from '../lib/assetLabels';
import { drawerForAsset } from '../lib/drawerSources';
import {
  getGenieTurns,
  getGenieTurnsServerSnapshot,
  setGenieTurns,
  subscribeGenieTurns,
  type GenieTurn,
} from '../lib/genieConversationStore';
import { useComposerScrollClearance } from './ask-genie.composer-clearance';
import { renderSourceAssetChip } from './ask-genie.growth-agent.helpers';
import { useRevealLatestExchange } from './ask-genie.thread-scroll';

/**
 * AskGenieAnswerPanel — the composer + conversation surface extracted from
 * `ask-genie.tsx` (props in, callbacks out; mirrors the
 * ask-genie.compose-plan-card / ask-genie.growth-run-card precedent).
 *
 * The route used to show ONE answer: asking a second question erased the
 * first, so the deep-dive view had no conversation even though the floating
 * panel kept one. Both surfaces now read the same tab-scoped transcript store
 * (`lib/genieConversationStore`), so a thread started in the bubble continues
 * here and vice versa.
 *
 * Ordering is the floating panel's (audit 2026-09-21 `visual-07`): the thread
 * reads oldest-first, the suggestions sit under it, and the composer is docked
 * at the bottom of the surface (`position: sticky; bottom: 0`, routes/
 * ask-genie.css), so it stays in view however long the thread gets and a new
 * answer lands directly above it. When the new exchange starts off screen,
 * useRevealLatestExchange scrolls its question into view. The route used to
 * put the composer first with the latest answer under it, the reverse of the
 * floating panel.
 *
 * The source-chip classification depends only on `payload`, so it lives here
 * rather than in the parent — it moved wholesale with the surface it
 * annotates, and is now computed per turn.
 */

interface SourceChip {
  label: string;
  title?: string;
  variant?: 'warning';
}

/** Governed source values that intentionally stop before showing data. */
const BLOCKED_SOURCES = new Set(['policy_blocked', 'refused', 'data_gap', 'out_of_footprint']);

const BLOCKED_CHIP_LABELS: Record<string, string> = {
  refused: 'Prompt refused',
  data_gap: 'Source pending',
  out_of_footprint: 'Outside footprint',
};

export function sourceChipFor(payload: GenieAnswerShape): SourceChip | null {
  const sourceLabel = payload.source ?? '';
  if (sourceLabel === 'degraded') {
    return {
      label: 'Genie reconnecting',
      title:
        'The Genie answer path is temporarily unavailable. Live answers will resume after health recovers.',
      variant: 'warning',
    };
  }
  if (BLOCKED_SOURCES.has(sourceLabel)) {
    return {
      label: BLOCKED_CHIP_LABELS[sourceLabel] ?? 'Policy blocked',
      title: 'The answer was not displayed because it did not meet the governed Genie policy.',
      variant: 'warning',
    };
  }
  const label = payload.trusted_assets?.[0] || sourceLabel || '';
  return label ? { label } : null;
}

export interface AskGenieAnswerPanelProps {
  questionRef: RefObject<HTMLTextAreaElement | null>;
  question: string;
  /** Called on textarea change with the raw value (parent clears active asset). */
  onQuestionChange: (value: string) => void;
  /** Commit the current question to the warming-up fetch. */
  onAsk: (question: string) => void;
  /** Start a fresh Genie thread. */
  onNewThread: () => void;
  /** Adopt a past conversation from the History menu (id + restored turns). */
  onLoadSession: (conversationId: string, turns: GenieTurn[]) => void;
  /** A turn settled and was appended to the thread; the parent clears the
   *  composer so it never keeps the question that was just answered. */
  onSettled?: (question: string) => void;
  loading: boolean;
  warmingUp: WarmingUpState | null;
  errorMsg: string | null;
  onRetry: () => void;
  /** Full sample-question list; the composer shows the first four. */
  sampleQuestions: string[];
  payload: GenieAnswerShape | null;
  /** Live lifecycle telemetry for the in-flight turn (null when idle). */
  liveProgress?: GenieLiveProgress | null;
  /** Epoch ms when the current ask started; drives the elapsed ticker. */
  askStartedAt?: number | null;
  submittedQuestion: string | null;
  onFollowUp: (question: string, conversationId: string | null) => void;
  onAction: (action: GenieActionSuggestion) => void;
  /** Refusal card "Edit question": restore the refused prompt to the composer. */
  onEditQuestion?: (question: string) => void;
  actionStatus: string | null;
  /** Governed sources Genie reads (from `/api/genie/start`); the empty state
   *  cites them as evidence chips, so every source is one click from its proof. */
  sourceAssets?: readonly string[];
}

/** How many source chips the empty state shows. */
const EMPTY_STATE_SOURCE_CHIPS = 3;

/**
 * Composer placeholder, in the reviewed segment vocabulary. Short enough to
 * fit the composer's two empty lines on a phone: `field-sizing: content`
 * sizes an empty box to its placeholder, and a longer one grew the docked
 * composer over the empty state. The empty state above it already names what
 * Genie answers about.
 */
export const COMPOSER_PLACEHOLDER = 'Ask about your book, e.g. prime refi candidates by state';

function GenieThreadTurn({
  turn,
  onFollowUp,
  onAction,
  onEditQuestion,
}: {
  turn: GenieTurn;
  onFollowUp: (question: string, conversationId: string | null) => void;
  onAction: (action: GenieActionSuggestion) => void;
  onEditQuestion?: (question: string) => void;
}) {
  const chip = sourceChipFor(turn.response);
  const drawerForSource = chip ? drawerForAsset(chip.label) : null;
  return (
    <div className="surface surface--inset">
      <div className="surface__body">
        {chip && (
          <div className="chip-row mb-3">
            <span className="muted fs-11">Source:</span>
            {chip.variant === 'warning' ? (
              // Degraded / governed refusal: warning chip with tooltip so the
              // user knows why no data is shown. Not clickable.
              <Chip variant="warning" icon="info" title={chip.title}>
                {chip.label}
              </Chip>
            ) : drawerForSource ? (
              // Specific UC asset → open the matching drawer entry. Plain
              // label on the chip, the governed path in its tooltip (flow-10;
              // same as the workflow cards' source chips).
              <EvidenceChip source={drawerForSource} title={chip.label}>
                {friendlyAssetLabel(chip.label)}
              </EvidenceChip>
            ) : (
              // Generic / unknown source → inert chip so a click doesn't open
              // the wrong drawer. (Prior code defaulted to NBO and was
              // misleading.)
              <Chip variant="neutral" title={`Source: ${chip.label}`}>
                {friendlyAssetLabel(chip.label)}
              </Chip>
            )}
          </div>
        )}
        {/* withChart=true: opt this deep-dive view in to the auto-detected
            chart for top-N / per-state-style table_rows payloads. The floating
            bubble does NOT pass this prop, so its compact form is unchanged. */}
        <GenieAnswer
          payload={turn.response}
          question={turn.question || undefined}
          onFollowUp={onFollowUp}
          onAction={onAction}
          onEditQuestion={onEditQuestion}
          withChart
        />
      </div>
    </div>
  );
}

export function AskGenieAnswerPanel({
  questionRef,
  question,
  onQuestionChange,
  onAsk,
  onNewThread,
  onLoadSession,
  onSettled,
  loading,
  warmingUp,
  errorMsg,
  onRetry,
  sampleQuestions,
  payload,
  liveProgress = null,
  askStartedAt = null,
  submittedQuestion,
  onFollowUp,
  onAction,
  onEditQuestion,
  actionStatus,
  sourceAssets = [],
}: AskGenieAnswerPanelProps) {
  const composerSampleQuestions = sampleQuestions.slice(0, 4);
  const [historyOpen, setHistoryOpen] = useState(false);
  const storedTurns = useSyncExternalStore(
    subscribeGenieTurns,
    getGenieTurns,
    getGenieTurnsServerSnapshot,
  );
  const inFlight = loading || warmingUp !== null;

  // A settled payload joins the thread on the SAME render it arrives, so the
  // answer never disappears for a frame between "progress done" and "turn
  // stored". The effect below only persists what is already being shown.
  //
  // `submittedQuestion === null` means no question is outstanding (New
  // thread, a restored session, an actor-boundary reset). The cached answer
  // of the question that WAS outstanding must not be re-adopted then — it
  // belongs to a thread the user just left.
  const answered = submittedQuestion !== null ? payload : null;
  // Anywhere in the thread, not just at the end: the floating panel writes to
  // the same store, so this route's settled turn can already have another
  // surface's turn stacked on top of it.
  const isStored = storedTurns.some((turn) => turn.response === answered);
  const pendingTurn: GenieTurn | null =
    answered && !isStored ? { question: submittedQuestion ?? '', response: answered } : null;
  const thread = pendingTurn ? [...storedTurns, pendingTurn] : storedTurns;

  // Append-once latch. StrictMode re-runs effects with the same closure, and
  // the payload object is stable across re-renders, so identity is the guard.
  const appendedRef = useRef<GenieAnswerShape | null>(null);
  useEffect(() => {
    if (submittedQuestion === null || !payload || appendedRef.current === payload) return;
    appendedRef.current = payload;
    const stored = getGenieTurns();
    if (stored.some((turn) => turn.response === payload)) return;
    // setGenieTurns enforces MAX_STORED_TURNS (oldest-first eviction).
    setGenieTurns([...stored, { question: submittedQuestion, response: payload }]);
    onSettled?.(submittedQuestion);
  }, [payload, submittedQuestion, onSettled]);

  const turnKey = (turn: GenieTurn, index: number) =>
    `${turn.response.message_id ?? turn.response.question_hash ?? 'turn'}-${index}`;

  // The exchange that starts at the end of the thread: the question in
  // flight, else the latest settled turn's question. When it changes and is
  // off screen, it is scrolled into view (the composer is docked below it).
  const showInFlight = inFlight && Boolean(submittedQuestion);
  const latestIndex = thread.length - 1;
  const latestAnchorRef = useRef<HTMLDivElement>(null);
  const dockRef = useRef<HTMLFormElement>(null);
  useRevealLatestExchange(
    latestAnchorRef,
    dockRef,
    showInFlight ? `pending-${thread.length}` : `settled-${thread.length}`,
  );
  // Focus scrolling stops above the docked composer (WCAG 2.2 SC 2.4.11).
  useComposerScrollClearance(dockRef);

  // Conversational controls (audit 2026-09-21 `genie-03`, client-only slice):
  // ArrowUp in an empty composer recalls the last question; Edit reloads a
  // sent question; Regenerate / Retry re-ask it as a NEW turn through the
  // same `onAsk` path (and the same server-side guards). Stop needs the
  // route's fetch lifecycle (routes/ask-genie.tsx) and lands with the shared
  // turn hook; the floating panel has it today.
  const lastQuestion =
    submittedQuestion ??
    [...thread].reverse().find((turn) => turn.question.trim().length > 0)?.question ??
    null;
  const editQuestion = (text: string) => {
    onQuestionChange(text);
    questionRef.current?.focus();
  };
  const canAsk = !loading && warmingUp === null && question.trim().length > 0;
  const reaskDisabledReason = inFlight ? 'Genie is still answering. This unlocks when the answer lands.' : null;
  const questionBubble = (turn: GenieTurn, anchor: boolean) => {
    const source = turn.response.source ?? '';
    // Refusals and data gaps would only repeat themselves: Edit only.
    const reask = source === 'degraded' ? 'retry' : BLOCKED_SOURCES.has(source) ? null : 'regenerate';
    return (
      <>
        <div ref={anchor ? latestAnchorRef : undefined} className="genie__msg genie__msg--user">
          {turn.question}
        </div>
        <GenieTurnActions
          placement="question"
          question={turn.question}
          onEdit={editQuestion}
          onRetry={reask === 'retry' ? onAsk : undefined}
          onRegenerate={reask === 'regenerate' ? onAsk : undefined}
          disabled={inFlight}
          disabledReason={reaskDisabledReason}
        />
      </>
    );
  };

  return (
    <div className="surface">
      <div className="surface__hdr surface__hdr--split">
        <div className="surface__hdr-main">
          <Icon name="sparkle" size={14} className="icon-accent" />
          <h2 className="h-4">Conversation</h2>
        </div>
        <div className="chip-row">
          <GenieHistoryMenu
            open={historyOpen}
            onToggle={setHistoryOpen}
            onLoad={(conversationId, turns) => {
              setHistoryOpen(false);
              onLoadSession(conversationId, turns);
            }}
            disabled={inFlight}
          />
          <Button variant="ghost" size="sm" icon="chat" onClick={onNewThread} disabled={inFlight}>
            New thread
          </Button>
        </div>
      </div>
      <div className="surface__body">
        {thread.length === 0 && !inFlight && !errorMsg && (
          <div className="surface surface--inset">
            <div className="surface__body genie-empty">
              <div className="genie-empty__icon">
                <Icon name="sparkle" size={16} />
              </div>
              <div>
                <div className="genie-empty__title">Ask about your book — coverage, segments, borrowers, market shifts.</div>
                <p className="genie-empty__copy">
                  Each answer shows the figures it used, where they came from and how fresh they are. Any follow-up
                  action still needs your approval.
                </p>
                {sourceAssets.length > 0 && (
                  <div className="chip-row mt-2" role="group" aria-label="Sources Genie answers from">
                    <span className="muted fs-11">Answers come from:</span>
                    {sourceAssets.slice(0, EMPTY_STATE_SOURCE_CHIPS).map((asset) => renderSourceAssetChip(asset))}
                  </div>
                )}
              </div>
            </div>
          </div>
        )}
        {(thread.length > 0 || inFlight) && (
          <div className="genie-thread">
            {/* Fragment, not a wrapper div: the user bubble aligns itself to
                the right edge of `.genie-thread`, so every bubble and card
                must stay a DIRECT flex child of it. */}
            {thread.map((turn, index) => (
              <Fragment key={turnKey(turn, index)}>
                {turn.question && questionBubble(turn, !showInFlight && index === latestIndex)}
                <GenieThreadTurn
                  turn={turn}
                  onFollowUp={onFollowUp}
                  onAction={onAction}
                  onEditQuestion={onEditQuestion}
                />
              </Fragment>
            ))}
            {showInFlight && (
              <>
                <div ref={latestAnchorRef} className="genie__msg genie__msg--user">{submittedQuestion}</div>
                {loading && !warmingUp && (
                  <div className="surface surface--inset">
                    <div className="surface__body">
                      <GenieProgress progress={liveProgress} startedAt={askStartedAt} />
                    </div>
                  </div>
                )}
              </>
            )}
          </div>
        )}
        {warmingUp && (
          <div className="mt-4">
            <WarmingUpBlock state={warmingUp} title="Asking Genie" compact />
          </div>
        )}
        {errorMsg && !warmingUp && (
          <div
            className="surface surface--inset surface--danger mt-4"
            role="alert"
          >
            <div className="surface__body status-callout--danger">
              <span>{errorMsg}</span>
              <button
                type="button"
                className="btn btn--ghost btn--sm"
                onClick={onRetry}
                disabled={loading}
                aria-label="Retry Genie question"
              >
                Retry
              </button>
            </div>
          </div>
        )}
        {actionStatus && (
          <div className="status-callout status-callout--info mt-3">{actionStatus}</div>
        )}
        {composerSampleQuestions.length > 0 && (
          <div
            className="genie-composer__samples"
            role="group"
            aria-label="Suggested Genie questions"
          >
            {composerSampleQuestions.map((q) => (
              <button
                key={q}
                type="button"
                className="filter filter--question"
                onClick={() => onAsk(q)}
              >
                <Icon name="sparkle" size={11} />
                <span className="filter__text">{q}</span>
              </button>
            ))}
          </div>
        )}
      </div>
      {/* The docked composer (visual-07): the prototype's card footer,
          sticky at the bottom of the route's scroller. */}
      <form
        ref={dockRef}
        className="surface__ft genie-composer"
        aria-label="Ask Genie composer"
        onSubmit={(e) => {
          e.preventDefault();
          if (canAsk) onAsk(question);
        }}
      >
        <textarea
          ref={questionRef}
          aria-label="Ask Genie — question"
          placeholder={COMPOSER_PLACEHOLDER}
          rows={2}
          value={question}
          onChange={(e) => {
            onQuestionChange(e.target.value);
          }}
          onKeyDown={(e) => {
            // ArrowUp in an EMPTY composer recalls the last question
            // (genie-03). A non-empty draft keeps the caret movement.
            if (e.key === 'ArrowUp' && question.length === 0 && lastQuestion) {
              e.preventDefault();
              onQuestionChange(lastQuestion);
              return;
            }
            // 2026-05-04 (FIX Δ1): standard chat keymap — Enter
            // submits, Shift+Enter inserts a newline. Match how
            // Slack / GitHub PRs behave so the keyboard-first user
            // doesn't have to mouse over to the Ask Genie button.
            // The submit-disabled guard mirrors the button's
            // `disabled` prop so a stray Enter during a warming-up
            // request can't double-fire.
            if (
              e.key === 'Enter' &&
              !e.shiftKey &&
              !e.metaKey &&
              !e.ctrlKey &&
              !e.altKey
            ) {
              e.preventDefault();
              if (canAsk) {
                onAsk(question);
              }
            }
          }}
          className="route-textarea route-textarea--genie genie-composer__input"
        />
        <Button variant="primary" icon="send" type="submit" disabled={!canAsk}>
          {loading || warmingUp !== null ? 'Asking…' : 'Ask Genie'}
        </Button>
      </form>
    </div>
  );
}
