import { useMemo } from 'react';
import type { Borrower360 } from '../../types';
import { Icon } from '../Icon';
import { buildBorrowerStory } from '../../lib/borrowerStory';

/**
 * "The story" (re-audit Buyer-Wow #3) — the system EXPLAINS a lead in plain
 * English, not just ranks it. Composed deterministically from the dossier's
 * proof-backed fields (no live Genie call → instant, never fails at the booth)
 * and every figure is routed through a numeric-claims verifier: the card only
 * badges itself "evidence-verified" when each number traces to its source
 * field and no un-grounded number appears.
 *
 * 2026-06-13: renders automatically (no "Tell the story" click). The narrative
 * IS the explanation a reviewer wants the moment they open the dossier; gating
 * it behind a button was friction. Always-on, so no aria-live reveal needed.
 */
export function BorrowerStoryCard({ borrower }: { borrower: Borrower360 }) {
  const story = useMemo(() => buildBorrowerStory(borrower), [borrower]);

  return (
    <div className="surface borrower-story">
      <div className="surface__hdr">
        <div className="surface__hdr-main">
          <div className="surface__icon"><Icon name="sparkle" size={14} /></div>
          <div>
            <div className="h-4">The story</div>
            <div className="muted fs-12">
              Plain-English explanation, grounded in this dossier&apos;s evidence.
            </div>
          </div>
        </div>
      </div>
      <div className="surface__body" data-testid="borrower-story-body">
        <p className="borrower-story__narrative">{story.sentences.join(' ')}</p>

        <div className="borrower-story__claims" aria-label="Figures grounded in the dossier">
          {story.claims.map((claim) => (
            <span
              key={`${claim.label}-${claim.token}`}
              className={`borrower-story__claim${claim.verified ? '' : ' borrower-story__claim--unverified'}`}
              title={`${claim.label}: ${claim.token} (from ${String(claim.field)})`}
            >
              <Icon name={claim.verified ? 'check' : 'cross'} size={9} />
              <span className="borrower-story__claim-label">{claim.label}</span>
              <span className="borrower-story__claim-token mono">{claim.token}</span>
            </span>
          ))}
        </div>

        {/* Only the honest caveat renders; the ambient "every figure verified…"
            reassurance was trimmed (the claim row above already shows per-figure
            check marks). */}
        {!story.allVerified && (
          <div className="borrower-story__verdict borrower-story__verdict--warn" role="status">
            <Icon name="info" size={11} />
            Some figures could not be verified against the dossier — review before presenting.
          </div>
        )}
      </div>
    </div>
  );
}
