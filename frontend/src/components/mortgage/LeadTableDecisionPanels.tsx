import { useRef, useState, type RefObject } from 'react';
import type { CallDisposition, SalesTeamMember } from '../../types';
import { Button, SurfaceTitle } from '../Primitives';
import { DISPOSITION_OPTIONS, REJECT_REASONS } from './LeadTable.constants';
import type { RejectReasonCode } from './LeadTable.types';
import type { LeadDispositionPayload } from './useLeadSalesActions';

/**
 * The Lead Queue's inline decision forms. Each one owns its fields (audit
 * runtime-04 slice 2): a keystroke re-renders the form, never the table,
 * and the table mounts a form keyed by its borrower id, so a new row always
 * starts clean. The table keeps only which row's form is open and the
 * submit callback.
 *
 * The reject form has no default reason (audit tables-07, D-approval-flow-d
 * item 13, Lead Queue half): it starts on "Choose a reason", Confirm reject
 * stays aria-disabled until one is picked, and a submit without one moves
 * focus to Reason and sends nothing. A default ('Low intent') used to be
 * recorded for a reviewer who never chose it.
 */
export function LeadRejectPanel({
  borrowerId,
  onCancel,
  onSubmit,
  reasonRef,
}: {
  borrowerId: string;
  onCancel: () => void;
  /** The panel's reason and rationale (untrimmed; the write trims it). */
  onSubmit: (reasonCode: RejectReasonCode, rationale: string) => void;
  /** The Reason field, which takes focus when the panel opens (tables-03). */
  reasonRef?: RefObject<HTMLSelectElement | null>;
}) {
  const [reasonCode, setReasonCode] = useState<RejectReasonCode | ''>('');
  const [rationale, setRationale] = useState('');
  const localReasonRef = useRef<HTMLSelectElement | null>(null);
  const selectRef = reasonRef ?? localReasonRef;
  return (
    <form
      className="decision-panel decision-panel--inline"
      onSubmit={(e) => {
        e.preventDefault();
        if (reasonCode === '') {
          selectRef.current?.focus();
          return;
        }
        onSubmit(reasonCode, rationale);
      }}
    >
      <div>
        <SurfaceTitle level={3}>Reject rationale</SurfaceTitle>
        <div className="muted fs-12">
          Record the committee-visible reason for {borrowerId}.
        </div>
      </div>
      <label className="decision-panel__field">
        <span className="field__label">Reason</span>
        <select
          ref={selectRef}
          value={reasonCode}
          onChange={(e) => setReasonCode(e.target.value as RejectReasonCode | '')}
          data-testid="lead-reject-reason"
        >
          <option value="">Choose a reason</option>
          {REJECT_REASONS.map((reason) => (
            <option key={reason.code} value={reason.code}>{reason.label}</option>
          ))}
        </select>
      </label>
      <label className="decision-panel__field decision-panel__field--wide">
        <span className="field__label">Rationale note</span>
        <textarea
          value={rationale}
          onChange={(e) => setRationale(e.target.value)}
          maxLength={500}
          placeholder="Optional unless reason is Other."
        />
      </label>
      <div className="decision-panel__actions">
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={onCancel}
        >
          Cancel
        </Button>
        <Button
          type="submit"
          variant="primary"
          size="sm"
          icon="cross"
          disabled={reasonCode === 'other_with_text' && rationale.trim().length === 0}
          // aria-disabled, never native `disabled`, until a reason is picked:
          // the submit then moves focus to Reason instead of sending.
          aria-disabled={reasonCode === '' || undefined}
          data-testid="lead-reject-confirm"
        >
          Confirm reject
        </Button>
      </div>
    </form>
  );
}

const CHOOSE_LO = 'Choose the loan officer who worked this lead.';
const CALLBACK_TIME_REQUIRED = 'Callback scheduled dispositions require a callback time.';

export function LeadDispositionPanel({
  borrowerId,
  salesTeam,
  salesBusy,
  initialLo,
  onCancel,
  onSubmit,
}: {
  borrowerId: string;
  salesTeam: SalesTeamMember[];
  salesBusy: boolean;
  /** The loan officer the form opens with (the row's assignee, else the toolbar's). */
  initialLo: string;
  onCancel: () => void;
  onSubmit: (payload: LeadDispositionPayload) => void;
}) {
  const [loEmail, setLoEmail] = useState(initialLo);
  const [outcome, setOutcome] = useState<CallDisposition['outcome']>('called_left_voicemail');
  const [callbackAt, setCallbackAt] = useState('');
  const [notes, setNotes] = useState('');
  // Pre-flight validation, said inside the form (it used to be the table's
  // alert, far from the field). noValidate: this text replaces the
  // browser's own bubble, so the reason is visible and announced.
  const [problem, setProblem] = useState<string | null>(null);

  function submit() {
    if (!loEmail) {
      setProblem(CHOOSE_LO);
      return;
    }
    if (outcome === 'callback_scheduled' && !callbackAt) {
      setProblem(CALLBACK_TIME_REQUIRED);
      return;
    }
    setProblem(null);
    onSubmit({
      lo_email: loEmail,
      outcome,
      callback_at: callbackAt ? new Date(callbackAt).toISOString() : null,
      notes: notes.trim() || null,
    });
  }

  return (
    <form
      className="decision-panel decision-panel--inline"
      noValidate
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
    >
      <div>
        <SurfaceTitle level={3}>Call disposition</SurfaceTitle>
        <div className="muted fs-12">
          Log LO activity for {borrowerId}.
        </div>
        {problem && (
          <div className="text-danger fs-12" role="alert" data-testid="lead-disposition-problem">
            {problem}
          </div>
        )}
      </div>
      <label className="decision-panel__field">
        <span className="field__label">Loan officer</span>
        <select
          value={loEmail}
          onChange={(e) => setLoEmail(e.target.value)}
          required
        >
          <option value="" disabled>Choose LO</option>
          {salesTeam.map((member) => (
            <option key={member.email} value={member.email}>
              {member.display_label} · {member.email}
            </option>
          ))}
        </select>
      </label>
      <label className="decision-panel__field">
        <span className="field__label">Outcome</span>
        <select
          value={outcome}
          onChange={(e) => setOutcome(e.target.value as CallDisposition['outcome'])}
        >
          {DISPOSITION_OPTIONS.map((option) => (
            <option key={option.outcome} value={option.outcome}>{option.label}</option>
          ))}
        </select>
      </label>
      {outcome === 'callback_scheduled' && (
        <label className="decision-panel__field">
          <span className="field__label">Callback time</span>
          <input
            type="datetime-local"
            value={callbackAt}
            onChange={(e) => setCallbackAt(e.target.value)}
            required
          />
        </label>
      )}
      <label className="decision-panel__field decision-panel__field--wide">
        <span className="field__label">Notes</span>
        <textarea
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          maxLength={500}
          placeholder="Optional operational note."
        />
      </label>
      <div className="decision-panel__actions">
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={onCancel}
          disabled={salesBusy}
        >
          Cancel
        </Button>
        <Button
          type="submit"
          variant="primary"
          size="sm"
          icon="bolt"
          disabled={salesBusy || !loEmail}
        >
          {salesBusy ? 'Logging…' : 'Log disposition'}
        </Button>
      </div>
    </form>
  );
}
