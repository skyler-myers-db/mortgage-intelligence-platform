import type { GenieTurnNote as GenieTurnNoteShape } from '../../lib/genieInFlightTurn';
import { Icon } from '../Icon';
import { Chip } from '../Primitives';
import { GenieTurnActions } from './GenieTurnActions';
import './GenieTurnActions.css';

/**
 * A turn that did not end in an answer, in transcript order, on both Genie
 * surfaces (audit 2026-09-21 `genie-03`, `runtime-01`). Moved out of
 * GenieChatBody with the same markup and classes so /ask-genie renders it too.
 *
 *   stopped      the user pressed Stop; the reply is discarded.
 *   interrupted  a reload found a turn it could not resume (still submitting,
 *                already completing, expired, or owned by another tab).
 *
 * Neither is a Genie answer, so neither is written to the shared transcript
 * store (where it would render as an answer, be pinnable, and survive a
 * reload it does not deserve); the in-flight turn store keeps them in memory.
 * The question comes back with "Ask again" (a NEW turn, never a re-send of
 * the old one) and Edit. The server-side caveat is visible text, not a
 * tooltip, and it is not a live region: the surface's one announcer already
 * said it.
 *
 * `.genie__msg--stopped` and `.genie__turn-controls` are the documented
 * extensions of the prototype's `.genie__msg` (design_files/index.html:741-760
 * has no Stop control and no stopped or interrupted bubble), styled in the
 * lazy GenieTurnActions.css. The interrupted variant reuses them unchanged.
 */
export function GenieTurnNote({
  note,
  disabled,
  disabledReason,
  onEdit,
  onAskAgain,
}: {
  note: GenieTurnNoteShape;
  /** A turn is in flight: Ask again is held. */
  disabled: boolean;
  disabledReason: string | null;
  onEdit: (question: string) => void;
  onAskAgain: (question: string) => void;
}) {
  return (
    <>
      {note.question && <div className="genie__msg genie__msg--user">{note.question}</div>}
      {/* "Ask again", not "Regenerate": a stopped or interrupted turn has no answer. */}
      <GenieTurnActions
        placement="question"
        question={note.question}
        onEdit={onEdit}
        onAskAgain={onAskAgain}
        disabled={disabled}
        disabledReason={disabledReason}
      />
      <div className="genie__msg genie__msg--ai genie__msg--stopped">
        <div className="bubble">
          <Chip variant="neutral">{note.kind === 'stopped' ? 'Stopped' : 'Interrupted'}</Chip>
          <span>{note.reason}</span>
        </div>
      </div>
    </>
  );
}

export const GENIE_STOP_LABEL = 'Stop this Genie turn';

/** Shown in place of a resumed turn's question until its first progress poll
 *  returns 200: the persisted question is not rendered before that. */
export const GENIE_RESUMING_LABEL = 'Resuming your last question…';

/** Stop under the progress card: stop waiting, never a server cancel. */
export function GenieStopRow({ onStop }: { onStop: () => void }) {
  return (
    <div className="genie__turn-controls">
      <button
        type="button"
        className="btn btn--ghost btn--sm"
        onClick={onStop}
        aria-label={GENIE_STOP_LABEL}
        title="Stop waiting for this answer. Genie may still finish it on the server; the reply is discarded."
      >
        <Icon name="cross" size={12} />
        Stop
      </button>
    </div>
  );
}
