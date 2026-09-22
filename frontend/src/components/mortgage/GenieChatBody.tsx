import { Fragment, type ReactNode, type RefObject } from 'react';
import type { GenieLiveProgress } from '../../lib/api';
import { drawerForAsset } from '../../lib/drawerSources';
import type { GenieChatMessage } from '../../lib/genieConversationStore';
import type { GenieActionSuggestion, GenieAnswer as GenieAnswerShape } from '../../types';
import { Icon } from '../Icon';
import { Chip, EvidenceChip } from '../Primitives';
import { GenieAnswer } from './GenieAnswer';
import {
  shouldRenderGenieSourceAssets,
  sourceAssetsFor,
  warningLabelForSource,
} from './GenieChat.helpers';
import { GenieProgress } from './GenieProgress';
import { GenieTurnActions } from './GenieTurnActions';

/**
 * Transcript body of the floating Genie panel: settled bubbles, the pending
 * question, the progress card, and the empty-state starters. Moved verbatim
 * out of `GenieChat.tsx` (file-size gate) so the panel's conversational
 * controls (audit 2026-09-21 `genie-03`) have room to land; the bubble markup
 * and class names are unchanged. The controls add per-bubble actions (Edit
 * under a question; Retry under a failed answer, Regenerate under an answered
 * one), Stop under the progress card, and the stopped-turn note.
 *
 * A STOPPED turn is panel-local: the user asked, pressed Stop, and the reply
 * is discarded. It is not a Genie answer, so it is never written to the
 * shared transcript store (where it would render as an answer on
 * `/ask-genie`, be pinnable, and survive a reload it does not deserve). It is
 * remembered here at the turn index it happened, so it stays in order when
 * later turns land under it.
 */

export interface StoppedTurn {
  /** Number of settled turns that existed when the turn was stopped. */
  atTurnIndex: number;
  question: string;
}

export interface GenieChatBodyProps {
  open: boolean;
  bodyRef: RefObject<HTMLDivElement | null>;
  lastAnswerRef: RefObject<HTMLDivElement | null>;
  messages: GenieChatMessage[];
  stoppedTurns: readonly StoppedTurn[];
  pendingQuestion: string | null;
  /** A live ask is in flight (the only kind of turn Stop applies to). */
  asking: boolean;
  /** An ask OR a governed action is in flight. */
  typing: boolean;
  liveProgress: GenieLiveProgress | null;
  askStartedAt: number | null;
  busyReason: string | null;
  /** Starter prompts for the empty state. */
  starters: string[];
  onAsk: (question: string, followUpConversationId?: string | null) => void;
  /** Returns the action's promise: GenieActions awaits it for its busy state. */
  onAction: (action: GenieActionSuggestion, payload: GenieAnswerShape) => void | Promise<void>;
  /** Load a question into the composer (Edit). Never sends. */
  onEdit: (question: string) => void;
  onStop: () => void;
}

const STOPPED_NOTE_TITLE =
  'Stopped in this browser only. Genie may still finish this turn on the server; its reply is discarded and never shown.';

function StoppedTurnNote({
  note,
  typing,
  busyReason,
  onEdit,
  onAsk,
}: {
  note: StoppedTurn;
  typing: boolean;
  busyReason: string | null;
  onEdit: (question: string) => void;
  onAsk: (question: string) => void;
}) {
  return (
    <>
      <div className="genie__msg genie__msg--user">{note.question}</div>
      <GenieTurnActions
        placement="question"
        question={note.question}
        onEdit={onEdit}
        onRegenerate={onAsk}
        disabled={typing}
        disabledReason={busyReason}
      />
      {/* Not a live region: the panel's one persistent announcer (a11y-06)
          already said "Stopped" when it happened. */}
      <div className="genie__msg genie__msg--ai genie__msg--stopped" title={STOPPED_NOTE_TITLE}>
        <div className="bubble">
          <Chip variant="neutral">Stopped</Chip>
          <span>This answer was discarded before it arrived.</span>
        </div>
      </div>
    </>
  );
}

/** Retry for a failed turn, Regenerate for an answered one; nothing else. */
function answerReask(payload: GenieAnswerShape): 'retry' | 'regenerate' | null {
  if (payload.source === 'degraded') return 'retry';
  // Refusals, data gaps and out-of-footprint answers would only repeat
  // themselves; the question's Edit is the way forward there.
  return warningLabelForSource(payload.source) === null ? 'regenerate' : null;
}

export function GenieChatBody({
  open,
  bodyRef,
  lastAnswerRef,
  messages: msgs,
  stoppedTurns,
  pendingQuestion,
  asking,
  typing,
  liveProgress,
  askStartedAt,
  busyReason,
  starters,
  onAsk,
  onAction,
  onEdit,
  onStop,
}: GenieChatBodyProps) {
  const lastAnswerIndex = msgs.reduce((last, m, i) => (m.who === 'ai' ? i : last), -1);
  const reask = (question: string) => onAsk(question, undefined);
  const stoppedNote = (note: StoppedTurn, key: string) => (
    <StoppedTurnNote
      key={key}
      note={note}
      typing={typing}
      busyReason={busyReason}
      onEdit={onEdit}
      onAsk={reask}
    />
  );

  const transcript: ReactNode[] = [];
  // Settled turns seen so far: every turn ends in exactly one answer.
  let turnIndex = 0;
  msgs.forEach((m, i) => {
    // A turn starts at its question, or at an answer with no question (a
    // governed action result); stopped notes are placed at turn starts.
    const startsTurn = m.who === 'user' || i === 0 || msgs[i - 1].who !== 'user';
    if (startsTurn) {
      stoppedTurns.forEach((note, n) => {
        if (note.atTurnIndex === turnIndex) transcript.push(stoppedNote(note, `stopped-${n}`));
      });
    }
    if (m.who === 'user') {
      transcript.push(
        <Fragment key={i}>
          <div className="genie__msg genie__msg--user">{m.text}</div>
          <GenieTurnActions placement="question" question={m.text} onEdit={onEdit} />
        </Fragment>,
      );
      return;
    }
    turnIndex += 1;
    const prev = msgs[i - 1];
    const question = prev && prev.who === 'user' ? prev.text : undefined;
    const reaskKind = question ? answerReask(m.payload) : null;
    transcript.push(
      <div
        key={i}
        ref={i === lastAnswerIndex ? lastAnswerRef : undefined}
        className="genie__msg genie__msg--ai"
      >
        <div className="bubble">
          <GenieAnswer
            payload={m.payload}
            question={question}
            onFollowUp={(q, followUpConversationId) => onAsk(q, followUpConversationId)}
            followUpDisabledReason={busyReason}
            announce={false}
            onAction={(action) => onAction(action, m.payload)}
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
        {/* Retry on a failed turn, Regenerate on an answered one (genie-03).
            Both re-ask as a NEW turn; the answer above stays. */}
        {question && reaskKind && (
          <GenieTurnActions
            placement="answer"
            question={question}
            onRetry={reaskKind === 'retry' ? reask : undefined}
            onRegenerate={reaskKind === 'regenerate' ? reask : undefined}
            disabled={typing}
            disabledReason={busyReason}
          />
        )}
      </div>,
    );
  });
  // Notes after the last settled turn (and any whose index outlived an
  // evicted turn) close the transcript.
  stoppedTurns.forEach((note, n) => {
    if (note.atTurnIndex >= turnIndex) transcript.push(stoppedNote(note, `stopped-${n}`));
  });

  return (
    <div className="genie__body" ref={bodyRef}>
      {transcript}
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
              paused={!open}
            />
            {/* Stop (genie-03, client-only): abandons the client turn and
                gives the question back. Governed actions are not stoppable. */}
            {asking && (
              <div className="genie__turn-controls">
                <button
                  type="button"
                  className="btn btn--ghost btn--sm"
                  onClick={onStop}
                  aria-label="Stop this Genie turn"
                  title="Stop waiting for this answer. Genie may still finish it on the server; the reply is discarded."
                >
                  <Icon name="cross" size={12} />
                  Stop
                </button>
              </div>
            )}
          </div>
        </div>
      )}
      {msgs.length === 0 && stoppedTurns.length === 0 && !typing && (
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
          {starters.map((s) => (
            <button
              key={s}
              className="filter genie-chat__sample"
              onClick={() => onAsk(s, undefined)}
              type="button"
            >
              <Icon name="sparkle" size={11} /> {s}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
