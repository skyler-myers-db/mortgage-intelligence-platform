import { useState } from 'react';
import { copyLink } from '../../lib/copyLink';
import { Countdown, WaitClock } from './RetryClock';

/**
 * WarmingUpBlock's waits and reference (audit 2026-09-21 `states-08` part 4),
 * loaded on demand by the block: only a warm-up renders them, so the clocks
 * stay out of every route's natural-load closure.
 */

/**
 * "Waiting 0:42 · next try in 4 s". The wait runs from when this line
 * mounted with the block; the next try is due one plan interval after the
 * attempt count last changed (TanStack exposes no failure times, so the
 * clock lives here, keyed by the attempt, not in the hook).
 */
export function WaitLine({ attempt, intervalMs }: { attempt: number; intervalMs?: number }) {
  const [since] = useState(() => Date.now());
  return (
    <span className="muted warming-block__meta" data-testid="warming-up-wait">
      · Waiting <WaitClock since={since} />
      {intervalMs !== undefined && <NextTry key={attempt} intervalMs={intervalMs} />}
    </span>
  );
}

function NextTry({ intervalMs }: { intervalMs: number }) {
  const [until] = useState(() => Date.now() + intervalMs);
  return (
    <>
      {' · next try in '}
      <Countdown until={until} />
    </>
  );
}

/** The support reference, behind a disclosure: a copyable id, never prose. */
export function ReferenceDetails({ reference }: { reference: string }) {
  return (
    <details className="warming-block__details">
      <summary className="muted fs-12">Details</summary>
      <span className="muted fs-12">Reference </span>
      <span className="mono fs-12" data-testid="warming-up-reference">{reference}</span>{' '}
      <button
        type="button"
        className="btn btn--ghost btn--sm"
        onClick={() => void copyLink(reference, { success: 'Reference copied', failure: 'Copy failed' })}
      >
        Copy
      </button>
    </details>
  );
}
