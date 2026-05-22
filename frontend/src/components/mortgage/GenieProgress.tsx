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

export function GenieProgress({ dense = false }: { dense?: boolean }) {
  const [step, setStep] = useState(0);

  useEffect(() => {
    const id = window.setInterval(() => {
      setStep((cur) => Math.min(cur + 1, GENIE_PROGRESS_STEPS.length - 1));
    }, 2600);
    return () => window.clearInterval(id);
  }, []);

  return (
    <div
      className={`genie-progress ${dense ? 'genie-progress--dense' : ''}`}
      role="status"
      aria-live="polite"
    >
      <div className="genie-progress__head">
        <Icon name="sparkle" size={12} className="icon-accent" />
        <span>{GENIE_PROGRESS_STEPS[step]}</span>
      </div>
      {!dense && (
        <div className="genie-progress__rail" aria-hidden="true">
          {GENIE_PROGRESS_STEPS.map((label, i) => (
            <span
              key={label}
              className={i <= step ? 'is-active' : undefined}
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
