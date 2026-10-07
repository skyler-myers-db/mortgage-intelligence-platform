import { useId, useLayoutEffect, useRef, useState, type RefObject } from 'react';
import type { LeadSummary } from '../../types';
import { formatCount } from '../../lib/formatters';
import { Button, Chip } from '../Primitives';
import { offerCounts, stateCounts } from './LeadBulkApproveReview.counts';
import { CONSENT_REJECT_CODES, REJECT_REASONS } from './LeadTable.constants';
import type { RejectReasonCode } from './LeadTable.types';

/**
 * The bulk reject gate (audit tables-07, D-approval-flow-d;
 * deviation:bulk-reject-gate): one reason and one required shared note for
 * every selected eligible row, then one OUTREACH_REJECT row per borrower
 * under one bulk id. It rides the lazy bulk chunk (re-exported from
 * LeadBulkApproveReview.tsx), so it adds nothing to the LeadTable chunk.
 *
 *   - The reason starts on "Choose a reason": there is NO default, so a run
 *     never records a reason nobody picked. Do Not Call and Opt-out are not
 *     offered: consent is recorded per borrower (the server refuses them
 *     with a bulk_id too).
 *   - The shared note is required (the server refuses a bulk_id without it).
 *   - Counts by offer and by state say what the run would reject.
 *   - Confirm stays aria-disabled until both are present; activating it
 *     then moves focus to the first missing field and sends nothing.
 *   - The gate takes focus on Reason when it mounts (a layout effect, W5c
 *     integrator C4): the opener's requestAnimationFrame focus raced the
 *     lazy gate's first paint and could land on nothing.
 *
 * Prototype: design_files/Module 0 Prototype.html:1501-1502 (the toolbar's
 * Reject beside Approve) and :2187 (batch reject); the prototype shows no
 * reason form, which this finding requires.
 */
export interface LeadBulkRejectGateProps {
  /** Selected eligible rows the run would reject, in table order. */
  leads: readonly LeadSummary[];
  /** Start the run; resolves true once it settled (the gate then closes). */
  onReject: (reasonCode: RejectReasonCode, note: string) => Promise<boolean>;
  /** The Reason field (Shift+R and the Cmd-K verb land there). */
  reasonRef?: RefObject<HTMLSelectElement | null>;
  /** A run is on the wire: Confirm is disabled until it settles. */
  running: boolean;
}

const BULK_REASONS = REJECT_REASONS.filter((reason) => !CONSENT_REJECT_CODES.includes(reason.code));

export function LeadBulkRejectGate({ leads, onReject, reasonRef, running }: LeadBulkRejectGateProps) {
  const statusId = useId();
  const localReasonRef = useRef<HTMLSelectElement | null>(null);
  const selectRef = reasonRef ?? localReasonRef;
  const noteRef = useRef<HTMLTextAreaElement | null>(null);
  const [reasonCode, setReasonCode] = useState<RejectReasonCode | ''>('');
  const [note, setNote] = useState('');
  const reasonMissing = reasonCode === '';
  const noteMissing = note.trim().length === 0;
  const armingCopy = reasonMissing && noteMissing
    ? 'Choose a reason and write a shared note before rejecting.'
    : reasonMissing
      ? 'Choose a reason before rejecting.'
      : noteMissing ? 'Write a shared note before rejecting.' : null;
  const count = leads.length;

  // Opened = mounted (LeadTableBulkActions renders the gate only while open).
  useLayoutEffect(() => {
    selectRef.current?.focus();
  }, [selectRef]);

  function submit() {
    if (running) return;
    if (reasonCode === '') {
      selectRef.current?.focus();
      return;
    }
    if (noteMissing) {
      noteRef.current?.focus();
      return;
    }
    void onReject(reasonCode, note);
  }

  return (
    <form
      className="bulk-actions__review bulk-actions__reject"
      aria-label="Bulk rejection"
      data-testid="lead-bulk-reject-gate"
      noValidate
      onSubmit={(event) => {
        event.preventDefault();
        submit();
      }}
    >
      <div className="bulk-actions__offers" data-testid="lead-bulk-reject-counts">
        <span className="field__label">By offer</span>
        {offerCounts(leads).map(({ label, count: rows }) => (
          <Chip key={`offer-${label}`} variant="neutral">
            {label} <span className="mono num">{formatCount(rows)}</span>
          </Chip>
        ))}
        <span className="field__label">By state</span>
        {stateCounts(leads).map(({ label, count: rows }) => (
          <Chip key={`state-${label}`} variant="neutral">
            {label} <span className="mono num">{formatCount(rows)}</span>
          </Chip>
        ))}
      </div>
      <div className="bulk-actions__reject-fields">
        <label className="decision-panel__field">
          <span className="field__label">Reason</span>
          <select
            ref={selectRef}
            value={reasonCode}
            onChange={(event) => setReasonCode(event.target.value as RejectReasonCode | '')}
            data-testid="lead-bulk-reject-reason"
          >
            <option value="">Choose a reason</option>
            {BULK_REASONS.map((reason) => (
              <option key={reason.code} value={reason.code}>{reason.label}</option>
            ))}
          </select>
        </label>
        <label className="decision-panel__field">
          <span className="field__label">Shared rejection note</span>
          <textarea
            ref={noteRef}
            value={note}
            onChange={(event) => setNote(event.target.value)}
            maxLength={500}
            required
            aria-required="true"
            data-testid="lead-bulk-reject-note"
          />
        </label>
      </div>
      <div className="bulk-actions__sampler">
        <Button
          type="submit"
          variant="danger"
          size="sm"
          icon="cross"
          disabled={running}
          aria-disabled={armingCopy !== null || undefined}
          aria-describedby={armingCopy !== null ? statusId : undefined}
          data-testid="lead-bulk-reject-confirm"
        >
          {`Reject ${formatCount(count)} eligible`}
        </Button>
        {armingCopy !== null && <span id={statusId} className="muted fs-12">{armingCopy}</span>}
      </div>
    </form>
  );
}
