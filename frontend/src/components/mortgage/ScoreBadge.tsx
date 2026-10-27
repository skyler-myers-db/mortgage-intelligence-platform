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
      {/* role="img": ARIA 1.2 prohibits a name on a role-less span, and
          screen readers that honour the rule read only the bare number. An
          image is named and reads as one phrase, and the badge's text stays
          the bare number that other readers of `.score` parse (the live
          segment_intersection_live.spec.ts ranks rows by it). An image's
          children are presentational in browsers; aria-hidden says so for
          readers that walk into it anyway, so the number is not read twice. */}
      <span
        className={`score score--${tier}`}
        role="img"
        aria-label={`Opportunity score ${value}. Scores of ${HIGH_OPPORTUNITY_SCORE_LABEL} are the strongest review candidates.`}
      >
        <span className="score__dot" />
        <span aria-hidden="true">{value}</span>
      </span>
    </Tooltip>
  );
}
