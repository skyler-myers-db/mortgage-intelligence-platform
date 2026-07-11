import { HIGH_OPPORTUNITY_SCORE_LABEL, scoreBand } from '../../lib/opportunityScore';

/**
 * ScoreBadge — prototype `.score` BEM. Auto-classifies `.score--high/med/low`
 * from a numeric opportunity score via the canonical band mapping in
 * lib/opportunityScore (prototype tiers; do not re-declare edges here).
 */
export function ScoreBadge({ value }: { value: number }) {
  const tier = scoreBand(value);
  return (
    <span
      className={`score score--${tier}`}
      title={`Opportunity score ranks lead strength on a 0-100 scale. Scores of ${HIGH_OPPORTUNITY_SCORE_LABEL} are the strongest review candidates and include economics, intent, fit, relationship, and evidence.`}
      aria-label={`Opportunity score ${value}. Scores of ${HIGH_OPPORTUNITY_SCORE_LABEL} are the strongest review candidates.`}
    >
      <span className="score__dot" />
      {value}
    </span>
  );
}
