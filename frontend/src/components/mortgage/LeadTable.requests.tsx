/**
 * The ranked-borrower table's approval-request wiring (audit flow-02 /
 * shell-06, D-approval-flow-c; deviation:request-approval), kept out of the
 * table shell (file-size gate). The Lead Queue passes `approvalRequests`;
 * Segment Intelligence passes none, and then nothing here renders or reads.
 *
 * The request path is open only to a signed-in non-approver whose session
 * check SETTLED (approverGate is exactly APPROVER_ROLE_REQUIRED: never while
 * the session loads or failed), outside a request-scoped queue. A campaign
 * binding keeps the controls visible but aria-disabled with the reason. The
 * form is a lazy chunk opened on the explicit Request approval (row or
 * toolbar); nothing here drafts, writes or reads by itself.
 */
import { useState, type ReactNode } from 'react';
import type { ApprovalRequestCreated } from '../../types/approvalRequests';
import { APPROVER_ROLE_REQUIRED } from './approverGate';
import type { LeadTableApprovalRequests } from './LeadTable.types';
import { lazyModule, useLazyModule } from './useLazyModule';

export const REQUEST_BLOCKED_BY_BINDING =
  "Clear the campaign binding to request approval; a campaign's owner or an admin decides bound outreach.";
const REQUEST_FORM_FAILED = 'The request form could not load, so nothing was requested. Reload the page.';
const REQUEST_FORM = lazyModule(() => import('./RequestApprovalForm'));

interface RequestFormState {
  ids: readonly string[];
  origin: 'row' | 'bulk';
}

export interface LeadTableRequestsInput {
  approvalRequests: LeadTableApprovalRequests | null | undefined;
  approverGate: string | null;
  /** A `?campaign_id=&variant_name=` binding is asked for (valid or not). */
  campaignBound: boolean;
  /** Drop the requested ids from the selection (after the 200 only). */
  dropFromSelection: (ids: readonly string[]) => void;
}

/** The approval request every decision in a scoped queue answers (approvers only). */
export function requestDecisionBatchId(approvalRequests: LeadTableApprovalRequests | null | undefined): string | null {
  return approvalRequests?.role === 'approver' ? approvalRequests.scope?.batchId ?? null : null;
}

export interface LeadTableRequests {
  /** Non-null while the request path is open: its blocked reason (null = live). */
  path: { blockedReason: string | null } | null;
  openRow: (borrowerId: string) => void;
  openBulk: (ids: readonly string[]) => void;
  /** The form when it was opened from a row (the surface-top slot), or from the toolbar. */
  rowForm: ReactNode;
  bulkForm: ReactNode;
  bulkFormOpen: boolean;
}

export function useLeadTableRequests({
  approvalRequests,
  approverGate,
  campaignBound,
  dropFromSelection,
}: LeadTableRequestsInput): LeadTableRequests {
  const [form, setForm] = useState<RequestFormState | null>(null);
  const scoped = approvalRequests?.scope ?? null;
  const pathOpen = approvalRequests?.role === 'requester' && approverGate === APPROVER_ROLE_REQUIRED && scoped === null;
  const path = pathOpen ? { blockedReason: campaignBound ? REQUEST_BLOCKED_BY_BINDING : null } : null;
  const formOpen = form !== null && pathOpen;
  const formChunk = useLazyModule(REQUEST_FORM, formOpen);
  const Form = formChunk.module?.RequestApprovalForm;
  const open = (ids: readonly string[], origin: RequestFormState['origin']) => {
    if (!pathOpen || campaignBound || ids.length === 0) return;
    setForm({ ids, origin });
  };
  const formNode = !formOpen ? null : Form ? (
    <Form
      key={`${form.origin}:${form.ids.join(',')}`}
      borrowerIds={form.ids}
      onCancel={() => setForm(null)}
      onRequested={(created: ApprovalRequestCreated) => {
        setForm(null);
        dropFromSelection(created.requested);
      }}
    />
  ) : formChunk.failed ? (
    <div role="alert" className="table-error" data-testid="request-approval-form-failed">{REQUEST_FORM_FAILED}</div>
  ) : null;
  return {
    path,
    openRow: (borrowerId) => open([borrowerId], 'row'),
    openBulk: (ids) => open(ids, 'bulk'),
    rowForm: form?.origin === 'row' ? formNode : null,
    bulkForm: form?.origin === 'bulk' ? formNode : null,
    bulkFormOpen: formOpen && form.origin === 'bulk',
  };
}
