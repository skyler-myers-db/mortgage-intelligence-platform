import { Fragment, type ReactNode, type RefObject } from 'react';
import { drawerForAsset } from '../../lib/drawerSources';
import type { GenieChatMessage } from '../../lib/genieConversationStore';
import type { GenieInFlightTurn, GenieTurnNote as GenieTurnNoteShape } from '../../lib/genieInFlightTurn';
import type { GenieActionSuggestion, GenieAnswer as GenieAnswerShape } from '../../types';
import { Icon } from '../Icon';
import { Chip, EvidenceChip } from '../Primitives';
import { GenieAnswer } from './GenieAnswer';
import {
  shouldRenderGenieSourceAssets,
  sourceAssetsFor,
  warningLabelForSource,
} from './GenieChat.helpers';
import { GenieCollapsedTurn } from './GenieCollapsedTurn';
import { GenieProgress } from './GenieProgress';
import { GenieTurnActions } from './GenieTurnActions';
import { GENIE_RESUMING_LABEL, GenieStopRow, GenieTurnNote } from './GenieTurnNote';
import { useGenieTurnCollapse } from './useGenieTurnCollapse';
import type { GenieMessageEntrance } from './useGenieMessageEntrance';
import './GenieTurnActions.css';
import './GenieAnswerReading.css';

/**
 * Transcript body of the floating Genie panel: settled bubbles, the pending
 * question, the progress card, and the empty-state starters. Moved verbatim
 * out of `GenieChat.tsx` (file-size gate) so the panel's conversational
 * controls (audit 2026-09-21 `genie-03`) have room to land; the bubble markup
 * and class names are unchanged. The controls add per-bubble actions (Edit
 * under a question; Retry under a failed answer, Regenerate under an answered
 * one), Stop under the progress card, and the stopped / interrupted notes
 * (GenieTurnNote).
 *
 * The pending turn and the notes come from the in-flight turn store
 * (`runtime-01`), whichever surface started the turn: a turn asked on
 * `/ask-genie` shows here too. A note is placed at the turn index it happened,
 * so it stays in order when later turns land under it.
 */

export interface GenieChatBodyProps {
  open: boolean;
  bodyRef: RefObject<HTMLDivElement | null>;
  lastAnswerRef: RefObject<HTMLDivElement | null>;
  messages: GenieChatMessage[];
  notes: readonly GenieTurnNoteShape[];
  /** The tab's in-flight Genie turn (the only kind of turn Stop applies to). */
  inFlight: GenieInFlightTurn | null;
  /** An ask OR a governed action is in flight. */
  typing: boolean;
  busyReason: string | null;
  /** Starter prompts for the empty state. */
  starters: string[];
  onAsk: (question: string, followUpConversationId?: string | null) => void;
  /** Returns the action's promise: GenieActions awaits it for its busy state. */
  onAction: (action: GenieActionSuggestion, payload: GenieAnswerShape) => void | Promise<void>;
  /** Load a question into the composer (Edit). Never sends. */
  onEdit: (question: string) => void;
  onStop: () => void;
  /** Speak a copy confirmation through the panel's one announcer. */
  onAnnounce: (text: string) => void;
  /** An answer landed while the reader was reading further up (genie-08). */
  newAnswer: boolean;
  /** Bring that answer's start into view and focus it. */
  onJumpToNewAnswer: () => void;
  /** Which new bubbles play their one-shot entrance (motion-v2). */
  entrance: GenieMessageEntrance;
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
  notes,
  inFlight,
  typing,
  busyReason,
  starters,
  onAsk,
  onAction,
  onEdit,
  onStop,
  onAnnounce,
  newAnswer,
  onJumpToNewAnswer,
  entrance,
}: GenieChatBodyProps) {
  const lastAnswerIndex = msgs.reduce((last, m, i) => (m.who === 'ai' ? i : last), -1);
  // Earlier turns this panel never saw land render as their digest (genie-08).
  const collapse = useGenieTurnCollapse(msgs.flatMap((m) => (m.who === 'ai' ? [m.payload] : [])));
  const reask = (question: string) => onAsk(question, undefined);
  const turnNote = (note: GenieTurnNoteShape, key: string) => (
    <GenieTurnNote
      key={key}
      note={note}
      disabled={typing}
      disabledReason={busyReason}
      onEdit={onEdit}
      onAskAgain={reask}
      entering={entrance.entering(note)}
      onEntered={entrance.onEntered(note)}
    />
  );

  const transcript: ReactNode[] = [];
  // Settled turns seen so far: every turn ends in exactly one answer.
  let turnIndex = 0;
  msgs.forEach((m, i) => {
    // A turn starts at its question, or at an answer with no question (a
    // governed action result); notes are placed at turn starts.
    const startsTurn = m.who === 'user' || i === 0 || msgs[i - 1].who !== 'user';
    if (startsTurn) {
      notes.forEach((note, n) => {
        if (note.atTurnIndex === turnIndex) transcript.push(turnNote(note, `note-${n}`));
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
    const presentation = collapse.presentation(m.payload);
    const fullAnswer = (
      <GenieAnswer
        payload={m.payload}
        question={question}
        onFollowUp={(q, followUpConversationId) => onAsk(q, followUpConversationId)}
        followUpDisabledReason={busyReason}
        onAction={(action) => onAction(action, m.payload)}
        onEditQuestion={onEdit}
        onAnnounce={onAnnounce}
        dense
      />
    );
    transcript.push(
      <div
        key={i}
        ref={i === lastAnswerIndex ? lastAnswerRef : undefined}
        className={`genie__msg genie__msg--ai${entrance.entering(m.payload) ? ' genie__msg--entering' : ''}`}
        onAnimationEnd={entrance.onEntered(m.payload)}
        // "New answer" moves focus here (genie-08); never a Tab stop.
        tabIndex={i === lastAnswerIndex ? -1 : undefined}
      >
        <div className="bubble">
          {presentation === 'full' ? (
            fullAnswer
          ) : (
            <GenieCollapsedTurn
              payload={m.payload}
              expanded={presentation === 'expanded'}
              onToggle={() => collapse.toggle(m.payload)}
            >
              {fullAnswer}
            </GenieCollapsedTurn>
          )}
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
  notes.forEach((note, n) => {
    if (note.atTurnIndex >= turnIndex) transcript.push(turnNote(note, `note-${n}`));
  });

  return (
    <div className="genie__body" ref={bodyRef}>
      {transcript}
      {inFlight?.revealed && (
        <div
          className={`genie__msg genie__msg--user${entrance.userEntering(inFlight.generation) ? ' genie__msg--entering' : ''}`}
          onAnimationEnd={entrance.onEntered(inFlight.generation)}
        >
          {inFlight.question}
        </div>
      )}
      {typing && (
        <div className="genie__msg genie__msg--ai">
          <div className="bubble">
            {/* A resumed turn keeps its question hidden until the first
                progress poll proves it is still this actor's turn. */}
            {inFlight && !inFlight.revealed && <p className="muted fs-11">{GENIE_RESUMING_LABEL}</p>}
            <GenieProgress
              dense
              progress={inFlight?.progress ?? null}
              startedAt={inFlight?.startedAt ?? null}
              paused={!open}
            />
            {/* Stop (genie-03, client-only): abandons the client turn and
                gives the question back. Governed actions are not stoppable. */}
            {inFlight && <GenieStopRow onStop={onStop} />}
          </div>
        </div>
      )}
      {msgs.length === 0 && notes.length === 0 && !typing && (
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
      {/* Reading an earlier turn when an answer lands (genie-08): nothing
          scrolls; this offers the jump instead. `.genie__jump` is a
          documented BEM extension of `.genie`, sticky at the bottom of the
          transcript (GenieAnswerReading.css). */}
      {newAnswer && (
        <button type="button" className="genie__jump" onClick={onJumpToNewAnswer}>
          New answer
          <Icon name="down" size={12} />
        </button>
      )}
    </div>
  );
}
