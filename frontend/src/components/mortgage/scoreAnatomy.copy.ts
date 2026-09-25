/**
 * The Score anatomy copy the STATIC side needs: the disclosure gate's own
 * strings and the recompute seal, which the Decision receipt shows too.
 * Everything only the lazy chunk renders lives in scoreSpine.copy.ts, so the
 * route chunks that carry the gate do not carry it (budget).
 */
export const SCORE_ANATOMY_COPY = {
  spineToggle: 'Score anatomy',
  marginsToggle: 'What would change this?',
  loading: 'Loading score anatomy',
  chunkFailed: 'Score anatomy could not load; reload to update',
  /**
   * Shown only when the proof is trusted and has no known data gap. The
   * arithmetic is Python parity over Unity Catalog rows, so it never says
   * "Unity Catalog recomputed".
   */
  seal: 'Recomputed from lead_scores components: matches',
} as const;
