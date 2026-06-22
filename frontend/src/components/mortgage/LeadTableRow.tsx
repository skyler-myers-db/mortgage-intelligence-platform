import { Fragment, type CSSProperties, type MouseEvent as ReactMouseEvent } from 'react';
import type { LeadSummary } from '../../types';
import { DRAWER_SOURCES } from '../../lib/drawerSources';
import { offerDisplayLabel } from '../../lib/offerLanguage';
import { segmentColor, segmentName } from '../../lib/segmentMetadata';
import { Icon } from '../Icon';
import { Button, Chip, EvidenceChip } from '../Primitives';
import { ConfidenceMeter } from './ConfidenceMeter';
import {
  dispositionLabel,
  dispositionVariant,
  formatDateTimeShort,
  outreachLabel,
  outreachVariant,
  relationshipLabel,
  relationshipVariant,
} from './LeadTable.logic';
import { RowPreview } from './LeadRowPreview';
import { ScoreBadge } from './ScoreBadge';

interface LeadTableRowProps {
  lead: LeadSummary;
  virtualIndex: number;
  isOpen: boolean;
  approval: string | undefined;
  isSelected: boolean;
  isSelectable: boolean;
  isApprovalEligible: boolean;
  bulkApproving: boolean;
  salesBusy: boolean;
  salesTeamCount: number;
  pendingApproval: boolean;
  onToggleRow: (lead: LeadSummary, isOpen: boolean) => void;
  onToggleSelect: (borrowerId: string) => void;
  onApprove: (borrowerId: string) => void;
  onReject: (borrowerId: string) => void;
  onOpenDisposition: (borrowerId: string) => void;
}

export function LeadTableRow({
  lead,
  virtualIndex,
  isOpen,
  approval,
  isSelected,
  isSelectable,
  isApprovalEligible,
  bulkApproving,
  salesBusy,
  salesTeamCount,
  pendingApproval,
  onToggleRow,
  onToggleSelect,
  onApprove,
  onReject,
  onOpenDisposition,
}: LeadTableRowProps) {
  const stop = (e: ReactMouseEvent) => e.stopPropagation();
  const toggleRow = () => onToggleRow(lead, isOpen);

  return (
    <Fragment>
      <tr
        className={isOpen ? 'is-expanded' : ''}
        aria-rowindex={virtualIndex + 2}
        onClick={toggleRow}
      >
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
        <td>
          <Icon name={isOpen ? 'down' : 'chevright'} size={14} className="muted" />
        </td>
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
            <span className="mono muted lead-table__clip">
              {lead.clip && lead.clip.length > 0 ? lead.clip : 'Property ref unavailable'}
            </span>
          </button>
        </td>
        <td>
          {lead.city}, {lead.state}
          <div className="muted mono lead-table__zip">{lead.zip}</div>
        </td>
        <td>
          <div className="chip-stack">
            <Chip variant={relationshipVariant(lead)}>{relationshipLabel(lead)}</Chip>
            {lead.marketing_eligible === false && (
              <Chip variant="warning">
                Suppressed{lead.suppression_reason ? `: ${lead.suppression_reason}` : ''}
              </Chip>
            )}
          </div>
        </td>
        <td>
          <div className="chip-stack">
            <Chip variant={lead.assigned_to_email ? 'success' : 'neutral'}>
              {lead.assigned_to_label ?? lead.assigned_to_email ?? 'Unassigned'}
            </Chip>
            {lead.assigned_at && (
              <span className="muted mono fs-11">{formatDateTimeShort(lead.assigned_at)}</span>
            )}
          </div>
        </td>
        <td>
          <div className="chip-stack">
            <Chip variant={outreachVariant(lead.outreach_status)}>
              {outreachLabel(lead.outreach_status)}
            </Chip>
            {typeof lead.aging_days === 'number' && lead.aging_days > 7 && (
              <Chip variant="warning">{lead.aging_days}d aging</Chip>
            )}
          </div>
        </td>
        <td>
          <div className="chip-stack lead-table__last-touch">
            <Chip variant={dispositionVariant(lead.latest_disposition_outcome)}>
              {dispositionLabel(lead.latest_disposition_outcome)}
            </Chip>
            {lead.latest_disposition_at && (
              <span className="muted mono fs-11">{formatDateTimeShort(lead.latest_disposition_at)}</span>
            )}
            <Button
              variant="ghost"
              size="sm"
              icon="bolt"
              onClick={(e) => {
                e.stopPropagation();
                onOpenDisposition(lead.borrower_id);
              }}
              disabled={salesBusy || salesTeamCount === 0}
              aria-label={`Log call disposition for ${lead.borrower_id}`}
            >
              Log
            </Button>
          </div>
        </td>
        <td>
          <div className="lead-table__segments">
            {lead.segment_codes.slice(0, 2).map((sid) => {
              const color = segmentColor(sid);
              return (
                <span
                  key={sid}
                  className="chip chip--segment chip--compact"
                  style={{ '--chip-hue': color } as CSSProperties}
                >
                  <span className="chip__label">{segmentName(sid)}</span>
                </span>
              );
            })}
            {lead.segment_codes.length > 2 && (
              <span className="chip chip--neutral chip--compact">
                <span className="chip__label">+{lead.segment_codes.length - 2}</span>
              </span>
            )}
          </div>
        </td>
        <td className="num tbl-cell--right">${(lead.equity_estimate / 1000).toFixed(0)}k</td>
        <td
          className={`num tbl-cell--right ${lead.rate_spread_bps >= 75 ? 'lead-table__rate--positive' : 'lead-table__rate--neutral'}`}
        >
          +{lead.rate_spread_bps}
        </td>
        <td>
          <span className="mono fs-12 text-1">
            {offerDisplayLabel(lead.recommended_offer_code, lead.recommended_offer)}
          </span>{' '}
          <EvidenceChip source={DRAWER_SOURCES.nbo}>{DRAWER_SOURCES.nbo.title}</EvidenceChip>
        </td>
        <td className="tbl-cell--right">
          <ScoreBadge value={lead.opportunity_score} />
        </td>
        <td>
          <ConfidenceMeter value={lead.confidence} compact />
        </td>
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
                disabled={pendingApproval}
                onClick={(e) => {
                  e.stopPropagation();
                  onApprove(lead.borrower_id);
                }}
                aria-label={`Approve ${lead.borrower_id}`}
                data-testid={`lead-approve-${lead.borrower_id}`}
              >
                {pendingApproval ? 'Approving…' : 'Approve'}
              </Button>
              <button
                type="button"
                className="btn btn--sm lead-table__reject"
                aria-label={`Reject ${lead.borrower_id}`}
                title="Reject"
                disabled={pendingApproval}
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
      </tr>
      {isOpen && (
        <tr className="tbl__expand">
          <td colSpan={15}>
            <RowPreview lead={lead} approval={approval} />
          </td>
        </tr>
      )}
    </Fragment>
  );
}
