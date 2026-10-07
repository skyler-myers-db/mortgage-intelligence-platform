/**
 * The Lead Queue header's approval-request entry (audit flow-02 / shell-06,
 * D-approval-flow-c record steps 18-19; deviation:request-approval): beside
 * the Triage entry in LeadTableHeader's title row. An approver sees
 * "Approval requests · N" (open borrowers across requests), anyone else
 * "My requests · n" (their own requests); hidden at 0 and while the one
 * audit-free list read is on its way. Each opens its panel (lazy, a
 * `.filter-menu` popover with role="dialog"); Escape closes it and focus
 * returns here. A failed read says so, with an explicit Retry: no marks are
 * shown and nothing falls back to a guess.
 */
import { useRef, useState } from 'react';
import { formatCount } from '../../lib/formatters';
import { Button } from '../Primitives';
import type { LeadTableApprovalRequests } from './LeadTable.types';
import { lazyModule, useLazyModule } from './useLazyModule';

const APPROVER_PANEL = lazyModule(() => import('./ApprovalRequestsPanel'));
const MY_PANEL = lazyModule(() => import('./MyApprovalRequestsPanel'));

export function ApprovalRequestChips({ requests }: { requests: LeadTableApprovalRequests }) {
  const [open, setOpen] = useState(false);
  const chipRef = useRef<HTMLButtonElement | null>(null);
  const approver = requests.role === 'approver';
  const approverPanel = useLazyModule(APPROVER_PANEL, open && approver);
  const myPanel = useLazyModule(MY_PANEL, open && !approver);
  if (requests.status === 'error') {
    return (
      <span className="inline-flex muted fs-12" role="status" data-testid="approval-requests-unavailable">
        Approval requests unavailable
        <Button size="sm" variant="ghost" onClick={requests.retry}>Retry</Button>
      </span>
    );
  }
  if (requests.status !== 'ready' || requests.count === 0) return null;
  const close = () => {
    setOpen(false);
    chipRef.current?.focus();
  };
  const ApproverPanel = approverPanel.module?.ApprovalRequestsPanel;
  const MyPanel = myPanel.module?.MyApprovalRequestsPanel;
  const failed = approver ? approverPanel.failed : myPanel.failed;
  return (
    <span className="approval-requests">
      <Button
        ref={chipRef}
        size="sm"
        aria-expanded={open}
        aria-haspopup="dialog"
        onClick={() => setOpen((current) => !current)}
        data-testid="approval-requests-chip"
      >
        {approver ? 'Approval requests' : 'My requests'} · {formatCount(requests.count)}
      </Button>
      {open && ApproverPanel && <ApproverPanel requests={requests} onClose={close} />}
      {open && MyPanel && <MyPanel requests={requests} onClose={close} />}
      {open && failed && (
        <span role="alert" className="text-danger fs-12">The approval requests could not load. Reload the page.</span>
      )}
    </span>
  );
}
