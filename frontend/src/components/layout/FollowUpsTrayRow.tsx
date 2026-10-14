import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router';
import { Button, Chip } from '../Primitives';
import { Timestamp } from '../ui/Timestamp';
import type { FollowupItem, SnoozeDays } from '../../lib/apiClients/followUps';
import { pushEscapeLayer } from '../../lib/escapeStack';
import { formatDate } from '../../lib/time';

/**
 * One Follow-ups tray row (deviation:followups-tray): a plain link to the
 * borrower (no intent preloading, no prefetch: Borrower 360 writes a
 * VIEW_BORROWER row, so it is read only on a click), the masked id, why the
 * row is here and when it is due. A withheld row adds its closed reason chip
 * and one "Do not call" line and never offers Snooze. A contactable row
 * offers Snooze as an inline group: 1 day, 3 days, 1 week, Cancel.
 */

type BlockReason = NonNullable<FollowupItem['contact_block_reason']>;

const BLOCK_CHIP: Record<BlockReason, string> = {
  consent_not_opt_in: 'No consent',
  suppressed: 'Suppressed',
  eligibility_not_proven: 'Eligibility not proven',
  contactability_not_configured: 'Not configured',
  frequency_cap: 'Frequency cap',
  not_in_coverage: 'Not in coverage',
};

export const SNOOZE_OPTIONS: ReadonlyArray<{ days: SnoozeDays; label: string }> = [
  { days: 1, label: '1 day' },
  { days: 3, label: '3 days' },
  { days: 7, label: '1 week' },
];

export const SNOOZE_HINT =
  'Back after the chosen time, or sooner when a new listing, sale, refinance or payoff is recorded at the next workflow sync.';

export function followupReason(item: FollowupItem): string {
  const signal = item.woke_on_signal ? ' · new signal' : '';
  if (item.kind === 'callback') {
    return `Callback scheduled${item.attempt_number ? ` · attempt ${item.attempt_number}` : ''}${signal}`;
  }
  if (item.kind === 'approval_follow_up') return `Approval follow-up${signal}`;
  if (item.kind === 'requested_follow_up') {
    const owner = item.owner_state === 'unassigned' ? 'Routed, not yet assigned' : item.owner_label;
    return `Follow-up you requested${owner ? ` · ${owner}` : ''}${signal}`;
  }
  return `Assigned to you${signal}`;
}

export function blockLine(item: FollowupItem): string {
  if (item.contact_block_reason === 'frequency_cap' && item.earliest_recontact_at) {
    return `Do not call before ${formatDate(item.earliest_recontact_at)} (contact frequency cap).`;
  }
  if (item.contact_block_reason === 'not_in_coverage') return 'Do not call: no longer in current coverage.';
  return 'Do not call: contact status changed since this was scheduled.';
}

interface RowProps {
  item: FollowupItem;
  /** The days in flight for THIS row, or null. */
  pendingDays: SnoozeDays | null;
  /** Any snooze in flight (another row's included): every option waits. */
  busy: boolean;
  onSnooze: (item: FollowupItem, days: SnoozeDays) => void;
}

export function FollowUpsTrayRow({ item, pendingDays, busy, onSnooze }: RowProps) {
  const reason = item.contact_block_reason;
  return (
    <li className="followups__row" data-borrower-id={item.borrower_id}>
      <Link className="followups__id" to={`/borrower-360/${encodeURIComponent(item.borrower_id)}`}>
        {item.borrower_id}
      </Link>
      <span className="followups__why">{followupReason(item)}</span>
      <Timestamp className="followups__due" value={item.due_at ?? item.since_at} relativeStyle="short" />
      {reason ? (
        <div className="followups__block">
          <Chip variant="warning">{BLOCK_CHIP[reason]}</Chip>
          <span>{blockLine(item)}</span>
        </div>
      ) : (
        <SnoozeControl item={item} pendingDays={pendingDays} busy={busy} onSnooze={onSnooze} />
      )}
    </li>
  );
}

function SnoozeControl({ item, pendingDays, busy, onSnooze }: RowProps) {
  const [choosing, setChoosing] = useState(false);
  const firstOptionRef = useRef<HTMLButtonElement | null>(null);
  const snoozeRef = useRef<HTMLButtonElement | null>(null);
  // Set by Cancel and Escape: Snooze takes focus back once it renders again.
  const restoreFocus = useRef(false);
  const open = choosing || pendingDays !== null;

  useEffect(() => {
    if (open) {
      firstOptionRef.current?.focus({ preventScroll: true });
    } else if (restoreFocus.current) {
      restoreFocus.current = false;
      snoozeRef.current?.focus({ preventScroll: true });
    }
  }, [open]);

  useEffect(() => {
    if (!open) return undefined;
    // Escape collapses the group onto Snooze; while a snooze is in flight it
    // is consumed and changes nothing (the row stays until the server answers).
    return pushEscapeLayer(() => {
      if (pendingDays !== null) return true;
      restoreFocus.current = true;
      setChoosing(false);
      return true;
    });
  }, [open, pendingDays]);

  const cancel = () => {
    restoreFocus.current = true;
    setChoosing(false);
  };

  if (!open) {
    return (
      <button ref={snoozeRef} type="button" className="btn btn--ghost btn--sm followups__snooze" onClick={() => setChoosing(true)}>
        Snooze
      </button>
    );
  }
  return (
    <div className="followups__snooze" role="group" aria-label={`Snooze ${item.borrower_id} for`}>
      {SNOOZE_OPTIONS.map((option, index) => (
        <Button
          key={option.days}
          ref={index === 0 ? firstOptionRef : undefined}
          size="sm"
          variant="ghost"
          loading={pendingDays === option.days}
          disabled={busy && pendingDays !== option.days}
          onClick={() => onSnooze(item, option.days)}
        >
          {option.label}
        </Button>
      ))}
      <Button size="sm" variant="ghost" disabled={busy} onClick={cancel}>
        Cancel
      </Button>
      <p className="followups__hint">{SNOOZE_HINT}</p>
    </div>
  );
}
