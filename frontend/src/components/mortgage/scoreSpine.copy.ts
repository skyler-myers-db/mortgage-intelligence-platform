/**
 * Copy and part order for the lazy Score anatomy chunk (ScoreAnatomy,
 * ScoreSpine, ProofMargins). Kept out of scoreAnatomy.copy.ts so only the
 * chunk carries it; the unit tests and the fixture spec pin these strings.
 */
import type { ProofScoreComponentKey } from '../../types';

export const SCORE_SPINE_COPY = {
  unavailableTitle: 'Proof unavailable',
  unavailableBody: 'The governed proof endpoint did not return. Try again once the warehouse is healthy.',
  retry: 'Try again',
  gapsTitle: 'Known data gaps',
  noComponents: 'Component sub-scores unavailable for this borrower.',
  segmentHint: 'weighted points; opens its math',
  spineCaption: 'Weighted points of 100',
  marginsTitle: 'What would change this?',
  marginsNote: 'Marketing prioritization, not a credit decision.',
  parProvenanceTail: 'rates move only when the gold refresh runs; the FRED ingest schedule ships paused',
  parRefreshFallback: 'the last gold refresh',
} as const;

/** The five score parts in weight order (0.35 / 0.30 / 0.15 / 0.10 / 0.10). */
export const SCORE_PART_ORDER: readonly ProofScoreComponentKey[] = [
  'economic_incentive',
  'intent_trigger',
  'fit',
  'relationship',
  'evidence',
];

/** BEM modifier (and --score-part-* token suffix) per score part. */
export const SCORE_PART_MODIFIER: Readonly<Record<ProofScoreComponentKey, string>> = {
  economic_incentive: 'economic',
  intent_trigger: 'intent',
  fit: 'fit',
  relationship: 'relationship',
  evidence: 'evidence',
};
