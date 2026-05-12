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
      title="Opportunity score combines rate spread, equity, intent triggers, relationship, and offer fit. 75+ is top tier."
      aria-label={`Opportunity score ${value}. 75 or higher is top tier.`}
    >
      <span className="score__dot" />
      {value}
    </span>
  );
}
