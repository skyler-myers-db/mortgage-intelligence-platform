/**
 * ScoreBadge — prototype `.score` BEM. Auto-classifies `.score--high/med/low`
 * from a numeric opportunity score. Thresholds match the prototype (>=85 high,
 * >=65 med, else low).
 */
export function ScoreBadge({ value }: { value: number }) {
  const tier = value >= 85 ? 'high' : value >= 65 ? 'med' : 'low';
  return (
    <span
      className={`score score--${tier}`}
      title="Opportunity score ranks lead strength on a 0-100 scale. Scores of 75+ are the strongest review candidates and include economics, intent, fit, relationship, and evidence."
      aria-label={`Opportunity score ${value}. Scores of 75 or higher are the strongest review candidates.`}
    >
      <span className="score__dot" />
      {value}
    </span>
  );
}
