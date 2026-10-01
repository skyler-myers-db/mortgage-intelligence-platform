import { useEffect, useLayoutEffect, useRef, useState, type MouseEvent as ReactMouseEvent } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { pushEscapeLayer } from '../../lib/escapeStack';
import { useAdvanceAssignment, useRecordAssignmentOutcome } from '../../lib/mutations/sales';
import { Button } from '../Primitives';
import { assignmentStatusLabel } from './LeadTable.logic';
import type { AssignmentLifecycleStatus, AssignmentOutcome, LeadSummary } from '../../types';

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
 *
 * Audit states-09: both writes run on keyed mutations (lib/mutations/sales,
 * ['mip','sales','lifecycle'|'outcome']), so the Lead Queue's own-write
 * store counts them and an own advance never raises "Queue updated".
 */

const NEXT_STATUS: Partial<Record<AssignmentLifecycleStatus, AssignmentLifecycleStatus>> = {
  assigned: 'contact_drafted',
  contact_drafted: 'approved',
  approved: 'actioned',
  actioned: 'outcome_recorded',
};

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
 * A refused write's result. 409 = the server refused an illegal or stale
 * transition: keep the row honest and show the server's message rather than
 * faking progress. Settled with .then(ok, err), so the component has no
 * try/finally (a React Compiler 1.0 bailout, runtime-03).
 */
function refused(fallback: string): (err: unknown) => WriteResult {
  return (err) => ({ ok: false, message: err instanceof Error ? err.message : fallback });
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
  // Budget trade (audit runtime-03, the wave-4b lane's budget cuts): its
  // try/finally bailouts are fixed (the module helpers above), so it
  // compiles cleanly without this line, which measured +0.35 KiB br on the
  // LeadTable chunk for a control rendered once per expanded row. Delete it
  // when the chunk has the room.
  'use no memo';

  const queryClient = useQueryClient();
  const advanceMutation = useAdvanceAssignment(queryClient);
  const outcomeMutation = useRecordAssignmentOutcome(queryClient);
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
  // While Record is on the wire the key is taken and does nothing: closing
  // then hid the pending "Recording…" while the POST still settled.
  useEffect(() => {
    if (!pickerOpen) return undefined;
    return pushEscapeLayer(() => {
      if (busy) return;
      focusNextRef.current = 'opener';
      setPicker(CLOSED);
    });
  }, [pickerOpen, busy]);

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
    void advanceMutation.mutateAsync({ assignmentId, status: next }).then(
      (result): WriteResult => ({ ok: true, status: result.assignment.status }),
      refused('Transition refused'),
    ).then(settle);
  };

  const record = (outcome: AssignmentOutcome) => {
    setBusy(true);
    setError(null);
    void outcomeMutation.mutateAsync({ assignmentId, outcome }).then(
      (result): WriteResult => ({ ok: true, status: result.assignment.status }),
      refused('Outcome refused'),
    ).then(settle);
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
