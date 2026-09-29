import { useEffect, useLayoutEffect, useRef, useState, type MouseEvent as ReactMouseEvent } from 'react';
import { api } from '../../lib/api';
import { pushEscapeLayer } from '../../lib/escapeStack';
import { Button } from '../Primitives';
import { assignmentStatusLabel } from './LeadTable.logic';
import type { AssignmentLifecycleStatus, AssignmentOutcome, LeadSummary } from '../../types';
import './AssignmentLifecycleAdvance.css';

/**
 * S6 lifecycle-advance control (the S2 deferred item): advances an active
 * assignment one legal step through the reviewed lifecycle. The SERVER owns
 * legality — an illegal or stale transition 409s and we surface that state
 * honestly instead of pretending it advanced. The terminal step collects the
 * recorded outcome (success / no response / declined) which the backend
 * writes through the governed feedback-table pattern with a transactional
 * audit row. No outreach is sent from this control.
 *
 * Audit critic-06: the advance names what it does ("Mark contact drafted",
 * not the bare next stage), and the outcome is a two-step choice: pick one,
 * then confirm "Record <outcome> for <id>?". Cancel and Escape close the
 * picker with no write and hand focus back to "Record outcome"; only Record
 * posts (one POST, pessimistic: the stage moves once the server returned).
 */

const NEXT_STATUS: Partial<Record<AssignmentLifecycleStatus, AssignmentLifecycleStatus>> = {
  assigned: 'contact_drafted',
  contact_drafted: 'approved',
  approved: 'actioned',
  actioned: 'outcome_recorded',
};

/** The lifecycle in order, for the stepper. */
const LIFECYCLE: readonly AssignmentLifecycleStatus[] = [
  'assigned', 'contact_drafted', 'approved', 'actioned', 'outcome_recorded',
];

/** Verb-first labels for the one-step advance (critic-06). */
const ADVANCE_LABEL: Partial<Record<AssignmentLifecycleStatus, string>> = {
  contact_drafted: 'Mark contact drafted',
  approved: 'Mark approved',
  actioned: 'Mark actioned',
};

const OUTCOME_OPTIONS: Array<{ value: AssignmentOutcome; label: string }> = [
  { value: 'success', label: 'Success' },
  { value: 'no_response', label: 'No response' },
  { value: 'declined', label: 'Declined' },
];

type WriteResult =
  | { ok: true; status: AssignmentLifecycleStatus }
  | { ok: false; message: string };

/**
 * One lifecycle write, start to settle; resolves to its result and never
 * rejects. Module helpers, so the component has no try/finally (a React
 * Compiler 1.0 bailout, runtime-03).
 */
async function advanceAssignment(assignmentId: string, next: AssignmentLifecycleStatus): Promise<WriteResult> {
  try {
    const result = await api.updateAssignmentStatus(assignmentId, next);
    return { ok: true, status: result.assignment.status };
  } catch (err) {
    // 409 = the server refused an illegal/stale transition; keep the row
    // honest and let the operator refresh rather than faking progress.
    return { ok: false, message: err instanceof Error ? err.message : 'Transition refused' };
  }
}

async function recordAssignmentOutcome(assignmentId: string, outcome: AssignmentOutcome): Promise<WriteResult> {
  try {
    const result = await api.recordAssignmentOutcome(assignmentId, outcome);
    return { ok: true, status: result.assignment.status };
  } catch (err) {
    return { ok: false, message: err instanceof Error ? err.message : 'Outcome refused' };
  }
}

/** The outcome picker: closed, choosing an outcome, or confirming one. */
type Picker = { step: 'closed' } | { step: 'choosing' } | { step: 'confirming'; outcome: AssignmentOutcome };

const CLOSED: Picker = { step: 'closed' };

function outcomeLabel(outcome: AssignmentOutcome): string {
  return OUTCOME_OPTIONS.find((option) => option.value === outcome)?.label ?? outcome;
}

export function AssignmentLifecycleAdvance({
  assignmentId,
  status,
  borrowerId,
  onAdvanced,
}: {
  assignmentId: string;
  status: AssignmentLifecycleStatus;
  borrowerId: string;
  onAdvanced: (borrowerId: string, update: Partial<LeadSummary>) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [picker, setPicker] = useState<Picker>(CLOSED);
  const openerRef = useRef<HTMLButtonElement | null>(null);
  const pickerRef = useRef<HTMLDivElement | null>(null);
  // Where focus goes once the picker's next step has rendered: the step that
  // held it unmounted (focus would drop to <body>).
  const focusNextRef = useRef<'opener' | 'picker' | null>(null);
  const pickerOpen = picker.step !== 'closed';

  // Escape closes the whole picker with no write (lib/escapeStack: the
  // topmost layer, so it never also closes a review or drawer below it).
  useEffect(() => {
    if (!pickerOpen) return undefined;
    return pushEscapeLayer(() => {
      focusNextRef.current = 'opener';
      setPicker(CLOSED);
    });
  }, [pickerOpen]);

  useLayoutEffect(() => {
    const target = focusNextRef.current;
    if (target === null) return;
    focusNextRef.current = null;
    if (target === 'opener') openerRef.current?.focus();
    else pickerRef.current?.querySelector<HTMLButtonElement>('button:not(:disabled)')?.focus();
  }, [picker]);

  const next = NEXT_STATUS[status];
  if (!next) return null;
  const stop = (e: ReactMouseEvent) => e.stopPropagation();

  const moveTo = (nextPicker: Picker, focus: 'opener' | 'picker') => {
    focusNextRef.current = focus;
    setPicker(nextPicker);
  };

  const settle = (result: WriteResult) => {
    setBusy(false);
    if (result.ok) {
      setPicker(CLOSED);
      onAdvanced(borrowerId, { assignment_status: result.status });
    } else {
      setError(result.message);
    }
  };

  const advance = () => {
    setBusy(true);
    setError(null);
    void advanceAssignment(assignmentId, next).then(settle);
  };

  const record = (outcome: AssignmentOutcome) => {
    setBusy(true);
    setError(null);
    void recordAssignmentOutcome(assignmentId, outcome).then(settle);
  };

  return (
    <div className="chip-stack" onClick={stop} data-testid={`lifecycle-advance-${borrowerId}`}>
      {next !== 'outcome_recorded' ? (
        <Button
          variant="ghost"
          size="sm"
          icon="chevright"
          disabled={busy}
          onClick={advance}
          aria-label={`Advance assignment for ${borrowerId} to ${assignmentStatusLabel(next)}`}
        >
          {ADVANCE_LABEL[next] ?? assignmentStatusLabel(next)}
        </Button>
      ) : picker.step === 'closed' ? (
        <Button
          ref={openerRef}
          variant="ghost"
          size="sm"
          icon="chevright"
          disabled={busy}
          onClick={() => moveTo({ step: 'choosing' }, 'picker')}
          aria-label={`Record outcome for ${borrowerId}`}
        >
          Record outcome
        </Button>
      ) : picker.step === 'choosing' ? (
        <div ref={pickerRef} className="chip-row" role="group" aria-label={`Outcome for ${borrowerId}`}>
          {OUTCOME_OPTIONS.map((option) => (
            <Button
              key={option.value}
              variant="ghost"
              size="sm"
              disabled={busy}
              onClick={() => moveTo({ step: 'confirming', outcome: option.value }, 'picker')}
            >
              {option.label}
            </Button>
          ))}
          <Button variant="ghost" size="sm" disabled={busy} onClick={() => moveTo(CLOSED, 'opener')}>
            Cancel
          </Button>
        </div>
      ) : (
        <div
          ref={pickerRef}
          className="chip-row"
          role="group"
          aria-label={`Confirm the outcome for ${borrowerId}`}
          data-testid={`lifecycle-outcome-confirm-${borrowerId}`}
        >
          <span className="fs-12">Record {outcomeLabel(picker.outcome)} for {borrowerId}?</span>
          <Button variant="primary" size="sm" disabled={busy} onClick={() => record(picker.outcome)}>
            {busy ? 'Recording…' : 'Record'}
          </Button>
          <Button variant="ghost" size="sm" disabled={busy} onClick={() => moveTo({ step: 'choosing' }, 'picker')}>
            Back
          </Button>
        </div>
      )}
      {error && (
        <span className="muted fs-11" role="alert">
          {error}
        </span>
      )}
    </div>
  );
}

/**
 * The assignment's lifecycle as a compact stepper (audit critic-06): the
 * five reviewed stages, the current one marked aria-current="step". Plain
 * list items, never `.chip__label`: the stage chip beside it stays the one
 * element specs and assistive tech read the stage from.
 */
export function AssignmentLifecycleSteps({ status }: { status: AssignmentLifecycleStatus }) {
  const current = LIFECYCLE.indexOf(status);
  return (
    <ol className="lifecycle-steps" aria-label="Assignment lifecycle" data-testid="assignment-lifecycle-steps">
      {LIFECYCLE.map((step, index) => (
        <li
          key={step}
          className={index < current
            ? 'lifecycle-steps__step lifecycle-steps__step--done'
            : index === current ? 'lifecycle-steps__step lifecycle-steps__step--current' : 'lifecycle-steps__step'}
          aria-current={index === current ? 'step' : undefined}
        >
          {assignmentStatusLabel(step)}
        </li>
      ))}
    </ol>
  );
}
