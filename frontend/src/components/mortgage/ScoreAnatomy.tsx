/**
 * ScoreAnatomy — the lazy chunk behind ScoreAnatomyGate (wow-stage-2 /
 * wow-ai-1).
 *
 * The audited /proof read lives HERE, so it can only start once this chunk
 * has loaded (a chunk that fails to load writes no VIEW_BORROWER_PROOF row),
 * and only when the gate says the reviewer clicked (`fetchEnabled`). Over a
 * warm cache the gate mounts this chunk without a click: the query stays
 * disabled and renders from the cache.
 *
 * - variant 'spine': the ScoreSpine (with its seal or gaps) and the margins
 *   under it.
 * - variant 'margins': the margins alone (Offer Orchestrator).
 *
 * A caller that owns a proof drawer (Borrower 360) passes onOpenComponent;
 * one that does not (the Lead Queue preview) gets a drawer mounted here, so
 * a segment still opens its math. That drawer opens over the cached proof.
 */
import { useState } from 'react';
import type { ProofScoreComponentKey } from '../../types';
import { Button } from '../Primitives';
import { Skeleton } from '../ui/Skeleton';
import { BorrowerProofDrawer } from './BorrowerProofDrawer';
import { ProofMargins } from './ProofMargins';
import { ScoreSpine } from './ScoreSpine';
import { SCORE_ANATOMY_COPY } from './scoreAnatomy.copy';
import { SCORE_SPINE_COPY } from './scoreSpine.copy';
import { useBorrowerProof } from './useBorrowerProof';

// Borrower 360 loads its proof drawer from this chunk too (one lazy chunk for
// the proof UI instead of two that each lose brotli's shared context).
export { BorrowerProofDrawer };

export type ScoreAnatomyVariant = 'spine' | 'margins';

export interface ScoreAnatomyProps {
  borrowerId: string;
  variant: ScoreAnatomyVariant;
  /** The reviewer clicked the disclosure for this borrower: the only thing that may read. */
  fetchEnabled: boolean;
  onOpenComponent?: (key: ProofScoreComponentKey) => void;
}

export function ScoreAnatomy({ borrowerId, variant, fetchEnabled, onOpenComponent }: ScoreAnatomyProps) {
  const query = useBorrowerProof(borrowerId, fetchEnabled);
  const [drawer, setDrawer] = useState<{ open: boolean; focus: ProofScoreComponentKey | null }>({
    open: false,
    focus: null,
  });
  const proof = query.data;

  if (!proof) {
    if (query.isError) {
      return (
        <div className="proof-callout proof-callout--warning" role="alert">
          <div className="h-5">{SCORE_SPINE_COPY.unavailableTitle}</div>
          <p className="muted fs-12 flush">{SCORE_SPINE_COPY.unavailableBody}</p>
          <Button size="sm" className="mt-2" onClick={() => void query.refetch()}>{SCORE_SPINE_COPY.retry}</Button>
        </div>
      );
    }
    return (
      <div className="stack-sm" role="status" aria-busy="true">
        <span className="sr-only">{SCORE_ANATOMY_COPY.loading}</span>
        <Skeleton width="100%" height={8} rounded="sm" />
        <Skeleton width="72%" height={12} rounded="sm" />
      </div>
    );
  }

  const ownsDrawer = variant === 'spine' && !onOpenComponent;
  const openComponent = onOpenComponent ?? ((key: ProofScoreComponentKey) => setDrawer({ open: true, focus: key }));

  return (
    <>
      {variant === 'spine' && <ScoreSpine proof={proof} onOpenComponent={openComponent} />}
      <ProofMargins proof={proof} titled={variant === 'spine'} />
      {ownsDrawer && (
        <BorrowerProofDrawer
          borrowerId={borrowerId}
          open={drawer.open}
          focusComponent={drawer.focus}
          onClose={() => setDrawer((current) => ({ ...current, open: false }))}
        />
      )}
    </>
  );
}
