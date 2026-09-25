/**
 * ScoreAnatomyGate — the static disclosure in front of the lazy Score
 * anatomy chunk (wow-stage-2 / wow-ai-1). Kept small: it ships in every
 * chunk that shows a score card.
 *
 * THE TRAP: every GET /api/borrowers/{id}/proof writes a VIEW_BORROWER_PROOF
 * audit row. So:
 * - collapsed over a cold cache, nothing loads and nothing is read;
 * - over a warm cache (e.g. "Show math" already read the proof) the region
 *   renders expanded without a click, from the cache, with the query
 *   DISABLED: only an explicit click on this button may enable it;
 * - the enabled query lives in the chunk, so it cannot start before the
 *   chunk loads: a chunk that fails to load writes no audit row. The chunk
 *   loads through lazyModule (no React.lazy, no Suspense), so a stale chunk
 *   is a plain note, never a route-boundary throw.
 * The click latches per borrower id (DecisionReceipt's revealLatch pattern):
 * paging to another borrower starts collapsed again.
 */
import { useId, useState } from 'react';
import type { ProofScoreComponentKey } from '../../types';
import { Button } from '../Primitives';
import { lazyModule, useLazyModule } from './useLazyModule';
import { useBorrowerProof } from './useBorrowerProof';
import { SCORE_ANATOMY_COPY } from './scoreAnatomy.copy';

const ANATOMY_CHUNK = lazyModule(() => import('./ScoreAnatomy'));

export interface ScoreAnatomyGateProps {
  borrowerId: string;
  variant: 'spine' | 'margins';
  /** The page's own proof drawer; without it the chunk mounts one. */
  onOpenComponent?: (key: ProofScoreComponentKey) => void;
}

export function ScoreAnatomyGate({ borrowerId, variant, onOpenComponent }: ScoreAnatomyGateProps) {
  const regionId = useId();
  // null: this borrower has not been toggled, so the region follows the cache.
  const [latch, setLatch] = useState<{ borrowerId: string; open: boolean | null }>({ borrowerId, open: null });
  if (latch.borrowerId !== borrowerId) setLatch({ borrowerId, open: null });
  const choice = latch.borrowerId === borrowerId ? latch.open : null;

  // A cache read only: this observer never fetches.
  const cached = useBorrowerProof(borrowerId, false);
  const expanded = choice ?? cached.data !== undefined;
  const chunk = useLazyModule(ANATOMY_CHUNK, expanded);
  const Anatomy = chunk.module?.ScoreAnatomy;

  return (
    <div className="mt-3" data-testid={`score-anatomy-${variant}`}>
      <Button
        variant="ghost"
        size="sm"
        icon={expanded ? 'chevdown' : 'chevright'}
        aria-expanded={expanded}
        aria-controls={regionId}
        onClick={() => setLatch({ borrowerId, open: !expanded })}
      >
        {variant === 'spine' ? SCORE_ANATOMY_COPY.spineToggle : SCORE_ANATOMY_COPY.marginsToggle}
      </Button>
      <div id={regionId} className="mt-2" hidden={!expanded} data-testid="score-anatomy-region">
        {expanded && (chunk.failed ? (
          <p className="muted fs-12 flush" role="status">{SCORE_ANATOMY_COPY.chunkFailed}</p>
        ) : Anatomy ? (
          <Anatomy
            borrowerId={borrowerId}
            variant={variant}
            fetchEnabled={choice === true}
            onOpenComponent={onOpenComponent}
          />
        ) : (
          <p className="muted fs-12 flush" role="status" aria-busy="true">{SCORE_ANATOMY_COPY.loading}</p>
        ))}
      </div>
    </div>
  );
}
