/**
 * The scope row of an approver's request-scoped Lead Queue (audit flow-02 /
 * shell-06, D-approval-flow-c; deviation:request-approval): who asked, how
 * many borrowers are still open and the "Reason for the request" (O3), with
 * Clear. It renders in LeadTableStatusChips' slot, from the Lead Queue route
 * (kept out of the shared LeadTable chunk); it reads nothing.
 */
import { formatCount } from '../../lib/formatters';
import { Button, Chip } from '../Primitives';
import { REQUEST_REASON_LABEL } from './approvalRequestCopy';
import type { LeadTableApprovalRequests } from './LeadTable.types';

export function ApprovalRequestScopeRow({
  requests,
  onClear,
}: {
  requests: LeadTableApprovalRequests;
  /** Drop the scope (the param) and return focus to the table region. */
  onClear: () => void;
}) {
  const batch = requests.scope?.batch ?? null;
  const open = batch ? batch.rows.filter((row) => row.state === 'open').length : 0;
  return (
    <div className="table-neutral chip-row" role="status" data-testid="approval-request-scope">
      <Chip variant="neutral" icon="shield">Approval request</Chip>
      {batch ? (
        <>
          <span>
            {batch.requested_by_display ?? 'A teammate'} · {formatCount(open)} {open === 1 ? 'borrower' : 'borrowers'}
          </span>
          <span>{REQUEST_REASON_LABEL}: {batch.rationale}</span>
        </>
      ) : (
        <span>
          {requests.status === 'error'
            ? 'Approval requests unavailable.'
            : requests.status === 'ready' ? 'This request has no open borrowers.' : 'Reading the request…'}
        </span>
      )}
      <Button variant="ghost" size="sm" onClick={onClear} data-testid="approval-request-scope-clear">Clear</Button>
    </div>
  );
}
