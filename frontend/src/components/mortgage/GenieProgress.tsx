import { useEffect, useState } from 'react';
import { Icon } from '../Icon';

const GENIE_PROGRESS_STEPS = [
  'Opening a governed Genie turn',
  'Selecting trusted Unity Catalog assets',
  'Generating read-only SQL',
  'Executing on the Databricks warehouse',
  'Validating sources, rows, and freshness',
  'Planning the answer view',
];

/**
 * Known Genie message statuses → human staged-progress copy. When the ask
 * flow has a live `genie_status`, we render the mapped label directly (no
 * timer, no invented cadence). Unknown / absent statuses fall back to the
 * generic rotating steps below.
 */
const GENIE_STATUS_LABELS: Record<string, string> = {
  FILTERING_CONTEXT: 'Scoping context',
  PENDING_WAREHOUSE: 'Warming warehouse',
  ASKING_AI: 'Composing answer',
  EXECUTING_QUERY: 'Running governed SQL',
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
  /** Live Genie message status. A known value renders staged copy with no
   *  fake timer; unknown/absent keeps the generic rotating fallback. */
  status?: string | null;
}) {
  const mapped = genieStatusLabel(status);
  const [step, setStep] = useState(0);

  useEffect(() => {
    // No fake timer when a real status drives the copy — the label is
    // status-derived, not clock-derived.
    if (mapped) return undefined;
    const id = window.setInterval(() => {
      setStep((cur) => Math.min(cur + 1, GENIE_PROGRESS_STEPS.length - 1));
    }, 2600);
    return () => window.clearInterval(id);
  }, [mapped]);

  const label = mapped ?? GENIE_PROGRESS_STEPS[step];
  // Rail highlight index: for a mapped status, light the rail proportionally
  // to where that status sits in the known lifecycle so the bar isn't stuck
  // at step 0. Purely cosmetic; no timer.
  const railActiveThrough = mapped
    ? Math.min(
        GENIE_PROGRESS_STEPS.length - 1,
        Object.keys(GENIE_STATUS_LABELS).indexOf(status ?? ''),
      )
    : step;

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
      {!dense && (
        <div className="genie-progress__rail" aria-hidden="true">
          {GENIE_PROGRESS_STEPS.map((railLabel, i) => (
            <span
              key={railLabel}
              className={i <= railActiveThrough ? 'is-active' : undefined}
            />
          ))}
        </div>
      )}
      <div className="genie-progress__meta">
        Live Genie calls can take 10-20 seconds while SQL compiles and runs.
      </div>
    </div>
  );
}
