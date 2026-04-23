import { Icon } from '../Icon';
import { Button } from '../Primitives';

/**
 * ApprovalBanner — prototype `.approval` BEM. Gradient amber banner with
 * shield icon, title + sub copy, Approve + Reject buttons. Nothing is ever
 * sent without the operator clicking Approve.
 */

interface ApprovalBannerProps {
  text?: string;
  count?: number;
  onApprove?: () => void;
  onReject?: () => void;
  approveLabel?: string;
  rejectLabel?: string;
  disabled?: boolean;
}

export function ApprovalBanner({
  text,
  count = 1,
  onApprove,
  onReject,
  approveLabel = 'Approve outreach',
  rejectLabel = 'Reject',
  disabled,
}: ApprovalBannerProps) {
  const sub =
    text ?? `${count} borrower${count === 1 ? '' : 's'} pending review.`;
  return (
    <div className="approval" role="region" aria-label="Approval queue">
      <div className="approval__ico"><Icon name="shield" size={16} /></div>
      <div className="approval__body">
        <div className="approval__title">Pending review</div>
        <div className="approval__sub">{sub}</div>
      </div>
      <div className="approval__actions">
        <Button variant="ghost" size="sm" onClick={onReject} icon="cross" disabled={disabled}>{rejectLabel}</Button>
        <Button variant="primary" size="sm" onClick={onApprove} icon="check" disabled={disabled}>{approveLabel}</Button>
      </div>
    </div>
  );
}
