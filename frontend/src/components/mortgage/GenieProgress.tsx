import { Icon } from '../Icon';

const INDETERMINATE_LABEL = 'Waiting for Genie response';

/**
 * Observed Genie message statuses → human progress copy. The current message
 * endpoint is synchronous, so absent/unknown status is an honest static
 * indeterminate state rather than a client-invented lifecycle.
 */
const GENIE_STATUS_LABELS: Record<string, string> = {
  SUBMITTED: 'Question submitted to Genie',
  IN_PROGRESS: 'Genie is processing the question',
  EXECUTING_QUERY: 'Genie is running the generated query',
};

export function genieStatusLabel(status: string | null | undefined): string | null {
  if (!status) return null;
  return GENIE_STATUS_LABELS[status] ?? null;
}

export function GenieProgress({
  dense = false,
  status = null,
}: {
  dense?: boolean;
  /** A live Genie message status. Unknown/absent remains indeterminate. */
  status?: string | null;
}) {
  const mapped = genieStatusLabel(status);
  const label = mapped ?? INDETERMINATE_LABEL;

  return (
    <div
      className={`genie-progress ${dense ? 'genie-progress--dense' : ''}`}
      role="status"
      aria-live="polite"
    >
      <div className="genie-progress__head">
        <Icon name="sparkle" size={12} className="icon-accent" />
        <span>{label}</span>
      </div>
      <div className="genie-progress__meta">
        {dense
          ? 'The live request is still pending.'
          : 'The answer will appear after the live request completes.'}
      </div>
    </div>
  );
}
