import { PageShell } from '../components/layout/PageShell';

export function OfferSnapshotReconciliation({
  borrowerId,
  inline = false,
}: {
  borrowerId: string;
  inline?: boolean;
}) {
  const status = (
    <div className={inline ? 'status-callout status-callout--warning' : 'status-callout'} role="status" aria-live="polite">
      {inline
        ? 'A data refresh is being reconciled. The current snapshot remains visible, but approval is paused until the borrower and offer cite the same refresh.'
        : 'The borrower profile and offer recommendation are being aligned before review controls become available.'}
    </div>
  );
  if (inline) return status;
  return (
    <PageShell
      eyebrow="Reconciling data refresh"
      title={`Aligning borrower and offer data for ${borrowerId}…`}
      lede="A gold-table refresh landed between the two reads. The app is bypassing its dossier cache and retrying until both views cite the same refresh."
    >
      <div className="surface"><div className="surface__body">{status}</div></div>
    </PageShell>
  );
}
