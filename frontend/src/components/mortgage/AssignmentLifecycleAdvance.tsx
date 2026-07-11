import { useState, type MouseEvent as ReactMouseEvent } from 'react';
import { api } from '../../lib/api';
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
 */

const NEXT_STATUS: Partial<Record<AssignmentLifecycleStatus, AssignmentLifecycleStatus>> = {
  assigned: 'contact_drafted',
  contact_drafted: 'approved',
  approved: 'actioned',
  actioned: 'outcome_recorded',
};

const OUTCOME_OPTIONS: Array<{ value: AssignmentOutcome; label: string }> = [
  { value: 'success', label: 'Success' },
  { value: 'no_response', label: 'No response' },
  { value: 'declined', label: 'Declined' },
];

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
  const [pickingOutcome, setPickingOutcome] = useState(false);
  const next = NEXT_STATUS[status];
  if (!next) return null;
  const stop = (e: ReactMouseEvent) => e.stopPropagation();

  const advance = async () => {
    setBusy(true);
    setError(null);
    try {
      const result = await api.updateAssignmentStatus(assignmentId, next);
      onAdvanced(borrowerId, { assignment_status: result.assignment.status });
    } catch (err) {
      // 409 = the server refused an illegal/stale transition; keep the row
      // honest and let the operator refresh rather than faking progress.
      setError(err instanceof Error ? err.message : 'Transition refused');
    } finally {
      setBusy(false);
    }
  };

  const recordOutcome = async (outcome: AssignmentOutcome) => {
    setBusy(true);
    setError(null);
    try {
      const result = await api.recordAssignmentOutcome(assignmentId, outcome);
      onAdvanced(borrowerId, { assignment_status: result.assignment.status });
      setPickingOutcome(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Outcome refused');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="chip-stack" onClick={stop} data-testid={`lifecycle-advance-${borrowerId}`}>
      {next !== 'outcome_recorded' ? (
        <Button
          variant="ghost"
          size="sm"
          icon="chevright"
          disabled={busy}
          onClick={() => void advance()}
          aria-label={`Advance assignment for ${borrowerId} to ${assignmentStatusLabel(next)}`}
        >
          {assignmentStatusLabel(next)}
        </Button>
      ) : !pickingOutcome ? (
        <Button
          variant="ghost"
          size="sm"
          icon="chevright"
          disabled={busy}
          onClick={() => setPickingOutcome(true)}
          aria-label={`Record outcome for ${borrowerId}`}
        >
          Record outcome
        </Button>
      ) : (
        <div className="chip-row" role="group" aria-label={`Outcome for ${borrowerId}`}>
          {OUTCOME_OPTIONS.map((option) => (
            <Button
              key={option.value}
              variant="ghost"
              size="sm"
              disabled={busy}
              onClick={() => void recordOutcome(option.value)}
            >
              {option.label}
            </Button>
          ))}
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
