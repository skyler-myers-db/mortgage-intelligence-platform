export function ApprovalBanner({ onApprove }: { onApprove?: () => void }) {
  return (
    <div className="approval">
      <div>
        <strong>Human approval required before outreach</strong>
        <div className="muted">No email, SMS, CRM task, or export happens until a human approves and the action is audited.</div>
      </div>
      <button className="button primary" onClick={onApprove}>Approve outreach</button>
    </div>
  );
}
