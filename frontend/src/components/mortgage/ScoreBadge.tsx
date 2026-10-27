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
      {/* The sentence is visually hidden text, not an aria-label: ARIA 1.2
          prohibits a name on a role-less span, and screen readers that honour
          the rule read only the bare number. One hidden run, with the shown
          number hidden from assistive tech, so it is read as one phrase. */}
      <span className={`score score--${tier}`}>
        <span className="score__dot" />
        <span className="sr-only">{`Opportunity score ${value}. Scores of ${HIGH_OPPORTUNITY_SCORE_LABEL} are the strongest review candidates.`}</span>
        <span aria-hidden="true">{value}</span>
      </span>
    </Tooltip>
  );
}
