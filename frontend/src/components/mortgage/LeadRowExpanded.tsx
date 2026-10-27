import type { LeadSummary } from '../../types';
import type { LeadDecisionReceipt } from './DecisionReceipt';
import { RowPreview, type RowPreviewApprovalGate } from './LeadRowPreview';
import { LeadRowWorkflowPanel } from './LeadRowWorkflowPanel';

// The Triage deck takes its preview from here (TriageDeck.tsx says why).
export { RowPreview };

export interface LeadRowExpandedProps {
  lead: LeadSummary;
  approval: string | undefined;
  decisionReceipt: LeadDecisionReceipt | null;
  approvalGate: RowPreviewApprovalGate | null;
  salesBusy: boolean;
  salesTeamCount: number;
  onOpenDisposition: (borrowerId: string) => void;
  onAssignmentUpdate: (borrowerId: string, update: Partial<LeadSummary>) => void;
}

/**
 * The expanded Lead Queue row's body: the Borrower 360 preview (RowPreview)
 * and the workflow strip (LeadRowWorkflowPanel, with its lifecycle advance).
 * None of it is on the queue's first paint, so it is its own chunk (the
 * leadsQuery chunk cut that funds tail T1c, 2026-10-26): LeadTableRow renders
 * it through LeadTableRow.expanded.tsx, which loads it on the expand and
 * warms it on idle and on row engagement. The approve review stays with the
 * row (its own chunk, rendered above this body).
 */
export function LeadRowExpanded({
  lead,
  approval,
  decisionReceipt,
  approvalGate,
  salesBusy,
  salesTeamCount,
  onOpenDisposition,
  onAssignmentUpdate,
}: LeadRowExpandedProps) {
  return (
    <>
      <RowPreview lead={lead} approval={approval} decisionReceipt={decisionReceipt} approvalGate={approvalGate} />
      <LeadRowWorkflowPanel
        lead={lead}
        salesBusy={salesBusy}
        salesTeamCount={salesTeamCount}
        onOpenDisposition={onOpenDisposition}
        onAssignmentUpdate={onAssignmentUpdate}
      />
    </>
  );
}
