import { Fragment, type CSSProperties, type MouseEvent as ReactMouseEvent, type ReactNode } from 'react';
import type { LeadSummary } from '../../types';
import { DRAWER_SOURCES } from '../../lib/drawerSources';
import { compactCurrency, signedBps } from '../../lib/formatters';
import { offerDisplayLabel } from '../../lib/offerLanguage';
import { safeSegmentName, segmentColor } from '../../lib/segmentMetadata';
import { Icon } from '../Icon';
import { Button, Chip, EvidenceChip } from '../Primitives';
import { ConfidenceMeter } from './ConfidenceMeter';
import type { LeadDecisionReceipt } from './DecisionReceipt';
import { RowPreview } from './LeadRowPreview';
import { LeadRowWorkflowPanel } from './LeadRowWorkflowPanel';
import { LEAD_TABLE_COLUMNS, type LeadTableColumnKey, type LeadTableView } from './LeadTable.columns';
import { LeadTableOverflowChip } from './LeadTableOverflowChip';
import {
  LeadRelationshipCell,
  LeadStatusCell,
  LeadWorkflowFieldCell,
} from './LeadTableWorkflowCells';
import { APPROVER_ROLE_STATUS_ID, describedBy } from './approverGate';
import { ScoreBadge } from './ScoreBadge';

interface LeadTableRowProps {
  lead: LeadSummary;
  virtualIndex: number;
  ariaRowIndex?: number;
  /** Column preset (audit tables-05). Default: the prototype columns + Status. */
  view?: LeadTableView;
  isOpen: boolean;
  approval: string | undefined;
  isSelected: boolean;
  isSelectable: boolean;
  isApprovalEligible: boolean;
  approvalActionsDisabled?: boolean;
  /** Non-null = the actor may not approve; the text is the accessible reason. */
  approverGate?: string | null;
  bulkApproving: boolean;
  salesBusy: boolean;
  salesTeamCount: number;
  pendingApproval: boolean;
  /** The audit row this row's decision wrote; the expanded preview reads it back. */
  decisionReceipt?: LeadDecisionReceipt | null;
  onToggleRow: (lead: LeadSummary, isOpen: boolean) => void;
  onToggleSelect: (borrowerId: string) => void;
  onApprove: (borrowerId: string) => void;
  onReject: (borrowerId: string) => void;
  onOpenDisposition: (borrowerId: string) => void;
  onAssignmentUpdate: (borrowerId: string, update: Partial<LeadSummary>) => void;
}

const SEGMENT_SOURCE = {
  title: 'Segment membership',
  assetPath: DRAWER_SOURCES.segmentPopulation.assetPath,
} as const;

/**
 * One ranked borrower: a one-line row at `--row-h` (audit tables-04; 44px
 * comfortable, 36px compact) whose cells follow the view's column model,
 * plus the expanded preview. Each cell holds one primary value; the rest of
 * what a cell knows is one `+n` (hover card) or one click away in the
 * preview.
 */
export function LeadTableRow({
  lead,
  virtualIndex,
  ariaRowIndex,
  view = 'default',
  isOpen,
  approval,
  isSelected,
  isSelectable,
  isApprovalEligible,
  approvalActionsDisabled = false,
  approverGate = null,
  bulkApproving,
  salesBusy,
  salesTeamCount,
  pendingApproval,
  decisionReceipt = null,
  onToggleRow,
  onToggleSelect,
  onApprove,
  onReject,
  onOpenDisposition,
  onAssignmentUpdate,
}: LeadTableRowProps) {
  const stop = (e: ReactMouseEvent) => e.stopPropagation();
  const toggleRow = () => onToggleRow(lead, isOpen);
  const expand = () => {
    if (!isOpen) onToggleRow(lead, isOpen);
  };
  const resolvedAriaRowIndex = ariaRowIndex ?? virtualIndex + 2;
  const gated = approverGate !== null;
  const decisionDescribedBy = describedBy(
    gated && APPROVER_ROLE_STATUS_ID,
    approvalActionsDisabled && 'campaign-binding-status',
  );
  const columns = LEAD_TABLE_COLUMNS[view];
  const offerLabel = offerDisplayLabel(lead.recommended_offer_code, lead.recommended_offer);
  const [primarySegment, ...moreSegments] = lead.segment_codes;

  const cells: Record<LeadTableColumnKey, () => ReactNode> = {
    select: () => (
      <td className="tbl-cell--select" onClick={stop}>
        <input
          type="checkbox"
          aria-label={`Select lead ${lead.borrower_id}`}
          checked={isSelected}
          disabled={!isSelectable || bulkApproving}
          onChange={() => onToggleSelect(lead.borrower_id)}
          onClick={stop}
          data-testid={`lead-select-${lead.borrower_id}`}
        />
      </td>
    ),
    expand: () => (
      <td>
        <Icon name={isOpen ? 'down' : 'chevright'} size={14} className="muted" />
      </td>
    ),
    borrower: () => (
      <td className="is-primary">
        <button
          type="button"
          className="lead-table__borrower-btn"
          aria-expanded={isOpen}
          aria-label={`Toggle preview for lead ${lead.borrower_id}`}
          onClick={(e) => {
            e.stopPropagation();
            onToggleRow(lead, isOpen);
          }}
        >
          <span className="mono lead-table__borrower">{lead.borrower_id}</span>
          {/* One opaque token — it truncates with an ellipsis rather than
              wrapping mid-identifier, so the full ref rides on `title`. */}
          <span
            className="mono muted lead-table__clip"
            title={lead.clip && lead.clip.length > 0 ? lead.clip : undefined}
          >
            {lead.clip && lead.clip.length > 0 ? lead.clip : 'Property ref unavailable'}
          </span>
        </button>
      </td>
    ),
    location: () => (
      <td className="lead-table__location">
        <div className="lead-table__city" title={`${lead.city}, ${lead.state}`}>{lead.city}, {lead.state}</div>
        <div className="muted mono lead-table__zip">{lead.zip}</div>
      </td>
    ),
    relationship: () => <LeadRelationshipCell lead={lead} onExpand={expand} />,
    assignment: () => (
      <LeadWorkflowFieldCell lead={lead} keys={['assignment']} noun={['assignment detail', 'assignment details']} empty="Unassigned" onExpand={expand} />
    ),
    outreach: () => (
      <LeadWorkflowFieldCell lead={lead} keys={['outreach', 'aging']} noun={['outreach detail', 'outreach details']} empty="No outreach" onExpand={expand} />
    ),
    lastTouch: () => (
      <LeadWorkflowFieldCell lead={lead} keys={['last_touch']} noun={['contact detail', 'contact details']} empty="No contact logged" onExpand={expand} />
    ),
    segments: () => (
      <td>
        <div className="lead-table__line lead-table__segments">
          {primarySegment && (
            <span
              className="chip chip--segment chip--compact"
              style={{ '--chip-hue': segmentColor(primarySegment) } as CSSProperties}
              title={safeSegmentName(primarySegment) ?? 'Unknown segment'}
            >
              <span className="chip__label">{safeSegmentName(primarySegment) ?? 'Unknown segment'}</span>
            </span>
          )}
          <LeadTableOverflowChip
            items={moreSegments.map((code) => ({ field: 'Segment', value: safeSegmentName(code) ?? 'Unknown segment' }))}
            noun={['segment', 'segments']}
            source={SEGMENT_SOURCE}
            onActivate={expand}
          />
        </div>
      </td>
    ),
    equity: () => <td className="num tbl-cell--right">{compactCurrency(lead.equity_estimate)}</td>,
    rate: () => (
      <td
        className={`num tbl-cell--right ${lead.rate_spread_bps >= 75 ? 'lead-table__rate--positive' : 'lead-table__rate--neutral'}`}
      >
        {signedBps(lead.rate_spread_bps)}
      </td>
    ),
    offer: () => (
      <td>
        <div className="lead-table__line">
          <span className="mono fs-12 text-1 lead-table__offer" title={offerLabel}>{offerLabel}</span>
          {/* The prototype's in-row chip is a short rule tag
              (design_files/Module 0 Prototype.html:2052, `nbo_v3`): "rules"
              on screen, the full source name for assistive tech. */}
          <EvidenceChip source={DRAWER_SOURCES.nbo}>
            <span className="sr-only">Primary offer </span>rules
          </EvidenceChip>
        </div>
      </td>
    ),
    score: () => (
      <td className="tbl-cell--right">
        <ScoreBadge value={lead.opportunity_score} />
      </td>
    ),
    confidence: () => (
      <td>
        <ConfidenceMeter value={lead.confidence} compact />
      </td>
    ),
    status: () => <LeadStatusCell lead={lead} onExpand={expand} />,
    approval: () => (
      <td className="tbl-cell--approval" data-testid={`lead-approval-cell-${lead.borrower_id}`}>
        {approval === 'approved' && <Chip variant="success" icon="check">Approved</Chip>}
        {approval === 'rejected' && <Chip variant="danger" icon="cross">Rejected</Chip>}
        {approval === 'hold' && <Chip variant="warning" icon="shield">Hold</Chip>}
        {!approval && !isApprovalEligible && <Chip variant="warning" icon="shield">Not actionable</Chip>}
        {!approval && isApprovalEligible && (
          <div className="lead-table__approval-actions" onClick={stop}>
            <Button
              variant="primary"
              size="sm"
              icon="check"
              disabled={gated || approvalActionsDisabled || pendingApproval}
              aria-describedby={decisionDescribedBy}
              title={approverGate ?? undefined}
              onClick={(e) => {
                e.stopPropagation();
                onApprove(lead.borrower_id);
              }}
              aria-label={`Approve ${lead.borrower_id}`}
              // A / R are bound to the EXPANDED row only, so only that row
              // advertises them (audit a11y-09); never for a gated actor.
              aria-keyshortcuts={isOpen && !gated ? 'A' : undefined}
              data-testid={`lead-approve-${lead.borrower_id}`}
            >
              {pendingApproval ? 'Approving…' : 'Approve'}
            </Button>
            <button
              type="button"
              className="btn btn--sm lead-table__reject"
              aria-label={`Reject ${lead.borrower_id}`}
              aria-keyshortcuts={isOpen && !gated ? 'R' : undefined}
              title={approverGate ?? 'Reject'}
              disabled={gated || approvalActionsDisabled || pendingApproval}
              aria-describedby={decisionDescribedBy}
              onClick={(e) => {
                e.stopPropagation();
                onReject(lead.borrower_id);
              }}
              data-testid={`lead-reject-${lead.borrower_id}`}
            >
              <Icon name="cross" size={12} />
            </button>
          </div>
        )}
      </td>
    ),
  };

  return (
    <Fragment>
      <tr
        className={isOpen ? 'is-expanded' : ''}
        aria-rowindex={resolvedAriaRowIndex}
        onClick={toggleRow}
      >
        {columns.map((column) => <Fragment key={column.key}>{cells[column.key]()}</Fragment>)}
      </tr>
      {isOpen && (
        <tr className="tbl__expand" aria-rowindex={resolvedAriaRowIndex + 1}>
          <td colSpan={columns.length}>
            <RowPreview lead={lead} approval={approval} decisionReceipt={decisionReceipt} />
            <LeadRowWorkflowPanel
              lead={lead}
              salesBusy={salesBusy}
              salesTeamCount={salesTeamCount}
              onOpenDisposition={onOpenDisposition}
              onAssignmentUpdate={onAssignmentUpdate}
            />
          </td>
        </tr>
      )}
    </Fragment>
  );
}
