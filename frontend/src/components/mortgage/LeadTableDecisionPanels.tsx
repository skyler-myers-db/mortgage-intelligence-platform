import type { CallDisposition, SalesTeamMember } from '../../types';
import { Button } from '../Primitives';
import { DISPOSITION_OPTIONS, REJECT_REASONS } from './LeadTable.constants';
import type { RejectReasonCode } from './LeadTable.types';

export function LeadRejectPanel({
  borrowerId,
  reasonCode,
  rationale,
  onReasonChange,
  onRationaleChange,
  onCancel,
  onSubmit,
}: {
  borrowerId: string;
  reasonCode: RejectReasonCode;
  rationale: string;
  onReasonChange: (reason: RejectReasonCode) => void;
  onRationaleChange: (value: string) => void;
  onCancel: () => void;
  onSubmit: () => void;
}) {
  return (
    <form
      className="decision-panel decision-panel--inline"
      onSubmit={(e) => {
        e.preventDefault();
        onSubmit();
      }}
    >
      <div>
        <div className="h-4">Reject rationale</div>
        <div className="muted fs-12">
          Record the committee-visible reason for {borrowerId}.
        </div>
      </div>
      <label className="decision-panel__field">
        <span className="field__label">Reason</span>
        <select
          value={reasonCode}
          onChange={(e) => onReasonChange(e.target.value as RejectReasonCode)}
        >
          {REJECT_REASONS.map((reason) => (
            <option key={reason.code} value={reason.code}>{reason.label}</option>
          ))}
        </select>
      </label>
      <label className="decision-panel__field decision-panel__field--wide">
        <span className="field__label">Rationale note</span>
        <textarea
          value={rationale}
          onChange={(e) => onRationaleChange(e.target.value)}
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
        >
          Confirm reject
        </Button>
      </div>
    </form>
  );
}

export function LeadDispositionPanel({
  borrowerId,
  salesTeam,
  salesBusy,
  loEmail,
  outcome,
  callbackAt,
  notes,
  onLoChange,
  onOutcomeChange,
  onCallbackAtChange,
  onNotesChange,
  onCancel,
  onSubmit,
}: {
  borrowerId: string;
  salesTeam: SalesTeamMember[];
  salesBusy: boolean;
  loEmail: string;
  outcome: CallDisposition['outcome'];
  callbackAt: string;
  notes: string;
  onLoChange: (value: string) => void;
  onOutcomeChange: (value: CallDisposition['outcome']) => void;
  onCallbackAtChange: (value: string) => void;
  onNotesChange: (value: string) => void;
  onCancel: () => void;
  onSubmit: () => void;
}) {
  return (
    <form
      className="decision-panel decision-panel--inline"
      onSubmit={(e) => {
        e.preventDefault();
        onSubmit();
      }}
    >
      <div>
        <div className="h-4">Call disposition</div>
        <div className="muted fs-12">
          Log LO activity for {borrowerId}.
        </div>
      </div>
      <label className="decision-panel__field">
        <span className="field__label">Loan officer</span>
        <select
          value={loEmail}
          onChange={(e) => onLoChange(e.target.value)}
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
          onChange={(e) => onOutcomeChange(e.target.value as CallDisposition['outcome'])}
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
            onChange={(e) => onCallbackAtChange(e.target.value)}
            required
          />
        </label>
      )}
      <label className="decision-panel__field decision-panel__field--wide">
        <span className="field__label">Notes</span>
        <textarea
          value={notes}
          onChange={(e) => onNotesChange(e.target.value)}
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
