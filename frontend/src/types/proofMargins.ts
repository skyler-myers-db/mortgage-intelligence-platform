/**
 * Deterministic "what would change this?" margins on the borrower proof
 * (wow-ai-1 phase 1; backend/schemas/proof.py ProofMargin). Recomputed
 * server-side from the dossier row's own inputs and thresholds through the
 * reviewed scoring mirrors: marketing prioritization, not a credit decision.
 */

export type ProofMarginKey =
  | 'spread_screen'
  | 'par_break_even'
  | 'equity_floor'
  | 'offer_flip_equity'
  | 'offer_flip_par';

export type ProofMarginDirection = 'clears' | 'short' | 'flips' | 'holds' | 'unavailable';

export interface ProofMargin {
  key: ProofMarginKey;
  label: string;
  value_text: string;
  /** Muted context line: the inputs compared, or why the margin is unavailable. */
  threshold: string;
  direction: ProofMarginDirection;
  /** A governed proof asset (mip.gold.fn_*), rendered as an EvidenceChip. */
  source?: string | null;
}
