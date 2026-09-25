/**
 * ScoreSpine — the five weighted score parts as one bar, plus the recompute
 * seal (wow-stage-2). Ships in the lazy Score anatomy chunk.
 *
 * BEM block `.score-spine` (ScoreSpine.css), a documented extension of the
 * prototype's segmented confidence bars `.conf__bars`
 * (design_files/index.html:556-565): the same "one bar, discrete parts" idea,
 * stacked by weighted points out of 100 instead of counted.
 *
 * - The bar is decorative (aria-hidden): five slices whose widths are each
 *   component's weighted points out of 100; the unfilled rest is the track.
 * - Under it, five real buttons in weight order carry the meaning: a swatch,
 *   the visible label and the points. Each accessible name begins with its
 *   visible text (WCAG 2.5.3); a pointer click on a slice triggers the same
 *   button. A button opens that component's math in the proof drawer.
 * - The seal renders only when the proof is trusted AND lists no known data
 *   gap (and has components to recompute from). Otherwise every gap is
 *   listed. It never claims "Unity Catalog recomputed": the recompute is
 *   Python parity over the governed rows.
 * - No animation and no view transition.
 */
import type { BorrowerProof, ProofScoreComponentKey } from '../../types';
import { formatFixed } from '../../lib/formatters';
import { Icon } from '../Icon';
import { SCORE_ANATOMY_COPY } from './scoreAnatomy.copy';
import { SCORE_PART_MODIFIER, SCORE_PART_ORDER, SCORE_SPINE_COPY } from './scoreSpine.copy';
import { proofSealHolds } from './useBorrowerProof';
import './ScoreSpine.css';

export interface ScoreSpineProps {
  proof: BorrowerProof;
  onOpenComponent: (key: ProofScoreComponentKey) => void;
}

function sliceWidth(points: number): string {
  return `${Math.min(100, Math.max(0, points))}%`;
}

export function ScoreSpine({ proof, onOpenComponent }: ScoreSpineProps) {
  const parts = SCORE_PART_ORDER.flatMap((key) => {
    const component = proof.score_components.find((item) => item.key === key);
    return component ? [component] : [];
  });
  const sealed = proofSealHolds(proof);

  return (
    <div className="score-spine" data-testid="score-spine">
      {parts.length > 0 && (
        <>
          <div className="score-spine__bar" aria-hidden="true" title={SCORE_SPINE_COPY.spineCaption}>
            {parts.map((part) => (
              <span
                key={part.key}
                className={`score-spine__slice score-spine__slice--${SCORE_PART_MODIFIER[part.key]}`}
                style={{ inlineSize: sliceWidth(part.weighted_points) }}
                data-part={part.key}
                onClick={() => onOpenComponent(part.key)}
              />
            ))}
          </div>
          <div className="score-spine__segs">
            {parts.map((part) => (
              <button
                key={part.key}
                type="button"
                className={`score-spine__seg score-spine__seg--${SCORE_PART_MODIFIER[part.key]}`}
                data-part={part.key}
                onClick={() => onOpenComponent(part.key)}
              >
                <span className="score-spine__label">{part.label}</span>{' '}
                <span className="score-spine__points">{formatFixed(part.weighted_points, 1)}</span>
                <span className="sr-only"> {SCORE_SPINE_COPY.segmentHint}</span>
              </button>
            ))}
          </div>
        </>
      )}
      {sealed ? (
        <p className="chip chip--success score-spine__seal" data-testid="score-spine-seal">
          <Icon name="check" size={12} />
          {SCORE_ANATOMY_COPY.seal}
        </p>
      ) : (
        <div className="score-spine__gaps" data-testid="score-spine-gaps">
          <div className="eyebrow">{SCORE_SPINE_COPY.gapsTitle}</div>
          <ul>
            {proof.known_data_gaps.map((gap) => (
              <li key={gap}>{gap}</li>
            ))}
            {proof.known_data_gaps.length === 0 && <li>{SCORE_SPINE_COPY.noComponents}</li>}
          </ul>
        </div>
      )}
    </div>
  );
}
