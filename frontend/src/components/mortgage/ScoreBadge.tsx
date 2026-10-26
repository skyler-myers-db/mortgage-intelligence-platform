import { HIGH_OPPORTUNITY_SCORE_LABEL, scoreBand } from '../../lib/opportunityScore';
import { Tooltip } from '../ui/Tooltip';

/**
 * ScoreBadge — prototype `.score` BEM. Auto-classifies `.score--high/med/low`
 * from a numeric opportunity score via the canonical band mapping in
 * lib/opportunityScore (prototype tiers; do not re-declare edges here).
 */
const SCORE_NOTE = `Opportunity score ranks lead strength on a 0-100 scale. Scores of ${HIGH_OPPORTUNITY_SCORE_LABEL} are the strongest review candidates and include economics, intent, fit, relationship, and evidence.`;

export function ScoreBadge({ value }: { value: number }) {
  const tier = scoreBand(value);
  return (
    <Tooltip content={SCORE_NOTE}>
      <span
        className={`score score--${tier}`}
        aria-label={`Opportunity score ${value}. Scores of ${HIGH_OPPORTUNITY_SCORE_LABEL} are the strongest review candidates.`}
      >
        <span className="score__dot" />
        {value}
      </span>
    </Tooltip>
  );
}
