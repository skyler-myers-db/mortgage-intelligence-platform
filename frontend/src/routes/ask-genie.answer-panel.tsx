import { Fragment, useRef, useState, useSyncExternalStore, type ReactNode, type RefObject } from 'react';
import type { GenieActionSuggestion, GenieAnswer as GenieAnswerShape } from '../types';
import { Button, Chip, EvidenceChip } from '../components/Primitives';
import { Icon } from '../components/Icon';
import { GenieAnswer } from '../components/mortgage/GenieAnswer';
import { GenieCollapsedTurn } from '../components/mortgage/GenieCollapsedTurn';
import { GenieHistoryMenu } from '../components/mortgage/GenieHistoryMenu';
import { GenieProgress } from '../components/mortgage/GenieProgress';
import { GenieTurnActions } from '../components/mortgage/GenieTurnActions';
import { GENIE_RESUMING_LABEL, GenieStopRow, GenieTurnNote } from '../components/mortgage/GenieTurnNote';
import { useGenieTurnCollapse, type GenieTurnPresentation } from '../components/mortgage/useGenieTurnCollapse';
import { friendlyAssetLabel } from '../lib/assetLabels';
import { drawerForAsset } from '../lib/drawerSources';
import {
  getGenieTurns,
  getGenieTurnsServerSnapshot,
  subscribeGenieTurns,
  type GenieTurn,
} from '../lib/genieConversationStore';
import {
  GENIE_BUSY_REASON,
  announceGenie,
  stopGenieTurn,
  useGenieTurn,
  type GenieTurnNote as GenieTurnNoteShape,
} from '../lib/genieInFlightTurn';
import { useComposerScrollClearance } from './ask-genie.composer-clearance';
import { renderSourceAssetChip } from './ask-genie.growth-agent.helpers';
import { useRevealLatestExchange, useRevealLatestOnArrival } from './ask-genie.thread-scroll';

/**
 * AskGenieAnswerPanel — the composer + conversation surface extracted from
 * `ask-genie.tsx` (props in, callbacks out; mirrors the
 * ask-genie.compose-plan-card / ask-genie.growth-run-card precedent).
 *
 * Both Genie surfaces read the same tab-scoped stores: the settled transcript
 * (`lib/genieConversationStore`) and, since wave 2 (`runtime-01`), the ONE
 * in-flight turn (`lib/genieInFlightTurn`). So a turn asked in the floating
 * panel shows here as pending, a turn asked here keeps running when the user
 * leaves the page, and coming back shows the same pending turn, or its answer
 * once the store has landed it. While a turn from EITHER surface is in
 * flight, everything that would start a second one is held with one reason
 * (`genie-v2`), and Stop (`genie-03`, `states-08`) stops waiting for it.
 *
 * Ordering is the floating panel's (audit 2026-09-21 `visual-07`): the thread
 * reads oldest-first, the suggestions sit under it, and the composer is docked
 * at the bottom of the surface (`position: sticky; bottom: 0`, routes/
 * ask-genie.css), so it stays in view however long the thread gets and a new
 * answer lands directly above it. When the new exchange starts off screen,
 * useRevealLatestExchange scrolls its question into view.
 *
 * Failures arrive as degraded turns (with Retry) from the store; this panel
 * renders no error block and no live region of its own: the route's one
 * announcer sits outside every tabpanel (ask-genie.tsx).
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
  /** Start a turn with this question (the store refuses while one runs). */
  onAsk: (question: string) => void;
  /** Start a new Genie thread. */
  onNewThread: () => void;
  /** Adopt a past conversation from the History menu (id + restored turns). */
  onLoadSession: (conversationId: string, turns: GenieTurn[]) => void;
  /** Full sample-question list; the composer shows the first four. */
  sampleQuestions: string[];
  onFollowUp: (question: string, conversationId: string | null) => void;
  /** A governed action, bound to the answer it was offered on. */
  onAction: (action: GenieActionSuggestion, payload: GenieAnswerShape) => void | Promise<void>;
  /** Refusal card "Edit question": restore the refused prompt to the composer. */
  onEditQuestion?: (question: string) => void;
  actionStatus: string | null;
  /** Governed sources Genie reads (from `/api/genie/start`); the empty state
   *  cites them as evidence chips, so every source is one click from its proof. */
  sourceAssets?: readonly string[];
}

/** How many source chips the empty state shows. */
const EMPTY_STATE_SOURCE_CHIPS = 3;

/** id of the busy reason the composer points `aria-describedby` at. */
const BUSY_HINT_ID = 'ask-genie-busy';

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
  followUpDisabledReason,
  presentation,
  onToggleCollapse,
}: {
  turn: GenieTurn;
  onFollowUp: (question: string, conversationId: string | null) => void;
  onAction: (action: GenieActionSuggestion, payload: GenieAnswerShape) => void | Promise<void>;
  onEditQuestion?: (question: string) => void;
  followUpDisabledReason: string | null;
  /** An earlier turn this route never saw land shows its digest (genie-08). */
  presentation: GenieTurnPresentation;
  onToggleCollapse: () => void;
}) {
  const chip = sourceChipFor(turn.response);
  const drawerForSource = chip ? drawerForAsset(chip.label) : null;
  const fullAnswer = (
    <GenieAnswer
      payload={turn.response}
      question={turn.question || undefined}
      onFollowUp={onFollowUp}
      followUpDisabledReason={followUpDisabledReason}
      onAction={(action) => onAction(action, turn.response)}
      onEditQuestion={onEditQuestion}
      onAnnounce={announceGenie}
      withChart
    />
  );
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
            bubble does NOT pass this prop, so its compact form is unchanged.
            An action is bound to THIS turn's answer, never the latest one. */}
        {presentation === 'full' ? (
          fullAnswer
        ) : (
          <GenieCollapsedTurn
            payload={turn.response}
            expanded={presentation === 'expanded'}
            onToggle={onToggleCollapse}
          >
            {fullAnswer}
          </GenieCollapsedTurn>
        )}
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
  sampleQuestions,
  onFollowUp,
  onAction,
  onEditQuestion,
  actionStatus,
  sourceAssets = [],
}: AskGenieAnswerPanelProps) {
  const composerSampleQuestions = sampleQuestions.slice(0, 4);
  const [historyOpen, setHistoryOpen] = useState(false);
  const thread = useSyncExternalStore(subscribeGenieTurns, getGenieTurns, getGenieTurnsServerSnapshot);
  const { inFlight, notes } = useGenieTurn();
  const collapse = useGenieTurnCollapse(thread.map((turn) => turn.response));
  const busyReason = inFlight ? GENIE_BUSY_REASON : null;

  const turnKey = (turn: GenieTurn, index: number) =>
    `${turn.response.message_id ?? turn.response.question_hash ?? 'turn'}-${index}`;

  // The exchange that starts at the end of the thread: the question in
  // flight, else the latest settled turn's question. The key grows when a
  // question is SENT and holds when it lands or is stopped (turns + notes +
  // in flight), so an answer or a Stop never scrolls the reader away.
  const latestIndex = thread.length - 1;
  const latestAnchorRef = useRef<HTMLDivElement>(null);
  const dockRef = useRef<HTMLFormElement>(null);
  useRevealLatestExchange(latestAnchorRef, dockRef, `exchange-${thread.length + notes.length + (inFlight ? 1 : 0)}`);
  // Arriving by a link with a thread already stored opens on its latest turn.
  useRevealLatestOnArrival(latestAnchorRef, dockRef);
  // Focus scrolling stops above the docked composer (WCAG 2.2 SC 2.4.11).
  useComposerScrollClearance(dockRef);

  // Conversational controls (audit 2026-09-21 `genie-03`): ArrowUp in an
  // empty composer recalls the last question; Edit reloads a sent question;
  // Regenerate / Retry re-ask it as a NEW turn through the same `onAsk` path
  // (and the same server-side guards); Stop stops waiting for the turn.
  const pendingQuestion = inFlight?.revealed ? inFlight.question : null;
  const lastQuestion =
    pendingQuestion ??
    [...thread].reverse().find((turn) => turn.question.trim().length > 0)?.question ??
    null;
  const focusComposer = () => {
    queueMicrotask(() => {
      const el = questionRef.current;
      if (!el) return;
      el.focus();
      el.setSelectionRange(el.value.length, el.value.length);
    });
  };
  const editQuestion = (text: string) => {
    onQuestionChange(text);
    questionRef.current?.focus();
  };
  /**
   * Stop waiting for the turn (client-only: Genie may still finish it on the
   * server; its reply is discarded). The question comes back to the composer
   * unless the user typed a different draft there, and focus lands in the
   * composer because the Stop button unmounts under the user.
   */
  const stopTurn = () => {
    if (!inFlight) return;
    const stopped = stopGenieTurn() ?? '';
    const draft = question.trim();
    const restore = stopped !== '' && (draft === '' || draft === stopped.trim());
    if (restore) onQuestionChange(stopped);
    focusComposer();
    announceGenie(
      restore
        ? 'Stopped. The question is back in the composer.'
        : stopped
          ? 'Stopped. Your draft was kept; Edit reloads the stopped question.'
          : 'Stopped.',
    );
  };
  const canAsk = !inFlight && question.trim().length > 0;
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
          disabled={inFlight !== null}
          disabledReason={busyReason}
        />
      </>
    );
  };
  const turnNote = (note: GenieTurnNoteShape, key: string) => (
    <GenieTurnNote
      key={key}
      note={note}
      disabled={inFlight !== null}
      disabledReason={busyReason}
      onEdit={editQuestion}
      onAskAgain={onAsk}
    />
  );

  // Notes sit where they happened: before the turn that settled after them,
  // and the rest after the last settled turn.
  const threadNodes: ReactNode[] = [];
  thread.forEach((turn, index) => {
    notes.forEach((note, n) => {
      if (note.atTurnIndex === index) threadNodes.push(turnNote(note, `note-${n}`));
    });
    threadNodes.push(
      <Fragment key={turnKey(turn, index)}>
        {turn.question && questionBubble(turn, !inFlight && index === latestIndex)}
        <GenieThreadTurn
          turn={turn}
          onFollowUp={onFollowUp}
          onAction={onAction}
          onEditQuestion={onEditQuestion}
          followUpDisabledReason={busyReason}
          presentation={collapse.presentation(turn.response)}
          onToggleCollapse={() => collapse.toggle(turn.response)}
        />
      </Fragment>,
    );
  });
  notes.forEach((note, n) => {
    if (note.atTurnIndex >= thread.length) threadNodes.push(turnNote(note, `note-${n}`));
  });
  const hasThread = thread.length > 0 || notes.length > 0 || inFlight !== null;

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
            disabled={inFlight !== null}
          />
          <Button
            variant="ghost"
            size="sm"
            icon="chat"
            onClick={onNewThread}
            disabled={inFlight !== null}
            title={busyReason ?? undefined}
          >
            New thread
          </Button>
        </div>
      </div>
      <div className="surface__body">
        {!hasThread && (
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
        {hasThread && (
          <div className="genie-thread">
            {/* Fragments, not wrapper divs: the user bubble aligns itself to
                the right edge of `.genie-thread`, so every bubble and card
                must stay a DIRECT flex child of it. */}
            {threadNodes}
            {inFlight && (
              <>
                {/* A resumed turn keeps its question hidden until the first
                    progress poll proves it is still this actor's turn. */}
                {inFlight.revealed ? (
                  <div ref={latestAnchorRef} className="genie__msg genie__msg--user">{inFlight.question}</div>
                ) : (
                  <div ref={latestAnchorRef} className="muted fs-11">{GENIE_RESUMING_LABEL}</div>
                )}
                <div className="surface surface--inset">
                  <div className="surface__body">
                    <GenieProgress progress={inFlight.progress} startedAt={inFlight.startedAt} />
                    <GenieStopRow onStop={stopTurn} />
                  </div>
                </div>
              </>
            )}
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
                disabled={inFlight !== null}
                title={busyReason ?? undefined}
              >
                <Icon name="sparkle" size={11} />
                <span className="filter__text">{q}</span>
              </button>
            ))}
          </div>
        )}
        {busyReason && (
          <p id={BUSY_HINT_ID} className="muted fs-11 mt-3">
            {busyReason}
          </p>
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
          aria-describedby={busyReason ? BUSY_HINT_ID : undefined}
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
            // The submit guard mirrors the button's `disabled` prop, so
            // Enter never starts a second turn while one is in flight.
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
        <Button
          variant="primary"
          icon="send"
          type="submit"
          disabled={!canAsk}
          title={busyReason ?? undefined}
        >
          {inFlight ? 'Asking…' : 'Ask Genie'}
        </Button>
      </form>
    </div>
  );
}
