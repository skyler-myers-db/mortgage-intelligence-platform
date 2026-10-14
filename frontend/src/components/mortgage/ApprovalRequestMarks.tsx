/**
 * The Approval column's approval-request marks (audit flow-02 / shell-06,
 * D-approval-flow-c; deviation:request-approval). The Lead Queue's request
 * read (routes/lead-queue.approvalRequests.ts) builds each mark once per
 * answer and LeadTableRow renders its node as given, so this markup ships in
 * the Lead Queue route chunk, not in the LeadTable chunk that Segment
 * Intelligence shares (its lazy-JS gate).
 *
 *  - A requester: a compact warning chip, "Approval requested", in place of
 *    the disabled Approve / Reject (the table's in-row chip size, so the label
 *    keeps its slack in the 140 px content box at Linux text widths).
 *  - An approver: the `send` glyph (the decision history's "requested") after
 *    Reject, a role="img" named "Requested · <name>" with the design-system
 *    tooltip carrying the same text; no title. Approve + Reject fill 126 px of
 *    the pinned column's 140 px content box, so a text chip there squeezed to
 *    an empty pill and pushed both buttons off their unmarked places.
 */
import { Icon } from '../Icon';
import { Chip } from '../Primitives';
import { Tooltip } from '../ui/Tooltip';
import type { ApprovalRequestMark } from './LeadTable.types';

export function requesterMark(label: string): ApprovalRequestMark {
  return {
    label,
    tone: 'warning',
    node: <Chip variant="warning" className="chip--compact lead-table__request-chip">{label}</Chip>,
  };
}

export function approverMark(label: string): ApprovalRequestMark {
  return {
    label,
    tone: 'neutral',
    node: (
      <Tooltip content={label}>
        <span role="img" aria-label={label} className="lead-table__request-mark">
          <Icon name="send" size={10} />
        </span>
      </Tooltip>
    ),
  };
}
